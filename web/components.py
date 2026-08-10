import urllib.parse
from core.scoring import calculate_skill_match_score

def render_job_card(j, is_reported_closed=False, is_applied=False, is_hidden=False, company_resp_map=None):
    j_id = j.get('id', '')
    comp_str = j.get('company', 'Unknown')
    title_str = j.get('title', 'Role')
    loc_str = j.get('location', 'UK')
    link_str = j.get('link', '#')
    date_str = j.get('date_found', 'Recently')

    match_score = j.get('match_score') or calculate_skill_match_score(title_str, comp_str, loc_str)

    avg_resp = "3"
    if company_resp_map and comp_str.lower() in company_resp_map:
        avg_resp = str(company_resp_map[comp_str.lower()])

    status_tag = "applied" if is_applied else ("closed" if is_reported_closed else "not_applied")

    comp_js = comp_str.replace("'", "\\'").replace('"', '&quot;')
    title_js = title_str.replace("'", "\\'").replace('"', '&quot;')

    if is_applied:
        status_badge = '<span class="ln-badge ln-badge-applied">✅ APPLIED</span>'
        action_btn = '<span class="ios-btn ios-btn-secondary" style="opacity:0.85; cursor:default;">✓ IN SHEET</span>'
    elif is_reported_closed:
        status_badge = '<span class="ln-badge ln-badge-closed">🛑 CLOSED</span>'
        action_btn = f'<button onclick="reopenJob(\'{j_id}\')" class="ios-btn ios-btn-secondary">🔓 RE-OPEN</button>'
    else:
        status_badge = '<span class="ln-badge ln-badge-open">⚡ OPEN</span>'
        action_btn = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="ios-btn ios-btn-success" title="Log this application to Google Sheets">+ LOG APPLIED</button>'

    t_low = title_str.lower()
    cat = "software"
    if any(k in t_low for k in ["quant", "trader", "trading", "quant analyst"]):
        cat = "quant"
    elif any(k in t_low for k in ["machine learning", "ml", "ai", "data science", "nlp"]):
        cat = "ml"
    elif any(k in t_low for k in ["cyber", "security", "cloud", "devops"]):
        cat = "cyber"

    sources_list = j.get("sources", [j.get("source", "Discovered Web")])
    source_badge_text = " • ".join(sources_list)

    source_url = j.get('source_url') or link_str
    display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    if len(display_url) > 38:
        display_url = display_url[:35] + "..."

    link_js = link_str.replace("'", "\\'").replace('"', '&quot;')
    resp_days_val = float(str(avg_resp).split('-')[0]) if '-' in str(avg_resp) else (float(avg_resp) if str(avg_resp).replace('.','',1).isdigit() else 3.0)
    report_btn_html = f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="ios-btn ios-btn-danger" title="Report closed to train AI filter">🚩 REPORT CLOSED</button>' if not is_reported_closed else ''

    # Highlight LN yellow for high skill match (>= 80)
    match_cls = "ln-match-high" if match_score >= 80 else "ln-match-norm"

    return f"""
    <div class="job-card" data-search="{comp_str.lower()} {title_str.lower()} {loc_str.lower()} {status_tag} {cat} {source_badge_text.lower()}" data-status="{status_tag}" data-cat="{cat}" data-date="{date_str}" data-match="{match_score}" data-resp="{resp_days_val}" data-company="{comp_str.lower()}" data-title="{title_str.lower()}">
        <div class="job-card-header">
            <div class="company-badge-wrap">
                <span class="company">{comp_str}</span>
                {status_badge}
            </div>
            <span class="ln-source-tag">⚡ {source_badge_text}</span>
        </div>

        <div class="job-title">{title_str}</div>

        <div class="telemetry-bar">
            <span class="ln-match-badge {match_cls}">🎯 {match_score}% MATCH</span>
            <span class="telemetry-item">📍 {loc_str}</span>
            <span class="telemetry-item">⏱️ {avg_resp}d RESP</span>
        </div>

        <div class="job-meta-row">
            <span>🕒 DISCOVERED: <b>{date_str}</b></span>
        </div>

        <div class="job-source-info">
            <span style="color:var(--text-muted);">SOURCE EGRESS:</span> <a href="{source_url}" target="_blank" class="source-link">{display_url} ↗</a>
        </div>

        <div class="job-actions">
            <a href="{link_str}" target="_blank" rel="noopener noreferrer" class="ios-btn ios-btn-primary">APPLY DIRECT ↗</a>
            {action_btn}
            <a href="/api/calendar.ics?summary={urllib.parse.quote('Apply: ' + comp_str + ' - ' + title_str)}&desc={urllib.parse.quote('Job Link: ' + link_str)}" class="ios-btn ios-btn-secondary" title="Add application deadline to Apple Calendar">📅 CALENDAR</a>
            {report_btn_html}
        </div>
    </div>
    """
