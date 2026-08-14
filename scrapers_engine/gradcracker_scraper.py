import re
import time
import requests
import concurrent.futures
from bs4 import BeautifulSoup
from config import SCRAPER_STATUS, add_scraper_log, update_source_status
from scrapers_engine.ats_scrapers import is_relevant_role, add_discovered_job, extract_and_register_ats_company, _JOB_LOCK

GRADCRACKER_SECTOR_URLS = [
    ("Computing & Technology", "https://www.gradcracker.com/search/computing-technology/work-placements-internships"),
    ("Computing & Technology", "https://www.gradcracker.com/search/computing-technology/graduate-jobs"),
    ("Maths & Actuarial", "https://www.gradcracker.com/search/maths-actuarial/work-placements-internships"),
    ("Maths & Actuarial", "https://www.gradcracker.com/search/maths-actuarial/graduate-jobs")
]

def scrape_gradcracker_website(seen_jobs, discovered_list, force_rescan=False, log_func=None, scraper_status=None):
    source_name = "Gradcracker API"
    if log_func:
        log_func("  ├── 🟢 [Gradcracker API] Scanning UK STEM Computing & Maths sectors on Gradcracker.com...")

    new_jobs = []
    total_items_fetched = 0
    relevant_found = 0

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    def fetch_sector(sector_tuple):
        nonlocal total_items_fetched, relevant_found
        sector_name, url = sector_tuple
        local_new = []

        try:
            resp = requests.get(url, headers=headers, timeout=7)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                job_containers = soup.find_all('div', class_=re.compile(r'tw-bg-white|job-card|tw-border'))

                if not job_containers:
                    job_containers = soup.find_all('div', id=re.compile(r'job-container|job_\d+'))

                sector_fetched = 0
                sector_relevant = 0

                for container in job_containers:
                    comp_elem = container.find('a', class_=re.compile(r'company|employer|tw-font-bold')) or container.find(['h3', 'h4', 'strong'])
                    title_elem = container.find('a', class_=re.compile(r'job-title|title|tw-text-')) or container.find('h2')
                    link_elem = container.find('a', href=re.compile(r'/hub/|/graduate-job/|/work-placement/|/internship/')) or container.find('a', href=True)

                    if not title_elem or not link_elem:
                        continue

                    company = comp_elem.get_text(strip=True) if comp_elem else "Gradcracker Employer"
                    title = title_elem.get_text(strip=True)
                    link = link_elem['href']
                    if not link.startswith("http"):
                        link = f"https://www.gradcracker.com{link}"

                    loc_elem = container.find(text=re.compile(r'London|Remote|UK|Manchester|Birmingham|Oxford|Cambridge', re.I))
                    location = loc_elem.strip() if loc_elem else "UK"

                    sector_fetched += 1
                    with _JOB_LOCK:
                        total_items_fetched += 1

                    extract_and_register_ats_company(link)

                    if is_relevant_role(title, location, company):
                        job_id = f"gc_{hash(company.lower() + title.lower())}"
                        is_new = add_discovered_job(
                            discovered_list, job_id, company, title, location, link, "Gradcracker", "https://www.gradcracker.com"
                        )

                        with _JOB_LOCK:
                            relevant_found += 1
                            if is_new and job_id not in seen_jobs:
                                seen_jobs.add(job_id)
                                local_new.append((f"{company} - {title}", location, link))

                if sector_relevant > 0 and log_func:
                    log_func(f"  │   ↳ Gradcracker {sector_name}: {sector_fetched} fetched ({sector_relevant} relevant)")

        except Exception as e:
            pass

        return local_new

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(fetch_sector, GRADCRACKER_SECTOR_URLS)
        for res in results:
            new_jobs.extend(res)

    status_str = f"🟢 Active • 4 STEM Sectors Online ({relevant_found} active schemes indexed)"
    update_source_status(source_name, status_str)
    if log_func:
        log_func(f"  │   ↳ Gradcracker Summary: Scanned 4 sectors ({relevant_found} active STEM schemes indexed)")

    return new_jobs
