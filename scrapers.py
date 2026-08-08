import os
import json
import time
import re
import requests
from datetime import datetime, timedelta
from config import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE, SCRAPER_STATUS,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES, ASHBY_COMPANIES,
    SMARTRECRUITERS_COMPANIES, load_settings, add_scraper_log
)

from notifications import send_notification

def is_trackr_item_active_and_recent(item):
    status_raw = str(
        item.get("status") or item.get("openStatus") or item.get("state") or ""
    ).lower().strip()

    if status_raw in ["closed", "unopened", "upcoming", "closed for applications", "expired"]:
        return False

    if item.get("isOpen") is False or item.get("isClosed") is True:
        return False

    now = datetime.now()
    six_months_ago = now - timedelta(days=180)

    open_date_str = (
        item.get("openDate") or item.get("openingDate") or item.get("open_date") or 
        item.get("openedAt") or item.get("created_at") or ""
    )
    close_date_str = (
        item.get("closeDate") or item.get("closingDate") or item.get("close_date") or 
        item.get("closedAt") or ""
    )

    if close_date_str:
        c_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(close_date_str))
        if c_match:
            try:
                close_dt = datetime.strptime(c_match.group(1), "%Y-%m-%d")
                if close_dt < now - timedelta(days=1):
                    return False
            except Exception:
                pass

    if not open_date_str:
        if status_raw != "open":
            return False
        return True

    o_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(open_date_str))
    if o_match:
        try:
            open_dt = datetime.strptime(o_match.group(1), "%Y-%m-%d")
            if open_dt < six_months_ago:
                return False
            if open_dt > now + timedelta(days=1):
                return False
            return True
        except Exception:
            pass

    return True

def extract_and_register_ats_company(url):
    if not url or not isinstance(url, str):
        return

    gh_match = re.search(r"boards\.greenhouse\.io/([^/?#]+)", url)
    if gh_match:
        comp = gh_match.group(1).lower().strip()
        if comp and comp not in GREENHOUSE_COMPANIES and comp not in ["embed", "jobs", "embeds"]:
            GREENHOUSE_COMPANIES.append(comp)
            print(f"  [ATS Auto-Discovery] Added new Greenhouse company: {comp}")

    lev_match = re.search(r"jobs\.lever\.co/([^/?#]+)", url)
    if lev_match:
        comp = lev_match.group(1).lower().strip()
        if comp and comp not in LEVER_COMPANIES and comp not in ["jobs"]:
            LEVER_COMPANIES.append(comp)
            print(f"  [ATS Auto-Discovery] Added new Lever company: {comp}")

def is_relevant_role(title, location="", company=""):
    settings = load_settings()
    t_lower = title.lower()
    l_lower = location.lower()
    c_lower = company.lower().replace(" ", "").replace("-", "")

    for ex_loc in settings.get("exclude_locations", []):
        if ex_loc in l_lower:
            return False

    is_special_intl = any(sc in c_lower for sc in settings.get("special_intl_companies", []))

    if not is_special_intl:
        has_uk_location = any(loc in l_lower for loc in settings.get("location_keywords", []))
        if location and not has_uk_location:
            return False

    for ex in settings.get("exclude_keywords", []):
        if ex in t_lower:
            return False

    has_role = any(rk in t_lower for rk in settings.get("role_keywords", []))
    has_level = any(lk in t_lower for lk in settings.get("level_keywords", []))

    return has_role and has_level

def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_jobs(seen_jobs):
    try:
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(list(seen_jobs), f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving seen_jobs: {e}")

def load_discovered_jobs():
    if os.path.exists(DISCOVERED_JOBS_FILE):
        try:
            with open(DISCOVERED_JOBS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_discovered_jobs(discovered_jobs):
    try:
        with open(DISCOVERED_JOBS_FILE, "w") as f:
            json.dump(discovered_jobs[:1000], f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving discovered_jobs: {e}")

import concurrent.futures
import threading

_JOB_LOCK = threading.Lock()

from config import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE, SCRAPER_STATUS,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES, ASHBY_COMPANIES,
    SMARTRECRUITERS_COMPANIES, load_settings, normalize_company, normalize_role
)

def calculate_skill_match_score(title, company, location, skills_list=None):
    """Calculates a skill match percentage (65-99%) for a job scheme based on user skills matrix."""
    if not skills_list:
        settings = load_settings()
        skills_list = settings.get("my_skills", ["python", "java", "javascript", "html", "css", "sql"])

    text = f"{title} {company} {location}".lower()
    total_skills = len(skills_list)
    if total_skills == 0:
        return 90

    matches = 0
    for skill in skills_list:
        sk = skill.lower().strip()
        if sk in text:
            matches += 1
        elif sk in ["python", "java", "c++", "sql"] and any(k in text for k in ["software", "developer", "engineer", "backend", "fullstack", "quant"]):
            matches += 0.85
        elif sk in ["javascript", "html", "css"] and any(k in text for k in ["fullstack", "frontend", "web", "developer"]):
            matches += 0.85

    score = int((matches / max(total_skills, 1)) * 100)
    if any(k in text for k in ["quant", "trader", "software", "developer", "machine learning", "ml", "ai"]):
        score = max(score, 84)
    score = min(max(score, 72), 99)
    return score

def add_discovered_job(discovered_list, job_id, company, title, location, link, source, source_url=None):
    norm_c = normalize_company(company)
    norm_t = normalize_role(title)

    if not source_url:
        source_url = link

    score = calculate_skill_match_score(title, company, location)

    with _JOB_LOCK:
        for item in discovered_list:
            if item.get("id") == job_id:
                item["source_url"] = source_url
                item["source"] = source
                item["match_score"] = score
                return
            item_c = normalize_company(item.get("company"))
            item_t = normalize_role(item.get("title"))
            if norm_c and norm_t and item_c == norm_c and item_t == norm_t:
                item["source_url"] = source_url
                item["source"] = source
                item["match_score"] = score
                return

        entry = {
            "id": job_id,
            "company": company,
            "title": title,
            "location": location if location else "UK / Remote",
            "link": link,
            "source": source,
            "source_url": source_url,
            "match_score": score,
            "date_found": time.strftime("%Y-%m-%d %H:%M")
        }
        discovered_list.insert(0, entry)



def scrape_greenhouse_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Greenhouse API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in GREENHOUSE_COMPANIES if c.strip()]))
    print(f"  [Greenhouse API] Scanning {len(clean_companies)} target companies concurrently...")

    def fetch_company(company):
        nonlocal companies_scanned, relevant_found
        if not company or company in ["embed", "jobs", "embeds"]:
            return []
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        board_url = f"https://boards.greenhouse.io/{company}"
        local_new = []
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
                with _JOB_LOCK:
                    companies_scanned += 1
                fetched = resp.json().get("jobs", [])
                company_relevant = 0

                for job in fetched:
                    title = job.get("title", "")
                    location = job.get("location", {}).get("name", "")
                    job_url = job.get("absolute_url", "")
                    job_id = f"gh_{company}_{job.get('id')}"

                    if is_relevant_role(title, location, company):
                        company_relevant += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, f"Greenhouse ({company})", board_url)

                        with _JOB_LOCK:
                            relevant_found += 1
                            if job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                local_new.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass
        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_company, clean_companies)
        for res in results:
            new_jobs.extend(res)

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_lever_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Lever API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in LEVER_COMPANIES if c.strip()]))
    print(f"  [Lever API] Scanning {len(clean_companies)} target companies concurrently...")

    def fetch_company(company):
        nonlocal companies_scanned, relevant_found
        if not company or company in ["jobs"]:
            return []
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        board_url = f"https://jobs.lever.co/{company}"
        local_new = []
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
                with _JOB_LOCK:
                    companies_scanned += 1
                fetched = resp.json()
                company_relevant = 0

                for job in fetched:
                    title = job.get("text", "")
                    location = job.get("categories", {}).get("location", "")
                    job_url = job.get("hostedUrl", "")
                    job_id = f"lever_{company}_{job.get('id')}"

                    if is_relevant_role(title, location, company):
                        company_relevant += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, f"Lever ({company})", board_url)

                        with _JOB_LOCK:
                            relevant_found += 1
                            if job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                local_new.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass
        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_company, clean_companies)
        for res in results:
            new_jobs.extend(res)

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_ashby_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Ashby API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in ASHBY_COMPANIES if c.strip()]))
    print(f"  [Ashby API] Scanning {len(clean_companies)} target companies concurrently...")

    def fetch_company(company):
        nonlocal companies_scanned, relevant_found
        local_new = []
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        board_url = f"https://jobs.ashbyhq.com/{company}"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
                with _JOB_LOCK:
                    companies_scanned += 1
                fetched = resp.json().get("jobs", [])
                company_relevant = 0

                for job in fetched:
                    title = job.get("title", "")
                    location = job.get("locationName", "")
                    job_url = job.get("jobUrl", f"https://jobs.ashbyhq.com/{company}/{job.get('id')}")
                    job_id = f"ashby_{company}_{job.get('id')}"

                    if is_relevant_role(title, location, company):
                        company_relevant += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, f"Ashby ({company})", board_url)

                        with _JOB_LOCK:
                            relevant_found += 1
                            if job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                local_new.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass
        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_company, clean_companies)
        for res in results:
            new_jobs.extend(res)

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_smartrecruiters_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "SmartRecruiters API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in SMARTRECRUITERS_COMPANIES if c.strip()]))
    print(f"  [SmartRecruiters API] Scanning {len(clean_companies)} target companies concurrently...")

    def fetch_company(company):
        nonlocal companies_scanned, relevant_found
        local_new = []
        url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        board_url = f"https://jobs.smartrecruiters.com/{company}"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
                with _JOB_LOCK:
                    companies_scanned += 1
                fetched = resp.json().get("content", [])
                company_relevant = 0

                for job in fetched:
                    title = job.get("name", "")
                    loc_dict = job.get("location", {})
                    location = f"{loc_dict.get('city', '')}, {loc_dict.get('country', '')}".strip(", ")
                    job_id = f"sr_{company}_{job.get('id')}"
                    job_url = f"https://jobs.smartrecruiters.com/{company}/{job.get('id')}"

                    if is_relevant_role(title, location, company):
                        company_relevant += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, f"SmartRecruiters ({company})", board_url)

                        with _JOB_LOCK:
                            relevant_found += 1
                            if job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                local_new.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass
        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = executor.map(fetch_company, clean_companies)
        for res in results:
            new_jobs.extend(res)

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs



LAST_TRACKR_RUN = 0

def scrape_trackr_website(seen_jobs, discovered_list, force=False):
    global LAST_TRACKR_RUN
    new_jobs = []
    source_name = "Trackr API"
    now = time.time()

    relevant_found = 0
    total_items_fetched = 0


    add_scraper_log("  [Trackr API] Fetching live UK Tech schemes from api.the-trackr.com (Tier 1 Direct Egress)...")
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
                                    add_discovered_job(discovered_list, job_id, company, role, "UK", link, trackr_source_name, trackr_source_url)

                                    with _JOB_LOCK:
                                        relevant_found += 1
                                        if job_id not in seen_jobs:
                                            seen_jobs.add(job_id)
                                            local_new.append((full_title, "UK", link))

                except Exception:
                    pass
            else:
                add_scraper_log(f"  [Trackr API] {season}/{t} HTTP {resp.status_code}")
        except Exception as e:
            err_msg = str(e)
            if "Missing dependencies for SOCKS support" in err_msg or "InvalidSchema" in err_msg:
                with _JOB_LOCK:
                    if not socks_missing_logged:
                        add_scraper_log("  [Trackr API Tier 2] PySocks dependency missing for SOCKS proxy. Relying on Direct ATS Auto-Discovery & Cache.")
                        socks_missing_logged = True
            else:
                add_scraper_log(f"  [Trackr API] Connection notice ({season}/{t}): {err_msg}")
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
        add_scraper_log("  [Trackr API Tier 2] ⚡ Engaging Dynamic Multi-Source Public Proxy Egress Pool...")
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
        add_scraper_log(f"  [Trackr API Tier 2] Fetched {len(candidate_proxies)} HTTP proxy nodes. Discovering fast exit node...")

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
            add_scraper_log(f"  [Trackr API Tier 2] 🔥 Discovered {len(working_nodes)} fast HTTP proxy exit nodes! Fetching live schemes...")
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




    if rate_limited and total_items_fetched == 0:
        add_scraper_log("  [Trackr API Tier 3] ⚠️ Retaining smart cache (discovered_list) and relying on Direct ATS Auto-Discovery.")
        SCRAPER_STATUS["source_status"][source_name] = f"⚠️ Rate Limited (HTTP 429 - Retaining {len(discovered_list)} cached schemes)"
    else:
        add_scraper_log(f"  [Trackr Summary] Fetched {total_items_fetched} raw items ({relevant_found} active schemes opened in last 6 months matching Maths & CS)")
        SCRAPER_STATUS["source_status"][source_name] = f"OK ({total_items_fetched} items fetched, {relevant_found} active recent schemes)"

    return new_jobs






def purge_expired_jobs():
    add_scraper_log("🧹 Starting Job Link Health Check & Purge...")
    discovered = load_discovered_jobs()
    initial_count = len(discovered)
    valid_jobs = []
    purged_count = 0

    def check_job(job):
        nonlocal purged_count
        link = job.get("link", "")
        if not link or not link.startswith("http"):
            return None
        try:
            resp = requests.head(link, timeout=4, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code in [404, 410]:
                add_scraper_log(f"  [Purge] Dead link ({resp.status_code}): {job.get('company')} - {job.get('title')}")
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
    add_scraper_log(f"🧹 Purge Complete: Checked {initial_count} schemes, removed {purged_count} dead/closed listings.")
    return purged_count


def run_all_scrapers():
    start_time = time.time()
    add_scraper_log("🔍 Running Parallel Multi-Threaded UK Scraper Engine...")
    seen_jobs = load_seen_jobs()
    discovered_list = load_discovered_jobs()
    all_new_jobs = []

    # Execute all 5 ATS & Trackr scrapers in parallel using worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        f_gh = executor.submit(scrape_greenhouse_jobs, seen_jobs, discovered_list)
        f_lever = executor.submit(scrape_lever_jobs, seen_jobs, discovered_list)
        f_ashby = executor.submit(scrape_ashby_jobs, seen_jobs, discovered_list)
        f_sr = executor.submit(scrape_smartrecruiters_jobs, seen_jobs, discovered_list)
        f_trackr = executor.submit(scrape_trackr_website, seen_jobs, discovered_list)

        all_new_jobs.extend(f_gh.result())
        all_new_jobs.extend(f_lever.result())
        all_new_jobs.extend(f_ashby.result())
        all_new_jobs.extend(f_sr.result())
        all_new_jobs.extend(f_trackr.result())

    elapsed = round(time.time() - start_time, 2)
    save_seen_jobs(seen_jobs)
    save_discovered_jobs(discovered_list)

    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["total_discovered_jobs"] = len(discovered_list)
    SCRAPER_STATUS["last_new_jobs_found"] = len(all_new_jobs)

    add_scraper_log(f"📊 Parallel Scraper Run Complete in {elapsed}s: {len(discovered_list)} total active schemes indexed ({len(all_new_jobs)} new alerts sent).")

    if all_new_jobs:
        if len(all_new_jobs) > 5:
            summary = "\n".join([f"• {t[0]}" for t in all_new_jobs[:3]])
            send_notification(
                title=f"{len(all_new_jobs)} New Roles Discovered!",
                message=f"Latest roles found:\n{summary}\n...\nTap to view all listings.",
                link=f"http://100.75.135.73:5000/jobs",
                tags="sparkles,uk",
                priority=4,
                sound="bing"
            )
        else:
            for title, location, link in all_new_jobs:
                send_notification(
                    title=f"New Role: {title}",
                    message=f"Location: {location}\nTap to view details!",
                    link=f"http://100.75.135.73:5000/jobs",
                    tags="sparkles,uk",
                    priority=3,
                    sound="bing"
                )