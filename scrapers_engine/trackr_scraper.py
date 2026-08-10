import time
import re
import requests
import concurrent.futures
from datetime import datetime, timedelta
from config import SCRAPER_STATUS, add_scraper_log
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
        if c_dt and c_dt < now - timedelta(days=1):
            return False

    open_date_str = (
        item.get("openDate") or item.get("openingDate") or item.get("open_date") or
        item.get("openedAt") or item.get("created_at") or ""
    )

    if open_date_str:
        o_dt = parse_trackr_date(open_date_str)
        if o_dt:
            if o_dt < six_months_ago or o_dt > now + timedelta(days=1):
                return False

    return True

LAST_TRACKR_RUN = 0

def scrape_trackr_website(seen_jobs, discovered_list, force=False, log_func=print, scraper_status=None):
    global LAST_TRACKR_RUN
    new_jobs = []
    source_name = "Trackr API"
    now = time.time()

    relevant_found = 0
    total_items_fetched = 0

    if log_func:
        log_func("  [Trackr API] Fetching live UK Tech schemes from api.the-trackr.com (Tier 1 Direct Egress)...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://app.the-trackr.com/",
        "Origin": "https://app.the-trackr.com"
    }

    types = ["summer-internships", "industrial-placements", "graduate-schemes", "spring-weeks"]
    seasons = ["2027", "2026"]
    tasks = [(season, t) for season in seasons for t in types]

    rate_limited = False
    socks_missing_logged = False
    LAST_TRACKR_RUN = now

    def fetch_trackr_param(pair, proxies=None, rotate_ua=False):
        nonlocal rate_limited, total_items_fetched, relevant_found, socks_missing_logged
        if rate_limited and not proxies and not rotate_ua:
            return []
        season, t = pair
        cb = int(time.time())
        url = f"https://api.the-trackr.com/programmes?region=UK&industry=Tech&season={season}&type={t}&_cb={cb}"
        local_new = []

        req_headers = dict(headers)
        if rotate_ua:
            req_headers["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"

        try:
            resp = requests.get(url, headers=req_headers, proxies=proxies, timeout=6)
            if resp.status_code == 429:
                with _JOB_LOCK:
                    rate_limited = True
                return []
            elif resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("programmes", data.get("data", []))
                    with _JOB_LOCK:
                        total_items_fetched += len(items)

                    for item in items:
                        if isinstance(item, dict):
                            if not is_trackr_item_active_and_recent(item):
                                continue

                            company = item.get("companyName") or item.get("company_name") or ""
                            if isinstance(item.get("company"), dict):
                                company = item.get("company", {}).get("name", company)
                            elif isinstance(item.get("company"), str) and not company:
                                company = item.get("company")

                            role = item.get("name") or item.get("programmeName") or item.get("title") or item.get("programme") or item.get("role") or ""
                            link = item.get("link") or item.get("url") or item.get("applyUrl") or item.get("apply_url") or "https://app.the-trackr.com"

                            if company and role:
                                full_title = f"{company} - {role}"
                                job_id = f"trackr_api_{hash(full_title)}"

                                extract_and_register_ats_company(link)

                                if is_relevant_role(full_title, "UK", company):
                                    trackr_source_name = f"Trackr UK Tech ({season})"
                                    trackr_source_url = "https://app.the-trackr.com"
                                    is_new = add_discovered_job(discovered_list, job_id, company, role, "UK", link, trackr_source_name, trackr_source_url)

                                    with _JOB_LOCK:
                                        relevant_found += 1
                                        if is_new and job_id not in seen_jobs:
                                            seen_jobs.add(job_id)
                                            local_new.append((full_title, "UK", link))

                except Exception:
                    pass
            else:
                if log_func:
                    log_func(f"  [Trackr API] {season}/{t} HTTP {resp.status_code}")
        except Exception as e:
            err_msg = str(e)
            if "ProxyError" in err_msg or "503" in err_msg or "Tunnel connection failed" in err_msg or "Max retries exceeded" in err_msg:
                pass
            elif "Missing dependencies for SOCKS support" in err_msg or "InvalidSchema" in err_msg:
                with _JOB_LOCK:
                    if not socks_missing_logged and log_func:
                        log_func("  [Trackr API Tier 2] PySocks dependency missing for SOCKS proxy. Relying on Direct ATS Auto-Discovery & Cache.")
                        socks_missing_logged = True
            else:
                if log_func:
                    log_func(f"  [Trackr API] Connection notice ({season}/{t}): {err_msg}")
        return local_new

    # Tier 1 Execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(lambda p: fetch_trackr_param(p, proxies=None), tasks)
        for res in results:
            new_jobs.extend(res)

    # Tier 1B Egress Rotation (Header & User-Agent Rotation if Tier 1 hit 429)
    if rate_limited:
        rate_limited = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = executor.map(lambda p: fetch_trackr_param(p, proxies=None, rotate_ua=True), tasks)
            for res in results:
                new_jobs.extend(res)

    # Tier 2 Egress Fallback (Dynamic Multi-Source Public Proxy Pool Egress)
    if rate_limited:
        if log_func:
            log_func("  [Trackr API Tier 2] ⚡ Engaging Dynamic Multi-Source Public Proxy Egress Pool...")
        candidate_proxies = []
        sources = [
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"
        ]
        for src in sources:
            try:
                resp = requests.get(src, timeout=3)
                if resp.status_code == 200:
                    lines = [p.strip() for p in resp.text.split("\n") if p.strip()]
                    candidate_proxies.extend(lines[:150])
            except Exception:
                pass

        candidate_proxies = list(set(candidate_proxies))
        if log_func:
            log_func(f"  [Trackr API Tier 2] Fetched {len(candidate_proxies)} HTTP proxy nodes. Discovering fast exit node...")

        working_nodes = []

        def check_node(px_str):
            px_dict = {"http": f"http://{px_str}", "https": f"http://{px_str}"}
            try:
                test_url = "https://api.the-trackr.com/programmes?region=UK&industry=Tech&season=2027&type=summer-internships"
                r = requests.get(test_url, proxies=px_dict, timeout=2.5)
                if r.status_code == 200 and len(r.content) > 1000:
                    return px_dict
            except Exception:
                pass
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            node_results = executor.map(check_node, candidate_proxies[:250])
            for res in node_results:
                if res:
                    working_nodes.append(res)
                    if len(working_nodes) >= len(tasks):
                        break

        if working_nodes:
            if log_func:
                log_func(f"  [Trackr API Tier 2] 🔥 Discovered {len(working_nodes)} fast HTTP proxy exit nodes! Fetching live schemes...")
            rate_limited = False

            def fetch_with_node(idx_task):
                idx, task_pair = idx_task
                for attempt_offset in range(len(working_nodes)):
                    node_proxy = working_nodes[(idx + attempt_offset) % len(working_nodes)]
                    res = fetch_trackr_param(task_pair, proxies=node_proxy, rotate_ua=True)
                    if res or total_items_fetched > 0:
                        return res
                return []

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                results = executor.map(fetch_with_node, enumerate(tasks))
                for res in results:
                    new_jobs.extend(res)

    status_dict = scraper_status if scraper_status is not None else SCRAPER_STATUS
    if rate_limited and total_items_fetched == 0:
        if log_func:
            log_func("  [Trackr API Tier 3] ⚠️ Retaining smart cache (discovered_list) and relying on Direct ATS Auto-Discovery.")
        status_dict["source_status"][source_name] = f"⚠️ Rate Limited (HTTP 429 - Retaining {len(discovered_list)} cached schemes)"
    else:
        if log_func:
            log_func(f"  [Trackr Summary] Fetched {total_items_fetched} raw items ({relevant_found} active schemes opened in last 6 months matching Maths & CS)")
        status_dict["source_status"][source_name] = f"OK ({total_items_fetched} items fetched, {relevant_found} active recent schemes)"

    return new_jobs
