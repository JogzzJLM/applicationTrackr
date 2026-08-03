import csv
import json
import os
import time
import threading
import http.server
import socketserver
import requests
import plotly.graph_objects as go

# Configuration
NTFY_TOPIC = "jog_applicationtrackr_alerts"
SEEN_JOBS_FILE = "seen_jobs.json"
CSV_FILE = "applications.csv"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"  # Your Tailscale IP

# ==========================================
# MODULE 1: EMBEDDED WEB SERVER (FOR SANKEY)
# ==========================================
def start_web_server():
    """Serves the sankey_diagram.html file at http://100.75.135.73:5000"""
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/sankey":
                self.path = "/sankey_diagram.html"
            return super().do_GET()

    # Allow port reuse
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        print(f"🌍 Sankey Web Dashboard live at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()


# ==========================================
# MODULE 2: RICH NOTIFICATIONS (WITH LINKS)
# ==========================================
def send_notification(title, message, link=None, tags="briefcase"):
    """Sends ntfy notification with clickable action links and icons."""
    headers = {
        "Title": title,
        "Tags": tags
    }
    if link:
        # Tapping the notification opens this URL directly
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
# MODULE 3: SANKEY GENERATOR
# ==========================================
def generate_sankey():
    """Reads applications.csv and generates sankey_diagram.html."""
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Company", "Stage"])
            writer.writerow(["Google", "Ghosted"])
            writer.writerow(["Meta", "Direct Rejection"])
            writer.writerow(["Amazon", "HR Screening"])
            writer.writerow(["Palantir", "Final Interview"])
            writer.writerow(["Jane Street", "Offer"])

    stages = {}
    with open(CSV_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stage = row["Stage"]
            stages[stage] = stages.get(stage, 0) + 1

    total_applied = sum(stages.values())

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

    fig.update_layout(title_text="ApplicationTrackr - Internship Funnel", font_size=12)
    fig.write_html("sankey_diagram.html")
    print("📊 Generated updated sankey_diagram.html")


# ==========================================
# MODULE 4: INITIALIZATION SELF-TEST
# ==========================================
def run_initialization_test():
    """Runs a full system test on startup."""
    print("\n🚀 --- STARTING INITIALIZATION SELF-TEST ---")
    
    # 1. Generate test Sankey
    generate_sankey()
    
    # 2. Send test notification with link
    dashboard_url = f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}"
    sample_job_url = "https://the-trackr.com"
    
    send_notification(
        title="🎉 ApplicationTrackr Ready!",
        message=f"System online!\n\nTap to test clickable link (Trackr).\nView Sankey chart at: {dashboard_url}",
        link=sample_job_url,
        tags="rocket,check-mark"
    )
    print("🚀 --- INITIALIZATION TEST COMPLETE ---\n")


# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    # Start Web Server in a background thread
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    # Give web server a second to initialize
    time.sleep(1)

    # Run initial feature self-test
    run_initialization_test()

    # Main Scraper / Monitoring Loop
    while True:
        # Re-generate Sankey diagram periodically
        generate_sankey()
        # Sleep for 4 hours
        time.sleep(14400)