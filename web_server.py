import os
import json
import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

from config import PORT, SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, generate_sankey_from_google_sheets, generate_default_sankey
from scrapers import run_all_scrapers, load_discovered_jobs
from scheduler import trigger_daily_briefing, trigger_weekly_report

def render_jobs_page_html():
    jobs = load_discovered_jobs()
    cards_html = ""

    for j in jobs:
        cards_html += f"""
        <div class="job-card" data-search="{j['company'].lower()} {j['title'].lower()} {j['location'].lower()}">
            <div class="job-header">
                <span class="company">{j['company']}</span>
                <span class="badge">{j['source']}</span>
            </div>
            <div class="job-title">{j['title']}</div>
            <div class="job-meta">📍 {j['location']} &nbsp;•&nbsp; 🕒 Discovered: {j['date_found']}</div>
            <div class="job-actions">
                <a href="{j['link']}" target="_blank" class="btn btn-primary">Apply Direct ↗</a>
                <button onclick="logJob('{j['company']}', '{j['title']}')" class="btn btn-success">+ Log to Sheet</button>
            </div>
        </div>
        """

    if not cards_html:
        cards_html = '<div class="empty-msg">No job listings indexed yet. <a href="/test-scraper">Click here to trigger an immediate scraper scan!</a></div>'

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>ApplicationTrackr - Discovered Schemes</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
        .nav {{ display: flex; gap: 15px; justify-content: center; margin-bottom: 25px; border-bottom: 1px solid #334155; padding-bottom: 15px; }}
        .nav a {{ color: #94a3b8; text-decoration: none; font-weight: bold; padding: 8px 16px; border-radius: 8px; transition: 0.2s; }}
        .nav a:hover, .nav a.active {{ background: #1e293b; color: #38bdf8; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        .header-box {{ text-align: center; margin-bottom: 25px; }}
        h1 {{ color: #38bdf8; margin-bottom: 10px; font-size: 26px; }}
        .rescan-btn {{ display: inline-block; background: #0284c7; color: white; text-decoration: none; padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: bold; margin-top: 5px; }}
        .rescan-btn:hover {{ background: #0369a1; }}
        .search-input {{ width: 100%; box-sizing: border-box; padding: 14px 20px; border-radius: 10px; border: 1px solid #334155; background: #1e293b; color: white; font-size: 16px; margin-bottom: 20px; outline: none; }}
        .search-input:focus {{ border-color: #38bdf8; }}
        .job-card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 15px; border: 1px solid #334155; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }}
        .job-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        .company {{ color: #f1f5f9; font-weight: bold; font-size: 18px; }}
        .badge {{ background: #0284c7; color: white; font-size: 12px; padding: 4px 10px; border-radius: 12px; font-weight: bold; }}
        .job-title {{ color: #93c5fd; font-size: 16px; margin-bottom: 10px; line-height: 1.4; }}
        .job-meta {{ color: #64748b; font-size: 13px; margin-bottom: 15px; }}
        .job-actions {{ display: flex; gap: 10px; }}
        .btn {{ padding: 10px 18px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px; border: none; cursor: pointer; transition: 0.2s; }}
        .btn-primary {{ background: #2563eb; color: white; }}
        .btn-primary:hover {{ background: #1d4ed8; }}
        .btn-success {{ background: #10b981; color: white; }}
        .btn-success:hover {{ background: #059669; }}
        .empty-msg {{ text-align: center; color: #64748b; padding: 40px; font-size: 16px; }}
        .empty-msg a {{ color: #38bdf8; font-weight: bold; }}
    </style>
    <script>
        function filterJobs() {{
            let q = document.getElementById('search').value.toLowerCase();
            let cards = document.querySelectorAll('.job-card');
            cards.forEach(c => {{
                let txt = c.getAttribute('data-search');
                c.style.display = txt.includes(q) ? 'block' : 'none';
            }});
        }}
        function logJob(company, role) {{
            fetch('/api/log?company=' + encodeURIComponent(company) + '&role=' + encodeURIComponent(role))
                .then(r => r.json())
                .then(d => alert('✅ Successfully logged ' + company + ' to Google Sheets!'))
                .catch(e => alert('❌ Error logging job: ' + e));
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="/">📊 Application Flow</a>
            <a href="/jobs" class="active">💼 Discovered Schemes ({len(jobs)})</a>
            <a href="/status">⚙️ Diagnostics</a>
        </div>
        <div class="header-box">
            <h1>Discovered UK Schemes ({len(jobs)})</h1>
            <p style="color: #94a3b8;">Real-time UK Maths, Quant & CS Internship Listings</p>
            <a href="/test-scraper" class="rescan-btn">🔄 Trigger Re-Scan Scrapers</a>
        </div>
        <input type="text" id="search" onkeyup="filterJobs()" placeholder="🔍 Search company, role title, or location..." class="search-input">
        <div id="job-list">
            {cards_html}
        </div>
    </div>
</body>
</html>"""


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

    def process_log_request(self, company, role, link):
        update_google_sheet_via_webhook(company, "Applied", role)
        generate_sankey_from_google_sheets()

        send_notification(
            title=f"Logged: {company}",
            message=f"Added {company} ({role}) as 'Applied' in Google Sheets.",
            tags="memo,check-mark",
            priority=3,
            sound="chime"
        )

    def do_POST(self):
        if self.path.startswith("/api/log"):
            company = "Unknown"
            role = "Software/Quant Role"
            link = ""

            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)
            if "company" in query_params:
                company = query_params["company"][0]
            if "role" in query_params:
                role = query_params["role"][0]

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                body_raw = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                if body_raw.strip():
                    try:
                        data = json.loads(body_raw)
                        company = data.get("company", company)
                        role = data.get("role", role)
                        link = data.get("link", link)
                    except Exception:
                        form_params = parse_qs(body_raw)
                        if "company" in form_params:
                            company = form_params["company"][0]
                        if "role" in form_params:
                            role = form_params["role"][0]

            self.process_log_request(company, role, link)

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": f"Logged {company}"}).encode("utf-8"))
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed_url = urlparse(self.path)
        clean_path = parsed_url.path

        if clean_path == "/api/log":
            query_params = parse_qs(parsed_url.query)
            company = query_params.get("company", ["Unknown"])[0]
            role = query_params.get("role", ["Software/Quant Role"])[0]
            link = query_params.get("link", [""])[0]

            self.process_log_request(company, role, link)

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": f"Logged {company}"}).encode("utf-8"))
            return

        elif clean_path in ["/", "/sankey", "/refresh"]:
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

        elif clean_path == "/jobs":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_jobs_page_html().encode("utf-8"))
            return

        elif clean_path == "/status":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))
            return

        elif clean_path == "/test-briefing":
            trigger_daily_briefing()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Triggered test Daily Morning Briefing! Check ntfy on your phone/Mac.".encode("utf-8"))
            return

        elif clean_path == "/test-weekly":
            trigger_weekly_report()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Triggered test Sunday Weekly Funnel Report! Check ntfy on your phone/Mac.".encode("utf-8"))
            return

        elif clean_path == "/test-scraper":
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

def start_web_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), CleanHandler) as httpd:
        print(f"🌍 Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()