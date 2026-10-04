"""Persist reasons for missing candidates so coverage failures are visible."""
import threading
from datetime import datetime, timezone
from core.storage import DATA_DIR, atomic_write_json, load_json_safe
from core.normalization import normalize_url

REVIEW_FILE = str(DATA_DIR / 'discovery_review.json')
_LOCK = threading.RLock()


def record_review(company, title, link, source, reason, state='needs_check'):
    with _LOCK:
        values = load_json_safe(REVIEW_FILE, {})
        values[normalize_url(link) or f'{company}|{title}'] = {
            'company': company, 'title': title, 'link': link, 'source': source,
            'reason': reason, 'state': state, 'checked_at': datetime.now(timezone.utc).isoformat()}
        atomic_write_json(REVIEW_FILE, dict(list(values.items())[-1500:]))


def clear_review(link):
    with _LOCK:
        values = load_json_safe(REVIEW_FILE, {})
        if values.pop(normalize_url(link), None): atomic_write_json(REVIEW_FILE, values)
