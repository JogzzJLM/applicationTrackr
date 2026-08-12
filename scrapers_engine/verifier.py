import re
import requests
from core.kb import load_closed_keywords_kb
from core.storage import load_closed_urls_cache, mark_url_as_closed

def verify_live_page_applyable(url):
    """
    Multi-Layered Live Page Verification Engine:
    1. Persistent Closed Cache Lookup (0ms instant return if previously closed)
    2. HTTP Status & ATS Redirect Egress Check
    3. Punctuation-Insensitive Knowledge Base Phrase Match
    4. High-Confidence Structural Proximity & Semantic Heuristic Rules
    5. Dynamic SPA & Meta Title Verification (Workday, Ashby, HiBob, Phenom, Eightfold)
    """
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return False

    closed_urls = load_closed_urls_cache()
    if url in closed_urls:
        return False

    kb_phrases = load_closed_keywords_kb()

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        resp = requests.get(url, headers=headers, timeout=4, allow_redirects=True)
        if resp.status_code in [404, 410, 403, 500]:
            mark_url_as_closed(url)
            return False

        # Layer 1: ATS Redirect & Expired Egress Check
        if "linkedin.com" in url or "linkedin.com" in resp.url:
            if "expired_jd_redirect" in resp.url or "trk=expired" in resp.url or ("/jobs/view/" in url and "/jobs/view/" not in resp.url):
                print(f"  [Live Closure Check] 🛑 LinkedIn Expired Redirect Match: {url} -> {resp.url}")
                mark_url_as_closed(url)
                return False

        if resp.url and resp.url != url:
            orig_path = url.split('?')[0].rstrip('/')
            final_path = resp.url.split('?')[0].rstrip('/')
            if len(orig_path.split('/')) > len(final_path.split('/')) and len(final_path.split('/')) <= 4:
                print(f"  [Live Closure Check] 🛑 Redirected from specific post to generic portal: {url} -> {resp.url}")
                mark_url_as_closed(url)
                return False

        # Clean HTML & normalize text by replacing punctuation with spaces
        text_raw = re.sub(r'<[^>]+>', ' ', resp.text).lower()
        text_clean = ' '.join(re.sub(r'[^a-z0-9\s]', ' ', text_raw).split())

        # Layer 2: Punctuation-Insensitive KB Phrase Match
        for phrase in kb_phrases:
            phrase_clean = ' '.join(re.sub(r'[^a-z0-9\s]', ' ', phrase.lower()).split())
            if phrase_clean and phrase_clean in text_clean:
                print(f"  [Live Closure Check] 🛑 Page indicates role is closed ('{phrase_clean}'): {url}")
                mark_url_as_closed(url)
                return False

        # Layer 3: High-Confidence Structural Proximity & Semantic Rules
        closure_states = {'closed', 'filled', 'expired', 'paused', 'unavailable', 'inactive', 'exist', 'removed', 'missing', 'invalid'}
        job_nouns = {'application', 'applications', 'programme', 'program', 'role', 'position', 'vacancy', 'scheme', 'opportunity', 'posting', 'job', 'page'}

        words = text_clean.split()
        for idx, w in enumerate(words):
            if w in closure_states:
                window = set(words[max(0, idx-5):min(len(words), idx+6)])
                if window & job_nouns:
                    print(f"  [Live Closure Check] 🛑 Structural Proximity Match ('{w}' near {window & job_nouns}): {url}")
                    mark_url_as_closed(url)
                    return False

        if 'no longer' in text_clean and any(k in text_clean for k in ['accepting', 'available', 'taking', 'open']):
            print(f"  [Live Closure Check] 🛑 Semantic Rule Match ('no longer accepting/available'): {url}")
            mark_url_as_closed(url)
            return False

        if any(k in text_clean for k in ["doesn t exist", "does not exist", "page you are looking for", "cannot be found"]):
            print(f"  [Live Closure Check] 🛑 Missing Page Rule Match ('doesn't exist / cannot be found'): {url}")
            mark_url_as_closed(url)
            return False

        # Layer 4: Dynamic SPA & Meta Title Verification (Workday, Ashby, HiBob, Phenom, Eightfold)
        title_match = re.search(r'<title>([^<]+)</title>', resp.text, re.IGNORECASE)
        page_title = title_match.group(1).strip().lower() if title_match else ''

        og_match = re.search(r'<meta\s+[^>]*property=[\"\']og:title[\"\']\s+content=[\"\']([^\"\']+)[\"\']', resp.text, re.IGNORECASE)
        og_title = og_match.group(1).strip().lower() if og_match else ''

        if any(ats in url for ats in ["myworkdayjobs.com", "eightfold.ai", "phenom.com"]):
            if not og_title:
                if not page_title:
                    print(f"  [Live Closure Check] 🛑 Dynamic SPA Metadata Check Failed (Empty og:title & title): {url}")
                    mark_url_as_closed(url)
                    return False

        is_specific_post = any(c in url for c in ['-', '_']) and (re.search(r'/[0-9a-f\-]{8,}', url, re.I) or re.search(r'/\d{5,}', url) or 'job' in url.lower())
        generic_titles = {'jobs', 'careers', 'job openings', 'career opportunities', 'welcome', 'search jobs', 'job search', 'workday', 'ashby'}

        if is_specific_post and page_title in generic_titles and not og_title:
            print(f"  [Live Closure Check] 🛑 Generic Meta Title Fallback Check Failed (title: '{page_title}'): {url}")
            mark_url_as_closed(url)
            return False

        return True

    except Exception:
        # If timeout or connection issue, permit to avoid false negatives
        return True
