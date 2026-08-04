import os
import json
import threading
import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

from config import PORT, SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, generate_sankey_from_google_sheets, generate_default_sankey, get_applied_companies_set
from scrapers import run_all_scrapers, load_discovered_jobs
from scheduler import trigger_daily_briefing, trigger_weekly_report

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def render_jobs_page_html():
    all_jobs = load_discovered_jobs()
    applied_set = get_applied_companies_set()
    cards_html = ""

    for j in all_jobs:
        comp_name = j['company']
        title_name = j['title']
        comp_lower = comp_name.lower().strip()
        is_applied = comp_lower in applied_set

        # Escape single quotes safely for JS onclick handlers outside f-string brackets
        comp_js = comp_name.replace("'", "\\'").replace('"', '&quot;')
        title_js = title_name.replace("'", "\\'").replace('"', '&quot;')

        if is_applied:
            status_badge = '<span class="badge badge-applied">✅ APPLIED</span>'
            action_btn = '<button class="btn btn-disabled" disabled>✓ Logged</button>'
        else:
            status_badge = '<span class="badge badge-not-applied">⚡ NOT APPLIED</span>'
            action_btn = f'<button onclick="logJob(\'{comp_js}\', \'{title_js}\')" class="btn btn-success">+ Log to Sheet</button>'

        cards_html += f"""
        <div class="job-card" data-search="{j['company'].lower()} {j['title'].lower()} {j['location'].lower()} {'applied' if is_applied else 'not applied'}">
            <div class="job-header">
                <div>
                    <span class="company">{j['company']}</span> &nbsp;
                    {status_badge}
                </div>
                <span class="badge badge-source">🌐 Source: {j['source']}</span>
            </div>
            <div class="job-title">{j['title']}</div>
            <div class="job-meta">📍 {j['location']} &nbsp;•&nbsp; 🕒 Discovered: {j['date_found']}</div>
            <div class="job-actions">
                <a href="{j['link']}" target="_blank" class="btn btn-primary">Apply Direct ↗</a>
                {action_btn}
            </div>
        </div>
        """

    if not cards_html:
        cards_html = '<div class="empty-msg">No job listings indexed yet. Click "Trigger Re-Scan" above to run scrapers!</div>'

    total_count = len(all_jobs)

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
        h1 {{ color: #38bdf8; margin-bottom: 5px; font-size: 26px; }}
        .subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 12px; }}
        .rescan-btn {{ display: inline-block; background: #0284c7; color: white; text-decoration: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: bold; border: none; cursor: pointer; }}
        .rescan-btn:hover {{ background: #0369a1; }}
        .search-input {{ width: 100%; box-sizing: border-box; padding: 14px 20px; border-radius: 10px; border: 1px solid #334155; background: #1e293b; color: white; font-size: 16px; margin-bottom: 20px; outline: none; }}
        .search-input:focus {{ border-color: #38bdf8; }}
        .job-card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 15px; border: 1px solid #334155; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }}
        .job-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 10px; }}
        .company {{ color: #f1f5f9; font-weight: bold; font-size: 18px; }}
        .badge {{ font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: bold; display: inline-block; }}
        .badge-source {{ background: #334155; color: #cbd5e1; }}
        .badge-applied {{ background: #064e3b; color: #6ee7b7; border: 1px solid #047857; }}
        .badge-not-applied {{ background: #1e3a8a; color: #93c5fd; border: 1px solid #1d4ed8; }}
        .job-title {{ color: #93c5fd; font-size: 16px; margin-bottom: 10px; line-height: 1.4; }}
        .job-meta {{ color: #64748b; font-size: 13px; margin-bottom: 15px; }}
        .job-actions {{ display: flex; gap: 10px; }}
        .btn {{ padding: 10px 18px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px; border: none; cursor: pointer; transition: 0.2s; }}
        .btn-primary {{ background: #2563eb; color: white; }}
        .btn-primary:hover {{ background: #1d4ed8; }}
        .btn-success {{ background: #10b981; color: white; }}
        .btn-success:hover {{ background: #059669; }}
        .btn-disabled {{ background: #334155; color: #94a3b8; cursor: not-allowed; }}
        .terminal-box {{ background: #020617; border: 1px solid #38bdf8; border-radius: 12px; padding: 20px; margin-bottom: 25px; text-align: left; font-family: monospace; box-shadow: 0 0 20px rgba(56,189,248,0.2); }}
        .terminal-header {{ color: #38bdf8; font-weight: bold; font-size: 14px; margin-bottom: 10px; display: flex; justify-content: space-between; }}
        .terminal-logs {{ color: #a5f3fc; font-size: 13px; margin: 0; white-space: pre-wrap; word-break: break-all; max-height: 180px; overflow-y: auto; }}
        .empty-msg {{ text-align: center; color: #64748b; padding: 40px; font-size: 16px; }}
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
                .then(d => {{
                    alert('✅ Successfully logged ' + company + ' to Google Sheets!');
                    location.reload();
                }})
                .catch(e => alert('❌ Error logging job: ' + e));
        }}
        function triggerInlineScan() {{
            let term = document.getElementById('terminal-box');
            let log = document.getElementById('terminal-logs');
            let tag = document.getElementById('scan-tag');
            term.style.display = 'block';
            tag.innerText = 'Running...';
            log.innerText = '⚡ Initiating background multi-source scraper run...\\nScanning Greenhouse API, Lever API, and Trackr API...\\nPlease wait...';
            
            fetch('/api/trigger-scan')
                .then(r => r.json())
                .then(d => {{
                    let checkInterval = setInterval(() => {{
                        fetch('/status')
                            .then(sr => sr.json())
                            .then(s => {{
                                log.innerText = "⚡ Scraper Status: Run in progress...\\nLast Run: " + s.last_run + "\\nActive Schemes Indexed: " + s.total_discovered_jobs;
                            }});
                    }}, 2000);
                    
                    setTimeout(() => {{
                        clearInterval(checkInterval);
                        tag.innerText = 'Complete!';
                        log.innerText += '\\n\\n✅ Scraper run completed successfully! Reloading schemes...';
                        setTimeout(() => location.reload(), 1500);
                    }}, 8000);
                }})
                .catch(e => {{
                    log.innerText += '\\n❌ Error triggering scan: ' + e;
                }});
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="/">📊 Application Flow</a>
            <a href="/jobs" class="active">💼 Discovered Schemes ({total_count})</a>
            <a href="/status">⚙️ Diagnostics</a>
        </div>
        <div class="header-box">
            <h1>Discovered UK Schemes ({total_count})</h1>
            <div class="subtitle">Real-time UK Maths, Quant & CS Internship Directory</div>
            <button onclick="triggerInlineScan()" class="rescan-btn">🔄 Trigger Inline Re-Scan</button>
        </div>

        <div id="terminal-box" class="terminal-box" style="display:none;">
            <div class="terminal-header">
                <span>⚡ Active Scraper Terminal Console</span>
                <span id="scan-tag" style="color:#10b981;">Running...</span>
            </div>
            <pre id="terminal-logs" class="terminal-logs">Initializing...</pre>
        </div>

        <input type="text" id="search" onkeyup="filterJobs()" placeholder="🔍 Search company, role, location, or 'applied' / 'not applied'..." class="search-input">
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

        elif clean_path == "/api/trigger-scan":
            threading.Thread(target=run_all_scrapers, daemon=True).start()

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "message": "Scraper run initiated"}).encode("utf-8"))
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
    with ThreadedHTTPServer(("", PORT), CleanHandler) as httpd:
        print(f"🌍 Threaded Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{PORT}")
        httpd.serve_forever()