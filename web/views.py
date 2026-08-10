import urllib.parse
from config import SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from core.storage import (
    load_settings, load_hidden_jobs,
    load_reported_closed_jobs, load_json_safe
)
from core.kb import load_closed_keywords_kb
from core.normalization import normalize_company, normalize_role
from core.scoring import calculate_skill_match_score
from sheets import (
    parse_sheet_stats, get_detailed_applications,
    get_applied_jobs_set, calculate_company_response_stats
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
                "company": a['company'],
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
        apps_table_rows = '<tr><td colspan="5" class="empty-table">No applications logged yet. Click "+ Log Applied" on any scheme to track!</td></tr>'
    else:
        for a in apps:
            st = a.get("status_type", "active")
            if st == "offer":
                badge_cls = "badge-offer"
            elif st == "rejected":
                badge_cls = "badge-rejected"
            elif st == "ghosted":
                badge_cls = "badge-ghosted"
            else:
                badge_cls = "badge-active"

            pipeline_str = " &rarr; ".join(a.get("stages", [])) if a.get("stages") else a.get("latest_stage", "Applied")

            apps_table_rows += f"""
            <tr>
                <td style="font-weight:700; color:#1d1d1f;">{a['company']}</td>
                <td style="color:#3a3a3c;">{a['role']}</td>
                <td><span class="apple-pill pill-active">{a['latest_stage']}</span></td>
                <td style="color:#86868b; font-size:13px;">{pipeline_str}</td>
                <td><span class="apple-pill pill-active">{a['status']}</span></td>
            </tr>
            """

    applied_count = 0
    not_applied_count = 0
    quant_count = 0
    sw_count = 0
    ml_count = 0
    cyber_count = 0
    closed_count = 0

    reported_closed_map = load_reported_closed_jobs()
    closed_ids = set(reported_closed_map.keys())
    closed_links = set(j.get('link') for j in reported_closed_map.values() if j.get('link'))

    kb_phrases = load_closed_keywords_kb()
    kb_count = len(kb_phrases)
    kb_badges_html = " ".join([f'<span class="apple-pill pill-kb">{p}</span>' for p in kb_phrases])

    resp_stats = calculate_company_response_stats()

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
                    existing["company"] = j.get("company")
                existing["title"] = j.get("title")
        else:
            j_copy = dict(j)
            j_copy["sources"] = [j.get("source", "Discovered API")]
            merged_jobs_map[key] = j_copy
            ordered_merged_jobs.append(j_copy)

    for c_id, c_job in reported_closed_map.items():
        if not any(j.get('id') == c_id for j in ordered_merged_jobs):
            ordered_merged_jobs.append(c_job)

    visible_jobs = ordered_merged_jobs

    open_count = len([j for j in visible_jobs if j.get('id') not in closed_ids and j.get('link') not in closed_links])
    discovered_count = open_count

    cards_html = ""
    closed_cards_html = ""

    for j in visible_jobs:
        j_id = j.get('id', '')
        j_link = j.get('link', '')
        comp_name = j.get('company', 'Unknown')
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
        if is_reported_closed:
            closed_count += 1
        else:
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

        card_markup = render_job_card(j, is_reported_closed=is_reported_closed, is_applied=is_applied, is_hidden=(j_id in hidden_jobs), company_resp_map=resp_stats)

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
        src_status_html += f'<div style="padding:6px 0; border-bottom:0.5px solid var(--apple-border); display:flex; justify-content:space-between;"><b>{s_name}</b> <span style="color:var(--apple-green); font-weight:600;">{s_msg}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>ApplicationTrackr — Apple Bento Command Center</title>
    
    <!-- Authentic Apple Typography & Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    
    <style>
        :root {{
            --apple-bg: #f5f5f7;
            --apple-card: #ffffff;
            --apple-card-translucent: rgba(255, 255, 255, 0.82);
            --apple-border: rgba(0, 0, 0, 0.08);
            --apple-border-strong: rgba(0, 0, 0, 0.12);
            --apple-blue: #0071e3;
            --apple-blue-hover: #0077ed;
            --apple-green: #34c759;
            --apple-orange: #ff9500;
            --apple-red: #ff3b30;
            --apple-purple: #af52de;
            --apple-indigo: #5856d6;
            --apple-text-primary: #1d1d1f;
            --apple-text-secondary: #515154;
            --apple-text-tertiary: #86868b;
            --apple-font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "SF Pro", "Helvetica Neue", "Plus Jakarta Sans", sans-serif;
            --apple-mono: "JetBrains Mono", SFMono-Regular, ui-monospace, Menlo, monospace;
        }}

        * {{ margin:0; padding:0; box-sizing:border-box; font-family: var(--apple-font); -webkit-tap-highlight-color: transparent; }}
        
        body {{
            background-color: var(--apple-bg);
            background-image: 
                radial-gradient(ellipse at 10% 10%, rgba(0, 113, 227, 0.05) 0%, transparent 60%),
                radial-gradient(ellipse at 90% 90%, rgba(52, 199, 89, 0.04) 0%, transparent 60%);
            background-attachment: fixed;
            color: var(--apple-text-primary);
            padding: 20px 16px 40px 16px;
            min-height: 100vh;
            font-size: 14px;
            -webkit-font-smoothing: antialiased;
        }}

        .container {{ max-width: 1280px; margin: 0 auto; }}

        /* 🍎 Apple Glass Header */
        .apple-header {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 16px 24px; background: var(--apple-card-translucent);
            backdrop-filter: blur(25px) saturate(190%);
            -webkit-backdrop-filter: blur(25px) saturate(190%);
            border-radius: 20px; border: 0.5px solid var(--apple-border);
            margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.03);
            flex-wrap: wrap; gap: 14px;
        }}

        .brand-row {{ display: flex; align-items: center; gap: 12px; }}
        .brand-logo {{
            width: 42px; height: 42px; background: linear-gradient(135deg, #0071e3 0%, #5856d6 100%);
            border-radius: 12px; display: flex; align-items: center; justify-content: center;
            color: #ffffff; font-size: 22px; font-weight: 800; box-shadow: 0 4px 12px rgba(0, 113, 227, 0.25);
        }}
        .brand-title {{ font-size: 21px; font-weight: 800; color: var(--apple-text-primary); letter-spacing: -0.025em; }}
        .brand-subtitle {{ font-size: 12px; color: var(--apple-text-tertiary); font-weight: 500; margin-top: 1px; }}

        .status-pill {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 6px 14px; background: rgba(52,199,89,0.12); color: #278a3c;
            border: 0.5px solid rgba(52,199,89,0.25); border-radius: 9999px;
            font-size: 12px; font-weight: 700;
        }}
        .status-dot {{ width: 7px; height: 7px; background: var(--apple-green); border-radius: 50%; box-shadow: 0 0 8px var(--apple-green); }}

        /* 🍱 High-Tech Lando Norris Bento Grid */
        .bento-grid {{
            display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; margin-bottom: 20px;
        }}

        .bento-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border);
            border-radius: 20px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.03);
            transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s ease;
            display: flex; flex-direction: column; justify-content: space-between;
        }}
        .bento-card:hover {{ transform: translateY(-3px); box-shadow: 0 12px 32px rgba(0,113,227,0.08); }}

        .bento-hero {{ grid-column: span 5; background: linear-gradient(135deg, #ffffff 0%, #f9f9fb 100%); }}
        .bento-stats {{ grid-column: span 7; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}

        .bento-stat-box {{
            background: var(--apple-bg); border: 0.5px solid var(--apple-border);
            border-radius: 14px; padding: 14px; text-align: center;
        }}
        .bento-stat-val {{ font-size: 26px; font-weight: 800; color: var(--apple-text-primary); letter-spacing: -0.03em; margin-top: 2px; }}
        .bento-stat-lbl {{ font-size: 11px; font-weight: 700; color: var(--apple-text-tertiary); text-transform: uppercase; letter-spacing: 0.04em; }}

        /* 🍎 Segmented Control Nav Tabs */
        .apple-nav-container {{
            background: rgba(229, 229, 234, 0.7); backdrop-filter: blur(20px);
            padding: 4px; border-radius: 16px; margin-bottom: 20px;
            border: 0.5px solid var(--apple-border);
        }}
        .nav-tabs {{ display: flex; gap: 4px; overflow-x: auto; }}

        .tab-btn {{
            flex: 1; padding: 10px 18px; background: transparent; border: none;
            color: var(--apple-text-secondary); font-size: 13px; font-weight: 700;
            cursor: pointer; border-radius: 12px; transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            whitespace: nowrap; text-align: center; letter-spacing: -0.01em;
        }}

        .tab-btn.active {{
            background: var(--apple-card); color: var(--apple-text-primary);
            box-shadow: 0 2px 10px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
        }}

        /* 🎛️ Dynamic Controls & Search Dock */
        .controls-dock {{
            display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
            justify-content: space-between; margin-bottom: 20px;
        }}

        .search-dock {{ flex: 1; min-width: 280px; position: relative; }}
        .search-input {{
            width: 100%; padding: 12px 16px 12px 42px; background: var(--apple-card);
            border: 0.5px solid var(--apple-border); border-radius: 14px;
            color: var(--apple-text-primary); font-size: 14px; font-weight: 500;
            outline: none; box-shadow: 0 2px 8px rgba(0,0,0,0.02); transition: border-color 0.15s ease;
        }}
        .search-input:focus {{ border-color: var(--apple-blue); box-shadow: 0 0 0 3px rgba(0,113,227,0.15); }}
        .search-icon {{ position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--apple-text-tertiary); font-size: 15px; }}

        .sort-select {{
            padding: 12px 16px; background: var(--apple-card); border: 0.5px solid var(--apple-border);
            border-radius: 14px; color: var(--apple-text-primary); font-size: 13px; font-weight: 700;
            cursor: pointer; outline: none; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }}

        .pill-dock {{ display: flex; gap: 6px; overflow-x: auto; width: 100%; padding-bottom: 4px; }}
        .pill-item {{
            padding: 7px 16px; background: rgba(0,0,0,0.04); border: 0.5px solid var(--apple-border);
            border-radius: 9999px; color: var(--apple-text-secondary); font-size: 12px; font-weight: 600;
            cursor: pointer; whitespace: nowrap; transition: all 0.15s ease;
        }}
        .pill-item.active {{ background: var(--apple-blue); color: #ffffff; border-color: var(--apple-blue); font-weight: 700; shadow: 0 2px 8px rgba(0,113,227,0.3); }}

        /* 💼 High-Tech Job Cards Grid */
        .jobs-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px;
        }}

        .job-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border);
            border-radius: 20px; padding: 20px; display: flex; flex-direction: column;
            justify-content: space-between; gap: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.03);
            transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s ease;
        }}
        .job-card:hover {{ transform: translateY(-3px); box-shadow: 0 12px 32px rgba(0, 113, 227, 0.08); }}

        .company-avatar-row {{ display: flex; align-items: center; gap: 10px; }}
        .company-avatar {{
            width: 38px; height: 38px; background: rgba(0, 113, 227, 0.1); color: var(--apple-blue);
            border: 0.5px solid rgba(0, 113, 227, 0.2); border-radius: 10px; display: flex; align-items: center;
            justify-content: center; font-weight: 800; font-size: 13px; letter-spacing: -0.02em;
        }}
        .company {{ font-weight: 800; color: var(--apple-text-primary); font-size: 15px; letter-spacing: -0.02em; }}
        .job-title {{ font-size: 16px; font-weight: 800; color: var(--apple-text-primary); line-height: 1.35; letter-spacing: -0.02em; }}
        .job-meta-inline {{ font-size: 11px; color: var(--apple-text-tertiary); font-weight: 500; margin-top: 1px; }}

        /* Telemetry Match Bar */
        .telemetry-bar-container {{
            background: var(--apple-bg); padding: 8px 12px; border-radius: 10px; border: 0.5px solid var(--apple-border);
        }}
        .telemetry-label-row {{ display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; margin-bottom: 4px; }}
        .telemetry-label {{ color: var(--apple-text-tertiary); text-transform: uppercase; letter-spacing: 0.04em; }}
        .telemetry-bar-track {{ height: 6px; background: rgba(0,0,0,0.06); border-radius: 9999px; overflow: hidden; }}
        .telemetry-bar-fill {{ height: 100%; border-radius: 9999px; transition: width 0.4s ease; }}

        .job-source-info {{ font-size: 11px; color: var(--apple-text-secondary); background: var(--apple-bg); padding: 6px 10px; border-radius: 8px; border: 0.5px solid var(--apple-border); }}
        .source-link {{ color: var(--apple-blue); text-decoration: none; word-break: break-all; font-weight: 600; }}

        .apple-pill {{
            display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px;
            border-radius: 9999px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.02em;
        }}
        .pill-open {{ background: rgba(52, 199, 89, 0.14); color: #278a3c; border: 0.5px solid rgba(52, 199, 89, 0.3); }}
        .pill-applied {{ background: rgba(0, 113, 227, 0.14); color: var(--apple-blue); border: 0.5px solid rgba(0, 113, 227, 0.3); }}
        .pill-closed {{ background: rgba(255, 59, 48, 0.14); color: var(--apple-red); border: 0.5px solid rgba(255, 59, 48, 0.3); }}
        .pill-active {{ background: rgba(0, 113, 227, 0.1); color: var(--apple-blue); border: 0.5px solid rgba(0, 113, 227, 0.2); }}
        .pill-kb {{ background: rgba(88, 86, 214, 0.1); color: var(--apple-indigo); border: 0.5px solid rgba(88, 86, 214, 0.25); font-size: 12px; margin: 3px; font-weight: 600; }}

        .job-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }}

        /* 🍏 Authentic Apple Action Buttons */
        .apple-btn {{
            padding: 8px 14px; border-radius: 9999px; font-size: 12px; font-weight: 700;
            text-decoration: none; display: inline-flex; align-items: center; gap: 4px;
            cursor: pointer; border: none; transition: all 0.15s cubic-bezier(0.16, 1, 0.3, 1);
            letter-spacing: -0.01em; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .apple-btn:active {{ transform: scale(0.96); opacity: 0.85; }}

        .apple-btn-primary {{ background: var(--apple-blue); color: #ffffff; box-shadow: 0 2px 8px rgba(0,113,227,0.28); }}
        .apple-btn-primary:hover {{ background: var(--apple-blue-hover); }}
        .apple-btn-secondary {{ background: rgba(0,0,0,0.05); color: var(--apple-text-primary); border: 0.5px solid var(--apple-border); }}
        .apple-btn-secondary.disabled {{ opacity: 0.7; cursor: default; }}
        .apple-btn-success {{ background: var(--apple-green); color: #ffffff; box-shadow: 0 2px 8px rgba(52,199,89,0.28); }}
        .apple-btn-danger {{ background: rgba(255,59,48,0.12); color: var(--apple-red); border: 0.5px solid rgba(255,59,48,0.25); }}

        .view-section {{ display: none; }}
        iframe {{ border: none; width: 100%; height: 500px; border-radius: 16px; background: #ffffff; border: 0.5px solid var(--apple-border); }}

        .bento-form-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border); border-radius: 20px;
            padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.03);
        }}
        .form-group {{ margin-bottom: 16px; }}
        .form-label {{ font-weight: 700; margin-bottom: 6px; display: block; color: var(--apple-blue); font-size: 13px; }}
        .form-input {{
            width: 100%; padding: 12px; background: #ffffff; border: 0.5px solid var(--apple-border);
            border-radius: 12px; color: var(--apple-text-primary); font-size: 13px; font-weight: 500; outline: none;
        }}
        .form-input:focus {{ border-color: var(--apple-blue); box-shadow: 0 0 0 3px rgba(0,113,227,0.12); }}

        @media (max-width: 900px) {{
            .bento-hero {{ grid-column: span 12; }}
            .bento-stats {{ grid-column: span 12; grid-template-columns: repeat(2, 1fr); }}
            .jobs-grid {{ grid-template-columns: 1fr; }}
            body {{ padding: 10px; }}
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- 🍎 Apple Glass Header Bar -->
    <div class="apple-header">
        <div class="brand-row">
            <div class="brand-logo">⚡</div>
            <div>
                <div class="brand-title">ApplicationTrackr</div>
                <div class="brand-subtitle">Headless HP Stream Engine &bull; UK Tech, Quant & AI Command Center</div>
            </div>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <div class="status-pill"><span class="status-dot"></span> HP-STREAM ONLINE ({HP_STREAM_TAILSCALE_IP}:5000)</div>
            <a href="/api/rescan" class="apple-btn apple-btn-primary">⚡ Rescan Now</a>
            <a href="/api/sync-sheet" class="apple-btn apple-btn-secondary">🔄 Sync Sheet</a>
        </div>
    </div>

    <!-- 🍱 Lando Norris High-Tech Bento Grid Header -->
    <div class="bento-grid">
        <div class="bento-card bento-hero">
            <div>
                <span class="apple-pill pill-active" style="margin-bottom:8px;">🎯 Application Telemetry</span>
                <h2 style="font-size:24px; font-weight:800; letter-spacing:-0.03em; margin-bottom:6px;">UK Tech & Quant Pipeline</h2>
                <p style="font-size:13px; color:var(--apple-text-secondary); line-height:1.5;">
                    Real-time automated multi-source egress engine tracking active graduate & placement schemes.
                </p>
            </div>
            <div style="margin-top:16px; display:flex; gap:12px; align-items:center;">
                <div style="font-size:32px; font-weight:800; color:var(--apple-blue); letter-spacing:-0.04em;">{open_count}</div>
                <div style="font-size:12px; color:var(--apple-text-tertiary); font-weight:600;">Active Apply-able<br>Schemes Indexed</div>
            </div>
        </div>

        <div class="bento-stats">
            <div class="bento-stat-box">
                <div class="bento-stat-lbl">TOTAL LOGGED</div>
                <div class="bento-stat-val">{total}</div>
            </div>
            <div class="bento-stat-box">
                <div class="bento-stat-lbl">ACTIVE ROUNDS</div>
                <div class="bento-stat-val" style="color:var(--apple-blue);">{active}</div>
            </div>
            <div class="bento-stat-box">
                <div class="bento-stat-lbl">OFFERS SECURED</div>
                <div class="bento-stat-val" style="color:var(--apple-green);">{offers}</div>
            </div>
            <div class="bento-stat-box">
                <div class="bento-stat-lbl">CONVERSION %</div>
                <div class="bento-stat-val" style="color:var(--apple-purple);">{conv_rate}%</div>
            </div>
        </div>
    </div>

    <!-- 🍎 Apple Segmented Control Nav Container -->
    <div class="apple-nav-container">
        <div class="nav-tabs">
            <button class="tab-btn {tab_flow}" onclick="switchTab('flow')">📊 Pipeline Flow & Sankey</button>
            <button class="tab-btn {tab_jobs}" onclick="switchTab('jobs')">💼 Discovered Schemes ({discovered_count})</button>
            <button class="tab-btn {tab_settings}" onclick="switchTab('settings')">⚙️ Filter Criteria</button>
            <button class="tab-btn {tab_status}" onclick="switchTab('diagnostics')">🛠 System Telemetry</button>
            <button class="tab-btn {tab_closed}" onclick="switchTab('closed')">🛑 Closed Schemes ({closed_count})</button>
        </div>
    </div>

    <!-- TAB 1: FLOW & SANKEY -->
    <div id="view-flow" class="view-section" style="{view_flow}">
        <div class="bento-form-card">
            <h3 style="margin-bottom:14px; font-weight:800; font-size:18px;">📊 Live Interactive Sankey Pipeline</h3>
            <iframe src="/sankey-embed" id="sankey-iframe"></iframe>
        </div>

        <div class="bento-form-card">
            <h3 style="margin-bottom:14px; font-weight:800; font-size:18px;">📋 Detailed Logged Applications</h3>
            <table style="width:100%; border-collapse:collapse; text-align:left; font-size:13px;">
                <thead>
                    <tr style="border-bottom:1px solid var(--apple-border); color:var(--apple-text-tertiary);">
                        <th style="padding:10px;">Company</th>
                        <th style="padding:10px;">Role</th>
                        <th style="padding:10px;">Latest Stage</th>
                        <th style="padding:10px;">Pipeline History</th>
                        <th style="padding:10px;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {apps_table_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 2: DISCOVERED SCHEMES -->
    <div id="view-jobs" class="view-section" style="{view_jobs}">
        <div class="controls-dock">
            <div class="search-dock">
                <span class="search-icon">🔍</span>
                <input type="text" id="job-search" class="search-input" placeholder="Search by company, role, location, or source..." onkeyup="filterJobs()">
            </div>

            <select id="sort-select" class="sort-select" onchange="sortJobs()">
                <option value="newest">🆕 Discovered: Newest First</option>
                <option value="oldest">⏳ Discovered: Oldest First</option>
                <option value="match_desc">🎯 Skill Match: Highest First</option>
                <option value="resp_asc">⚡ Response Time: Fastest First</option>
                <option value="company_asc">🔤 Company Name: A → Z</option>
                <option value="title_asc">💼 Role Title: A → Z</option>
            </select>

            <div class="pill-dock">
                <button class="pill-item active" onclick="filterPill('all', this)">All Open Schemes ({discovered_count})</button>
                <button class="pill-item" onclick="filterPill('not_applied', this)">⚡ Not Applied ({not_applied_count})</button>
                <button class="pill-item" onclick="filterPill('applied', this)">✅ Applied ({applied_count})</button>
                <button class="pill-item" onclick="filterPill('quant', this)">📈 Quant & Trading ({quant_count})</button>
                <button class="pill-item" onclick="filterPill('software', this)">💻 Software ({sw_count})</button>
                <button class="pill-item" onclick="filterPill('ml', this)">🧠 ML & AI ({ml_count})</button>
                <button class="pill-item" onclick="filterPill('cyber', this)">🛡️ Cyber & Cloud ({cyber_count})</button>
            </div>
        </div>

        <div id="jobs-container" class="jobs-grid">
            {cards_html}
        </div>
    </div>

    <!-- TAB 3: SETTINGS -->
    <div id="view-settings" class="view-section" style="{view_settings}">
        <div class="bento-form-card">
            <h3 style="margin-bottom:16px; font-weight:800; font-size:18px;">⚙️ Scraper & Filter Criteria</h3>
            <form action="/api/settings" method="POST">
                <div class="form-group">
                    <label class="form-label">Target Skills Matrix (Comma-Separated)</label>
                    <input type="text" name="my_skills" class="form-input" value="{my_skills_str}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude Keyword Terms (Comma-Separated)</label>
                    <input type="text" name="exclude_keywords" class="form-input" value="{ex_kw}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude Locations (Comma-Separated)</label>
                    <input type="text" name="exclude_locations" class="form-input" value="{ex_loc}">
                </div>

                <div class="form-group">
                    <label class="form-label">Greenhouse Target Companies</label>
                    <textarea name="greenhouse_companies" class="form-input" rows="3">{gh_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Lever Target Companies</label>
                    <textarea name="lever_companies" class="form-input" rows="2">{lev_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Ashby Target Companies</label>
                    <textarea name="ashby_companies" class="form-input" rows="2">{ash_comp}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">SmartRecruiters Target Companies</label>
                    <textarea name="smartrecruiters_companies" class="form-input" rows="2">{sr_comp}</textarea>
                </div>

                <div class="form-group" style="margin-top:12px;">
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk}>
                        <span style="font-size:13px; font-weight:600;">Automatically hide other open listings from companies I have already applied to</span>
                    </label>
                </div>

                <button type="submit" class="apple-btn apple-btn-primary" style="margin-top:10px;">💾 Save Filter Criteria</button>
            </form>
        </div>
    </div>

    <!-- TAB 4: SYSTEM DIAGNOSTICS -->
    <div id="view-status" class="view-section" style="{view_status}">
        <div class="bento-form-card">
            <h3 style="margin-bottom:12px; font-weight:800; font-size:18px;">🛠 System & Scraper Diagnostics</h3>
            <div style="font-size:13px; line-height:1.6; color:var(--apple-text-secondary);">
                <div><b>Last Scraper Engine Run:</b> {last_run}</div>
                <div><b>Total Schemes Indexed:</b> {discovered_count}</div>
                <div style="margin-top:12px; font-weight:700; color:var(--apple-text-primary);">ATS Source Egress Statuses:</div>
                <div style="margin-top:6px;">{src_status_html}</div>
            </div>
        </div>

        <div class="bento-form-card">
            <h3 style="margin-bottom:12px; font-weight:800; font-size:18px;">🧠 AI Self-Learning Knowledge Base ({kb_count} rules)</h3>
            <div style="max-height:180px; overflow-y:auto; padding:12px; background:var(--apple-bg); border-radius:12px; border:0.5px solid var(--apple-border);">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- TAB 5: CLOSED SCHEMES -->
    <div id="view-closed" class="view-section" style="{view_closed}">
        <div class="bento-form-card">
            <h3 style="margin-bottom:12px; font-weight:800; font-size:18px;">🛑 Reported & Inactive Schemes Directory ({closed_count})</h3>
            <p style="font-size:12px; color:var(--apple-text-tertiary); margin-bottom:16px;">
                Schemes listed here were automatically or manually marked as closed. You can re-open any scheme if it opens again.
            </p>
            <div class="jobs-grid">
                {closed_cards_html}
            </div>
        </div>
    </div>
</div>

<script>
    function switchTab(tabId) {{
        document.querySelectorAll('.view-section').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

        var targetView = document.getElementById('view-' + tabId);
        if (targetView) targetView.style.display = 'block';

        var btns = document.querySelectorAll('.tab-btn');
        btns.forEach(b => {{
            if (b.getAttribute('onclick').includes(tabId)) b.classList.add('active');
        }});
    }}

    var currentPill = 'all';

    function filterPill(cat, btn) {{
        currentPill = cat;
        document.querySelectorAll('.pill-item').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        filterJobs();
    }}

    function filterJobs() {{
        var q = document.getElementById('job-search').value.toLowerCase().trim();
        var cards = document.querySelectorAll('#jobs-container .job-card');

        cards.forEach(c => {{
            var searchData = (c.getAttribute('data-search') || '').toLowerCase();
            var statusData = c.getAttribute('data-status') || '';
            var catData = c.getAttribute('data-cat') || '';

            var matchesSearch = !q || searchData.includes(q);
            var matchesPill = true;

            if (currentPill === 'not_applied') matchesPill = (statusData === 'not_applied');
            else if (currentPill === 'applied') matchesPill = (statusData === 'applied');
            else if (currentPill !== 'all') matchesPill = (catData === currentPill);

            if (matchesSearch && matchesPill) {{
                c.style.display = 'flex';
            }} else {{
                c.style.display = 'none';
            }}
        }});
    }}

    function sortJobs() {{
        var mode = document.getElementById('sort-select').value;
        var container = document.getElementById('jobs-container');
        var cards = Array.from(container.children);

        cards.sort((a, b) => {{
            if (mode === 'newest') {{
                return (b.getAttribute('data-date') || '').localeCompare(a.getAttribute('data-date') || '');
            }} else if (mode === 'oldest') {{
                return (a.getAttribute('data-date') || '').localeCompare(b.getAttribute('data-date') || '');
            }} else if (mode === 'match_desc') {{
                return (parseFloat(b.getAttribute('data-match')) || 0) - (parseFloat(a.getAttribute('data-match')) || 0);
            }} else if (mode === 'resp_asc') {{
                return (parseFloat(a.getAttribute('data-resp')) || 99) - (parseFloat(b.getAttribute('data-resp')) || 99);
            }} else if (mode === 'company_asc') {{
                return (a.getAttribute('data-company') || '').localeCompare(b.getAttribute('data-company') || '');
            }} else if (mode === 'title_asc') {{
                return (a.getAttribute('data-title') || '').localeCompare(b.getAttribute('data-title') || '');
            }}
            return 0;
        }});

        cards.forEach(card => container.appendChild(card));
    }}

    function reportClosedJob(jobId, link) {{
        if (confirm('Report this scheme as closed/filled? This will train the AI Knowledge Base and recheck all open schemes.')) {{
            fetch('/api/report-closed?id=' + encodeURIComponent(jobId) + '&link=' + encodeURIComponent(link))
                .then(r => r.json())
                .then(data => {{
                    location.reload();
                }});
        }}
    }}

    function reopenJob(jobId) {{
        fetch('/api/reopen-job?id=' + encodeURIComponent(jobId))
            .then(r => r.json())
            .then(data => {{
                location.reload();
            }});
    }}

    function logJob(comp, title) {{
        fetch('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title))
            .then(r => r.json())
            .then(data => {{
                alert('✅ Marked as applied! Logged to Google Sheets.');
                location.reload();
            }});
    }}
</script>

</body>
</html>
"""
    return html
