import csv
import json
import os
import time
import io
import threading
import http.server
import socketserver
import requests
from bs4 import BeautifulSoup
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

# Global Debug Status State
SCRAPER_STATUS = {
    "last_run": "Never",
    "total_seen_jobs": 0,
    "last_new_jobs_found": 0,
    "source_status": {}
}

# Target Keywords for Filtering UK Internship Roles
TARGET_KEYWORDS = ["intern", "internship", "placement", "spring", "graduate", "early talent"]
UK_LOCATION_KEYWORDS = ["london", "uk", "united kingdom", "remote", "hybrid"]

# Public Companies to Monitor via Lever & Greenhouse ATS APIs (Zero API keys required)
GREENHOUSE_COMPANIES = ["deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", "janestreet", "optiver", "canonical"]
LEVER_COMPANIES = ["spotify", "revolut", "checkout"]


# ==========================================
# MODULE 1: EMBEDDED WEB SERVER & DASHBOARD
# ==========================================
def start_web_server():
    """Serves Sankey (/sankey) and Scraper Status (/status) on port 5000."""
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ["/", "/sankey"]:
                self.path = "/sankey_diagram.html"
                return super().do_GET()
            elif self.path == "/status":
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))
                return
            return super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        print(f"🌍 Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()


# ==========================================
# MODULE 2: NOTIFICATION HELPER
# ==========================================
def send_notification(title, message, link=None, tags="briefcase"):
    """Sends ntfy push notification with clickable action link."""
    headers = {"Title": title, "Tags": tags}
    if link:
        headers["Click"] = link

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        print(f"✅ Notification Sent: {title}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


# ==========================================
# MODULE 3: SCRAPER ENGINE SOURCES
# ==========================================
def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"⚠️ Error reading {SEEN_JOBS_FILE}: {e}")
    return set()

def save_seen_jobs(seen_jobs):
    try:
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(list(seen_jobs), f)
    except Exception as e:
        print(f"⚠️ Error saving {SEEN_JOBS_FILE}: {e}")


def scrape_greenhouse_jobs(seen_jobs):
    """Scrapes Greenhouse ATS public APIs for UK intern roles."""
    new_jobs = []
    source_name = "Greenhouse API"
    errors = 0

    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    location = job.get("location", {}).get("name", "")
                    job_url = job.get("absolute_url", "")
                    job_id = f"gh_{company}_{job.get('id')}"

                    if job_id not in seen_jobs:
                        title_lower = title.lower()
                        loc_lower = location.lower()

                        if any(k in title_lower for k in TARGET_KEYWORDS) and any(l in loc_lower for l in UK_LOCATION_KEYWORDS):
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"⚠️ [Greenhouse Scraper Error] {company}: {e}")

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({errors} warnings)"
    return new_jobs


def scrape_lever_jobs(seen_jobs):
    """Scrapes Lever ATS public APIs for UK intern roles."""
    new_jobs = []
    source_name = "Lever API"
    errors = 0

    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                jobs = resp.json()
                for job in jobs:
                    title = job.get("text", "")
                    location = job.get("categories", {}).get("location", "")
                    job_url = job.get("hostedUrl", "")
                    job_id = f"lever_{company}_{job.get('id')}"

                    if job_id not in seen_jobs:
                        title_lower = title.lower()
                        loc_lower = location.lower()

                        if any(k in title_lower for k in TARGET_KEYWORDS) and any(l in loc_lower for l in UK_LOCATION_KEYWORDS):
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"⚠️ [Lever Scraper Error] {company}: {e}")

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({errors} warnings)"
    return new_jobs


def scrape_github_lists(seen_jobs):
    """Scrapes public open-source UK/International internship repositories."""
    new_jobs = []
    source_name = "GitHub Repos"
    urls = [
        "https://raw.githubusercontent.com/simplify-jobs/Summer2026-Internships/dev/README.md",
        "https://raw.githubusercontent.com/pittcsc/Summer2025-Internships/main/README.md"
    ]

    for url in urls:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                lines = resp.text.split("\n")
                for line in lines:
                    if any(l in line.lower() for l in ["london", "united kingdom", "uk"]):
                        # Hash line as job ID
                        job_id = f"ghrepo_{hash(line)}"
                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            # Extract clean text line preview
                            clean_line = line.replace("|", " ").replace("**", "").strip()[:80]
                            new_jobs.append(("GitHub UK Listing", "UK / London", "https://github.com/simplify-jobs/Summer2026-Internships"))
        except Exception as e:
            print(f"⚠️ [GitHub Scraper Error]: {e}")

    SCRAPER_STATUS["source_status"][source_name] = "OK"
    return new_jobs


def scrape_trackr_website(seen_jobs):
    """Scrapes Trackr public listings page."""
    new_jobs = []
    source_name = "Trackr Web"
    url = "https://the-trackr.com"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Parse links or cards containing internship text
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if any(k in text.lower() for k in TARGET_KEYWORDS):
                    job_id = f"trackr_{hash(href)}"
                    if job_id not in seen_jobs:
                        seen_jobs.add(job_id)
                        new_jobs.append((f"Trackr Role: {text[:40]}", "UK", href if href.startswith("http") else f"https://the-trackr.com{href}"))
        SCRAPER_STATUS["source_status"][source_name] = "OK"
    except Exception as e:
        print(f"⚠️ [Trackr Scraper Error]: {e}")
        SCRAPER_STATUS["source_status"][source_name] = f"Error: {e}"

    return new_jobs


# ==========================================
# MODULE 4: MAIN SCRAPER RUNNER
# ==========================================
def run_all_scrapers():
    """Runs all scrapers, saves state, and sends alerts for new roles."""
    print("\n🔍 Running Active UK Internship Scraper Engine...")
    seen_jobs = load_seen_jobs()
    all_new_jobs = []

    # Run each isolated scraper
    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs))
    all_new_jobs.extend(scrape_github_lists(seen_jobs))
    all_new_jobs.extend(scrape_trackr_website(seen_jobs))

    save_seen_jobs(seen_jobs)

    # Update Scraper Status Dashboard
    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["last_new_jobs_found"] = len(all_new_jobs)

    # Send alerts for any newly discovered roles
    if all_new_jobs:
        print(f"🚨 FOUND {len(all_new_jobs)} NEW UK INTERNSHIPS!")
        for title, location, link in all_new_jobs:
            send_notification(
                title=f"🚨 New UK Role: {title}",
                message=f"Location: {location}\nTap to open direct application link!",
                link=link,
                tags="sparkles,uk"
            )
    else:
        print("✅ No new internship listings found this cycle.")


# ==========================================
# MODULE 5: GOOGLE SHEETS SANKEY GENERATOR
# ==========================================
def generate_sankey_from_google_sheets():
    """Fetches data from Google Sheets CSV URL and generates Sankey Diagram."""
    if "YOUR_GOOGLE_SHEET_ID_HERE" in GOOGLE_SHEET_CSV_URL:
        print("⚠️ Note: Google Sheets CSV URL not configured yet. Skipping Sankey generation.")
        return

    try:
        response = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
        if response.status_code != 200:
            return

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
        print("📊 Updated Sankey diagram from Google Sheets.")

    except Exception as e:
        print(f"Error parsing Google Sheet: {e}")


# ==========================================
# MAIN EXECUTION LOOP
# ==========================================
if __name__ == "__main__":
    # Start Web Dashboard
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    time.sleep(1)

    print("\n🚀 ApplicationTrackr Engine Online!")
    send_notification(
        title="🚀 Scraper Engine Online",
        message=f"Monitoring UK Internships.\nView Status: http://{HP_STREAM_TAILSCALE_IP}:{PORT}/status",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/status",
        tags="rocket"
    )

    while True:
        # 1. Run all scrapers
        run_all_scrapers()

        # 2. Update Sankey diagram
        generate_sankey_from_google_sheets()

        # 3. Sleep for 1 hour before next scrape cycle
        time.sleep(3600)