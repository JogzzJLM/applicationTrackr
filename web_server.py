import os
import json
import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

from config import PORT, SCRAPER_STATUS, HP_STREAM_TAILSCALE_IP
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, generate_sankey_from_google_sheets, generate_default_sankey
from scrapers import run_all_scrapers
from scheduler import trigger_daily_briefing, trigger_weekly_report

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