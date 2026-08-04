import os

NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
SEEN_EMAILS_FILE = "seen_emails.json"
DISCOVERED_JOBS_FILE = "discovered_jobs.json"
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

EXCLUDE_KEYWORDS = ["vice president", "vp", "director", "head of", "principal", "senior manager", "sales development", "account executive", "recruiter", "marketing", "legal"]
EXCLUDE_LOCATIONS = ["us government", "aus government", "poland", "france", "japan", "canada", "australia", "singapore", "usg", "us defense", "defense tech - us"]

LEVEL_KEYWORDS = ["intern", "internship", "placement", "industrial placement", "sandwich", "spring week", "insight week", "graduate", "grad", "early talent", "early career", "undergrad"]
ROLE_KEYWORDS = ["software", "developer", "engineer", "engineering", "backend", "fullstack", "full-stack", "systems", "quant", "quantitative", "trader", "trading", "research", "machine learning", "ml", "ai", "data science", "cyber", "security", "cloud", "devops", "technology", "collaboration"]
LOCATION_KEYWORDS = ["london", "birmingham", "oxford", "aylesbury", "west midlands", "remote", "uk", "united kingdom", "cambridge", "manchester", "edinburgh"]
SPECIAL_INTL_COMPANIES = ["beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", "hrt", "twosigma", "imc", "flowtraders", "wayve", "samsara", "quadrature", "millennium"]

GREENHOUSE_COMPANIES = ["deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading", "palantir", "millennium", "quadrature", "samsara", "imc"]
LEVER_COMPANIES = ["spotify", "revolut", "checkout", "beamng", "wayve", "palantir"]