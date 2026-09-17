from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from core.storage import DATA_DIR, atomic_write_json, load_json_safe

AUTOAPPLY_DIR = Path(DATA_DIR) / "autoapply"
AUTOAPPLY_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_FILE = AUTOAPPLY_DIR / "applicant_profile.json"

PROFILE_TEMPLATE: Dict[str, Any] = {
    "personal": {
        "first_name": "", "last_name": "", "preferred_name": "", "email": "", "phone": "",
        "address_line1": "", "address_line2": "", "city": "", "postcode": "", "country": "United Kingdom",
    },
    "education": {"university": "", "degree": "", "course": "", "graduation_year": "", "grade": ""},
    "links": {"linkedin": "", "github": "", "portfolio": ""},
    "eligibility": {"right_to_work_uk": "", "requires_sponsorship": ""},
    "documents": {"resume_path": "/data/autoapply/resume.pdf", "cover_letter_path": ""},
    "answers": {},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def ensure_profile() -> Dict[str, Any]:
    existing = load_json_safe(str(PROFILE_FILE), None)
    merged = _deep_merge(PROFILE_TEMPLATE, existing if isinstance(existing, dict) else {})
    if not PROFILE_FILE.exists():
        atomic_write_json(str(PROFILE_FILE), merged)
    return merged


def save_profile(profile: Dict[str, Any]) -> None:
    atomic_write_json(str(PROFILE_FILE), _deep_merge(PROFILE_TEMPLATE, profile or {}))


def flatten_profile(profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
    profile = profile or ensure_profile()
    flat: Dict[str, Any] = {}
    def walk(prefix: str, value: Any):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            flat[prefix] = value
    walk("", profile)
    return flat


def profile_completeness(profile: Dict[str, Any] | None = None) -> Tuple[int, list[str]]:
    flat = flatten_profile(profile)
    required = [
        "personal.first_name", "personal.last_name", "personal.email", "personal.phone",
        "personal.city", "personal.postcode", "education.university", "education.course",
        "education.graduation_year", "links.linkedin", "links.github", "documents.resume_path",
    ]
    missing = [key for key in required if not str(flat.get(key, "")).strip()]
    return round((len(required) - len(missing)) / len(required) * 100), missing
