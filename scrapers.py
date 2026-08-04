import os
import json
import time
import re
import requests
from datetime import datetime, timedelta
from config import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE, SCRAPER_STATUS,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES, ASHBY_COMPANIES,
    SMARTRECRUITERS_COMPANIES, load_settings
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

def add_discovered_job(discovered_list, job_id, company, title, location, link, source):
    for item in discovered_list:
        if item.get("id") == job_id:
            return
    entry = {
        "id": job_id,
        "company": company,
        "title": title,
        "location": location if location else "UK / Remote",
        "link": link,
        "source": source,
        "date_found": time.strftime("%Y-%m-%d %H:%M")
    }
    discovered_list.insert(0, entry)

def scrape_greenhouse_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Greenhouse API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in GREENHOUSE_COMPANIES if c.strip()]))

    print(f"  [Greenhouse API] Scanning {len(clean_companies)} target companies...")
    for company in clean_companies:
        if not company or company in ["embed", "jobs", "embeds"]:
            continue
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
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
                        relevant_found += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, source_name)

                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_lever_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Lever API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in LEVER_COMPANIES if c.strip()]))

    print(f"  [Lever API] Scanning {len(clean_companies)} target companies...")
    for company in clean_companies:
        if not company or company in ["jobs"]:
            continue
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
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
                        relevant_found += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, source_name)

                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_ashby_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Ashby API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in ASHBY_COMPANIES if c.strip()]))
    print(f"  [Ashby API] Scanning {len(clean_companies)} target companies...")
    for company in clean_companies:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
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
                        relevant_found += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, source_name)

                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_smartrecruiters_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "SmartRecruiters API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in SMARTRECRUITERS_COMPANIES if c.strip()]))
    print(f"  [SmartRecruiters API] Scanning {len(clean_companies)} target companies...")
    for company in clean_companies:
        url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200:
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
                        relevant_found += 1
                        add_discovered_job(discovered_list, job_id, company.capitalize(), title, location, job_url, source_name)

                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))

                if company_relevant > 0:
                    print(f"    ↳ {company.capitalize()}: {len(fetched)} jobs fetched ({company_relevant} relevant)")
        except Exception:
            pass

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_trackr_website(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Trackr API"
    relevant_found = 0
    total_items_fetched = 0

    print("  [Trackr API] Fetching live UK Tech schemes from api.the-trackr.com...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://app.the-trackr.com/",
        "Origin": "https://app.the-trackr.com"
    }

    # Consolidated endpoints (2 calls max per run to respect Trackr 200/day IP rate limit)
    endpoints = [
        "https://api.the-trackr.com/programmes?region=UK&industry=Tech&season=2027",
        "https://api.the-trackr.com/programmes?region=UK&industry=Tech&season=2026"
    ]

    rate_limited = False

    for url in endpoints:
        time.sleep(1.0)
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 429:
                rate_limited = True
                print("  [Trackr API] ⚠️ Rate Limited (HTTP 429: 200-request daily limit reached). Retaining cached schemes.")
                break
            elif resp.status_code == 200:
                try:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("programmes", data.get("data", []))
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
                                    relevant_found += 1
                                    add_discovered_job(discovered_list, job_id, company, role, "UK", link, source_name)

                                    if job_id not in seen_jobs:
                                        seen_jobs.add(job_id)
                                        new_jobs.append((full_title, "UK", link))

                except Exception:
                    pass
        except Exception as e:
            print(f"  [Trackr API] Connection error: {e}")

    if rate_limited:
        SCRAPER_STATUS["source_status"][source_name] = f"⚠️ Rate Limited (HTTP 429 - Retaining {len(discovered_list)} cached schemes)"
    else:
        print(f"  [Trackr Summary] Fetched {total_items_fetched} raw items ({relevant_found} active schemes opened in last 6 months matching Maths & CS)")
        SCRAPER_STATUS["source_status"][source_name] = f"OK ({total_items_fetched} items fetched, {relevant_found} active recent schemes)"

    return new_jobs

def run_all_scrapers():
    print("\n🔍 Running Active UK Scraper Engine...")
    seen_jobs = load_seen_jobs()
    discovered_list = load_discovered_jobs()
    all_new_jobs = []

    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs, discovered_list))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs, discovered_list))
    all_new_jobs.extend(scrape_ashby_jobs(seen_jobs, discovered_list))
    all_new_jobs.extend(scrape_smartrecruiters_jobs(seen_jobs, discovered_list))
    all_new_jobs.extend(scrape_trackr_website(seen_jobs, discovered_list))

    save_seen_jobs(seen_jobs)
    save_discovered_jobs(discovered_list)

    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["total_discovered_jobs"] = len(discovered_list)
    SCRAPER_STATUS["last_new_jobs_found"] = len(all_new_jobs)

    print(f"📊 Scraper Run Complete: {len(discovered_list)} total active schemes indexed ({len(all_new_jobs)} new alerts sent).")

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