import os
import json
import urllib.parse
import http.server
import socketserver
import threading
from urllib.parse import parse_qs, urlparse, quote

from config import PORT, SCRAPER_STATUS, add_scraper_log
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
            html = generate_sankey_from_google_sheets()
            self.wfile.write(html.encode("utf-8"))

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))

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
                    "company": "Reported Company",
                    "title": "Reported Position",
                    "date_reported": "Recently"
                }
                save_reported_closed_jobs(closed_map)

                if target_link and target_link.startswith("http"):
                    try:
                        import requests
                        r = requests.get(target_link, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
                        phrases = extract_generic_closure_phrases(r.text)
                        if phrases:
                            kb = load_closed_keywords_kb()
                            kb.extend(phrases)
                            save_closed_keywords_kb(kb)
                    except Exception:
                        pass

                threading.Thread(target=recheck_existing_open_jobs_for_closure, daemon=True).start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "reported_id": j_id}).encode("utf-8"))

        elif path == "/api/reopen-job":
            j_id = qs.get("id", [""])[0]
            if j_id:
                closed_map = load_reported_closed_jobs()
                if j_id in closed_map:
                    del closed_map[j_id]
                    save_reported_closed_jobs(closed_map)
                    add_scraper_log(f"🔓 Re-opened scheme ID: {j_id}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "reopened_id": j_id}).encode("utf-8"))

        elif path == "/api/mark-applied":
            comp = qs.get("company", [""])[0]
            title = qs.get("title", [""])[0]
            if comp and title:
                update_google_sheet_via_webhook(comp, title, "Applied", "Direct Apply")
                add_scraper_log(f"✅ Marked applied via Web UI: {comp} - {title}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "company": comp, "title": title}).encode("utf-8"))

        elif path == "/api/calendar.ics":
            summary = qs.get("summary", ["Application Deadline"])[0]
            desc = qs.get("desc", ["Logged via ApplicationTrackr"])[0]
            ics_content = generate_apple_calendar_ics(summary, desc)
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="application_deadline.ics"')
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(ics_content.encode("utf-8"))

        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len).decode('utf-8')
        params = parse_qs(body)

        if path == "/api/settings":
            settings = load_settings()

            def parse_list(val_str):
                return [x.strip() for x in val_str.split(",") if x.strip()]

            if "my_skills" in params:
                settings["my_skills"] = parse_list(params["my_skills"][0])
            if "exclude_keywords" in params:
                settings["exclude_keywords"] = parse_list(params["exclude_keywords"][0])
            if "exclude_locations" in params:
                settings["exclude_locations"] = parse_list(params["exclude_locations"][0])
            if "greenhouse_companies" in params:
                settings["greenhouse_companies"] = parse_list(params["greenhouse_companies"][0])
            if "lever_companies" in params:
                settings["lever_companies"] = parse_list(params["lever_companies"][0])
            if "ashby_companies" in params:
                settings["ashby_companies"] = parse_list(params["ashby_companies"][0])
            if "smartrecruiters_companies" in params:
                settings["smartrecruiters_companies"] = parse_list(params["smartrecruiters_companies"][0])

            settings["auto_hide_applied_company_jobs"] = ("auto_hide_applied_company_jobs" in params)

            save_settings(settings)
            self.send_response(302)
            self.send_header("Location", "/settings")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def start_web_server(port=PORT):
    from config import HP_STREAM_TAILSCALE_IP
    server = ThreadedHTTPServer(("0.0.0.0", port), CleanHandler)
    print(f"🌍 Threaded Web Dashboard running at: http://{HP_STREAM_TAILSCALE_IP}:{port} (Local: http://127.0.0.1:{port})")
    server.serve_forever()

