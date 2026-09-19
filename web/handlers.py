import json
import threading
from urllib.parse import parse_qs, urlparse
import http.server
import socketserver

from config import APP_BASE_URL, PORT, add_scraper_log
from core.storage import hide_job, load_reported_closed_jobs, load_settings, save_reported_closed_jobs, save_settings
from core.kb import load_closed_keywords_kb, save_closed_keywords_kb, extract_generic_closure_phrases
from sheets import generate_sankey_from_google_sheets, update_google_sheet_via_webhook
from scrapers_engine.audit import run_all_scrapers, recheck_existing_open_jobs_for_closure, purge_irrelevant_jobs
from web.autoapply_view import render_autoapply_html
from web.views import render_unified_dashboard_html
from autoapply.learning import learn_answer, learn_mapping
from autoapply.service import (
    autopilot_candidates,
    get_batch,
    get_run,
    start_application_run,
    start_autopilot_batch,
    status as autoapply_status,
)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CleanHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def safe_write(self, payload):
        try:
            self.wfile.write(payload)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, payload, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_cors_headers()
        self.end_headers()
        self.safe_write(json.dumps(payload, indent=2).encode("utf-8"))

    def _html(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_cors_headers()
        self.end_headers()
        self.safe_write(payload.encode("utf-8"))

    def _raw(self, payload, content_type, cache_control="public, max-age=3600"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_cors_headers()
        self.end_headers()
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.safe_write(payload)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path == "/manifest.webmanifest":
            manifest = {
                "name": "ApplicationTrackr",
                "short_name": "AppTrackr",
                "description": "Application tracking and UK early-career job discovery",
                "start_url": "/",
                "scope": "/",
                "display": "standalone",
                "background_color": "#f5f7fa",
                "theme_color": "#ffffff",
                "icons": [
                    {
                        "src": "/app-icon.svg",
                        "sizes": "any",
                        "type": "image/svg+xml",
                        "purpose": "any maskable"
                    }
                ]
            }
            return self._raw(
                json.dumps(manifest, separators=(",", ":")),
                "application/manifest+json; charset=utf-8",
            )
        if path == "/app-icon.svg":
            icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">
<rect width="180" height="180" rx="38" fill="#ffffff"/>
<rect x="12" y="12" width="156" height="156" rx="31" fill="#ff8000"/>
<path d="M90 38v104M116 53H77c-17 0-27 9-27 23s10 23 27 23h27c17 0 27 9 27 23s-10 23-27 23H60" fill="none" stroke="#fff" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""
            return self._raw(icon, "image/svg+xml; charset=utf-8", "public, max-age=86400")
        if path == "/":
            return self._html(render_unified_dashboard_html("flow"))
        if path in ("/jobs", "/discovered"):
            return self._html(render_unified_dashboard_html("jobs"))
        if path == "/settings":
            return self._html(render_unified_dashboard_html("settings"))
        if path in ("/status", "/diagnostics"):
            return self._html(render_unified_dashboard_html("diagnostics"))
        if path == "/closed":
            return self._html(render_unified_dashboard_html("closed"))
        if path == "/autoapply":
            return self._html(render_autoapply_html(qs.get("job_id", [""])[0]))
        if path == "/sankey-embed":
            try:
                generate_sankey_from_google_sheets(force_refresh=True)
                page = open("sankey_diagram.html", encoding="utf-8").read()
            except Exception:
                page = "<html><body><h3>Sankey Diagram Loading...</h3></body></html>"
            return self._html(page)
        if path == "/api/status":
            from config import SCRAPER_STATUS
            return self._json(SCRAPER_STATUS)
        if path == "/api/kb-status":
            phrases = load_closed_keywords_kb()
            return self._json({"count": len(phrases), "phrases": phrases})
        if path == "/api/logs":
            from config import get_scraper_logs
            return self._json({"logs": get_scraper_logs()})
        if path == "/api/clear-logs":
            from config import clear_scraper_logs
            clear_scraper_logs()
            return self._json({"status": "ok"})
        if path == "/api/autoapply/status":
            return self._json(autoapply_status())
        if path == "/api/autoapply/candidates":
            limit = qs.get("limit", ["20"])[0]
            try:
                limit = max(1, min(100, int(limit)))
            except Exception:
                limit = 20
            return self._json({"candidates": autopilot_candidates(limit)})
        if path == "/api/autoapply/run":
            run_id = qs.get("id", [""])[0]
            payload = get_run(run_id) if run_id else None
            return self._json(payload or {"status": "not_found"}, 200 if payload else 404)
        if path == "/api/autoapply/batch":
            batch_id = qs.get("id", [""])[0]
            payload = get_batch(batch_id) if batch_id else None
            return self._json(payload or {"status": "not_found"}, 200 if payload else 404)
        if path == "/api/relevance-audit":
            return self._json({"status": "ok", "removed": purge_irrelevant_jobs()})
        if path == "/api/mark-applied":
            comp = qs.get("company", [""])[0]
            title = qs.get("title", ["Software/Quant Role"])[0]
            stage = qs.get("stage", ["Applied"])[0]
            if comp:
                update_google_sheet_via_webhook(comp, stage, role=title, resolve_sequential=True)
                add_scraper_log(f"📊 Web UI logged application: {comp} -> {stage}")
                try: generate_sankey_from_google_sheets(force_refresh=True)
                except Exception: pass
            return self._json({"status": "ok", "company": comp, "stage": stage})
        if path == "/api/resolve-pending-update":
            from core.storage import remove_pending_email_update
            u_id = qs.get("id", [""])[0]
            comp = qs.get("company", [""])[0]
            title = qs.get("role", ["Software/Quant Role"])[0]
            stage = qs.get("stage", ["Rejected"])[0]
            if comp and title:
                update_google_sheet_via_webhook(comp, stage, role=title, resolve_sequential=True)
                if u_id: remove_pending_email_update(u_id)
            return self._json({"status": "ok"})
        if path == "/api/dismiss-pending-update":
            from core.storage import remove_pending_email_update
            u_id = qs.get("id", [""])[0]
            if u_id: remove_pending_email_update(u_id)
            return self._json({"status": "ok"})
        if path == "/api/rescan":
            add_scraper_log("⚡ Triggered Scraper Rescan from Dashboard UI")
            threading.Thread(target=run_all_scrapers, daemon=True).start()
            self.send_response(302); self.send_header("Location", "/jobs"); self.end_headers(); return
        if path == "/api/sync-sheet":
            try: generate_sankey_from_google_sheets(force_refresh=True)
            except Exception as exc: add_scraper_log(f"⚠️ Sheet sync error: {exc}")
            self.send_response(302); self.send_header("Location", "/"); self.end_headers(); return
        if path == "/api/hide-job":
            j_id = qs.get("id", [""])[0]
            if j_id: hide_job(j_id)
            self.send_response(302); self.send_header("Location", "/jobs"); self.end_headers(); return
        if path == "/api/report-closed":
            j_id = qs.get("id", [""])[0]
            link = qs.get("link", [""])[0]
            if j_id:
                closed = load_reported_closed_jobs()
                closed[j_id] = {"id": j_id, "link": link, "company": "Reported Company", "title": "Reported Position", "date_reported": "Recently"}
                save_reported_closed_jobs(closed)
                if link.startswith("http"):
                    try:
                        import requests
                        page = requests.get(link, timeout=4, headers={"User-Agent":"Mozilla/5.0"}).text
                        phrases = extract_generic_closure_phrases(page)
                        if phrases:
                            kb = load_closed_keywords_kb(); kb.extend(phrases); save_closed_keywords_kb(kb)
                    except Exception: pass
                threading.Thread(target=recheck_existing_open_jobs_for_closure, daemon=True).start()
            return self._json({"status":"ok","reported_id":j_id})
        if path == "/api/reopen-job":
            j_id = qs.get("id", [""])[0]
            closed = load_reported_closed_jobs()
            if j_id in closed:
                del closed[j_id]; save_reported_closed_jobs(closed)
            return self._json({"status":"ok","reopened_id":j_id})
        return self._json({"status":"not_found","path":path},404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8") if length else "")
        if path == "/api/settings":
            settings = load_settings()
            def csv(key): return [x.strip() for x in form.get(key,[""])[0].split(",") if x.strip()]
            for key in ("my_skills","exclude_keywords","exclude_locations","greenhouse_companies","lever_companies","ashby_companies","smartrecruiters_companies"):
                settings[key] = csv(key)
            settings["auto_hide_applied_company_jobs"] = "auto_hide_applied_company_jobs" in form
            save_settings(settings)
            removed = purge_irrelevant_jobs()
            add_scraper_log(f"⚙️ Saved filter settings; relevance audit removed {removed} listings")
            self.send_response(302); self.send_header("Location", "/settings"); self.end_headers(); return
        if path == "/api/autoapply/run":
            run_id = start_application_run(job_id=form.get("job_id",[""])[0], url=form.get("url",[""])[0], auto_submit=form.get("auto_submit",["false"])[0].lower() in {"1","true","yes"})
            return self._json({"status":"queued","run_id":run_id})
        if path == "/api/autoapply/autopilot":
            try:
                limit = int(form.get("limit", ["0"])[0] or 0) or None
            except Exception:
                limit = None
            batch_id = start_autopilot_batch(
                limit=limit,
                auto_submit=form.get("auto_submit",["false"])[0].lower() in {"1","true","yes"},
            )
            return self._json({"status":"queued","batch_id":batch_id})
        if path == "/api/autoapply/learn-mapping":
            label, key = form.get("label",[""])[0], form.get("profile_key",[""])[0]
            if not label or not key: return self._json({"status":"error","message":"label and profile_key required"},400)
            learn_mapping(
                label,
                key,
                context=form.get("context",[""])[0],
                domain=form.get("domain",[""])[0],
            )
            return self._json({"status":"ok"})
        if path == "/api/autoapply/learn-answer":
            question, answer = form.get("question",[""])[0], form.get("answer",[""])[0]
            if not question: return self._json({"status":"error","message":"question required"},400)
            learn_answer(question, answer, domain=form.get("domain",[""])[0])
            return self._json({"status":"ok"})
        return self._json({"status":"not_found","path":path},404)


def start_web_server():
    server = ThreadedHTTPServer(("0.0.0.0", PORT), CleanHandler)
    print(f"🌐 Threaded Web Dashboard running at: {APP_BASE_URL} (container-local: http://127.0.0.1:{PORT})")
    server.serve_forever()
