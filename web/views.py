import urllib.parse
from config import SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from core.storage import (
    load_settings, load_hidden_jobs,
    load_reported_closed_jobs, load_json_safe
)
from core.kb import load_closed_keywords_kb
from core.normalization import normalize_company, normalize_role, extract_program_type, clean_company_display_name
from core.scoring import calculate_skill_match_score
from sheets import (
    parse_sheet_stats, get_detailed_applications,
    get_applied_jobs_set
)
from scrapers_engine.audit import load_discovered_jobs
from web.components import render_job_card

def render_unified_dashboard_html(active_tab="flow"):
    stats = parse_sheet_stats()
    apps = get_detailed_applications()
    all_jobs = load_discovered_jobs()
    applied_jobs, applied_companies = get_applied_jobs_set()
    settings = load_settings()
    hidden_jobs = load_hidden_jobs()

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

    apps_table_rows = ""
    if not apps:
        apps_table_rows = '<tr><td colspan="4" class="empty-state">No applications logged yet.</td></tr>'
    else:
        for a in apps:
            st = a.get("status_type", "active")
            if st == "offer":
                badge_cls = "badge-green"
            elif st == "rejected":
                badge_cls = "badge-red"
            elif st == "ghosted":
                badge_cls = "badge-gray"
            else:
                badge_cls = "badge-blue"

            pipeline_str = " → ".join(a.get("stages", [])) if a.get("stages") else a.get("latest_stage", "Applied")

            apps_table_rows += f"""
            <tr>
                <td class="td-company">{clean_company_display_name(a['company'])}</td>
                <td class="td-role">{a['role']}</td>
                <td><span class="badge {badge_cls}">{a['latest_stage']}</span></td>
                <td><span class="badge {badge_cls}">{a['status']}</span></td>
            </tr>
            """

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

    merged_jobs_map = {}
    ordered_merged_jobs = []

    for j in visible_jobs:
        comp_norm = normalize_company(j.get("company", ""))
        role_norm = normalize_role(j.get("title", ""))
        key = (comp_norm, role_norm)

        if key in merged_jobs_map:
            existing = merged_jobs_map[key]
            src = j.get("source", "Discovered API")
            if src not in existing["sources"]:
                existing["sources"].append(src)
            if any(ats in j.get("link", "").lower() for ats in ["greenhouse", "lever", "ashby", "smartrecruiters"]):
                existing["link"] = j["link"]
                if j.get("company") and len(j.get("company")) > 2:
                    existing["company"] = clean_company_display_name(j.get("company"))
                existing["title"] = j.get("title")
        else:
            j_copy = dict(j)
            j_copy["company"] = clean_company_display_name(j.get("company"))
            j_copy["sources"] = [j.get("source", "Discovered API")]
            merged_jobs_map[key] = j_copy
            ordered_merged_jobs.append(j_copy)

    for c_id, c_job in reported_closed_map.items():
        if not any(j.get('id') == c_id for j in ordered_merged_jobs):
            c_job_copy = dict(c_job)
            c_job_copy["company"] = clean_company_display_name(c_job.get("company"))
            ordered_merged_jobs.append(c_job_copy)

    visible_jobs = ordered_merged_jobs

    open_count = len([j for j in visible_jobs if j.get('id') not in closed_ids and j.get('link') not in closed_links])
    discovered_count = open_count

    cards_html = ""
    closed_cards_html = ""

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

        card_markup = render_job_card(j, is_reported_closed=is_reported_closed, is_applied=is_applied, is_hidden=(j_id in hidden_jobs))

        if is_reported_closed:
            closed_cards_html += card_markup
        else:
            cards_html += card_markup

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
    <title>ApplicationTrackr</title>
    <meta name="description" content="UK graduate scheme application tracker and job discovery engine">

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">

    <style>
        :root {{
            --bg: #f5f5f7;
            --bg-secondary: #ededf0;
            --border: #d2d2d7;
            --border-light: rgba(0, 0, 0, 0.06);
            --text-primary: #1d1d1f;
            --text-secondary: #6e6e73;
            --text-tertiary: #86868b;
            --blue: #0071e3;
            --blue-bg: rgba(0, 113, 227, 0.08);
            --blue-glow: rgba(0, 113, 227, 0.18);
            --green: #34c759;
            --green-bg: rgba(52, 199, 89, 0.1);
            --red: #ff3b30;
            --red-bg: rgba(255, 59, 48, 0.1);
            --orange: #ff9500;
            --orange-bg: rgba(255, 149, 0, 0.1);
            --purple: #af52de;
            --purple-bg: rgba(175, 82, 222, 0.1);
            --gray-bg: rgba(142, 142, 147, 0.12);
            --card-bg: rgba(255, 255, 255, 0.76);
            --card-border: rgba(255, 255, 255, 0.85);
            --card-shadow: 0 1px 4px rgba(0,0,0,0.04), 0 2px 12px rgba(0,0,0,0.03);
            --card-shadow-hover: 0 8px 30px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04);
            --radius: 14px;
            --radius-lg: 18px;
            --font: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif;
            --font-mono: 'JetBrains Mono', 'SF Mono', monospace;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}

        body {{
            background: var(--bg);
            background-image:
                radial-gradient(ellipse at 20% 0%, rgba(0, 113, 227, 0.045) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 100%, rgba(52, 199, 89, 0.035) 0%, transparent 50%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: var(--font);
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            line-height: 1.47059;
        }}

        /* ─── Layout ─── */
        .shell {{ max-width: 1200px; margin: 0 auto; padding: 0 24px; }}

        /* ─── Top bar ─── */
        .topbar {{
            display: flex; align-items: center; justify-content: space-between;
            padding: 14px 24px;
            border-bottom: 1px solid var(--border-light);
            background: rgba(255,255,255,0.78);
            backdrop-filter: saturate(180%) blur(20px);
            -webkit-backdrop-filter: saturate(180%) blur(20px);
            position: sticky; top: 0; z-index: 100;
        }}
        .topbar-brand {{
            font-size: 17px; font-weight: 700; color: var(--text-primary);
            letter-spacing: -0.022em;
        }}
        .topbar-right {{ display: flex; align-items: center; gap: 8px; }}
        .status-pill {{
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 12px; font-weight: 500; color: var(--green);
            padding: 5px 12px; background: var(--green-bg);
            border-radius: 100px;
        }}
        .status-pill .dot {{
            width: 6px; height: 6px; border-radius: 50%;
            background: var(--green);
            animation: blink 2s ease-in-out infinite;
        }}
        @keyframes blink {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}

        /* ─── Buttons ─── */
        .btn {{
            display: inline-flex; align-items: center; gap: 4px;
            padding: 7px 14px; border-radius: 980px;
            font-size: 12px; font-weight: 600; font-family: var(--font);
            cursor: pointer; border: none; text-decoration: none;
            transition: all 0.18s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }}
        .btn:active {{ transform: scale(0.96); }}
        .btn-filled {{ background: var(--blue); color: #fff; box-shadow: 0 2px 8px var(--blue-glow); }}
        .btn-filled:hover {{ box-shadow: 0 4px 16px var(--blue-glow); }}
        .btn-tinted {{ background: var(--blue-bg); color: var(--blue); }}
        .btn-tinted:hover {{ background: rgba(0, 113, 227, 0.14); }}
        .btn-ghost {{ background: transparent; color: var(--text-secondary); }}
        .btn-ghost:hover {{ background: rgba(0,0,0,0.04); }}
        .btn-danger-text {{ color: var(--red); }}

        /* ─── Stats row ─── */
        .stats-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 14px;
            margin: 28px 0 24px 0;
        }}
        .stat-cell {{
            background: var(--card-bg);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 18px 16px;
            text-align: center;
            box-shadow: var(--card-shadow);
            transition: transform 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94), box-shadow 0.2s ease;
        }}
        .stat-cell:hover {{
            transform: translateY(-2px);
            box-shadow: var(--card-shadow-hover);
        }}
        .stat-label {{ font-size: 11px; font-weight: 600; color: var(--text-tertiary); letter-spacing: 0.02em; text-transform: uppercase; }}
        .stat-value {{ font-size: 26px; font-weight: 700; color: var(--text-primary); margin-top: 4px; letter-spacing: -0.02em; }}

        /* ─── Tab bar ─── */
        .tab-bar {{
            display: flex; gap: 4px;
            border-bottom: 1px solid var(--border-light);
            margin-bottom: 24px;
            overflow-x: auto;
        }}
        .tab {{
            padding: 10px 16px;
            font-size: 13px; font-weight: 600;
            color: var(--text-secondary);
            background: transparent; border: none;
            cursor: pointer; font-family: var(--font);
            border-bottom: 2px solid transparent;
            white-space: nowrap;
            transition: color 0.2s ease, border-color 0.2s ease;
        }}
        .tab:hover {{ color: var(--text-primary); }}
        .tab.active {{
            color: var(--blue);
            border-bottom-color: var(--blue);
        }}

        /* ─── Section panel ─── */
        .panel {{ display: none; }}

        /* ─── Pipeline Split Grid ─── */
        .pipeline-grid {{
            display: grid;
            grid-template-columns: 1.35fr 1fr;
            gap: 20px;
            align-items: start;
        }}
        @media (max-width: 960px) {{
            .pipeline-grid {{ grid-template-columns: 1fr; }}
        }}

        /* ─── Search & filters ─── */
        .toolbar {{
            display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap;
            align-items: center;
        }}
        .search-wrap {{ flex: 1; min-width: 240px; position: relative; }}
        .search-wrap svg {{
            position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
            width: 14px; height: 14px; color: var(--text-tertiary);
        }}
        .search-input {{
            width: 100%; padding: 10px 14px 10px 36px;
            border: 1px solid var(--border-light);
            border-radius: var(--radius);
            font-size: 13px; font-weight: 500;
            color: var(--text-primary);
            background: rgba(255,255,255,0.8);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            outline: none; font-family: var(--font);
            box-shadow: var(--card-shadow);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}
        .search-input:focus {{
            border-color: var(--blue);
            box-shadow: 0 0 0 4px var(--blue-glow), var(--card-shadow);
        }}

        .sort-select {{
            padding: 10px 14px;
            border: 1px solid var(--border-light);
            border-radius: var(--radius);
            font-size: 13px; font-weight: 500;
            color: var(--text-primary);
            background: rgba(255,255,255,0.8);
            outline: none; font-family: var(--font);
            cursor: pointer;
        }}

        .filter-group-label {{
            font-size: 11px; font-weight: 700; color: var(--text-tertiary);
            text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 6px;
        }}

        .filter-chips-wrap {{ margin-bottom: 20px; display: flex; flex-direction: column; gap: 8px; }}
        .filter-chips {{ display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px; }}
        .chip {{
            padding: 7px 16px;
            border-radius: 980px;
            font-size: 12px; font-weight: 600;
            color: var(--text-secondary);
            background: rgba(255,255,255,0.65);
            border: 1px solid var(--border-light);
            cursor: pointer;
            white-space: nowrap;
            font-family: var(--font);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
        }}
        .chip:hover {{ background: rgba(255,255,255,0.9); border-color: var(--border); }}
        .chip.active {{
            background: var(--blue);
            color: #fff;
            border-color: var(--blue);
            box-shadow: 0 2px 10px var(--blue-glow);
        }}

        /* ─── Job cards ─── */
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 14px;
        }}
        .card {{
            background: var(--card-bg);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid var(--card-border);
            border-radius: var(--radius);
            padding: 18px;
            display: flex; flex-direction: column; gap: 8px;
            box-shadow: var(--card-shadow);
            transition: transform 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94), box-shadow 0.25s ease;
        }}
        .card:hover {{
            transform: translateY(-3px);
            box-shadow: var(--card-shadow-hover);
        }}
        .card-top {{ display: flex; justify-content: space-between; align-items: center; }}
        .card-status {{
            display: inline-flex; align-items: center; gap: 5px;
            font-size: 11px; font-weight: 600; color: var(--text-tertiary);
        }}
        .status-dot {{ width: 6px; height: 6px; border-radius: 50%; }}
        .dot-green {{ background: var(--green); }}
        .dot-blue {{ background: var(--blue); }}
        .dot-red {{ background: var(--red); }}
        .card-match {{
            font-size: 12px; font-weight: 700; color: var(--blue);
            background: var(--blue-bg);
            padding: 3px 10px; border-radius: 980px;
        }}
        .card-company {{ font-size: 15px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.01em; }}
        .card-role {{ font-size: 13.5px; font-weight: 600; color: var(--blue); line-height: 1.4; }}
        .card-meta {{ font-size: 12px; color: var(--text-tertiary); font-weight: 500; }}
        .card-source {{ font-size: 11px; color: var(--text-tertiary); }}
        .link-muted {{ color: var(--blue); text-decoration: none; font-weight: 500; }}
        .link-muted:hover {{ text-decoration: underline; }}
        .card-actions {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }}

        /* ─── Badges ─── */
        .badge {{
            display: inline-block; padding: 3px 8px;
            border-radius: 6px; font-size: 11px; font-weight: 600;
        }}
        .badge-blue {{ background: var(--blue-bg); color: var(--blue); }}
        .badge-green {{ background: var(--green-bg); color: #248a3d; }}
        .badge-red {{ background: var(--red-bg); color: var(--red); }}
        .badge-purple {{ background: var(--purple-bg); color: var(--purple); }}
        .badge-orange {{ background: var(--orange-bg); color: #d97706; }}
        .badge-gray {{ background: var(--gray-bg); color: var(--text-secondary); }}

        /* ─── Table ─── */
        .data-table {{
            width: 100%; border-collapse: collapse; font-size: 13px;
        }}
        .data-table th {{
            text-align: left; padding: 10px 12px;
            font-size: 11px; font-weight: 600; color: var(--text-tertiary);
            text-transform: uppercase; letter-spacing: 0.02em;
            border-bottom: 1px solid var(--border-light);
        }}
        .data-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-light);
            vertical-align: middle;
        }}
        .td-company {{ font-weight: 600; color: var(--text-primary); }}
        .td-role {{ color: var(--text-secondary); }}
        .td-pipeline {{ color: var(--text-tertiary); font-size: 12px; }}
        .empty-state {{ text-align: center; padding: 40px; color: var(--text-tertiary); }}

        /* ─── Section card ─── */
        .section-card {{
            background: var(--card-bg);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-lg);
            padding: 24px; margin-bottom: 20px;
            box-shadow: var(--card-shadow);
        }}
        .section-title {{
            font-size: 16px; font-weight: 700; color: var(--text-primary);
            letter-spacing: -0.022em; margin-bottom: 16px;
        }}

        /* ─── Settings form ─── */
        .form-group {{ margin-bottom: 16px; }}
        .form-label {{
            font-size: 13px; font-weight: 600; color: var(--text-primary);
            margin-bottom: 6px; display: block;
        }}
        .form-input {{
            width: 100%; padding: 10px 14px;
            border: 1px solid var(--border-light);
            border-radius: 10px;
            font-size: 14px; font-weight: 500;
            color: var(--text-primary);
            background: rgba(255,255,255,0.85);
            outline: none; font-family: var(--font);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}
        .form-input:focus {{
            border-color: var(--blue);
            box-shadow: 0 0 0 4px var(--blue-glow);
        }}
        textarea.form-input {{ resize: vertical; }}

        /* ─── Diagnostics ─── */
        .diag-row {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid var(--border-light);
            font-size: 13px;
        }}
        .diag-label {{ font-weight: 600; color: var(--text-primary); }}
        .diag-value {{ color: var(--green); font-weight: 600; }}
        .kb-tag {{
            display: inline-block; padding: 4px 10px;
            background: rgba(255,255,255,0.7); border: 1px solid var(--border-light);
            border-radius: 6px; font-size: 12px; font-weight: 500;
            color: var(--text-secondary); margin: 3px;
        }}

        /* ─── Sankey iframe ─── */
        .sankey-frame {{
            width: 100%; height: 380px; border: none;
            border-radius: var(--radius);
            background: transparent;
        }}

        /* ─── Responsive ─── */
        @media (max-width: 768px) {{
            .topbar {{ padding: 12px 16px; }}
            .shell {{ padding: 0 16px; }}
            .stats-row {{ grid-template-columns: repeat(3, 1fr); }}
            .stat-cell {{ padding: 14px 10px; }}
            .stat-value {{ font-size: 22px; }}
            .grid {{ grid-template-columns: 1fr; }}
            .topbar-right {{ gap: 4px; }}
        }}
        @media (max-width: 480px) {{
            .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
    <div class="topbar-brand">ApplicationTrackr</div>
    <div class="topbar-right">
        <div class="status-pill"><span class="dot"></span> Online</div>
        <a href="/api/rescan" class="btn btn-filled">Rescan</a>
        <a href="/api/sync-sheet" class="btn btn-tinted">Sync Sheet</a>
    </div>
</div>

<div class="shell">

    <!-- Stats -->
    <div class="stats-row">
        <div class="stat-cell">
            <div class="stat-label">Applications</div>
            <div class="stat-value">{total}</div>
        </div>
        <div class="stat-cell">
            <div class="stat-label">Active</div>
            <div class="stat-value" style="color:var(--blue);">{active}</div>
        </div>
        <div class="stat-cell">
            <div class="stat-label">Offers</div>
            <div class="stat-value" style="color:var(--green);">{offers}</div>
        </div>
        <div class="stat-cell">
            <div class="stat-label">Rejected</div>
            <div class="stat-value" style="color:var(--red);">{rejections}</div>
        </div>
        <div class="stat-cell">
            <div class="stat-label">Conversion</div>
            <div class="stat-value">{conv_rate}%</div>
        </div>
        <div class="stat-cell">
            <div class="stat-label">Discovered</div>
            <div class="stat-value">{discovered_count}</div>
        </div>
    </div>

    <!-- Tabs -->
    <div class="tab-bar">
        <button class="tab {tab_flow}" data-tab="flow" onclick="switchTab('flow')">Pipeline Flow</button>
        <button class="tab {tab_jobs}" data-tab="jobs" id="tab-jobs-btn" onclick="switchTab('jobs')">Discovered Schemes ({discovered_count})</button>
        <button class="tab {tab_settings}" data-tab="settings" onclick="switchTab('settings')">Filter Settings</button>
        <button class="tab {tab_status}" data-tab="diagnostics" onclick="switchTab('diagnostics')">Diagnostics & Logs</button>
        <button class="tab {tab_closed}" data-tab="closed" onclick="switchTab('closed')">Closed Schemes ({closed_count})</button>
    </div>

    <!-- Panel: Pipeline & Sankey (Side-by-Side Split View) -->
    <div id="view-flow" class="panel" style="{view_flow}">
        <div class="pipeline-grid">
            <div class="section-card" style="margin-bottom:0;">
                <div class="section-title">Application Flow Pipeline</div>
                <iframe src="/sankey-embed" class="sankey-frame" id="sankey-iframe"></iframe>
            </div>
            <div class="section-card" style="margin-bottom:0;">
                <div class="section-title">Logged Applications ({total})</div>
                <div style="overflow-x:auto;">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>Company</th>
                                <th>Role</th>
                                <th>Stage</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {apps_table_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
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

            <div class="filter-group-label" style="margin-top:4px;">Domain Focus & Status</div>
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
                    <label style="display:flex; align-items:center; gap:8px; font-size:13px; font-weight:500; cursor:pointer;">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk} style="width:16px; height:16px; accent-color:var(--blue);">
                        Hide other listings from applied companies
                    </label>
                </div>
                <button type="submit" class="btn btn-filled">Save</button>
            </form>
        </div>
    </div>

    <!-- Panel: Diagnostics -->
    <div id="view-status" class="panel" style="{view_status}">
        <div class="section-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                <div class="section-title" style="margin-bottom:0;">Live Terminal Output (docker logs -f applicationtrackr)</div>
                <div style="display:flex; gap:8px;">
                    <button onclick="fetchLiveLogs()" class="btn btn-tinted">Refresh</button>
                    <button onclick="clearLiveLogs()" class="btn btn-ghost btn-danger-text">Clear</button>
                </div>
            </div>
            <div id="live-log-container" style="background:#1c1c1e; color:#34c759; font-family:var(--font-mono); font-size:12px; line-height:1.6; height:420px; overflow-y:auto; padding:16px; border-radius:12px; border:1px solid rgba(255,255,255,0.1); white-space:pre-wrap; word-break:break-word;">
                Loading live container stream...
            </div>
        </div>

        <div class="section-card">
            <div class="section-title">System Status</div>
            <div class="diag-row">
                <span class="diag-label">Last scraper run</span>
                <span class="diag-value" style="color:var(--text-secondary);">{last_run}</span>
            </div>
            <div class="diag-row">
                <span class="diag-label">Indexed schemes</span>
                <span class="diag-value" style="color:var(--text-secondary);">{discovered_count}</span>
            </div>
            <div style="margin-top:16px; font-size:13px; font-weight:600; color:var(--text-primary); margin-bottom:8px;">Source status</div>
            {src_status_html}
        </div>

        <div class="section-card">
            <div class="section-title">Knowledge Base ({kb_count} rules)</div>
            <div style="max-height:200px; overflow-y:auto;">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- Panel: Closed -->
    <div id="view-closed" class="panel" style="{view_closed}">
        <div class="section-card">
            <div class="section-title">Closed Schemes ({closed_count})</div>
            <p style="font-size:13px; color:var(--text-tertiary); margin-bottom:16px;">
                Schemes marked as closed or filled. Re-open any scheme if it becomes available again.
            </p>
            <div class="grid">
                {closed_cards_html}
            </div>
        </div>
    </div>

</div>

<script>
    var logInterval = null;

    function startLiveLogPolling() {{
        fetchLiveLogs();
        if (!logInterval) {{
            logInterval = setInterval(fetchLiveLogs, 2000);
        }}
    }}

    function stopLiveLogPolling() {{
        if (logInterval) {{
            clearInterval(logInterval);
            logInterval = null;
        }}
    }}

    function switchTab(tabId) {{
        document.querySelectorAll('.panel').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));

        var targetView = document.getElementById('view-' + tabId);
        if (targetView) targetView.style.display = 'block';

        var activeBtn = document.querySelector('.tab[data-tab="' + tabId + '"]');
        if (activeBtn) activeBtn.classList.add('active');

        if (tabId === 'diagnostics') {{
            startLiveLogPolling();
        }} else {{
            stopLiveLogPolling();
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

            // Calculate live counts for Programme Chips (matching search + active domain)
            if (matchesSearch && matchesDomain) {{
                progCounts.all++;
                if (progData in progCounts) progCounts[progData]++;
            }}

            // Calculate live counts for Domain Chips (matching search + active programme)
            if (matchesSearch && matchesProgram) {{
                domainCounts.all++;
                if (statusData in domainCounts) domainCounts[statusData]++;
                if (catData in domainCounts) domainCounts[catData]++;
            }}
        }});

        // Update Programme Chips LIVE
        updateChipText('data-prog-chip="all"', 'All Programmes (' + progCounts.all + ')');
        updateChipText('data-prog-chip="graduate"', 'Graduate Schemes (Yr 3+) (' + progCounts.graduate + ')');
        updateChipText('data-prog-chip="internship"', 'Internships (Yr 2 / Summer) (' + progCounts.internship + ')');
        updateChipText('data-prog-chip="placement"', 'Industrial Placements (Yr 2 / 12-Mo) (' + progCounts.placement + ')');

        // Update Domain Chips LIVE
        updateChipText('data-dom-chip="all"', 'All Focuses (' + domainCounts.all + ')');
        updateChipText('data-dom-chip="not_applied"', 'Not Applied (' + domainCounts.not_applied + ')');
        updateChipText('data-dom-chip="applied"', 'Applied (' + domainCounts.applied + ')');
        updateChipText('data-dom-chip="quant"', 'Quant (' + domainCounts.quant + ')');
        updateChipText('data-dom-chip="software"', 'Software (' + domainCounts.software + ')');
        updateChipText('data-dom-chip="ml"', 'ML & AI (' + domainCounts.ml + ')');
        updateChipText('data-dom-chip="cyber"', 'Cyber (' + domainCounts.cyber + ')');

        // Update Tab Label LIVE
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
            if (mode === 'oldest') return (a.getAttribute('data-date') || '').localeCompare(b.getAttribute('data-date') || '');
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

    function fetchLiveLogs() {{
        fetch('/api/logs')
            .then(r => r.json())
            .then(data => {{
                var container = document.getElementById('live-log-container');
                if (container && data.logs) {{
                    if (data.logs.length === 0) {{
                        container.innerHTML = '<span style="color:#8e8e93;">No terminal output recorded yet. Trigger a Rescan or Sheet Sync to stream live output.</span>';
                    }} else {{
                        var isAtBottom = (container.scrollHeight - container.scrollTop <= container.clientHeight + 50);
                        container.innerHTML = data.logs.map(l => '<div>' + escapeHtml(l) + '</div>').join('');
                        if (isAtBottom) {{
                            container.scrollTop = container.scrollHeight;
                        }}
                    }}
                }}
            }})
            .catch(() => {{}});
    }}

    function clearLiveLogs() {{
        fetch('/api/clear-logs')
            .then(r => r.json())
            .then(() => fetchLiveLogs());
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        filterJobs();
        if ('{active_tab}' === 'diagnostics') {{
            startLiveLogPolling();
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
