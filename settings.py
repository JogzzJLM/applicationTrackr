import os
import json
from config import FILTER_SETTINGS_FILE

DEFAULT_SETTINGS = {
    "active_profile": "jog",
    "profiles": {
        "jog": {
            "name": "Jog (Maths & CS Student)",
            "grad_years": ["2028", "2029"],
            "excluded_skills": ["c++", "c/c++", "golang", "rust"],
            "include_keywords": ["software", "developer", "engineer", "engineering", "backend", "fullstack", "full-stack", "systems", "quant", "quantitative", "trader", "trading", "research", "machine learning", "ml", "ai", "data science", "cyber", "security", "cloud", "devops", "technology", "collaboration"],
            "exclude_keywords": ["vice president", "vp", "director", "head of", "principal", "senior manager", "sales development", "account executive", "recruiter", "marketing", "legal"],
            "exclude_locations": ["us government", "aus government", "poland", "france", "japan", "canada", "australia"],
            "target_locations": ["london", "birmingham", "oxford", "aylesbury", "west midlands", "remote", "uk"]
        },
        "uncle": {
            "name": "Uncle (Outside IR35 Tech Contracts)",
            "grad_years": [],
            "excluded_skills": [],
            "include_keywords": ["outside ir35", "contractor", "interim", "senior engineer", "lead developer", "solutions architect", "contract"],
            "exclude_keywords": ["inside ir35", "junior", "intern", "graduate", "trainee"],
            "exclude_locations": [],
            "target_locations": ["uk", "remote", "london", "west midlands"]
        },
        "brother": {
            "name": "Brother (Accounting & Finance Grad)",
            "grad_years": ["2024", "2025", "2026"],
            "excluded_skills": ["software engineer", "developer"],
            "include_keywords": ["accounting", "finance", "audit", "tax", "advisory", "financial analyst", "aca", "acca", "cima", "graduate scheme", "trainee accountant"],
            "exclude_keywords": ["software", "coding", "quant developer"],
            "exclude_locations": [],
            "target_locations": ["uk", "london", "birmingham", "manchester"]
        }
    }
}

def load_filter_settings():
    if os.path.exists(FILTER_SETTINGS_FILE):
        try:
            with open(FILTER_SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_SETTINGS

def save_filter_settings(settings):
    try:
        with open(FILTER_SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Error saving settings: {e}")

def get_active_profile_settings():
    settings = load_filter_settings()
    active_key = settings.get("active_profile", "jog")
    profiles = settings.get("profiles", {})
    return profiles.get(active_key, DEFAULT_SETTINGS["profiles"]["jog"])