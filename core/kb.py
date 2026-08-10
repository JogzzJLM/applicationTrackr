import os
import re
from core.storage import CLOSED_KB_FILE, load_json_safe, atomic_write_json

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
    "role is filled",
    "doesn't exist",
    "does not exist",
    "page you are looking for",
    "job no longer exists",
    "cannot be found",
    "posting has been removed",
    "job listing has been removed",
    "position no longer exists"
]

def extract_generic_closure_phrases(html, company_name="", title_name=""):
    """
    Truly Universal Zero-Shot Closure Extractor:
    1. Extracts clauses matching known trigger words (if present).
    2. Zero-Shot Fallback: Parses text clauses from headings, status banners, alerts, and notice elements.
    3. Strips company names, role titles, dates, locations, and common web boilerplate noise.
    """
    if not html or not isinstance(html, str):
        return []
    text = re.sub(r'<[^>]+>', ' ', html).lower()
    text = ' '.join(text.split())

    noise_items = [company_name.lower(), title_name.lower(), '2024', '2025', '2026', '2027', '2028', 'uk', 'london', 'privacy', 'policy', 'rights', 'reserved', 'copyright', 'cookie', 'cookies', 'terms', 'conditions', 'contact', 'home']
    closure_triggers = [
        'closed', 'filled', 'no longer', 'expired', 'paused', 'unavailable',
        'ended', 'completed', 'exist', 'does not exist', 'doesn\'t exist',
        'not found', 'removed', 'inactive', 'cannot be found', 'archived',
        'deactivated', 'concluded', 'finished', 'passed', 'full'
    ]

    extracted = []
    # Strategy A: Clause Extraction via Triggers
    clauses = re.split(r'[\.\!\?\,\;\:]+', text)
    for clause in clauses:
        clause_str = clause.strip()
        if any(tr in clause_str for tr in closure_triggers):
            words = clause_str.split()
            for idx, w in enumerate(words):
                if any(tr in w for tr in closure_triggers):
                    start = max(0, idx - 3)
                    end = min(len(words), idx + 4)
                    sub = ' '.join(words[start:end])
                    if len(sub) > 6 and not any(nw and len(nw) > 3 and nw in sub for nw in noise_items):
                        extracted.append(sub)

    # Strategy B: Zero-Shot Heading & Banner Extraction (Handles completely novel wording)
    banner_matches = re.findall(r'<(h[1-4]|div|p|span)[^>]*?(?:class|id|role)=["\'][^"\']*?(?:alert|banner|notice|status|error|message|closed|hero|title|heading)[^"\']*?>(.*?)</\1>', html, re.IGNORECASE | re.DOTALL)
    for _, banner_html in banner_matches:
        banner_text = re.sub(r'<[^>]+>', ' ', banner_html).lower()
        banner_clean = ' '.join(banner_text.split())
        for sub_clause in re.split(r'[\.\!\?\,\;\:]+', banner_clean):
            words = sub_clause.strip().split()
            if 3 <= len(words) <= 7:
                sub = ' '.join(words)
                if len(sub) > 8 and not any(nw and len(nw) > 3 and nw in sub for nw in noise_items):
                    extracted.append(sub)

    return list(set(extracted))

def load_closed_keywords_kb():
    kb = list(DEFAULT_CLOSED_PHRASES)
    saved = load_json_safe(CLOSED_KB_FILE, None)
    if saved and isinstance(saved, list):
        for item in saved:
            if item and item.lower() not in kb:
                kb.append(item.lower())
    else:
        save_closed_keywords_kb(kb)
    return kb

def save_closed_keywords_kb(kb_list):
    clean_kb = list(set([k.lower().strip() for k in kb_list if k and len(k.strip()) > 3]))
    atomic_write_json(CLOSED_KB_FILE, clean_kb)
