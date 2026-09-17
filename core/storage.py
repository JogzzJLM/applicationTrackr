import os
import json
import threading
import shutil
from pathlib import Path

_FILE_LOCK = threading.Lock()


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PORT = _env_int("PORT", 5000)


def _state_file(filename):
    target = DATA_DIR / filename
    seed = PROJECT_ROOT / filename
    if not target.exists() and seed.exists() and seed.resolve() != target.resolve():
        try:
            shutil.copy2(seed, target)
        except Exception as exc:
            print(f"⚠️ Could not seed {filename} into {DATA_DIR}: {exc}")
    return str(target)


SEEN_JOBS_FILE = _state_file("seen_jobs.json")
SEEN_EMAILS_FILE = _state_file("seen_emails.json")
DISCOVERED_JOBS_FILE = _state_file("discovered_jobs.json")
SETTINGS_FILE = _state_file("settings.json")
CLOSED_KB_FILE = _state_file("closed_keywords_kb.json")
REPORTED_CLOSED_FILE = _state_file("reported_closed_jobs.json")
CLOSED_URLS_CACHE_FILE = _state_file("closed_urls_cache.json")
HIDDEN_JOBS_FILE = _state_file("hidden_jobs.json")
SCRAPER_STATUS_FILE = _state_file("scraper_status.json")
PENDING_EMAILS_FILE = _state_file("pending_email_updates.json")

SETTINGS_SCHEMA_VERSION = 4
DEFAULT_SETTINGS = {
    "settings_schema_version": SETTINGS_SCHEMA_VERSION,
    "grad_years_allowed": ["2027", "2028", "2029"],
    "target_programmes": ["internship", "placement"],
    "target_role_categories": ["software", "ai_ml", "quant", "cyber"],
    "strict_location_filter": True,
    "allow_unknown_location": False,
    "allow_special_international": False,
    # 55 was intentionally high-recall but admitted too many merely technical roles.
    # 65 keeps clear SWE/ML/quant/cyber titles while requiring evidence for generic titles.
    "relevance_min_score": 65,
    "exclude_keywords": [
        "vice president", "vp", "director", "head of", "principal", "staff engineer",
        "senior manager", "engineering manager", "lead engineer", "marketing", "social media",
        "accounting", "internal audit", "sales", "public policy", "legal", "human resources",
        "recruiter", "actuarial", "class of 2026", "graduating in 2026", "class of 2025",
        "product manager", "project manager", "business development", "customer success",
    ],
    "exclude_locations": [
        "united states", "usa", "canada", "australia", "singapore", "hong kong",
        "japan", "france", "poland", "germany",
    ],
    "my_skills": ["python", "java", "javascript", "sql", "git", "linux", "docker", "data structures", "algorithms"],
    "role_keywords": ["software", "developer", "engineer", "backend", "frontend", "full stack", "systems", "quant", "quantitative", "machine learning", "data science", "cyber", "security", "cloud", "devops"],
    "level_keywords": ["intern", "internship", "placement", "industrial placement", "sandwich", "spring week", "insight week", "graduate", "early career", "undergrad"],
    "location_keywords": ["london", "birmingham", "oxford", "aylesbury", "west midlands", "uk", "united kingdom", "england", "scotland", "wales", "cambridge", "manchester", "edinburgh", "bristol", "leeds", "glasgow", "reading", "uk remote", "remote uk"],
    "special_intl_companies": ["beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", "hrt", "twosigma", "imc", "flowtraders", "wayve", "samsara", "quadrature", "millennium"],
    "auto_hide_applied_company_jobs": False,
    "greenhouse_companies": ["deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading", "palantir", "millennium", "quadrature", "samsara", "imc", "bloomberg", "two-sigma", "jump-trading", "barclays"],
    "lever_companies": ["spotify", "revolut", "checkout", "beamng", "wayve", "palantir", "five-ai"],
    "ashby_companies": ["mistral", "synthesia", "multiverse", "ramp", "huggingface", "cohere", "notion", "scaleai"],
    "smartrecruiters_companies": ["squarepointcapital", "visa", "ubisoft", "zalando", "bosch"],
}

DEFAULT_SCRAPER_STATUS = {
    "last_run": "Never", "total_seen_jobs": 0, "total_discovered_jobs": 0,
    "last_new_jobs_found": 0, "last_irrelevant_pruned": 0,
    "source_status": {
        "Greenhouse API": "🟢 Active • target companies configured",
        "Lever API": "🟢 Active • target companies configured",
        "Ashby API": "🟢 Active • target companies configured",
        "SmartRecruiters API": "🟢 Active • target companies configured",
        "The Trackr API": "🟢 Active • UK Tech source",
        "Gradcracker API": "🟢 Active • Computing & Maths sectors",
        "Email Inbox Listener": "🟢 Active • email auto-tracker active",
    },
}


def atomic_write_json(filepath, data, indent=2):
    filepath = str(filepath)
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with _FILE_LOCK:
        tmp = f"{filepath}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent, ensure_ascii=False)
            os.replace(tmp, filepath)
        except Exception as exc:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except Exception: pass
            print(f"⚠️ Error performing atomic JSON write to {filepath}: {exc}")


def load_json_safe(filepath, default_val):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return default_val


def load_reported_closed_jobs(): return load_json_safe(REPORTED_CLOSED_FILE, {})
def save_reported_closed_jobs(value): atomic_write_json(REPORTED_CLOSED_FILE, value)
def load_closed_urls_cache():
    value = load_json_safe(CLOSED_URLS_CACHE_FILE, [])
    return set(value) if isinstance(value, list) else set()
def save_closed_urls_cache(value): atomic_write_json(CLOSED_URLS_CACHE_FILE, list(value))
def mark_url_as_closed(url):
    if url and isinstance(url, str) and url.startswith("http"):
        values = load_closed_urls_cache()
        if url not in values:
            values.add(url); save_closed_urls_cache(values)
def load_hidden_jobs():
    value = load_json_safe(HIDDEN_JOBS_FILE, [])
    return set(value) if isinstance(value, list) else set()
def save_hidden_jobs(value): atomic_write_json(HIDDEN_JOBS_FILE, list(value))
def hide_job(job_id):
    values = load_hidden_jobs(); values.add(job_id); save_hidden_jobs(values)
def load_pending_email_updates(): return load_json_safe(PENDING_EMAILS_FILE, [])
def save_pending_email_updates(value): atomic_write_json(PENDING_EMAILS_FILE, value)
def add_pending_email_update(value):
    values = load_pending_email_updates(); values.append(value); save_pending_email_updates(values)
def remove_pending_email_update(update_id):
    values = load_pending_email_updates(); filtered = [v for v in values if v.get("id") != update_id]; save_pending_email_updates(filtered); return len(values)-len(filtered)


def _merge_settings(loaded):
    merged = dict(DEFAULT_SETTINGS)
    if not isinstance(loaded, dict):
        return merged

    try:
        saved_schema = int(loaded.get("settings_schema_version", 1) or 1)
    except (TypeError, ValueError):
        saved_schema = 1

    merged.update(loaded)
    merged["settings_schema_version"] = SETTINGS_SCHEMA_VERSION

    for key in ("exclude_keywords", "exclude_locations"):
        saved = loaded.get(key, []) if isinstance(loaded.get(key), list) else []
        combined, seen = [], set()
        for value in list(saved) + list(DEFAULT_SETTINGS[key]):
            norm = str(value).strip().lower()
            if norm and norm not in seen:
                seen.add(norm); combined.append(str(value).strip())
        merged[key] = combined

    locations = merged.get("location_keywords", []) if isinstance(merged.get("location_keywords"), list) else []
    merged["location_keywords"] = [x for x in locations if str(x).strip().lower() not in {"remote", "hybrid", "in-office", "onsite", "on-site"}]
    for value in DEFAULT_SETTINGS["location_keywords"]:
        if value not in merged["location_keywords"]:
            merged["location_keywords"].append(value)

    if saved_schema < 3:
        old_programmes = loaded.get("target_programmes")
        if not old_programmes or set(old_programmes) == {"internship", "placement", "graduate"}:
            merged["target_programmes"] = list(DEFAULT_SETTINGS["target_programmes"])

    # v4 raises the old broad default threshold. Preserve a deliberate custom
    # threshold above 55, but migrate the previous default automatically.
    if saved_schema < 4:
        try:
            old_threshold = int(loaded.get("relevance_min_score", 55))
        except (TypeError, ValueError):
            old_threshold = 55
        if old_threshold <= 55:
            merged["relevance_min_score"] = DEFAULT_SETTINGS["relevance_min_score"]

    return merged


def load_settings(): return _merge_settings(load_json_safe(SETTINGS_FILE, None))
def save_settings(data):
    atomic_write_json(SETTINGS_FILE, _merge_settings(data)); print("💾 Saved updated filter settings to settings.json")
def save_scraper_status(value): atomic_write_json(SCRAPER_STATUS_FILE, value)
def load_scraper_status():
    loaded = load_json_safe(SCRAPER_STATUS_FILE, None)
    if loaded and isinstance(loaded, dict):
        merged = dict(DEFAULT_SCRAPER_STATUS); merged.update(loaded)
        merged["source_status"] = dict(DEFAULT_SCRAPER_STATUS["source_status"])
        merged["source_status"].update(loaded.get("source_status", {}))
        return merged
    return dict(DEFAULT_SCRAPER_STATUS)
