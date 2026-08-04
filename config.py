import os
import json

NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
SEEN_EMAILS_FILE = "seen_emails.json"
DISCOVERED_JOBS_FILE = "discovered_jobs.json"
SETTINGS_FILE = "settings.json"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

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
    "special_intl_companies": ["beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", "hrt", "twosigma", "imc", "flowtraders", "wayve", "samsara", "quadrature", "millennium"]
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