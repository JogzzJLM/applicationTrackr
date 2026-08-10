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
        status_badge = '<span class="badge badge-applied">✅ Applied</span>'
        action_btn = '<span class="apple-btn apple-btn-secondary" style="opacity:0.85; font-weight:600; cursor:default;">✓ In Sheet</span>'
    elif is_reported_closed:
        status_badge = '<span class="badge badge-closed">🛑 Closed</span>'
        action_btn = f'<button onclick="reopenJob(\'{j_id}\')" class="apple-btn apple-btn-secondary">🔓 Re-Open Scheme</button>'
    else:
        status_badge = '<span class="badge badge-open">⚡ Active</span>'
        action_btn = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="apple-btn apple-btn-success" title="Log this application to Google Sheets">+ Log Applied</button>'

    t_low = title_str.lower()
    cat = "software"
    if any(k in t_low for k in ["quant", "trader", "trading", "quant analyst"]):
        cat = "quant"
    elif any(k in t_low for k in ["machine learning", "ml", "ai", "data science", "nlp"]):
        cat = "ml"
    elif any(k in t_low for k in ["cyber", "security", "cloud", "devops"]):
        cat = "cyber"

    sources_list = j.get("sources", [j.get("source", "Discovered Web")])
    if len(sources_list) > 1:
        source_badge_text = "🌐 " + " + ".join(sources_list)
    else:
        source_badge_text = f"🌐 {sources_list[0]}"

    source_url = j.get('source_url') or link_str
    display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    if len(display_url) > 38:
        display_url = display_url[:35] + "..."

    link_js = link_str.replace("'", "\\'").replace('"', '&quot;')
    resp_days_val = float(str(avg_resp).split('-')[0]) if '-' in str(avg_resp) else (float(avg_resp) if str(avg_resp).replace('.','',1).isdigit() else 3.0)
    report_btn_html = f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="apple-btn apple-btn-danger" title="Report this job as closed/filled to train the AI filter">🚩 Report Closed</button>' if not is_reported_closed else ''

    # Generate initials avatar
    words = [w for w in comp_str.split() if w[0].isalnum()]
    initials = (words[0][0] + words[1][0]).upper() if len(words) >= 2 else (comp_str[:2].upper() if comp_str else "TC")

    # Match score bar gradient
    bar_width = min(100, max(10, match_score))

    return f"""
    <div class="job-card" data-search="{comp_str.lower()} {title_str.lower()} {loc_str.lower()} {status_tag} {cat} {source_badge_text.lower()}" data-status="{status_tag}" data-cat="{cat}" data-date="{date_str}" data-match="{match_score}" data-resp="{resp_days_val}" data-company="{comp_str.lower()}" data-title="{title_str.lower()}">
        <div class="job-card-header">
            <div style="display:flex; align-items:center; gap:10px;">
                <div class="avatar-circle">{initials}</div>
                <div>
                    <div class="company-title">{comp_str}</div>
                    <div style="display:flex; align-items:center; gap:6px; margin-top:2px;">
                        {status_badge}
                        <span class="badge badge-source">{source_badge_text}</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="job-card-title">{title_str}</div>

        <div class="match-score-row">
            <div class="match-score-pill">🎯 {match_score}% Skill Match</div>
            <div class="match-bar-bg">
                <div class="match-bar-fill" style="width: {bar_width}%;"></div>
            </div>
        </div>

        <div class="job-card-meta">
            <span>📍 {loc_str}</span>
            <span>&bull;</span>
            <span>🕒 {date_str}</span>
            <span>&bull;</span>
            <span>⚡ ~{avg_resp}d response</span>
        </div>

        <div class="job-source-banner">
            🔍 <b>Source:</b> <a href="{source_url}" target="_blank" rel="noopener noreferrer" class="source-link">{display_url} ↗</a>
        </div>

        <div class="job-card-actions">
            <a href="{link_str}" target="_blank" rel="noopener noreferrer" class="apple-btn apple-btn-primary">Apply Direct ↗</a>
            {action_btn}
            <a href="/api/calendar.ics?summary={urllib.parse.quote('Apply: ' + comp_str + ' - ' + title_str)}&desc={urllib.parse.quote('Job Link: ' + link_str)}" class="apple-btn apple-btn-secondary" title="Add application deadline to Apple Calendar">📅 Cal</a>
            {report_btn_html}
        </div>
    </div>
    """
