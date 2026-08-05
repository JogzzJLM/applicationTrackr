import os
import json
import threading
import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

from config import PORT, SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP, load_settings, save_settings, normalize_company, normalize_role
from notifications import send_notification
from sheets import (
    update_google_sheet_via_webhook, generate_sankey_from_google_sheets,
    generate_default_sankey, get_applied_jobs_set, parse_sheet_stats,
    get_detailed_applications
)
from scrapers import run_all_scrapers, load_discovered_jobs
from scheduler import trigger_daily_briefing, trigger_weekly_report

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def render_unified_dashboard_html(active_tab="flow"):
    stats = parse_sheet_stats()
    apps = get_detailed_applications()
    all_jobs = load_discovered_jobs()
    applied_jobs, _ = get_applied_jobs_set()
    settings = load_settings()

    total = stats.get("total", 0)
    active = stats.get("active", 0)
    offers = stats.get("offers", 0)
    rejections = stats.get("rejections", 0)
    conv_rate = round((offers / total * 100), 1) if total > 0 else 0.0

    last_run = SCRAPER_STATUS.get("last_run", "Never")

    # Ensure every logged application ALWAYS stays in the Discovered Schemes directory even if external ATS closes
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

    discovered_count = len(all_jobs)

    # Active Apps Table Rows
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

    # Dynamic Category Counters
    applied_count = 0
    not_applied_count = 0
    quant_count = 0
    sw_count = 0
    ml_count = 0
    cyber_count = 0

    # Job Cards HTML
    cards_html = ""
    for j in all_jobs:
        comp_name = j.get('company', 'Unknown')
        title_name = j.get('title', 'Role')
        comp_norm = normalize_company(comp_name)
        title_norm = normalize_role(title_name)

        is_applied = (comp_norm, title_norm) in applied_jobs
        if not is_applied:
            for (ac, ar) in applied_jobs:
                if ac == comp_norm:
                    if ar and (ar in title_norm or title_norm in ar):
                        is_applied = True
                        break

        comp_js = comp_name.replace("'", "\\'").replace('"', '&quot;')
        title_js = title_name.replace("'", "\\'").replace('"', '&quot;')

        title_lower = title_name.lower()
        cat = "other"
        if any(k in title_lower for k in ["quant", "trader", "trading", "finance", "financial"]):
            cat = "quant"
            quant_count += 1
        elif any(k in title_lower for k in ["software", "developer", "backend", "fullstack", "full-stack", "engineer"]):
            cat = "software"
            sw_count += 1
        elif any(k in title_lower for k in ["machine learning", "ml", "ai", "data science"]):
            cat = "ml"
            ml_count += 1
        elif any(k in title_lower for k in ["cyber", "security", "cloud", "devops"]):
            cat = "cyber"
            cyber_count += 1

        if is_applied:
            status_badge = '<span class="badge badge-applied">✅ APPLIED</span>'
            action_btn = '<button class="btn btn-disabled" disabled>✓ Logged</button>'
            status_tag = "applied"
            applied_count += 1
        else:
            status_badge = '<span class="badge badge-not-applied">⚡ NOT APPLIED</span>'
            action_btn = f'''
            <button onclick="autoApply('{comp_js}', '{title_js}', '{j['link']}')" class="btn btn-auto">⚡ Auto-Apply & Log</button>
            <button onclick="logJob('{comp_js}', '{title_js}')" class="btn btn-success">+ Log Only</button>
            '''
            status_tag = "notapplied"
            not_applied_count += 1

        source_name = j.get('source', 'Discovered API')
        source_url = j.get('source_url') or j.get('link') or '#'
        display_url = source_url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
        if len(display_url) > 42:
            display_url = display_url[:39] + "..."

        cards_html += f"""
        <div class="job-card" data-search="{j['company'].lower()} {j['title'].lower()} {j['location'].lower()} {status_tag} {cat} {source_name.lower()}" data-status="{status_tag}" data-cat="{cat}">
            <div class="job-header">
                <div>
                    <span class="company">{j['company']}</span> &nbsp;
                    {status_badge}
                </div>
                <span class="badge badge-source">🌐 {source_name}</span>
            </div>
            <div class="job-title">{j['title']}</div>
            <div class="job-meta">📍 {j['location']} &nbsp;&bull;&nbsp; 🕒 Discovered: {j['date_found']}</div>
            <div class="job-source-info">
                🔍 <b>Scraped Webpage:</b> <a href="{source_url}" target="_blank" class="source-link">{display_url} ↗</a>
            </div>
            <div class="job-actions">
                <a href="{j['link']}" target="_blank" class="btn btn-primary">Apply Direct ↗</a>
                {action_btn}
            </div>
        </div>
        """

    if not cards_html:
        cards_html = '<div class="empty-msg">No job listings indexed yet. Click "Trigger Re-Scan" above to run scrapers!</div>'


    grad_years = ", ".join(settings.get("grad_years_allowed", []))
    ex_keywords = ", ".join(settings.get("exclude_keywords", []))
    ex_locations = ", ".join(settings.get("exclude_locations", []))
    my_skills = ", ".join(settings.get("my_skills", []))

    status_json_formatted = json.dumps(SCRAPER_STATUS, indent=2)


    flow_display = "block" if active_tab == "flow" else "none"
    jobs_display = "block" if active_tab == "jobs" else "none"
    settings_display = "block" if active_tab == "settings" else "none"
    diag_display = "block" if active_tab == "diagnostics" else "none"

    flow_active = "active" if active_tab == "flow" else ""
    jobs_active = "active" if active_tab == "jobs" else ""
    settings_active = "active" if active_tab == "settings" else ""
    diag_active = "active" if active_tab == "diagnostics" else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ApplicationTrackr - Unified Command Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090d16;
            --card-bg: rgba(19, 28, 46, 0.75);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #38bdf8;
            --primary-glow: rgba(56, 189, 248, 0.15);
            --accent: #818cf8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --font: 'Plus Jakarta Sans', sans-serif;
            --mono: 'JetBrains Mono', monospace;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: var(--font);
            background: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px 20px;
            background-image: 
                radial-gradient(at 0% 0%, rgba(56, 189, 248, 0.08) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(129, 140, 248, 0.08) 0px, transparent 50%);
            background-attachment: fixed;
        }}

        .container {{ max-width: 1280px; margin: 0 auto; }}

        /* Top Header & System Bar */
        .header-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            padding: 20px 28px;
            border-radius: 20px;
            margin-bottom: 24px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.3);
            flex-wrap: wrap;
            gap: 16px;
        }}

        .brand-title {{
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .brand-subtitle {{
            color: var(--text-muted);
            font-size: 13px;
            margin-top: 4px;
            font-weight: 500;
        }}

        .server-status-pill {{
            background: rgba(16, 185, 129, 0.12);
            color: #6ee7b7;
            border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 6px 14px;
            border-radius: 30px;
            font-size: 12px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }}

        .status-dot {{
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: 50%;
            box-shadow: 0 0 10px var(--success);
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0% {{ transform: scale(0.95); opacity: 0.8; }}
            50% {{ transform: scale(1.15); opacity: 1; }}
            100% {{ transform: scale(0.95); opacity: 0.8; }}
        }}

        .header-actions {{ display: flex; gap: 12px; align-items: center; }}

        .btn-header {{
            background: var(--primary-glow);
            color: var(--primary);
            border: 1px solid rgba(56, 189, 248, 0.3);
            padding: 10px 18px;
            border-radius: 12px;
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.25s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }}

        .btn-header:hover {{
            background: var(--primary);
            color: #090d16;
            box-shadow: 0 0 20px rgba(56, 189, 248, 0.4);
            transform: translateY(-2px);
        }}

        /* KPI Metrics Cards Grid */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}

        .metric-card {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 20px;
            transition: all 0.25s ease;
            box-shadow: 0 4px 16px rgba(0,0,0,0.2);
        }}

        .metric-card:hover {{
            border-color: rgba(56, 189, 248, 0.4);
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }}

        .metric-label {{ color: var(--text-muted); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
        .metric-value {{ font-size: 28px; font-weight: 800; color: var(--text-main); font-family: var(--mono); }}
        .metric-value.offers {{ color: var(--success); }}
        .metric-value.active {{ color: var(--primary); }}
        .metric-value.rejections {{ color: var(--danger); }}
        .metric-value.rate {{ color: var(--accent); }}

        /* Navigation Tabs */
        .nav-tabs {{
            display: flex;
            gap: 10px;
            background: var(--card-bg);
            padding: 8px;
            border-radius: 16px;
            border: 1px solid var(--card-border);
            margin-bottom: 24px;
            overflow-x: auto;
        }}

        .nav-tab {{
            padding: 12px 22px;
            color: var(--text-muted);
            font-weight: 700;
            font-size: 14px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
            user-select: none;
        }}

        .nav-tab:hover {{ color: var(--text-main); background: rgba(255, 255, 255, 0.04); }}
        .nav-tab.active {{ background: var(--primary); color: #090d16; font-weight: 800; box-shadow: 0 4px 16px rgba(56, 189, 248, 0.3); }}

        /* Tab Content Cards */
        .content-card {{
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 28px;
            margin-bottom: 24px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.3);
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--card-border);
        }}

        .card-title {{ font-size: 18px; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 10px; }}

        /* Active Apps Table */
        .table-responsive {{ overflow-x: auto; margin-top: 15px; }}
        .app-table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        .app-table th {{ background: rgba(15, 23, 42, 0.6); padding: 14px 18px; color: var(--text-muted); font-weight: 700; font-size: 12px; text-transform: uppercase; border-bottom: 1px solid var(--card-border); }}
        .app-table td {{ padding: 16px 18px; border-bottom: 1px solid var(--card-border); vertical-align: middle; }}
        .app-table tr:hover {{ background: rgba(255, 255, 255, 0.02); }}
        .empty-table {{ text-align: center; color: var(--text-muted); padding: 30px; }}

        /* Badges */
        .badge {{ font-size: 11px; padding: 5px 12px; border-radius: 20px; font-weight: 700; display: inline-block; text-transform: uppercase; }}
        .badge-active {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
        .badge-offer {{ background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.3); }}
        .badge-rejected {{ background: rgba(239, 68, 68, 0.15); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.3); }}
        .badge-ghosted {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.3); }}
        .badge-applied {{ background: rgba(6, 78, 59, 0.8); color: #6ee7b7; border: 1px solid #047857; }}
        .badge-not-applied {{ background: rgba(30, 58, 138, 0.8); color: #93c5fd; border: 1px solid #1d4ed8; }}
        .badge-source {{ background: rgba(51, 65, 85, 0.6); color: #cbd5e1; }}

        /* Discovered Schemes Search & Filter Pills */
        .search-box {{
            width: 100%;
            padding: 14px 20px;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            color: var(--text-main);
            font-size: 15px;
            font-family: var(--font);
            outline: none;
            margin-bottom: 20px;
            transition: all 0.25s ease;
        }}
        .search-box:focus {{ border-color: var(--primary); box-shadow: 0 0 16px var(--primary-glow); }}

        .filter-pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }}
        .pill {{
            padding: 8px 16px;
            background: rgba(30, 41, 59, 0.6);
            color: var(--text-muted);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .pill:hover, .pill.active {{ background: var(--primary); color: #090d16; border-color: var(--primary); font-weight: 800; }}

        /* Job Cards */
        .job-card {{
            background: rgba(15, 23, 42, 0.6);
            border-radius: 14px;
            padding: 20px;
            margin-bottom: 16px;
            border: 1px solid var(--card-border);
            transition: all 0.2s ease;
        }}
        .job-card:hover {{ border-color: rgba(56, 189, 248, 0.4); transform: translateY(-2px); }}
        .job-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 10px; }}
        .company {{ color: #f1f5f9; font-weight: 800; font-size: 18px; }}
        .job-title {{ color: #93c5fd; font-size: 15px; margin-bottom: 10px; font-weight: 600; line-height: 1.4; }}
        .job-meta {{ color: #64748b; font-size: 13px; margin-bottom: 8px; }}
        .job-source-info {{ font-size: 12px; color: #94a3b8; margin-bottom: 16px; background: rgba(15, 23, 42, 0.75); padding: 6px 12px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.06); display: inline-block; }}
        .source-link {{ color: #38bdf8; text-decoration: none; font-weight: 600; }}
        .source-link:hover {{ text-decoration: underline; color: #7dd3fc; }}
        .job-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}


        /* Buttons */
        .btn {{
            padding: 10px 18px;
            border-radius: 10px;
            text-decoration: none;
            font-weight: 700;
            font-size: 13px;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn-primary {{ background: #2563eb; color: white; }}
        .btn-primary:hover {{ background: #1d4ed8; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4); }}
        .btn-auto {{ background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%); color: white; }}
        .btn-auto:hover {{ opacity: 0.9; box-shadow: 0 4px 12px rgba(139, 92, 246, 0.4); }}
        .btn-success {{ background: #10b981; color: white; }}
        .btn-success:hover {{ background: #059669; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4); }}
        .btn-disabled {{ background: #334155; color: #94a3b8; cursor: not-allowed; }}

        /* Terminal Console */
        .terminal-box {{
            background: #020617;
            border: 1px solid var(--primary);
            border-radius: 14px;
            padding: 20px;
            margin-bottom: 24px;
            font-family: var(--mono);
            box-shadow: 0 0 24px rgba(56, 189, 248, 0.15);
        }}
        .terminal-header {{ color: var(--primary); font-weight: 700; font-size: 13px; margin-bottom: 12px; display: flex; justify-content: space-between; }}
        .terminal-logs {{ color: #a5f3fc; font-size: 13px; white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; }}

        /* Form Controls */
        .form-group {{ margin-bottom: 22px; }}
        .form-group label {{ display: block; color: var(--text-main); font-weight: 700; font-size: 14px; margin-bottom: 8px; }}
        .form-input {{
            width: 100%;
            padding: 12px 18px;
            border-radius: 10px;
            border: 1px solid var(--card-border);
            background: rgba(15, 23, 42, 0.7);
            color: var(--text-main);
            font-size: 14px;
            font-family: var(--font);
            outline: none;
            transition: all 0.25s ease;
        }}
        .form-input:focus {{ border-color: var(--primary); box-shadow: 0 0 16px var(--primary-glow); }}
        .btn-save {{ width: 100%; background: var(--success); color: white; font-weight: 800; padding: 14px; border-radius: 12px; border: none; font-size: 15px; cursor: pointer; transition: all 0.2s ease; }}
        .btn-save:hover {{ background: #059669; box-shadow: 0 4px 16px rgba(16, 185, 129, 0.4); }}

        /* Diagnostics Code Block */
        .code-block {{
            background: #020617;
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 18px;
            color: #38bdf8;
            font-family: var(--mono);
            font-size: 13px;
            overflow-x: auto;
        }}

        .empty-msg {{ text-align: center; color: var(--text-muted); padding: 40px; font-size: 15px; }}
    </style>
    <script>
        function switchTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
            
            document.getElementById('tab-' + tabId).style.display = 'block';
            document.getElementById('nav-' + tabId).classList.add('active');

            let urlMap = {{ 'flow': '/', 'jobs': '/jobs', 'settings': '/settings', 'diagnostics': '/status' }};
            if (urlMap[tabId]) {{
                history.pushState(null, '', urlMap[tabId]);
            }}
        }}

        function filterJobs() {{
            let q = document.getElementById('search').value.toLowerCase();
            let cards = document.querySelectorAll('.job-card');
            cards.forEach(c => {{
                let txt = c.getAttribute('data-search');
                c.style.display = txt.includes(q) ? 'block' : 'none';
            }});
        }}

        function filterPill(category, btn) {{
            document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            let cards = document.querySelectorAll('.job-card');
            cards.forEach(c => {{
                if (category === 'all') {{
                    c.style.display = 'block';
                }} else if (category === 'applied') {{
                    c.style.display = c.getAttribute('data-status') === 'applied' ? 'block' : 'none';
                }} else if (category === 'notapplied') {{
                    c.style.display = c.getAttribute('data-status') === 'notapplied' ? 'block' : 'none';
                }} else {{
                    c.style.display = c.getAttribute('data-cat') === category ? 'block' : 'none';
                }}
            }});
        }}

        function logJob(company, role) {{
            fetch('/api/log?company=' + encodeURIComponent(company) + '&role=' + encodeURIComponent(role))
                .then(r => r.json())
                .then(d => {{
                    alert('✅ Successfully logged ' + company + ' to Google Sheets!');
                    location.reload();
                }})
                .catch(e => alert('❌ Error logging job: ' + e));
        }}

        function autoApply(company, role, link) {{
            window.open(link, '_blank');
            fetch('/api/log?company=' + encodeURIComponent(company) + '&role=' + encodeURIComponent(role))
                .then(r => r.json())
                .then(d => {{
                    setTimeout(() => location.reload(), 1000);
                }});
        }}

        function triggerInlineScan() {{
            switchTab('jobs');
            let term = document.getElementById('terminal-box');
            let log = document.getElementById('terminal-logs');
            let tag = document.getElementById('scan-tag');
            term.style.display = 'block';
            tag.innerText = 'Running...';
            log.innerText = '⚡ Initiating background multi-source scraper run...\\nScanning Greenhouse API, Lever API, and Trackr REST API...\\nPlease wait...';
            
            fetch('/api/trigger-scan')
                .then(r => r.json())
                .then(d => {{
                    let checkInterval = setInterval(() => {{
                        fetch('/status')
                            .then(sr => sr.json())
                            .then(s => {{
                                log.innerText = "⚡ Scraper Status: Run in progress...\\nLast Run: " + s.last_run + "\\nTotal Discovered Schemes: " + (s.total_seen_jobs || {discovered_count});
                            }});
                    }}, 2000);
                    
                    setTimeout(() => {{
                        clearInterval(checkInterval);
                        tag.innerText = 'Complete!';
                        log.innerText += '\\n\\n✅ Scraper run completed successfully! Reloading schemes...';
                        setTimeout(() => location.reload(), 1500);
                    }}, 8000);
                }})
                .catch(e => {{
                    log.innerText += '\\n❌ Error triggering scan: ' + e;
                }});
        }}

        function refreshSankey() {{
            let iframe = document.getElementById('sankey-iframe');
            if (iframe) {{
                iframe.src = '/sankey-embed?_cb=' + Date.now();
                alert('🔄 Refreshed Sankey Diagram from Google Sheets!');
            }}
        }}

        function saveSettings(e) {{
            e.preventDefault();
            let body = {{
                grad_years_allowed: document.getElementById('grad_years').value.split(',').map(s=>s.trim()).filter(Boolean),
                exclude_keywords: document.getElementById('ex_keywords').value.split(',').map(s=>s.trim()).filter(Boolean),
                exclude_locations: document.getElementById('ex_locations').value.split(',').map(s=>s.trim()).filter(Boolean),
                my_skills: document.getElementById('my_skills').value.split(',').map(s=>s.trim()).filter(Boolean)
            }};
            fetch('/api/settings', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(body)
            }})
            .then(r=>r.json())
            .then(d=> alert('💾 Settings saved successfully! Scrapers will use these updated filters.'))
            .catch(err=> alert('❌ Error saving settings: ' + err));
        }}

        function triggerTestEndpoint(endpoint, label) {{
            fetch(endpoint)
                .then(r => r.text())
                .then(msg => alert('✅ ' + label + ': ' + msg))
                .catch(e => alert('❌ Error triggering ' + label + ': ' + e));
        }}
    </script>
</head>
<body>
    <div class="container">
        <!-- Top Header & System Bar -->
        <div class="header-bar">
            <div>
                <div class="brand-title">⚡ ApplicationTrackr <span style="font-size:12px; padding:4px 10px; border-radius:12px; background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.3); font-weight:700;">COMMAND CENTER</span></div>
                <div class="brand-subtitle">Headless HP Stream Server &bull; 24/7 UK Maths, Quant & CS Career Engine</div>
            </div>
            <div class="header-actions">
                <span class="server-status-pill">
                    <span class="status-dot"></span> HP-STREAM ONLINE ({HP_STREAM_TAILSCALE_IP}:5000)
                </span>
                <button onclick="triggerInlineScan()" class="btn-header">⚡ Rescan Now</button>
                <button onclick="refreshSankey()" class="btn-header">🔄 Sync Sheet</button>
            </div>
        </div>

        <!-- Top KPI Metrics Cards -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Total Applications</div>
                <div class="metric-value">{total}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Active / Pending Rounds</div>
                <div class="metric-value active">{active}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Offers Secured</div>
                <div class="metric-value offers">{offers}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Rejections / Ghosted</div>
                <div class="metric-value rejections">{rejections}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Funnel Conversion Rate</div>
                <div class="metric-value rate">{conv_rate}%</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Discovered Schemes</div>
                <div class="metric-value">{discovered_count}</div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="nav-tabs">
            <div id="nav-flow" class="nav-tab {flow_active}" onclick="switchTab('flow')">📊 Application Flow & Sankey</div>
            <div id="nav-jobs" class="nav-tab {jobs_active}" onclick="switchTab('jobs')">💼 Discovered Schemes ({discovered_count})</div>
            <div id="nav-settings" class="nav-tab {settings_active}" onclick="switchTab('settings')">⚙️ Filter Settings</div>
            <div id="nav-diagnostics" class="nav-tab {diag_active}" onclick="switchTab('diagnostics')">🛠 System Diagnostics</div>
        </div>

        <!-- Tab 1: Application Flow & Sankey -->
        <div id="tab-flow" class="tab-content" style="display:{flow_display};">
            <div class="content-card">
                <div class="card-header">
                    <div class="card-title">📊 Multi-Stage Application Sankey Diagram</div>
                    <button onclick="refreshSankey()" class="btn btn-header">🔄 Refresh Plot</button>
                </div>
                <iframe id="sankey-iframe" src="/sankey-embed" width="100%" height="580" style="border:none; border-radius:12px; background:#0f172a;"></iframe>
            </div>

            <div class="content-card">
                <div class="card-header">
                    <div class="card-title">⚡ Active Applications Tracker</div>
                </div>
                <div class="table-responsive">
                    <table class="app-table">
                        <thead>
                            <tr>
                                <th>Company</th>
                                <th>Target Role</th>
                                <th>Latest Stage</th>
                                <th>Pipeline Stage Flow</th>
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

        <!-- Tab 2: Discovered Schemes Directory -->
        <div id="tab-jobs" class="tab-content" style="display:{jobs_display};">
            <div class="content-card">
                <div class="card-header">
                    <div class="card-title">💼 Discovered UK Schemes Directory ({discovered_count})</div>
                    <button onclick="triggerInlineScan()" class="btn btn-primary">⚡ Trigger Scraper Rescan</button>
                </div>

                <div id="terminal-box" class="terminal-box" style="display:none;">
                    <div class="terminal-header">
                        <span>⚡ Live Scraper Terminal Console</span>
                        <span id="scan-tag" style="color:#10b981;">Running...</span>
                    </div>
                    <pre id="terminal-logs" class="terminal-logs">Initializing...</pre>
                </div>

                <input type="text" id="search" onkeyup="filterJobs()" placeholder="🔍 Search by company, role, location, or status ('applied', 'quant', 'software')..." class="search-box">

                <div class="filter-pills">
                    <button class="pill active" onclick="filterPill('all', this)">All Schemes ({discovered_count})</button>

                    <button class="pill" onclick="filterPill('notapplied', this)">⚡ Not Applied ({not_applied_count})</button>
                    <button class="pill" onclick="filterPill('applied', this)">✅ Applied ({applied_count})</button>
                    <button class="pill" onclick="filterPill('software', this)">💻 Software / Dev ({sw_count})</button>
                    <button class="pill" onclick="filterPill('quant', this)">📈 Quant / Trading ({quant_count})</button>
                    <button class="pill" onclick="filterPill('ml', this)">🧠 ML / AI ({ml_count})</button>
                    <button class="pill" onclick="filterPill('cyber', this)">🔒 Cyber / Cloud ({cyber_count})</button>
                </div>

                <div id="job-list">
                    {cards_html}
                </div>
            </div>
        </div>

        <!-- Tab 3: Filter Settings -->
        <div id="tab-settings" class="tab-content" style="display:{settings_display};">
            <div class="content-card" style="max-width:700px; margin:0 auto;">
                <div class="card-header">
                    <div class="card-title">⚙️ Dynamic Scraper Filter Settings</div>
                </div>
                <p style="color:var(--text-muted); font-size:14px; margin-bottom:24px;">Updates filter rules immediately — No git push required!</p>
                <form onsubmit="saveSettings(event)">
                    <div class="form-group">
                        <label>Target Graduation Years (Comma-separated)</label>
                        <input type="text" id="grad_years" value="{grad_years}" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>Excluded Terms / Seniority Keywords (Comma-separated)</label>
                        <input type="text" id="ex_keywords" value="{ex_keywords}" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>Excluded Foreign Locations (Comma-separated)</label>
                        <input type="text" id="ex_locations" value="{ex_locations}" class="form-input">
                    </div>
                    <div class="form-group">
                        <label>My Known Skills Matrix (Comma-separated)</label>
                        <input type="text" id="my_skills" value="{my_skills}" class="form-input">
                    </div>
                    <button type="submit" class="btn-save">💾 Save Filter Settings</button>
                </form>
            </div>
        </div>

        <!-- Tab 4: Diagnostics & System Control -->
        <div id="tab-diagnostics" class="tab-content" style="display:{diag_display};">
            <div class="content-card">
                <div class="card-header">
                    <div class="card-title">🛠 System Diagnostics & Triggers</div>
                    <div style="display:flex; gap:10px;">
                        <button onclick="triggerTestEndpoint('/test-briefing', 'Daily Briefing')" class="btn btn-primary">🔔 Test Briefing</button>
                        <button onclick="triggerTestEndpoint('/test-weekly', 'Weekly Report')" class="btn btn-auto">📈 Test Weekly</button>
                        <button onclick="triggerTestEndpoint('/test-scraper', 'Manual Scraper')" class="btn btn-success">⚡ Test Scraper</button>
                    </div>
                </div>
                <p style="color:var(--text-muted); font-size:14px; margin-bottom:16px;">Live Scraper & System Status Output (`/status` JSON):</p>
                <pre class="code-block">{status_json_formatted}</pre>
            </div>
        </div>
    </div>
</body>
</html>"""

class CleanHandler(http.server.BaseHTTPRequestHandler):
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def process_log_request(self, company, role, link):
        update_google_sheet_via_webhook(company, "Applied", role, link)
        generate_sankey_from_google_sheets(force_refresh=True)

        send_notification(
            title=f"Logged: {company}",
            message=f"Added {company} ({role}) as 'Applied' in Google Sheets.",
            tags="memo,check-mark",
            priority=3,
            sound="chime"
        )


    def do_POST(self):
        if self.path.startswith("/api/log"):
            company = "Unknown"
            role = "Software/Quant Role"
            link = ""

            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            if "company" in query_params:
                company = query_params["company"][0]
            if "role" in query_params:
                role = query_params["role"][0]

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                body_raw = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                if body_raw.strip():
                    try:
                        data = json.loads(body_raw)
                        company = data.get("company", company)
                        role = data.get("role", role)
                        link = data.get("link", link)
                    except Exception:
                        form_params = parse_qs(body_raw)
                        if "company" in form_params:
                            company = form_params["company"][0]
                        if "role" in form_params:
                            role = form_params["role"][0]

            self.process_log_request(company, role, link)

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": f"Logged {company}"}).encode("utf-8"))
            return

        elif self.path == "/api/settings":
            content_length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(content_length).decode("utf-8", errors="ignore")
            try:
                data = json.loads(body_raw)
                current = load_settings()
                current.update(data)
                save_settings(current)

                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Settings updated"}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        clean_path = parsed_url.path

        if clean_path == "/api/log":
            query_params = parse_qs(parsed_url.query)
            company = query_params.get("company", ["Unknown"])[0]
            role = query_params.get("role", ["Software/Quant Role"])[0]
            link = query_params.get("link", [""])[0]

            self.process_log_request(company, role, link)

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": f"Logged {company}"}).encode("utf-8"))
            return

        elif clean_path == "/api/trigger-scan":
            threading.Thread(target=run_all_scrapers, daemon=True).start()

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "message": "Scraper run initiated"}).encode("utf-8"))
            return

        elif clean_path == "/sankey-embed":
            generate_sankey_from_google_sheets()
            if not os.path.exists("sankey_diagram.html"):
                generate_default_sankey()

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open("sankey_diagram.html", "rb") as f:
                self.wfile.write(f.read())
            return

        elif clean_path in ["/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"]:
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "image/svg+xml")
            self.end_headers()
            svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">⚡</text></svg>'
            self.wfile.write(svg_icon.encode("utf-8"))
            return

        elif clean_path in ["/", "/sankey", "/refresh"]:

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html(active_tab="flow").encode("utf-8"))
            return

        elif clean_path == "/jobs":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html(active_tab="jobs").encode("utf-8"))
            return

        elif clean_path == "/settings":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html(active_tab="settings").encode("utf-8"))
            return

        elif clean_path == "/status":
            accept_header = self.headers.get("Accept", "")
            if "text/html" in accept_header:
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(render_unified_dashboard_html(active_tab="diagnostics").encode("utf-8"))
                return
            else:
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))
                return

        elif clean_path == "/test-briefing":
            trigger_daily_briefing()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Triggered test Daily Morning Briefing! Check ntfy on your phone/Mac.".encode("utf-8"))
            return

        elif clean_path == "/test-weekly":
            trigger_weekly_report()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Triggered test Sunday Weekly Funnel Report! Check ntfy on your phone/Mac.".encode("utf-8"))
            return

        elif clean_path == "/test-scraper":
            run_all_scrapers()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Triggered manual scraper run!".encode("utf-8"))
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write("404 Not Found".encode("utf-8"))

def start_web_server():
    with ThreadedHTTPServer(("", PORT), CleanHandler) as httpd:
        print(f"🌍 Threaded Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()