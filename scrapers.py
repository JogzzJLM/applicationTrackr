import os
import json
import requests
from config import (
    SEEN_JOBS_FILE, SCRAPER_STATUS, EXCLUDE_KEYWORDS, LEVEL_KEYWORDS,
    ROLE_KEYWORDS, LOCATION_KEYWORDS, SPECIAL_INTL_COMPANIES,
    GREENHOUSE_COMPANIES, LEVER_COMPANIES
)
from notifications import send_notification

def is_relevant_role(title, location, company):
    title_lower = title.lower()
    loc_lower = location.lower()
    comp_lower = company.lower()

    if any(ex in title_lower for ex in EXCLUDE_KEYWORDS):
        return False
    if not any(lvl in title_lower for lvl in LEVEL_KEYWORDS):
        return False
    if not any(rk in title_lower for rk in ROLE_KEYWORDS):
        return False
    if any(sc in comp_lower for sc in SPECIAL_INTL_COMPANIES):
        return True
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

def scrape_greenhouse_jobs(seen_jobs):
    new_jobs = []
    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for job in resp.json().get("jobs", []):
                    title = job.get("title", "")
                    location = job.get("location", {}).get("name", "")
                    job_url = job.get("absolute_url", "")
                    job_id = f"gh_{company}_{job.get('id')}"

                    if job_id not in seen_jobs and is_relevant_role(title, location, company):
                        seen_jobs.add(job_id)
                        new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
        except Exception:
            pass
    return new_jobs

def scrape_lever_jobs(seen_jobs):
    new_jobs = []
    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for job in resp.json():
                    title = job.get("text", "")
                    location = job.get("categories", {}).get("location", "")
                    job_url = job.get("hostedUrl", "")
                    job_id = f"lever_{company}_{job.get('id')}"

                    if job_id not in seen_jobs and is_relevant_role(title, location, company):
                        seen_jobs.add(job_id)
                        new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
        except Exception:
            pass
    return new_jobs

def run_all_scrapers():
    print("\n🔍 Running Scraper Engine...")
    seen_jobs = load_seen_jobs()
    all_new_jobs = []

    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs))
    save_seen_jobs(seen_jobs)

    if all_new_jobs:
        print(f"🚨 FOUND {len(all_new_jobs)} NEW MATCHING ROLES!")
        for title, location, link in all_new_jobs:
            send_notification(
                title=f"New Role: {title}",
                message=f"Location: {location}\nTap to open application link!",
                link=link,
                tags="sparkles,uk",
                priority=3,
                sound="bing"
            )