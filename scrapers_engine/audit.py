import concurrent.futures
import time
import threading
from datetime import datetime, timezone, date

import requests

from config import APP_BASE_URL, SCRAPER_STATUS, add_scraper_log, save_scraper_status
from core.normalization import deduplicate_job_list
from core.relevance import evaluate_job
from core.storage import (
    DISCOVERED_JOBS_FILE, SEEN_JOBS_FILE, atomic_write_json, load_closed_urls_cache,
    load_json_safe, load_reported_closed_jobs, load_settings, mark_url_as_closed,
    save_reported_closed_jobs,
)
from notifications import send_notification, notify_discoveries
from scrapers_engine.ats_scrapers import _JOB_LOCK, scrape_ashby_jobs, scrape_greenhouse_jobs, scrape_lever_jobs, scrape_smartrecruiters_jobs
from scrapers_engine.gradcracker_scraper import scrape_gradcracker_website
from scrapers_engine.trackr_scraper import scrape_trackr_website
from scrapers_engine.verifier import verify_live_page_applyable, verify_listing, deadline_date
from scrapers_engine.uk_boards import scrape_uk_boards
from scrapers_engine.quality import record_review

_RUN_LOCK = threading.Lock()


def load_seen_jobs():
    data = load_json_safe(SEEN_JOBS_FILE, [])
    return set(data) if isinstance(data, list) else set()


def save_seen_jobs(seen_jobs):
    atomic_write_json(SEEN_JOBS_FILE, list(seen_jobs))


def load_discovered_jobs():
    from core.jobs import MANUAL_JOBS_FILE
    return load_json_safe(DISCOVERED_JOBS_FILE, []) + load_json_safe(MANUAL_JOBS_FILE, [])


def save_discovered_jobs(discovered_jobs):
    atomic_write_json(DISCOVERED_JOBS_FILE, deduplicate_job_list([j for j in discovered_jobs if j.get("source") != "Manual"])[:1000])


def purge_irrelevant_jobs():
    """Re-evaluate the entire existing cache with today's relevance rules."""
    discovered = load_discovered_jobs()
    settings = load_settings()
    kept, reason_counts = [], {}
    for job in discovered:
        if job.get("source") == "Manual":
            kept.append(job)
            continue
        metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        decision = evaluate_job(job.get("title", ""), job.get("company", ""), job.get("location", ""), metadata=metadata, settings=settings)
        if decision.eligible:
            job["match_score"] = decision.score
            job["match_tier"] = decision.tier
            job["match_reasons"] = decision.reasons[:5]
            job["category"] = decision.category
            job["program_type"] = decision.program_type
            kept.append(job)
        else:
            for reason in decision.rejection_reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    removed = len(discovered) - len(kept)
    save_discovered_jobs(kept)
    if removed:
        top = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
        add_scraper_log(f"  ├── 🎯 Relevance cleanup removed {removed} stale/noisy listings. " + "; ".join(f"{r} ({n})" for r, n in top))
    else:
        add_scraper_log("  ├── 🎯 Relevance cleanup: all indexed listings still qualify.")
    return removed


def purge_expired_jobs():
    discovered = load_discovered_jobs()
    def check_job(job):
        if job.get('source') == 'Manual': return job
        link = job.get('link', '')
        closing = deadline_date(job.get('deadline') or job.get('metadata', {}).get('closing_date'))
        if closing and closing < date.today():
            record_review(job.get('company'), job.get('title'), link, job.get('source'), 'Application deadline passed', 'closed')
            return None
        verification = job.get('verification', {})
        try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(verification.get('checked_at', ''))).total_seconds()
        except (ValueError, TypeError): age = float('inf')
        if verification.get('state') == 'verified' and age < 6 * 3600: return job
        result = verify_listing(link, job.get('title', ''))
        if result['state'] != 'verified':
            record_review(job.get('company'), job.get('title'), link, job.get('source'), result['reason'], result['state'])
            return None
        metadata = dict(job.get('metadata') or {})
        for key in ('description', 'country', 'closing_date', 'employment_type'):
            if result.get(key): metadata[key] = result[key]
        if metadata.get('closing_date') and not deadline_date(metadata['closing_date']):
            metadata.pop('closing_date', None)
            job.pop('deadline', None)
        title, location = result.get('title') or job.get('title', ''), result.get('location') or job.get('location', '')
        decision = evaluate_job(title, job.get('company', ''), location, metadata)
        if not decision.eligible:
            record_review(job.get('company'), title, link, job.get('source'), '; '.join(decision.rejection_reasons), 'filtered')
            return None
        job.update(title=title, location=location, metadata=metadata, verification={k: result[k] for k in ('state', 'reason', 'checked_at')},
                   match_score=decision.score, match_tier=decision.tier, match_reasons=decision.reasons[:5])
        if deadline_date(metadata.get('closing_date')): job['deadline'] = deadline_date(metadata['closing_date']).isoformat()
        return job
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        kept = [job for job in pool.map(check_job, discovered) if job]
    save_discovered_jobs(kept)
    removed = len(discovered) - len(kept)
    add_scraper_log(f'Quality check: {len(kept)} retained; {removed} closed or unverified candidates moved out of the apply feed.')
    return removed


def recheck_existing_open_jobs_for_closure():
    discovered = load_discovered_jobs()
    closed_map = load_reported_closed_jobs()
    closed_urls = load_closed_urls_cache()
    closed_links = {c.get("link") for c in closed_map.values() if c.get("link")}.union(closed_urls)
    existing_open = [j for j in discovered if j.get("id") not in closed_map and j.get("link") not in closed_links]
    if not existing_open:
        add_scraper_log("  └── ℹ️ No active open schemes to recheck.")
        return 0

    newly_closed = []
    def evaluate(job):
        link = job.get("link", "")
        if link.startswith("http") and not verify_live_page_applyable(link):
            mark_url_as_closed(link)
            return job
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(evaluate, existing_open):
            if result: newly_closed.append(result)

    if newly_closed:
        with _JOB_LOCK:
            closed_map = load_reported_closed_jobs()
            for job in newly_closed:
                closed_map[job.get("id", f"job_{time.time()}")] = job
            save_reported_closed_jobs(closed_map)
        send_notification(title=f"Closure Audit: {len(newly_closed)} Schemes Moved to Closed", message=f"Moved {len(newly_closed)} newly closed schemes.", link=f"{APP_BASE_URL}/status", tags="broom,brain", priority=3, sound="chime")
    return len(newly_closed)


def run_all_scrapers():
    if not _RUN_LOCK.acquire(blocking=False):
        add_scraper_log('Discovery scan already running; avoiding an overlapping rescan.')
        return
    try:
        _run_scrapers()
    finally:
        _RUN_LOCK.release()


def _run_scrapers():
    start = time.time()
    seen_jobs, discovered, new_jobs = load_seen_jobs(), load_discovered_jobs(), []
    sources = {
        'Greenhouse API': scrape_greenhouse_jobs, 'Lever API': scrape_lever_jobs,
        'Ashby API': scrape_ashby_jobs, 'SmartRecruiters API': scrape_smartrecruiters_jobs,
        'The Trackr API': scrape_trackr_website, 'Gradcracker API': scrape_gradcracker_website,
        'UK student boards': scrape_uk_boards,
    }
    failures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(fn, seen_jobs, discovered, scraper_status=SCRAPER_STATUS): name for name, fn in sources.items()}
        for future in concurrent.futures.as_completed(futures):
            try: new_jobs.extend(future.result())
            except Exception as exc:
                name = futures[future]; failures[name] = type(exc).__name__
                from config import update_source_status
                update_source_status(name, f'🟠 Source failed ({type(exc).__name__}); other sources continue')
    save_discovered_jobs(discovered)
    irrelevant_removed = purge_irrelevant_jobs()
    quality_removed = purge_expired_jobs()
    final_jobs = load_discovered_jobs()
    save_seen_jobs(seen_jobs)
    SCRAPER_STATUS.update(last_run=time.strftime('%Y-%m-%d %H:%M:%S'), total_seen_jobs=len(seen_jobs),
        total_discovered_jobs=len(final_jobs), last_new_jobs_found=len(new_jobs),
        last_irrelevant_pruned=irrelevant_removed, last_quality_pruned=quality_removed, source_failures=failures,
        cycle_seconds=round(time.time() - start, 1))
    save_scraper_status(SCRAPER_STATUS)
    notified = notify_discoveries(final_jobs)
    add_scraper_log(f'Discovery complete: {len(final_jobs)} listings, {notified} newly alerted; {len(failures)} source failures; {round(time.time()-start, 1)}s.')
