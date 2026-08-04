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

def get_detailed_applications():
    """Fetches Google Sheet CSV and returns a list of detailed application dicts."""
    apps = []
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time() * 1000)}"
        resp = requests.get(cache_url, timeout=10)
        if resp.status_code == 200:
            reader = csv.DictReader(io.StringIO(resp.text))
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
    <title>ApplicationTrackr - Live Flow</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', system-ui, sans-serif; background: #090d16; color: #f8fafc; text-align: center; padding: 40px 20px; margin: 0; }
        .card { background: rgba(19, 28, 46, 0.75); backdrop-filter: blur(16px); border-radius: 20px; padding: 40px; max-width: 500px; margin: 40px auto; border: 1px solid rgba(255, 255, 255, 0.08); box-shadow: 0 10px 30px rgba(0,0,0,0.4); }
        h1 { color: #38bdf8; font-size: 22px; margin-top: 15px; margin-bottom: 10px; font-weight: 800; }
        p { color: #94a3b8; font-size: 14px; line-height: 1.6; }
        .badge { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.3); font-weight: 700; padding: 6px 16px; border-radius: 20px; font-size: 12px; display: inline-block; }
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">Sheet Status: Fresh Start</span>
        <h1>0 Applications Logged</h1>
        <p>Your Google Sheet is currently clean and empty.</p>
        <p>Log your first job application using the <b>Safari Smart Auto-Apply Bookmarklet</b> or from the <b>Discovered Schemes</b> tab to see your live Sankey flow!</p>
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

                # Root flow: Applications -> First Stage (e.g. Applications -> Applied)
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

        colors = []
        for name in node_list:
            lower = name.lower()
            if name == "Applications":
                colors.append("#818cf8")
            elif "offer" in lower:
                colors.append("#10b981")
            elif "reject" in lower or "fail" in lower:
                colors.append("#ef4444")
            elif "ghost" in lower:
                colors.append("#64748b")
            elif "assessment" in lower or "interview" in lower or "oa" in lower:
                colors.append("#f59e0b")
            else:
                colors.append("#38bdf8")

        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=24,
                thickness=20,
                line=dict(color="rgba(255, 255, 255, 0.2)", width=1),
                label=node_list,
                color=colors
            ),
            link=dict(
                source=sources,
                target=targets,
                value=values,
                color="rgba(56, 189, 248, 0.25)"
            )
        )])

        fig.update_layout(
            title_text="<b>Application Trackr - Visual Application Pipeline</b>",
            font_size=13,
            font_color="#f8fafc",
            font_family="Plus Jakarta Sans, sans-serif",
            autosize=True,
            height=540,
            paper_bgcolor="#090d16",
            plot_bgcolor="#090d16",
            margin=dict(l=20, r=20, t=50, b=20)
        )
        fig.write_html("sankey_diagram.html")

    except Exception as e:
        print(f"Error generating Sankey diagram: {e}")
        generate_default_sankey()