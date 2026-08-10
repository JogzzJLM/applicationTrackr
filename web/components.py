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

    if is_applied:
        status_badge = '<span class="badge badge-applied">✅ Applied & Tracked</span>'
        action_btn = '<span class="ios-btn ios-btn-secondary" style="opacity:0.85; font-weight:600; cursor:default;">✓ In Sheet</span>'
    elif is_reported_closed:
        status_badge = '<span class="badge badge-closed">🛑 Scheme Closed</span>'
        action_btn = f'<a href="/api/reopen-job?id={j_id}" class="ios-btn ios-btn-secondary" style="font-size:12px;" title="Re-open this scheme if it is active again">🔓 Re-Open Scheme</a>'
    else:
        status_badge = '<span class="badge badge-open">⚡ Not Applied</span>'
        action_btn = f'<a href="/api/mark-applied?id={j_id}" class="ios-btn ios-btn-success" style="font-size:12px;" title="Log this application to Google Sheets">+ Log Applied</a>'

    t_low = title_str.lower()
    cat = "software"
    if any(k in t_low for k in ["quant", "trader", "trading", "quant analyst"]):
        cat = "quant"
    elif any(k in t_low for k in ["machine learning", "ml", "ai", "data science", "nlp"]):
        cat = "ai"
    elif any(k in t_low for k in ["cyber", "security", "cloud", "devops"]):
        cat = "cyber"

    sources_list = j.get("sources", [j.get("source", "Discovered Web")])
    if len(sources_list) > 1:
        source_badge_text = "🌐 " + " + ".join(sources_list)
    else:
        source_badge_text = f"🌐 {sources_list[0]}"

    source_url = j.get('source_url') or link_str
    display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    if len(display_url) > 42:
        display_url = display_url[:39] + "..."

    link_js = link_str.replace("'", "\\'").replace('"', '&quot;')
    resp_days_val = float(str(avg_resp).split('-')[0]) if '-' in str(avg_resp) else (float(avg_resp) if str(avg_resp).replace('.','',1).isdigit() else 3.0)
    report_btn_html = f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="ios-btn ios-btn-danger" style="font-size:12px;" title="Report this job as closed/filled to train the AI filter">🚩 Report Closed</button>' if not is_reported_closed else ''

    return f"""
    <div class="job-card" data-search="{comp_str.lower()} {title_str.lower()} {loc_str.lower()} {status_tag} {cat} {source_badge_text.lower()}" data-status="{status_tag}" data-cat="{cat}" data-date="{date_str}" data-match="{match_score}" data-resp="{resp_days_val}" data-company="{comp_str.lower()}" data-title="{title_str.lower()}">
        <div class="job-header">
            <div>
                <span class="company">{comp_str}</span> &nbsp;
                {status_badge}
                <span class="badge badge-active" style="background:rgba(52,199,89,0.12); color:#278a3c; border:0.5px solid rgba(52,199,89,0.3);">🎯 {match_score}% Skill Match</span>
            </div>
            <span class="badge badge-source">{source_badge_text}</span>
        </div>
        <div class="job-title">{title_str}</div>
        <div class="job-meta">📍 {loc_str} &nbsp;&bull;&nbsp; 🕒 Discovered: {date_str} &nbsp;&bull;&nbsp; ⚡ Avg Response: {avg_resp} days</div>
        <div class="job-source-info">
            🔍 <b>Scraped Webpage:</b> <a href="{source_url}" target="_blank" class="source-link">{display_url} ↗</a>
        </div>
        <div class="job-actions">
            <a href="{link_str}" target="_blank" rel="noopener noreferrer" class="ios-btn ios-btn-primary">Apply Direct ↗</a>
            {action_btn}
            <a href="/api/calendar.ics?summary={urllib.parse.quote('Apply: ' + comp_str + ' - ' + title_str)}&desc={urllib.parse.quote('Job Link: ' + link_str)}" class="ios-btn ios-btn-secondary" style="font-size:12px;" title="Add application deadline to Apple Calendar">📅 Apple Cal</a>
            {report_btn_html}
        </div>
    </div>
    """
