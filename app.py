import csv
import json
import os
import time
import io
import threading
import http.server
import socketserver
import requests
import plotly.graph_objects as go

# ==========================================
# CONFIGURATION
# ==========================================
NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

# ⚠️ PASTE YOUR PUBLISHED GOOGLE SHEET CSV URL HERE:
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vS94NpozDGeHO9UPag662CXcH-C5TGN9Y61-nW04VDlPJSZGVTq62E1lRvnXl8gq_CbR5kvMx5XnMFi/pub?output=csv"


# ==========================================
# MODULE 1: EMBEDDED WEB SERVER
# ==========================================
def start_web_server():
    """Serves the sankey_diagram.html file at http://100.75.135.73:5000"""
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/sankey":
                self.path = "/sankey_diagram.html"
            return super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        print(f"🌍 Sankey Web Dashboard live at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()


# ==========================================
# MODULE 2: RICH NOTIFICATIONS
# ==========================================
def send_notification(title, message, link=None, tags="briefcase"):
    """Sends ntfy notification with clickable action links."""
    headers = {"Title": title, "Tags": tags}
    if link:
        headers["Click"] = link

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers
        )
        print(f"✅ Notification sent: {title}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


# ==========================================
# MODULE 3: GOOGLE SHEETS SANKEY GENERATOR
# ==========================================
def generate_sankey_from_google_sheets():
    """Fetches data from Google Sheets CSV URL and generates Sankey Diagram."""
    if "YOUR_GOOGLE_SHEET_ID_HERE" in GOOGLE_SHEET_CSV_URL:
        print("⚠️ Warning: Please replace GOOGLE_SHEET_CSV_URL with your published link!")
        return

    try:
        response = requests.get(GOOGLE_SHEET_CSV_URL)
        if response.status_code != 200:
            print(f"❌ Failed to fetch Google Sheet: Status {response.status_code}")
            return

        # Read CSV data directly from URL response
        csv_data = io.StringIO(response.text)
        reader = csv.DictReader(csv_data)

        stages = {}
        for row in reader:
            stage = row.get("Stage", "").strip()
            if stage:
                stages[stage] = stages.get(stage, 0) + 1

        total_applied = sum(stages.values())
        if total_applied == 0:
            return

        labels = [
            f"Applied ({total_applied})",
            f"Ghosted ({stages.get('Ghosted', 0)})",
            f"Direct Rejection ({stages.get('Direct Rejection', 0)})",
            f"HR Screening ({stages.get('HR Screening', 0)})",
            f"Final Round ({stages.get('Final Interview', 0)})",
            f"Offer ({stages.get('Offer', 0)})"
        ]

        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=15,
                thickness=20,
                line=dict(color="black", width=0.5),
                label=labels,
                color=["#3182bd", "#969696", "#de2d26", "#e6550d", "#756bb1", "#31a354"]
            ),
            link=dict(
                source=[0, 0, 0, 3, 4],
                target=[1, 2, 3, 4, 5],
                value=[
                    stages.get('Ghosted', 1),
                    stages.get('Direct Rejection', 1),
                    stages.get('HR Screening', 1),
                    stages.get('Final Interview', 1),
                    stages.get('Offer', 1)
                ]
            )
        )])

        fig.update_layout(title_text="ApplicationTrackr - Live Google Sheets Funnel", font_size=12)
        fig.write_html("sankey_diagram.html")
        print("📊 Updated Sankey diagram from Google Sheets data.")

    except Exception as e:
        print(f"Error parsing Google Sheet: {e}")


# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    time.sleep(1)

    print("\n🚀 ApplicationTrackr connected to Google Sheets!")
    send_notification(
        title="🎉 ApplicationTrackr Google Sheets Active",
        message=f"Live Dashboard: http://{HP_STREAM_TAILSCALE_IP}:{PORT}",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}",
        tags="google,rocket"
    )

    while True:
        generate_sankey_from_google_sheets()
        # Refresh every 15 minutes
        time.sleep(900)