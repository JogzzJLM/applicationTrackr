import csv
import time
import io
import re
import requests
from config import GOOGLE_SHEET_WEBHOOK_URL, GOOGLE_SHEET_CSV_URL, normalize_company, normalize_role

_SHEET_CSV_CACHE = {"timestamp": 0, "content": ""}

def fetch_google_sheet_csv(force_refresh=False):
    """Fetches CSV from Google Sheets with 5-second in-memory caching & automatic retry handling."""
    now = time.time()
    if not force_refresh and (now - _SHEET_CSV_CACHE["timestamp"]) < 5 and _SHEET_CSV_CACHE["content"]:
        return _SHEET_CSV_CACHE["content"]

    cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(now * 1000)}"

    for attempt in range(2):
        try:
            resp = requests.get(cache_url, timeout=6)
            if resp.status_code == 200 and resp.text.strip():
                _SHEET_CSV_CACHE["timestamp"] = now
                _SHEET_CSV_CACHE["content"] = resp.text
                return resp.text
        except Exception:
            if attempt == 0:
                time.sleep(0.5)

    return _SHEET_CSV_CACHE.get("content", "")

def resolve_smart_stage(company, stage):
    """
    Inspects existing stages logged for `company` in Google Sheets and returns an intelligent
    sequentially numbered stage name (e.g., 'Interview 1' -> 'Interview 2', 'Assessment 1' -> 'Assessment 2').
    """
    apps = get_detailed_applications()
    norm_c = normalize_company(company)

    existing_stages = []
    for app in apps:
        if normalize_company(app["company"]) == norm_c:
            existing_stages = app.get("stages", [])
            break

    if not existing_stages:
        if stage == "Interview":
            return "Interview 1"
        elif stage == "Online Assessment":
            return "Assessment 1"
        return stage

    stage_lower = stage.lower()

    if "interview" in stage_lower:
        count = sum(1 for s in existing_stages if "interview" in s.lower())
        new_num = count + 1
        return f"Interview {new_num}"

    elif "assessment" in stage_lower or "oa" in stage_lower or "test" in stage_lower:
        count = sum(1 for s in existing_stages if any(k in s.lower() for k in ["assessment", "oa", "test", "hackerrank", "codility"]))
        new_num = count + 1
        return f"Assessment {new_num}"

    elif "applied" in stage_lower:
        if any("applied" in s.lower() for s in existing_stages):
            return None

    elif "reject" in stage_lower or "fail" in stage_lower:
        if any("reject" in s.lower() for s in existing_stages):
            return None

    elif "offer" in stage_lower:
        if any("offer" in s.lower() for s in existing_stages):
            return None

    return stage

def update_google_sheet_via_webhook(company, stage, role="Software/Quant Role", link="", resolve_sequential=True):
    if not GOOGLE_SHEET_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in GOOGLE_SHEET_WEBHOOK_URL:
        return

    if resolve_sequential:
        final_stage = resolve_smart_stage(company, stage)
        if final_stage is None:
            print(f"📊 Sheet Notice: Stage '{stage}' for {company} already recorded. Skipping duplicate.")
            return
    else:
        final_stage = stage

    payload = {"company": company, "stage": final_stage, "role": role, "link": link}
    try:
        resp = requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"📊 Auto-updated Google Sheet: {company} -> {final_stage}")
            fetch_google_sheet_csv(force_refresh=True)
    except Exception as e:
        print(f"⚠️ Error sending Webhook to Google Sheet: {e}")

def get_applied_jobs_set():
    """Fetches Google Sheet and returns a set of (norm_comp, norm_role) tuples and set of normalized company names."""
    applied_jobs = set()
    applied_companies = set()
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
        except Exception:
            pass
    return applied_jobs, applied_companies

def get_applied_companies_set():
    """Fetches Google Sheet and returns a set of lowercased company names already logged."""
    _, applied_companies = get_applied_jobs_set()
    return applied_companies

def parse_sheet_stats():
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
            stages = []
            for k, v in row.items():
                if v and v.strip() and k.strip().lower() not in ["company", "role", "link", "date"]:
                    stages.append(v.strip())

            if stages:
                total += 1
                latest = stages[-1].lower()
                if "offer" in latest:
                    offers += 1
                elif "reject" in latest or "fail" in latest or "ghost" in latest:
                    rejections += 1
                else:
                    active += 1

        return {"total": total, "active": active, "offers": offers, "rejections": rejections}
    except Exception as e:
        print(f"Error parsing stats for report: {e}")
        return {"total": 0, "active": 0, "offers": 0, "rejections": 0}

def get_detailed_applications():
    """Fetches Google Sheet CSV and returns a list of detailed application dicts."""
    apps = []
    csv_text = fetch_google_sheet_csv()
    if csv_text:
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            for row in reader:
                company = row.get("Company", "").strip()
                role = row.get("Role", "Software/Quant Role").strip()
                if not company:
                    continue
                stages = []
                for k, v in row.items():
                    if v and v.strip() and k.strip().lower() not in ["company", "role", "link", "date"]:
                        stages.append(v.strip())

                latest_stage = stages[-1] if stages else "Applied"
                latest_lower = latest_stage.lower()
                if "offer" in latest_lower:
                    status = "Offer 🎉"
                    status_type = "offer"
                elif "reject" in latest_lower or "fail" in latest_lower:
                    status = "Rejected"
                    status_type = "rejected"
                elif "ghost" in latest_lower:
                    status = "Ghosted"
                    status_type = "ghosted"
                else:
                    status = "Active"
                    status_type = "active"

                apps.append({
                    "company": company,
                    "role": role,
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
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', -apple-system, sans-serif; background: transparent; color: #1d1d1f; text-align: center; padding: 60px 20px; margin: 0; }
        .box { background: rgba(255,255,255,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.85); border-radius: 16px; padding: 32px; max-width: 420px; margin: 20px auto; box-shadow: 0 4px 16px rgba(0,0,0,0.03); }
        h2 { font-size: 16px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 6px; color: #0071e3; }
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

def get_node_color(name):
    lower = name.lower()
    if name == "Applications" or lower == "applied":
        return "#0071e3"
    elif "offer" in lower or "accepted" in lower:
        return "#34c759"
    elif "reject" in lower or "fail" in lower:
        return "#ff3b30"
    elif "ghost" in lower:
        return "#8e8e93"
    elif "interview" in lower:
        return "#ff9500"
    elif "assessment" in lower or "oa" in lower or "test" in lower:
        return "#af52de"
    else:
        return "#5856d6"

def generate_sankey_from_google_sheets(force_refresh=False):
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
            stages = []
            for col_name, val in row.items():
                if val and val.strip():
                    clean_val = val.strip()
                    if col_name and col_name.strip().lower() in ["company", "role", "link", "date"]:
                        continue
                    stages.append(clean_val)

            if stages:
                row_count += 1
                p0 = ("Applications", stages[0])
                flow_counts[p0] = flow_counts.get(p0, 0) + 1
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

        # Group nodes into columns (layers)
        layer_map = {}
        for name in all_nodes:
            n = name.lower()
            if name == "Applications":
                layer_map[name] = 0
            elif "applied" in n:
                layer_map[name] = 1
            elif "assessment" in n or "oa" in n or "test" in n:
                layer_map[name] = 1 if "1" in n or name == "Assessment" else 2
            elif "interview" in n:
                layer_map[name] = 2 if "2" in n else (1 if "1" in n else 2)
            elif any(k in n for k in ["offer", "accepted", "reject", "fail", "ghost"]):
                layer_map[name] = 3
            else:
                layer_map[name] = 1

        layers = {0: [], 1: [], 2: [], 3: []}
        for name, layer_idx in layer_map.items():
            layers[layer_idx].append(name)

        # Sort layers for clean layout
        for l_idx in layers:
            layers[l_idx].sort(key=lambda n: (0 if "offer" in n.lower() else (1 if "interview" in n.lower() else (2 if "assessment" in n.lower() else (3 if "applied" in n.lower() else 4)))), reverse=False)

        # Remove empty layers
        active_layers = [l for l in sorted(layers.keys()) if len(layers[l]) > 0]

        # Geometry calculations
        view_width = 680
        view_height = 360

        layer_x_positions = {}
        num_layers = len(active_layers)
        for i, l_idx in enumerate(active_layers):
            if num_layers == 1:
                layer_x_positions[l_idx] = view_width / 2
            else:
                margin = 80
                layer_x_positions[l_idx] = margin + i * ((view_width - 2 * margin) / (num_layers - 1))

        node_positions = {}
        gradients_svg = ""
        paths_svg = ""
        nodes_html = ""

        # Calculate node Y positions
        for l_idx in active_layers:
            nodes_in_l = layers[l_idx]
            count_l = len(nodes_in_l)
            x_pos = layer_x_positions[l_idx]

            available_h = view_height - 60
            step_y = available_h / max(count_l, 1)
            start_y = 40 + (step_y / 2 if count_l > 1 else available_h / 2)

            for idx, node in enumerate(nodes_in_l):
                y_pos = start_y + idx * (step_y * 0.7) if count_l > 1 else start_y
                node_positions[node] = (x_pos, y_pos)

        # Generate SVG Gradients and Bezier Flow Paths
        path_id_counter = 0
        for (src, tgt), count in flow_counts.items():
            if src in node_positions and tgt in node_positions:
                path_id_counter += 1
                x1, y1 = node_positions[src]
                x2, y2 = node_positions[tgt]

                c_src = get_node_color(src)
                c_tgt = get_node_color(tgt)
                grad_id = f"flow-grad-{path_id_counter}"

                gradients_svg += f"""
                <linearGradient id="{grad_id}" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="{c_src}" stop-opacity="0.65"/>
                    <stop offset="100%" stop-color="{c_tgt}" stop-opacity="0.65"/>
                </linearGradient>"""

                stroke_w = max(4, min(int((count / row_count) * 36), 28))
                cx1 = x1 + (x2 - x1) * 0.45
                cx2 = x1 + (x2 - x1) * 0.55

                path_d = f"M {x1+40} {y1} C {cx1} {y1}, {cx2} {y2}, {x2-40} {y2}"
                paths_svg += f'<path d="{path_d}" fill="none" stroke="url(#{grad_id})" stroke-width="{stroke_w}" stroke-linecap="round" class="flow-path"><title>{src} → {tgt}: {count} application(s)</title></path>'

        # Generate HTML Node Pills
        for node, (x_pos, y_pos) in node_positions.items():
            c_node = get_node_color(node)
            cnt = node_counts.get(node, 1)
            percent = round((cnt / row_count) * 100) if row_count > 0 else 100

            nodes_html += f"""
            <div class="sankey-node" style="left:{x_pos}px; top:{y_pos}px; background:{c_node};">
                <span class="node-title">{node}</span>
                <span class="node-badge">{cnt}</span>
            </div>"""

        svg_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: transparent;
            width: 100%; height: 100%;
            overflow: hidden;
            user-select: none;
        }}
        .sankey-wrap {{
            position: relative;
            width: 100%; height: 380px;
            display: flex; align-items: center; justify-content: center;
        }}
        svg {{
            width: 100%; height: 100%;
            overflow: visible;
        }}
        .flow-path {{
            transition: stroke-width 0.2s ease, opacity 0.2s ease;
            cursor: pointer;
        }}
        .flow-path:hover {{
            stroke-opacity: 0.95;
            filter: drop-shadow(0 2px 8px rgba(0,113,227,0.3));
        }}
        .sankey-node {{
            position: absolute;
            transform: translate(-50%, -50%);
            padding: 8px 14px;
            border-radius: 980px;
            color: #ffffff;
            font-size: 12px; font-weight: 700;
            display: inline-flex; align-items: center; gap: 8px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.12);
            border: 1.5px solid rgba(255,255,255,0.4);
            backdrop-filter: blur(12px);
            white-space: nowrap;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            cursor: pointer;
        }}
        .sankey-node:hover {{
            transform: translate(-50%, -50%) scale(1.06);
            box-shadow: 0 8px 24px rgba(0,0,0,0.2);
        }}
        .node-badge {{
            background: rgba(255,255,255,0.28);
            color: #ffffff;
            padding: 2px 7px;
            border-radius: 100px;
            font-size: 11px; font-weight: 800;
        }}
    </style>
</head>
<body>
    <div class="sankey-wrap">
        <svg viewBox="0 0 {view_width} {view_height}" preserveAspectRatio="xMidYMid meet">
            <defs>
                {gradients_svg}
            </defs>
            {paths_svg}
        </svg>
        {nodes_html}
    </div>
</body>
</html>"""

        with open("sankey_diagram.html", "w", encoding="utf-8") as f:
            f.write(svg_content)

    except Exception as e:
        print(f"Error generating Sankey diagram: {e}")
        generate_default_sankey()