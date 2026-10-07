"""Readable coverage diagnostics; unsuitable candidates never become apply cards."""
from collections import Counter
from html import escape
from urllib.parse import urlparse
from config import SCRAPER_STATUS
from core.storage import load_json_safe
from scrapers_engine.quality import REVIEW_FILE


def render_discovery_report():
    reviews = list(load_json_safe(REVIEW_FILE, {}).values())
    counts = Counter(j.get('state', 'unknown') for j in reviews)
    source_rows = ''.join(f'<tr><td data-label="Source">{escape(name)}</td><td data-label="Latest result">{escape(str(status))}</td></tr>' for name, status in SCRAPER_STATUS.get('source_status', {}).items())
    feed_failures = []
    for provider in ('greenhouse', 'lever', 'ashby', 'smartrecruiters'):
        for board, error in SCRAPER_STATUS.get(provider + '_coverage', {}).get('failures', {}).items():
            feed_failures.append(f'<tr><td data-label="Provider">{escape(provider.title())}</td><td data-label="Employer feed">{escape(str(board))}</td><td data-label="Failure">{escape(str(error))}</td></tr>')
    rows = []
    for job in sorted(reviews, key=lambda j: j.get('checked_at', ''), reverse=True):
        if job.get('state') not in {'unknown', 'needs_check'}: continue
        url = str(job.get('link') or '')
        title = escape(str(job.get('title') or 'Untitled'))
        link = f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{title} ↗</a>' if urlparse(url).scheme in {'http', 'https'} else title
        rows.append(f'<tr><td data-label="Role">{escape(str(job.get("company") or "Unknown"))}<br>{link}</td><td data-label="Source">{escape(str(job.get("source") or "Unknown"))}</td><td data-label="Check result">{escape(str(job.get("reason") or "Awaiting checks"))}</td></tr>')
    trackr = SCRAPER_STATUS.get('trackr_coverage', {})
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Discovery coverage</title>
<style>body{{font:16px system-ui;margin:30px auto;padding:0 20px;max-width:1050px;color:#172033;background:#f6f8fb}}table{{width:100%;border-collapse:collapse;background:white;margin:20px 0}}td,th{{padding:14px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}}a{{color:#17669e}}small{{color:#566}}</style><link rel="stylesheet" href="/assets/pages.css">
<a href="/jobs">← Back to jobs</a><h1>Discovery coverage</h1>
<p>Trackr supplied {trackr.get('fetched', 0)} programmes. {trackr.get('relevant_candidates', 0)} passed the first suitability check; {trackr.get('verified', 0)} were verified from that source during the scan.</p>
<p>{counts['unknown'] + counts['needs_check']} candidates need more checks. {counts['filtered']} were unsuitable and {counts['closed']} were closed. These counts describe checked candidates, including repeats from older scans.</p>
<small>A blocked website or missing job details cannot prove a role is open. Those candidates stay out of the apply list and are checked again. Your logged applications remain in your pipeline.</small>
<h2>Sources</h2><table><tr><th>Source</th><th>Latest result</th></tr>{source_rows}</table>
<h2>Employer feeds needing repair</h2><table><tr><th>Provider</th><th>Employer feed</th><th>Failure</th></tr>{''.join(feed_failures) or '<tr><td colspan="3">No employer-feed failures recorded.</td></tr>'}</table>
<h2>Candidates awaiting verification</h2><table><tr><th>Role</th><th>Source</th><th>What prevented verification</th></tr>{''.join(rows[:150]) or '<tr><td colspan="3">No candidates currently awaiting verification.</td></tr>'}</table></html>'''
