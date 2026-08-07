import os
import json
import threading
import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

from config import (
    PORT, SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP, load_settings, save_settings,
    normalize_company, normalize_role, load_hidden_jobs, hide_job, save_hidden_jobs,
    get_scraper_logs, clear_scraper_logs
)
from notifications import send_notification
from sheets import (
    update_google_sheet_via_webhook, generate_sankey_from_google_sheets,
    generate_default_sankey, get_applied_jobs_set, parse_sheet_stats,
    get_detailed_applications
)
from scrapers import run_all_scrapers, load_discovered_jobs, purge_expired_jobs
from scheduler import trigger_daily_briefing, trigger_weekly_report


class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

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

    auto_hide_company = settings.get("auto_hide_applied_company_jobs", False)

    # Filter out hidden jobs, excluded keywords (e.g. data science), and company duplicate policies
    visible_jobs = []
    for j in all_jobs:
        j_id = j.get('id', '')
        comp_norm = normalize_company(j.get('company'))
        title_norm = normalize_role(j.get('title'))
        title_lower = j.get('title', '').lower()

        # 1. Check if manually hidden by 1-click Hide button
        if j_id in hidden_jobs or (comp_norm, title_norm) in hidden_jobs:
            continue

        # 2. Check Exclude Keywords (e.g. "data science", "vice president", "recruiter", etc.)
        if any(kw.lower() in title_lower for kw in settings.get('exclude_keywords', []) if kw.strip()):
            continue

        # 3. Check Applied Status
        is_applied = (comp_norm, title_norm) in applied_jobs
        if not is_applied:
            for (ac, ar) in applied_jobs:
                if ac == comp_norm and ar and (ar in title_norm or title_norm in ar):
                    is_applied = True
                    break

        # 4. Check 1-App Per Company Policy setting
        if auto_hide_company and not is_applied and comp_norm in applied_companies:
            continue

        visible_jobs.append(j)

    discovered_count = len(visible_jobs)


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
    for j in visible_jobs:
        j_id = j.get('id', '')
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
            action_btn = '<span class="ios-btn ios-btn-secondary" style="opacity:0.65; cursor:default;">✓ Logged</span>'
            status_tag = "applied"
            applied_count += 1
        else:
            status_badge = '<span class="badge badge-not-applied">⚡ AVAILABLE</span>'
            action_btn = f'''
            <a href="{j['link']}" target="_blank" rel="noopener noreferrer" onclick="logJob('{comp_js}', '{title_js}')" class="ios-btn ios-btn-success">⚡ Apply & Log ↗</a>
            <button onclick="logJob('{comp_js}', '{title_js}')" class="ios-btn ios-btn-secondary">+ Log Only</button>
            <button onclick="hideJob('{j_id}', this)" class="ios-btn ios-btn-danger" title="Hide listing">🚫 Hide</button>
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
                <a href="{j['link']}" target="_blank" rel="noopener noreferrer" class="ios-btn ios-btn-primary">Apply Direct ↗</a>
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
    auto_hide_checked = "checked" if settings.get("auto_hide_applied_company_jobs", False) else ""

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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="ApplicationTrackr">
    <title>ApplicationTrackr - Unified Command Center</title>

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --ios-bg: #f2f2f7;
            --ios-card: rgba(255, 255, 255, 0.85);
            --ios-card-solid: #ffffff;
            --ios-border: rgba(0, 0, 0, 0.08);
            --ios-blue: #007aff;
            --ios-blue-glow: rgba(0, 122, 255, 0.22);
            --ios-green: #34c759;
            --ios-green-glow: rgba(52, 199, 89, 0.22);
            --ios-orange: #ff9500;
            --ios-red: #ff3b30;
            --ios-indigo: #5856d6;
            --ios-text-primary: #1c1c1e;
            --ios-text-secondary: #6c6c70;
            --ios-text-tertiary: #8e8e93;
            --font-apple: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "SF Pro", "Helvetica Neue", sans-serif;
            --font-mono: "SF Mono", SFMono-Regular, ui-monospace, Menlo, monospace;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}

        html, body {{
            background-color: var(--ios-bg);
            color: var(--ios-text-primary);
            font-family: var(--font-apple);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        body {{
            padding-top: max(16px, env(safe-area-inset-top));
            padding-bottom: max(32px, env(safe-area-inset-bottom));
            padding-left: max(16px, env(safe-area-inset-left));
            padding-right: max(16px, env(safe-area-inset-right));
            background-image: 
                radial-gradient(ellipse at 10% 15%, rgba(0, 122, 255, 0.08) 0%, transparent 45%),
                radial-gradient(ellipse at 90% 20%, rgba(52, 199, 89, 0.06) 0%, transparent 45%);
            background-attachment: fixed;
        }}

        .container {{ max-width: 1180px; margin: 0 auto; }}

        /* Apple Light Navigation Header */
        .header-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--ios-card);
            backdrop-filter: blur(30px) saturate(180%);
            -webkit-backdrop-filter: blur(30px) saturate(180%);
            border: 0.5px solid var(--ios-border);
            padding: 18px 24px;
            border-radius: 20px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04), inset 0 1px 1px rgba(255, 255, 255, 0.9);
            flex-wrap: wrap;
            gap: 14px;
        }}

        .brand-title {{
            font-size: 22px;
            font-weight: 800;
            letter-spacing: -0.02em;
            color: var(--ios-text-primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .brand-subtitle {{
            color: var(--ios-text-secondary);
            font-size: 13px;
            margin-top: 2px;
            font-weight: 500;
        }}

        .server-status-pill {{
            background: rgba(52, 199, 89, 0.12);
            color: #278a3c;
            border: 0.5px solid rgba(52, 199, 89, 0.3);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .status-dot {{
            width: 7px;
            height: 7px;
            background: var(--ios-green);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--ios-green);
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0% {{ transform: scale(0.95); opacity: 0.8; }}
            50% {{ transform: scale(1.15); opacity: 1; }}
            100% {{ transform: scale(0.95); opacity: 0.8; }}
        }}

        .header-actions {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}

        /* Unified Apple Buttons */
        .ios-btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 8px 15px;
            border-radius: 12px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: -0.01em;
            text-decoration: none;
            cursor: pointer;
            border: none;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
            transition: transform 0.15s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.15s ease, background-color 0.15s ease;
            line-height: 1.3;
        }}

        .ios-btn:active {{
            transform: scale(0.96);
            opacity: 0.88;
        }}

        .ios-btn-primary {{
            background: var(--ios-blue);
            color: #ffffff;
            box-shadow: 0 3px 10px rgba(0, 122, 255, 0.28);
        }}

        .ios-btn-primary:hover {{
            background: #0066d6;
            box-shadow: 0 4px 14px rgba(0, 122, 255, 0.38);
        }}

        .ios-btn-success {{
            background: var(--ios-green);
            color: #ffffff;
            box-shadow: 0 3px 10px rgba(52, 199, 89, 0.28);
        }}

        .ios-btn-success:hover {{
            background: #2cb04d;
            box-shadow: 0 4px 14px rgba(52, 199, 89, 0.38);
        }}

        .ios-btn-secondary {{
            background: rgba(120, 120, 128, 0.12);
            color: var(--ios-text-primary);
            border: 0.5px solid rgba(0, 0, 0, 0.08);
        }}

        .ios-btn-secondary:hover {{
            background: rgba(120, 120, 128, 0.18);
        }}

        .ios-btn-danger {{
            background: rgba(255, 59, 48, 0.12);
            color: var(--ios-red);
            border: 0.5px solid rgba(255, 59, 48, 0.2);
        }}

        .ios-btn-danger:hover {{
            background: rgba(255, 59, 48, 0.18);
        }}

        .btn-header {{
            background: rgba(0, 122, 255, 0.1);
            color: var(--ios-blue);
            border: 0.5px solid rgba(0, 122, 255, 0.25);
            padding: 8px 14px;
            border-radius: 12px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s ease;
        }}

        .btn-header:hover {{
            background: var(--ios-blue);
            color: #ffffff;
        }}

        /* Apple Light Metrics Grid */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }}

        .metric-card {{
            background: var(--ios-card);
            backdrop-filter: blur(25px) saturate(180%);
            -webkit-backdrop-filter: blur(25px) saturate(180%);
            border: 0.5px solid var(--ios-border);
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.03), inset 0 1px 1px rgba(255, 255, 255, 0.8);
            padding: 16px;
            border-radius: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}

        .metric-label {{
            font-size: 11px;
            font-weight: 700;
            color: var(--ios-text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.02em;
        }}

        .metric-value {{
            font-size: 26px;
            font-weight: 800;
            letter-spacing: -0.03em;
            margin-top: 6px;
            color: var(--ios-text-primary);
        }}

        .metric-value.active {{ color: var(--ios-blue); }}
        .metric-value.offers {{ color: var(--ios-green); }}
        .metric-value.rejections {{ color: var(--ios-red); }}

        /* Navigation Tabs Segmented Control */
        .nav-tabs {{
            display: flex;
            gap: 6px;
            background: rgba(118, 118, 128, 0.12);
            padding: 5px;
            border-radius: 16px;
            margin-bottom: 20px;
            overflow-x: auto;
            border: 0.5px solid var(--ios-border);
        }}

        .nav-tab {{
            flex: 1;
            text-align: center;
            padding: 10px 16px;
            color: var(--ios-text-secondary);
            font-weight: 600;
            font-size: 13px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            white-space: nowrap;
            user-select: none;
        }}

        .nav-tab:hover {{ color: var(--ios-text-primary); }}
        .nav-tab.active {{ background: #ffffff; color: var(--ios-blue); font-weight: 700; box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08); }}

        /* Content Cards */
        .content-card {{
            background: var(--ios-card);
            backdrop-filter: blur(30px) saturate(180%);
            -webkit-backdrop-filter: blur(30px) saturate(180%);
            border: 0.5px solid var(--ios-border);
            border-radius: 20px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.04), inset 0 1px 1px rgba(255, 255, 255, 0.9);
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            padding-bottom: 14px;
            border-bottom: 0.5px solid var(--ios-border);
        }}

        .card-title {{ font-size: 18px; font-weight: 700; color: var(--ios-text-primary); display: flex; align-items: center; gap: 8px; }}

        /* Active Apps Table */
        .table-responsive {{ overflow-x: auto; margin-top: 10px; }}
        .app-table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        .app-table th {{ background: rgba(118, 118, 128, 0.08); padding: 12px 16px; color: var(--ios-text-secondary); font-weight: 700; font-size: 11px; text-transform: uppercase; border-bottom: 0.5px solid var(--ios-border); }}
        .app-table td {{ padding: 14px 16px; border-bottom: 0.5px solid rgba(0, 0, 0, 0.06); vertical-align: middle; }}
        .empty-table {{ text-align: center; color: var(--ios-text-secondary); padding: 30px; }}

        /* Badges */
        .badge {{ font-size: 11px; padding: 4px 10px; border-radius: 14px; font-weight: 700; display: inline-block; text-transform: uppercase; }}
        .badge-active {{ background: rgba(0, 122, 255, 0.12); color: var(--ios-blue); border: 0.5px solid rgba(0, 122, 255, 0.25); }}
        .badge-offer {{ background: rgba(52, 199, 89, 0.15); color: #278a3c; border: 0.5px solid rgba(52, 199, 89, 0.3); }}
        .badge-rejected {{ background: rgba(255, 59, 48, 0.12); color: var(--ios-red); border: 0.5px solid rgba(255, 59, 48, 0.25); }}
        .badge-ghosted {{ background: rgba(142, 142, 147, 0.15); color: #636366; border: 0.5px solid rgba(142, 142, 147, 0.25); }}
        .badge-applied {{ background: rgba(52, 199, 89, 0.15); color: #278a3c; border: 0.5px solid rgba(52, 199, 89, 0.3); }}
        .badge-not-applied {{ background: rgba(0, 122, 255, 0.12); color: var(--ios-blue); border: 0.5px solid rgba(0, 122, 255, 0.25); }}
        .badge-source {{ background: rgba(142, 142, 147, 0.12); color: #636366; border: 0.5px solid rgba(142, 142, 147, 0.2); }}

        /* Search & Filter Pills */
        .search-box {{
            width: 100%;
            padding: 12px 18px;
            background: #ffffff;
            border: 0.5px solid var(--ios-border);
            border-radius: 14px;
            color: var(--ios-text-primary);
            font-size: 16px;
            font-family: var(--font-apple);
            outline: none;
            margin-bottom: 16px;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.04);
            transition: border-color 0.2s ease;
        }}
        .search-box:focus {{ border-color: var(--ios-blue); }}

        .filter-pills {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 20px; }}
        .pill {{
            padding: 7px 14px;
            background: rgba(118, 118, 128, 0.1);
            color: var(--ios-text-secondary);
            border: 0.5px solid var(--ios-border);
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .pill:hover, .pill.active {{ background: var(--ios-blue); color: #ffffff; border-color: var(--ios-blue); font-weight: 700; box-shadow: 0 2px 8px rgba(0, 122, 255, 0.25); }}

        /* Job Cards */
        .job-card {{
            background: #ffffff;
            border-radius: 16px;
            padding: 18px;
            margin-bottom: 14px;
            border: 0.5px solid var(--ios-border);
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.03);
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }}
        .job-card:hover {{ box-shadow: 0 6px 20px rgba(0, 0, 0, 0.06); transform: translateY(-1px); }}
        .job-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }}
        .company {{ color: var(--ios-text-primary); font-weight: 800; font-size: 17px; }}
        .job-title {{ color: var(--ios-text-primary); font-size: 15px; margin-bottom: 8px; font-weight: 600; line-height: 1.4; }}
        .job-meta {{ color: var(--ios-text-secondary); font-size: 13px; margin-bottom: 8px; }}
        .job-source-info {{ font-size: 12px; color: var(--ios-text-secondary); margin-bottom: 14px; background: rgba(118, 118, 128, 0.08); padding: 6px 12px; border-radius: 8px; border: 0.5px solid var(--ios-border); display: inline-block; }}
        .source-link {{ color: var(--ios-blue); text-decoration: none; font-weight: 600; }}
        .source-link:hover {{ text-decoration: underline; }}
        .job-actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}

        /* Form Controls */
        .form-group {{ margin-bottom: 20px; }}
        .form-group label {{ display: block; color: var(--ios-text-primary); font-weight: 600; font-size: 13px; margin-bottom: 6px; }}
        .form-input {{
            width: 100%;
            padding: 12px 16px;
            border-radius: 12px;
            border: 0.5px solid var(--ios-border);
            background: #ffffff;
            color: var(--ios-text-primary);
            font-size: 16px;
            font-family: var(--font-apple);
            outline: none;
        }}
        .form-input:focus {{ border-color: var(--ios-blue); }}
        .btn-save {{ width: 100%; background: var(--ios-green); color: white; font-weight: 700; padding: 12px; border-radius: 12px; border: none; font-size: 15px; cursor: pointer; box-shadow: 0 3px 12px rgba(52, 199, 89, 0.3); }}

        /* Code Block */
        .code-block {{
            background: #1c1c1e;
            border: 0.5px solid var(--ios-border);
            border-radius: 12px;
            padding: 16px;
            color: #34c759;
            font-family: var(--font-mono);
            font-size: 12px;
            overflow-x: auto;
        }}

        .empty-msg {{ text-align: center; color: var(--ios-text-secondary); padding: 40px; font-size: 15px; }}

        /* iOS Safari & Mobile Layout Responsiveness */
        @media (max-width: 768px) {{
            body {{
                padding: max(12px, env(safe-area-inset-top)) 10px max(24px, env(safe-area-inset-bottom)) 10px;
                -webkit-tap-highlight-color: transparent;
            }}

            .header-bar {{
                padding: 16px;
                border-radius: 16px;
                flex-direction: column;
                align-items: stretch;
                gap: 12px;
            }}

            .brand-title {{
                font-size: 20px;
            }}

            .brand-subtitle {{
                font-size: 12px;
            }}

            .header-actions {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
                width: 100%;
            }}

            .btn-header {{
                justify-content: center;
                min-height: 44px;
                padding: 10px 12px;
                font-size: 13px;
                border-radius: 10px;
            }}

            .metrics-grid {{
                grid-template-columns: repeat(2, 1fr);
                gap: 10px;
                margin-bottom: 16px;
            }}

            .metric-card {{
                padding: 14px 12px;
                border-radius: 14px;
            }}

            .metric-label {{
                font-size: 10px;
                margin-bottom: 4px;
            }}

            .metric-value {{
                font-size: 22px;
            }}

            .nav-tabs {{
                padding: 6px;
                border-radius: 14px;
                gap: 6px;
                margin-bottom: 16px;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
            }}

            .nav-tabs::-webkit-scrollbar {{
                display: none;
            }}

            .nav-tab {{
                padding: 10px 14px;
                font-size: 13px;
                min-height: 44px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
            }}

            .content-card {{
                padding: 16px 14px;
                border-radius: 16px;
                margin-bottom: 16px;
            }}

            .card-header {{
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
                margin-bottom: 14px;
                padding-bottom: 12px;
            }}

            .card-title {{
                font-size: 16px;
            }}

            .search-box {{
                font-size: 16px; /* Prevents iOS Safari auto-zoom on focus */
                padding: 12px 14px;
                border-radius: 12px;
                margin-bottom: 14px;
            }}

            .form-input {{
                font-size: 16px; /* Prevents iOS Safari auto-zoom on focus */
                padding: 12px 14px;
            }}

            .filter-pills {{
                display: flex;
                overflow-x: auto;
                gap: 6px;
                white-space: nowrap;
                -webkit-overflow-scrolling: touch;
                scrollbar-width: none;
                padding-bottom: 6px;
                margin-bottom: 16px;
            }}

            .filter-pills::-webkit-scrollbar {{
                display: none;
            }}

            .pill {{
                padding: 8px 14px;
                font-size: 12px;
                min-height: 38px;
                display: inline-flex;
                align-items: center;
            }}

            .job-card {{
                padding: 16px 14px;
                border-radius: 14px;
                margin-bottom: 12px;
            }}

            .job-header {{
                flex-direction: column;
                align-items: flex-start;
                gap: 6px;
            }}

            .company {{
                font-size: 17px;
            }}

            .job-title {{
                font-size: 14px;
                margin-bottom: 8px;
            }}

            .job-meta {{
                font-size: 12px;
                margin-bottom: 8px;
            }}

            .job-source-info {{
                font-size: 11px;
                padding: 6px 10px;
                margin-bottom: 12px;
                width: 100%;
                word-break: break-all;
            }}

            .job-actions {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
                width: 100%;
            }}

            .btn {{
                min-height: 44px;
                justify-content: center;
                padding: 10px 8px;
                font-size: 12px;
                width: 100%;
            }}

            .btn-primary {{
                grid-column: span 2;
            }}

            .app-table th, .app-table td {{
                padding: 10px 12px;
                font-size: 12px;
                white-space: nowrap;
            }}

            #sankey-iframe {{
                height: 420px !important;
            }}
        }}
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

        function hideJob(jobId, btn) {{
            if (confirm("Hide this job listing from your directory?")) {{
                let card = btn.closest('.job-card');
                if (card) {{
                    card.style.transition = 'all 0.25s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'scale(0.9)';
                    setTimeout(() => card.remove(), 250);
                }}
                fetch('/api/hide-job?id=' + encodeURIComponent(jobId));
            }}
        }}

        function unhideAll() {{
            if (confirm("Restore all hidden job listings?")) {{
                fetch('/api/unhide-all')
                    .then(r => r.json())
                    .then(d => {{
                        alert("✅ All hidden job listings have been restored!");
                        location.reload();
                    }});
            }}
        }}

        function saveSettings(e) {{
            e.preventDefault();
            let body = {{
                grad_years_allowed: document.getElementById('grad_years').value.split(',').map(s=>s.trim()).filter(Boolean),
                exclude_keywords: document.getElementById('ex_keywords').value.split(',').map(s=>s.trim()).filter(Boolean),
                exclude_locations: document.getElementById('ex_locations').value.split(',').map(s=>s.trim()).filter(Boolean),
                my_skills: document.getElementById('my_skills').value.split(',').map(s=>s.trim()).filter(Boolean),
                auto_hide_applied_company_jobs: document.getElementById('auto_hide_company').checked
            }};
            fetch('/api/settings', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(body)
            }})
            .then(r=>r.json())
            .then(d=> {{
                alert('💾 Settings saved successfully! Filters and auto-hide rules updated.');
                location.reload();
            }})
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
                <iframe id="sankey-iframe" src="/sankey-embed" width="100%" height="520" style="border:none; border-radius:16px; background:transparent;"></iframe>

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
                        <label>Excluded Terms / Seniority / Unwanted Keywords (e.g. data science, vice president, sales)</label>
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
                    <div class="form-group" style="display:flex; align-items:center; gap:12px; background:rgba(15,23,42,0.6); padding:14px; border-radius:10px; border:1px solid var(--card-border);">
                        <input type="checkbox" id="auto_hide_company" {auto_hide_checked} style="width:20px; height:20px; cursor:pointer;">
                        <label for="auto_hide_company" style="margin:0; cursor:pointer; color:#f8fafc; font-size:14px;">Auto-hide other job listings from companies I've applied to (Strict 1-App Policy)</label>
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
                        <button onclick="unhideAll()" class="btn btn-danger" style="background:#ef4444; color:white;">🔄 Restore Hidden Jobs ({hidden_count})</button>
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

        elif clean_path == "/api/hide-job":
            query_params = parse_qs(parsed_url.query)
            job_id = query_params.get("id", [""])[0]
            if job_id:
                hide_job(job_id)
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "hidden_id": job_id}).encode("utf-8"))
            return

        elif clean_path == "/api/unhide-all":
            save_hidden_jobs(set())
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": "All hidden jobs restored"}).encode("utf-8"))
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

        elif clean_path == "/api/trigger-scan":
            clear_scraper_logs()
            threading.Thread(target=run_all_scrapers, daemon=True).start()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "message": "Scraper run initiated"}).encode("utf-8"))
            return

        elif clean_path == "/api/scraper-logs":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"logs": get_scraper_logs()}).encode("utf-8"))
            return

        elif clean_path == "/api/purge-expired":
            clear_scraper_logs()
            purged = purge_expired_jobs()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "purged": purged}).encode("utf-8"))
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
    for port in [PORT, 5001, 5055, 8080]:
        try:
            httpd = ThreadedHTTPServer(("", port), CleanHandler)
            print(f"🌍 Threaded Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{port} (Local: http://127.0.0.1:{port})")
            httpd.serve_forever()
            break
        except OSError:
            continue