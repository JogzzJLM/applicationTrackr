import html
import urllib.parse

from core.normalization import clean_company_display_name, extract_program_type
from core.scoring import calculate_skill_match_score


def render_job_card(j, is_reported_closed=False, is_applied=False, is_hidden=False, company_resp_map=None):
    j_id = j.get("id", "")
    comp = clean_company_display_name(j.get("company", "Unknown"))
    title = j.get("title", "Role")
    location = j.get("location", "Unknown")
    link = j.get("link", "#")
    date_found = j.get("date_found", "Recently")
    metadata = j.get("metadata") if isinstance(j.get("metadata"), dict) else {}
    score = j.get("match_score")
    if score is None:
        score = calculate_skill_match_score(title, comp, location, metadata=metadata)
    tier = j.get("match_tier", "")
    tier_label = {"strong": "STRONG FIT", "good": "GOOD FIT", "borderline": "BORDERLINE"}.get(tier, "FIT")

    program = j.get("program_type") or extract_program_type(title)
    if program == "placement":
        badge = '<span class="badge badge-cyan">Placement (Yr 2 / 12-Mo)</span>'
    elif program == "internship":
        badge = '<span class="badge badge-papaya">Internship (Yr 2 / Summer)</span>'
    else:
        badge = '<span class="badge badge-yellow">Graduate Scheme (Yr 3+)</span>'

    deadline = j.get("deadline") or j.get("closeDate") or j.get("closing_date") or "Rolling / ASAP"
    status_tag = "applied" if is_applied else ("closed" if is_reported_closed else "not_applied")
    comp_js = comp.replace("'", "\\'").replace('"', '&quot;')
    title_js = title.replace("'", "\\'").replace('"', '&quot;')

    if is_applied:
        status_dot, status_label = '<span class="status-dot dot-cyan"></span>', "Applied"
        action = '<span class="btn btn-ghost" style="cursor:default;color:var(--cyan);">In Sheet</span>'
    elif is_reported_closed:
        status_dot, status_label = '<span class="status-dot dot-red"></span>', "Closed"
        action = f'<button onclick="reopenJob(\'{j_id}\')" class="btn btn-ghost">Re-open</button>'
    else:
        status_dot, status_label = '<span class="status-dot dot-green"></span>', "Open"
        action = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="btn btn-tinted">+ Log App</button>'

    category = j.get("category", "software")
    cat_filter = "ml" if category == "ai_ml" else category
    sources = j.get("sources", [j.get("source", "Unknown")])
    source_text = " + ".join(sources) if len(sources) > 1 else sources[0]
    source_url = j.get("source_url") or link
    display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    if len(display_url) > 36:
        display_url = display_url[:33] + "..."

    link_js = link.replace("'", "\\'").replace('"', '&quot;')
    report = "" if is_reported_closed else f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="btn btn-ghost btn-danger-text">Report Closed</button>'
    reasons = j.get("match_reasons", [])
    reason_html = ""
    if reasons:
        reason_html = '<div style="font-size:11.5px;color:var(--text-secondary);line-height:1.5;">' + " · ".join(html.escape(str(r)) for r in reasons[:3]) + "</div>"
    agent = "" if is_reported_closed or link == "#" else f'<a href="/autoapply?job_id={urllib.parse.quote(str(j_id))}" class="btn btn-ghost">Apply Agent</a>'

    return f'''<div class="card" data-search="{html.escape(comp.lower())} {html.escape(title.lower())} {html.escape(location.lower())} {status_tag} {cat_filter} {program} {html.escape(source_text.lower())}" data-status="{status_tag}" data-cat="{cat_filter}" data-program="{program}" data-date="{date_found}" data-deadline="{str(deadline).lower()}" data-match="{score}" data-company="{html.escape(comp.lower())}" data-title="{html.escape(title.lower())}">
<div class="card-top"><div class="card-status">{status_dot} {status_label} &nbsp;·&nbsp; {badge}</div><div class="card-match">{score}% {tier_label}</div></div>
<div class="card-company">{html.escape(comp)}</div><div class="card-role">{html.escape(title)}</div>
<div class="card-meta">{html.escape(location)} · Discovered: {html.escape(str(date_found))} · <span style="color:var(--papaya);font-weight:700;">Deadline: {html.escape(str(deadline))}</span></div>
{reason_html}<div class="card-source"><a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener" class="link-muted">{html.escape(display_url)}</a> · <span style="color:var(--cyan);">{html.escape(source_text)}</span></div>
<div class="card-actions"><a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener noreferrer" class="btn btn-filled">Apply ↗</a>{agent}{action}{report}</div></div>'''
