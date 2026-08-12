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
import sys
import time
import threading
import subprocess

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

_LOG_LOCK = threading.Lock()
SCRAPER_LOGS = []
_in_tee = threading.local()

class TerminalStreamTee:
    """Tee stream capturing sys.stdout & sys.stderr for rolling web terminal console (docker logs -f output)."""
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
                        formatted = f"[{timestamp}] {clean_line}" if not clean_line.startswith("[") else clean_line
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

def get_git_commit():
    """Returns active Git commit hash and branch name."""
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        return f"{commit} ({branch})"
    except Exception:
        return "Unknown"

def add_scraper_log(msg):
    print(msg)

def get_scraper_logs():
    with _LOG_LOCK:
        return list(SCRAPER_LOGS)

def clear_scraper_logs():
    global SCRAPER_LOGS
    with _LOG_LOCK:
        SCRAPER_LOGS = []

# Dynamic ATS lists re-exported from settings for backward compatibility
_settings = load_settings()
GREENHOUSE_COMPANIES = _settings.get("greenhouse_companies", [])
LEVER_COMPANIES = _settings.get("lever_companies", [])
ASHBY_COMPANIES = _settings.get("ashby_companies", [])
SMARTRECRUITERS_COMPANIES = _settings.get("smartrecruiters_companies", [])