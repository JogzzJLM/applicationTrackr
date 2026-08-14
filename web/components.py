import urllib.parse
from core.scoring import calculate_skill_match_score
from core.normalization import clean_company_display_name, extract_program_type

def render_job_card(j, is_reported_closed=False, is_applied=False, is_hidden=False, company_resp_map=None):
    j_id = j.get('id', '')
    raw_comp = j.get('company', 'Unknown')
    comp_str = clean_company_display_name(raw_comp)
    title_str = j.get('title', 'Role')
    loc_str = j.get('location', 'UK')
    link_str = j.get('link', '#')
    date_str = j.get('date_found', 'Recently')

    match_score = j.get('match_score') or calculate_skill_match_score(title_str, comp_str, loc_str)

    prog_type = j.get('program_type') or extract_program_type(title_str)
    if prog_type == "placement":
        prog_badge = '<span class="badge badge-cyan">Placement (Yr 2 / 12-Mo)</span>'
    elif prog_type == "internship":
        prog_badge = '<span class="badge badge-papaya">Internship (Yr 2 / Summer)</span>'
    else:
        prog_badge = '<span class="badge badge-yellow">Graduate Scheme (Yr 3+)</span>'

    deadline_str = j.get('deadline') or j.get('closeDate') or j.get('closing_date') or 'Rolling / ASAP'

    status_tag = "applied" if is_applied else ("closed" if is_reported_closed else "not_applied")

    comp_js = comp_str.replace("'", "\\'").replace('"', '&quot;')
    title_js = title_str.replace("'", "\\'").replace('"', '&quot;')

    if is_applied:
        status_dot = '<span class="status-dot dot-cyan"></span>'
        status_label = 'Applied'
        action_btn = '<span class="btn btn-ghost" style="cursor:default; color:var(--cyan);">In Sheet</span>'
    elif is_reported_closed:
        status_dot = '<span class="status-dot dot-red"></span>'
        status_label = 'Closed'
        action_btn = f'<button onclick="reopenJob(\'{j_id}\')" class="btn btn-ghost">Re-open</button>'
    else:
        status_dot = '<span class="status-dot dot-green"></span>'
        status_label = 'Open'
        action_btn = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="btn btn-tinted">+ Log App</button>'

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
    if len(display_url) > 36:
        display_url = display_url[:33] + "..."

    link_js = link_str.replace("'", "\\'").replace('"', '&quot;')
    report_btn_html = f'<button onclick="reportClosedJob(\'{j_id}\', \'{link_js}\')" class="btn btn-ghost btn-danger-text">Report Closed</button>' if not is_reported_closed else ''

    return f"""
    <div class="card" data-search="{comp_str.lower()} {title_str.lower()} {loc_str.lower()} {status_tag} {cat} {prog_type} {source_text.lower()}" data-status="{status_tag}" data-cat="{cat}" data-program="{prog_type}" data-date="{date_str}" data-deadline="{deadline_str.lower()}" data-match="{match_score}" data-company="{comp_str.lower()}" data-title="{title_str.lower()}">
        <div class="card-top">
            <div class="card-status">{status_dot} {status_label} &nbsp;·&nbsp; {prog_badge}</div>
            <div class="card-match">{match_score}% MATCH</div>
        </div>
        <div class="card-company">{comp_str}</div>
        <div class="card-role">{title_str}</div>
        <div class="card-meta">{loc_str}  ·  Discovered: {date_str}  ·  <span style="color:var(--papaya); font-weight:700;">Deadline: {deadline_str}</span></div>
        <div class="card-source"><a href="{source_url}" target="_blank" rel="noopener" class="link-muted">{display_url}</a> · <span style="color:var(--cyan);">{source_text}</span></div>
        <div class="card-actions">
            <a href="{link_str}" target="_blank" rel="noopener noreferrer" class="btn btn-filled">Apply ↗</a>
            {action_btn}
            {report_btn_html}
        </div>
    </div>
    """
