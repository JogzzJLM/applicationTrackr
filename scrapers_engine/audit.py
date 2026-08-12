import time
import requests
import concurrent.futures
from config import SCRAPER_STATUS, add_scraper_log, update_scraper_status, update_source_status
from core.storage import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE,
    load_reported_closed_jobs, save_reported_closed_jobs,
    load_closed_urls_cache, mark_url_as_closed,
    load_json_safe, atomic_write_json, save_scraper_status
)

from core.normalization import normalize_company, normalize_role
from notifications import send_notification
from scrapers_engine.verifier import verify_live_page_applyable
from scrapers_engine.ats_scrapers import (
    scrape_greenhouse_jobs, scrape_lever_jobs, scrape_ashby_jobs,
    scrape_smartrecruiters_jobs, _JOB_LOCK
)
from scrapers_engine.trackr_scraper import scrape_trackr_website

def load_seen_jobs():
    data = load_json_safe(SEEN_JOBS_FILE, [])
    return set(data) if isinstance(data, list) else set()

def save_seen_jobs(seen_jobs):
    atomic_write_json(SEEN_JOBS_FILE, list(seen_jobs))

def load_discovered_jobs():
    return load_json_safe(DISCOVERED_JOBS_FILE, [])

def save_discovered_jobs(discovered_jobs):
    atomic_write_json(DISCOVERED_JOBS_FILE, discovered_jobs[:1000])

def purge_expired_jobs():
    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🧹 JOB LINK HEALTH CHECK & DEAD LISTING PURGE                         │
└────────────────────────────────────────────────────────────────────────┘""")
    discovered = load_discovered_jobs()
    closed_urls = load_closed_urls_cache()
    initial_count = len(discovered)
    valid_jobs = []
    purged_count = 0

    def check_job(job):
        nonlocal purged_count
        link = job.get("link", "")
        if not link or not link.startswith("http"):
            return None

        if link in closed_urls:
            add_scraper_log(f"  ├── 🛑 Known Closed URL (Skipped HTTP): {job.get('company')} - {job.get('title')}")
            return None

        try:
            resp = requests.head(link, timeout=4, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code in [404, 410, 403, 500]:
                mark_url_as_closed(link)
                add_scraper_log(f"  ├── 🛑 Dead link ({resp.status_code}): {job.get('company')} - {job.get('title')}")
                return None
        except Exception:
            pass
        return job

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(check_job, discovered)
        for res in results:
            if res:
                valid_jobs.append(res)
            else:
                purged_count += 1

    save_discovered_jobs(valid_jobs)
    add_scraper_log(f"  └── 🧹 Purge Complete: Checked {initial_count} schemes, removed {purged_count} dead/closed listings.")
    return purged_count

def recheck_existing_open_jobs_for_closure():
    """
    Cascade Re-Evaluation Task:
    Iterates through all existing open schemes in discovered_jobs.json, fetches their live webpages,
    and checks if any match newly learned closure patterns. If closed, automatically moves them to reported_closed_jobs.json.
    """
    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🛑 CASCADE CLOSURE AUDIT (AI KNOWLEDGE BASE VERIFICATION)              │
└────────────────────────────────────────────────────────────────────────┘""")
    discovered = load_discovered_jobs()
    closed_map = load_reported_closed_jobs()
    closed_urls = load_closed_urls_cache()

    closed_links = set(c.get("link") for c in closed_map.values() if c.get("link")).union(closed_urls)
    closed_ids = set(closed_map.keys())

    existing_open = [j for j in discovered if j.get("id") not in closed_ids and j.get("link") not in closed_links]

    if not existing_open:
        add_scraper_log("  └── ℹ️ No active open schemes to recheck (all up to date).")
        return 0

    add_scraper_log(f"  ├── 🔍 Re-evaluating {len(existing_open)} active open schemes against updated Knowledge Base...")

    newly_detected_closed = []

    def evaluate_open_job(job):
        link = job.get("link", "")
        if not link or not link.startswith("http"):
            return None
        if not verify_live_page_applyable(link):
            mark_url_as_closed(link)
            return job
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = executor.map(evaluate_open_job, existing_open)
        for res in results:
            if res:
                newly_detected_closed.append(res)

    if newly_detected_closed:
        with _JOB_LOCK:
            closed_map = load_reported_closed_jobs()
            for c_job in newly_detected_closed:
                j_id = c_job.get("id", f"job_{time.time()}")
                closed_map[j_id] = c_job
                add_scraper_log(f"  ├── 🛑 Auto-Moved to Closed Directory: {c_job.get('company')} - {c_job.get('title')}")
            save_reported_closed_jobs(closed_map)

        send_notification(
            title=f"Closure Audit: {len(newly_detected_closed)} Schemes Moved to Closed",
            message=f"Re-evaluated {len(existing_open)} open schemes against updated AI patterns. Automatically moved {len(newly_detected_closed)} newly closed schemes to Closed Directory.",
            link=f"http://{HP_STREAM_TAILSCALE_IP}:5000/status",
            tags="broom,brain",
            priority=3,
            sound="chime"
        )
        add_scraper_log(f"  └── ✅ Cascade Closure Audit complete! Automatically moved {len(newly_detected_closed)} newly closed schemes to Closed Directory.")
    else:
        add_scraper_log(f"  └── ✅ Cascade Closure Audit complete! All {len(existing_open)} open schemes confirmed 100% active & apply-able.")

    return len(newly_detected_closed)

def run_all_scrapers():
    start_time = time.time()
    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🔍 UK SCHEME PARALLEL MULTI-THREADED SCRAPER ENGINE                    │
└────────────────────────────────────────────────────────────────────────┘""")
    seen_jobs = load_seen_jobs()
    discovered_list = load_discovered_jobs()
    all_new_jobs = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        f_gh = executor.submit(scrape_greenhouse_jobs, seen_jobs, discovered_list, SCRAPER_STATUS)
        f_lever = executor.submit(scrape_lever_jobs, seen_jobs, discovered_list, SCRAPER_STATUS)
        f_ashby = executor.submit(scrape_ashby_jobs, seen_jobs, discovered_list, SCRAPER_STATUS)
        f_sr = executor.submit(scrape_smartrecruiters_jobs, seen_jobs, discovered_list, SCRAPER_STATUS)
        f_trackr = executor.submit(scrape_trackr_website, seen_jobs, discovered_list, False, add_scraper_log, SCRAPER_STATUS)

        all_new_jobs.extend(f_gh.result())
        all_new_jobs.extend(f_lever.result())
        all_new_jobs.extend(f_ashby.result())
        all_new_jobs.extend(f_sr.result())
        all_new_jobs.extend(f_trackr.result())

    save_seen_jobs(seen_jobs)
    save_discovered_jobs(discovered_list)

    purge_expired_jobs()

    elapsed = round(time.time() - start_time, 2)
    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["total_discovered_jobs"] = len(discovered_list)
    SCRAPER_STATUS["last_new_jobs_found"] = len(all_new_jobs)

    save_scraper_status(SCRAPER_STATUS)

    add_scraper_log(f"  └── 📊 Parallel Scraper Run Complete in {elapsed}s: {len(discovered_list)} total active schemes indexed.")

    if all_new_jobs:
        send_notification(
            title=f"🚀 {len(all_new_jobs)} New UK Schemes Discovered!",
            message="\n".join([f"• {j[0]} ({j[1]})" for j in all_new_jobs[:5]]),
            link=f"http://{HP_STREAM_TAILSCALE_IP}:5000/discovered",
            tags="bell,rocket",
            priority=4,
            sound="fanfare"
        )
