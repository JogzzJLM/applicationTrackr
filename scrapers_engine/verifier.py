"""Evidence-based checks: blocked/network responses are unknown, never closed/open."""
import html
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from core.storage import DATA_DIR, atomic_write_json, load_json_safe

CACHE_FILE = str(DATA_DIR / 'listing_checks.json')
_LOCK = threading.RLock()
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html,application/json'}


def plain(value):
    return BeautifulSoup(html.unescape(str(value or '')), 'html.parser').get_text(' ', strip=True)


def deadline_date(value):
    value = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', str(value or ''))
    for fmt in ('%Y-%m-%d', '%d %B %Y', '%d %b %Y', '%d/%m/%Y'):
        try:
            result = datetime.strptime(value[:10] if fmt == '%Y-%m-%d' else value, fmt).date()
            # Some boards use 2036-01-01 as a rolling-deadline sentinel.
            return result if result.year <= date.today().year + 2 else None
        except ValueError:
            pass
    return None


def job_postings(markup):
    def walk(value):
        if isinstance(value, list):
            for item in value: yield from walk(item)
        elif isinstance(value, dict):
            if value.get('@type') == 'JobPosting': yield value
            for key in ('@graph', 'itemListElement', 'item', 'mainEntity'):
                if key in value: yield from walk(value[key])
    soup = BeautifulSoup(markup, 'html.parser')
    for script in soup.find_all('script', type='application/ld+json'):
        try: yield from walk(json.loads(script.get_text()))
        except (ValueError, TypeError): continue


def posting_fields(post):
    locations = post.get('jobLocation') or []
    if isinstance(locations, dict): locations = [locations]
    cities, countries = [], []
    for loc in locations:
        address = loc.get('address') or {}
        if isinstance(address, str): cities.append(address); continue
        cities.extend(str(address[k]) for k in ('addressLocality', 'addressRegion') if address.get(k))
        country = address.get('addressCountry', '')
        if isinstance(country, dict): country = country.get('name', '')
        if country: countries.append(str(country))
    company = post.get('hiringOrganization') or {}
    return {'title': plain(post.get('title', '')), 'company': plain(company.get('name', '')),
            'location': ', '.join(dict.fromkeys(cities + countries)),
            'description': plain(post.get('description')), 'country': ', '.join(dict.fromkeys(countries)),
            'closing_date': post.get('validThrough', ''), 'published_at': post.get('datePosted', ''),
            'employment_type': ', '.join(post.get('employmentType', [])) if isinstance(post.get('employmentType'), list) else post.get('employmentType', '')}


def _result(state, reason, **fields):
    return {'state': state, 'reason': reason, 'checked_at': datetime.now(timezone.utc).isoformat(), **fields}


def _workday(url):
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith('.myworkdayjobs.com'): return None
    # The public Workday detail endpoint supplies the description missing from SPA HTML.
    parts = parsed.path.strip('/').split('/')
    if 'job' not in parts: return None
    idx = parts.index('job')
    if idx < 1: return None
    tenant = parsed.hostname.split('.')[0]
    site = parts[idx - 1]
    r = requests.get(f'https://{parsed.hostname}/wday/cxs/{tenant}/{site}/' + '/'.join(parts[idx:]), headers=HEADERS, timeout=10)
    if r.status_code != 200: return None
    info = r.json().get('jobPostingInfo') or {}
    if not info.get('title') or not info.get('jobDescription'): return None
    return _result('verified', 'Employer Workday job detail', title=info['title'], description=plain(info['jobDescription']),
                   location=info.get('location', ''), country=info.get('country', {}).get('descriptor', '') if isinstance(info.get('country'), dict) else '',
                   published_at=info.get('startDate', ''), final_url=url)


def _check(url, expected_title=''):
    parsed = urlparse(str(url))
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return _result('closed', 'Missing or invalid listing URL')
    if re.fullmatch(r'/hub/\d+/[^/]+/?', parsed.path) or parsed.path.rstrip('/') in {'', '/jobs', '/careers', '/search-jobs'}:
        return _result('unknown', 'Employer/search portal rather than a specific job')
    try:
        wd = _workday(url)
        if wd: return wd
        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code in (404, 410): return _result('closed', f'HTTP {r.status_code}')
        if not 200 <= r.status_code < 300: return _result('unknown', f'HTTP {r.status_code}; cannot verify')
        soup = BeautifulSoup(r.text, 'html.parser')
        for script in soup.find_all(['script', 'style', 'nav', 'footer']): script.decompose()
        text = soup.get_text(' ', strip=True).lower()
        if any(p in text for p in ('verify you are human', 'checking your browser', 'access denied', 'enable javascript and cookies')):
            return _result('unknown', 'Access challenge; cannot verify')
        # Whole-page proximity previously mistook unrelated footer/description text
        # for closure. These explicit phrases must refer to the current posting.
        closed = re.search(r'(?:this|the)\s+(?:job|position|vacancy|posting|role)\s+(?:is\s+no\s+longer\s+available|has\s+(?:been\s+)?(?:filled|closed|expired|removed))|(?:applications for this (?:role|job)|this (?:job|position))\s+(?:are|is)\s+closed|job not found', text)
        if closed: return _result('closed', 'Explicit posting closure')
        posts = list(job_postings(r.text))
        if len(posts) == 1:
            fields = posting_fields(posts[0])
            closing = deadline_date(fields.get('closing_date'))
            if closing and closing < date.today(): return _result('closed', 'Application deadline passed', **fields)
            if fields['title'] and fields['description']:
                return _result('verified', 'Specific JobPosting with job description', final_url=r.url, **fields)
        headings = ' '.join(h.get_text(' ', strip=True) for h in soup.find_all(['h1', 'h2'])).lower()
        title_tokens = [w for w in re.findall(r'[a-z]+', expected_title.lower()) if len(w) > 3 and w not in {'summer', 'programme', 'program', 'internship'}]
        matches = sum(w in headings for w in title_tokens)
        apply_controls = [x for x in soup.find_all(['a', 'button', 'input']) if re.search(r'\bapply\b', x.get_text(' ', strip=True) + ' ' + str(x.get('value', '')), re.I)]
        form = soup.find('form')
        specific = len(urlparse(r.url).path.strip('/').split('/')) >= 2
        if specific and matches >= min(2, max(1, len(title_tokens))) and (apply_controls or form) and len(text) > 300:
            location_node = soup.select_one('.job__location, #header .location, .posting-categories .location, .job-location')
            description_node = soup.select_one('.job__description, #content, .posting-page .section-wrapper')
            title_node = soup.find('h1')
            return _result('verified', 'Matching job heading and application control', final_url=r.url,
                           location=location_node.get_text(' ', strip=True) if location_node else '',
                           title=title_node.get_text(' ', strip=True) if title_node else expected_title,
                           description=(description_node or soup).get_text(' ', strip=True)[:14000])
        return _result('unknown', 'No specific live job/application evidence')
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return _result('unknown', 'Network or response error; will retry')


def verify_listing(url, expected_title='', force=False):
    with _LOCK:
        cached = load_json_safe(CACHE_FILE, {}).get(url)
    if cached and not force:
        age = time.time() - cached.get('saved_at', 0)
        ttl = 6 * 3600 if cached.get('state') == 'verified' else 30 * 60
        if age < ttl: return dict(cached)
    result = _check(url, expected_title)
    result['saved_at'] = time.time()
    with _LOCK:
        checks = load_json_safe(CACHE_FILE, {})
        checks[url] = result
        checks = dict(sorted(checks.items(), key=lambda x: x[1].get('saved_at', 0), reverse=True)[:2000])
        atomic_write_json(CACHE_FILE, checks)
    return result


def verify_live_page_applyable(url):
    # Compatibility for the explicit closure audit: unknown isn't evidence of closure.
    return verify_listing(url)['state'] != 'closed'
