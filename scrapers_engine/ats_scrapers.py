import re
import time
import requests
import concurrent.futures
from core.normalization import normalize_company, normalize_role, normalize_url, extract_ats_post_id
from core.storage import load_reported_closed_jobs, load_settings, save_settings
from core.scoring import calculate_skill_match_score
from scrapers_engine.verifier import verify_live_page_applyable

_JOB_LOCK = concurrent.futures.ThreadPoolExecutor.__module__ and __import__('threading').Lock()

def validate_job_legitimacy(company, title, link):
    if not company or not title or not isinstance(company, str) or not isinstance(title, str):
        return False
    if not link or not isinstance(link, str) or not link.startswith("http"):
        return False
    t_lower = title.lower()
    c_lower = company.lower()
    noise_words = ["test", "demo", "sample", "undefined", "null", "placeholder", "test job", "do not apply"]
    if any(nw in t_lower for nw in noise_words) or any(nw in c_lower for nw in noise_words):
        return False
    return True

def is_relevant_role(title, location="", company=""):
    settings = load_settings()
    text = f"{title} {location} {company}".lower()

    for kw in settings.get("exclude_keywords", []):
        if kw.lower() in text:
            return False

    for loc in settings.get("exclude_locations", []):
        if loc.lower() in text:
            return False

    has_role_kw = any(kw.lower() in text for kw in settings.get("role_keywords", []))
    has_level_kw = any(kw.lower() in text for kw in settings.get("level_keywords", []))

    return has_role_kw and has_level_kw

def add_discovered_job(discovered_list, job_id, company, title, location, link, source, source_url=None):
    if not validate_job_legitimacy(company, title, link):
        return False

    norm_c = normalize_company(company)
    norm_t = normalize_role(title)
    norm_u = normalize_url(link)
    ats_id = extract_ats_post_id(link)

    closed_map = load_reported_closed_jobs()
    if (job_id in closed_map) or (norm_u in set(c.get("link") for c in closed_map.values() if c.get("link"))):
        return False

    for c_job in closed_map.values():
        if normalize_company(c_job.get("company")) == norm_c and normalize_role(c_job.get("title")) == norm_t:
            return False

    if not verify_live_page_applyable(link):
        return False

    if not source_url:
        source_url = link

    score = calculate_skill_match_score(title, company, location)

    with _JOB_LOCK:
        for item in discovered_list:
            item_url = normalize_url(item.get("link", ""))
            item_ats = extract_ats_post_id(item.get("link", ""))
            item_c = normalize_company(item.get("company"))
            item_t = normalize_role(item.get("title"))

            is_match = False
            if ats_id and item_ats and ats_id == item_ats:
                is_match = True
            elif norm_u and item_url and norm_u == item_url:
                is_match = True
            elif norm_c and norm_t and item_c == norm_c and item_t == norm_t:
                is_match = True

            if is_match:
                if "sources" not in item:
                    item["sources"] = [item.get("source", "Discovered API")]
                if source not in item["sources"]:
                    item["sources"].append(source)
                if any(ats in link.lower() for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]):
                    item["link"] = link
                    item["source_url"] = source_url
                item["match_score"] = score
                return False

        entry = {
            "id": job_id,
            "company": company,
            "title": title,
            "location": location if location else "UK / Remote",
            "link": link,
            "source": source,
            "sources": [source],
            "source_url": source_url,
            "match_score": score,
            "date_found": time.strftime("%Y-%m-%d %H:%M")
        }
        discovered_list.insert(0, entry)
        return True

def extract_and_register_ats_company(url):
    if not url or not isinstance(url, str):
        return
    settings = load_settings()
    updated = False

    gh_match = re.search(r'greenhouse\.io/([^/?#]+)', url, re.IGNORECASE)
    if gh_match:
        comp = gh_match.group(1).lower().strip()
        gh_list = settings.get("greenhouse_companies", [])
        if comp and comp not in gh_list and comp not in ["embed", "jobs", "embeds"]:
            gh_list.append(comp)
            settings["greenhouse_companies"] = gh_list
            updated = True
            print(f"  [ATS Auto-Discovery] Added new Greenhouse company: {comp}")

    lev_match = re.search(r'lever\.co/([^/?#]+)', url, re.IGNORECASE)
    if lev_match:
        comp = lev_match.group(1).lower().strip()
        lev_list = settings.get("lever_companies", [])
        if comp and comp not in lev_list and comp not in ["jobs"]:
            lev_list.append(comp)
            settings["lever_companies"] = lev_list
            updated = True
            print(f"  [ATS Auto-Discovery] Added new Lever company: {comp}")

    ash_match = re.search(r'ashbyhq\.com/([^/?#]+)', url, re.IGNORECASE)
    if ash_match:
        comp = ash_match.group(1).lower().strip()
        ash_list = settings.get("ashby_companies", [])
        if comp and comp not in ash_list and comp not in ["jobs"]:
            ash_list.append(comp)
            settings["ashby_companies"] = ash_list
            updated = True
            print(f"  [ATS Auto-Discovery] Added new Ashby company: {comp}")

    if updated:
        save_settings(settings)

def scrape_greenhouse_jobs(seen_jobs, discovered_list, scraper_status=None):
    settings = load_settings()
    companies = settings.get("greenhouse_companies", [])
    new_jobs = []
    source_name = "Greenhouse API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in companies if c.strip()]))
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

    if scraper_status is not None:
        scraper_status["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_lever_jobs(seen_jobs, discovered_list, scraper_status=None):
    settings = load_settings()
    companies = settings.get("lever_companies", [])
    new_jobs = []
    source_name = "Lever API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.split('?')[0].split('#')[0].strip() for c in companies if c.strip()]))
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

    if scraper_status is not None:
        scraper_status["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_ashby_jobs(seen_jobs, discovered_list, scraper_status=None):
    settings = load_settings()
    companies = settings.get("ashby_companies", [])
    new_jobs = []
    source_name = "Ashby API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in companies if c.strip()]))
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

    if scraper_status is not None:
        scraper_status["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs

def scrape_smartrecruiters_jobs(seen_jobs, discovered_list, scraper_status=None):
    settings = load_settings()
    companies = settings.get("smartrecruiters_companies", [])
    new_jobs = []
    source_name = "SmartRecruiters API"
    companies_scanned = 0
    relevant_found = 0

    clean_companies = list(set([c.strip() for c in companies if c.strip()]))
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

    if scraper_status is not None:
        scraper_status["source_status"][source_name] = f"OK ({companies_scanned}/{len(clean_companies)} companies online, {relevant_found} active schemes)"
    return new_jobs
