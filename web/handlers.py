import os
import json
import urllib.parse
import http.server
import socketserver
import threading
from urllib.parse import parse_qs, urlparse, quote

from config import APP_BASE_URL, PORT, SCRAPER_STATUS, add_scraper_log
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
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
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
            generate_sankey_from_google_sheets(force_refresh=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_cors_headers()
            self.end_headers()
            try:
                with open("sankey_diagram.html", "r", encoding="utf-8") as f:
                    html = f.read()
            except Exception:
                html = "<html><body><h3>Sankey Diagram Loading...</h3></body></html>"
            self.wfile.write(html.encode("utf-8"))

        elif path == "/api/status":
            from config import SCRAPER_STATUS
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(SCRAPER_STATUS, indent=2).encode("utf-8"))

        elif path == "/api/kb-status":
            kb_phrases = load_closed_keywords_kb()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"count": len(kb_phrases), "phrases": kb_phrases}, indent=2).encode("utf-8"))

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

        elif path == "/api/mark-applied":
            comp = qs.get("company", [""])[0]
            title = qs.get("title", ["Software/Quant Role"])[0]
            stage = qs.get("stage", ["Applied"])[0]
            if comp:
                update_google_sheet_via_webhook(comp, stage, role=title, resolve_sequential=True)
                add_scraper_log(f"📊 Web UI logged application: {comp} -> {stage}")
                try:
                    generate_sankey_from_google_sheets(force_refresh=True)
                except Exception:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "company": comp, "stage": stage}).encode("utf-8"))

        elif path == "/api/resolve-pending-update":
            from core.storage import remove_pending_email_update
            u_id = qs.get("id", [""])[0]
            comp = qs.get("company", [""])[0]
            title = qs.get("role", ["Software/Quant Role"])[0]
            stage = qs.get("stage", ["Rejected"])[0]

            if comp and title:
                update_google_sheet_via_webhook(comp, stage, role=title, resolve_sequential=True)
                add_scraper_log(f"✅ User resolved email update: {comp} ({title}) -> {stage}")
                if u_id:
                    remove_pending_email_update(u_id)
                try:
                    generate_sankey_from_google_sheets(force_refresh=True)
                except Exception:
                    pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        elif path == "/api/dismiss-pending-update":
            from core.storage import remove_pending_email_update
            u_id = qs.get("id", [""])[0]
            if u_id:
                remove_pending_email_update(u_id)
                add_scraper_log(f"🙈 User dismissed pending email update ID: {u_id}")
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
                generate_sankey_from_google_sheets(force_refresh=True)
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/settings":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8")
            form = parse_qs(post_data)

            settings = load_settings()

            def parse_csv_input(key):
                raw = form.get(key, [""])[0]
                return [x.strip() for x in raw.split(",") if x.strip()]

            settings["my_skills"] = parse_csv_input("my_skills")
            settings["exclude_keywords"] = parse_csv_input("exclude_keywords")
            settings["exclude_locations"] = parse_csv_input("exclude_locations")
            settings["greenhouse_companies"] = parse_csv_input("greenhouse_companies")
            settings["lever_companies"] = parse_csv_input("lever_companies")
            settings["ashby_companies"] = parse_csv_input("ashby_companies")
            settings["smartrecruiters_companies"] = parse_csv_input("smartrecruiters_companies")
            settings["auto_hide_applied_company_jobs"] = "auto_hide_applied_company_jobs" in form

            save_settings(settings)
            add_scraper_log("⚙️ Saved updated filter settings via Web Interface")

            self.send_response(302)
            self.send_header("Location", "/settings")
            self.end_headers()

def start_web_server():
    server = ThreadedHTTPServer(("0.0.0.0", PORT), CleanHandler)
    print(f"🌐 Threaded Web Dashboard running at: {APP_BASE_URL} (container-local: http://127.0.0.1:{PORT})")
    server.serve_forever()
