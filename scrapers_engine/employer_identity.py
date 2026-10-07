"""Resolve employer names from a published ATS board, never from its slug."""
import time
import requests
from core.storage import DATA_DIR, load_json_safe, atomic_write_json, _FILE_LOCK

IDENTITIES_FILE = str(DATA_DIR / 'employer_identities.json')


def greenhouse_employer(board):
    with _FILE_LOCK:
        saved = load_json_safe(IDENTITIES_FILE, {})
        cached = saved.get(board, {})
    if cached.get('name') and time.time() - cached.get('checked_at', 0) < 86400:
        return cached['name']
    try:
        response = requests.get(f'https://boards-api.greenhouse.io/v1/boards/{board}', timeout=6)
        name = response.json().get('name', '') if response.status_code == 200 else ''
        if isinstance(name, str) and name.strip():
            with _FILE_LOCK:
                saved = load_json_safe(IDENTITIES_FILE, {})
                saved[board] = {'name': name.strip(), 'checked_at': time.time()}
                atomic_write_json(IDENTITIES_FILE, saved)
            return name.strip()
    except (requests.RequestException, ValueError, AttributeError):
        pass
    return cached.get('name', '')
