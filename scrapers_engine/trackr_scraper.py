import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime

import requests

from config import update_source_status
from scrapers_engine.ats_scrapers import (
    _JOB_LOCK,
    add_discovered_job,
    extract_and_register_ats_company,
    is_relevant_role,
)


# ---------------------------------------------------------------------------
# Trackr configuration
# ---------------------------------------------------------------------------

TRACKR_API_URL = "https://api.the-trackr.com/programmes"
TRACKR_SOURCE_URL = "https://app.the-trackr.com/uk-tech"

# Current + immediately previous recruitment season.
TRACKR_SEASONS = [str(datetime.now().year + 1), str(datetime.now().year)]

TRACKR_TYPES = [
    "summer-internships",
    "off-cycle-internships",
    "placements",
]

# Trackr changes much more slowly than our direct ATS sources.
# Refresh it every 3 hours rather than every ApplicationTrackr cycle.
TRACKR_REFRESH_SECONDS = 3 * 60 * 60
TRACKR_CACHE_FILE = os.path.join(
    os.getenv("DATA_DIR", "/data"),
    "trackr_cache.json",
)

TRACKR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://app.the-trackr.com",
    "Referer": "https://app.the-trackr.com/",
}


# ---------------------------------------------------------------------------
# In-memory Trackr cache
# ---------------------------------------------------------------------------

_TRACKR_CACHE_LOCK = threading.Lock()
_TRACKR_LAST_REFRESH = 0.0
_TRACKR_LAST_ATTEMPT = 0.0
_TRACKR_CACHED_ITEMS = []


def _load_persistent_trackr_cache():
    """Load the last successful Trackr dataset from persistent /data storage."""
    try:
        with open(TRACKR_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if isinstance(payload, dict):
            items = payload.get("items", [])
            saved_at = float(payload.get("saved_at", 0) or 0)
        elif isinstance(payload, list):
            # Backwards-compatible fallback if the cache was ever stored as a list.
            items = payload
            saved_at = 0
        else:
            return [], 0.0

        if not isinstance(items, list):
            return [], 0.0

        return items, saved_at
    except FileNotFoundError:
        return [], 0.0
    except Exception:
        return [], 0.0


def _save_persistent_trackr_cache(items):
    """Atomically persist only a successful, non-empty Trackr dataset."""
    directory = os.path.dirname(TRACKR_CACHE_FILE)
    os.makedirs(directory, exist_ok=True)

    payload = {
        "saved_at": time.time(),
        "items": items,
    }

    tmp = TRACKR_CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    os.replace(tmp, TRACKR_CACHE_FILE)


_TRACKR_CACHED_ITEMS, _TRACKR_LAST_REFRESH = _load_persistent_trackr_cache()


def parse_trackr_date(d_str):
    """Parse the date formats Trackr has used historically/currently."""

    if not d_str or not isinstance(d_str, str):
        return None

    d_str = d_str.strip()

    # Current Trackr ISO timestamps:
    # 2026-09-14T00:00:00.000Z
    m = re.search(r"(\d{4}-\d{2}-\d{2})", d_str)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except Exception:
            pass

    # Older Trackr date representation.
    m = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})", d_str)
    if m:
        value = m.group(1)

        for fmt in ("%d %b %Y", "%d %b %y"):
            try:
                return datetime.strptime(value, fmt)
            except Exception:
                pass

    return None


def is_trackr_item_active_and_recent(item):
    """Reject explicitly closed or clearly stale Trackr programmes."""

    status_raw = str(
        item.get("status")
        or item.get("openStatus")
        or item.get("state")
        or item.get("programmeStatus")
        or ""
    ).lower().strip()

    if any(
        keyword in status_raw
        for keyword in (
            "close",
            "unopen",
            "upcom",
            "expir",
            "fill",
            "pause",
            "archiv",
        )
    ):
        return False

    if (
        item.get("isOpen") is False
        or item.get("isClosed") is True
        or item.get("is_open") is False
    ):
        return False

    if status_raw and status_raw not in (
        "open",
        "active",
        "opened",
        "accepting applications",
        "open for applications",
    ):
        return False

    now = datetime.now()

    close_date_str = (
        item.get("closingDate")
        or item.get("closeDate")
        or item.get("close_date")
        or item.get("closedAt")
        or ""
    )

    if close_date_str:
        close_date = parse_trackr_date(close_date_str)

        if close_date and close_date.date() < now.date():
            return False

    open_date_str = (
        item.get("openingDate")
        or item.get("openDate")
        or item.get("open_date")
        or item.get("dateOpened")
        or item.get("postedDate")
        or item.get("createdAt")
        or item.get("updatedAt")
        or ""
    )

    if open_date_str:
        open_date = parse_trackr_date(open_date_str)

        if open_date and open_date.date() > now.date():
            return False

    # A summer that has already finished isn't a current recruitment season.
    if item.get("type") == "summer-internships" and str(item.get("season", "")).isdigit():
        if int(item["season"]) < now.year or (int(item["season"]) == now.year and now.month >= 10):
            return False

    return True


def _extract_trackr_fields(item):
    """
    Normalise both the current Trackr API schema and the older schema
    ApplicationTrackr previously consumed.
    """

    # Current API:
    #
    # "company": {
    #     "id": "american-express",
    #     "name": "American Express"
    # }

    company_data = item.get("company")

    if isinstance(company_data, dict):
        company = str(
            company_data.get("name")
            or company_data.get("id")
            or ""
        ).strip()
    else:
        company = str(
            item.get("companyName")
            or company_data
            or item.get("employer")
            or ""
        ).strip()

    # Current API uses "name".
    title = str(
        item.get("name")
        or item.get("role")
        or item.get("title")
        or item.get("programmeName")
        or item.get("jobTitle")
        or ""
    ).strip()

    # Current API uses "url".
    link = str(
        item.get("url")
        or item.get("applicationLink")
        or item.get("link")
        or item.get("applyUrl")
        or ""
    ).strip()

    # Current API uses a locations array.
    locations = item.get("locations")

    if isinstance(locations, list) and locations:
        location = ", ".join(
            str(value).strip()
            for value in locations
            if str(value).strip()
        )
    else:
        location = str(
            item.get("location")
            or item.get("city")
            or item.get("region")
            or "UK"
        ).strip()

    if not location:
        location = "UK"

    return company, title, link, location


def trackr_metadata(item):
    return {
        "source_region": item.get("region", ""),
        "programme_type": item.get("type", ""),
        "division": ", ".join(item.get("divisions") or []),
        "eligibility": item.get("eligibility") or "",
        "location_notes": item.get("notes") or "",
        "closing_date": item.get("closingDate") or "",
        "opening_date": item.get("openingDate") or "",
        "season": item.get("season") or "",
    }


def _fetch_trackr_query(season, programme_type, log_func=None):
    """Fetch one required Trackr season/type combination."""

    params = {
        "region": "UK",
        "industry": "Tech",
        "season": season,
        "type": programme_type,
    }

    try:
        response = requests.get(
            TRACKR_API_URL,
            params=params,
            headers=TRACKR_HEADERS,
            timeout=20,
        )

        if response.status_code == 429:
            if log_func:
                log_func(
                    f"  │   ⚠️ Trackr rate limited "
                    f"({season}/{programme_type})"
                )

            return None

        if response.status_code != 200:
            if log_func:
                log_func(
                    f"  │   ⚠️ Trackr HTTP {response.status_code} "
                    f"({season}/{programme_type})"
                )

            return None

        try:
            data = response.json()
        except Exception as exc:
            if log_func:
                log_func(
                    f"  │   ⚠️ Trackr returned invalid JSON "
                    f"({season}/{programme_type}): "
                    f"{type(exc).__name__}: {exc}"
                )

            return None

        # Current Trackr response:
        #
        # {
        #     "programmes": [...],
        #     "groups": [...]
        # }
        #
        # Retain backwards compatibility with the historical list response.

        if isinstance(data, dict):
            items = data.get("programmes")

            if items is None:
                items = data.get("items")

        elif isinstance(data, list):
            items = data

        else:
            items = None

        if not isinstance(items, list):
            if log_func:
                log_func(
                    f"  │   ⚠️ Trackr returned unexpected response "
                    f"({season}/{programme_type})"
                )

            return None

        # Important:
        # An empty result from every query is suspicious because Trackr
        # currently returns [] to our home connection when access is restricted.
        #
        # Don't immediately destroy our previous cache because of that.

        return items

    except requests.RequestException as exc:
        if log_func:
            log_func(
                f"  │   ⚠️ Trackr request failed "
                f"({season}/{programme_type}): "
                f"{type(exc).__name__}: {exc}"
            )

        return None

    except Exception as exc:
        if log_func:
            log_func(
                f"  │   ⚠️ Unexpected Trackr error "
                f"({season}/{programme_type}): "
                f"{type(exc).__name__}: {exc}"
            )

        return None


def _refresh_trackr_cache(log_func=None):
    """Contact Trackr and build a fresh raw-programme cache."""

    tasks = [
        (season, programme_type)
        for season in TRACKR_SEASONS
        for programme_type in TRACKR_TYPES
    ]

    results = []

    # Keep concurrency modest.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_map = {
            executor.submit(
                _fetch_trackr_query,
                season,
                programme_type,
                log_func,
            ): (season, programme_type)
            for season, programme_type in tasks
        }

        for future in concurrent.futures.as_completed(future_map):
            season, programme_type = future_map[future]

            try:
                items = future.result()
            except Exception as exc:
                if log_func:
                    log_func(
                        f"  │   ⚠️ Trackr worker failed "
                        f"({season}/{programme_type}): "
                        f"{type(exc).__name__}: {exc}"
                    )

                continue

            if items:
                results.extend(items)

    # Deduplicate by Trackr's native programme ID where possible,
    # otherwise by URL.
    deduplicated = {}
    anonymous = []

    for item in results:
        if not isinstance(item, dict):
            continue

        key = item.get("id") or item.get("url")

        if key:
            deduplicated[str(key)] = item
        else:
            anonymous.append(item)

    fresh_items = list(deduplicated.values()) + anonymous

    return fresh_items


def _get_trackr_items(force_rescan=False, log_func=None):
    """
    Return cached Trackr data while respecting a source-specific cooldown.

    A failed/empty attempt also starts the three-hour cooldown, preventing the
    normal five-minute ApplicationTrackr loop from repeatedly hitting Trackr.
    The last successful non-empty dataset is persisted to /data.
    """

    global _TRACKR_LAST_REFRESH
    global _TRACKR_LAST_ATTEMPT
    global _TRACKR_CACHED_ITEMS

    now = time.time()

    with _TRACKR_CACHE_LOCK:
        attempt_age = (
            now - _TRACKR_LAST_ATTEMPT
            if _TRACKR_LAST_ATTEMPT
            else None
        )

        # Respect the Trackr cooldown even for a dashboard/manual rescan. The
        # purpose is to avoid repeatedly hitting an endpoint that is currently
        # treating this public IP differently.
        if attempt_age is not None and attempt_age < TRACKR_REFRESH_SECONDS:
            remaining = TRACKR_REFRESH_SECONDS - attempt_age

            if log_func:
                minutes = max(1, int(remaining // 60))
                if _TRACKR_CACHED_ITEMS:
                    log_func(
                        f"  │   ↳ Trackr: cooldown active; using "
                        f"{len(_TRACKR_CACHED_ITEMS)} cached programmes "
                        f"(next API attempt in ~{minutes}m)."
                    )
                else:
                    log_func(
                        f"  │   ↳ Trackr: cooldown active after empty/failed "
                        f"response (next API attempt in ~{minutes}m)."
                    )

            return list(_TRACKR_CACHED_ITEMS), False

        _TRACKR_LAST_ATTEMPT = now

    # Do not hold the cache lock while performing network requests.
    fresh_items = _refresh_trackr_cache(log_func=log_func)

    if fresh_items:
        saved_at = time.time()

        try:
            _save_persistent_trackr_cache(fresh_items)
        except Exception as exc:
            if log_func:
                log_func(
                    f"  │   ⚠️ Trackr cache persistence failed: "
                    f"{type(exc).__name__}: {exc}"
                )

        with _TRACKR_CACHE_LOCK:
            _TRACKR_CACHED_ITEMS = fresh_items
            _TRACKR_LAST_REFRESH = saved_at

        if log_func:
            log_func(
                f"  │   ↳ Trackr: refreshed dataset "
                f"({len(fresh_items)} programmes)"
            )

        return list(fresh_items), True

    # Empty/failed response: preserve the last known-good dataset.
    with _TRACKR_CACHE_LOCK:
        if _TRACKR_CACHED_ITEMS:
            cached = list(_TRACKR_CACHED_ITEMS)

            if log_func:
                log_func(
                    "  │   ⚠️ Trackr refresh returned no programmes; "
                    f"retaining {len(cached)} persistent cached programmes."
                )

            return cached, False

    if log_func:
        log_func(
            "  │   ⚠️ Trackr returned no programmes and no previous "
            "persistent cache is available. Next API attempt is in 3h."
        )

    return [], False


def scrape_trackr_website(seen_jobs, discovered_list, force_rescan=False, log_func=None, scraper_status=None):
    from collections import Counter
    from core.relevance import evaluate_job
    from core.normalization import normalize_url
    from scrapers_engine.quality import record_review
    from config import update_scraper_status
    items, refreshed = _get_trackr_items(force_rescan=force_rescan, log_func=log_func)
    source_name = 'The Trackr API'
    new_jobs, rejected = [], Counter()
    candidates = []
    for item in items:
        if not isinstance(item, dict): continue
        company, title, link, location = _extract_trackr_fields(item)
        if not company or not title or not link: rejected['missing application link/details'] += 1; continue
        if not is_trackr_item_active_and_recent(item): rejected['closed, future opening or past summer'] += 1; continue
        metadata = trackr_metadata(item)
        decision = evaluate_job(title, company, location, metadata)
        if not decision.eligible:
            rejected.update(decision.rejection_reasons)
            record_review(company, title, link, source_name, '; '.join(decision.rejection_reasons), 'filtered')
            continue
        candidates.append((item, company, title, link, location, metadata))
    def ingest(row):
        item, company, title, link, location, metadata = row
        extract_and_register_ats_company(link)
        job_id = 'trackr_' + str(item.get('id') or hashlib.sha256(link.encode()).hexdigest()[:20])
        added = add_discovered_job(discovered_list, job_id, company, title, location, link, source_name, TRACKR_SOURCE_URL, metadata=metadata)
        if added:
            with _JOB_LOCK:
                if job_id not in seen_jobs:
                    seen_jobs.add(job_id); new_jobs.append((company + ' - ' + title, location, link))
        return added
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(ingest, candidates))
    with _JOB_LOCK:
        verified = sum('The Trackr API' in j.get('sources', []) and j.get('verification', {}).get('state') == 'verified' for j in discovered_list)
    report = {'fetched': len(items), 'relevant_candidates': len(candidates), 'verified': verified,
              'rejected': dict(rejected), 'cached': not refreshed,
              'cache_age_hours': round((time.time() - _TRACKR_LAST_REFRESH) / 3600, 1) if _TRACKR_LAST_REFRESH else None}
    update_scraper_status('trackr_coverage', report)
    update_source_status(source_name, f"{'🟢' if refreshed else '🟡'} {len(items)} fetched • {len(candidates)} relevant candidates • {verified} verified")
    if log_func: log_func(f"Trackr: {len(items)} programmes, {len(candidates)} relevant candidates, {verified} verified listings")
    return new_jobs
