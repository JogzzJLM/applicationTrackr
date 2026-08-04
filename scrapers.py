import os
import json
import time
import requests
from bs4 import BeautifulSoup
from config import (
    SEEN_JOBS_FILE, DISCOVERED_JOBS_FILE, SCRAPER_STATUS, EXCLUDE_KEYWORDS, EXCLUDE_LOCATIONS,
    LEVEL_KEYWORDS, ROLE_KEYWORDS, LOCATION_KEYWORDS, SPECIAL_INTL_COMPANIES,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES
)
from notifications import send_notification

def is_relevant_role(title, location, company):
    title_lower = title.lower()
    loc_lower = location.lower()
    comp_lower = company.lower()
    full_text = f"{title_lower} {loc_lower}"

    # 1. Reject non-technical keywords
    if any(ex in title_lower for ex in EXCLUDE_KEYWORDS):
        return False

    # 2. Reject foreign/non-UK locations (e.g., US Government, France, Poland)
    if any(ex_loc in full_text for ex_loc in EXCLUDE_LOCATIONS):
        return False

    # 3. Must match internship/grad level
    if not any(lvl in title_lower for lvl in LEVEL_KEYWORDS):
        return False

    # 4. Must match software/quant domain
    if not any(rk in title_lower for rk in ROLE_KEYWORDS):
        return False

    # 5. Allow special international companies (e.g., BeamNG in Germany)
    if any(sc in comp_lower for sc in SPECIAL_INTL_COMPANIES):
        return True

    # 6. Must be UK / London / Remote / West Midlands
    if any(loc in loc_lower for loc in LOCATION_KEYWORDS) or "remote" in loc_lower or "uk" in loc_lower or loc_lower == "":
        return True

    return False

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
            json.dump(jobs[:250], f, indent=2)
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

    print(f"  [Greenhouse API] Scanning {len(GREENHOUSE_COMPANIES)} target companies...")
    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=10)
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
        except Exception as e:
            print(f"    ⚠️ Greenhouse error [{company}]: {e}")

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(GREENHOUSE_COMPANIES)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_lever_jobs(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Lever API"
    companies_scanned = 0
    relevant_found = 0

    print(f"  [Lever API] Scanning {len(LEVER_COMPANIES)} target companies...")
    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=10)
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
        except Exception as e:
            print(f"    ⚠️ Lever error [{company}]: {e}")

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({companies_scanned}/{len(LEVER_COMPANIES)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_trackr_website(seen_jobs, discovered_list):
    new_jobs = []
    source_name = "Trackr Web"
    url = "https://the-trackr.com"
    relevant_found = 0

    print("  [Trackr Web] Scraping the-trackr.com table listings...")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.find_all("tr")

            for row in rows:
                cols = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cols) >= 2:
                    company = cols[1] if len(cols) > 1 else ""
                    role = cols[2] if len(cols) > 2 else ""
                    
                    if company and role and company.lower() != "company name":
                        full_title = f"{company} - {role}"
                        link_tag = row.find("a", href=True)
                        href = link_tag["href"] if link_tag else url
                        full_url = href if href.startswith("http") else f"https://the-trackr.com{href}"
                        job_id = f"trackr_{hash(full_title)}"

                        if is_relevant_role(full_title, "UK", company):
                            relevant_found += 1
                            add_discovered_job(discovered_list, job_id, company, role, "UK", full_url, source_name)

                            if job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                new_jobs.append((full_title, "UK", full_url))

            print(f"    ↳ Trackr: Scraped {len(rows)} rows ({relevant_found} relevant matching schemes)")
            SCRAPER_STATUS["source_status"][source_name] = f"OK ({relevant_found} active schemes found)"
        else:
            SCRAPER_STATUS["source_status"][source_name] = f"HTTP {resp.status_code}"
    except Exception as e:
        print(f"    ⚠️ Trackr Error: {e}")
        SCRAPER_STATUS["source_status"][source_name] = f"Error: {e}"

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