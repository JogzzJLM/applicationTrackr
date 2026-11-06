import re
from html import unescape
import csv
import time
import io
import requests
import plotly.graph_objects as go
from config import GOOGLE_SHEET_WEBHOOK_URL, GOOGLE_SHEET_CSV_URL, normalize_company, normalize_role, update_scraper_status
from core.storage import DATA_DIR, load_json_safe, atomic_write_json
from datetime import datetime, timezone
import threading
from core.stages import canonical_stage, compact_stage_history, stage_recorded
from core.jobs import role_identity

_SHEET_LOCK = threading.RLock()
_SHEET_SNAPSHOT = str(DATA_DIR / "sheet_snapshot.json")

_SHEET_CSV_CACHE = {"timestamp": 0, "content": ""}

def fetch_google_sheet_csv(force_refresh=False):
    with _SHEET_LOCK:
        return _fetch_google_sheet_csv(force_refresh)


def _fetch_google_sheet_csv(force_refresh=False):
    """Fetches CSV from Google Sheets with 5-second in-memory caching & automatic retry handling."""
    now = time.time()
    if not force_refresh and (now - _SHEET_CSV_CACHE["timestamp"]) < 5 and _SHEET_CSV_CACHE["content"]:
        return _SHEET_CSV_CACHE["content"]

    if not GOOGLE_SHEET_CSV_URL:
        print("⚠️ GOOGLE_SHEET_CSV_URL is not configured.")
        snapshot = load_json_safe(_SHEET_SNAPSHOT, {})
        update_scraper_status("sheet_health", {"ok": False, "using_cached": bool(snapshot.get("content")), "error": "Sheet read not configured"})
        return _SHEET_CSV_CACHE.get("content") or snapshot.get("content", "")

    separator = "&" if "?" in GOOGLE_SHEET_CSV_URL else "?"
    cache_url = f"{GOOGLE_SHEET_CSV_URL}{separator}_cb={int(now * 1000)}"

    for attempt in range(2):
        try:
            resp = requests.get(cache_url, timeout=6)
            columns = next(csv.reader(io.StringIO(resp.text)), []) if resp.status_code == 200 else []
            if resp.status_code == 200 and {'Company', 'Role'}.issubset(set(columns)):
                _SHEET_CSV_CACHE["timestamp"] = now
                _SHEET_CSV_CACHE["content"] = resp.text
                atomic_write_json(_SHEET_SNAPSHOT, {'content': resp.text, 'timestamp': now})
                update_scraper_status('sheet_health', {'ok': True, 'checked_at': datetime.now(timezone.utc).isoformat(), 'using_cached': False})
                return resp.text
        except Exception:
            if attempt == 0:
                time.sleep(0.5)

    snapshot = load_json_safe(_SHEET_SNAPSHOT, {})
    update_scraper_status('sheet_health', {'ok': False, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'using_cached': bool(_SHEET_CSV_CACHE.get('content') or snapshot.get('content')), 'error': 'Sheet read unavailable; retaining last successful snapshot'})
    return _SHEET_CSV_CACHE.get("content") or snapshot.get('content', '')

def sheet_stages(row):
    """Only explicit Stage columns, in numeric order, are application history."""
    stages = sorted(((int(m.group(1)), str(value).strip()) for key, value in row.items()
        if (m := re.fullmatch(r'stage\s*(\d+)', str(key or '').strip(), re.I)) and value and str(value).strip()))
    return compact_stage_history([value for _, value in stages])

def resolve_smart_stage(company, stage, role=None):
    """Compatibility entry point: deduplicate an explicit event; never invent rounds."""
    stage = canonical_stage(stage)
    if not role:
        return stage
    apps = get_detailed_applications(force_refresh=True)
    matches = [app for app in apps if normalize_company(app['company']) == normalize_company(company)
               and role_identity(app.get('role', '')) == role_identity(role)]
    if any(stage_recorded(stage, app.get('stages', [])) for app in matches):
        return None
    return stage


def update_google_sheet_via_webhook(company, stage, role='', link='', resolve_sequential=True,
                                    event_id=None, automatic=False):
    """One serialized write boundary for email, pending review, and manual updates.

    The legacy resolve_sequential argument remains accepted, but no caller can
    bypass duplicate protection or manufacture numbered rounds through it.
    The Sheet-side lock performs the authoritative check against live cells.
    """
    if not GOOGLE_SHEET_WEBHOOK_URL or 'YOUR_WEBHOOK_ID' in GOOGLE_SHEET_WEBHOOK_URL:
        return False
    company, role, stage = str(company).strip(), str(role).strip(), canonical_stage(stage)
    if (not company or not role or not stage or company == 'Application Company'
            or normalize_company(company) == normalize_company(role)
            or any(value.startswith(('=', '+', '-', '@')) for value in (company, role, stage))):
        print('Sheet write refused: a specific company, role and plain-text stage are required.')
        return False
    with _SHEET_LOCK:
        # Published CSV may lag behind direct Sheet edits. Always let the locked
        # Sheet writer check live cells, including when this looks like a duplicate.
        final_stage = stage
        payload = {'company': company, 'stage': final_stage, 'role': role, 'link': link,
                   'automatic': automatic}
        if event_id:
            from hashlib import sha256
            # Stable opaque identity; don't transmit raw mailbox IDs or addresses.
            payload['event_id'] = sha256(event_id.encode()).hexdigest()
        try:
            resp = requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=35)
            if resp.status_code not in (200, 201):
                print(f'⚠️ Google Sheet Webhook returned HTTP {resp.status_code}')
                return False
            if 'text/html' in resp.headers.get('Content-Type', '').lower():
                print('Sheet webhook returned HTML instead of an acknowledgement.')
                return False
            try:
                result = resp.json()
            except ValueError:
                result = None
            acknowledged = (isinstance(result, dict) and not result.get('error')
                and result.get('success') is not False
                and (result.get('success') is True or result.get('status') in ('ok', 'success', 'duplicate')))
            if not acknowledged:
                print('Sheet webhook returned no explicit success acknowledgement.')
                return False
            print(f'✅ Logged to Google Sheet: {company} -> {final_stage}')
            _SHEET_CSV_CACHE['timestamp'] = 0
            fetch_google_sheet_csv(force_refresh=True)
            return True
        except Exception as exc:
            print(f'⚠️ Webhook dispatch failed ({type(exc).__name__}); update remains pending.')
            return False

def get_applied_jobs_set(csv_text=None):
    """Fetches Google Sheet and returns a set of (norm_comp, norm_role) tuples and set of normalized company names."""
    applied_jobs = set()
    applied_companies = set()
    if csv_text is None:
        csv_text = fetch_google_sheet_csv()
    if csv_text:
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                comp = row.get("Company", "").strip()
                role = row.get("Role", "").strip()
                norm_c = normalize_company(comp)
                norm_r = normalize_role(role)
                if norm_c:
                    applied_companies.add(norm_c)
                    if norm_r:
                        applied_jobs.add((norm_c, norm_r))
        except Exception as e:
            print(f"Error reading applied jobs from Google Sheet: {e}")
    return applied_jobs, applied_companies

def get_applied_companies_set(csv_text=None):
    """Fetches Google Sheet and returns a set of lowercased company names already logged."""
    _, applied_companies = get_applied_jobs_set(csv_text=csv_text)
    return applied_companies

def parse_sheet_stats(csv_text=None):
    if csv_text is None:
        csv_text = fetch_google_sheet_csv()
    if not csv_text:
        return {"total": 0, "active": 0, "offers": 0, "rejections": 0}

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        total = 0
        active = 0
        offers = 0
        rejections = 0

        for row in reader:
            stages = sheet_stages(row)

            if str(row.get("Company", "")).strip():
                total += 1
                stages = stages or ["Applied"]
                latest = stages[-1].lower()
                if "offer" in latest:
                    offers += 1
                elif any(word in latest for word in ("reject", "fail", "ghost", "withdraw")):
                    rejections += 1
                else:
                    active += 1

        return {"total": total, "active": active, "offers": offers, "rejections": rejections}
    except Exception as e:
        print(f"Error parsing stats for report: {e}")
        return {"total": 0, "active": 0, "offers": 0, "rejections": 0}

def get_detailed_applications(csv_text=None, force_refresh=False):
    """Fetches Google Sheet CSV and returns a list of detailed application dicts."""
    apps = []
    if csv_text is None:
        csv_text = fetch_google_sheet_csv(force_refresh=force_refresh)
    if csv_text:
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                company = unescape(row.get("Company", "")).strip()
                role = unescape(row.get("Role", "Software/Quant Role")).strip()
                if not company:
                    continue
                stages = sheet_stages(row)

                latest_stage = stages[-1] if stages else "Applied"
                latest_lower = latest_stage.lower()
                if "offer" in latest_lower:
                    status = "Offer 🎉"
                    status_type = "offer"
                elif "reject" in latest_lower or "fail" in latest_lower:
                    status = "Rejected"
                    status_type = "rejected"
                elif "withdraw" in latest_lower:
                    status = "Withdrawn"
                    status_type = "withdrawn"
                elif "ghost" in latest_lower:
                    status = "Ghosted"
                    status_type = "ghosted"
                else:
                    status = "Active"
                    status_type = "active"

                apps.append({
                    "company": company,
                    "role": role,
                    "link": row.get("Link", "").strip(),
                    "latest_stage": latest_stage,
                    "stages": stages,
                    "status": status,
                    "status_type": status_type
                })
        except Exception as e:
            print(f"Error reading detailed applications: {e}")
    return apps

def generate_default_sankey():
    """Renders a clean zero-data state when Google Sheet has 0 applications."""
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: "Trebuchet MS", Verdana, sans-serif; background: transparent; color: #1d1d1f; text-align: center; padding: 60px 20px; margin: 0; }
        .box { background: rgba(255,255,255,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.85); border-radius: 16px; padding: 32px; max-width: 420px; margin: 20px auto; box-shadow: 0 4px 16px rgba(0,0,0,0.03); }
        h2 { font-size: 16px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 6px; color: #50329b; }
        p { color: #86868b; font-size: 13px; line-height: 1.5; margin: 0; }
    </style>
</head>
<body>
    <div class="box">
        <h2>No applications logged yet</h2>
        <p>Log your first application via the Discovered Schemes tab to populate your live flow pipeline.</p>
    </div>
</body>
</html>"""
    with open("sankey_diagram.html", "w", encoding="utf-8") as f:
        f.write(html_content)

def generate_sankey_from_google_sheets(force_refresh=False):
    with _SHEET_LOCK:
        return _generate_sankey_from_google_sheets(force_refresh)


def _generate_sankey_from_google_sheets(force_refresh=False):
    try:
        csv_text = fetch_google_sheet_csv(force_refresh)
        if not csv_text:
            generate_default_sankey()
            return

        csv_data = io.StringIO(csv_text)
        reader = csv.DictReader(csv_data)

        flow_counts = {}
        all_nodes = set()
        node_counts = {}
        row_count = 0

        for row in reader:
            stages = sheet_stages(row) or (["Applied"] if str(row.get("Company", "")).strip() else [])

            if stages:
                row_count += 1
                pair0 = ("Applications", stages[0])
                flow_counts[pair0] = flow_counts.get(pair0, 0) + 1
                node_counts["Applications"] = node_counts.get("Applications", 0) + 1
                node_counts[stages[0]] = node_counts.get(stages[0], 0) + 1
                all_nodes.add("Applications")
                all_nodes.add(stages[0])

                for i in range(len(stages) - 1):
                    src = stages[i]
                    tgt = stages[i + 1]
                    if src != tgt:
                        pair = (src, tgt)
                        flow_counts[pair] = flow_counts.get(pair, 0) + 1
                        node_counts[tgt] = node_counts.get(tgt, 0) + 1
                        all_nodes.add(src)
                        all_nodes.add(tgt)

        if row_count == 0 or not flow_counts:
            generate_default_sankey()
            return

        def node_rank(name):
            n = name.lower()
            if name == "Applications":
                return (0, name)
            elif any(k in n for k in ["offer", "accepted"]):
                return (4, name)
            elif any(k in n for k in ["reject", "fail"]):
                return (3, name)
            elif any(k in n for k in ["ghost"]):
                return (3, name)
            elif "interview" in n:
                return (2, name)
            elif "assessment" in n or "oa" in n or "test" in n:
                return (1.5, name)
            else:
                return (1, name)

        sorted_nodes = sorted(list(all_nodes), key=node_rank)
        node_indices = {name: idx for idx, name in enumerate(sorted_nodes)}

        display_labels = [f"{name} ({node_counts.get(name, 1)})" for name in sorted_nodes]

        sources = [node_indices[src] for (src, tgt) in flow_counts.keys()]
        targets = [node_indices[tgt] for (src, tgt) in flow_counts.keys()]
        values = list(flow_counts.values())

        node_colors = []
        for name in sorted_nodes:
            lower = name.lower()
            if name == "Applications" or lower == "applied":
                node_colors.append("#50329b")
            elif "offer" in lower or "accepted" in lower:
                node_colors.append("#428057")
            elif "reject" in lower or "fail" in lower:
                node_colors.append("#bd4b63")
            elif "ghost" in lower:
                node_colors.append("#8e8e93")
            elif "interview" in lower:
                node_colors.append("#b97629")
            elif "assessment" in lower or "oa" in lower or "test" in lower:
                node_colors.append("#5676b1")
            else:
                node_colors.append("#7a619a")

        link_colors = []
        for src, tgt in flow_counts.keys():
            tgt_lower = tgt.lower()
            if "offer" in tgt_lower or "accepted" in tgt_lower:
                link_colors.append("rgba(66, 128, 87, 0.25)")
            elif "reject" in tgt_lower or "fail" in tgt_lower:
                link_colors.append("rgba(189, 75, 99, 0.22)")
            elif "interview" in tgt_lower or "assessment" in tgt_lower or "oa" in tgt_lower:
                link_colors.append("rgba(185, 118, 41, 0.25)")
            else:
                link_colors.append("rgba(80, 50, 155, 0.19)")

        fig = go.Figure(data=[go.Sankey(
            arrangement="snap",
            node=dict(
                pad=24,
                thickness=20,
                line=dict(color="rgba(0, 0, 0, 0.08)", width=1),
                label=display_labels,
                color=node_colors
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values,
                color=link_colors
            )
        )])

        fig.update_layout(
            font_size=13,
            font_color="#1d1d1f",
            font_family="Trebuchet MS, Verdana, sans-serif",
            autosize=True,

            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=15, r=15, t=15, b=15)
        )

        markup = fig.to_html(include_plotlyjs="cdn", default_height="100%", default_width="100%",
            config={'displayModeBar': False, 'responsive': True, 'scrollZoom': False})
        markup = markup.replace('</head>', '<style>html,body{height:100%;margin:0;overflow:hidden}</style></head>')
        import os
        from pathlib import Path
        temporary = Path('sankey_diagram.html.tmp')
        temporary.write_text(markup, encoding='utf-8')
        os.replace(temporary, 'sankey_diagram.html')

    except Exception as e:
        print(f"Error generating Sankey diagram: {e}")
        generate_default_sankey()

def get_sheet_edit_url():
    from config import GOOGLE_SHEET_EDIT_URL
    from urllib.parse import urlparse
    if GOOGLE_SHEET_EDIT_URL:
        url = GOOGLE_SHEET_EDIT_URL
    else:
        match = re.search(r"/spreadsheets/d/(?!e/)([A-Za-z0-9_-]+)", GOOGLE_SHEET_CSV_URL)
        url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/edit" if match else ''
    parsed = urlparse(url)
    return url if parsed.scheme == 'https' and parsed.hostname == 'docs.google.com' else ''
