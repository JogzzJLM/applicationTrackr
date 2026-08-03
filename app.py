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
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/YOUR_GOOGLE_SHEET_ID_HERE/pub?output=csv"

# Global Debug Status State
SCRAPER_STATUS = {
    "last_run": "Never",
    "total_seen_jobs": 0,
    "last_new_jobs_found": 0,
    "source_status": {}
}

# ==========================================
# TAILORED FILTERING CRITERIA (Maths + CS)
# ==========================================

# 1. EXCLUSIONS (Instantly drop non-technical or senior roles)
EXCLUDE_KEYWORDS = [
    "vice president", "vp", "director", "head of", "principal", "senior manager",
    "sales development", "account executive", "account manager", "sdr", "bdr",
    "recruiter", "recruitment", "human resources", "marketing", "legal", "counsel",
    "payroll", "facilities", "receptionist", "executive assistant", "office manager"
]

# 2. OPPORTUNITY TYPES (Year 2 Maths & CS Focus)
LEVEL_KEYWORDS = [
    "intern", "internship", "placement", "industrial placement", "sandwich",
    "spring week", "insight week", "insight programme", "graduate", "grad",
    "early talent", "early career", "trainee"
]

# 3. ROLE DOMAINS (Software, Quant, ML/AI, Cyber, FinTech)
ROLE_KEYWORDS = [
    "software", "developer", "engineer", "engineering", "backend", "fullstack",
    "full-stack", "full stack", "systems", "quant", "quantitative", "trader",
    "trading", "research", "machine learning", "ml", "ai", "artificial intelligence",
    "data science", "data scientist", "data engineer", "cyber", "security",
    "cloud", "devops", "infrastructure", "technology", "tech"
]

# 4. LOCATION PREFERENCES
LOCATION_KEYWORDS = [
    "london", "birmingham", "oxford", "aylesbury", "west midlands", "remote",
    "uk", "united kingdom", "cambridge", "manchester", "edinburgh", "bristol"
]

# 5. SPECIAL INTERNATIONAL EXCEPTIONS (Cool Tech/Quant/Cars like BeamNG, Jane Street, Citadel)
SPECIAL_INTL_COMPANIES = [
    "beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", 
    "hrt", "twosigma", "imc", "flowtraders", "wayve"
]

# Companies to Monitor via ATS APIs
GREENHOUSE_COMPANIES = [
    "deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", 
    "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading"
]
LEVER_COMPANIES = ["spotify", "revolut", "checkout", "beamng", "wayve"]


def is_relevant_role(title, location, company):
    """Filters roles specifically for Year 2 Maths & CS Student."""
    title_lower = title.lower()
    loc_lower = location.lower()
    comp_lower = company.lower()

    # Step 1: Reject excluded/irrelevant keywords
    if any(ex in title_lower for ex in EXCLUDE_KEYWORDS):
        return False

    # Step 2: Must be an Internship, Placement, Spring Week, or Grad Role
    has_level = any(lvl in title_lower for lvl in LEVEL_KEYWORDS)
    if not has_level:
        return False

    # Step 3: Must match Software / Quant / AI / Tech domain
    has_role = any(rk in title_lower for rk in ROLE_KEYWORDS)
    if not has_role:
        return False

    # Step 4: Special International Exception (e.g. BeamNG or Top Quant)
    if any(sc in comp_lower for sc in SPECIAL_INTL_COMPANIES):
        return True

    # Step 5: Location Match (UK / Preferred / Remote)
    has_loc = any(loc in loc_lower for loc in LOCATION_KEYWORDS)
    if has_loc or "remote" in loc_lower or "uk" in loc_lower or loc_lower == "":
        return True

    return False


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
    """Sends ntfy push notification with clean headers."""
    clean_title = title.encode("ascii", "ignore").decode("ascii").strip()
    if not clean_title:
        clean_title = title.replace("🚀", "").replace("🚨", "").replace("🎉", "").strip()

    headers = {"Title": clean_title, "Tags": tags}
    if link:
        headers["Click"] = link

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        print(f"✅ Notification Sent: {clean_title}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


# ==========================================
# MODULE 3: SCRAPER ENGINE
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
                        if is_relevant_role(title, location, company):
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
            else:
                errors += 1
        except Exception as e:
            errors += 1

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({errors} warnings)"
    return new_jobs


def scrape_lever_jobs(seen_jobs):
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
                        if is_relevant_role(title, location, company):
                            seen_jobs.add(job_id)
                            new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
            else:
                errors += 1
        except Exception as e:
            errors += 1

    SCRAPER_STATUS["source_status"][source_name] = f"OK ({errors} warnings)"
    return new_jobs


def scrape_github_lists(seen_jobs):
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
                    line_lower = line.lower()
                    if is_relevant_role(line, "UK", "GitHubRepo"):
                        job_id = f"ghrepo_{hash(line)}"
                        if job_id not in seen_jobs:
                            seen_jobs.add(job_id)
                            new_jobs.append(("GitHub Tech Role", "UK / Remote", "https://github.com/simplify-jobs/Summer2026-Internships"))
        except Exception as e:
            print(f"⚠️ [GitHub Scraper Error]: {e}")

    SCRAPER_STATUS["source_status"][source_name] = "OK"
    return new_jobs


def scrape_trackr_website(seen_jobs):
    new_jobs = []
    source_name = "Trackr Web"
    url = "https://the-trackr.com"

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if is_relevant_role(text, "UK", "Trackr"):
                    job_id = f"trackr_{hash(href)}"
                    if job_id not in seen_jobs:
                        seen_jobs.add(job_id)
                        full_url = href if href.startswith("http") else f"https://the-trackr.com{href}"
                        new_jobs.append((f"Trackr: {text[:40]}", "UK", full_url))
        SCRAPER_STATUS["source_status"][source_name] = "OK"
    except Exception as e:
        SCRAPER_STATUS["source_status"][source_name] = f"Error: {e}"

    return new_jobs


def run_all_scrapers():
    print("\n🔍 Running Tailored UK Maths & CS Scraper Engine...")
    seen_jobs = load_seen_jobs()
    all_new_jobs = []

    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs))
    all_new_jobs.extend(scrape_github_lists(seen_jobs))
    all_new_jobs.extend(scrape_trackr_website(seen_jobs))

    save_seen_jobs(seen_jobs)

    SCRAPER_STATUS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    SCRAPER_STATUS["total_seen_jobs"] = len(seen_jobs)
    SCRAPER_STATUS["last_new_jobs_found"] = len(all_new_jobs)

    if all_new_jobs:
        print(f"🚨 FOUND {len(all_new_jobs)} NEW MATCHING ROLES!")
        for title, location, link in all_new_jobs:
            send_notification(
                title=f"New Role: {title}",
                message=f"Location: {location}\nTap to open direct application link!",
                link=link,
                tags="rotating_light,sparkles,uk"
            )
    else:
        print("✅ No new matching internship listings found this cycle.")


def generate_sankey_from_google_sheets():
    if "YOUR_GOOGLE_SHEET_ID_HERE" in GOOGLE_SHEET_CSV_URL:
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

    except Exception as e:
        print(f"Error parsing Google Sheet: {e}")


if __name__ == "__main__":
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    time.sleep(1)

    print("\n🚀 ApplicationTrackr Engine Online!")
    send_notification(
        title="Scraper Engine Online",
        message=f"Monitoring UK Maths & CS Roles.\nView Status: http://{HP_STREAM_TAILSCALE_IP}:{PORT}/status",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/status",
        tags="rocket,uk"
    )

    while True:
        run_all_scrapers()
        generate_sankey_from_google_sheets()
        time.sleep(3600)