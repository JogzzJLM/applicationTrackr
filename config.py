import os
import json
import time
import re

NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
SEEN_EMAILS_FILE = "seen_emails.json"
DISCOVERED_JOBS_FILE = "discovered_jobs.json"
SETTINGS_FILE = "settings.json"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

SCRAPER_LOGS = []

def add_scraper_log(msg):
    timestamp = time.strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {msg}"
    print(log_line)
    SCRAPER_LOGS.append(log_line)
    if len(SCRAPER_LOGS) > 300:
        SCRAPER_LOGS.pop(0)

def get_scraper_logs():
    return list(SCRAPER_LOGS)

def clear_scraper_logs():
    global SCRAPER_LOGS
    SCRAPER_LOGS = []


def normalize_company(name):
    if not name:
        return ""
    cleaned = str(name).lower().strip()
    cleaned = re.sub(r'[^a-z0-9]', '', cleaned)
    for suffix in ["ltd", "inc", "plc", "llc", "capital", "technologies", "technology", "group", "uk", "europe", "limited"]:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 2:
            cleaned = cleaned[:-len(suffix)]
    return cleaned

def normalize_role(title):
    if not title:
        return ""
    cleaned = str(title).lower().strip()
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned


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

HIDDEN_JOBS_FILE = "hidden_jobs.json"

def load_hidden_jobs():
    if os.path.exists(HIDDEN_JOBS_FILE):
        try:
            with open(HIDDEN_JOBS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_hidden_jobs(hidden_set):
    try:
        with open(HIDDEN_JOBS_FILE, "w") as f:
            json.dump(list(hidden_set), f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving hidden_jobs.json: {e}")

def hide_job(job_id):
    hidden = load_hidden_jobs()
    hidden.add(job_id)
    save_hidden_jobs(hidden)

DEFAULT_SETTINGS = {
    "grad_years_allowed": ["2027", "2028", "2029"],
    "exclude_keywords": [
        "vice president", "vp", "director", "head of", "principal", "senior manager",
        "sales development", "account executive", "recruiter", "marketing", "legal",
        "class of 2026", "graduating in 2026", "graduating 2026", "class of 2025", "graduating in 2025"
    ],
    "exclude_locations": ["us government", "aus government", "poland", "france", "japan", "canada", "australia", "singapore", "usg", "us defense", "defense tech - us"],
    "my_skills": ["python", "java", "javascript", "html", "css", "sql"],
    "role_keywords": ["software", "developer", "engineer", "engineering", "backend", "fullstack", "full-stack", "systems", "quant", "quantitative", "trader", "trading", "research", "machine learning", "ml", "ai", "data science", "cyber", "security", "cloud", "devops", "technology", "collaboration"],
    "level_keywords": ["intern", "internship", "placement", "industrial placement", "sandwich", "spring week", "insight week", "graduate", "grad", "early talent", "early career", "undergrad"],
    "location_keywords": ["london", "birmingham", "oxford", "aylesbury", "west midlands", "remote", "uk", "united kingdom", "cambridge", "manchester", "edinburgh"],
    "special_intl_companies": ["beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", "hrt", "twosigma", "imc", "flowtraders", "wayve", "samsara", "quadrature", "millennium"],
    "auto_hide_applied_company_jobs": False
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_SETTINGS

def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
            print("💾 Saved updated filter settings to settings.json")
    except Exception as e:
        print(f"⚠️ Error saving settings.json: {e}")


GREENHOUSE_COMPANIES = [
    "deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", 
    "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading", 
    "palantir", "millennium", "quadrature", "samsara", "imc", "bloomberg",
    "two-sigma", "jump-trading", "barclays"
]
LEVER_COMPANIES = ["spotify", "revolut", "checkout", "beamng", "wayve", "palantir", "five-ai"]
ASHBY_COMPANIES = ["mistral", "synthesia", "multiverse", "ramp", "huggingface", "cohere", "notion", "scaleai"]
SMARTRECRUITERS_COMPANIES = ["squarepointcapital", "visa", "ubisoft", "zalando", "bosch"]