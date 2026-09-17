import concurrent.futures
import time

import requests

from config import APP_BASE_URL, SCRAPER_STATUS, add_scraper_log, save_scraper_status
from core.normalization import deduplicate_job_list
from core.relevance import evaluate_job
from core.storage import (
    DISCOVERED_JOBS_FILE, SEEN_JOBS_FILE, atomic_write_json, load_closed_urls_cache,
    load_json_safe, load_reported_closed_jobs, load_settings, mark_url_as_closed,
    save_reported_closed_jobs,
)
from notifications import send_notification
from scrapers_engine.ats_scrapers import _JOB_LOCK, scrape_ashby_jobs, scrape_greenhouse_jobs, scrape_lever_jobs, scrape_smartrecruiters_jobs
from scrapers_engine.gradcracker_scraper import scrape_gradcracker_website
from scrapers_engine.trackr_scraper import scrape_trackr_website
from scrapers_engine.verifier import verify_live_page_applyable


def load_seen_jobs():
    data = load_json_safe(SEEN_JOBS_FILE, [])
    return set(data) if isinstance(data, list) else set()


def save_seen_jobs(seen_jobs):
    atomic_write_json(SEEN_JOBS_FILE, list(seen_jobs))


def load_discovered_jobs():
    return load_json_safe(DISCOVERED_JOBS_FILE, [])


def save_discovered_jobs(discovered_jobs):
    atomic_write_json(DISCOVERED_JOBS_FILE, deduplicate_job_list(discovered_jobs)[:1000])


def purge_irrelevant_jobs():
    """Re-evaluate the entire existing cache with today's relevance rules."""
    discovered = load_discovered_jobs()
    settings = load_settings()
    kept, reason_counts = [], {}
    for job in discovered:
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
    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🧹 JOB LINK HEALTH CHECK & DEAD LISTING PURGE                         │
└────────────────────────────────────────────────────────────────────────┘""")
    discovered = load_discovered_jobs()
    closed_urls = load_closed_urls_cache()
    initial_count = len(discovered)
    valid_jobs = []

    def check_job(job):
        link = job.get("link", "")
        if not link or not link.startswith("http"):
            return None
        if link in closed_urls:
            add_scraper_log(f"  ├── 🛑 Known Closed URL: {job.get('company')} - {job.get('title')}")
            return None
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.head(link, timeout=4, allow_redirects=True, headers=headers)
            if resp.status_code in (403, 405) or resp.status_code >= 500:
                resp = requests.get(link, timeout=6, allow_redirects=True, headers=headers, stream=True)
            if resp.status_code in (404, 410):
                mark_url_as_closed(link)
                add_scraper_log(f"  ├── 🛑 Dead link ({resp.status_code}): {job.get('company')} - {job.get('title')}")
                return None
        except Exception:
            pass
        return job

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(check_job, discovered):
            if result:
                valid_jobs.append(result)
    removed = initial_count - len(valid_jobs)
    save_discovered_jobs(valid_jobs)
    add_scraper_log(f"  └── 🧹 Purge Complete: Checked {initial_count} schemes, removed {removed} dead/closed listings.")
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
    start = time.time()
    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🔍 UK SCHEME PARALLEL MULTI-THREADED SCRAPER ENGINE                    │
└────────────────────────────────────────────────────────────────────────┘""")
    seen_jobs = load_seen_jobs()
    discovered = load_discovered_jobs()
    new_jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(scrape_greenhouse_jobs, seen_jobs, discovered, SCRAPER_STATUS),
            pool.submit(scrape_lever_jobs, seen_jobs, discovered, SCRAPER_STATUS),
            pool.submit(scrape_ashby_jobs, seen_jobs, discovered, SCRAPER_STATUS),
            pool.submit(scrape_smartrecruiters_jobs, seen_jobs, discovered, SCRAPER_STATUS),
            pool.submit(scrape_trackr_website, seen_jobs, discovered, False, add_scraper_log, SCRAPER_STATUS),
            pool.submit(scrape_gradcracker_website, seen_jobs, discovered, False, add_scraper_log, SCRAPER_STATUS),
        ]
        for future in futures:
            new_jobs.extend(future.result())

    save_seen_jobs(seen_jobs)
    save_discovered_jobs(discovered)
    irrelevant_removed = purge_irrelevant_jobs()
    purge_expired_jobs()
    final_jobs = load_discovered_jobs()

    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["total_discovered_jobs"] = len(final_jobs)
    SCRAPER_STATUS["last_new_jobs_found"] = len(new_jobs)
    SCRAPER_STATUS["last_irrelevant_pruned"] = irrelevant_removed
    save_scraper_status(SCRAPER_STATUS)
    add_scraper_log(f"  └── 📊 Scraper run complete in {round(time.time()-start,2)}s: {len(final_jobs)} eligible active schemes indexed.")

    if new_jobs:
        send_notification(title=f"🚀 {len(new_jobs)} New Relevant UK Schemes Discovered!", message="\n".join(f"• {j[0]} ({j[1]})" for j in new_jobs[:5]), link=f"{APP_BASE_URL}/discovered", tags="bell,rocket", priority=4, sound="fanfare")
