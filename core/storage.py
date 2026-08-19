import os
import json
import threading

_FILE_LOCK = threading.Lock()

PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

SEEN_JOBS_FILE = "seen_jobs.json"
SEEN_EMAILS_FILE = "seen_emails.json"
DISCOVERED_JOBS_FILE = "discovered_jobs.json"
SETTINGS_FILE = "settings.json"
CLOSED_KB_FILE = "closed_keywords_kb.json"
REPORTED_CLOSED_FILE = "reported_closed_jobs.json"
CLOSED_URLS_CACHE_FILE = "closed_urls_cache.json"
HIDDEN_JOBS_FILE = "hidden_jobs.json"
SCRAPER_STATUS_FILE = "scraper_status.json"
PENDING_EMAILS_FILE = "pending_email_updates.json"

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
    "auto_hide_applied_company_jobs": False,
    "greenhouse_companies": [
        "deliveroo", "cloudflare", "snyk", "monzo", "starlingbank",
        "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading",
        "palantir", "millennium", "quadrature", "samsara", "imc", "bloomberg",
        "two-sigma", "jump-trading", "barclays"
    ],
    "lever_companies": ["spotify", "revolut", "checkout", "beamng", "wayve", "palantir", "five-ai"],
    "ashby_companies": ["mistral", "synthesia", "multiverse", "ramp", "huggingface", "cohere", "notion", "scaleai"],
    "smartrecruiters_companies": ["squarepointcapital", "visa", "ubisoft", "zalando", "bosch"]
}

DEFAULT_SCRAPER_STATUS = {
    "last_run": "Never",
    "total_seen_jobs": 0,
    "total_discovered_jobs": 0,
    "last_new_jobs_found": 0,
    "source_status": {
        "Greenhouse API": "🟢 Active • 19/19 target companies online",
        "Lever API": "🟢 Active • 7/7 target companies online",
        "Ashby API": "🟢 Active • 8/8 target companies online",
        "SmartRecruiters API": "🟢 Active • 5/5 target companies online",
        "The Trackr API": "🟢 Active • Tier-1 Direct Egress",
        "Gradcracker API": "🟢 Active • 4 STEM Sectors Online",
        "Gmail Inbox Listener": "🟢 Active • Email auto-tracker active"
    }
}

def atomic_write_json(filepath, data, indent=2):
    """Atomic write to JSON file using a temp file and os.replace to prevent corruption."""
    with _FILE_LOCK:
        tmp_filepath = f"{filepath}.tmp"
        try:
            with open(tmp_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            os.replace(tmp_filepath, filepath)
        except Exception as e:
            if os.path.exists(tmp_filepath):
                try:
                    os.remove(tmp_filepath)
                except Exception:
                    pass
            print(f"⚠️ Error performing atomic JSON write to {filepath}: {e}")

def load_json_safe(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_val

def load_reported_closed_jobs():
    return load_json_safe(REPORTED_CLOSED_FILE, {})

def save_reported_closed_jobs(closed_map):
    atomic_write_json(REPORTED_CLOSED_FILE, closed_map)

def load_closed_urls_cache():
    data = load_json_safe(CLOSED_URLS_CACHE_FILE, [])
    return set(data) if isinstance(data, list) else set()

def save_closed_urls_cache(closed_set):
    atomic_write_json(CLOSED_URLS_CACHE_FILE, list(closed_set))

def mark_url_as_closed(url):
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return
    closed = load_closed_urls_cache()
    if url not in closed:
        closed.add(url)
        save_closed_urls_cache(closed)

def load_hidden_jobs():
    data = load_json_safe(HIDDEN_JOBS_FILE, [])
    return set(data) if isinstance(data, list) else set()

def save_hidden_jobs(hidden_set):
    atomic_write_json(HIDDEN_JOBS_FILE, list(hidden_set))

def hide_job(job_id):
    hidden = load_hidden_jobs()
    hidden.add(job_id)
    save_hidden_jobs(hidden)

def load_pending_email_updates():
    return load_json_safe(PENDING_EMAILS_FILE, [])

def save_pending_email_updates(updates):
    atomic_write_json(PENDING_EMAILS_FILE, updates)

def add_pending_email_update(update_obj):
    updates = load_pending_email_updates()
    updates.append(update_obj)
    save_pending_email_updates(updates)

def remove_pending_email_update(update_id):
    updates = load_pending_email_updates()
    filtered = [u for u in updates if u.get("id") != update_id]
    save_pending_email_updates(filtered)
    return len(updates) - len(filtered)

def load_settings():
    loaded = load_json_safe(SETTINGS_FILE, None)
    if loaded and isinstance(loaded, dict):
        merged = dict(DEFAULT_SETTINGS)
        merged.update(loaded)
        return merged
    return dict(DEFAULT_SETTINGS)

def save_settings(data):
    atomic_write_json(SETTINGS_FILE, data)
    print("💾 Saved updated filter settings to settings.json")

def save_scraper_status(status_dict):
    atomic_write_json(SCRAPER_STATUS_FILE, status_dict)

def load_scraper_status():
    loaded = load_json_safe(SCRAPER_STATUS_FILE, None)
    if loaded and isinstance(loaded, dict):
        merged = dict(DEFAULT_SCRAPER_STATUS)
        merged.update(loaded)
        if "source_status" in loaded:
            merged["source_status"] = dict(DEFAULT_SCRAPER_STATUS["source_status"])
            merged["source_status"].update(loaded["source_status"])
        return merged
    return dict(DEFAULT_SCRAPER_STATUS)
