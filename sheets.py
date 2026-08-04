import csv
import time
import io
import requests
import plotly.graph_objects as go
from config import GOOGLE_SHEET_WEBHOOK_URL, GOOGLE_SHEET_CSV_URL

def update_google_sheet_via_webhook(company, stage, role="Software/Quant Role"):
    if not GOOGLE_SHEET_WEBHOOK_URL or "YOUR_WEBHOOK_ID" in GOOGLE_SHEET_WEBHOOK_URL:
        return

    payload = {"company": company, "stage": stage, "role": role}
    try:
        resp = requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"📊 Auto-updated Google Sheet: {company} -> {stage}")
    except Exception as e:
        print(f"⚠️ Error sending Webhook to Google Sheet: {e}")

def get_applied_companies_set():
    """Fetches Google Sheet and returns a set of lowercased company names already logged."""
    applied = set()
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time() * 1000)}"
        resp = requests.get(cache_url, timeout=10)
        if resp.status_code == 200:
            reader = csv.DictReader(io.StringIO(resp.text))
            for row in reader:
                comp = row.get("Company", "").strip().lower()
                if comp:
                    applied.add(comp)
    except Exception:
        pass
    return applied

def parse_sheet_stats():
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time() * 1000)}"
        resp = requests.get(cache_url, timeout=10)
        if resp.status_code != 200:
            return {"total": 0, "active": 0, "offers": 0, "rejections": 0}

        reader = csv.DictReader(io.StringIO(resp.text))
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

def generate_default_sankey():
    """Renders a clean zero-data state when Google Sheet has 0 applications."""
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>ApplicationTrackr - Live Dashboard</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 20px; }
        .nav { display: flex; gap: 15px; justify-content: center; margin-bottom: 25px; border-bottom: 1px solid #334155; padding-bottom: 15px; }
        .nav a { color: #94a3b8; text-decoration: none; font-weight: bold; padding: 8px 16px; border-radius: 8px; transition: 0.2s; }
        .nav a:hover, .nav a.active { background: #1e293b; color: #38bdf8; }
        .card { background: #1e293b; border-radius: 16px; padding: 40px; max-width: 550px; margin: 40px auto; box-shadow: 0 10px 30px rgba(0,0,0,0.4); border: 1px solid #334155; }
        h1 { color: #38bdf8; font-size: 28px; margin-top: 15px; margin-bottom: 10px; }
        p { color: #94a3b8; font-size: 15px; line-height: 1.6; }
        .badge { background: #10b981; color: #022c22; font-weight: bold; padding: 6px 16px; border-radius: 20px; font-size: 13px; display: inline-block; }
        .btn { display: inline-block; background: #3b82f6; color: white; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; margin-top: 20px; }
        .btn:hover { background: #2563eb; }
    </style>
</head>
<body>
    <div class="nav">
        <a href="/" class="active">📊 Application Flow</a>
        <a href="/jobs">💼 Discovered Schemes</a>
        <a href="/status">⚙️ Diagnostics</a>
    </div>
    <div class="card">
        <span class="badge">Sheet Status: Fresh Start</span>
        <h1>0 Applications Logged</h1>
        <p>Your Google Sheet is currently clean and empty.</p>
        <p>Log your first job application using your <b>Safari Bookmarklet</b> or by editing your Google Sheet!</p>
        <a href="/refresh" class="btn">Refresh Dashboard</a>
    </div>
</body>
</html>"""
    with open("sankey_diagram.html", "w", encoding="utf-8") as f:
        f.write(html_content)

def generate_sankey_from_google_sheets():
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time() * 1000)}"
        response = requests.get(cache_url, timeout=10)
        if response.status_code != 200:
            generate_default_sankey()
            return

        csv_data = io.StringIO(response.text)
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

        node_list = list(all_nodes)
        node_indices = {name: idx for idx, name in enumerate(node_list)}

        sources = [node_indices[src] for (src, tgt) in flow_counts.keys()]
        targets = [node_indices[tgt] for (src, tgt) in flow_counts.keys()]
        values = list(flow_counts.values())

        colors = []
        for name in node_list:
            lower = name.lower()
            if "offer" in lower:
                colors.append("#2ecc71")
            elif "reject" in lower or "fail" in lower:
                colors.append("#e74c3c")
            elif "ghost" in lower:
                colors.append("#95a5a6")
            else:
                colors.append("#3498db")

        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=20,
                thickness=25,
                line=dict(color="black", width=0.5),
                label=node_list,
                color=colors
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values
            )
        )])
        fig.update_layout(
            title_text="<b>ApplicationTrackr - Application Flow</b>",
            font_size=13,
            font_family="Arial, sans-serif",
            autosize=True,
            height=600,
            margin=dict(l=20, r=20, t=50, b=20)
        )
        fig.write_html("sankey_diagram.html")

    except Exception:
        generate_default_sankey()