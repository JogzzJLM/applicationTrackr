import csv
import json
import os
import time
import io
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime
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
SEEN_EMAILS_FILE = "seen_emails.json"
PORT = 5000
HP_STREAM_TAILSCALE_IP = "100.75.135.73"

GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS", "")
GOOGLE_SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "")
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vS94NpozDGeHO9UPag662CXcH-C5TGN9Y61-nW04VDlPJSZGVTq62E1lRvnXl8gq_CbR5kvMx5XnMFi/pub?output=csv"

SCRAPER_STATUS = {
    "last_run": "Never",
    "total_seen_jobs": 0,
    "last_new_jobs_found": 0,
    "source_status": {}
}

EXCLUDE_KEYWORDS = ["vice president", "vp", "director", "head of", "principal", "senior manager", "sales development", "account executive", "recruiter", "marketing", "legal"]
LEVEL_KEYWORDS = ["intern", "internship", "placement", "industrial placement", "sandwich", "spring week", "insight week", "graduate", "grad", "early talent", "early career"]
ROLE_KEYWORDS = ["software", "developer", "engineer", "engineering", "backend", "fullstack", "full-stack", "systems", "quant", "quantitative", "trader", "trading", "research", "machine learning", "ml", "ai", "data science", "cyber", "security", "cloud", "devops"]
LOCATION_KEYWORDS = ["london", "birmingham", "oxford", "aylesbury", "west midlands", "remote", "uk", "united kingdom", "cambridge", "manchester", "edinburgh"]
SPECIAL_INTL_COMPANIES = ["beamng", "janestreet", "optiver", "citadel", "hudsonrivertrading", "hrt", "twosigma", "imc", "flowtraders", "wayve"]

GREENHOUSE_COMPANIES = ["deliveroo", "cloudflare", "snyk", "monzo", "starlingbank", "janestreet", "optiver", "canonical", "citadel", "hudsonrivertrading"]
LEVER_COMPANIES = ["spotify", "revolut", "checkout", "beamng", "wayve"]


def is_relevant_role(title, location, company):
    title_lower = title.lower()
    loc_lower = location.lower()
    comp_lower = company.lower()

    if any(ex in title_lower for ex in EXCLUDE_KEYWORDS):
        return False
    if not any(lvl in title_lower for lvl in LEVEL_KEYWORDS):
        return False
    if not any(rk in title_lower for rk in ROLE_KEYWORDS):
        return False
    if any(sc in comp_lower for sc in SPECIAL_INTL_COMPANIES):
        return True
    if any(loc in loc_lower for loc in LOCATION_KEYWORDS) or "remote" in loc_lower or "uk" in loc_lower or loc_lower == "":
        return True
    return False


# ==========================================
# MODULE 1: EMBEDDED WEB SERVER & API ENDPOINTS
# ==========================================
def start_web_server():
    class CleanHandler(http.server.BaseHTTPRequestHandler):
        def send_cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_cors_headers()
            self.end_headers()

        def do_POST(self):
            if self.path == "/api/log":
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                try:
                    data = json.loads(body)
                    company = data.get("company", "Unknown")
                    role = data.get("role", "Software/Quant Role")

                    update_google_sheet_via_webhook(company, "Applied", role)
                    generate_sankey_from_google_sheets()

                    send_notification(
                        title=f"Logged: {company}",
                        message=f"Added {company} ({role}) as 'Applied' in Google Sheets.",
                        tags="memo,check-mark",
                        priority=3,
                        sound="chime"
                    )

                    self.send_response(200)
                    self.send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "message": f"Logged {company}"}).encode("utf-8"))
                    return
                except Exception as e:
                    self.send_response(500)
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
                    return

            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()

        def do_GET(self):
            # 1. SANKEY DIAGRAM DASHBOARD
            if self.path in ["/", "/sankey", "/refresh"]:
                generate_sankey_from_google_sheets()
                if not os.path.exists("sankey_diagram.html"):
                    generate_default_sankey()

                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open("sankey_diagram.html", "rb") as f:
                    self.wfile.write(f.read())
                return

            # 2. DIAGNOSTICS STATUS
            elif self.path == "/status":
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))
                return

            # 3. TEST ENDPOINT: DAILY BRIEFING
            elif self.path == "/test-briefing":
                trigger_daily_briefing()
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("✅ Triggered test Daily Morning Briefing! Check ntfy on your phone/Mac.".encode("utf-8"))
                return

            # 4. TEST ENDPOINT: WEEKLY REPORT
            elif self.path == "/test-weekly":
                trigger_weekly_report()
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("✅ Triggered test Sunday Weekly Funnel Report! Check ntfy on your phone/Mac.".encode("utf-8"))
                return

            # 5. TEST ENDPOINT: SCRAPER RUN
            elif self.path == "/test-scraper":
                run_all_scrapers()
                self.send_response(200)
                self.send_cors_headers()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("✅ Triggered manual scraper run!".encode("utf-8"))
                return

            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write("404 Not Found".encode("utf-8"))

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CleanHandler) as httpd:
        print(f"🌍 Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()


# ==========================================
# MODULE 2: NOTIFICATION & SHEET HELPERS
# ==========================================
def send_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime"):
    clean_title = title.encode("ascii", "ignore").decode("ascii").strip()
    if not clean_title:
        clean_title = "ApplicationTrackr Alert"

    headers = {
        "Title": clean_title,
        "Tags": tags,
        "Priority": str(priority),
        "Sound": sound
    }
    if link:
        headers["Click"] = link

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        print(f"✅ Notification Sent [{sound}]: {clean_title}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


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
    """Parses Google Sheet and returns summary statistics for briefings."""
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


def trigger_daily_briefing():
    """Triggers the Daily Morning Briefing alert."""
    stats = parse_sheet_stats()
    new_jobs_today = SCRAPER_STATUS.get("last_new_jobs_found", 0)
    
    send_notification(
        title="Daily Morning Briefing",
        message=f"Good morning! Total Logged: {stats['total']} | Active/Pending Rounds: {stats['active']} | New Schemes Found Yesterday: {new_jobs_today}",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/sankey",
        tags="sun,briefcase",
        priority=3,
        sound="bing"
    )


def trigger_weekly_report():
    """Triggers the Sunday Weekly Funnel Report alert."""
    stats = parse_sheet_stats()
    total = stats["total"]
    conv_rate = round((stats["active"] + stats["offers"]) / total * 100, 1) if total > 0 else 0.0

    send_notification(
        title="Sunday Weekly Funnel Report",
        message=f"Weekly Funnel Summary: Applied Total: {total} | Active Rounds: {stats['active']} | Offers: {stats['offers']} | Rejections: {stats['rejections']} | Conversion Rate: {conv_rate}%",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/sankey",
        tags="bar_chart,trophy",
        priority=4,
        sound="fanfare"
    )


# ==========================================
# MODULE 3: EMAIL INBOX LISTENER (IMAP)
# ==========================================
def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        try:
            with open(SEEN_EMAILS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_emails(seen):
    try:
        with open(SEEN_EMAILS_FILE, "w") as f:
            json.dump(list(seen), f)
    except Exception:
        pass


def check_email_inbox():
    if not GMAIL_USER or not GMAIL_APP_PASS or "your_email" in GMAIL_USER:
        return

    print("📧 Checking Gmail Inbox for application updates...")
    seen_emails = load_seen_emails()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        if status != "OK":
            return

        email_ids = messages[0].split()
        for e_id in email_ids[-15:]:
            if e_id.decode() in seen_emails:
                continue

            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8", errors="ignore")
                    
                    from_sender = msg.get("From", "")
                    body_text = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body_text = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body_text = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    combined_text = f"{subject} {body_text}".lower()

                    company_match = re.search(r"at ([A-Z][a-zA-Z0-9]+)", subject) or re.search(r"@([a-zA-Z0-9]+)\.", from_sender)
                    company_name = company_match.group(1).capitalize() if company_match else "Company"

                    if any(k in combined_text for k in ["online assessment", "coding test", "hackerrank", "codility", "hirevue", "invitation to interview", "schedule your interview"]):
                        seen_emails.add(e_id.decode())
                        stage = "Online Assessment" if "assessment" in combined_text or "hackerrank" in combined_text else "Interview"
                        update_google_sheet_via_webhook(company_name, stage)
                        send_notification(
                            title=f"Assessment Invite: {company_name}",
                            message=f"New interview/assessment email received from {company_name}.\nCheck your inbox!",
                            tags="tada,fire",
                            priority=5,
                            sound="fanfare"
                        )

                    elif any(k in combined_text for k in ["thank you for applying", "application received", "thanks for your interest", "received your application"]):
                        seen_emails.add(e_id.decode())
                        update_google_sheet_via_webhook(company_name, "Applied")
                        send_notification(
                            title=f"Application Confirmed: {company_name}",
                            message=f"Logged 'Applied' status for {company_name} in Google Sheets.",
                            tags="check-mark",
                            priority=2,
                            sound="subtle"
                        )

                    elif any(k in combined_text for k in ["we regret to inform you", "unfortunately", "will not be moving forward", "pursue other candidates"]):
                        seen_emails.add(e_id.decode())
                        update_google_sheet_via_webhook(company_name, "Rejected")
                        send_notification(
                            title=f"Update: {company_name}",
                            message=f"Application status updated to Rejected for {company_name}.",
                            tags="x",
                            priority=2,
                            sound="minion"
                        )

        mail.logout()
        save_seen_emails(seen_emails)

    except Exception as e:
        print(f"⚠️ Email Listener Error: {e}")


# ==========================================
# MODULE 4: SCRAPER ENGINE SOURCES
# ==========================================
def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        try:
            with open(SEEN_JOBS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_jobs(seen_jobs):
    try:
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(list(seen_jobs), f)
    except Exception:
        pass


def scrape_greenhouse_jobs(seen_jobs):
    new_jobs = []
    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for job in resp.json().get("jobs", []):
                    title = job.get("title", "")
                    location = job.get("location", {}).get("name", "")
                    job_url = job.get("absolute_url", "")
                    job_id = f"gh_{company}_{job.get('id')}"

                    if job_id not in seen_jobs and is_relevant_role(title, location, company):
                        seen_jobs.add(job_id)
                        new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
        except Exception:
            pass
    return new_jobs


def scrape_lever_jobs(seen_jobs):
    new_jobs = []
    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                for job in resp.json():
                    title = job.get("text", "")
                    location = job.get("categories", {}).get("location", "")
                    job_url = job.get("hostedUrl", "")
                    job_id = f"lever_{company}_{job.get('id')}"

                    if job_id not in seen_jobs and is_relevant_role(title, location, company):
                        seen_jobs.add(job_id)
                        new_jobs.append((f"{company.capitalize()} - {title}", location, job_url))
        except Exception:
            pass
    return new_jobs


def run_all_scrapers():
    print("\n🔍 Running Scraper Engine...")
    seen_jobs = load_seen_jobs()
    all_new_jobs = []

    all_new_jobs.extend(scrape_greenhouse_jobs(seen_jobs))
    all_new_jobs.extend(scrape_lever_jobs(seen_jobs))
    save_seen_jobs(seen_jobs)

    if all_new_jobs:
        print(f"🚨 FOUND {len(all_new_jobs)} NEW MATCHING ROLES!")
        for title, location, link in all_new_jobs:
            send_notification(
                title=f"New Role: {title}",
                message=f"Location: {location}\nTap to open application link!",
                link=link,
                tags="sparkles,uk",
                priority=3,
                sound="bing"
            )


# ==========================================
# MODULE 5: SANKEY FLOW GENERATOR
# ==========================================
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


# ==========================================
# MODULE 6: SCHEDULER LOOP
# ==========================================
def scheduler_loop():
    last_daily_date = ""
    last_weekly_date = ""

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour == 8 and now.minute == 0 and last_daily_date != today_str:
                trigger_daily_briefing()
                last_daily_date = today_str

            if now.weekday() == 6 and now.hour == 18 and now.minute == 0 and last_weekly_date != today_str:
                trigger_weekly_report()
                last_weekly_date = today_str

        except Exception as e:
            print(f"⚠️ Scheduler Error: {e}")

        time.sleep(30)


# ==========================================
# MAIN LOOP
# ==========================================
if __name__ == "__main__":
    generate_sankey_from_google_sheets()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

    time.sleep(1)

    print("\n🚀 ApplicationTrackr Engine Online!")
    send_notification(
        title="Scraper Engine Online",
        message="Monitoring UK Maths & CS Roles + Email Inbox + Scheduled Reports.",
        tags="rocket,uk",
        priority=3,
        sound="chime"
    )

    while True:
        check_email_inbox()
        run_all_scrapers()
        generate_sankey_from_google_sheets()
        time.sleep(1800)