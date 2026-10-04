import re
from urllib.parse import urlsplit, parse_qsl, urlencode

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
    "ramp": "ramp",
    "imctrading": "imc", "bnymellon": "bny", "bankofnewyorkmellon": "bny",
    "thephoenixpartnership": "tpp", "mizuhobanking": "mizuho"
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
    "quora": "Quora", "imc": "IMC", "imctrading": "IMC", "scaleai": "Scale AI",
    "databricks": "Databricks", "figma": "Figma", "stripe": "Stripe", "andurilindustries": "Anduril",
    "epicgames": "Epic Games", "hsbc": "HSBC", "bny": "BNY", "bnymellon": "BNY Mellon",
    "tpp": "TPP", "thephoenixpartnership": "TPP", "bae": "BAE Systems", "baesystems": "BAE Systems",
    "gresearch": "G-Research", "pimco": "PIMCO", "mbda": "MBDA", "pwc": "PwC",
    "tiktok": "TikTok", "dvtrading": "DV Trading", "mavensecuritiesholding": "Maven Securities"
}

def clean_company_display_name(name):
    if not name:
        return "Unknown"
    cleaned = str(name).strip()

    norm = re.sub(r'[^a-z0-9]', '', cleaned.lower())

    if norm in COMPANY_DISPLAY_NAMES:
        return COMPANY_DISPLAY_NAMES[norm]

    canonical = normalize_company(cleaned)
    if canonical in COMPANY_DISPLAY_NAMES:
        return COMPANY_DISPLAY_NAMES[canonical]

    for suffix in ["internshipprogram", "careers", "jobs", "program"]:
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

def stem_word(w):
    w = str(w).lower()
    for suffix in ["ing", "ships", "ship", "s", "ed"]:
        if w.endswith(suffix) and len(w) > len(suffix) + 3:
            return w[:-len(suffix)]
    return w

def fuzzy_roles_match(title1, title2, threshold=0.70):
    """
    Computes a token-set similarity ratio between two role titles after word stemming.
    Returns True if titles represent the same underlying scheme.
    """
    if not title1 or not title2:
        return False
    norm1 = normalize_role(title1)
    norm2 = normalize_role(title2)
    if norm1 == norm2:
        return True
    words1 = set(stem_word(w) for w in norm1.split())
    words2 = set(stem_word(w) for w in norm2.split())
    if not words1 or not words2:
        return False
    intersection = words1 & words2
    union = words1 | words2
    jaccard = len(intersection) / len(union) if union else 0.0
    return jaccard >= threshold

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
    parsed = urlsplit(url.strip())
    host = re.sub(r'^www\.', '', parsed.netloc.lower())
    query = [(k.lower(), v.lower()) for k, v in parse_qsl(parsed.query) if k.lower() in {'gh_jid', 'jobid', 'job', 'jid', 'reqid', 'requisitionid', 'vacancyid'}]
    suffix = '?' + urlencode(sorted(query)) if query else ''
    return host + parsed.path.lower().rstrip('/') + suffix


def same_listing(first, second):
    a, b = first.get('link', ''), second.get('link', '')
    if a and b and normalize_url(a) == normalize_url(b): return True
    a_id, b_id = extract_ats_post_id(a), extract_ats_post_id(b)
    if a_id and b_id: return a_id == b_id
    if normalize_company(first.get('company')) != normalize_company(second.get('company')): return False
    years_a = set(re.findall(r'\b20\d{2}\b', first.get('title', '')))
    years_b = set(re.findall(r'\b20\d{2}\b', second.get('title', '')))
    if years_a and years_b and years_a != years_b: return False
    if extract_program_type(first.get('title')) != extract_program_type(second.get('title')): return False
    def location_key(value):
        value = str(value or '').lower()
        value = re.sub(r'\b(?:united kingdom|england|uk|gbr|gb)\b', '', value)
        return re.sub(r'[^a-z0-9]+', ' ', value).strip()
    la, lb = location_key(first.get('location')), location_key(second.get('location'))
    if la and lb and la not in {'uk', 'unknown'} and lb not in {'uk', 'unknown'} and la != lb:
        if not (la in lb or lb in la): return False
    # Different specific employer posts are distinct. Cross-board equivalents
    # may merge only on a close title and compatible programme/location.
    aggregators = ('the-trackr', 'gradcracker', 'grb.uk.com', 'higherin.com', 'brightnetwork')
    if a and b and not any(x in a or x in b for x in aggregators): return False
    return fuzzy_roles_match(first.get('title'), second.get('title'), threshold=0.90)

def extract_ats_post_id(url):
    """Extracts unique ATS job post IDs (e.g. Greenhouse job ID, Lever job UUID, Ashby UUID)."""
    if not url or not isinstance(url, str):
        return None
    # Employer-hosted Greenhouse adverts expose their native ID in gh_jid.
    for key, value in parse_qsl(urlsplit(url).query):
        if key.lower() == 'gh_jid' and value.isdigit():
            return f'gh_{value}'
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

def deduplicate_job_list(job_list):
    """
    Multi-Layered Smart Job Deduplication:
    1. ATS Post ID Match (e.g. gh_5162206007 == gh_5162206007)
    2. Normalized URL Match (e.g. job-boards.greenhouse.io/verkada/jobs/5162206007)
    3. Company Name + Fuzzy Role Title Match (e.g. Verkada + Technical Support Engineer Industrial Placement)
    Collapses duplicates into single master cards with merged sources list.
    """
    if not job_list:
        return []

    deduped = []
    for item in job_list:
        if not item or not isinstance(item, dict):
            continue

        comp = item.get("company", "")
        title = item.get("title", "")
        link = item.get("link", "")

        norm_c = normalize_company(comp)
        norm_t = normalize_role(title)
        norm_u = normalize_url(link)
        ats_id = extract_ats_post_id(link)

        matched = False
        for existing in deduped:
            e_link = existing.get("link", "")
            e_comp = existing.get("company", "")
            e_title = existing.get("title", "")

            e_norm_c = normalize_company(e_comp)
            e_norm_t = normalize_role(e_title)
            e_norm_u = normalize_url(e_link)
            e_ats_id = extract_ats_post_id(e_link)

            is_match = False
            is_match = same_listing(item, existing)

            if is_match:
                matched = True
                existing['metadata'] = {**item.get('metadata', {}), **existing.get('metadata', {})}
                if not existing.get('deadline') and item.get('deadline'): existing['deadline'] = item['deadline']
                if "sources" not in existing:
                    existing["sources"] = [existing.get("source", "Discovered API")]
                src = item.get("source", "Discovered API")
                if isinstance(item.get("sources"), list):
                    for s in item.get("sources"):
                        if s not in existing["sources"]:
                            existing["sources"].append(s)
                elif src not in existing["sources"]:
                    existing["sources"].append(src)

                if any(ats in link.lower() for ats in ["greenhouse", "lever", "ashby", "smartrecruiters", "gradcracker"]):
                    existing["link"] = link
                    existing["source_url"] = item.get("source_url") or link
                    if comp and len(comp) > 2:
                        existing["company"] = clean_company_display_name(comp)
                    if title and len(title) > len(existing.get("title", "")):
                        existing["title"] = title
                break

        if not matched:
            item_copy = dict(item)
            item_copy["company"] = clean_company_display_name(comp)
            if "sources" not in item_copy:
                item_copy["sources"] = [item_copy.get("source", "Discovered API")]
            deduped.append(item_copy)

    return deduped
