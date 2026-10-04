import concurrent.futures
import re
import threading
import time

import requests
from bs4 import BeautifulSoup

from core.normalization import extract_ats_post_id, fuzzy_roles_match, normalize_company, normalize_role, normalize_url, same_listing
from core.relevance import evaluate_job
from core.storage import load_closed_urls_cache, load_reported_closed_jobs, load_settings, mark_url_as_closed, save_settings
from scrapers_engine.verifier import verify_listing, deadline_date
from scrapers_engine.quality import record_review, clear_review
from datetime import date, datetime, timezone
from config import update_source_status

_JOB_LOCK = threading.Lock()
_REGISTRY_LOCK = threading.Lock()


def _plain_html(value):
    if not value:
        return ""
    try:
        return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", str(value))


def validate_job_legitimacy(company, title, link):
    if not company or not title or not isinstance(company, str) or not isinstance(title, str):
        return False
    if not link or not isinstance(link, str) or not link.startswith("http"):
        return False
    text = f"{company} {title}".lower()
    return not any(x in text for x in ("test job", "do not apply", "undefined", "placeholder"))


def relevance_decision(title, location="", company="", metadata=None):
    return evaluate_job(title, company, location, metadata=metadata or {})


def is_relevant_role(title, location="", company="", metadata=None):
    return relevance_decision(title, location, company, metadata).eligible


def _compact_metadata(metadata):
    metadata = dict(metadata or {})
    allowed = {"description", "department", "team", "employment_type", "commitment", "workplace_type", "country", "source_region", "apply_url", "published_at", "division", "eligibility", "location_notes", "closing_date", "opening_date", "programme_type", "season", "listing_status"}
    out = {k: v for k, v in metadata.items() if k in allowed and v not in (None, "", [], {})}
    if out.get("description"):
        out["description"] = str(out["description"])[:8000]
    return out


def add_discovered_job(discovered_list, job_id, company, title, location, link, source, source_url=None, metadata=None, relevance=None):
    if not validate_job_legitimacy(company, title, link):
        return False
    metadata = _compact_metadata(metadata)
    relevance = relevance or evaluate_job(title, company, location, metadata=metadata)
    if not relevance.eligible:
        return False

    norm_c, norm_t, norm_u = normalize_company(company), normalize_role(title), normalize_url(link)
    ats_id = extract_ats_post_id(link)
    closed_map = load_reported_closed_jobs()
    if job_id in closed_map or norm_u in {normalize_url(c.get("link", "")) for c in closed_map.values()}:
        mark_url_as_closed(link)
        return False
    for c_job in closed_map.values():
        if normalize_company(c_job.get("company")) == norm_c and (normalize_role(c_job.get("title")) == norm_t or fuzzy_roles_match(title, c_job.get("title"))):
            # A closed programme from last year must not ban a new requisition.
            if normalize_url(c_job.get('link', '')) == norm_u:
                return False
    if metadata.get('listing_status') == 'published' and source.startswith(('Greenhouse (', 'Lever (', 'Ashby (', 'SmartRecruiters (')):
        verification = {'state': 'verified', 'reason': 'Published employer ATS feed', 'checked_at': datetime.now(timezone.utc).isoformat()}
    else:
        verification = verify_listing(link, title)
    if verification['state'] != 'verified':
        record_review(company, title, link, source, verification['reason'], verification['state'])
        return False
    for key in ('description', 'country', 'closing_date', 'published_at', 'employment_type'):
        if verification.get(key): metadata[key] = verification[key]
    if verification.get('location'): location = verification['location']
    if verification.get('title'): title = verification['title']
    relevance = evaluate_job(title, company, location, metadata=metadata)
    closing = deadline_date(metadata.get('closing_date'))
    if metadata.get('closing_date') and not closing:
        metadata.pop('closing_date', None)
    if closing and closing < date.today():
        record_review(company, title, link, source, 'Application deadline passed', 'closed')
        return False
    if not relevance.eligible:
        record_review(company, title, link, source, '; '.join(relevance.rejection_reasons), 'filtered')
        return False
    clear_review(link)

    source_url = source_url or link
    payload = {
        "match_score": relevance.score,
        "match_tier": relevance.tier,
        "match_reasons": relevance.reasons[:5],
        "category": relevance.category,
        "program_type": relevance.program_type,
        "verification": {k: verification[k] for k in ('state', 'reason', 'checked_at')},
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    if closing: payload['deadline'] = closing.isoformat()

    with _JOB_LOCK:
        for item in discovered_list:
            item_url = normalize_url(item.get("link", ""))
            item_ats = extract_ats_post_id(item.get("link", ""))
            item_c, item_t = normalize_company(item.get("company")), normalize_role(item.get("title"))
            same = same_listing(item, {'company': company, 'title': title, 'link': link, 'location': location, 'metadata': metadata})
            if same:
                item.setdefault("sources", [item.get("source", "Discovered API")])
                if source not in item["sources"]:
                    item["sources"].append(source)
                if any(ats in link.lower() for ats in ("greenhouse", "lever", "ashby", "smartrecruiters")):
                    item["link"], item["source_url"] = link, source_url
                item.update(payload)
                if metadata:
                    item["metadata"] = {**item.get('metadata', {}), **metadata}
                item['location'] = location
                return False

        entry = {
            "id": job_id, "company": company, "title": title,
            "location": location if location else "Unknown", "link": link,
            "source": source, "sources": [source], "source_url": source_url,
            "date_found": time.strftime("%Y-%m-%d %H:%M"), **payload,
        }
        if metadata:
            entry["metadata"] = metadata
        discovered_list.insert(0, entry)
        return True


def extract_and_register_ats_company(url):
    with _REGISTRY_LOCK:
        _register_ats_company(url)


def _register_ats_company(url):
    if not url or not isinstance(url, str):
        return
    settings = load_settings()
    updated = False
    patterns = (
        (r"greenhouse\.io/([^/?#]+)", "greenhouse_companies", {"embed", "jobs", "embeds"}),
        (r"lever\.co/([^/?#]+)", "lever_companies", {"jobs"}),
        (r"ashbyhq\.com/([^/?#]+)", "ashby_companies", {"jobs"}),
    )
    for pattern, key, ignored in patterns:
        match = re.search(pattern, url, re.I)
        if not match:
            continue
        company = match.group(1).lower().strip()
        values = settings.get(key, [])
        if company and company not in ignored and company not in values:
            values.append(company)
            settings[key] = values
            updated = True
            print(f"  ├── ⚙️ [ATS Auto-Discovery] Added {key}: {company}")
    if updated:
        save_settings(settings)


def _record_job(discovered_list, seen_jobs, local_new, relevant_counter, *, job_id, company, title, location, job_url, source, board_url, metadata):
    decision = relevance_decision(title, location, company, metadata)
    if not decision.eligible:
        if decision.program_type in {'internship', 'placement'}:
            record_review(company, title, job_url, source, '; '.join(decision.rejection_reasons), 'filtered')
        return False
    added = add_discovered_job(discovered_list, job_id, company, title, location, job_url, source, board_url, metadata=metadata, relevance=decision)
    if added and job_id not in seen_jobs:
        seen_jobs.add(job_id)
        local_new.append((f"{company} - {title}", location, job_url))
    return True


def scrape_greenhouse_jobs(seen_jobs, discovered_list, scraper_status=None):
    companies = list(set(c.split("?")[0].split("#")[0].strip() for c in load_settings().get("greenhouse_companies", []) if c.strip()))
    print(f"  ├── 🟢 [Greenhouse API] Scanning {len(companies)} target companies concurrently...")
    online = relevant = 0
    failures = {}
    new_jobs = []
    def fetch(company):
        nonlocal online, relevant
        if company in {"embed", "jobs", "embeds"}:
            return []
        local = []
        try:
            # content=true provides the job description, departments and offices.
            resp = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true", timeout=8)
            if resp.status_code != 200:
                failures[company] = f"HTTP {resp.status_code}"
                return local
            with _JOB_LOCK: online += 1
            jobs = resp.json().get("jobs", [])
            local_relevant = 0
            for job in jobs:
                title = job.get("title", "")
                location = (job.get("location") or {}).get("name", "")
                metadata = {
                    "listing_status": "published",
                    "description": _plain_html(job.get("content", "")),
                    "department": ", ".join(d.get("name", "") for d in job.get("departments", []) if d.get("name")),
                    "team": ", ".join(o.get("name", "") for o in job.get("offices", []) if o.get("name")),
                }
                job_id = f"gh_{company}_{job.get('id')}"
                if _record_job(discovered_list, seen_jobs, local, relevant, job_id=job_id, company=company.capitalize(), title=title, location=location, job_url=job.get("absolute_url", ""), source=f"Greenhouse ({company})", board_url=f"https://boards.greenhouse.io/{company}", metadata=metadata):
                    local_relevant += 1
                    with _JOB_LOCK: relevant += 1
            if local_relevant:
                print(f"  │   ↳ {company.capitalize()}: {len(jobs)} fetched ({local_relevant} eligible)")
        except Exception as exc:
            failures[company] = type(exc).__name__
            print(f"  │   ⚠️ {company.capitalize()} Greenhouse error: {type(exc).__name__}")
        return local
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(fetch, companies): new_jobs.extend(rows)
    update_source_status("Greenhouse API", f"{'🟢' if online == len(companies) else '🟠'} {online}/{len(companies)} employer feeds online • {relevant} relevant candidates")
    from config import update_scraper_status
    update_scraper_status("greenhouse_coverage", {"online": online, "configured": len(companies), "failures": failures, "relevant": relevant})
    return new_jobs


def scrape_lever_jobs(seen_jobs, discovered_list, scraper_status=None):
    companies = list(set(c.split("?")[0].split("#")[0].strip() for c in load_settings().get("lever_companies", []) if c.strip()))
    print(f"  ├── 🟢 [Lever API] Scanning {len(companies)} target companies concurrently...")
    online = relevant = 0
    failures = {}
    new_jobs = []
    def fetch(company):
        nonlocal online, relevant
        local = []
        try:
            resp = requests.get(f"https://api.lever.co/v0/postings/{company}?mode=json", timeout=8)
            if resp.status_code != 200:
                failures[company] = f"HTTP {resp.status_code}"
                return local
            with _JOB_LOCK: online += 1
            jobs = resp.json()
            local_relevant = 0
            for job in jobs:
                categories = job.get("categories", {}) or {}
                metadata = {
                    "listing_status": "published",
                    "description": job.get("descriptionPlain", "") or _plain_html(job.get("description", "")),
                    "department": categories.get("department", ""), "team": categories.get("team", ""),
                    "employment_type": categories.get("commitment", ""), "commitment": categories.get("commitment", ""),
                    "workplace_type": job.get("workplaceType", ""), "country": job.get("country", ""), "apply_url": job.get("applyUrl", ""),
                }
                job_id = f"lever_{company}_{job.get('id')}"
                if _record_job(discovered_list, seen_jobs, local, relevant, job_id=job_id, company=company.capitalize(), title=job.get("text", ""), location=categories.get("location", ""), job_url=job.get("hostedUrl", ""), source=f"Lever ({company})", board_url=f"https://jobs.lever.co/{company}", metadata=metadata):
                    local_relevant += 1
                    with _JOB_LOCK: relevant += 1
            if local_relevant: print(f"  │   ↳ {company.capitalize()}: {len(jobs)} fetched ({local_relevant} eligible)")
        except Exception as exc: print(f"  │   ⚠️ {company.capitalize()} Lever error: {type(exc).__name__}")
        return local
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(fetch, companies): new_jobs.extend(rows)
    update_source_status("Lever API", f"{'🟢' if online == len(companies) else '🟠'} {online}/{len(companies)} employer feeds online • {relevant} relevant candidates")
    from config import update_scraper_status
    update_scraper_status("lever_coverage", {"online": online, "configured": len(companies), "failures": failures, "relevant": relevant})
    return new_jobs


def scrape_ashby_jobs(seen_jobs, discovered_list, scraper_status=None):
    companies = list(set(c.strip() for c in load_settings().get("ashby_companies", []) if c.strip()))
    print(f"  ├── 🟢 [Ashby API] Scanning {len(companies)} target companies concurrently...")
    online = relevant = 0
    failures = {}
    new_jobs = []
    def fetch(company):
        nonlocal online, relevant
        local = []
        try:
            resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{company}", timeout=8)
            if resp.status_code != 200:
                failures[company] = f"HTTP {resp.status_code}"
                return local
            with _JOB_LOCK: online += 1
            jobs = resp.json().get("jobs", [])
            local_relevant = 0
            for job in jobs:
                postal = ((job.get("address") or {}).get("postalAddress") or {})
                metadata = {
                    "listing_status": "published",
                    "description": job.get("descriptionPlain", "") or _plain_html(job.get("descriptionHtml", "")),
                    "department": job.get("department", ""), "team": job.get("team", ""),
                    "employment_type": job.get("employmentType", ""), "workplace_type": job.get("workplaceType", ""),
                    "country": postal.get("addressCountry", ""), "apply_url": job.get("applyUrl", ""), "published_at": job.get("publishedAt", ""),
                }
                job_url = job.get("jobUrl", f"https://jobs.ashbyhq.com/{company}/{job.get('id')}")
                job_id = f"ashby_{company}_{job.get('id') or abs(hash(job_url))}"
                if _record_job(discovered_list, seen_jobs, local, relevant, job_id=job_id, company=company.capitalize(), title=job.get("title", ""), location=job.get("location", "") or job.get("locationName", ""), job_url=job_url, source=f"Ashby ({company})", board_url=f"https://jobs.ashbyhq.com/{company}", metadata=metadata):
                    local_relevant += 1
                    with _JOB_LOCK: relevant += 1
            if local_relevant: print(f"  │   ↳ {company.capitalize()}: {len(jobs)} fetched ({local_relevant} eligible)")
        except Exception as exc: print(f"  │   ⚠️ {company.capitalize()} Ashby error: {type(exc).__name__}")
        return local
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(fetch, companies): new_jobs.extend(rows)
    update_source_status("Ashby API", f"{'🟢' if online == len(companies) else '🟠'} {online}/{len(companies)} employer feeds online • {relevant} relevant candidates")
    from config import update_scraper_status
    update_scraper_status("ashby_coverage", {"online": online, "configured": len(companies), "failures": failures, "relevant": relevant})
    return new_jobs


def scrape_smartrecruiters_jobs(seen_jobs, discovered_list, scraper_status=None):
    companies = list(set(c.strip() for c in load_settings().get("smartrecruiters_companies", []) if c.strip()))
    print(f"  ├── 🟢 [SmartRecruiters API] Scanning {len(companies)} target companies concurrently...")
    online = relevant = 0
    failures = {}
    new_jobs = []
    def fetch(company):
        nonlocal online, relevant
        local = []
        try:
            resp = requests.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings", params={"limit": 100, "offset": 0}, timeout=8)
            if resp.status_code != 200:
                failures[company] = f"HTTP {resp.status_code}"
                return local
            with _JOB_LOCK: online += 1
            data = resp.json()
            jobs = data.get("content", [])
            total = int(data.get("totalFound", len(jobs)))
            offset = 100
            while offset < min(total, 5000):
                page = requests.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings", params={"limit": 100, "offset": offset}, timeout=8)
                page.raise_for_status()
                rows = page.json().get("content", [])
                if not rows: break
                jobs.extend(rows); offset += 100
            local_relevant = 0
            for job in jobs:
                loc = job.get("location", {}) or {}
                location = f"{loc.get('city','')}, {loc.get('country','')}".strip(", ")
                metadata = {"country": loc.get("country", ""), "listing_status": "published"}
                department = job.get("department")
                if isinstance(department, dict): metadata["department"] = department.get("label", "")
                elif department: metadata["department"] = department
                if not is_relevant_role(job.get('name', ''), location, company, metadata): continue
                detail = requests.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{job.get('id')}", timeout=8)
                if detail.status_code != 200: continue
                info = detail.json()
                sections = (info.get('jobAd') or {}).get('sections') or {}
                metadata['description'] = ' '.join(_plain_html((sections.get(k) or {}).get('text')) for k in ('jobDescription', 'qualifications', 'additionalInformation'))
                metadata['employment_type'] = (info.get('typeOfEmployment') or {}).get('label', '')
                job_id = f"sr_{company}_{job.get('id')}"
                if _record_job(discovered_list, seen_jobs, local, relevant, job_id=job_id, company=company.capitalize(), title=job.get("name", ""), location=location, job_url=f"https://jobs.smartrecruiters.com/{company}/{job.get('id')}", source=f"SmartRecruiters ({company})", board_url=f"https://jobs.smartrecruiters.com/{company}", metadata=metadata):
                    local_relevant += 1
                    with _JOB_LOCK: relevant += 1
            if local_relevant: print(f"  │   ↳ {company.capitalize()}: {len(jobs)} fetched ({local_relevant} eligible)")
        except Exception as exc: print(f"  │   ⚠️ {company.capitalize()} SmartRecruiters error: {type(exc).__name__}")
        return local
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(fetch, companies): new_jobs.extend(rows)
    update_source_status("SmartRecruiters API", f"{'🟢' if online == len(companies) else '🟠'} {online}/{len(companies)} employer feeds online • {relevant} relevant candidates")
    from config import update_scraper_status
    update_scraper_status("smartrecruiters_coverage", {"online": online, "configured": len(companies), "failures": failures, "relevant": relevant})
    return new_jobs
