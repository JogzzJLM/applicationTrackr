from html import escape
import json
import urllib.parse
from config import SCRAPER_STATUS
from core.storage import (
    load_settings, load_hidden_jobs, is_job_hidden,
    load_reported_closed_jobs, load_json_safe,
    load_pending_email_updates
)
from core.kb import load_closed_keywords_kb
from core.normalization import (
    normalize_company, normalize_role, extract_program_type,
    clean_company_display_name, deduplicate_job_list, fuzzy_roles_match
)
from core.scoring import calculate_skill_match_score
from sheets import (
    fetch_google_sheet_csv, parse_sheet_stats, get_detailed_applications,
    get_applied_jobs_set, get_sheet_edit_url
)
from scrapers_engine.audit import load_discovered_jobs
from web.components import render_application_card, render_job_card
from scrapers_engine.quality import REVIEW_FILE, is_actionable_listing

def render_unified_dashboard_html(active_tab="flow"):
    csv_text = fetch_google_sheet_csv()
    stats = parse_sheet_stats(csv_text=csv_text)
    apps = get_detailed_applications(csv_text=csv_text)
    all_jobs = load_discovered_jobs()
    from core.jobs import JobRepository
    job_objects = JobRepository().sync(all_jobs, apps, reconcile=bool(csv_text))
    details = {j.id: j for j in job_objects}
    for listing in all_jobs:
        obj = details.get(listing.get('id')) or next((j for j in job_objects if normalize_company(j.company) == normalize_company(listing.get('company')) and normalize_role(j.title) == normalize_role(listing.get('title'))), None)
        if obj:
            listing['object_id'] = obj.id
            listing['notes'] = obj.notes
            listing['custom_fields'] = obj.custom_fields
    for application in apps:
        obj = next((j for j in job_objects if normalize_company(j.company) == normalize_company(application.get('company')) and normalize_role(j.title) == normalize_role(application.get('role'))), None)
        if obj:
            application['object_id'] = obj.id
            events = [e for e in obj.email_events if not e.get("invalidated")]
            application["last_activity"] = max((e.get("received_at", "") for e in events), default="")
    sheet_url = get_sheet_edit_url()
    ntfy_status = escape(str(SCRAPER_STATUS.get("ntfy", {"ok": False, "error": "No notification receipt recorded yet."})))
    sheet_button = f'<a href="{escape(sheet_url, quote=True)}" target="_blank" rel="noopener noreferrer" class="btn btn-tinted sheet-shortcut" aria-label="Open Google Sheet" title="Open Google Sheet"><span aria-hidden="true">▦</span><span class="desktop-label"> Open Sheet ↗</span></a>' if sheet_url else '<span class="btn btn-ghost sheet-shortcut" aria-label="Sheet URL needed" title="Set GOOGLE_SHEET_EDIT_URL in Portainer"><span aria-hidden="true">▦</span><span class="desktop-label"> Sheet URL needed</span></span>'
    applied_jobs, applied_companies = get_applied_jobs_set(csv_text=csv_text)
    settings = load_settings()
    hidden_jobs = load_hidden_jobs()
    verified_count = sum(j.get('verification', {}).get('state') == 'verified' and is_actionable_listing(j, settings) and not is_job_hidden(j, hidden_jobs) for j in all_jobs)
    review_count = sum(v.get('state') in {'unknown', 'needs_check'} for v in load_json_safe(REVIEW_FILE, {}).values())
    from config import NTFY_BASE_URL, NTFY_TOPIC
    ntfy_topic_url = escape(f'{NTFY_BASE_URL}/{NTFY_TOPIC}', quote=True) if NTFY_TOPIC else ''
    receipt = SCRAPER_STATUS.get('ntfy', {})
    ntfy_status = escape(('Last accepted by ntfy: ' + receipt.get('last_sent', 'No receipt yet')) if receipt.get('ok') else receipt.get('error', 'No receipt yet'))

    from web.pending_view import render_pending_updates
    pending_updates_banner_html = render_pending_updates(load_pending_email_updates())

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
                "id": a.get("object_id", f"applied_{ac_norm}_{ar_norm}"),
                "company": clean_company_display_name(a['company']),
                "title": a['role'],
                "location": "UK / Remote",
                "link": a.get("link") or "#",
                "source": "Logged Application",
                "date_found": "Active Application"
            }
            all_jobs.insert(0, synthetic_job)
            existing_keys.add((ac_norm, ar_norm))

    auto_hide_company = settings.get("auto_hide_applied_company_jobs", False)

    hidden_listing_cards_html = ""
    visible_jobs = []
    for j in all_jobs:
        j_id = j.get('id', '')
        comp_norm = normalize_company(j.get('company'))
        title_norm = normalize_role(j.get('title'))
        title_lower = j.get('title', '').lower()

        if is_job_hidden(j, hidden_jobs):
            hidden_listing_cards_html += render_job_card(j, is_applied=(comp_norm, title_norm) in applied_jobs, is_hidden=True)
            continue

        if j.get('source') != 'Manual' and any(kw.lower() in title_lower for kw in settings.get('exclude_keywords', []) if kw.strip()):
            continue

        is_applied = (comp_norm, title_norm) in applied_jobs
        if not is_applied:
            for (ac, ar) in applied_jobs:
                if ac == comp_norm and ar and fuzzy_roles_match(ar, title_norm, threshold=0.90):
                    is_applied = True
                    break

        if auto_hide_company and not is_applied and comp_norm in applied_companies:
            continue

        if not is_applied and not is_actionable_listing(j, settings):
            continue

        visible_jobs.append(j)

    visible_jobs = deduplicate_job_list(visible_jobs)

    cnt_applied = 0
    cnt_assessment = 0
    cnt_interview = 0
    cnt_offer = 0
    cnt_rejected = 0

    from core.health import engine_health
    health = engine_health()
    engine_label = escape(health["label"])
    connection_pct = health["source_connection_percent"]
    apps.sort(key=lambda a: a.get("last_activity", ""), reverse=True)
    apps_cards_html = ""
    if not apps:
        apps_cards_html = '<div class="empty-state">No applications logged yet. Tap "+ Log App" to track your first role.</div>'
    else:
        for a in apps:
            latest_stage = a.get("latest_stage", "Applied")
            latest_lower = latest_stage.lower()

            if "offer" in latest_lower:
                cnt_offer += 1
            elif "reject" in latest_lower or "fail" in latest_lower:
                cnt_rejected += 1
            elif "interview" in latest_lower:
                cnt_interview += 1
            elif "assessment" in latest_lower or "oa" in latest_lower or "test" in latest_lower:
                cnt_assessment += 1
            else:
                cnt_applied += 1

            apps_cards_html += render_application_card(a)

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
    kb_badges_html = " ".join([f'<span class="kb-tag">{escape(p)}</span>' for p in kb_phrases])

    closed_jobs_list = []
    for c_id, c_job in reported_closed_map.items():
        if not any(j.get('id') == c_id for j in visible_jobs):
            c_job_copy = dict(c_job)
            c_job_copy["company"] = clean_company_display_name(c_job.get("company"))
            closed_jobs_list.append(c_job_copy)

    open_count = len([j for j in visible_jobs if j.get('id') not in closed_ids and j.get('link') not in closed_links])
    discovered_count = sum(j.get("verification", {}).get("state") == "verified" for j in visible_jobs if j.get("id") not in closed_ids and j.get("link") not in closed_links)

    cards_html = ""
    closed_cards_html = ""
    action_items_html = ""
    closing_candidates = []

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
                    if ar and fuzzy_roles_match(ar, title_norm, threshold=0.90):
                        is_applied = True
                        break

        title_lower = title_name.lower()
        prog_type = j.get('program_type') or extract_program_type(title_name)

        if is_reported_closed:
            closed_count += 1
        else:
            if j.get("verification", {}).get("state") == "verified":
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

                from scrapers_engine.verifier import listing_deadline_date
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from config import APP_TIMEZONE
                closing = listing_deadline_date(j)
                if closing and closing >= datetime.now(ZoneInfo(APP_TIMEZONE)).date():
                    closing_candidates.append((closing, j))

        card_markup = render_job_card(j, is_reported_closed=is_reported_closed, is_applied=is_applied, is_hidden=(j_id in hidden_jobs))

        if is_reported_closed:
            closed_cards_html += card_markup
        else:
            cards_html += card_markup

    for c_job in closed_jobs_list:
        closed_count += 1
        closed_cards_html += render_job_card(c_job, is_reported_closed=True, is_applied=False, is_hidden=False)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config import APP_TIMEZONE
    today = datetime.now(ZoneInfo(APP_TIMEZONE)).date()
    closing_candidates.sort(key=lambda item: (item[0], item[1].get('company', ''), item[1].get('title', '')))
    urgent = [(day, job) for day, job in closing_candidates if (day - today).days <= 7]
    next_deadlines = urgent[:6] if urgent else closing_candidates[:3]
    closing_caption = ('Verified, unapplied listings closing in the next 7 days.' if urgent else
                       'None close in the next 7 days. These are your next known deadlines.' if next_deadlines else
                       'No known upcoming deadlines for your visible, unapplied listings. Rolling or unpublished deadlines still appear in Jobs.')
    for closing, job in next_deadlines:
        days = (closing - today).days
        label = 'Today' if days == 0 else 'Tomorrow' if days == 1 else f'In {days} days'
        job_id = escape(str(job.get('id') or ''), quote=True)
        action_items_html += f'<article class="action-item-card" data-closing-id="{job_id}"><div class="closing-head"><strong>{escape(clean_company_display_name(job.get("company")))}</strong><span class="badge badge-papaya">{label}</span></div><p>{escape(str(job.get("title") or "Role"))}</p><div class="closing-date">Closes {closing.strftime("%d %b %Y")}</div><a href="{escape(str(job.get("link") or "#"), quote=True)}" target="_blank" rel="noopener noreferrer" class="btn btn-tinted">Open listing ↗</a></article>'
    closing_section_html = f'<section class="section-card closing-section" id="closing-soon-section"><div class="section-title"><span>Closing soon</span><a href="/jobs" class="btn btn-ghost" onclick="switchTab(\'jobs\');document.getElementById(\'sort-select\').value=\'deadline_asc\';sortJobs();return false;">Browse deadlines ↗</a></div><p class="closing-caption">{closing_caption}</p><div class="action-items-grid">{action_items_html}</div></section>'

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

    applications_toggle_html = ""

    src_status_html = ""
    for s_name, s_msg in SCRAPER_STATUS.get("source_status", {}).items():
        src_status_html += f'<div class="diag-row"><span class="diag-label">{escape(str(s_name))}</span><span class="diag-value">{escape(str(s_msg))}</span></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>ApplicationTrackr // UK Early Career Dashboard</title>
    <meta name="description" content="UK graduate scheme application tracker and job discovery engine">
    <meta name="theme-color" content="#fffdf7">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="ApplicationTrackr">
    <meta name="format-detection" content="telephone=no">
    <link rel="manifest" href="/manifest.webmanifest">
    <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
    <link rel="apple-touch-icon" href="/app-icon.svg">

    <link rel="stylesheet" href="/assets/dashboard.css">
    <script src="/assets/dashboard.js" defer></script>
</head>
<body data-active-tab="{active_tab}">

<!-- Clean Topbar -->
<div class="topbar">
    <div class="topbar-brand">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="4"/><path d="M8 8h8M8 12h5M8 16h3"/></svg>
        <span>Application</span>Trackr
    </div>
    <div class="topbar-right">
        <div class="status-pill" title="{engine_label}"><span class="dot"></span><span class="status-label">{engine_label}</span></div>
        <button onclick="openLogModal()" class="btn btn-filled" aria-label="Log application" title="Log application">+<span class="desktop-label"> Log App</span></button>
        {sheet_button}
        <details class="header-menu">
            <summary class="btn btn-ghost" aria-label="More actions">•••</summary>
            <div class="header-menu-items">
                <button onclick="openLogModal()" class="btn btn-ghost">Log application</button>
                <button onclick="openManualJobModal()" class="btn btn-ghost">Add your own job</button>
                <button onclick="testNotification()" class="btn btn-ghost">Test notification</button>
                <button onclick="syncSheetAndReload()" class="btn btn-ghost">Sync Google Sheet</button>
                <button onclick="mutate('/api/rescan').then(() => location.reload())" class="btn btn-ghost">Rescan listings</button>
                <a href="/profile" class="btn btn-ghost">Mac autofill settings</a>
            </div>
        </details>
    </div>
</div>

<div class="shell">
    <header class="hunt-masthead">
        <div><p class="hunt-kicker">APPLICATIONTRACKR / YOUR PERSONAL INTERNET CORNER</p>
        <h1>The great <br><em>job hunt.</em><span class="masthead-star" aria-hidden="true">✳</span></h1>
        <p class="hunt-caption">Good opportunities. Actual progress. All in one place.</p></div>
        <div class="hunt-sticker" aria-hidden="true">
            <svg viewBox="0 0 120 100" fill="none"><path d="M17 76V31h66l21 18v37H17Z" fill="#b9dcff" stroke="currentColor" stroke-width="3"/><path d="M17 31l22-17h41l3 17M83 31v55M86 48h14" stroke="currentColor" stroke-width="3"/><path d="M44 47v14m17-14v14" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><path d="M44 69q8 9 17 0" stroke="currentColor" stroke-width="3"/><path d="M93 27V9h17v12H93" fill="#ff9f88" stroke="currentColor" stroke-width="3"/></svg>
            <span>YOU'VE GOT<br>POSSIBILITIES.</span>
        </div>
    </header>

    <!-- Hero Stat Cards Grid -->
    <div class="hero-stats-grid">
        <div class="hero-stat-card card-papaya">
            <div class="stat-header">
                <span class="stat-title">Applications Tracked</span>
                <span class="stat-badge badge-papaya">SHEET TRACKING</span>
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
                <span class="stat-badge badge-yellow">DISCOVERY SOURCES</span>
            </div>
            <div class="stat-number">{discovered_count} <span style="font-size:16px; font-weight:700; color:var(--yellow);">SCHEMES</span></div>
            <div class="stat-footer">
                <span class="stat-sub">{intern_count} Intern · {grad_count} Grad · {placement_count} Placement</span>
                <div class="stat-meter"><div style="width: {connection_pct}%; background: var(--yellow);"></div></div>
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
        <div id="pending-email-updates">{pending_updates_banner_html}</div>

        <div class="pipeline-grid">
            <div class="section-card flow-sankey">
                <div class="section-title">
                    <span>Application Flow Pipeline</span>
                    <button onclick="reloadSankeyIframe()" class="btn btn-tinted" style="font-size:12px;">🔄 Reload Diagram</button>
                </div>
                <button class="btn btn-ghost mobile-only" onclick="toggleMobileSection('sankey-mobile-wrap', this, 'Show diagram', 'Hide diagram')">Show diagram</button>
                <div id="sankey-mobile-wrap" class="mobile-collapsible-content open-desktop">
                    <iframe src="/sankey-embed" class="sankey-frame" id="sankey-iframe" title="Application stage flow"></iframe>
                </div>
            </div>

            {closing_section_html}
            <div class="section-card flow-apps">
                <div class="section-title">
                    <span id="application-count">Logged Applications ({total})</span>
                    <button onclick="openLogModal()" class="btn btn-filled" style="font-size:12px;">+ Log App</button>
                </div>

                <div id="application-distribution" style="margin-bottom:16px;">
                    <div class="stage-distribution-row" style="display:flex; justify-content:space-between; font-size:12px; font-weight:700; color:var(--text-tertiary); margin-bottom:6px;">
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

                <div class="application-filters">
                    <input id="application-search" class="search-input" type="search" placeholder="Search company, role or stage" aria-label="Search applications" oninput="filterApplications()">
                    <select id="application-status" class="sort-select" aria-label="Filter applications" onchange="filterApplications()"><option value="active">Active applications</option><option value="all">All applications</option><option value="offer">Offers</option><option value="rejected">Rejected</option><option value="withdrawn">Withdrawn</option><option value="ghosted">Ghosted</option></select>
                </div>
                <p id="application-results" class="results-summary" aria-live="polite"></p>
                <div class="applications-scroll" tabindex="0" role="region" aria-label="Applications — scroll to see more">
                    <div id="applications-grid" class="applications-grid">{apps_cards_html}</div>
                </div>
                <p class="application-scroll-hint">Scroll inside this panel to see more applications.</p>
                {applications_toggle_html}
            </div>
        </div>


    </div>

    <!-- Panel: Discovered Schemes -->
    <div id="view-jobs" class="panel" style="{view_jobs}">
        <h1 class="browse-heading">Find your next opportunity</h1>
        <p class="browse-subtitle">{verified_count} verified listings · {review_count} being checked. <a href="/discovery-report" target="_blank" rel="noopener">Coverage report ↗</a></p>
        <p id="autofill-status" class="browse-subtitle" hidden></p>
        <div class="toolbar">
            <div class="search-wrap">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" /></svg>
                <input type="text" id="job-search" class="search-input" placeholder="Search company, role, or location..." oninput="filterJobs()" aria-label="Search jobs">
            </div>
            <select id="sort-select" aria-label="Sort jobs" class="sort-select" onchange="sortJobs()">
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="match_desc">Best match</option>
                <option value="deadline_asc">Closing soonest</option>
                <option value="deadline_desc">Closing latest</option>
                <option value="company_asc">Company A-Z</option>
                <option value="title_asc">Role A-Z</option>
            </select>
            <select id="job-status" class="sort-select" aria-label="Application status" onchange="filterJobs()">
                <option value="not_applied">Not applied</option><option value="applied">Applied</option><option value="all">All visible statuses</option><option value="hidden">Hidden listings</option>
            </select>
            <button class="btn btn-ghost filter-toggle" aria-expanded="false" aria-controls="mobile-filters" onclick="toggleMobileFilters(this)">Categories</button>
        </div>

        <div id="mobile-filters" class="filter-chips-wrap mobile-filter-panel">
            <div class="filter-group-label">Programme type</div>
            <div class="filter-chips">
                <button class="chip active" data-prog-chip="all" onclick="filterProgram('all', this)">All Programmes ({discovered_count})</button>
                <button class="chip" data-prog-chip="graduate" onclick="filterProgram('graduate', this)">Graduate Schemes (Yr 3+) ({grad_count})</button>
                <button class="chip" data-prog-chip="internship" onclick="filterProgram('internship', this)">Internships (Yr 2 / Summer) ({intern_count})</button>
                <button class="chip" data-prog-chip="placement" onclick="filterProgram('placement', this)">Industrial Placements (Yr 2 / 12-Mo) ({placement_count})</button>
            </div>

            <div class="filter-group-label" style="margin-top:6px;">Area of interest</div>
            <div class="filter-chips">
                <button class="chip active" data-dom-chip="all" onclick="filterDomain('all', this)">All Focuses ({discovered_count})</button>
                <button class="chip" data-dom-chip="quant" onclick="filterDomain('quant', this)">Quant ({quant_count})</button>
                <button class="chip" data-dom-chip="software" onclick="filterDomain('software', this)">Software ({sw_count})</button>
                <button class="chip" data-dom-chip="ml" onclick="filterDomain('ml', this)">ML & AI ({ml_count})</button>
                <button class="chip" data-dom-chip="cyber" onclick="filterDomain('cyber', this)">Cyber ({cyber_count})</button>
            </div>
        </div>

        <div class="results-summary" id="results-summary" role="status" aria-live="polite"></div>
        <div id="jobs-container" class="grid">
            {cards_html}
            {hidden_listing_cards_html}
        </div>
        <div id="jobs-empty" class="empty-jobs" hidden>No jobs match these filters. <button class="btn btn-tinted" onclick="resetJobFilters()">Reset filters</button></div>
        <nav class="job-pagination" aria-label="Job list pages">
            <button id="jobs-prev" class="btn btn-ghost" onclick="changeJobPage(-1)">← Previous</button>
            <span id="jobs-page-label"></span>
            <button id="jobs-next" class="btn btn-tinted" onclick="changeJobPage(1)">Next →</button>
        </nav>
    </div>

    <!-- Panel: Settings -->
    <div id="view-settings" class="panel" style="{view_settings}">
        <div class="section-card">
            <div class="section-title">Filter Configuration</div>
            <form action="/api/settings" method="POST">
                <div class="form-group">
                    <label class="form-label">Skills</label>
                    <input type="text" name="my_skills" class="form-input" value="{escape(my_skills_str, quote=True)}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude keywords</label>
                    <input type="text" name="exclude_keywords" class="form-input" value="{escape(ex_kw, quote=True)}">
                </div>
                <div class="form-group">
                    <label class="form-label">Exclude locations</label>
                    <input type="text" name="exclude_locations" class="form-input" value="{escape(ex_loc, quote=True)}">
                </div>
                <div class="form-group">
                    <label class="form-label">British citizenship (for restricted roles)</label>
                    <select name="british_citizen" class="form-input">
                        <option value="unknown" {'selected' if settings.get('british_citizen') is None else ''}>Not confirmed — restricted roles need review</option>
                        <option value="true" {'selected' if settings.get('british_citizen') is True else ''}>British citizen, including dual nationality</option>
                        <option value="false" {'selected' if settings.get('british_citizen') is False else ''}>Not a British citizen</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Greenhouse companies</label>
                    <textarea name="greenhouse_companies" class="form-input" rows="3">{escape(gh_comp)}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Lever companies</label>
                    <textarea name="lever_companies" class="form-input" rows="2">{escape(lev_comp)}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Ashby companies</label>
                    <textarea name="ashby_companies" class="form-input" rows="2">{escape(ash_comp)}</textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">SmartRecruiters companies</label>
                    <textarea name="smartrecruiters_companies" class="form-input" rows="2">{escape(sr_comp)}</textarea>
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
        <div class="section-card"><div class="section-title">Notification delivery</div><p>{ntfy_status}</p><p>Each new suitable job sends its own alert once it is verified and saved. Tap the alert to open its application page. Existing listings and duplicate sources do not send repeat alerts. Job and deadline summaries arrive at 08:00 and 18:00 UK time. Failed sends are queued for retry.</p><a href="{ntfy_topic_url}" target="_blank" rel="noopener" class="btn btn-ghost">Open ntfy topic ↗</a><button class="btn btn-tinted" onclick="testNotification()">Send test notification</button></div>
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
            <div style="margin-top:18px; font-size:12px; font-weight:800; color:var(--text-tertiary); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px;">Live Source Connectors</div>
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

<nav class="mobile-nav" aria-label="Primary navigation">
    <button class="mobile-tab {tab_flow}" data-tab="flow" onclick="switchTab('flow')"><span class="nav-icon">⌂</span><span>Home</span></button>
    <button class="mobile-tab {tab_jobs}" data-tab="jobs" onclick="switchTab('jobs')"><span class="nav-icon">⌕</span><span>Jobs</span></button>
    <button class="mobile-tab {tab_closed}" data-tab="closed" onclick="switchTab('closed')"><span class="nav-icon">✓</span><span>Closed</span></button>
    <button class="mobile-tab {tab_settings}" data-tab="settings" onclick="switchTab('settings')"><span class="nav-icon">⚙</span><span>Settings</span></button>
    <button class="mobile-tab {tab_status}" data-tab="diagnostics" onclick="switchTab('diagnostics')"><span class="nav-icon">◉</span><span>Status</span></button>
</nav>

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
                <option value="Application Update" hidden disabled>Record email only</option>
                <option value="Interview">Interview</option>
                <option value="Applied" selected>Applied</option>
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

<div id="manual-job-modal" class="modal-backdrop" style="display:none;">
<div class="modal-card"><h2>Add a job</h2><form id="manual-job-form" onsubmit="saveManualJob(event)">
<label class="form-label">Company</label><input class="form-input" name="company" required>
<label class="form-label">Role</label><input class="form-input" name="title" required>
<label class="form-label">Job link (optional)</label><input class="form-input" name="link" type="url">
<label class="form-label">Location</label><input class="form-input" name="location">
<label class="form-label">Notes</label><textarea class="form-input" name="notes"></textarea>
<label class="form-label">Additional information</label><div id="manual-job-fields"></div><button type="button" class="btn btn-ghost" onclick="addExtraField('manual-job-fields')">+ Add field</button>
<button class="btn btn-filled" type="submit">Save job</button><button class="btn btn-ghost" type="button" onclick="document.getElementById('manual-job-modal').style.display='none'">Cancel</button>
</form></div></div>
<div id="job-details-modal" class="modal-backdrop" style="display:none;">
<div class="modal-card"><h2 id="job-details-title">Job details</h2><form id="job-details-form" onsubmit="saveJobDetails(event)">
<input type="hidden" name="id"><label class="form-label">Notes</label><textarea class="form-input" name="notes"></textarea>
<label class="form-label">Additional information</label><div id="job-details-fields"></div><button type="button" class="btn btn-ghost" onclick="addExtraField('job-details-fields')">+ Add field</button>
<div id="job-email-history" style="max-height:150px;overflow:auto"></div>
<button class="btn btn-filled" type="submit">Save details</button><button class="btn btn-ghost" type="button" onclick="document.getElementById('job-details-modal').style.display='none'">Close</button>
</form></div></div>
<script>
    function addExtraField(containerId, key='', value='') {{
        const row=document.createElement('div'); row.style.cssText='display:flex;gap:6px;margin-bottom:8px';
        const name=document.createElement('input');name.className='form-input';name.placeholder='Field name';name.value=key;
        const content=document.createElement('input');content.className='form-input';content.placeholder='Value';content.value=typeof value==='object'?JSON.stringify(value):value;
        const remove=document.createElement('button');remove.type='button';remove.className='btn btn-ghost';remove.textContent='×';remove.onclick=()=>row.remove();
        row.append(name,content,remove);document.getElementById(containerId).append(row);
    }}
    function collectExtraFields(containerId) {{
        const fields=Object.create(null);
        for(const row of document.getElementById(containerId).children) {{
            const inputs=row.querySelectorAll('input');const key=inputs[0].value.trim();
            if(key) fields[key]=inputs[1].value;
        }}
        return JSON.stringify(fields);
    }}
    async function postForm(url, body) {{
        const response = await fetch(url, {{method:'POST',body:new URLSearchParams(body)}});
        const data = await response.json();
        if (!response.ok || data.status === 'error') throw new Error(data.message || 'Request failed');
        return data;
    }}
    async function testNotification() {{
        try {{ const result=await postForm('/api/test-notification',{{}}); alert(result.message); }}
        catch(error) {{ alert(error.message); }}
    }}
    function openManualJobModal() {{ document.getElementById('manual-job-modal').style.display='flex'; if(!document.getElementById('manual-job-fields').children.length) addExtraField('manual-job-fields'); }}
    async function saveManualJob(event) {{
        event.preventDefault();
        try {{ const body=new FormData(event.target);body.set('custom_fields',collectExtraFields('manual-job-fields'));await postForm('/api/manual-job',body); location.reload(); }}
        catch(error) {{ alert(error.message); }}
    }}
    async function openJobDetails(id) {{
        try {{
            const response=await fetch('/api/jobs'); const data=await response.json();
            const job=data.jobs.find(j=>j.id===id); if(!job) throw new Error('Job not found');
            const form=document.getElementById('job-details-form'); form.elements.id.value=id;
            form.elements.notes.value=job.notes||'';
            document.getElementById('job-details-fields').replaceChildren();
            for(const [key,value] of Object.entries(job.custom_fields||{{}})) addExtraField('job-details-fields',key,value);
            document.getElementById('job-details-title').textContent=job.company+' · '+job.title;
            document.getElementById('job-email-history').textContent='Email history: '+(job.email_events||[]).map(e=>(e.received_at||'').slice(0,16).replace('T',' ')+' UTC · '+e.stage+' — '+e.subject+(e.invalidated?' (invalidated: '+e.invalidated_reason+')':e.sheet_synced?' (synced)':' (pending)')).join('\\n');
            document.getElementById('job-details-modal').style.display='flex';
        }} catch(error) {{alert(error.message);}}
    }}
    async function saveJobDetails(event) {{
        event.preventDefault();
        try {{ const body=new FormData(event.target);body.set('custom_fields',collectExtraFields('job-details-fields'));await postForm('/api/job-details',body); location.reload(); }}
        catch(error) {{ alert(error.message); }}
    }}
    var logInterval = null;
    var statusInterval = null;
    var kbInterval = null;


    function toggleMobileSection(id, button, closedLabel, openLabel) {{
        var target = document.getElementById(id);
        if (!target) return;
        var isOpen = target.classList.toggle('open');
        if (button) button.innerText = isOpen ? openLabel : closedLabel;
    }}

    function toggleApplications(button) {{
        var grid = document.getElementById('applications-grid');
        if (!grid) return;
        var collapsed = grid.classList.toggle('is-collapsed');
        if (button) button.innerText = collapsed ? 'Show all {total} applications' : 'Show fewer applications';
    }}

    function toggleMobileFilters(button) {{
        var panel = document.getElementById('mobile-filters');
        if (!panel) return;
        var open = panel.classList.toggle('open');
        if (button) {{ button.innerText = open ? 'Hide categories' : 'Categories'; button.setAttribute('aria-expanded', String(open)); }}
    }}

    var pendingLogId = null;
    function openLogModal() {{
        pendingLogId = null;
        document.querySelector('#modal-stage option[value=\"Application Update\"]').disabled = true;
        document.querySelector('#modal-stage option[value=\"Application Update\"]').hidden = true;
        document.getElementById('modal-stage').value = 'Applied';
        document.getElementById('log-modal').style.display = 'flex';
        document.getElementById('modal-company').focus();
    }}

    function openLogModalForPending(pendingId, comp, stage) {{
        pendingLogId = pendingId;
        document.querySelector('#modal-stage option[value="Application Update"]').disabled = false;
        document.querySelector('#modal-stage option[value="Application Update"]').hidden = false;
        document.getElementById('log-modal').style.display = 'flex';
        document.getElementById('modal-company').value = comp;
        document.getElementById('modal-stage').value = stage || 'Applied';
        document.getElementById('modal-role').focus();
    }}

    function closeLogModal() {{
        document.getElementById('log-modal').style.display = 'none';
    }}

    function mutate(url) {{
        const target = new URL(url, location.origin);
        return fetch(target.pathname, {{method: 'POST', headers: {{'Content-Type':'application/x-www-form-urlencoded'}}, body: target.searchParams}});
    }}
    function reloadSankeyIframe() {{
        var iframe = document.getElementById('sankey-iframe');
        if (iframe) {{
            iframe.src = '/sankey-embed?t=' + Date.now();
        }}
    }}

    function syncSheetAndReload() {{
        mutate('/api/sync-sheet')
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
        if (!title) {{ alert("Please enter a role title."); return; }}
        if (pendingLogId) {{ resolvePendingUpdate(pendingLogId, comp, title, stage); }}
        else {{ logJobWithStage(comp, title, stage); }}
    }}

    function quickUpdateStage(comp, stage, role) {{
        logJobWithStage(comp, role || 'Software/Quant Role', stage);
    }}

    function resolvePendingUpdate(id, comp, role, stage) {{
        mutate('/api/resolve-pending-update?id=' + encodeURIComponent(id) + '&company=' + encodeURIComponent(comp) + '&role=' + encodeURIComponent(role) + '&stage=' + encodeURIComponent(stage))
            .then(r => r.json())
            .then(data => {{
                if (data.status === "error") {{ alert(data.message || "Save failed"); return; }}
                reloadSankeyIframe();
                location.reload();
            }}).catch(error => alert("Save failed: " + error.message));
    }}

    function dismissPendingUpdate(id) {{
        mutate('/api/dismiss-pending-update?id=' + encodeURIComponent(id))
            .then(r => r.json())
            .then(() => location.reload());
    }}

    function logJobWithStage(comp, title, stage) {{
        mutate('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title) + '&stage=' + encodeURIComponent(stage || 'Applied'))
            .then(r => r.json())
            .then(data => {{
                if (data.status === "error") {{ alert(data.message || "Save failed"); return; }}
                reloadSankeyIframe();
                location.reload();
            }}).catch(error => alert("Save failed: " + error.message));
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
        document.body.dataset.activeTab = tabId;
        document.querySelectorAll('.panel').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.tab, .mobile-tab').forEach(el => el.classList.remove('active'));

        var targetView = document.getElementById('view-' + tabId);
        if (targetView) targetView.style.display = 'block';

        document.querySelectorAll('[data-tab="' + tabId + '"]').forEach(el => el.classList.add('active'));

        var tabPaths = {{ flow: '/', jobs: '/jobs', settings: '/settings', diagnostics: '/diagnostics', closed: '/closed' }};
        if (tabPaths[tabId] && window.location.pathname !== tabPaths[tabId]) {{
            history.replaceState(null, '', tabPaths[tabId]);
        }}
        window.scrollTo(0, 0);

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

    function filterJobs(resetPage = true) {{
        var q = document.getElementById('job-search').value.toLowerCase().trim();
        var status = document.getElementById('job-status').value;
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

            var matchesStatus = (status === 'all' && statusData !== 'hidden') || statusData === status;
            var isVisible = matchesSearch && matchesProgram && matchesDomain && matchesStatus;
            c.dataset.matches = isVisible ? 'true' : 'false';
            c.style.display = isVisible ? 'flex' : 'none';
            if (isVisible) visibleCount++;

            if (matchesSearch && matchesDomain && matchesStatus) {{
                progCounts.all++;
                if (progData in progCounts) progCounts[progData]++;
            }}

            if (matchesSearch && matchesProgram && matchesStatus) {{
                domainCounts.all++;
                if (statusData in domainCounts) domainCounts[statusData]++;
                if (catData in domainCounts) domainCounts[catData]++;
            }}
        }});

        updateChipText('data-prog-chip="all"', 'All programmes (' + progCounts.all + ')');
        updateChipText('data-prog-chip="graduate"', 'Graduate (' + progCounts.graduate + ')');
        updateChipText('data-prog-chip="internship"', 'Internships (' + progCounts.internship + ')');
        updateChipText('data-prog-chip="placement"', 'Placements (' + progCounts.placement + ')');

        updateChipText('data-dom-chip="all"', 'All Focuses (' + domainCounts.all + ')');
        updateChipText('data-dom-chip="not_applied"', 'Not Applied (' + domainCounts.not_applied + ')');
        updateChipText('data-dom-chip="applied"', 'Applied (' + domainCounts.applied + ')');
        updateChipText('data-dom-chip="quant"', 'Quant (' + domainCounts.quant + ')');
        updateChipText('data-dom-chip="software"', 'Software (' + domainCounts.software + ')');
        updateChipText('data-dom-chip="ml"', 'ML & AI (' + domainCounts.ml + ')');
        updateChipText('data-dom-chip="cyber"', 'Cyber (' + domainCounts.cyber + ')');

        var tabBtn = document.getElementById('tab-jobs-btn');
        if (tabBtn) {{
            tabBtn.innerText = 'Jobs (' + visibleCount + ')';
        }}
        if (typeof renderJobPage === 'function') renderJobPage(resetPage);
    }}

    function updateChipText(attr, text) {{
        var el = document.querySelector('.chip[' + attr + ']');
        if (el) el.innerText = text;
    }}

    function sortJobs(resetPage = true) {{
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
        filterJobs(resetPage);
    }}

    function escapeHtml(str) {{
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }}

    function clearLiveLogs() {{
        mutate('/api/clear-logs')
            .then(r => r.json())
            .then(() => fetchLiveLogs());
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        sortJobs();
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
            mutate('/api/report-closed?id=' + encodeURIComponent(jobId) + '&link=' + encodeURIComponent(link))
                .then(r => r.json())
                .then(() => location.reload());
        }}
    }}

    function reopenJob(jobId) {{
        mutate('/api/reopen-job?id=' + encodeURIComponent(jobId))
            .then(r => r.json())
            .then(() => location.reload());
    }}

    async function logJob(comp, title, button) {{
        if (button?.disabled) return;
        if (button) {{ button.disabled = true; button.textContent = 'Saving…'; }}
        try {{
            const response = await mutate('/api/mark-applied?company=' + encodeURIComponent(comp) + '&title=' + encodeURIComponent(title));
            const result = await response.json();
            if (!response.ok || result.status !== 'ok') throw new Error(result.message || 'Could not log this application.');
            if (button) button.textContent = 'Applied ✓';
            location.reload();
        }} catch (error) {{
            if (button) {{ button.disabled = false; button.textContent = 'Log applied'; }}
            showNotice(error.message);
        }}
    }}

</script>

<footer class="web-footer"><span class="web-stamp">SMALL WEB.<br>BIG PLANS.</span><span>ApplicationTrackr · A little corner of the internet for your next chapter.</span><span class="web-stamp">BEST VIEWED<br>WITH AMBITION.</span></footer>
</body>
</html>
"""
    return html
