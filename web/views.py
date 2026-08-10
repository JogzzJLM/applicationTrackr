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
                <td><span class="badge {badge_cls}">{a['latest_stage']}</span></td>
                <td style="color:#86868b; font-size:13px;">{pipeline_str}</td>
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
    kb_badges_html = " ".join([f'<span class="badge badge-kb">{p}</span>' for p in kb_phrases])

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
        src_status_html += f'<div style="padding:6px 0; border-bottom:0.5px solid var(--apple-border);"><b>{s_name}:</b> <span style="color:var(--apple-green); font-weight:600;">{s_msg}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>ApplicationTrackr - Apple Command Center</title>

    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">

    <style>
        :root {{
            --apple-bg: #F5F5F7;
            --apple-card: rgba(255, 255, 255, 0.78);
            --apple-card-solid: #ffffff;
            --apple-border: rgba(0, 0, 0, 0.08);
            --apple-glass-border: rgba(255, 255, 255, 0.9);
            --apple-blue: #0071e3;
            --apple-blue-glow: rgba(0, 113, 227, 0.25);
            --apple-green: #34c759;
            --apple-orange: #ff9500;
            --apple-red: #ff3b30;
            --apple-purple: #af52de;
            --apple-indigo: #5856d6;
            --apple-text-main: #1d1d1f;
            --apple-text-sub: #86868b;
            --font-apple: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", sans-serif;
            --font-mono: 'JetBrains Mono', "SF Mono", monospace;
        }}

        * {{ margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color: transparent; }}

        body {{
            background-color: var(--apple-bg);
            background-image: 
                radial-gradient(ellipse at 15% 15%, rgba(0, 113, 227, 0.06) 0%, transparent 45%),
                radial-gradient(ellipse at 85% 85%, rgba(52, 199, 89, 0.05) 0%, transparent 45%),
                radial-gradient(ellipse at 50% 50%, rgba(175, 82, 222, 0.03) 0%, transparent 50%);
            background-attachment: fixed;
            color: var(--apple-text-main);
            font-family: var(--font-apple);
            padding: 20px 16px 40px 16px;
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        .container {{ max-width: 1280px; margin: 0 auto; }}

        /* Lando Norris Style Dynamic Marquee Ticker Tape */
        .marquee-container {{
            background: rgba(0, 113, 227, 0.06);
            border: 0.5px solid rgba(0, 113, 227, 0.18);
            border-radius: 30px;
            padding: 8px 16px;
            margin-bottom: 20px;
            overflow: hidden;
            white-space: nowrap;
            display: flex;
            align-items: center;
        }}

        .marquee-content {{
            display: inline-block;
            animation: marquee 35s linear infinite;
            font-size: 12px;
            font-weight: 700;
            color: var(--apple-blue);
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}

        @keyframes marquee {{
            0% {{ transform: translateX(0%); }}
            100% {{ transform: translateX(-50%); }}
        }}

        /* Apple Glass Header */
        .header {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 20px 26px;
            background: var(--apple-card);
            backdrop-filter: blur(30px) saturate(190%);
            -webkit-backdrop-filter: blur(30px) saturate(190%);
            border-radius: 24px;
            border: 1px solid var(--apple-glass-border);
            box-shadow: 0 10px 40px -10px rgba(0, 0, 0, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
            margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
        }}

        .brand {{ display: flex; align-items: center; gap: 14px; }}
        .brand-logo {{
            width: 44px; height: 44px;
            background: linear-gradient(135deg, #0071e3 0%, #409cff 100%);
            border-radius: 14px;
            display: flex; align-items: center; justify-content: center;
            color: #ffffff; font-size: 22px; font-weight: 800;
            box-shadow: 0 4px 14px var(--apple-blue-glow);
        }}
        .brand-title {{ font-size: 22px; font-weight: 800; color: var(--apple-text-main); letter-spacing: -0.02em; }}
        .brand-sub {{ font-size: 13px; color: var(--apple-text-sub); margin-top: 2px; font-weight: 500; }}

        .server-status-pill {{
            display: inline-flex; align-items: center; gap: 8px;
            padding: 8px 16px; background: rgba(52, 199, 89, 0.12); color: #278a3c;
            border: 0.5px solid rgba(52, 199, 89, 0.3); border-radius: 20px;
            font-size: 13px; font-weight: 700;
        }}
        .status-dot {{
            width: 8px; height: 8px; background: var(--apple-green); border-radius: 50%;
            box-shadow: 0 0 10px var(--apple-green); animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0% {{ transform: scale(0.9); opacity: 0.8; }}
            50% {{ transform: scale(1.2); opacity: 1; }}
            100% {{ transform: scale(0.9); opacity: 0.8; }}
        }}

        /* Apple Bento Grid Stat Cards */
        .bento-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 16px; margin-bottom: 24px;
        }}

        .bento-card {{
            background: var(--apple-card);
            backdrop-filter: blur(25px) saturate(180%);
            -webkit-backdrop-filter: blur(25px) saturate(180%);
            border: 1px solid var(--apple-glass-border);
            border-radius: 20px; padding: 20px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
            transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s ease;
            position: relative; overflow: hidden;
        }}
        .bento-card:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 30px -10px rgba(0, 113, 227, 0.12);
        }}
        .bento-lbl {{ font-size: 11px; font-weight: 800; color: var(--apple-text-sub); text-transform: uppercase; letter-spacing: 0.05em; }}
        .bento-val {{ font-size: 32px; font-weight: 800; color: var(--apple-text-main); margin-top: 6px; font-family: var(--font-apple); letter-spacing: -0.03em; }}

        /* Apple Segmented Control Nav Tabs */
        .apple-nav {{
            display: flex; gap: 6px; background: rgba(230, 230, 235, 0.8);
            backdrop-filter: blur(20px);
            padding: 5px; border-radius: 16px; margin-bottom: 24px; overflow-x: auto;
            border: 0.5px solid rgba(0, 0, 0, 0.06);
        }}

        .apple-tab {{
            flex: 1; padding: 12px 18px; background: transparent; border: none;
            color: var(--apple-text-sub); font-size: 13px; font-weight: 700;
            cursor: pointer; border-radius: 12px; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            whitespace: nowrap; text-align: center; font-family: var(--font-apple);
        }}

        .apple-tab.active {{
            background: #ffffff; color: var(--apple-text-main);
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08); font-weight: 800;
        }}

        /* Interactive Controls & Floating Search Bar */
        .controls-bar {{
            display: flex; flex-direction: column; gap: 14px; margin-bottom: 20px;
        }}

        .search-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}

        .search-box {{ flex: 1; min-width: 280px; position: relative; }}

        .search-input {{
            width: 100%; padding: 14px 16px 14px 44px; background: var(--apple-card);
            backdrop-filter: blur(20px);
            border: 1px solid var(--apple-glass-border); border-radius: 16px; color: var(--apple-text-main);
            font-size: 14px; font-weight: 600; outline: none;
            box-shadow: 0 4px 16px rgba(0,0,0,0.03); font-family: var(--font-apple);
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }}
        .search-input:focus {{
            border-color: var(--apple-blue);
            box-shadow: 0 4px 20px var(--apple-blue-glow);
        }}

        .search-icon {{ position: absolute; left: 16px; top: 50%; transform: translateY(-50%); color: var(--apple-text-sub); font-size: 16px; }}

        .sort-select {{
            padding: 14px 18px; background: var(--apple-card);
            backdrop-filter: blur(20px);
            border: 1px solid var(--apple-glass-border); border-radius: 16px;
            color: var(--apple-text-main); font-size: 13px; font-weight: 700;
            cursor: pointer; outline: none; box-shadow: 0 4px 16px rgba(0,0,0,0.03);
            font-family: var(--font-apple);
        }}

        .pill-filters {{ display: flex; gap: 8px; overflow-x: auto; padding-bottom: 6px; }}
        .pill {{
            padding: 8px 18px; background: rgba(255, 255, 255, 0.7);
            border: 0.5px solid var(--apple-border); border-radius: 20px;
            color: var(--apple-text-sub); font-size: 13px; font-weight: 700;
            cursor: pointer; whitespace: nowrap; transition: all 0.2s ease; font-family: var(--font-apple);
        }}
        .pill.active {{
            background: var(--apple-blue); color: #ffffff; border-color: var(--apple-blue);
            box-shadow: 0 4px 14px var(--apple-blue-glow);
        }}

        /* Apple Bento Cards Grid */
        .jobs-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 18px;
        }}

        .job-card {{
            background: var(--apple-card);
            backdrop-filter: blur(30px) saturate(190%);
            -webkit-backdrop-filter: blur(30px) saturate(190%);
            border: 1px solid var(--apple-glass-border);
            border-radius: 22px; padding: 22px;
            display: flex; flex-direction: column; justify-content: space-between; gap: 12px;
            box-shadow: 0 6px 24px -6px rgba(0, 0, 0, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
            transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s ease;
        }}
        .job-card:hover {{
            transform: translateY(-4px) scale(1.005);
            box-shadow: 0 16px 40px -10px rgba(0, 113, 227, 0.15);
        }}

        .job-card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }}
        .avatar-circle {{
            width: 40px; height: 40px; background: linear-gradient(135deg, #0071e3 0%, #5856d6 100%);
            color: #ffffff; border-radius: 12px; font-weight: 800; font-size: 14px;
            display: flex; align-items: center; justify-content: center; flex-shrink: 0;
            box-shadow: 0 3px 10px rgba(0, 113, 227, 0.25);
        }}
        .company-title {{ font-weight: 800; color: var(--apple-text-main); font-size: 16px; letter-spacing: -0.01em; }}
        .job-card-title {{ font-size: 17px; font-weight: 800; color: var(--apple-blue); line-height: 1.3; letter-spacing: -0.02em; }}

        .match-score-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0; }}
        .match-score-pill {{
            background: rgba(255, 149, 0, 0.12); color: #d97706; border: 0.5px solid rgba(255, 149, 0, 0.3);
            padding: 4px 10px; border-radius: 10px; font-size: 12px; font-weight: 800;
        }}
        .match-bar-bg {{ flex: 1; height: 6px; background: rgba(0, 0, 0, 0.06); border-radius: 10px; overflow: hidden; }}
        .match-bar-fill {{ height: 100%; background: linear-gradient(90deg, #34c759 0%, #0071e3 100%); border-radius: 10px; }}

        .job-card-meta {{ font-size: 12px; color: var(--apple-text-sub); font-weight: 600; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }}
        .job-source-banner {{
            font-size: 11px; color: var(--apple-text-sub); background: rgba(0,0,0,0.03);
            padding: 8px 12px; border-radius: 10px; border: 0.5px solid var(--apple-border);
        }}
        .source-link {{ color: var(--apple-blue); text-decoration: none; word-break: break-all; font-weight: 700; }}

        .badge {{ display: inline-block; padding: 5px 10px; border-radius: 10px; font-size: 11px; font-weight: 800; text-transform: uppercase; }}
        .badge-open {{ background: rgba(52, 199, 89, 0.14); color: #278a3c; }}
        .badge-applied {{ background: rgba(0, 113, 227, 0.14); color: var(--apple-blue); }}
        .badge-closed {{ background: rgba(255, 59, 48, 0.14); color: var(--apple-red); }}
        .badge-source {{ background: rgba(0,0,0,0.05); color: var(--apple-text-sub); font-size: 10px; }}
        .badge-kb {{ background: rgba(0, 113, 227, 0.08); color: var(--apple-blue); border: 0.5px solid rgba(0, 113, 227, 0.2); font-size: 12px; margin: 3px; padding: 6px 12px; border-radius: 12px; font-weight: 700; display: inline-block; }}

        .job-card-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }}

        .apple-btn {{
            padding: 10px 16px; border-radius: 12px; font-size: 12px; font-weight: 700; text-decoration: none;
            display: inline-flex; align-items: center; gap: 6px; cursor: pointer; border: none;
            box-shadow: 0 2px 6px rgba(0,0,0,0.06); transition: transform 0.12s ease, opacity 0.12s ease, background 0.12s ease;
            font-family: var(--font-apple);
        }}
        .apple-btn:active {{ transform: scale(0.95); opacity: 0.85; }}

        .apple-btn-primary {{ background: var(--apple-blue); color: #ffffff; box-shadow: 0 4px 14px var(--apple-blue-glow); }}
        .apple-btn-primary:hover {{ background: #0062c4; }}
        .apple-btn-secondary {{ background: rgba(0,0,0,0.05); color: var(--apple-text-main); border: 0.5px solid var(--apple-border); }}
        .apple-btn-success {{ background: var(--apple-green); color: #ffffff; box-shadow: 0 4px 14px rgba(52, 199, 89, 0.3); }}
        .apple-btn-danger {{ background: rgba(255, 59, 48, 0.12); color: var(--apple-red); border: 0.5px solid rgba(255, 59, 48, 0.25); }}

        .view-section {{ display: none; }}

        iframe {{ border: none; width: 100%; height: 500px; border-radius: 18px; background: #ffffff; border: 1px solid var(--apple-glass-border); }}

        .settings-card {{
            background: var(--apple-card);
            backdrop-filter: blur(25px) saturate(180%);
            -webkit-backdrop-filter: blur(25px) saturate(180%);
            border: 1px solid var(--apple-glass-border);
            border-radius: 22px; padding: 26px; margin-bottom: 24px;
            box-shadow: 0 6px 24px -6px rgba(0, 0, 0, 0.04);
        }}
        .settings-group {{ margin-bottom: 18px; }}
        .settings-label {{ font-weight: 800; margin-bottom: 8px; display: block; color: var(--apple-blue); font-size: 14px; letter-spacing: -0.01em; }}
        .settings-input {{
            width: 100%; padding: 12px 16px; background: #ffffff; border: 1px solid var(--apple-glass-border);
            border-radius: 14px; color: var(--apple-text-main); font-size: 14px; font-weight: 600; outline: none;
            font-family: var(--font-apple); box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }}
        .settings-input:focus {{ border-color: var(--apple-blue); box-shadow: 0 4px 16px var(--apple-blue-glow); }}

        @media (max-width: 768px) {{
            body {{ padding: 12px; }}
            .header {{ flex-direction: column; align-items: flex-start; gap: 14px; }}
            .jobs-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

<div class="container">
    <!-- Lando Norris Dynamic Telemetry Ticker -->
    <div class="marquee-container">
        <div class="marquee-content">
            ⚡ UK TECH SCHEME ENGINE ONLINE &nbsp;&bull;&nbsp; 🚀 69 ACTIVE OPEN SCHEMES INDEXED &nbsp;&bull;&nbsp; 🎯 SKILL MATCH MATRIX RUNNING &nbsp;&bull;&nbsp; 📧 REALTIME GMAIL INBOX WATCHDOG ENGAGED &nbsp;&bull;&nbsp; 📊 SANKEY PIPELINE SYNCED &nbsp;&bull;&nbsp; ⚡ UK TECH SCHEME ENGINE ONLINE &nbsp;&bull;&nbsp; 🚀 69 ACTIVE OPEN SCHEMES INDEXED &nbsp;&bull;&nbsp; 🎯 SKILL MATCH MATRIX RUNNING
        </div>
    </div>

    <!-- Apple Glass Header -->
    <div class="header">
        <div class="brand">
            <div class="brand-logo"></div>
            <div>
                <div class="brand-title">ApplicationTrackr</div>
                <div class="brand-sub">Headless HP Stream Server &bull; UK Maths, Quant & CS Engine</div>
            </div>
        </div>
        <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
            <div class="server-status-pill"><span class="status-dot"></span> HP-STREAM ONLINE ({HP_STREAM_TAILSCALE_IP}:5000)</div>
            <a href="/api/rescan" class="apple-btn apple-btn-primary">⚡ Rescan Now</a>
            <a href="/api/sync-sheet" class="apple-btn apple-btn-secondary">🔄 Sync Sheet</a>
        </div>
    </div>

    <!-- Apple Bento Grid Stat Cards -->
    <div class="bento-grid">
        <div class="bento-card">
            <div class="bento-lbl">TOTAL APPLICATIONS</div>
            <div class="bento-val">{total}</div>
        </div>
        <div class="bento-card">
            <div class="bento-lbl">ACTIVE ROUNDS</div>
            <div class="bento-val" style="color:var(--apple-blue);">{active}</div>
        </div>
        <div class="bento-card">
            <div class="bento-lbl">OFFERS SECURED</div>
            <div class="bento-val" style="color:var(--apple-green);">{offers} 🎉</div>
        </div>
        <div class="bento-card">
            <div class="bento-lbl">REJECTIONS / GHOSTED</div>
            <div class="bento-val" style="color:var(--apple-red);">{rejections}</div>
        </div>
        <div class="bento-card">
            <div class="bento-lbl">CONVERSION RATE</div>
            <div class="bento-val" style="color:var(--apple-purple);">{conv_rate}%</div>
        </div>
        <div class="bento-card">
            <div class="bento-lbl">DISCOVERED SCHEMES</div>
            <div class="bento-val" style="color:var(--apple-orange);">{discovered_count}</div>
        </div>
    </div>

    <!-- Apple Segmented Navigation Bar -->
    <div class="apple-nav">
        <button class="apple-tab {tab_flow}" onclick="switchTab('flow')">📊 Application Flow & Sankey</button>
        <button class="apple-tab {tab_jobs}" onclick="switchTab('jobs')">💼 Discovered Schemes ({discovered_count})</button>
        <button class="apple-tab {tab_settings}" onclick="switchTab('settings')">⚙️ Filter Settings</button>
        <button class="apple-tab {tab_status}" onclick="switchTab('diagnostics')">🛠 System Diagnostics</button>
        <button class="apple-tab {tab_closed}" onclick="switchTab('closed')">🛑 Closed Schemes ({closed_count})</button>
    </div>

    <!-- TAB 1: FLOW & SANKEY -->
    <div id="view-flow" class="view-section" style="{view_flow}">
        <div class="settings-card">
            <h3 style="margin-bottom:16px; font-weight:800; font-size:18px;">📊 Live Application Pipeline Flow</h3>
            <iframe src="/sankey-embed" id="sankey-iframe"></iframe>
        </div>

        <div class="settings-card" style="margin-top:20px;">
            <h3 style="margin-bottom:16px; font-weight:800; font-size:18px;">📋 Detailed Logged Applications</h3>
            <div style="overflow-x:auto;">
                <table style="width:100%; border-collapse:collapse; text-align:left; font-size:14px;">
                    <thead>
                        <tr style="border-bottom:1px solid var(--apple-border); color:var(--apple-text-sub);">
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
    </div>

    <!-- TAB 2: DISCOVERED SCHEMES -->
    <div id="view-jobs" class="view-section" style="{view_jobs}">
        <div class="controls-bar">
            <div class="search-row">
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="job-search" class="search-input" placeholder="Search company, role, location, or source... (Press '/' to search)" onkeyup="filterJobs()">
                </div>

                <select id="sort-select" class="sort-select" onchange="sortJobs()">
                    <option value="newest">🆕 Discovered: Newest First</option>
                    <option value="oldest">⏳ Discovered: Oldest First</option>
                    <option value="match_desc">🎯 Skill Match: Highest First</option>
                    <option value="resp_asc">⚡ Response Time: Fastest First</option>
                    <option value="company_asc">🔤 Company Name: A → Z</option>
                    <option value="title_asc">💼 Role Title: A → Z</option>
                </select>
            </div>

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
            <h3 style="margin-bottom:18px; font-weight:800; font-size:18px;">⚙️ Scraper & Filter Criteria</h3>
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
                    <label style="display:flex; align-items:center; gap:10px; cursor:pointer; font-size:14px; font-weight:600;">
                        <input type="checkbox" name="auto_hide_applied_company_jobs" value="true" {auto_hide_chk} style="width:18px; height:18px; accent-color:var(--apple-blue);">
                        <span>Automatically hide other open listings from companies I have already applied to</span>
                    </label>
                </div>

                <button type="submit" class="apple-btn apple-btn-primary" style="margin-top:14px;">💾 Save Filter Settings</button>
            </form>
        </div>
    </div>

    <!-- TAB 4: SYSTEM DIAGNOSTICS -->
    <div id="view-status" class="view-section" style="{view_status}">
        <div class="settings-card">
            <h3 style="margin-bottom:16px; font-weight:800; font-size:18px;">🛠 System & Scraper Diagnostics</h3>
            <div style="font-size:14px; line-height:1.7; color:var(--apple-text-sub);">
                <div><b>Last Scraper Engine Run:</b> {last_run}</div>
                <div><b>Total Schemes Indexed:</b> {discovered_count}</div>
                <div style="margin-top:12px; font-weight:800; color:var(--apple-text-main);">ATS Source Engine Statuses:</div>
                {src_status_html}
            </div>
        </div>

        <div class="settings-card" style="margin-top:20px;">
            <h3 style="margin-bottom:16px; font-weight:800; font-size:18px;">🧠 AI Self-Learning Knowledge Base ({kb_count} rules)</h3>
            <div style="max-height:200px; overflow-y:auto; padding:14px; background:#ffffff; border-radius:14px; border:1px solid var(--apple-glass-border);">
                {kb_badges_html}
            </div>
        </div>
    </div>

    <!-- TAB 5: CLOSED SCHEMES -->
    <div id="view-closed" class="view-section" style="{view_closed}">
        <div class="settings-card">
            <h3 style="margin-bottom:14px; font-weight:800; font-size:18px;">🛑 Reported & Inactive Schemes Directory ({closed_count})</h3>
            <p style="font-size:13px; color:var(--apple-text-sub); margin-bottom:18px;">
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
        document.querySelectorAll('.apple-tab').forEach(el => el.classList.remove('active'));

        var targetView = document.getElementById('view-' + tabId);
        if (targetView) targetView.style.display = 'block';

        var btns = document.querySelectorAll('.apple-tab');
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

    document.addEventListener('keydown', function(e) {{
        if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {{
            e.preventDefault();
            var searchInput = document.getElementById('job-search');
            if (searchInput) searchInput.focus();
        }}
    }});

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
