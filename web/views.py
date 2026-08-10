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
        apps_table_rows = '<tr><td colspan="5" class="empty-table">No applications logged yet. Log your first application via Safari Bookmarklet or the Discovered Schemes tab!</td></tr>'
    else:
        for a in apps:
            st = a.get("status_type", "active")
            if st == "offer":
                badge_cls = "ln-badge ln-badge-applied"
            elif st == "rejected":
                badge_cls = "ln-badge ln-badge-closed"
            elif st == "ghosted":
                badge_cls = "ln-badge ln-badge-closed"
            else:
                badge_cls = "ln-badge ln-badge-open"

            pipeline_str = " &rarr; ".join(a.get("stages", [])) if a.get("stages") else a.get("latest_stage", "Applied")

            apps_table_rows += f"""
            <tr>
                <td style="font-weight:800; color:var(--apple-text-main);">{a['company']}</td>
                <td style="color:#3a3a3c; font-weight:600;">{a['role']}</td>
                <td><span class="{badge_cls}">{a['latest_stage']}</span></td>
                <td style="color:var(--apple-text-sub); font-size:12px; font-weight:600;">{pipeline_str}</td>
                <td><span class="{badge_cls}">{a['status']}</span></td>
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
    kb_badges_html = " ".join([f'<span class="kb-tag">{p}</span>' for p in kb_phrases])

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
        src_status_html += f'<div style="padding:8px 0; border-bottom:0.5px solid var(--apple-border);"><b>{s_name}:</b> <span style="color:var(--apple-blue); font-weight:700;">{s_msg}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>ApplicationTrackr // LN4 Telemetry Command Center</title>

    <!-- Embedded High-Performance Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700;800&display=swap" rel="stylesheet">

    <style>
        :root {{
            --font-sans: 'Plus Jakarta Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
            --font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
            
            --ln-yellow: #dfff00;
            --ln-yellow-dark: #000000;
            --apple-bg: #f4f4f7;
            --apple-card: #ffffff;
            --apple-border: rgba(0, 0, 0, 0.07);
            --apple-border-strong: rgba(0, 0, 0, 0.12);
            --apple-text-main: #1d1d1f;
            --apple-text-sub: #6e6e73;
            --apple-blue: #007aff;
            --apple-green: #34c759;
            --apple-orange: #ff9500;
            --apple-red: #ff3b30;
            --apple-purple: #af52de;
            --apple-indigo: #5856d6;
        }}

        * {{ margin:0; padding:0; box-sizing:border-box; font-family: var(--font-sans) !important; -webkit-tap-highlight-color: transparent; }}
        body {{
            background: var(--apple-bg); color: var(--apple-text-main);
            padding: 16px; min-height: 100vh; font-size: 14px; -webkit-font-smoothing: antialiased;
            background-image: radial-gradient(circle at 50% 0%, rgba(223, 255, 0, 0.05) 0%, transparent 60%);
        }}

        .container {{ max-width: 1280px; margin: 0 auto; }}

        /* LN4 Telemetry Strip */
        .ln-telemetry-strip {{
            display: flex; justify-content: space-between; align-items: center;
            background: #18181b; color: #ffffff; padding: 8px 18px; border-radius: 12px;
            margin-bottom: 16px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.12); flex-wrap: wrap; gap: 8px;
        }}
        .ln-badge-yellow {{ background: var(--ln-yellow); color: #000000; padding: 3px 8px; border-radius: 6px; font-weight: 900; text-transform: uppercase; }}

        /* Apple Glass Header */
        .header {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 18px 24px; background: rgba(255, 255, 255, 0.9); backdrop-filter: blur(25px) saturate(180%);
            border-radius: 20px; border: 0.5px solid var(--apple-border-strong); margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.03); flex-wrap: wrap; gap: 14px;
        }}

        .brand {{ display: flex; align-items: center; gap: 14px; }}
        .brand-logo {{
            width: 44px; height: 44px; background: #000000; color: var(--ln-yellow);
            border-radius: 12px; display: flex; align-items: center; justify-content: center;
            font-size: 20px; font-weight: 900; font-family: var(--font-mono) !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}

        .brand-title {{ font-size: 22px; font-weight: 900; color: var(--apple-text-main); letter-spacing: -0.6px; }}
        .brand-sub {{ font-size: 12px; color: var(--apple-text-sub); font-weight: 600; margin-top: 1px; }}

        .status-pill {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 6px 14px; background: rgba(52,199,89,0.12); color: #278a3c;
            border: 0.5px solid rgba(52,199,89,0.3); border-radius: 20px;
            font-size: 12px; font-weight: 800;
        }}

        /* Racing Telemetry Stat Cards */
        .stats-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px; margin-bottom: 20px;
        }}

        .stat-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border);
            border-radius: 18px; padding: 18px; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
            position: relative; overflow: hidden; transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .stat-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.05); }}
        .stat-card::before {{ content:''; position:absolute; top:0; left:0; right:0; height:3px; background: var(--apple-border); }}
        .stat-card.accent-yellow::before {{ background: var(--ln-yellow); }}
        .stat-card.accent-blue::before {{ background: var(--apple-blue); }}
        .stat-card.accent-green::before {{ background: var(--apple-green); }}
        .stat-card.accent-red::before {{ background: var(--apple-red); }}
        .stat-card.accent-purple::before {{ background: var(--apple-purple); }}

        .stat-val {{ font-size: 26px; font-weight: 900; color: var(--apple-text-main); margin-top: 4px; letter-spacing: -0.5px; }}
        .stat-lbl {{ font-size: 10px; color: var(--apple-text-sub); font-weight: 800; text-transform: uppercase; letter-spacing: 0.8px; }}

        /* Segmented Apple Nav Tabs */
        .nav-tabs {{
            display: flex; gap: 6px; background: rgba(229, 229, 234, 0.7);
            padding: 4px; border-radius: 16px; margin-bottom: 20px; overflow-x: auto;
        }}

        .tab-btn {{
            flex: 1; padding: 12px 18px; background: transparent; border: none;
            color: var(--apple-text-sub); font-size: 13px; font-weight: 800;
            cursor: pointer; border-radius: 12px; transition: all 0.2s ease; whitespace: nowrap; text-align: center;
        }}

        .tab-btn.active {{
            background: var(--apple-card); color: var(--apple-text-main);
            box-shadow: 0 2px 10px rgba(0,0,0,0.08); font-weight: 900;
        }}

        /* Control Filter & Search Bar */
        .controls-bar {{
            display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 18px;
        }}

        .search-box {{ flex: 1; min-width: 260px; position: relative; }}

        .search-input {{
            width: 100%; padding: 12px 16px 12px 40px; background: var(--apple-card);
            border: 0.5px solid var(--apple-border-strong); border-radius: 14px; color: var(--apple-text-main);
            font-size: 14px; font-weight: 600; outline: none; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }}
        .search-input:focus {{ border-color: var(--apple-blue); box-shadow: 0 0 0 3px rgba(0,122,255,0.15); }}

        .search-icon {{ position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--apple-text-sub); font-size: 14px; }}

        .sort-select {{
            padding: 12px 16px; background: var(--apple-card); border: 0.5px solid var(--apple-border-strong);
            border-radius: 14px; color: var(--apple-text-main); font-size: 13px; font-weight: 700; cursor: pointer;
            outline: none; box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }}

        .pill-filters {{ display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px; width: 100%; }}
        .pill {{
            padding: 8px 16px; background: rgba(0, 0, 0, 0.04); border: 0.5px solid var(--apple-border);
            border-radius: 20px; color: var(--apple-text-sub); font-size: 12px; font-weight: 700; cursor: pointer; whitespace: nowrap;
        }}
        .pill.active {{ background: #18181b; color: var(--ln-yellow); border-color: #18181b; font-weight: 900; }}

        /* Cards Grid Layout */
        .jobs-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px;
        }}

        .job-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border-strong); border-radius: 18px;
            padding: 20px; display: flex; flex-direction: column; justify-content: space-between; gap: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.03); transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .job-card:hover {{ transform: translateY(-3px); box-shadow: 0 10px 30px rgba(0, 122, 255, 0.1); }}

        .job-card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
        .company-badge-wrap {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
        .company {{ font-weight: 900; color: var(--apple-blue); font-size: 16px; letter-spacing: -0.3px; }}

        .job-title {{ font-size: 17px; font-weight: 800; color: var(--apple-text-main); line-height: 1.3; }}

        .telemetry-bar {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 2px 0; }}
        .telemetry-item {{ font-size: 12px; color: var(--apple-text-sub); font-weight: 700; }}

        .job-meta-row {{ font-size: 11px; color: var(--apple-text-sub); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
        .job-source-info {{ font-size: 11px; color: var(--apple-text-sub); background: rgba(0,0,0,0.03); padding: 8px 12px; border-radius: 10px; border: 0.5px solid var(--apple-border); font-weight: 600; }}
        .source-link {{ color: var(--apple-blue); text-decoration: none; word-break: break-all; font-weight: 700; }}

        .ln-badge {{
            display: inline-block; padding: 4px 10px; border-radius: 8px; font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.5px;
        }}
        .ln-badge-open {{ background: rgba(52, 199, 89, 0.15); color: #1fb042; }}
        .ln-badge-applied {{ background: rgba(0, 122, 255, 0.15); color: var(--apple-blue); }}
        .ln-badge-closed {{ background: rgba(255, 59, 48, 0.15); color: var(--apple-red); }}

        .ln-match-badge {{ display: inline-block; padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 900; letter-spacing: 0.5px; }}
        .ln-match-high {{ background: var(--ln-yellow); color: #000000; font-weight: 900; }}
        .ln-match-norm {{ background: rgba(255, 149, 0, 0.15); color: #d97706; }}

        .ln-source-tag {{ font-size: 10px; font-weight: 800; color: var(--apple-text-sub); text-transform: uppercase; background: rgba(0,0,0,0.05); padding: 4px 8px; border-radius: 6px; }}

        .job-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }}

        .ios-btn {{
            padding: 9px 15px; border-radius: 12px; font-size: 11px; font-weight: 800; text-decoration: none;
            display: inline-flex; align-items: center; gap: 4px; cursor: pointer; border: none;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06); transition: transform 0.1s ease, opacity 0.1s ease; text-transform: uppercase; letter-spacing: 0.5px;
        }}
        .ios-btn:active {{ transform: scale(0.96); opacity: 0.85; }}

        .ios-btn-primary {{ background: var(--apple-blue); color: #ffffff; }}
        .ios-btn-secondary {{ background: rgba(0,0,0,0.05); color: var(--apple-text-main); border: 0.5px solid var(--apple-border-strong); }}
        .ios-btn-success {{ background: var(--apple-green); color: #ffffff; }}
        .ios-btn-danger {{ background: rgba(255,59,48,0.12); color: var(--apple-red); border: 0.5px solid rgba(255,59,48,0.25); }}

        .view-section {{ display: none; }}

        iframe {{ border: none; width: 100%; height: 520px; border-radius: 16px; background: #ffffff; border: 0.5px solid var(--apple-border-strong); }}

        .settings-card {{
            background: var(--apple-card); border: 0.5px solid var(--apple-border-strong); border-radius: 18px; padding: 22px; margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }}
        .settings-group {{ margin-bottom: 16px; }}
        .settings-label {{ font-weight: 800; margin-bottom: 6px; display: block; color: var(--apple-blue); font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .settings-input {{
            width: 100%; padding: 12px; background: #ffffff; border: 0.5px solid var(--apple-border-strong);
            border-radius: 12px; color: var(--apple-text-main); font-size: 13px; font-weight: 600; outline: none;
        }}
        .settings-input:focus {{ border-color: var(--apple-blue); box-shadow: 0 0 0 3px rgba(0,122,255,0.15); }}

        .kb-tag {{ display: inline-block; background: rgba(0, 122, 255, 0.1); color: var(--apple-blue); border: 0.5px solid rgba(0, 122, 255, 0.25); font-size: 12px; margin: 3px; padding: 5px 10px; border-radius: 8px; font-weight: 700; }}

        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .header {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
            .jobs-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- LN4 Telemetry Banner -->
    <div class="ln-telemetry-strip">
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="ln-badge-yellow">LN4 TELEMETRY</span>
            <span>HEADLESS HP-STREAM TELEMETRY ACTIVE</span>
        </div>
        <div style="display:flex; gap:14px; align-items:center;">
            <span>⚡ EGRESS: <b>100% DIRECT</b></span>
            <span>🎯 MATCH ENGINE: <b>ACTIVE</b></span>
            <span>🕒 SYNC: <b>AUTO-CRON</b></span>
        </div>
    </div>

    <!-- Apple Header -->
    <div class="header">
        <div class="brand">
            <div class="brand-logo">LN4</div>
            <div>
                <div class="brand-title">ApplicationTrackr</div>
                <div class="brand-sub">UK Maths, Quant & CS High-Octane Scraper System</div>
            </div>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <div class="status-pill">● HP-STREAM ONLINE ({HP_STREAM_TAILSCALE_IP}:5000)</div>
            <a href="/api/rescan" class="ios-btn ios-btn-primary">⚡ RESCAN NOW</a>
            <a href="/api/sync-sheet" class="ios-btn ios-btn-secondary">🔄 SYNC SHEET</a>
        </div>
    </div>

    <!-- Racing Stat Cards -->
    <div class="stats-grid">
        <div class="stat-card accent-blue">
            <div class="stat-lbl">TOTAL APPLICATIONS</div>
            <div class="stat-val">{total}</div>
        </div>
        <div class="stat-card accent-yellow">
            <div class="stat-lbl">ACTIVE ROUNDS</div>
            <div class="stat-val" style="color:var(--apple-blue);">{active}</div>
        </div>
        <div class="stat-card accent-green">
            <div class="stat-lbl">OFFERS SECURED</div>
            <div class="stat-val" style="color:var(--apple-green);">{offers}</div>
        </div>
        <div class="stat-card accent-red">
            <div class="stat-lbl">REJECTIONS / GHOSTED</div>
            <div class="stat-val" style="color:var(--apple-red);">{rejections}</div>
        </div>
        <div class="stat-card accent-purple">
            <div class="stat-lbl">CONVERSION RATE</div>
            <div class="stat-val" style="color:var(--apple-purple);">{conv_rate}%</div>
        </div>
        <div class="stat-card accent-yellow">
            <div class="stat-lbl">OPEN SCHEMES</div>
            <div class="stat-val" style="color:#1d1d1f;">{discovered_count}</div>
        </div>
    </div>

    <!-- Segmented Navigation Tabs -->
    <div class="nav-tabs">
        <button class="tab-btn {tab_flow}" onclick="switchTab('flow')">📊 FLOW & TELEMETRY</button>
        <button class="tab-btn {tab_jobs}" onclick="switchTab('jobs')">💼 DISCOVERED SCHEMES ({discovered_count})</button>
        <button class="tab-btn {tab_settings}" onclick="switchTab('settings')">⚙️ FILTER SETTINGS</button>
        <button class="tab-btn {tab_status}" onclick="switchTab('diagnostics')">🛠 SCRAPER TELEMETRY</button>
        <button class="tab-btn {tab_closed}" onclick="switchTab('closed')">🛑 CLOSED SCHEMES ({closed_count})</button>
    </div>

    <!-- TAB 1: FLOW & SANKEY -->
    <div id="view-flow" class="view-section" style="{view_flow}">
        <div class="settings-card">
            <h3 style="margin-bottom:14px; font-weight:900; font-size:18px;">📊 Live Application Pipeline Flow</h3>
            <iframe src="/sankey-embed" id="sankey-iframe"></iframe>
        </div>

        <div class="settings-card" style="margin-top:16px;">
            <h3 style="margin-bottom:14px; font-weight:900; font-size:18px;">📋 Detailed Logged Applications</h3>
            <table style="width:100%; border-collapse:collapse; text-align:left; font-size:13px;">
                <thead>
                    <tr style="border-bottom:2px solid var(--apple-border-strong); color:var(--apple-text-sub); font-size:11px; text-transform:uppercase; letter-spacing:0.8px;">
                        <th style="padding:12px;">Company</th>
                        <th style="padding:12px;">Role</th>
                        <th style="padding:12px;">Latest Stage</th>
                        <th style="padding:12px;">Pipeline History</th>
                        <th style="padding:12px;">Status</th>
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
        <div class="controls-bar">
            <div class="search-box">
                <span class="search-icon">🔍</span>
                <input type="text" id="job-search" class="search-input" placeholder="Search company, role, location, or source..." onkeyup="filterJobs()">
            </div>

            <select id="sort-select" class="sort-select" onchange="sortJobs()">
                <option value="newest">🆕 DISCOVERED: NEWEST FIRST</option>
                <option value="oldest">⏳ DISCOVERED: OLDEST FIRST</option>
                <option value="match_desc">🎯 SKILL MATCH: HIGHEST FIRST</option>
                <option value="resp_asc">⚡ RESPONSE TIME: FASTEST FIRST</option>
                <option value="company_asc">🔤 COMPANY NAME: A → Z</option>
                <option value="title_asc">💼 ROLE TITLE: A → Z</option>
            </select>

            <div class="pill-filters">
                <button class="pill active" onclick="filterPill('all', this)">ALL SCHEMES ({discovered_count})</button>
                <button class="pill" onclick="filterPill('not_applied', this)">⚡ NOT APPLIED ({not_applied_count})</button>
                <button class="pill" onclick="filterPill('applied', this)">✅ APPLIED ({applied_count})</button>
                <button class="pill" onclick="filterPill('quant', this)">📈 QUANT ({quant_count})</button>
                <button class="pill" onclick="filterPill('software', this)">💻 SOFTWARE ({sw_count})</button>
                <button class="pill" onclick="filterPill('ml', this)">🧠 ML & AI ({ml_count})</button>
                <button class="pill" onclick="filterPill('cyber', this)">🛡️ CYBER ({cyber_count})</button>
            </div>
        </div>

        <div id="jobs-container" class="jobs-grid">
            {cards_html}
        </div>
    </div>

    <!-- TAB 3: SETTINGS -->
    <div id="view-settings" class="view-section" style="{view_settings}">
        <div class="settings-card">
            <h3 style="margin-bottom:16px; font-weight:900; font-size:18px;">⚙️ Scraper & Filter Criteria</h3>
            <form action="/api/settings" method="POST">
                <div class="settings-group">
                    <label class="settings-label">Target Skills Matrix (Comma-Separated)</label>
                    <input type="text" name="my_skills" class="settings-input" value="{my_skills_str}">
                </div>
                <div class="settings-group">
                    <label class="settings-label">Exclude Keyword Terms (Comma-Separated)</label>
                    <input type="text" name="exclude_keywords" class="settings-input" value="{ex_kw}">
                </div>
                <div class="settings-group">
                    <label class="settings-label">Exclude Locations (Comma-Separated)</label>
                    <input type="text" name="exclude_locations" class="settings-input" value="{ex_loc}">
                </div>

                <div class="settings-group">
                    <label class="settings-label">Greenhouse Target Companies</label>
                    <textarea name="greenhouse_companies" class="settings-input" rows="3">{gh_comp}</textarea>
                </div>
                <div class="settings-group">
                    <label class="settings-label">Lever Target Companies</label>
                    <textarea name="lever_companies" class="settings-input" rows="2">{lev_comp}</textarea>
                </div>
                <div class="settings-group">
                    <label class="settings-label">Ashby Target Companies</label>
                    <textarea name="ashby_companies" class="settings-input" rows="2">{ash_comp}</textarea>
                </div>
                <div class="settings-group">
                    <label class="settings-label">SmartRecruiters Target Companies</label>
                    <textarea name="smartrecruiters_companies" class="settings-input" rows="2">{sr_comp}</textarea>
                </div>

                <div class="settings-group" style="margin-top:14px;">
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:700;">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk}>
                        <span>Automatically hide other open listings from companies I have already applied to</span>
                    </label>
                </div>

                <button type="submit" class="ios-btn ios-btn-primary" style="margin-top:12px;">💾 SAVE FILTER SETTINGS</button>
            </form>
        </div>
    </div>

    <!-- TAB 4: SYSTEM DIAGNOSTICS -->
    <div id="view-status" class="view-section" style="{view_status}">
        <div class="settings-card">
            <h3 style="margin-bottom:14px; font-weight:900; font-size:18px;">🛠 Scraper Telemetry Diagnostics</h3>
            <div style="font-size:13px; line-height:1.7; color:var(--apple-text-sub);">
                <div><b>Last Scraper Engine Run:</b> <span style="color:var(--apple-text-main); font-weight:700;">{last_run}</span></div>
                <div><b>Total Schemes Indexed:</b> <span style="color:var(--apple-text-main); font-weight:700;">{discovered_count}</span></div>
                <div style="margin-top:12px; font-weight:800; color:var(--apple-text-main); text-transform:uppercase; letter-spacing:0.5px;">ATS Source Egress Statuses:</div>
                {src_status_html}
            </div>
        </div>

        <div class="settings-card" style="margin-top:16px;">
            <h3 style="margin-bottom:14px; font-weight:900; font-size:18px;">🧠 AI Self-Learning Knowledge Base ({kb_count} rules)</h3>
            <div style="max-height:160px; overflow-y:auto; padding:12px; background:#ffffff; border-radius:12px; border:0.5px solid var(--apple-border-strong);">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- TAB 5: CLOSED SCHEMES -->
    <div id="view-closed" class="view-section" style="{view_closed}">
        <div class="settings-card">
            <h3 style="margin-bottom:12px; font-weight:900; font-size:18px;">🛑 Reported & Inactive Schemes Directory ({closed_count})</h3>
            <p style="font-size:12px; color:var(--apple-text-sub); margin-bottom:16px; font-weight:600;">
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
        document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
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
