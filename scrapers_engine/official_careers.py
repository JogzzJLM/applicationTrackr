"""Discover employer-hosted listings and embedded ATS boards from official pages.

Pages are seeds, not a guarantee of vacancies. Every candidate goes through the
same live-page, geography and programme checks as other discovery sources.
"""
import concurrent.futures
import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from config import update_source_status, update_scraper_status
from core.storage import load_settings
from scrapers_engine.ats_scrapers import _record_job, extract_and_register_ats_company
from hashlib import sha256
from scrapers_engine.verifier import verify_listing

DEFAULT_CAREER_PAGES = [
    'https://www.cambridgeconsultants.com/careers/',
    'https://www.twosigma.com/careers/',
    'https://jobs.bloomberg.com/',
]


def career_links(markup, base):
    soup = BeautifulSoup(markup, 'html.parser')
    employer = soup.select_one('meta[property="og:site_name"]')
    company = employer.get('content', '') if employer else ''
    boards, candidates, portals = set(), [], set()
    for element in soup.select('a[href], iframe[src], script[src]'):
        url = urljoin(base, element.get('href') or element.get('src'))
        if urlparse(url).scheme not in {'http', 'https'}: continue
        if re.search(r'greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com', url): boards.add(url)
        if element.name != 'a': continue
        title = element.get_text(' ', strip=True)
        if re.search(r'\b(?:intern|internship|placement|graduate)\b', title, re.I) and re.search(r'/jobs?/|/JobDetail/|/careers/details/', url, re.I):
            candidates.append((title, url))
        elif re.search(r'^(?:view |explore )?open (?:roles|positions)|^search (?:jobs|roles)', title, re.I):
            portals.add(url)
    return company, boards, candidates, portals


def scrape_official_careers(seen_jobs, discovered_list, scraper_status=None):
    pages = load_settings().get('employer_career_pages', DEFAULT_CAREER_PAGES)
    failures, candidates = {}, {}
    online = 0
    def read(url):
        try:
            response = requests.get(url, timeout=10, headers={'User-Agent':'Mozilla/5.0'})
            if response.status_code != 200: return url, None, f'HTTP {response.status_code}'
            return url, career_links(response.text, response.url), ''
        except (requests.RequestException, ValueError): return url, None, 'Network error'
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        initial = list(pool.map(read, pages[:20]))
        portals = {}
        for url, result, error in initial:
            if error: failures[url] = error; continue
            online += 1
            company, boards, roles, follow = result
            for board in boards: extract_and_register_ats_company(board)
            for title, link in roles: candidates[link] = (company, title)
            for link in follow: portals[link] = company
        for url, result, error in pool.map(read, list(portals)[:20]):
            if error: failures[url] = error; continue
            company, boards, roles, _ = result
            for board in boards: extract_and_register_ats_company(board)
            for title, link in roles: candidates[link] = (portals[url] or company, title)
    found = []
    def check(item):
        link, (company, title) = item
        if not company: return []
        verified = verify_listing(link, title)
        if verified.get('state') != 'verified': return []
        local = []
        metadata = {k: verified[k] for k in ('description', 'country', 'closing_date', 'employment_type') if verified.get(k)}
        _record_job(discovered_list, seen_jobs, local, 0,
                    job_id='official_' + sha256(link.encode()).hexdigest()[:20], company=company,
                    title=verified.get('title') or title, location=verified.get('location', ''), job_url=link, source='Official employer careers',
                    board_url=link, metadata=metadata)
        return local
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for rows in pool.map(check, list(candidates.items())[:80]): found.extend(rows)
    update_source_status('Official employer careers', f"{'🟢' if not failures else '🟠'} {online}/{len(pages)} career pages read • {len(candidates)} candidates checked")
    update_scraper_status('official_careers_coverage', {'online':online,'configured':len(pages),'candidates':len(candidates),'failures':failures})
    return found
