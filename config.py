"""
Config facade for ApplicationTrackr.
Re-exports storage, normalization, knowledge base, and settings functions.
"""
from core.storage import (
    SEEN_JOBS_FILE, SEEN_EMAILS_FILE, DISCOVERED_JOBS_FILE, SETTINGS_FILE,
    CLOSED_KB_FILE, REPORTED_CLOSED_FILE, HIDDEN_JOBS_FILE, PORT, HP_STREAM_TAILSCALE_IP,
    DEFAULT_SETTINGS, load_json_safe, atomic_write_json,
    load_reported_closed_jobs, save_reported_closed_jobs,
    load_hidden_jobs, save_hidden_jobs, hide_job,
    load_settings, save_settings
)
from core.kb import (
    DEFAULT_CLOSED_PHRASES, extract_generic_closure_phrases,
    load_closed_keywords_kb, save_closed_keywords_kb
)
from core.normalization import (
    COMPANY_ALIASES, normalize_company, normalize_role, normalize_url, extract_ats_post_id
)
import os
import time

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "jog_applicationtrackr_alerts")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS", "")
GOOGLE_SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "")
HEALTHCHECKS_PING_URL = os.getenv("HEALTHCHECKS_PING_URL", "")
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vS94NpozDGeHO9UPag662CXcH-C5TGN9Y61-nW04VDlPJSZGVTq62E1lRvnXl8gq_CbR5kvMx5XnMFi/pub?output=csv"

SCRAPER_STATUS = {
    "last_run": "Never",
    "total_seen_jobs": 0,
    "last_new_jobs_found": 0,
    "source_status": {}
}

SCRAPER_LOGS = []

def add_scraper_log(msg):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    SCRAPER_LOGS.append(formatted)
    if len(SCRAPER_LOGS) > 300:
        SCRAPER_LOGS.pop(0)

def get_scraper_logs():
    return list(SCRAPER_LOGS)

def clear_scraper_logs():
    global SCRAPER_LOGS
    SCRAPER_LOGS = []

# Dynamic ATS lists re-exported from settings for backward compatibility
_settings = load_settings()
GREENHOUSE_COMPANIES = _settings.get("greenhouse_companies", [])
LEVER_COMPANIES = _settings.get("lever_companies", [])
ASHBY_COMPANIES = _settings.get("ashby_companies", [])
SMARTRECRUITERS_COMPANIES = _settings.get("smartrecruiters_companies", [])