"""
Config facade for ApplicationTrackr.
Re-exports storage, normalization, knowledge base, and settings functions.
"""
from core.storage import (
    SEEN_JOBS_FILE, SEEN_EMAILS_FILE, DISCOVERED_JOBS_FILE, SETTINGS_FILE,
    CLOSED_KB_FILE, REPORTED_CLOSED_FILE, HIDDEN_JOBS_FILE, PORT,
    DEFAULT_SETTINGS, load_json_safe, atomic_write_json,
    load_reported_closed_jobs, save_reported_closed_jobs,
    load_hidden_jobs, save_hidden_jobs, hide_job,
    load_settings, save_settings,
    load_scraper_status, save_scraper_status
)
from core.kb import (
    DEFAULT_CLOSED_PHRASES, extract_generic_closure_phrases,
    load_closed_keywords_kb, save_closed_keywords_kb
)
from core.normalization import (
    COMPANY_ALIASES, normalize_company, normalize_role, normalize_url, extract_ats_post_id
)
import os
import sys
import time
import threading

APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/London")
SCRAPER_INTERVAL_SECONDS = int(os.getenv("SCRAPER_INTERVAL_SECONDS", "300"))
EMAIL_POLL_SECONDS = max(30, int(os.getenv("EMAIL_POLL_SECONDS", "60")))
NTFY_BASE_URL = os.getenv("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

# Gmail inbox automation.
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS", "")

# Optional generic third-party IMAP mailbox.
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_APP_PASS = os.getenv("EMAIL_APP_PASS", "")
EMAIL_IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "")
EMAIL_IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))

GOOGLE_SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "")
GOOGLE_SHEET_CSV_URL = os.getenv("GOOGLE_SHEET_CSV_URL", "")
HEALTHCHECKS_PING_URL = os.getenv("HEALTHCHECKS_PING_URL", "")

SCRAPER_STATUS = load_scraper_status()

_LOG_LOCK = threading.Lock()
SCRAPER_LOGS = []
_in_tee = threading.local()

class TerminalStreamTee:
    """Tee stream capturing sys.stdout & sys.stderr for rolling web terminal console."""
    def __init__(self, original_stream):
        self.original_stream = original_stream
        self.line_buffer = ""

    def write(self, buf):
        if getattr(_in_tee, 'active', False):
            self.original_stream.write(buf)
            return
        try:
            _in_tee.active = True
            self.original_stream.write(buf)
            self.original_stream.flush()
            self.line_buffer += str(buf)
            while "\n" in self.line_buffer:
                line, self.line_buffer = self.line_buffer.split("\n", 1)
                clean_line = line.strip()
                if clean_line:
                    with _LOG_LOCK:
                        timestamp = time.strftime("%H:%M:%S")
                        if not clean_line.startswith(("[", "┌", "├", "└", "│", "  ")):
                            formatted = f"[{timestamp}] {clean_line}"
                        else:
                            formatted = clean_line
                        SCRAPER_LOGS.append(formatted)
                        if len(SCRAPER_LOGS) > 600:
                            SCRAPER_LOGS.pop(0)
        except Exception:
            pass
        finally:
            _in_tee.active = False

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

if not getattr(sys, '_terminal_tee_installed', False):
    sys.stdout = TerminalStreamTee(sys.stdout)
    sys.stderr = TerminalStreamTee(sys.stderr)
    sys._terminal_tee_installed = True

def add_scraper_log(msg):
    print(msg)

def get_scraper_logs():
    with _LOG_LOCK:
        return list(SCRAPER_LOGS)

def clear_scraper_logs():
    global SCRAPER_LOGS
    with _LOG_LOCK:
        SCRAPER_LOGS = []

def update_scraper_status(key, value):
    with _LOG_LOCK:
        SCRAPER_STATUS[key] = value
        save_scraper_status(SCRAPER_STATUS)

def update_source_status(source_name, status_str):
    with _LOG_LOCK:
        if "source_status" not in SCRAPER_STATUS:
            SCRAPER_STATUS["source_status"] = {}
        SCRAPER_STATUS["source_status"][source_name] = status_str
        save_scraper_status(SCRAPER_STATUS)

_settings = load_settings()
GREENHOUSE_COMPANIES = _settings.get("greenhouse_companies", [])
LEVER_COMPANIES = _settings.get("lever_companies", [])
ASHBY_COMPANIES = _settings.get("ashby_companies", [])
SMARTRECRUITERS_COMPANIES = _settings.get("smartrecruiters_companies", [])
