import os
import json
import time
import re

NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
SEEN_EMAILS_FILE = "seen_emails.json"
DISCOVERED_JOBS_FILE = "discovered_jobs.json"
SETTINGS_FILE = "settings.json"
CLOSED_KB_FILE = "closed_keywords_kb.json"
REPORTED_CLOSED_FILE = "reported_closed_jobs.json"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

def load_reported_closed_jobs():
    if os.path.exists(REPORTED_CLOSED_FILE):
        try:
            with open(REPORTED_CLOSED_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_reported_closed_jobs(closed_map):
    try:
        with open(REPORTED_CLOSED_FILE, "w") as f:
            json.dump(closed_map, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving reported_closed_jobs: {e}")



DEFAULT_CLOSED_PHRASES = [
    "no longer accepting applications",
    "application closed",
    "job closed",
    "this position has been filled",
    "no longer available",
    "position closed",
    "applications are now closed",
    "role has been filled",
    "applications closed",
    "job is no longer active",
    "programme is now closed",
    "applications for this role have closed",
    "applications for this role are now closed",
    "this job posting has expired",
    "listing expired",
    "404 not found",
    "page not found",
    "this position is closed",
    "are now closed",
    "have now closed",
    "is no longer accepting",
    "not accepting applications",
    "role is now closed",
    "vacancy closed",
    "vacancy is closed",
    "applications are closed",
    "applications have closed",
    "position is filled",
    "role is filled"
]

def extract_generic_closure_phrases(html, company_name="", title_name=""):
    """
    Strips company names, dates, numbers, and noise from target webpage HTML,
    extracting clean generic 3-5 word closure phrases to train the knowledge base.
    """
    if not html or not isinstance(html, str):
        return []
    text = re.sub(r'<[^>]+>', ' ', html).lower()
    text = ' '.join(text.split())

    noise_items = [company_name.lower(), title_name.lower(), '2024', '2025', '2026', '2027', '2028', 'uk', 'london']
    closure_triggers = ['closed', 'filled', 'no longer', 'expired', 'paused', 'unavailable', 'ended', 'completed']

    extracted = []
    clauses = re.split(r'[\.\!\?\,\;\:]+', text)
    for clause in clauses:
        clause_str = clause.strip()
        if any(tr in clause_str for tr in closure_triggers):
            words = clause_str.split()
            for idx, w in enumerate(words):
                if any(tr in w for tr in closure_triggers):
                    start = max(0, idx - 2)
                    end = min(len(words), idx + 4)
                    sub = ' '.join(words[start:end])
                    if len(sub) > 6 and not any(nw and len(nw) > 3 and nw in sub for nw in noise_items):
                        extracted.append(sub)
    return list(set(extracted))

def load_closed_keywords_kb():
    kb = list(DEFAULT_CLOSED_PHRASES)
    if os.path.exists(CLOSED_KB_FILE):
        try:
            with open(CLOSED_KB_FILE, "r") as f:
                saved = json.load(f)
                if isinstance(saved, list):
                    for item in saved:
                        if item and item.lower() not in kb:
                            kb.append(item.lower())
        except Exception:
            pass
    else:
        save_closed_keywords_kb(kb)
    return kb



def save_closed_keywords_kb(kb_list):
    try:
        clean_kb = list(set([k.lower().strip() for k in kb_list if k and len(k.strip()) > 3]))
        with open(CLOSED_KB_FILE, "w") as f:
            json.dump(clean_kb, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving closed_keywords_kb: {e}")


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


COMPANY_ALIASES = {
    "mwinternshipprogram": "marshallwace",
    "marshallwace": "marshallwace",
    "mw": "marshallwace",
    "squarepointcapital": "squarepoint",
    "squarepoint": "squarepoint",
    "hudsonrivertrading": "hrt",
    "hrt": "hrt",
    "twosigma": "twosigma",
    "twosigmaca": "twosigma",
    "jumptrading": "jumptrading",
    "janestreet": "janestreet",
    "optiver": "optiver",
    "canonical": "canonical",
    "canonicaljobs": "canonical",
    "starlingbank": "starling",
    "starling": "starling",
    "beamng": "beamng",
    "wayve": "wayve",
    "palantir": "palantir",
    "samsara": "samsara",
    "cohere": "cohere",
    "notion": "notion",
    "ramp": "ramp"
}

def normalize_company(name):
    if not name:
        return ""
    cleaned = str(name).lower().strip()
    cleaned = re.sub(r'[^a-z0-9]', '', cleaned)
    for suffix in ["ltd", "inc", "plc", "llc", "capital", "technologies", "technology", "group", "uk", "europe", "limited", "careers", "jobs", "program"]:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 2:
            cleaned = cleaned[:-len(suffix)]
    
    if cleaned in COMPANY_ALIASES:
        return COMPANY_ALIASES[cleaned]
    
    for alias, canonical in COMPANY_ALIASES.items():
        if alias in cleaned or cleaned in alias:
            return canonical

    return cleaned

def normalize_role(title):
    if not title:
        return ""
    cleaned = str(title).lower().strip()
    cleaned = re.sub(r'-\s*(london|uk|2027|2026|remote)', '', cleaned)
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
    stop_words = {"london", "uk", "2027", "2026", "remote", "year", "in", "industry"}
    words = [w for w in cleaned.split() if w not in stop_words]
    return " ".join(words)

def normalize_url(url):
    """Strips query strings, tracking parameters, hashes, and trailing slashes for exact URL matching."""
    if not url or not isinstance(url, str):
        return ""
    cleaned = re.sub(r'^https?://(www\.)?', '', url.strip().lower())
    cleaned = cleaned.split('?')[0].split('#')[0].rstrip('/')
    return cleaned

def extract_ats_post_id(url):
    """Extracts unique ATS job post IDs (e.g. Greenhouse job ID, Lever job UUID, Ashby UUID)."""
    if not url or not isinstance(url, str):
        return None
    gh_match = re.search(r'greenhouse\.io/[^/]+/jobs/(\d+)', url, re.IGNORECASE)
    if gh_match:
        return f"gh_{gh_match.group(1)}"
    lev_match = re.search(r'lever\.co/[^/]+/([a-f0-9\-]{20,})', url, re.IGNORECASE)
    if lev_match:
        return f"lev_{lev_match.group(1)}"
    ash_match = re.search(r'ashbyhq\.com/[^/]+/([a-f0-9\-]{20,})', url, re.IGNORECASE)
    if ash_match:
        return f"ash_{ash_match.group(1)}"
    return None




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