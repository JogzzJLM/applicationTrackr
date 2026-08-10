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
    hidden_count = len(hidden_jobs)

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
                <td style="font-weight:700; color:#f8fafc;">{a['company']}</td>
                <td style="color:#cbd5e1;">{a['role']}</td>
                <td><span class="badge {badge_cls}">{a['latest_stage']}</span></td>
                <td style="color:#94a3b8; font-size:13px;">{pipeline_str}</td>
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

    reported_closed_map = load_reported_closed_jobs()
    closed_ids = set(reported_closed_map.keys())
    closed_links = set(j.get('link') for j in reported_closed_map.values() if j.get('link'))

    kb_phrases = load_closed_keywords_kb()
    kb_count = len(kb_phrases)
    kb_badges_html = " ".join([f'<span class="badge" style="background:rgba(56,189,248,0.12); color:#38bdf8; border:0.5px solid rgba(56,189,248,0.3); font-size:12px; margin:2px; padding:4px 8px;">{p}</span>' for p in kb_phrases])

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
        src_status_html += f'<div><b>{s_name}:</b> <span style="color:#22c55e;">{s_msg}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>⚡ ApplicationTrackr - Command Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #090d16;
            --bg-card: rgba(17, 24, 39, 0.75);
            --border-color: rgba(255, 255, 255, 0.08);
            --accent-blue: #38bdf8;
            --accent-green: #34c759;
            --accent-purple: #a855f7;
            --accent-red: #ef4444;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
        }}

        * {{ margin:0; padding:0; box-sizing:border-box; font-family:'Inter', -apple-system, sans-serif; }}
        body {{ background: var(--bg-primary); color: var(--text-primary); padding: 16px; min-height: 100vh; font-size: 14px; -webkit-font-smoothing: antialiased; }}

        .container {{ max-width: 1280px; margin: 0 auto; }}

        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            background: var(--bg-card);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            margin-bottom: 20px;
        }}

        .brand {{ display: flex; align-items: center; gap: 12px; }}
        .brand-icon {{ font-size: 24px; color: var(--accent-blue); }}
        .brand-title {{ font-size: 18px; font-weight: 800; letter-spacing: -0.5px; background: linear-gradient(135deg, #38bdf8, #a855f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .brand-sub {{ font-size: 12px; color: var(--text-secondary); margin-top: 2px; }}

        .status-pill {{
            display: inline-flex; align-items: center; gap: 6px;
            padding: 6px 12px; background: rgba(52,199,89,0.12);
            color: var(--accent-green); border: 1px solid rgba(52,199,89,0.25);
            border-radius: 20px; font-size: 12px; font-weight: 600;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }}

        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 16px;
            backdrop-filter: blur(12px);
        }}

        .stat-val {{ font-size: 26px; font-weight: 800; margin-top: 4px; color: var(--text-primary); }}
        .stat-lbl {{ font-size: 12px; color: var(--text-secondary); font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }}

        .nav-tabs {{
            display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; overflow-x: auto;
        }}

        .tab-btn {{
            padding: 10px 18px; background: transparent; border: none; color: var(--text-secondary);
            font-size: 14px; font-weight: 600; cursor: pointer; border-radius: 10px; transition: all 0.2s ease; whitespace: nowrap;
        }}

        .tab-btn:hover {{ color: var(--text-primary); background: rgba(255,255,255,0.04); }}
        .tab-btn.active {{ color: var(--text-primary); background: var(--border-color); border: 1px solid rgba(255,255,255,0.1); }}

        .controls-bar {{
            display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 16px;
        }}

        .search-box {{
            flex: 1; min-width: 240px; position: relative;
        }}

        .search-input {{
            width: 100%; padding: 10px 14px 10px 36px; background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color); border-radius: 10px; color: white; font-size: 14px;
        }}

        .search-icon {{ position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-secondary); }}

        .sort-select {{
            padding: 10px 14px; background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color);
            border-radius: 10px; color: white; font-size: 13px; font-weight: 600; cursor: pointer;
        }}

        .pill-filters {{ display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px; }}
        .pill {{
            padding: 6px 14px; background: rgba(255,255,255,0.04); border: 1px solid var(--border-color);
            border-radius: 20px; color: var(--text-secondary); font-size: 12px; font-weight: 600; cursor: pointer; whitespace: nowrap;
        }}
        .pill.active {{ background: var(--accent-blue); color: #000; border-color: var(--accent-blue); }}

        .jobs-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px;
        }}

        .job-card {{
            background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 14px;
            padding: 18px; display: flex; flex-direction: column; justify-content: space-between; gap: 12px;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }}

        .job-card:hover {{ transform: translateY(-2px); border-color: rgba(56,189,248,0.3); }}

        .job-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
        .company {{ font-weight: 700; color: var(--accent-blue); font-size: 15px; }}
        .job-title {{ font-size: 16px; font-weight: 700; color: var(--text-primary); line-height: 1.3; }}
        .job-meta {{ font-size: 12px; color: var(--text-secondary); }}
        .job-source-info {{ font-size: 11px; color: var(--text-secondary); background: rgba(0,0,0,0.2); padding: 6px 10px; border-radius: 6px; }}
        .source-link {{ color: var(--accent-blue); text-decoration: none; word-break: break-all; }}

        .badge {{
            display: inline-block; padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase;
        }}

        .badge-open {{ background: rgba(52,199,89,0.15); color: var(--accent-green); }}
        .badge-applied {{ background: rgba(56,189,248,0.15); color: var(--accent-blue); }}
        .badge-closed {{ background: rgba(239,68,68,0.15); color: var(--accent-red); }}
        .badge-source {{ background: rgba(255,255,255,0.06); color: var(--text-secondary); font-size: 10px; }}

        .job-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }}

        .ios-btn {{
            padding: 8px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; text-decoration: none;
            display: inline-flex; align-items: center; gap: 4px; cursor: pointer; border: none; transition: background 0.2s;
        }}

        .ios-btn-primary {{ background: var(--accent-blue); color: #000; }}
        .ios-btn-secondary {{ background: rgba(255,255,255,0.08); color: var(--text-primary); border: 1px solid var(--border-color); }}
        .ios-btn-success {{ background: var(--accent-green); color: #000; }}
        .ios-btn-danger {{ background: rgba(239,68,68,0.15); color: var(--accent-red); border: 1px solid rgba(239,68,68,0.3); }}

        .view-section {{ display: none; }}

        iframe {{ border: none; width: 100%; height: 600px; border-radius: 12px; }}

        .settings-card {{
            background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 14px; padding: 20px; margin-bottom: 20px;
        }}
        .settings-group {{ margin-bottom: 16px; }}
        .settings-label {{ font-weight: 700; margin-bottom: 6px; display: block; color: var(--accent-blue); }}
        .settings-input {{
            width: 100%; padding: 10px; background: rgba(0,0,0,0.3); border: 1px solid var(--border-color);
            border-radius: 8px; color: white; font-size: 13px;
        }}

        @media (max-width: 768px) {{
            body {{ padding: 8px; }}
            .header {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
            .jobs-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

<div class="container">
    <div class="header">
        <div class="brand">
            <span class="brand-icon">⚡</span>
            <div>
                <div class="brand-title">ApplicationTrackr</div>
                <div class="brand-sub">Headless HP Stream Server &bull; UK Maths, Quant & CS Engine</div>
            </div>
        </div>
        <div style="display:flex; gap:10px; align-items:center;">
            <div class="status-pill">● HP-STREAM ONLINE ({HP_STREAM_TAILSCALE_IP}:5000)</div>
            <a href="/api/rescan" class="ios-btn ios-btn-primary" style="font-size:12px;">⚡ Rescan Now</a>
            <a href="/api/sync-sheet" class="ios-btn ios-btn-secondary" style="font-size:12px;">🔄 Sync Sheet</a>
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-lbl">TOTAL APPLICATIONS</div>
            <div class="stat-val">{total}</div>
        </div>
        <div class="stat-card">
            <div class="stat-lbl">ACTIVE / PENDING ROUNDS</div>
            <div class="stat-val" style="color:var(--accent-blue);">{active}</div>
        </div>
        <div class="stat-card">
            <div class="stat-lbl">OFFERS SECURED</div>
            <div class="stat-val" style="color:var(--accent-green);">{offers}</div>
        </div>
        <div class="stat-card">
            <div class="stat-lbl">REJECTIONS / GHOSTED</div>
            <div class="stat-val" style="color:var(--accent-red);">{rejections}</div>
        </div>
        <div class="stat-card">
            <div class="stat-lbl">FUNNEL CONVERSION RATE</div>
            <div class="stat-val" style="color:var(--accent-purple);">{conv_rate}%</div>
        </div>
        <div class="stat-card">
            <div class="stat-lbl">DISCOVERED SCHEMES</div>
            <div class="stat-val">{discovered_count}</div>
        </div>
    </div>

    <div class="nav-tabs">
        <button class="tab-btn {tab_flow}" onclick="switchTab('flow')">📊 Application Flow & Sankey</button>
        <button class="tab-btn {tab_jobs}" onclick="switchTab('jobs')">💼 Discovered Schemes ({discovered_count})</button>
        <button class="tab-btn {tab_settings}" onclick="switchTab('settings')">⚙️ Filter Settings</button>
        <button class="tab-btn {tab_status}" onclick="switchTab('diagnostics')">🛠 System Diagnostics</button>
        <button class="tab-btn {tab_closed}" onclick="switchTab('closed')">🛑 Closed Schemes ({closed_count})</button>
    </div>

    <!-- TAB 1: FLOW & SANKEY -->
    <div id="view-flow" class="view-section" style="{view_flow}">
        <div class="settings-card">
            <h3 style="margin-bottom:12px; font-weight:800;">📊 Live Application Pipeline</h3>
            <iframe src="/sankey-embed" id="sankey-iframe"></iframe>
        </div>

        <div class="settings-card" style="margin-top:20px;">
            <h3 style="margin-bottom:12px; font-weight:800;">📋 Detailed Application Tracker</h3>
            <table style="width:100%; border-collapse:collapse; text-align:left; font-size:13px;">
                <thead>
                    <tr style="border-bottom:1px solid var(--border-color); color:var(--text-secondary);">
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
        <div class="controls-bar">
            <div class="search-box">
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

            <div class="pill-filters">
                <button class="pill active" onclick="filterPill('all', this)">All Open Schemes ({discovered_count})</button>
                <button class="pill" onclick="filterPill('not_applied', this)">⚡ Not Applied ({not_applied_count})</button>
                <button class="pill" onclick="filterPill('applied', this)">✅ Applied ({applied_count})</button>
                <button class="pill" onclick="filterPill('quant', this)">📈 Quant & Trading ({quant_count})</button>
                <button class="pill" onclick="filterPill('software', this)">💻 Software ({sw_count})</button>
                <button class="pill" onclick="filterPill('ml', this)">🧠 ML & AI ({ml_count})</button>
                <button class="pill" onclick="filterPill('cyber', this)">🛡️ Cyber & Cloud ({cyber_count})</button>
            </div>
        </div>

        <div id="jobs-container" class="jobs-grid">
            {cards_html}
        </div>
    </div>

    <!-- TAB 3: SETTINGS -->
    <div id="view-settings" class="view-section" style="{view_settings}">
        <div class="settings-card">
            <h3 style="margin-bottom:16px; font-weight:800;">⚙️ Scraper & Filter Criteria</h3>
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

                <div class="settings-group" style="margin-top:12px;">
                    <label style="display:flex; align-items:center; gap:8px; cursor:pointer;">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk}>
                        <span>Automatically hide other open listings from companies I have already applied to</span>
                    </label>
                </div>

                <button type="submit" class="ios-btn ios-btn-primary" style="margin-top:10px;">💾 Save Filter Settings</button>
            </form>
        </div>
    </div>

    <!-- TAB 4: SYSTEM DIAGNOSTICS -->
    <div id="view-status" class="view-section" style="{view_status}">
        <div class="settings-card">
            <h3 style="margin-bottom:12px; font-weight:800;">🛠 System & Scraper Diagnostics</h3>
            <div style="font-size:13px; line-height:1.6; color:var(--text-secondary);">
                <div><b>Last Scraper Engine Run:</b> {last_run}</div>
                <div><b>Total Schemes Indexed:</b> {discovered_count}</div>
                <div style="margin-top:10px; font-weight:700; color:var(--text-primary);">ATS Source Engine Statuses:</div>
                {src_status_html}
            </div>
        </div>

        <div class="settings-card" style="margin-top:16px;">
            <h3 style="margin-bottom:12px; font-weight:800;">🧠 AI Self-Learning Knowledge Base ({kb_count} rules)</h3>
            <div style="max-height:160px; overflow-y:auto; padding:10px; background:rgba(0,0,0,0.3); border-radius:8px; border:1px solid var(--border-color);">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- TAB 5: CLOSED SCHEMES -->
    <div id="view-closed" class="view-section" style="{view_closed}">
        <div class="settings-card">
            <h3 style="margin-bottom:12px; font-weight:800;">🛑 Reported & Inactive Schemes Directory ({closed_count})</h3>
            <p style="font-size:12px; color:var(--text-secondary); margin-bottom:16px;">
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
        var q = document.getElementById('job-search').value.lower().strip();
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
        if (confirm('Report this scheme as closed/filled? This will train the AI Knowledge Base and check all open schemes.')) {{
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
        fetch('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title));
    }}
</script>

</body>
</html>
"""
    return html
