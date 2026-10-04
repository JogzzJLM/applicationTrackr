"""UK student boards independent of Trackr, with real pagination and detail checks."""
import concurrent.futures
import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from config import update_source_status, update_scraper_status
from core.storage import DATA_DIR, atomic_write_json, load_json_safe
from core.relevance import evaluate_job
from scrapers_engine.ats_scrapers import add_discovered_job, extract_and_register_ats_company
from scrapers_engine.verifier import deadline_date

CACHE_FILE = str(DATA_DIR / 'uk_board_cache.json')
HEADERS = {'User-Agent': 'Mozilla/5.0'}


def higherin_payload(markup):
    marker = 'window.__RMP_SEARCH_RESULTS_INITIAL_STATE__'
    for script in BeautifulSoup(markup, 'html.parser').find_all('script'):
        text = script.get_text()
        if marker in text:
            return json.JSONDecoder().raw_decode(text.split(marker, 1)[1].split('=', 1)[1].lstrip())[0]
    raise ValueError('Higherin public search data missing')


def parse_grb(markup, base):
    soup = BeautifulSoup(markup, 'html.parser')
    rows = []
    for card in soup.select('.e-loop-item'):
        title = card.find('h2')
        company = card.select_one('span.elementor-heading-title')
        link = card.find('a', href=re.compile(r'/(?:internships|placements)/[^/?]+/?$'))
        if not title or not company or not link: continue
        classes = ' '.join(card.get('class', []))
        loc = re.search(r'grb_job_location-([a-z-]+)', classes)
        rows.append({'company': company.get_text(' ', strip=True), 'title': title.get_text(' ', strip=True),
                     'location': loc.group(1).replace('-', ' ').title() if loc else 'Unknown',
                     'link': urljoin(base, link['href']), 'metadata': {'source_region': 'UK'}})
    return rows


def _fetch_higherin():
    rows, pages = [], 0
    for kind in ('internships', 'placements'):
        base = f'https://higherin.com/search-jobs/{kind}/technology'
        page, last = 1, 1
        while page <= last:
            r = requests.get(base, params={'page': page}, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = higherin_payload(r.text)
            last = min(int(data.get('meta', {}).get('pagination', {}).get('lastPage', 1)), 30)
            for item in data['data']:
                if item.get('isPreReg'): continue
                rows.append({'company': item.get('companyName', ''), 'title': item.get('jobTitle', ''),
                             'location': item.get('jobLocationNames', ''), 'link': item.get('url', ''),
                             'metadata': {'programme_type': 'placements' if kind == 'placements' else 'summer-internships',
                                          'closing_date': item.get('deadline', ''), 'employment_type': item.get('jobTypeName', '')}})
            page += 1; pages += 1
    return rows, pages


def _fetch_grb():
    rows, pages = [], 0
    for kind in ('internships', 'placements'):
        base = f'https://www.grb.uk.com/{kind}/'
        url, seen = base, set()
        while url and url not in seen and len(seen) < 20:
            seen.add(url)
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            rows.extend(parse_grb(r.text, base)); pages += 1
            soup = BeautifulSoup(r.text, 'html.parser')
            next_link = soup.select_one('a.next.page-numbers, a[rel="next"]')
            url = urljoin(base, next_link['href']) if next_link else None
            if url and urlparse(url).netloc != urlparse(base).netloc: break
    if not rows: raise ValueError('GRB parser returned no cards')
    return rows, pages


def scrape_uk_boards(seen_jobs, discovered, scraper_status=None):
    cache = load_json_safe(CACHE_FILE, {})
    new_jobs, reports = [], {}
    for name, fetch in [('Higherin', _fetch_higherin), ('GRB', _fetch_grb)]:
        saved = cache.get(name, {})
        error = ''
        # Refresh hourly; failed source attempts back off for 30 minutes.
        if time.time() - saved.get('attempted_at', 0) >= (1800 if saved.get('error') else 3600):
            try:
                rows, pages = fetch()
                saved = {'rows': rows, 'pages': pages, 'saved_at': time.time(), 'attempted_at': time.time()}
            except (requests.RequestException, ValueError, KeyError) as exc:
                error = f'{type(exc).__name__}; retaining last successful snapshot'
                saved.update(attempted_at=time.time(), error=error)
            cache[name] = saved
            atomic_write_json(CACHE_FILE, cache)
        rows = saved.get('rows', [])
        eligible = [row for row in rows if evaluate_job(row['title'], row['company'], row['location'], row['metadata']).eligible]
        accepted = 0
        def ingest(row):
            link = row['link']; extract_and_register_ats_company(link)
            job_id = name.lower() + '_' + hashlib.sha256(link.encode()).hexdigest()[:20]
            added = add_discovered_job(discovered, job_id, row['company'], row['title'], row['location'], link, name, link, metadata=row['metadata'])
            return job_id, row, added
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for job_id, row, added in pool.map(ingest, eligible):
                if added and job_id not in seen_jobs:
                    seen_jobs.add(job_id)
                    new_jobs.append((row['company'] + ' - ' + row['title'], row['location'], row['link']))
                accepted += int(added)
        reports[name] = {'fetched': len(rows), 'relevant_candidates': len(eligible), 'added': accepted,
                         'pages': saved.get('pages', 0), 'last_success': saved.get('saved_at'), 'error': saved.get('error', '')}
        if saved.get('error'):
            update_source_status(name, f'🟠 Unavailable • {len(rows)} cached candidates • retry in 30m')
        else:
            update_source_status(name, f'🟢 {len(rows)} fetched across {saved.get("pages", 0)} pages • {len(eligible)} technical candidates')
    update_scraper_status('uk_board_coverage', reports)
    return new_jobs
