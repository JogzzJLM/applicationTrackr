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

def parse_sheet_stats():
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time())}"
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
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=["Applied (0)", "Ghosted (0)", "Direct Rejection (0)", "HR Screening (0)", "Final Round (0)", "Offer (0)"],
            color=["#3182bd", "#969696", "#de2d26", "#e6550d", "#756bb1", "#31a354"]
        ),
        link=dict(source=[0], target=[1], value=[0])
    )])
    fig.update_layout(title_text="ApplicationTrackr - Waiting for Google Sheets Data", font_size=12)
    fig.write_html("sankey_diagram.html")

def generate_sankey_from_google_sheets():
    try:
        cache_url = f"{GOOGLE_SHEET_CSV_URL}&_cb={int(time.time())}"
        response = requests.get(cache_url, timeout=10)
        if response.status_code != 200:
            generate_default_sankey()
            return

        csv_data = io.StringIO(response.text)
        reader = csv.DictReader(csv_data)

        flow_counts = {}
        all_nodes = set()

        for row in reader:
            stages = []
            for col_name, val in row.items():
                if val and val.strip():
                    clean_val = val.strip()
                    if col_name and col_name.strip().lower() in ["company", "role", "link", "date"]:
                        continue
                    stages.append(clean_val)

            for i in range(len(stages) - 1):
                src = stages[i]
                tgt = stages[i + 1]
                if src != tgt:
                    pair = (src, tgt)
                    flow_counts[pair] = flow_counts.get(pair, 0) + 1
                    all_nodes.add(src)
                    all_nodes.add(tgt)

        if not flow_counts:
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
            node=dict(pad=15, thickness=20, label=node_list, color=colors),
            link=dict(source=sources, target=targets, value=values)
        )])
        fig.update_layout(title_text="ApplicationTrackr - Multi-Round Application Flow", font_size=12)
        fig.write_html("sankey_diagram.html")

    except Exception:
        generate_default_sankey()