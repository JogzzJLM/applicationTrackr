import time
import re
import requests
import concurrent.futures
import hashlib
from datetime import datetime, timedelta
from config import SCRAPER_STATUS, add_scraper_log, update_source_status
from scrapers_engine.ats_scrapers import is_relevant_role, add_discovered_job, extract_and_register_ats_company, _JOB_LOCK


def parse_trackr_date(d_str):
    if not d_str or not isinstance(d_str, str):
        return None
    d_str = d_str.strip()
    m = re.search(r'(\d{4}-\d{2}-\d{2})', d_str)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y-%m-%d')
        except Exception:
            pass
    m = re.search(r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})', d_str)
    if m:
        val = m.group(1)
        for fmt in ['%d %b %Y', '%d %b %y']:
            try:
                return datetime.strptime(val, fmt)
            except Exception:
                pass
    return None

def is_trackr_item_active_and_recent(item):
    status_raw = str(
        item.get("status") or item.get("openStatus") or item.get("state") or item.get("programmeStatus") or ""
    ).lower().strip()

    if any(kw in status_raw for kw in ["close", "unopen", "upcom", "expir", "fill", "pause", "archiv"]):
        return False

    if item.get("isOpen") is False or item.get("isClosed") is True or item.get("is_open") is False:
        return False

    if status_raw and status_raw not in ["open", "active", "opened", "accepting applications", "open for applications"]:
        return False

    now = datetime.now()
    six_months_ago = now - timedelta(days=180)

    close_date_str = (
        item.get("closeDate") or item.get("closingDate") or item.get("close_date") or
        item.get("closedAt") or ""
    )

    if close_date_str:
        c_dt = parse_trackr_date(close_date_str)
        if c_dt and c_dt < now:
            return False

    open_date_str = (
        item.get("openDate") or item.get("openingDate") or item.get("open_date") or
        item.get("dateOpened") or item.get("postedDate") or item.get("createdAt") or
        item.get("updatedAt") or ""
    )

    if open_date_str:
        o_dt = parse_trackr_date(open_date_str)
        if o_dt and o_dt < six_months_ago:
            return False

    return True

def scrape_trackr_website(seen_jobs, discovered_list, force_rescan=False, log_func=None, scraper_status=None):
    if log_func:
        log_func("  ├── 🟢 [The Trackr API] Fetching live UK Tech schemes from api.the-trackr.com (Tier 1 Direct Egress)...")

    new_jobs = []
    source_name = "The Trackr API"

    tasks = []
    seasons = ["2027", "2026", "2025"]
    types = ["summer-internships", "graduate-schemes", "off-cycle-internships", "placements", "spring-insight"]
    for s in seasons:
        for t in types:
            tasks.append((s, t))

    total_items_fetched = 0
    relevant_found = 0
    rate_limited = False
    socks_missing_logged = False

    def fetch_trackr_param(task_pair, proxies=None, rotate_ua=False):
        nonlocal total_items_fetched, relevant_found, rate_limited, socks_missing_logged
        season, t = task_pair
        url = f"https://api.the-trackr.com/programmes?region=UK&industry=Tech&season={season}&type={t}"
        local_new = []

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://the-trackr.com/"
        }
        if rotate_ua:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

        try:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=5)
            if resp.status_code == 429:
                rate_limited = True
                return []

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("programmes") or data.get("items") or []

                    with _JOB_LOCK:
                        total_items_fetched += len(items)

                    for item in items:
                        company = (
                            item.get("companyName") or item.get("company") or
                            item.get("employer") or item.get("name") or ""
                        ).strip()

                        title = (
                            item.get("role") or item.get("title") or
                            item.get("programmeName") or item.get("jobTitle") or ""
                        ).strip()

                        link = (
                            item.get("applicationLink") or item.get("link") or
                            item.get("url") or item.get("applyUrl") or ""
                        ).strip()

                        location = (
                            item.get("location") or item.get("city") or
                            item.get("region") or "UK"
                        ).strip()

                        if not company or not title or not link:
                            continue

                        extract_and_register_ats_company(link)

                        if is_trackr_item_active_and_recent(item):
                            if is_relevant_role(title, location, company):
                                stable_key = f"{company}|{title}|{link}".encode("utf-8")
                                job_id = f"trackr_{hashlib.sha256(stable_key).hexdigest()[:20]}"
                                is_new = add_discovered_job(
                                    discovered_list, job_id, company, title, location, link, "The Trackr API", "https://the-trackr.com"
                                )

                                with _JOB_LOCK:
                                    relevant_found += 1
                                    if is_new and job_id not in seen_jobs:
                                        seen_jobs.add(job_id)
                                        local_new.append((f"{company} - {title}", location or "UK", link))

                except Exception:
                    pass
        except Exception:
            pass
        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(lambda p: fetch_trackr_param(p, proxies=None), tasks)
        for res in results:
            new_jobs.extend(res)

    status_str = f"🟢 Active • Direct Egress ({relevant_found} active schemes indexed)"
    update_source_status(source_name, status_str)
    if log_func:
        log_func(f"  │   ↳ Trackr Summary: {total_items_fetched} raw items fetched ({relevant_found} active recent schemes matching Maths & CS)")

    return new_jobs
