import urllib.parse
from config import SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from core.storage import (
    load_settings, load_hidden_jobs,
    load_reported_closed_jobs, load_json_safe,
    load_pending_email_updates
)
from core.kb import load_closed_keywords_kb
from core.normalization import (
    normalize_company, normalize_role, extract_program_type,
    clean_company_display_name, deduplicate_job_list
)
from core.scoring import calculate_skill_match_score
from sheets import (
    fetch_google_sheet_csv, parse_sheet_stats, get_detailed_applications,
    get_applied_jobs_set
)
from scrapers_engine.audit import load_discovered_jobs
from web.components import render_job_card

def render_unified_dashboard_html(active_tab="flow"):
    csv_text = fetch_google_sheet_csv()
    stats = parse_sheet_stats(csv_text=csv_text)
    apps = get_detailed_applications(csv_text=csv_text)
    all_jobs = load_discovered_jobs()
    applied_jobs, applied_companies = get_applied_jobs_set(csv_text=csv_text)
    settings = load_settings()
    hidden_jobs = load_hidden_jobs()

    pending_updates = load_pending_email_updates()
    pending_updates_banner_html = ""
    if pending_updates:
        items_html = ""
        for u in pending_updates:
            u_id = u.get("id", "")
            comp = u.get("company", "Company")
            stage = u.get("stage", "Update")
            subj = u.get("subject", "")
            date_rec = u.get("date_received", "")
            options = u.get("options", [])

            opt_btns = ""
            if options:
                for opt in options:
                    opt_role = opt.get("role", "Software/Quant Role")
                    opt_comp = opt.get("company", comp)
                    opt_role_js = opt_role.replace("'", "\\'").replace('"', '&quot;')
                    opt_comp_js = opt_comp.replace("'", "\\'").replace('"', '&quot;')
                    opt_btns += f"""
                    <button onclick="resolvePendingUpdate('{u_id}', '{opt_comp_js}', '{opt_role_js}', '{stage}')" class="btn btn-filled" style="font-size:12px; margin-right:6px; margin-top:6px;">
                        Assign to: {opt_role}
                    </button>
                    """
            else:
                opt_btns += f"""
                <button onclick="openLogModalForPending('{u_id}', '{comp.replace("'", "\\'")}', '{stage}')" class="btn btn-filled" style="font-size:12px; margin-right:6px; margin-top:6px;">
                    + Assign to New Role
                </button>
                """

            opt_btns += f"""
            <button onclick="dismissPendingUpdate('{u_id}')" class="btn btn-ghost" style="font-size:12px; margin-top:6px;">
                Dismiss
            </button>
            """

            items_html += f"""
            <div style="background:#fff; border:1px solid rgba(255,128,0,0.4); border-radius:12px; padding:16px; margin-top:12px;">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <span class="badge badge-papaya">ACTION REQUIRED</span>
                        <strong style="font-size:15px; margin-left:8px; color:var(--text-primary);">{comp}</strong>
                        <span style="color:var(--red); font-weight:800; margin-left:6px;">[{stage}]</span>
                    </div>
                    <span style="font-size:12px; color:var(--text-tertiary);">{date_rec}</span>
                </div>
                <div style="font-size:13px; color:var(--text-secondary); margin-top:6px;">Email Subject: <em>"{subj}"</em></div>
                <div style="font-size:12.5px; font-weight:700; color:var(--text-primary); margin-top:10px;">Which logged application does this status update belong to?</div>
                <div style="margin-top:4px;">{opt_btns}</div>
            </div>
            """

        pending_updates_banner_html = f"""
        <div class="section-card" style="border:2px solid var(--papaya); background:rgba(255,128,0,0.04);">
            <div class="section-title" style="color:var(--papaya);">
                <span>⚠️ Action Required: Email Updates Received ({len(pending_updates)})</span>
            </div>
            <p style="font-size:13.5px; color:var(--text-secondary);">
                Incoming status emails were received for companies with multiple roles (or unlogged roles). Select which role to update on Google Sheets:
            </p>
            {items_html}
        </div>
        """

    total = stats.get("total", 0)
    active = stats.get("active", 0)
    offers = stats.get("offers", 0)
    rejections = stats.get("rejections", 0)
    conv_rate = round((offers / total * 100), 1) if total > 0 else 0.0

    last_run = SCRAPER_STATUS.get("last_run", "Never")

    existing_keys = set((normalize_company(j.get('company')), normalize_role(j.get('title'))) for j in all_jobs)
    for a in apps:
        ac_norm = normalize_company(a.get('company'))
        ar_norm = normalize_role(a.get('role'))
        if ac_norm and (ac_norm, ar_norm) not in existing_keys:
            synthetic_job = {
                "id": f"applied_{ac_norm}_{hash(ar_norm)}",
                "company": clean_company_display_name(a['company']),
                "title": a['role'],
                "location": "UK / Remote",
                "link": "#",
                "source": "Logged Application",
                "date_found": "Active Application"
            }
            all_jobs.insert(0, synthetic_job)
            existing_keys.add((ac_norm, ar_norm))

    auto_hide_company = settings.get("auto_hide_applied_company_jobs", False)

    visible_jobs = []
    for j in all_jobs:
        j_id = j.get('id', '')
        comp_norm = normalize_company(j.get('company'))
        title_norm = normalize_role(j.get('title'))
        title_lower = j.get('title', '').lower()

        if j_id in hidden_jobs or (comp_norm, title_norm) in hidden_jobs:
            continue

        if any(kw.lower() in title_lower for kw in settings.get('exclude_keywords', []) if kw.strip()):
            continue

        is_applied = (comp_norm, title_norm) in applied_jobs
        if not is_applied:
            for (ac, ar) in applied_jobs:
                if ac == comp_norm and ar and (ar in title_norm or title_norm in ar):
                    is_applied = True
                    break

        if auto_hide_company and not is_applied and comp_norm in applied_companies:
            continue

        visible_jobs.append(j)

    visible_jobs = deduplicate_job_list(visible_jobs)

    cnt_applied = 0
    cnt_assessment = 0
    cnt_interview = 0
    cnt_offer = 0
    cnt_rejected = 0

    apps_table_rows = ""
    if not apps:
        apps_table_rows = '<tr><td colspan="5" class="empty-state">No applications logged yet. Click "+ Log Application" to track your first role!</td></tr>'
    else:
        for a in apps:
            st = a.get("status_type", "active")
            latest_stage = a.get("latest_stage", "Applied")
            latest_lower = latest_stage.lower()

            if "offer" in latest_lower:
                cnt_offer += 1
                badge_cls = "badge-green"
            elif "reject" in latest_lower or "fail" in latest_lower:
                cnt_rejected += 1
                badge_cls = "badge-red"
            elif "interview" in latest_lower:
                cnt_interview += 1
                badge_cls = "badge-papaya"
            elif "assessment" in latest_lower or "oa" in latest_lower or "test" in latest_lower:
                cnt_assessment += 1
                badge_cls = "badge-cyan"
            else:
                cnt_applied += 1
                badge_cls = "badge-yellow"

            comp_clean = clean_company_display_name(a['company'])
            comp_js = comp_clean.replace("'", "\\'").replace('"', '&quot;')
            role_js = a['role'].replace("'", "\\'").replace('"', '&quot;')

            action_html = f"""
            <div style="display:flex; gap:4px;">
                <button onclick="quickUpdateStage('{comp_js}', 'Interview', '{role_js}')" class="btn btn-tinted" style="font-size:11px; padding:3px 8px;" title="Promote to Interview">+ Interview</button>
                <button onclick="quickUpdateStage('{comp_js}', 'Rejected', '{role_js}')" class="btn btn-ghost btn-danger-text" style="font-size:11px; padding:3px 8px;" title="Mark Rejected">Reject</button>
            </div>
            """

            apps_table_rows += f"""
            <tr>
                <td class="td-company">{comp_clean}</td>
                <td class="td-role">{a['role']}</td>
                <td><span class="badge {badge_cls}">{latest_stage}</span></td>
                <td><span class="badge {badge_cls}">{a['status']}</span></td>
                <td>{action_html}</td>
            </tr>
            """

    tot_apps = len(apps) if apps else 1
    pct_applied = round((cnt_applied / tot_apps) * 100, 1)
    pct_assessment = round((cnt_assessment / tot_apps) * 100, 1)
    pct_interview = round((cnt_interview / tot_apps) * 100, 1)
    pct_offer = round((cnt_offer / tot_apps) * 100, 1)
    pct_rejected = round((cnt_rejected / tot_apps) * 100, 1)

    applied_count = 0
    not_applied_count = 0
    quant_count = 0
    sw_count = 0
    ml_count = 0
    cyber_count = 0
    closed_count = 0

    grad_count = 0
    intern_count = 0
    placement_count = 0

    reported_closed_map = load_reported_closed_jobs()
    closed_ids = set(reported_closed_map.keys())
    closed_links = set(j.get('link') for j in reported_closed_map.values() if j.get('link'))

    kb_phrases = load_closed_keywords_kb()
    kb_count = len(kb_phrases)
    kb_badges_html = " ".join([f'<span class="kb-tag">{p}</span>' for p in kb_phrases])

    closed_jobs_list = []
    for c_id, c_job in reported_closed_map.items():
        if not any(j.get('id') == c_id for j in visible_jobs):
            c_job_copy = dict(c_job)
            c_job_copy["company"] = clean_company_display_name(c_job.get("company"))
            closed_jobs_list.append(c_job_copy)

    open_count = len([j for j in visible_jobs if j.get('id') not in closed_ids and j.get('link') not in closed_links])
    discovered_count = open_count

    cards_html = ""
    closed_cards_html = ""
    action_items_html = ""
    action_items_count = 0

    for j in visible_jobs:
        j_id = j.get('id', '')
        j_link = j.get('link', '')
        comp_name = clean_company_display_name(j.get('company', 'Unknown'))
        title_name = j.get('title', 'Role')
        comp_norm = normalize_company(comp_name)
        title_norm = normalize_role(title_name)

        is_reported_closed = (j_id in closed_ids) or (j_link in closed_links)

        is_applied = (comp_norm, title_norm) in applied_jobs
        if not is_applied:
            for (ac, ar) in applied_jobs:
                if ac == comp_norm:
                    if not ar or (ar in title_norm or title_norm in ar or (len(set(ar.split()) & set(title_norm.split())) >= 2)):
                        is_applied = True
                        break

        title_lower = title_name.lower()
        prog_type = j.get('program_type') or extract_program_type(title_name)

        if is_reported_closed:
            closed_count += 1
        else:
            if prog_type == "placement":
                placement_count += 1
            elif prog_type == "internship":
                intern_count += 1
            else:
                grad_count += 1

            if any(k in title_lower for k in ["quant", "trader", "trading", "finance", "financial"]):
                quant_count += 1
            elif any(k in title_lower for k in ["software", "developer", "backend", "fullstack", "full-stack", "engineer"]):
                sw_count += 1
            elif any(k in title_lower for k in ["machine learning", "ml", "ai", "data science"]):
                ml_count += 1
            elif any(k in title_lower for k in ["cyber", "security", "cloud", "devops"]):
                cyber_count += 1

            if is_applied:
                applied_count += 1
            else:
                not_applied_count += 1

                deadline_raw = str(j.get('deadline') or j.get('closeDate') or '').lower()
                if deadline_raw and not any(kw in deadline_raw for kw in ['rolling', 'asap', 'none']):
                    if action_items_count < 6:
                        action_items_count += 1
                        comp_js = comp_name.replace("'", "\\'").replace('"', '&quot;')
                        title_js = title_name.replace("'", "\\'").replace('"', '&quot;')
                        action_items_html += f"""
                        <div class="action-item-card">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                                <div style="font-weight:800; font-size:15px; color:var(--text-primary);">{comp_name}</div>
                                <span class="badge badge-papaya">CLOSING SOON</span>
                            </div>
                            <div style="font-size:13.5px; color:var(--cyan); font-weight:700; margin-top:3px;">{title_name}</div>
                            <div style="font-size:12px; color:var(--text-tertiary); margin-top:4px;">Deadline: <strong style="color:var(--papaya); font-family:var(--font-mono);">{j.get('deadline')}</strong></div>
                            <div style="margin-top:12px; display:flex; gap:8px;">
                                <a href="{j_link}" target="_blank" rel="noopener" class="btn btn-filled" style="flex:1; justify-content:center;">Apply Now ↗</a>
                                <button onclick="logJob('{comp_js}', '{title_js}')" class="btn btn-tinted">+ Log</button>
                            </div>
                        </div>
                        """

        card_markup = render_job_card(j, is_reported_closed=is_reported_closed, is_applied=is_applied, is_hidden=(j_id in hidden_jobs))

        if is_reported_closed:
            closed_cards_html += card_markup
        else:
            cards_html += card_markup

    for c_job in closed_jobs_list:
        closed_count += 1
        closed_cards_html += render_job_card(c_job, is_reported_closed=True, is_applied=False, is_hidden=False)

    tab_flow = 'active' if active_tab == 'flow' else ''
    tab_jobs = 'active' if active_tab == 'jobs' else ''
    tab_settings = 'active' if active_tab == 'settings' else ''
    tab_status = 'active' if active_tab == 'diagnostics' else ''
    tab_closed = 'active' if active_tab == 'closed' else ''

    view_flow = 'display:block;' if active_tab == 'flow' else 'display:none;'
    view_jobs = 'display:block;' if active_tab == 'jobs' else 'display:none;'
    view_settings = 'display:block;' if active_tab == 'settings' else 'display:none;'
    view_status = 'display:block;' if active_tab == 'diagnostics' else 'display:none;'
    view_closed = 'display:block;' if active_tab == 'closed' else 'display:none;'

    ex_kw = ", ".join(settings.get("exclude_keywords", []))
    ex_loc = ", ".join(settings.get("exclude_locations", []))
    my_skills_str = ", ".join(settings.get("my_skills", []))
    gh_comp = ", ".join(settings.get("greenhouse_companies", []))
    lev_comp = ", ".join(settings.get("lever_companies", []))
    ash_comp = ", ".join(settings.get("ashby_companies", []))
    sr_comp = ", ".join(settings.get("smartrecruiters_companies", []))
    auto_hide_chk = "checked" if settings.get("auto_hide_applied_company_jobs", False) else ""

    src_status_html = ""
    for s_name, s_msg in SCRAPER_STATUS.get("source_status", {}).items():
        src_status_html += f'<div class="diag-row"><span class="diag-label">{s_name}</span><span class="diag-value">{s_msg}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>ApplicationTrackr // UK Early Career Dashboard</title>
    <meta name="description" content="UK graduate scheme application tracker and job discovery engine">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800;900&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet">

    <style>
        :root {{
            --papaya: #FF8000;
            --papaya-glow: rgba(255, 128, 0, 0.16);
            --papaya-light: rgba(255, 128, 0, 0.08);
            --bg-main: #f5f7fa;
            --bg-card: #ffffff;
            --border-card: rgba(0, 0, 0, 0.09);
            --cyan: #0284c7;
            --yellow: #d97706;
            --green: #16a34a;
            --red: #dc2626;
            --text-primary: #111827;
            --text-secondary: #4b5563;
            --text-tertiary: #9ca3af;
            --radius: 12px;
            --radius-lg: 16px;
            --font: 'Outfit', 'Inter', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}

        body {{
            background: var(--bg-main);
            color: var(--text-primary);
            font-family: var(--font);
            -webkit-font-smoothing: antialiased;
            line-height: 1.45;
        }}

        .shell {{ max-width: 1440px; margin: 0 auto; padding: 0 28px 40px 28px; }}

        /* ─── Clean Topbar ─── */
        .topbar {{
            display: flex; align-items: center; justify-content: space-between;
            padding: 14px 28px;
            border-bottom: 1px solid var(--border-card);
            background: rgba(255, 255, 255, 0.94);
            backdrop-filter: blur(20px);
            position: sticky; top: 0; z-index: 100;
            box-shadow: 0 2px 10px rgba(0,0,0,0.03);
        }}
        .topbar-brand {{
            font-size: 20px; font-weight: 800; color: var(--text-primary);
            letter-spacing: -0.02em; display: flex; align-items: center; gap: 10px;
        }}
        .topbar-brand span {{ color: var(--papaya); font-weight: 900; }}
        .topbar-right {{ display: flex; align-items: center; gap: 10px; }}

        .status-pill {{
            display: inline-flex; align-items: center; gap: 8px;
            font-size: 12px; font-weight: 700; color: var(--green);
            padding: 6px 14px; background: rgba(22, 163, 74, 0.08);
            border-radius: 980px; border: 1px solid rgba(22, 163, 74, 0.2);
        }}
        .status-pill .dot {{
            width: 8px; height: 8px; border-radius: 50%;
            background: var(--green);
        }}

        /* ─── Action Buttons ─── */
        .btn {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 8px 16px; border-radius: 8px;
            font-size: 13px; font-weight: 700; font-family: var(--font);
            cursor: pointer; border: none; text-decoration: none;
            transition: all 0.15s ease;
        }}
        .btn:active {{ transform: scale(0.97); }}
        .btn-filled {{ background: var(--papaya); color: #fff; box-shadow: 0 2px 8px var(--papaya-glow); }}
        .btn-filled:hover {{ background: #e67300; }}
        .btn-tinted {{ background: rgba(2, 132, 199, 0.08); color: var(--cyan); border: 1px solid rgba(2, 132, 199, 0.2); }}
        .btn-tinted:hover {{ background: rgba(2, 132, 199, 0.15); }}
        .btn-ghost {{ background: transparent; color: var(--text-secondary); border: 1px solid var(--border-card); }}
        .btn-ghost:hover {{ background: rgba(0,0,0,0.04); color: var(--text-primary); }}
        .btn-danger-text {{ color: var(--red); border-color: rgba(220, 38, 38, 0.2); }}

        /* ─── Stat Cards Grid ─── */
        .hero-stats-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px; margin: 24px 0 24px 0;
        }}
        @media (max-width: 1100px) {{ .hero-stats-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
        @media (max-width: 600px) {{ .hero-stats-grid {{ grid-template-columns: 1fr; }} }}

        .hero-stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            border-radius: var(--radius-lg);
            padding: 22px 24px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.03);
            display: flex; flex-direction: column; justify-content: space-between;
            position: relative; overflow: hidden;
        }}
        .hero-stat-card::before {{
            content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 4px;
        }}
        .card-papaya::before {{ background: var(--papaya); }}
        .card-cyan::before {{ background: var(--cyan); }}
        .card-green::before {{ background: var(--green); }}
        .card-yellow::before {{ background: var(--yellow); }}

        .stat-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
        .stat-title {{ font-size: 12px; font-weight: 800; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.05em; }}
        .stat-badge {{ font-size: 11px; font-weight: 700; font-family: var(--font-mono); padding: 4px 8px; border-radius: 6px; }}

        .stat-number {{ font-size: 34px; font-weight: 800; font-family: var(--font-mono); color: var(--text-primary); letter-spacing: -0.03em; line-height: 1.1; margin: 6px 0 10px 0; }}
        .stat-footer {{ margin-top: auto; display: flex; flex-direction: column; gap: 8px; }}
        .stat-sub {{ font-size: 12.5px; font-weight: 600; color: var(--text-secondary); }}

        .stat-meter {{
            width: 100%; height: 6px; background: rgba(0,0,0,0.06); border-radius: 100px; overflow: hidden;
        }}
        .stat-meter div {{ height: 100%; border-radius: 100px; }}

        /* ─── Navigation Tabs ─── */
        .tab-bar {{
            display: flex; gap: 8px;
            border-bottom: 1px solid var(--border-card);
            margin-bottom: 24px; overflow-x: auto;
        }}
        .tab {{
            padding: 12px 20px;
            font-size: 14px; font-weight: 700;
            color: var(--text-secondary);
            background: transparent; border: none;
            cursor: pointer; font-family: var(--font);
            border-bottom: 3px solid transparent;
            white-space: nowrap; transition: all 0.15s ease;
        }}
        .tab:hover {{ color: var(--text-primary); }}
        .tab.active {{
            color: var(--papaya);
            border-bottom-color: var(--papaya);
            font-weight: 800;
        }}

        /* ─── Section Cards ─── */
        .section-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            border-radius: var(--radius-lg);
            padding: 24px; margin-bottom: 24px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.03);
        }}
        .section-title {{
            font-size: 17px; font-weight: 800; color: var(--text-primary);
            letter-spacing: -0.01em; margin-bottom: 18px;
            display: flex; align-items: center; justify-content: space-between;
        }}

        /* ─── Grid Split Layout ─── */
        .pipeline-grid {{
            display: grid;
            grid-template-columns: 1.35fr 1fr;
            gap: 20px; align-items: start;
        }}
        @media (max-width: 1080px) {{ .pipeline-grid {{ grid-template-columns: 1fr; }} }}

        .sankey-frame {{
            width: 100%; height: 420px; border: none;
            border-radius: var(--radius); background: transparent;
        }}

        /* ─── Action Items Cards Grid ─── */
        .action-items-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;
        }}
        .action-item-card {{
            background: var(--papaya-light);
            border: 1px solid rgba(255, 128, 0, 0.25);
            border-radius: var(--radius); padding: 18px;
            display: flex; flex-direction: column; justify-content: space-between;
        }}

        /* ─── Toolbar & Chips ─── */
        .toolbar {{ display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }}
        .search-wrap {{ flex: 1; min-width: 240px; position: relative; }}
        .search-wrap svg {{ position: absolute; left: 14px; top: 50%; transform: translateY(-50%); width: 16px; height: 16px; color: var(--text-tertiary); }}
        .search-input {{
            width: 100%; padding: 10px 14px 10px 40px;
            border: 1px solid var(--border-card); border-radius: var(--radius);
            font-size: 14px; font-weight: 500; color: var(--text-primary);
            background: #fff; outline: none; font-family: var(--font);
        }}
        .search-input:focus {{ border-color: var(--papaya); box-shadow: 0 0 10px var(--papaya-glow); }}

        .sort-select {{
            padding: 10px 14px; border: 1px solid var(--border-card);
            border-radius: var(--radius); font-size: 13.5px; font-weight: 600;
            color: var(--text-primary); background: #fff; outline: none; cursor: pointer;
        }}

        .filter-group-label {{
            font-size: 11.5px; font-weight: 800; color: var(--text-tertiary);
            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;
        }}
        .filter-chips-wrap {{ margin-bottom: 24px; display: flex; flex-direction: column; gap: 10px; }}
        .filter-chips {{ display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }}
        .chip {{
            padding: 8px 16px; border-radius: 8px;
            font-size: 13px; font-weight: 700; color: var(--text-secondary);
            background: #fff; border: 1px solid var(--border-card);
            cursor: pointer; white-space: nowrap; font-family: var(--font);
            transition: all 0.15s ease;
        }}
        .chip:hover {{ background: rgba(0,0,0,0.03); color: var(--text-primary); }}
        .chip.active {{
            background: var(--papaya); color: #fff; border-color: var(--papaya);
            box-shadow: 0 2px 8px var(--papaya-glow);
        }}

        /* ─── Job Cards ─── */
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; }}
        .card {{
            background: var(--bg-card); border: 1px solid var(--border-card);
            border-radius: var(--radius); padding: 20px;
            display: flex; flex-direction: column; gap: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
            transition: transform 0.15s ease, border-color 0.15s ease;
        }}
        .card:hover {{ transform: translateY(-2px); border-color: var(--papaya); box-shadow: 0 6px 20px rgba(255, 128, 0, 0.12); }}
        .card-top {{ display: flex; justify-content: space-between; align-items: center; }}
        .card-status {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: var(--text-tertiary); }}
        .status-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
        .dot-green {{ background: var(--green); }}
        .dot-cyan {{ background: var(--cyan); }}
        .dot-red {{ background: var(--red); }}
        .card-match {{ font-size: 12px; font-weight: 800; font-family: var(--font-mono); color: var(--papaya); background: var(--papaya-light); border: 1px solid rgba(255, 128, 0, 0.25); padding: 4px 8px; border-radius: 6px; }}
        .card-company {{ font-size: 17px; font-weight: 800; color: var(--text-primary); letter-spacing: -0.01em; }}
        .card-role {{ font-size: 14.5px; font-weight: 700; color: var(--cyan); }}
        .card-meta {{ font-size: 12.5px; color: var(--text-secondary); font-weight: 500; }}
        .card-source {{ font-size: 12px; color: var(--text-tertiary); }}
        .link-muted {{ color: var(--papaya); text-decoration: none; font-weight: 700; }}
        .card-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}

        /* ─── Badges ─── */
        .badge {{ display: inline-block; padding: 4px 9px; border-radius: 6px; font-size: 11.5px; font-weight: 700; font-family: var(--font-mono); }}
        .badge-papaya {{ background: var(--papaya-light); color: var(--papaya); border: 1px solid rgba(255, 128, 0, 0.3); }}
        .badge-cyan {{ background: rgba(2, 132, 199, 0.08); color: var(--cyan); border: 1px solid rgba(2, 132, 199, 0.25); }}
        .badge-green {{ background: rgba(22, 163, 74, 0.08); color: var(--green); border: 1px solid rgba(22, 163, 74, 0.25); }}
        .badge-red {{ background: rgba(220, 38, 38, 0.08); color: var(--red); border: 1px solid rgba(220, 38, 38, 0.25); }}
        .badge-yellow {{ background: rgba(217, 119, 6, 0.08); color: var(--yellow); border: 1px solid rgba(217, 119, 6, 0.25); }}

        /* ─── Data Table ─── */
        .data-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        .data-table th {{
            text-align: left; padding: 12px 14px; font-size: 12px; font-weight: 800;
            color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.05em;
            border-bottom: 1px solid var(--border-card);
        }}
        .data-table td {{ padding: 12px 14px; border-bottom: 1px solid rgba(0,0,0,0.04); vertical-align: middle; }}
        .td-company {{ font-weight: 800; color: var(--text-primary); }}
        .td-role {{ color: var(--text-secondary); font-weight: 500; }}

        /* ─── Modal ─── */
        .modal-backdrop {{
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(0, 0, 0, 0.5); backdrop-filter: blur(8px);
            z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 20px;
        }}
        .modal-card {{
            background: #ffffff; border: 1px solid var(--border-card); border-radius: 16px; width: 100%; max-width: 480px;
            padding: 26px; box-shadow: 0 20px 40px rgba(0,0,0,0.12);
        }}

        .form-group {{ margin-bottom: 18px; }}
        .form-label {{ font-size: 13px; font-weight: 700; color: var(--text-primary); margin-bottom: 6px; display: block; }}
        .form-input {{
            width: 100%; padding: 10px 14px; border: 1px solid var(--border-card);
            border-radius: 8px; font-size: 14px; font-weight: 500; color: var(--text-primary);
            background: #fff; outline: none; font-family: var(--font);
        }}
        .form-input:focus {{ border-color: var(--papaya); box-shadow: 0 0 10px var(--papaya-glow); }}

        .diag-row {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(0,0,0,0.05); font-size: 14px; }}
        .diag-label {{ font-weight: 700; color: var(--text-primary); }}
        .diag-value {{ color: var(--green); font-weight: 700; font-family: var(--font-mono); font-size: 13px; }}
        .kb-tag {{ display: inline-block; padding: 4px 10px; background: rgba(0,0,0,0.04); border: 1px solid rgba(0,0,0,0.08); border-radius: 6px; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin: 3px; }}
    </style>
</head>
<body>

<!-- Clean Topbar -->
<div class="topbar">
    <div class="topbar-brand">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FF8000" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
        <span>Application</span>Trackr
    </div>
    <div class="topbar-right">
        <div class="status-pill"><span class="dot"></span> Engine Online</div>
        <button onclick="openLogModal()" class="btn btn-filled">+ Log App</button>
        <button onclick="syncSheetAndReload()" class="btn btn-tinted">Sync Sheet</button>
        <a href="/api/rescan" class="btn btn-ghost">Rescan</a>
    </div>
</div>

<div class="shell">

    <!-- Hero Stat Cards Grid -->
    <div class="hero-stats-grid">
        <div class="hero-stat-card card-papaya">
            <div class="stat-header">
                <span class="stat-title">Applications Tracked</span>
                <span class="stat-badge badge-papaya">100% SYNCED</span>
            </div>
            <div class="stat-number">{total}</div>
            <div class="stat-footer">
                <span class="stat-sub">{active} active in pipeline</span>
                <div class="stat-meter"><div style="width: {pct_applied}%; background: var(--papaya);"></div></div>
            </div>
        </div>

        <div class="hero-stat-card card-cyan">
            <div class="stat-header">
                <span class="stat-title">Active Pipeline</span>
                <span class="stat-badge badge-cyan">{cnt_assessment + cnt_interview} IN OA/INT</span>
            </div>
            <div class="stat-number">{active}</div>
            <div class="stat-footer">
                <span class="stat-sub">{cnt_assessment} OA · {cnt_interview} Interviews</span>
                <div class="stat-meter"><div style="width: {pct_assessment + pct_interview}%; background: var(--cyan);"></div></div>
            </div>
        </div>

        <div class="hero-stat-card card-green">
            <div class="stat-header">
                <span class="stat-title">Offers & Conversion</span>
                <span class="stat-badge badge-green">{conv_rate}% CONVERSION</span>
            </div>
            <div class="stat-number">{offers} <span style="font-size:16px; font-weight:700; color:var(--green);">OFFERS</span></div>
            <div class="stat-footer">
                <span class="stat-sub">{cnt_offer} Secured · {cnt_rejected} Rejected</span>
                <div class="stat-meter"><div style="width: {conv_rate}%; background: var(--green);"></div></div>
            </div>
        </div>

        <div class="hero-stat-card card-yellow">
            <div class="stat-header">
                <span class="stat-title">Discovered Market</span>
                <span class="stat-badge badge-yellow">7 SOURCES ACTIVE</span>
            </div>
            <div class="stat-number">{discovered_count} <span style="font-size:16px; font-weight:700; color:var(--yellow);">SCHEMES</span></div>
            <div class="stat-footer">
                <span class="stat-sub">{intern_count} Intern · {grad_count} Grad · {placement_count} Placement</span>
                <div class="stat-meter"><div style="width: 88%; background: var(--yellow);"></div></div>
            </div>
        </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="tab-bar">
        <button class="tab {tab_flow}" data-tab="flow" onclick="switchTab('flow')">Pipeline Flow</button>
        <button class="tab {tab_jobs}" data-tab="jobs" id="tab-jobs-btn" onclick="switchTab('jobs')">Discovered Schemes ({discovered_count})</button>
        <button class="tab {tab_settings}" data-tab="settings" onclick="switchTab('settings')">Filter Settings</button>
        <button class="tab {tab_status}" data-tab="diagnostics" onclick="switchTab('diagnostics')">Diagnostics & Logs</button>
        <button class="tab {tab_closed}" data-tab="closed" onclick="switchTab('closed')">Closed Schemes ({closed_count})</button>
    </div>

    <!-- Panel: Pipeline & Sankey (Side-by-Side Split View) -->
    <div id="view-flow" class="panel" style="{view_flow}">
        {pending_updates_banner_html}

        <div class="pipeline-grid">
            <div class="section-card">
                <div class="section-title">
                    <span>Application Flow Pipeline</span>
                    <button onclick="reloadSankeyIframe()" class="btn btn-tinted" style="font-size:12px;">🔄 Reload Diagram</button>
                </div>
                <iframe src="/sankey-embed" class="sankey-frame" id="sankey-iframe"></iframe>
            </div>

            <div class="section-card">
                <div class="section-title">
                    <span>Logged Applications ({total})</span>
                    <button onclick="openLogModal()" class="btn btn-filled" style="font-size:12px;">+ Log App</button>
                </div>

                <div style="margin-bottom:16px;">
                    <div style="display:flex; justify-content:space-between; font-size:12px; font-weight:700; color:var(--text-tertiary); margin-bottom:6px;">
                        <span>Stage Distribution</span>
                        <span>{cnt_applied} Applied · {cnt_assessment} OA · {cnt_interview} Int · {cnt_offer} Offer</span>
                    </div>
                    <div style="background:rgba(0,0,0,0.06); border-radius:100px; height:8px; display:flex; overflow:hidden;">
                        <div style="width:{pct_applied}%; background:var(--yellow);" title="Applied"></div>
                        <div style="width:{pct_assessment}%; background:var(--cyan);" title="Assessment"></div>
                        <div style="width:{pct_interview}%; background:var(--papaya);" title="Interview"></div>
                        <div style="width:{pct_offer}%; background:var(--green);" title="Offer"></div>
                        <div style="width:{pct_rejected}%; background:var(--red);" title="Rejected"></div>
                    </div>
                </div>

                <div style="overflow-x:auto;">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>Company</th>
                                <th>Role</th>
                                <th>Stage</th>
                                <th>Status</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {apps_table_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        {"<div class='section-card' style='margin-top:10px;'><div class='section-title'>⏰ Schemes Closing Soon (Urgent Action Items)</div><div class='action-items-grid'>" + action_items_html + "</div></div>" if action_items_html else ""}
    </div>

    <!-- Panel: Discovered Schemes -->
    <div id="view-jobs" class="panel" style="{view_jobs}">
        <div class="toolbar">
            <div class="search-wrap">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" /></svg>
                <input type="text" id="job-search" class="search-input" placeholder="Search company, role, or location..." onkeyup="filterJobs()">
            </div>
            <select id="sort-select" class="sort-select" onchange="sortJobs()">
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="match_desc">Best match</option>
                <option value="deadline_asc">Closing soonest</option>
                <option value="deadline_desc">Closing latest</option>
                <option value="company_asc">Company A-Z</option>
                <option value="title_asc">Role A-Z</option>
            </select>
        </div>

        <div class="filter-chips-wrap">
            <div class="filter-group-label">Programme Type (Year Target)</div>
            <div class="filter-chips">
                <button class="chip active" data-prog-chip="all" onclick="filterProgram('all', this)">All Programmes ({discovered_count})</button>
                <button class="chip" data-prog-chip="graduate" onclick="filterProgram('graduate', this)">Graduate Schemes (Yr 3+) ({grad_count})</button>
                <button class="chip" data-prog-chip="internship" onclick="filterProgram('internship', this)">Internships (Yr 2 / Summer) ({intern_count})</button>
                <button class="chip" data-prog-chip="placement" onclick="filterProgram('placement', this)">Industrial Placements (Yr 2 / 12-Mo) ({placement_count})</button>
            </div>

            <div class="filter-group-label" style="margin-top:6px;">Domain Focus & Status</div>
            <div class="filter-chips">
                <button class="chip active" data-dom-chip="all" onclick="filterDomain('all', this)">All Focuses ({discovered_count})</button>
                <button class="chip" data-dom-chip="not_applied" onclick="filterDomain('not_applied', this)">Not Applied ({not_applied_count})</button>
                <button class="chip" data-dom-chip="applied" onclick="filterDomain('applied', this)">Applied ({applied_count})</button>
                <button class="chip" data-dom-chip="quant" onclick="filterDomain('quant', this)">Quant ({quant_count})</button>
                <button class="chip" data-dom-chip="software" onclick="filterDomain('software', this)">Software ({sw_count})</button>
                <button class="chip" data-dom-chip="ml" onclick="filterDomain('ml', this)">ML & AI ({ml_count})</button>
                <button class="chip" data-dom-chip="cyber" onclick="filterDomain('cyber', this)">Cyber ({cyber_count})</button>
            </div>
        </div>

        <div id="jobs-container" class="grid">
            {cards_html}
        </div>
    </div>

    <!-- Panel: Settings -->
    <div id="view-settings" class="panel" style="{view_settings}">
        <div class="section-card">
            <div class="section-title">Filter Configuration</div>
            <form action="/api/settings" method="POST">
                <div class="form-group">
                    <label class="form-label">Skills</label>
                    <input type="text" name="my_skills" class="form-input" value="{my_skills_str}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude keywords</label>
                    <input type="text" name="exclude_keywords" class="form-input" value="{ex_kw}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude locations</label>
                    <input type="text" name="exclude_locations" class="form-input" value="{ex_loc}">
                </div>
                <div class="form-group">
                    <label class="form-label">Greenhouse companies</label>
                    <textarea name="greenhouse_companies" class="form-input" rows="3">{gh_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Lever companies</label>
                    <textarea name="lever_companies" class="form-input" rows="2">{lev_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Ashby companies</label>
                    <textarea name="ashby_companies" class="form-input" rows="2">{ash_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">SmartRecruiters companies</label>
                    <textarea name="smartrecruiters_companies" class="form-input" rows="2">{sr_comp}</textarea>
                </div>
                <div class="form-group">
                    <label style="display:flex; align-items:center; gap:10px; font-size:14px; font-weight:600; cursor:pointer; color:var(--text-primary);">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk} style="width:18px; height:18px; accent-color:var(--papaya);">
                        Hide other listings from applied companies
                    </label>
                </div>
                <button type="submit" class="btn btn-filled">Save Filter Settings</button>
            </form>
        </div>
    </div>

    <!-- Panel: Diagnostics -->
    <div id="view-diagnostics" class="panel" style="{view_status}">
        <div class="section-card">
            <div class="section-title">
                <span><span class="status-pill"><span class="dot"></span> Live Log Stream</span> (docker logs -f applicationtrackr)</span>
                <button onclick="clearLiveLogs()" class="btn btn-ghost btn-danger-text" style="font-size:12px;">Clear Buffer</button>
            </div>
            <div id="live-log-container" style="background:#1e293b; color:#38bdf8; font-family:var(--font-mono); font-size:12.5px; line-height:1.65; height:420px; overflow-y:auto; padding:18px; border-radius:12px; border:1px solid var(--border-card); white-space:pre-wrap; word-break:break-word;">
                Streaming live container output...
            </div>
        </div>

        <div class="section-card">
            <div class="section-title">
                <span>System Status & Source Health</span>
                <span style="font-size:12px; font-weight:700; font-family:var(--font-mono); color:var(--cyan);">AUTO-SYNC LIVE</span>
            </div>
            <div class="diag-row">
                <span class="diag-label">Last scraper run</span>
                <span class="diag-value" id="diag-last-run" style="color:var(--text-primary); font-family:var(--font-mono); font-weight:600;">{last_run}</span>
            </div>
            <div class="diag-row">
                <span class="diag-label">Indexed active schemes</span>
                <span class="diag-value" id="diag-indexed-count" style="color:var(--papaya); font-size:16px;">{discovered_count}</span>
            </div>
            <div style="margin-top:18px; font-size:12px; font-weight:800; color:var(--text-tertiary); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px;">Live Source Connectors (7 Sources)</div>
            <div id="diag-source-status">
                {src_status_html}
            </div>
        </div>

        <div class="section-card">
            <div class="section-title">
                <span id="diag-kb-title">Knowledge Base ({kb_count} rules)</span>
                <span style="font-size:12px; font-weight:600; font-family:var(--font-mono); color:var(--text-tertiary);">AI CLOSURE RULES</span>
            </div>
            <div id="diag-kb-container" style="max-height:220px; overflow-y:auto;">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- Panel: Closed -->
    <div id="view-closed" class="panel" style="{view_closed}">
        <div class="section-card">
            <div class="section-title">Closed Schemes ({closed_count})</div>
            <p style="font-size:14px; color:var(--text-tertiary); margin-bottom:20px;">
                Schemes marked as closed or filled. Re-open any scheme if it becomes available again.
            </p>
            <div class="grid">
                {closed_cards_html}
            </div>
        </div>
    </div>

</div>

<!-- Modal: Log New Application -->
<div id="log-modal" class="modal-backdrop" style="display:none;">
    <div class="modal-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:18px;">
            <div style="font-size:18px; font-weight:800; color:var(--text-primary);">Log App to Google Sheets</div>
            <button onclick="closeLogModal()" class="btn btn-ghost">✕</button>
        </div>
        <div class="form-group">
            <label class="form-label">Company Name</label>
            <input type="text" id="modal-company" class="form-input" placeholder="e.g. Marshall Wace, Palantir">
        </div>
        <div class="form-group">
            <label class="form-label">Role Title</label>
            <input type="text" id="modal-role" class="form-input" placeholder="e.g. Software Engineering Intern 2027" value="Software Engineering Intern">
        </div>
        <div class="form-group">
            <label class="form-label">Application Stage</label>
            <select id="modal-stage" class="form-input">
                <option value="Applied">Applied</option>
                <option value="Online Assessment">Online Assessment (OA)</option>
                <option value="Interview 1">Interview 1</option>
                <option value="Interview 2">Interview 2 / Final</option>
                <option value="Offer">Offer 🎉</option>
                <option value="Rejected">Rejected</option>
            </select>
        </div>
        <div style="display:flex; gap:10px; margin-top:24px;">
            <button onclick="submitModalLog()" class="btn btn-filled" style="flex:1; justify-content:center;">Save to Google Sheets</button>
            <button onclick="closeLogModal()" class="btn btn-ghost">Cancel</button>
        </div>
    </div>
</div>

<script>
    var logInterval = null;
    var statusInterval = null;
    var kbInterval = null;

    function openLogModal() {{
        document.getElementById('log-modal').style.display = 'flex';
        document.getElementById('modal-company').focus();
    }}

    function openLogModalForPending(pendingId, comp, stage) {{
        document.getElementById('log-modal').style.display = 'flex';
        document.getElementById('modal-company').value = comp;
        document.getElementById('modal-stage').value = stage || 'Applied';
        document.getElementById('modal-role').focus();
    }}

    function closeLogModal() {{
        document.getElementById('log-modal').style.display = 'none';
    }}

    function reloadSankeyIframe() {{
        var iframe = document.getElementById('sankey-iframe');
        if (iframe) {{
            iframe.src = '/sankey-embed?t=' + Date.now();
        }}
    }}

    function syncSheetAndReload() {{
        fetch('/api/sync-sheet')
            .then(() => {{
                reloadSankeyIframe();
                location.reload();
            }});
    }}

    function submitModalLog() {{
        var comp = document.getElementById('modal-company').value.trim();
        var title = document.getElementById('modal-role').value.trim();
        var stage = document.getElementById('modal-stage').value;
        if (!comp) {{
            alert('Please enter a company name.');
            return;
        }}
        logJobWithStage(comp, title, stage);
    }}

    function quickUpdateStage(comp, stage, role) {{
        logJobWithStage(comp, role || 'Software/Quant Role', stage);
    }}

    function resolvePendingUpdate(id, comp, role, stage) {{
        fetch('/api/resolve-pending-update?id=' + encodeURIComponent(id) + '&company=' + encodeURIComponent(comp) + '&role=' + encodeURIComponent(role) + '&stage=' + encodeURIComponent(stage))
            .then(r => r.json())
            .then(() => {{
                reloadSankeyIframe();
                location.reload();
            }});
    }}

    function dismissPendingUpdate(id) {{
        fetch('/api/dismiss-pending-update?id=' + encodeURIComponent(id))
            .then(r => r.json())
            .then(() => location.reload());
    }}

    function logJobWithStage(comp, title, stage) {{
        fetch('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title) + '&stage=' + encodeURIComponent(stage || 'Applied'))
            .then(r => r.json())
            .then(() => {{
                reloadSankeyIframe();
                location.reload();
            }});
    }}

    function fetchLiveLogs() {{
        fetch('/api/logs')
            .then(r => r.json())
            .then(data => {{
                var container = document.getElementById('live-log-container');
                if (container && data.logs) {{
                    if (data.logs.length === 0) {{
                        container.innerHTML = '<span style="color:#94a3b8;">No terminal output recorded yet. Trigger a Rescan or Sheet Sync to stream live output.</span>';
                    }} else {{
                        var isAtBottom = (container.scrollHeight - container.scrollTop <= container.clientHeight + 60);
                        container.innerHTML = data.logs.map(l => '<div>' + escapeHtml(l) + '</div>').join('');
                        if (isAtBottom) {{
                            container.scrollTop = container.scrollHeight;
                        }}
                    }}
                }}
            }})
            .catch(() => {{}});
    }}

    function fetchSystemStatus() {{
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {{
                var elLastRun = document.getElementById('diag-last-run');
                if (elLastRun && data.last_run) elLastRun.innerText = data.last_run;

                var elIndexed = document.getElementById('diag-indexed-count');
                if (elIndexed && data.total_discovered_jobs !== undefined) elIndexed.innerText = data.total_discovered_jobs;

                var elSources = document.getElementById('diag-source-status');
                if (elSources && data.source_status) {{
                    var html = '';
                    for (var src in data.source_status) {{
                        html += '<div class="diag-row"><span class="diag-label">' + escapeHtml(src) + '</span><span class="diag-value">' + escapeHtml(data.source_status[src]) + '</span></div>';
                    }}
                    elSources.innerHTML = html;
                }}
            }})
            .catch(() => {{}});
    }}

    function fetchKBStatus() {{
        fetch('/api/kb-status')
            .then(r => r.json())
            .then(data => {{
                var elTitle = document.getElementById('diag-kb-title');
                if (elTitle && data.count !== undefined) elTitle.innerText = 'Knowledge Base (' + data.count + ' rules)';

                var elContainer = document.getElementById('diag-kb-container');
                if (elContainer && data.phrases) {{
                    elContainer.innerHTML = data.phrases.map(p => '<span class="kb-tag">' + escapeHtml(p) + '</span>').join(' ');
                }}
            }})
            .catch(() => {{}});
    }}

    function startLivePolling() {{
        fetchLiveLogs();
        fetchSystemStatus();
        fetchKBStatus();

        if (!logInterval) logInterval = setInterval(fetchLiveLogs, 2000);
        if (!statusInterval) statusInterval = setInterval(fetchSystemStatus, 3000);
        if (!kbInterval) kbInterval = setInterval(fetchKBStatus, 5000);
    }}

    function stopLivePolling() {{
        if (logInterval) {{ clearInterval(logInterval); logInterval = null; }}
        if (statusInterval) {{ clearInterval(statusInterval); statusInterval = null; }}
        if (kbInterval) {{ clearInterval(kbInterval); kbInterval = null; }}
    }}

    function switchTab(tabId) {{
        document.querySelectorAll('.panel').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));

        var targetView = document.getElementById('view-' + tabId);
        if (targetView) targetView.style.display = 'block';

        var activeBtn = document.querySelector('.tab[data-tab="' + tabId + '"]');
        if (activeBtn) activeBtn.classList.add('active');

        if (tabId === 'diagnostics') {{
            startLivePolling();
        }} else {{
            stopLivePolling();
        }}
    }}

    var currentProgramPill = 'all';
    var currentDomainPill = 'all';

    function filterProgram(prog, btn) {{
        currentProgramPill = prog;
        btn.parentElement.querySelectorAll('.chip').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        filterJobs();
    }}

    function filterDomain(dom, btn) {{
        currentDomainPill = dom;
        btn.parentElement.querySelectorAll('.chip').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        filterJobs();
    }}

    function filterJobs() {{
        var q = document.getElementById('job-search').value.toLowerCase().trim();
        var cards = document.querySelectorAll('#jobs-container .card');

        var visibleCount = 0;

        var progCounts = {{ all: 0, graduate: 0, internship: 0, placement: 0 }};
        var domainCounts = {{ all: 0, not_applied: 0, applied: 0, quant: 0, software: 0, ml: 0, cyber: 0 }};

        cards.forEach(c => {{
            var searchData = (c.getAttribute('data-search') || '').toLowerCase();
            var statusData = c.getAttribute('data-status') || '';
            var catData = c.getAttribute('data-cat') || '';
            var progData = c.getAttribute('data-program') || 'graduate';

            var matchesSearch = !q || searchData.includes(q);
            var matchesProgram = (currentProgramPill === 'all') || (progData === currentProgramPill);

            var matchesDomain = true;
            if (currentDomainPill === 'not_applied') matchesDomain = (statusData === 'not_applied');
            else if (currentDomainPill === 'applied') matchesDomain = (statusData === 'applied');
            else if (currentDomainPill !== 'all') matchesDomain = (catData === currentDomainPill);

            var isVisible = matchesSearch && matchesProgram && matchesDomain;
            c.style.display = isVisible ? 'flex' : 'none';
            if (isVisible) visibleCount++;

            if (matchesSearch && matchesDomain) {{
                progCounts.all++;
                if (progData in progCounts) progCounts[progData]++;
            }}

            if (matchesSearch && matchesProgram) {{
                domainCounts.all++;
                if (statusData in domainCounts) domainCounts[statusData]++;
                if (catData in domainCounts) domainCounts[catData]++;
            }}
        }});

        updateChipText('data-prog-chip="all"', 'All Programmes (' + progCounts.all + ')');
        updateChipText('data-prog-chip="graduate"', 'Graduate Schemes (Yr 3+) (' + progCounts.graduate + ')');
        updateChipText('data-prog-chip="internship"', 'Internships (Yr 2 / Summer) (' + progCounts.internship + ')');
        updateChipText('data-prog-chip="placement"', 'Industrial Placements (Yr 2 / 12-Mo) (' + progCounts.placement + ')');

        updateChipText('data-dom-chip="all"', 'All Focuses (' + domainCounts.all + ')');
        updateChipText('data-dom-chip="not_applied"', 'Not Applied (' + domainCounts.not_applied + ')');
        updateChipText('data-dom-chip="applied"', 'Applied (' + domainCounts.applied + ')');
        updateChipText('data-dom-chip="quant"', 'Quant (' + domainCounts.quant + ')');
        updateChipText('data-dom-chip="software"', 'Software (' + domainCounts.software + ')');
        updateChipText('data-dom-chip="ml"', 'ML & AI (' + domainCounts.ml + ')');
        updateChipText('data-dom-chip="cyber"', 'Cyber (' + domainCounts.cyber + ')');

        var tabBtn = document.getElementById('tab-jobs-btn');
        if (tabBtn) {{
            tabBtn.innerText = 'Discovered Schemes (' + visibleCount + ')';
        }}
    }}

    function updateChipText(attr, text) {{
        var el = document.querySelector('.chip[' + attr + ']');
        if (el) el.innerText = text;
    }}

    function sortJobs() {{
        var mode = document.getElementById('sort-select').value;
        var container = document.getElementById('jobs-container');
        var cards = Array.from(container.children);

        cards.sort((a, b) => {{
            if (mode === 'newest') return (b.getAttribute('data-date') || '').localeCompare(a.getAttribute('data-date') || '');
            if (mode === 'oldest') return (a.getAttribute('data-date') || '').localeCompare(a.getAttribute('data-date') || '');
            if (mode === 'match_desc') return (parseFloat(b.getAttribute('data-match')) || 0) - (parseFloat(a.getAttribute('data-match')) || 0);
            if (mode === 'deadline_asc') return (a.getAttribute('data-deadline') || 'z').localeCompare(b.getAttribute('data-deadline') || 'z');
            if (mode === 'deadline_desc') return (b.getAttribute('data-deadline') || 'a').localeCompare(a.getAttribute('data-deadline') || 'a');
            if (mode === 'company_asc') return (a.getAttribute('data-company') || '').localeCompare(b.getAttribute('data-company') || '');
            if (mode === 'title_asc') return (a.getAttribute('data-title') || '').localeCompare(b.getAttribute('data-title') || '');
            return 0;
        }});

        cards.forEach(card => container.appendChild(card));
    }}

    function escapeHtml(str) {{
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }}

    function clearLiveLogs() {{
        fetch('/api/clear-logs')
            .then(r => r.json())
            .then(() => fetchLiveLogs());
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        filterJobs();
        if ('{active_tab}' === 'diagnostics') {{
            startLivePolling();
        }}
    }});

    document.addEventListener('keydown', function(e) {{
        if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {{
            e.preventDefault();
            var searchInput = document.getElementById('job-search');
            if (searchInput) searchInput.focus();
        }}
    }});

    function reportClosedJob(jobId, link) {{
        if (confirm('Report this scheme as closed?')) {{
            fetch('/api/report-closed?id=' + encodeURIComponent(jobId) + '&link=' + encodeURIComponent(link))
                .then(r => r.json())
                .then(() => location.reload());
        }}
    }}

    function reopenJob(jobId) {{
        fetch('/api/reopen-job?id=' + encodeURIComponent(jobId))
            .then(r => r.json())
            .then(() => location.reload());
    }}

    function logJob(comp, title) {{
        fetch('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title))
            .then(r => r.json())
            .then(() => {{
                alert('Marked as applied.');
                location.reload();
            }});
    }}
</script>

</body>
</html>
"""
    return html
