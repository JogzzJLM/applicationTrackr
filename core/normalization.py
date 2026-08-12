import re

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

COMPANY_DISPLAY_NAMES = {
    "marshallwace": "Marshall Wace",
    "mwinternshipprogram": "Marshall Wace",
    "mw": "Marshall Wace",
    "squarepoint": "Squarepoint Capital",
    "squarepointcapital": "Squarepoint Capital",
    "hrt": "Hudson River Trading",
    "hudsonrivertrading": "Hudson River Trading",
    "jumptrading": "Jump Trading",
    "janestreet": "Jane Street",
    "optiver": "Optiver",
    "twosigma": "Two Sigma",
    "canonical": "Canonical",
    "canonicaljobs": "Canonical",
    "starling": "Starling Bank",
    "starlingbank": "Starling Bank",
    "palantir": "Palantir",
    "cohere": "Cohere",
    "samsara": "Samsara",
    "ramp": "Ramp",
    "notion": "Notion",
    "beamng": "BeamNG",
    "wayve": "Wayve",
    "verkada": "Verkada",
    "quora": "Quora"
}

def clean_company_display_name(name):
    if not name:
        return "Unknown"
    cleaned = str(name).strip()

    norm = re.sub(r'[^a-z0-9]', '', cleaned.lower())

    if norm in COMPANY_DISPLAY_NAMES:
        return COMPANY_DISPLAY_NAMES[norm]

    for key, display_val in COMPANY_DISPLAY_NAMES.items():
        if key == norm or key in norm:
            return display_val

    for suffix in ["internshipprogram", "careers", "jobs", "program", "limited", "ltd", "inc", "plc", "llc"]:
        if cleaned.lower().endswith(suffix) and len(cleaned) > len(suffix) + 2:
            cleaned = cleaned[:-len(suffix)].strip()

    cleaned = re.sub(r'([a-z])([A-Z])', r'\1 \2', cleaned)
    cleaned = cleaned.replace('-', ' ').replace('_', ' ')
    cleaned = ' '.join(word.capitalize() for word in cleaned.split())
    return cleaned if cleaned else name

def normalize_company(name):
    if not name:
        return ""
    cleaned = str(name).lower().strip()
    cleaned = re.sub(r'[^a-z0-9]', '', cleaned)
    for suffix in ["ltd", "inc", "plc", "llc", "capital", "technologies", "technology", "group", "uk", "europe", "limited", "careers", "jobs", "program", "internshipprogram"]:
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

def extract_program_type(title):
    """
    Extracts the academic year programme target:
    - 'placement': Industrial Placement / 12-month (Year 2 target)
    - 'internship': Summer / Spring Internship (Year 2 target)
    - 'graduate': Full-time Graduate Scheme (Year 3 / Final Year target)
    """
    if not title:
        return "graduate"
    t_lower = str(title).lower()

    if any(k in t_lower for k in ["placement", "industrial placement", "12-month", "12 month", "year in industry", "co-op", "coop"]):
        return "placement"
    elif any(k in t_lower for k in ["intern", "internship", "summer", "spring", "off-cycle", "insight"]):
        return "internship"
    elif any(k in t_lower for k in ["grad", "graduate", "entry level", "new grad", "analyst programme"]):
        return "graduate"
    else:
        if "2027" in t_lower:
            return "internship"
        return "graduate"

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
