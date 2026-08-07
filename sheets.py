import csv
import time
import io
import requests
import plotly.graph_objects as go
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

def update_google_sheet_via_webhook(company, stage, role="Software/Quant Role", link=""):
    if not GOOGLE_SHEET_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in GOOGLE_SHEET_WEBHOOK_URL:
        return

    payload = {"company": company, "stage": stage, "role": role, "link": link}
    try:
        resp = requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"📊 Auto-updated Google Sheet: {company} -> {stage}")
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
    <title>Trackr - Live Flow</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", sans-serif; background: transparent; color: #1c1c1e; text-align: center; padding: 40px 20px; margin: 0; }
        .card { background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(20px); border-radius: 20px; padding: 36px; max-width: 500px; margin: 30px auto; border: 0.5px solid rgba(0, 0, 0, 0.08); box-shadow: 0 4px 20px rgba(0,0,0,0.04); }
        h1 { color: #007aff; font-size: 22px; margin-top: 15px; margin-bottom: 10px; font-weight: 700; letter-spacing: -0.01em; }
        p { color: #6c6c70; font-size: 14px; line-height: 1.6; }
        .badge { background: rgba(0, 122, 255, 0.12); color: #007aff; border: 0.5px solid rgba(0, 122, 255, 0.25); font-weight: 700; padding: 6px 16px; border-radius: 20px; font-size: 12px; display: inline-block; }
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">Sheet Status: Connected & Fresh</span>
        <h1>0 Applications Logged Yet</h1>
        <p>Your Google Sheet is connected and ready.</p>
        <p>Log your first application or click <b>⚡ Apply & Log ↗</b> on any target scheme to populate your live interactive Sankey flow!</p>
    </div>
</body>
</html>"""
    with open("sankey_diagram.html", "w", encoding="utf-8") as f:
        f.write(html_content)

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

                # Root flow: Applications -> First Stage
                pair0 = ("Applications", stages[0])
                flow_counts[pair0] = flow_counts.get(pair0, 0) + 1
                all_nodes.add("Applications")
                all_nodes.add(stages[0])

                # Subsequent stage flows: stages[i] -> stages[i+1]
                for i in range(len(stages) - 1):
                    src = stages[i]
                    tgt = stages[i + 1]
                    if src != tgt:
                        pair = (src, tgt)
                        flow_counts[pair] = flow_counts.get(pair, 0) + 1
                        all_nodes.add(src)
                        all_nodes.add(tgt)

        if row_count == 0 or not flow_counts:
            generate_default_sankey()
            return

        # Ensure "Applications" is the first node
        node_list = ["Applications"] + [n for n in sorted(list(all_nodes)) if n != "Applications"]
        node_indices = {name: idx for idx, name in enumerate(node_list)}

        sources = [node_indices[src] for (src, tgt) in flow_counts.keys()]
        targets = [node_indices[tgt] for (src, tgt) in flow_counts.keys()]
        values = list(flow_counts.values())

        # Apple System Color Palette (Light Theme Integrated)
        colors = []
        for name in node_list:
            lower = name.lower()
            if name == "Applications":
                colors.append("#007aff")  # Apple System Blue
            elif "offer" in lower or "accepted" in lower:
                colors.append("#34c759")  # Apple System Green
            elif "reject" in lower or "fail" in lower:
                colors.append("#ff3b30")  # Apple System Red
            elif "ghost" in lower:
                colors.append("#8e8e93")  # Apple System Gray
            elif "assessment" in lower or "interview" in lower or "oa" in lower:
                colors.append("#ff9500")  # Apple System Orange
            else:
                colors.append("#5856d6")  # Apple System Indigo

        link_colors = []
        for src, tgt in flow_counts.keys():
            tgt_lower = tgt.lower()
            if "offer" in tgt_lower or "accepted" in tgt_lower:
                link_colors.append("rgba(52, 199, 89, 0.35)")
            elif "reject" in tgt_lower or "fail" in tgt_lower:
                link_colors.append("rgba(255, 59, 48, 0.25)")
            elif "interview" in tgt_lower or "assessment" in tgt_lower or "oa" in tgt_lower:
                link_colors.append("rgba(255, 149, 0, 0.3)")
            else:
                link_colors.append("rgba(0, 122, 255, 0.25)")

        fig = go.Figure(data=[go.Sankey(
            arrangement="snap",
            node=dict(
                pad=26,
                thickness=22,
                line=dict(color="rgba(0, 0, 0, 0.08)", width=1),
                label=node_list,
                color=colors
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
            font_color="#1c1c1e",
            font_family="-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', sans-serif",
            autosize=True,
            height=500,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=20, b=20)
        )
        fig.write_html("sankey_diagram.html")

    except Exception as e:
        print(f"Error generating Sankey diagram: {e}")
        generate_default_sankey()