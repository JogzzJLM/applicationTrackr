import hashlib
import re
import time

import requests
from bs4 import BeautifulSoup

from config import update_source_status
from scrapers_engine.ats_scrapers import (
    _JOB_LOCK,
    add_discovered_job,
    extract_and_register_ats_company,
    is_relevant_role,
)


GRADCRACKER_SECTOR_URLS = [
    ("Computing & Technology", "https://www.gradcracker.com/search/computing-technology/work-placements-internships"),
    ("Computing & Technology", "https://www.gradcracker.com/search/computing-technology/graduate-jobs"),
    ("Maths & Actuarial", "https://www.gradcracker.com/search/maths-actuarial/work-placements-internships"),
    ("Maths & Actuarial", "https://www.gradcracker.com/search/maths-actuarial/graduate-jobs"),
]

# Gradcracker changes slowly. Do not request all four pages every five minutes.
GRADCRACKER_REFRESH_SECONDS = 3 * 60 * 60
GRADCRACKER_BLOCKED_RETRY_SECONDS = 6 * 60 * 60

_GRADCRACKER_LAST_ATTEMPT = 0.0
_GRADCRACKER_LAST_SUCCESS = 0.0
_GRADCRACKER_LAST_BLOCKED = 0.0


def _fmt_wait(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:.1f}h"
    return f"{max(1, seconds // 60)}m"


def _extract_gradcracker_jobs(html):
    """
    Parse the layouts ApplicationTrackr has historically seen.

    If Gradcracker changes its HTML again, returning an empty list is handled
    as a parser warning rather than being reported as a successful zero-job scan.
    """
    soup = BeautifulSoup(html, "html.parser")

    job_containers = soup.find_all(
        "div",
        class_=re.compile(r"tw-bg-white|job-card|tw-border", re.I),
    )

    if not job_containers:
        job_containers = soup.find_all(
            "div",
            id=re.compile(r"job-container|job_\d+", re.I),
        )

    # Fallback: walk up from links that look like Gradcracker opportunity links.
    if not job_containers:
        seen_nodes = set()
        for a in soup.find_all(
            "a",
            href=re.compile(r"/hub/|/graduate-job/|/work-placement/|/internship/", re.I),
        ):
            node = a
            for _ in range(4):
                if node.parent is None:
                    break
                node = node.parent
                text = node.get_text(" ", strip=True)
                if len(text) >= 40:
                    key = id(node)
                    if key not in seen_nodes:
                        seen_nodes.add(key)
                        job_containers.append(node)
                    break

    return job_containers


def scrape_gradcracker_website(
    seen_jobs,
    discovered_list,
    force_rescan=False,
    log_func=None,
    scraper_status=None,
):
    global _GRADCRACKER_LAST_ATTEMPT
    global _GRADCRACKER_LAST_SUCCESS
    global _GRADCRACKER_LAST_BLOCKED

    source_name = "Gradcracker API"
    now = time.time()

    # A dashboard/manual rescan should not hammer a source that is actively
    # challenging this server. Respect the source-specific backoff either way.
    if _GRADCRACKER_LAST_BLOCKED:
        blocked_age = now - _GRADCRACKER_LAST_BLOCKED
        if blocked_age < GRADCRACKER_BLOCKED_RETRY_SECONDS:
            remaining = GRADCRACKER_BLOCKED_RETRY_SECONDS - blocked_age
            status = f"🟠 Cloudflare blocked • retry in {_fmt_wait(remaining)}"
            update_source_status(source_name, status)
            if log_func:
                log_func(
                    f"  ├── 🟠 [Gradcracker] Cloudflare previously returned 403; "
                    f"skipping this cycle (retry in {_fmt_wait(remaining)})."
                )
            return []

    if _GRADCRACKER_LAST_SUCCESS and not force_rescan:
        success_age = now - _GRADCRACKER_LAST_SUCCESS
        if success_age < GRADCRACKER_REFRESH_SECONDS:
            remaining = GRADCRACKER_REFRESH_SECONDS - success_age
            update_source_status(source_name, "🟢 Active • refresh cached")
            if log_func:
                log_func(
                    f"  ├── 🟢 [Gradcracker] Recent successful scan; "
                    f"next refresh in {_fmt_wait(remaining)}."
                )
            return []

    if log_func:
        log_func(
            "  ├── 🟢 [Gradcracker] Scanning UK STEM Computing & Maths sectors..."
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    _GRADCRACKER_LAST_ATTEMPT = time.time()
    new_jobs = []
    total_items_fetched = 0
    relevant_found = 0
    successful_pages = 0
    parser_empty_pages = 0

    # Probe the first page before requesting all four. If Cloudflare is blocking
    # this public IP, one request is enough to know that.
    first_sector, first_url = GRADCRACKER_SECTOR_URLS[0]
    try:
        first_resp = requests.get(first_url, headers=headers, timeout=12)
    except requests.RequestException as exc:
        update_source_status(source_name, "🟠 Network error")
        if log_func:
            log_func(
                f"  │   ⚠️ Gradcracker request failed: {type(exc).__name__}: {exc}"
            )
        return []

    if first_resp.status_code == 403:
        _GRADCRACKER_LAST_BLOCKED = time.time()
        update_source_status(
            source_name,
            "🟠 Cloudflare blocked • 6h backoff",
        )
        if log_func:
            log_func(
                "  │   ⚠️ Gradcracker returned HTTP 403 / Cloudflare challenge. "
                "Backing off for 6 hours; existing discovered jobs are retained."
            )
        return []

    responses = [(first_sector, first_url, first_resp)]

    # Only fetch the remaining pages if the probe was not blocked.
    for sector_name, url in GRADCRACKER_SECTOR_URLS[1:]:
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            responses.append((sector_name, url, resp))
        except requests.RequestException as exc:
            if log_func:
                log_func(
                    f"  │   ⚠️ Gradcracker request failed ({sector_name}): "
                    f"{type(exc).__name__}: {exc}"
                )

    for sector_name, url, resp in responses:
        if resp.status_code == 403:
            _GRADCRACKER_LAST_BLOCKED = time.time()
            if log_func:
                log_func(
                    f"  │   ⚠️ Gradcracker Cloudflare challenge on {sector_name} "
                    f"(HTTP 403)."
                )
            continue

        if resp.status_code != 200:
            if log_func:
                log_func(
                    f"  │   ⚠️ Gradcracker HTTP {resp.status_code} ({sector_name})."
                )
            continue

        successful_pages += 1

        try:
            job_containers = _extract_gradcracker_jobs(resp.text)
        except Exception as exc:
            if log_func:
                log_func(
                    f"  │   ⚠️ Gradcracker parser error ({sector_name}): "
                    f"{type(exc).__name__}: {exc}"
                )
            continue

        if not job_containers:
            parser_empty_pages += 1
            if log_func:
                log_func(
                    f"  │   ⚠️ Gradcracker returned real HTML for {sector_name}, "
                    "but no recognised job containers were found."
                )
            continue

        sector_fetched = 0
        sector_relevant = 0

        for container in job_containers:
            try:
                comp_elem = (
                    container.find(
                        "a",
                        class_=re.compile(r"company|employer|tw-font-bold", re.I),
                    )
                    or container.find(["h3", "h4", "strong"])
                )

                title_elem = (
                    container.find(
                        "a",
                        class_=re.compile(r"job-title|title|tw-text-", re.I),
                    )
                    or container.find(["h2", "h3"])
                )

                link_elem = (
                    container.find(
                        "a",
                        href=re.compile(
                            r"/hub/|/graduate-job/|/work-placement/|/internship/",
                            re.I,
                        ),
                    )
                    or container.find("a", href=True)
                )

                if not title_elem or not link_elem:
                    continue

                company = (
                    comp_elem.get_text(" ", strip=True)
                    if comp_elem
                    else "Gradcracker Employer"
                )
                title = title_elem.get_text(" ", strip=True)
                link = link_elem.get("href", "").strip()

                if not link:
                    continue
                if not link.startswith("http"):
                    link = f"https://www.gradcracker.com{link}"

                loc_elem = container.find(
                    string=re.compile(
                        r"London|Remote|UK|Manchester|Birmingham|Oxford|Cambridge",
                        re.I,
                    )
                )
                location = loc_elem.strip() if loc_elem else "UK"

                sector_fetched += 1
                total_items_fetched += 1

                try:
                    extract_and_register_ats_company(link)
                except Exception as exc:
                    if log_func:
                        log_func(
                            f"  │   ⚠️ Gradcracker ATS discovery error for "
                            f"{company}: {type(exc).__name__}: {exc}"
                        )

                if not is_relevant_role(title, location, company):
                    continue

                stable_key = f"{company}|{title}|{link}".encode("utf-8")
                job_id = "gc_" + hashlib.sha256(stable_key).hexdigest()[:20]

                is_new = add_discovered_job(
                    discovered_list,
                    job_id,
                    company,
                    title,
                    location,
                    link,
                    "Gradcracker",
                    "https://www.gradcracker.com",
                )

                relevant_found += 1
                sector_relevant += 1

                with _JOB_LOCK:
                    if is_new and job_id not in seen_jobs:
                        seen_jobs.add(job_id)
                        new_jobs.append(
                            (f"{company} - {title}", location, link)
                        )

            except Exception as exc:
                if log_func:
                    log_func(
                        f"  │   ⚠️ Gradcracker listing parse error "
                        f"({sector_name}): {type(exc).__name__}: {exc}"
                    )

        if log_func:
            log_func(
                f"  │   ↳ Gradcracker {sector_name}: "
                f"{sector_fetched} fetched ({sector_relevant} relevant)"
            )

    if _GRADCRACKER_LAST_BLOCKED:
        update_source_status(
            source_name,
            "🟠 Partially/fully Cloudflare blocked • 6h backoff",
        )
    elif successful_pages and parser_empty_pages == successful_pages:
        update_source_status(
            source_name,
            "🟠 HTML received but parser found no listings",
        )
    elif successful_pages:
        _GRADCRACKER_LAST_SUCCESS = time.time()
        update_source_status(
            source_name,
            f"🟢 Active • {relevant_found} active schemes indexed",
        )
    else:
        update_source_status(source_name, "🟠 Gradcracker unavailable")

    if log_func:
        log_func(
            f"  │   ↳ Gradcracker Summary: {successful_pages}/4 pages reachable, "
            f"{total_items_fetched} listings parsed, "
            f"{relevant_found} relevant."
        )

    return new_jobs
