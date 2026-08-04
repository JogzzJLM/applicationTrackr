import os
import json
import time
import re
import requests
from config import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE, SCRAPER_STATUS,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES
)
from notifications import send_notification
from settings import get_active_profile_settings

def extract_and_register_ats_company(url):
    try:
        clean_url = url.split("?")[0].split("#")[0].strip()
        if "greenhouse.io" in clean_url:
            match = re.search(r"greenhouse\.io/([^/]+)", clean_url)
            if match:
                company = match.group(1).lower().strip()
                if company not in GREENHOUSE_COMPANIES and company not in ["embed", "jobs", "embeds"]:
                    GREENHOUSE_COMPANIES.append(company)
                    print(f"  ✨ Dynamically registered new Greenhouse company: {company}")
        elif "lever.co" in clean_url:
            match = re.search(r"lever\.co/([^/]+)", clean_url)
            if match:
                company = match.group(1).lower().strip()
                if company not in LEVER_COMPANIES and company not in ["jobs"]:
                    LEVER_COMPANIES.append(company)
                    print(f"  ✨ Dynamically registered new Lever company: {company}")
    except Exception:
        pass

def is_relevant_role(title, location, company, description=""):
    """Validates job role using live settings from filter_settings.json."""
    profile = get_active_profile_settings()
    
    title_lower = title.lower()
    loc_lower = location.lower()
    comp_lower = company.lower()
    full_text = f"{title_lower} {loc_lower} {comp_lower} {description.lower()}"

    # 1. Reject Excluded Keywords
    if any(ex in full_text for ex in profile.get("exclude_keywords", [])):
        return False

    # 2. Reject Excluded Locations
    if any(ex_loc in full_text for ex_loc in profile.get("exclude_locations", [])):
        return False

    # 3. Reject Excluded Skills (e.g., C, C++)
    if any(skill in full_text for skill in profile.get("excluded_skills", [])):
        return False

    # 4. Check Graduation Year Mismatches (e.g., Class of 2026 vs 2028/2029)
    grad_years = profile.get("grad_years", [])
    if grad_years:
        grad_matches = re.findall(r"(?:class of|graduating in|graduation year|graduating)\s*(\d{4})", full_text)
        if grad_matches:
            if not any(g in grad_years for g in grad_matches):
                return False

    # 5. Require Included Keywords
    inc_keywords = profile.get("include_keywords", [])
    if inc_keywords and not any(rk in full_text for rk in inc_keywords):
        return False

    # 6. Location Match
    target_locations = profile.get("target_locations", [])
    if target_locations:
        if not (any(loc in loc_lower for loc in target_locations) or "remote" in loc_lower or "uk" in loc_lower or loc_lower == ""):
            return False

    return True

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
            json.dump(list(seen_jobs), f)
    except Exception:
        pass

def load_discovered_jobs():
    if os.path.exists(DISCOVERED_JOBS_FILE):
        try:
            with open(DISCOVERED_JOBS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_discovered_jobs(jobs):
    try:
        with open(DISCOVERED_JOBS_FILE, "w") as f:
            json.dump(jobs[:1000], f, indent=2)
    except Exception:
        pass

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

def scrape_trackr_website(seen_jobs, discovered_list):
    """Scrapes official Trackr REST API (api.the-trackr.com) with active profile filters."""
    new_jobs = []
    source_name = "Trackr API"
    types = ["summer-internships", "industrial-placements", "graduate-schemes", "spring-weeks"]
    seasons = ["2027", "2026", "2025"]
    relevant_found = 0
    total_items_fetched = 0

    print("  [Trackr API] Fetching live UK Tech schemes from api.the-trackr.com...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    for t in types:
        for season in seasons:
            url = f"https://api.the-trackr.com/programmes?region=UK&industry=Tech&type={t}&season={season}"

            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        items = data if isinstance(data, list) else data.get("programmes", data.get("data", []))
                        total_items_fetched += len(items)

                        for item in items:
                            if isinstance(item, dict):
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
            except Exception:
                pass

    print(f"  [Trackr Summary] Fetched {total_items_fetched} raw items across categories ({relevant_found} active schemes matching profile)")
    SCRAPER_STATUS["source_status"][source_name] = f"OK ({total_items_fetched} items fetched, {relevant_found} active schemes)"
    return new_jobs

def run_all_scrapers():
    print("\n🔍 Running Active UK Scraper Engine...")
    seen_jobs = load_seen_jobs()
    discovered_list = load_discovered_jobs()
    all_new_jobs = []

    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs, discovered_list))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs, discovered_list))
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