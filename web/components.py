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
        status_dot = '<span class="status-dot dot-blue"></span>'
        status_label = 'Applied'
        action_btn = '<span class="btn btn-ghost" style="cursor:default;">In Sheet</span>'
    elif is_reported_closed:
        status_dot = '<span class="status-dot dot-red"></span>'
        status_label = 'Closed'
        action_btn = f'<button onclick="reopenJob(\'{j_id}\')" class="btn btn-ghost">Re-open</button>'
    else:
        status_dot = '<span class="status-dot dot-green"></span>'
        status_label = 'Open'
        action_btn = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="btn btn-tinted">Log Applied</button>'

    t_low = title_str.lower()
    cat = "software"
    if any(k in t_low for k in ["quant", "trader", "trading", "quant analyst"]):
        cat = "quant"
    elif any(k in t_low for k in ["machine learning", "ml", "ai", "data science", "nlp"]):
        cat = "ml"
    elif any(k in t_low for k in ["cyber", "security", "cloud", "devops"]):
        cat = "cyber"

    sources_list = j.get("sources", [j.get("source", "Unknown")])
    source_text = " + ".join(sources_list) if len(sources_list) > 1 else sources_list[0]

    source_url = j.get('source_url') or link_str
    display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    if len(display_url) > 40:
        display_url = display_url[:37] + "..."

    link_js = link_str.replace("'", "\\'").replace('"', '&quot;')
    resp_days_val = float(str(avg_resp).split('-')[0]) if '-' in str(avg_resp) else (float(avg_resp) if str(avg_resp).replace('.','',1).isdigit() else 3.0)
    report_btn_html = f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="btn btn-ghost btn-danger-text">Report Closed</button>' if not is_reported_closed else ''

    return f"""
    <div class="card" data-search="{comp_str.lower()} {title_str.lower()} {loc_str.lower()} {status_tag} {cat} {source_text.lower()}" data-status="{status_tag}" data-cat="{cat}" data-date="{date_str}" data-match="{match_score}" data-resp="{resp_days_val}" data-company="{comp_str.lower()}" data-title="{title_str.lower()}">
        <div class="card-top">
            <div class="card-status">{status_dot} {status_label}</div>
            <div class="card-match">{match_score}%</div>
        </div>
        <div class="card-company">{comp_str}</div>
        <div class="card-role">{title_str}</div>
        <div class="card-meta">{loc_str}  ·  {date_str}  ·  ~{avg_resp}d response</div>
        <div class="card-source"><a href="{source_url}" target="_blank" rel="noopener" class="link-muted">{display_url}</a> · {source_text}</div>
        <div class="card-actions">
            <a href="{link_str}" target="_blank" rel="noopener noreferrer" class="btn btn-filled">Apply</a>
            {action_btn}
            <a href="/api/calendar.ics?summary={urllib.parse.quote('Apply: ' + comp_str + ' - ' + title_str)}&desc={urllib.parse.quote('Job Link: ' + link_str)}" class="btn btn-ghost">Add to Cal</a>
            {report_btn_html}
        </div>
    </div>
    """
