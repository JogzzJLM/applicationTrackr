import os
import json
import urllib.parse
import http.server
import socketserver
import threading
import requests
from urllib.parse import parse_qs, urlparse, quote

from config import PORT, SCRAPER_STATUS, add_scraper_log, get_git_commit
from core.storage import (
    load_settings, save_settings,
    load_hidden_jobs, hide_job, save_hidden_jobs,
    load_reported_closed_jobs, save_reported_closed_jobs, atomic_write_json
)

from core.kb import (
    load_closed_keywords_kb, save_closed_keywords_kb,
    extract_generic_closure_phrases
)

from notifications import generate_apple_calendar_ics
from sheets import (
    update_google_sheet_via_webhook, generate_sankey_from_google_sheets,
    generate_default_sankey, get_applied_jobs_set
)
from scrapers_engine.audit import run_all_scrapers, recheck_existing_open_jobs_for_closure
from web.views import render_unified_dashboard_html

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class CleanHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html("flow").encode("utf-8"))

        elif path in ["/jobs", "/discovered"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html("jobs").encode("utf-8"))

        elif path == "/settings":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html("settings").encode("utf-8"))

        elif path in ["/status", "/diagnostics"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html("diagnostics").encode("utf-8"))

        elif path == "/closed":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(render_unified_dashboard_html("closed").encode("utf-8"))

        elif path == "/sankey-embed":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            if not os.path.exists("sankey_diagram.html"):
                generate_sankey_from_google_sheets()
            try:
                with open("sankey_diagram.html", "r", encoding="utf-8") as f:
                    html = f.read()
            except Exception:
                html = "<html><body><h3>Sankey Diagram Loading...</h3></body></html>"
            self.wfile.write(html.encode("utf-8"))

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            status_data = dict(SCRAPER_STATUS)
            status_data["commit"] = get_git_commit()
            self.wfile.write(json.dumps(status_data, indent=2).encode("utf-8"))

        elif path in ["/api/version", "/version"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            version_data = {
                "commit": get_git_commit(),
                "last_run": SCRAPER_STATUS.get("last_run", "Never"),
                "status": "online"
            }
            self.wfile.write(json.dumps(version_data, indent=2).encode("utf-8"))

        elif path == "/api/logs":
            from config import get_scraper_logs
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            logs = get_scraper_logs()
            self.wfile.write(json.dumps({"logs": logs}, indent=2).encode("utf-8"))

        elif path == "/api/clear-logs":
            from config import clear_scraper_logs
            clear_scraper_logs()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        elif path == "/api/rescan":
            add_scraper_log("⚡ Triggered Scraper Rescan from Dashboard UI")
            threading.Thread(target=run_all_scrapers, daemon=True).start()
            self.send_response(302)
            self.send_header("Location", "/jobs")
            self.end_headers()

        elif path == "/api/sync-sheet":
            add_scraper_log("🔄 Triggered Google Sheets Sync & Sankey Re-generation")
            try:
                generate_sankey_from_google_sheets()
            except Exception as e:
                add_scraper_log(f"⚠️ Sheet sync error: {e}")
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

        elif path == "/api/hide-job":
            j_id = qs.get("id", [""])[0]
            if j_id:
                hide_job(j_id)
                add_scraper_log(f"🙈 Hid job ID: {j_id}")
            self.send_response(302)
            self.send_header("Location", "/jobs")
            self.end_headers()

        elif path == "/api/report-closed":
            j_id = qs.get("id", [""])[0]
            target_link = qs.get("link", [""])[0]
            if j_id:
                closed_map = load_reported_closed_jobs()
                closed_map[j_id] = {
                    "id": j_id,
                    "link": target_link,
                    "company": qs.get("company", [""])[0],
                    "title": qs.get("title", [""])[0]
                }
                save_reported_closed_jobs(closed_map)

                if target_link and target_link.startswith("http"):
                    try:
                        r = requests.get(target_link, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
                        if r.status_code == 200:
                            extracted = extract_generic_closure_phrases(r.text)
                            if extracted:
                                save_closed_keywords_kb(extracted)
                                add_scraper_log(f"🧠 ML Knowledge Base learned {len(extracted)} closure patterns from {target_link}")
                    except Exception as ex:
                        add_scraper_log(f"⚠️ Failed to learn from link {target_link}: {ex}")

                add_scraper_log(f"🚫 Reported scheme as CLOSED: {j_id}")
            self.send_response(302)
            self.send_header("Location", "/jobs")
            self.end_headers()

        elif path == "/api/reopen-job":
            j_id = qs.get("id", [""])[0]
            if j_id:
                closed_map = load_reported_closed_jobs()
                if j_id in closed_map:
                    del closed_map[j_id]
                    save_reported_closed_jobs(closed_map)
                    add_scraper_log(f"🔓 Re-opened scheme ID: {j_id}")
            self.send_response(302)
            self.send_header("Location", "/closed")
            self.end_headers()

        elif path == "/api/mark-applied":
            comp = qs.get("company", [""])[0]
            title = qs.get("title", [""])[0]
            if comp:
                update_google_sheet_via_webhook(comp, "Applied", title)
                add_scraper_log(f"✅ Marked application logged for {comp} ({title})")
            self.send_response(302)
            self.send_header("Location", "/jobs")
            self.end_headers()

        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/settings":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            parsed_data = parse_qs(post_data)

            current_settings = load_settings()

            my_skills_raw = parsed_data.get('my_skills', [''])[0]
            current_settings['my_skills'] = [s.strip() for s in my_skills_raw.split(',') if s.strip()]

            ex_kw_raw = parsed_data.get('exclude_keywords', [''])[0]
            current_settings['exclude_keywords'] = [s.strip() for s in ex_kw_raw.split(',') if s.strip()]

            ex_loc_raw = parsed_data.get('exclude_locations', [''])[0]
            current_settings['exclude_locations'] = [s.strip() for s in ex_loc_raw.split(',') if s.strip()]

            def parse_multiline(param_name):
                raw = parsed_data.get(param_name, [''])[0]
                lines = []
                for line in raw.replace('\r', '').split('\n'):
                    for item in line.split(','):
                        if item.strip():
                            lines.append(item.strip())
                return lines

            current_settings['greenhouse_companies'] = parse_multiline('greenhouse_companies')
            current_settings['lever_companies'] = parse_multiline('lever_companies')
            current_settings['ashby_companies'] = parse_multiline('ashby_companies')
            current_settings['smartrecruiters_companies'] = parse_multiline('smartrecruiters_companies')

            current_settings['auto_hide_applied_company_jobs'] = 'auto_hide_applied_company_jobs' in parsed_data

            save_settings(current_settings)
            add_scraper_log("⚙️ Updated and saved custom filter settings.")

            self.send_response(302)
            self.send_header("Location", "/settings")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def start_web_server(port=PORT):
    server = ThreadedHTTPServer(("0.0.0.0", port), CleanHandler)
    add_scraper_log(f"🌐 Unified Dashboard Web Server active on port {port}")
    server.serve_forever()
