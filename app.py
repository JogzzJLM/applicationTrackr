import csv
import json
import os
import time
import requests
from bs4 import BeautifulSoup
import plotly.graph_objects as go

# Configuration
NTFY_TOPIC = "jog_hpstream_alerts"  # Your ntfy channel
SEEN_JOBS_FILE = "seen_jobs.json"
CSV_FILE = "applications.csv"

# ==========================================
# MODULE 1: JOB OPENING MONITOR
# ==========================================
def check_job_openings():
    """Scrapes UK OpenTracker / Github lists for new postings."""
    print("Checking for new UK internship openings...")

    # Load previously seen jobs
    seen_jobs = set()
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE, "r") as f:
            seen_jobs = set(json.load(f))

    # Example: Scraping public UK Tech Internships listing / API
    url = "https://raw.githubusercontent.com/pittcsc/Summer2025-Internships/main/README.md"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            lines = response.text.split("\n")
            for line in lines:
                # Look for lines containing UK or Remote positions
                if "United Kingdom" in line or "UK" in line or "London" in line:
                    # Clean line to extract company name/role keyword
                    job_id = line.strip()[:100]
                    if job_id not in seen_jobs:
                        seen_jobs.add(job_id)
                        # Send alert to your phone
                        send_notification(
                            "🚨 New UK Internship Found!",
                            f"Listing updated: {job_id[:80]}..."
                        )

            # Save updated seen jobs
            with open(SEEN_JOBS_FILE, "w") as f:
                json.dump(list(seen_jobs), f)

    except Exception as e:
        print(f"Error checking jobs: {e}")


def send_notification(title, message):
    """Sends push notification via ntfy.sh"""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title}
        )
        print(f"Notification sent: {title}")
    except Exception as e:
        print(f"Failed to send notification: {e}")


# ==========================================
# MODULE 2: SANKEY DIAGRAM GENERATOR
# ==========================================
def generate_sankey():
    """Reads applications.csv and generates a Sankey diagram HTML file."""
    if not os.path.exists(CSV_FILE):
        # Create dummy file if it doesn't exist
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Company", "Stage"])
            writer.writerow(["Google", "Ghosted"])
            writer.writerow(["Meta", "Direct Rejection"])
            writer.writerow(["Amazon", "HR Screening"])
            writer.writerow(["Palantir", "Final Interview"])

    # Count stages
    stages = {}
    with open(CSV_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stage = row["Stage"]
            stages[stage] = stages.get(stage, 0) + 1

    total_applied = sum(stages.values())
    if total_applied == 0:
        return

    # Sankey nodes
    labels = [
        f"Applied ({total_applied})",
        f"Ghosted ({stages.get('Ghosted', 0)})",
        f"Direct Rejection ({stages.get('Direct Rejection', 0)})",
        f"HR Screening ({stages.get('HR Screening', 0)})",
        f"Technical/First Round ({stages.get('First Round', 0)})",
        f"Final Round ({stages.get('Final Round', 0)})",
        f"Offer ({stages.get('Offer', 0)})"
    ]

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=labels,
            color="blue"
        ),
        link=dict(
            source=[0, 0, 0, 3, 4, 5], # indices match labels above
            target=[1, 2, 3, 4, 5, 6],
            value=[
                stages.get('Ghosted', 1),
                stages.get('Direct Rejection', 1),
                stages.get('HR Screening', 1),
                stages.get('First Round', 1),
                stages.get('Final Round', 1),
                stages.get('Offer', 1)
            ]
        )
    )])

    fig.update_layout(title_text="Internship Application Funnel", font_size=12)
    fig.write_html("sankey_diagram.html")
    print("Updated sankey_diagram.html successfully.")


# ==========================================
# MAIN LOOP
# ==========================================
if __name__ == "__main__":
    print("HP Stream Internship Automator Running...")
    send_notification("HP Stream Bot Active", "Internship monitoring and Sankey generator initialized.")
    
    while True:
        check_job_openings()
        generate_sankey()
        # Sleep for 4 hours before next check
        time.sleep(14400)