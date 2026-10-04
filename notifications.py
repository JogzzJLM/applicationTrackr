import re
import hashlib
import threading
import time
import requests
from datetime import datetime, timezone
from config import APP_BASE_URL, NTFY_BASE_URL, NTFY_TOPIC, NTFY_TOKEN, HEALTHCHECKS_PING_URL, update_scraper_status

def _publish_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime"):
    # Sound is retained for callers; notification sounds are controlled by the client.
    if not NTFY_TOPIC or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", NTFY_TOPIC):
        update_scraper_status("ntfy", {"ok": False, "error": "Set a valid NTFY_TOPIC in the stack environment."})
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode().replace("\n", " ").replace("\r", " ").strip() or "ApplicationTrackr Alert",
               "Tags": tags, "Priority": str(priority), "Content-Type": "text/plain; charset=utf-8"}
    if link:
        headers["Click"] = link
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:
        response = requests.post(f"{NTFY_BASE_URL}/{NTFY_TOPIC}", data=message.encode("utf-8"), headers=headers, timeout=10)
        if not 200 <= response.status_code < 300:
            raise ValueError(f"ntfy returned HTTP {response.status_code}; check topic permissions, token and server.")
        receipt = response.json()
        if receipt.get("event") != "message" or not receipt.get("id"):
            raise ValueError("ntfy did not return a message receipt.")
        update_scraper_status("ntfy", {"ok": True, "message_id": receipt["id"], "last_sent": datetime.now(timezone.utc).isoformat()})
        print("Notification accepted by ntfy.")
        return True
    except Exception as exc:
        # Do not log exception URLs: topics and tokens may be private.
        error = str(exc) if isinstance(exc, ValueError) else f"ntfy request failed ({type(exc).__name__})."
        update_scraper_status("ntfy", {"ok": False, "error": error})
        print(error)
        return False

# Persist before sending. Discovery/email events survive a rejected publish or restart.
from core.storage import DATA_DIR, atomic_write_json, load_json_safe
OUTBOX_FILE = str(DATA_DIR / "notification_outbox.json")
NOTIFIED_FILE = str(DATA_DIR / "notified_listings.json")
_OUTBOX_LOCK = threading.RLock()
_DISCOVERY_ALERT_LOCK = threading.RLock()
_ALERT_BASELINE_MARKER = 'per-listing-alerts:v1:initialized'


def _outbox():
    return load_json_safe(OUTBOX_FILE, {"pending": {}, "delivered": {}})


def send_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime", event_id=None):
    if len(message.encode("utf-8")) > 3800:
        message = message.encode("utf-8")[:3700].decode("utf-8", "ignore") + "\nFull details in ApplicationTrackr."
    event_id = event_id or hashlib.sha256((title + message + str(link) + str(int(time.time() // 600))).encode()).hexdigest()
    with _OUTBOX_LOCK:
        state = _outbox()
        if event_id in state["delivered"]: return True
        state["pending"].setdefault(event_id, {"title": title, "message": message, "link": link,
            "tags": tags, "priority": priority, "sound": sound, "attempts": 0, "next_retry": 0})
        atomic_write_json(OUTBOX_FILE, state)
    flush_notification_outbox()
    with _OUTBOX_LOCK:
        return event_id in _outbox()["delivered"]


def flush_notification_outbox():
    with _OUTBOX_LOCK:
        state = _outbox()
        due = [(key, item) for key, item in state['pending'].items() if item.get('next_retry', 0) <= time.time()][:10]
        for key, item in due:
            args = {k: item[k] for k in ("title", "message", "link", "tags", "priority", "sound")}
            if _publish_notification(**args):
                state["delivered"][key] = time.time()
                del state["pending"][key]
            else:
                item["attempts"] += 1
                item["next_retry"] = time.time() + min(3600, 60 * 2 ** min(item["attempts"], 6))
            atomic_write_json(OUTBOX_FILE, state)
        state["delivered"] = dict(sorted(state["delivered"].items(), key=lambda x: x[1], reverse=True)[:1500])
        atomic_write_json(OUTBOX_FILE, state)
        update_scraper_status("notification_queue", {"pending": len(state["pending"]), "delivered": len(state["delivered"])})


def job_alert_line(job):
    deadline = job.get("deadline") or job.get("metadata", {}).get("closing_date") or "Deadline not published"
    return f"{job['company']} — {job['title']}\n{job.get('location', 'Unknown')} | Closes: {deadline}\n{job['link']}"


def listing_alert_payload(job):
    message = job_alert_line(job) + '\n\nVerified and saved to ApplicationTrackr.'
    reasons = job.get('match_reasons', [])[:2]
    if reasons: message += '\nSuitability checks: ' + '; '.join(reasons)
    message += f'\nTracker: {APP_BASE_URL}/jobs'
    return {'title': f"New job: {job['company']} - {job['title']}"[:180], 'message': message,
            'link': job['link'], 'tags': 'briefcase,bell', 'priority': 3}


def _listing_alert_keys(job):
    from core.normalization import normalize_url, extract_ats_post_id
    keys = {'url:' + normalize_url(job['link'])}
    if job.get('id'): keys.add('id:' + str(job['id']))
    ats = extract_ats_post_id(job['link'])
    if ats: keys.add('ats:' + ats)
    return keys


def initialize_discovery_alerts(existing_jobs):
    """Baseline the saved backlog once, before a new scan can add listings."""
    from scrapers_engine.quality import is_actionable_listing
    with _DISCOVERY_ALERT_LOCK:
        notified = set(load_json_safe(NOTIFIED_FILE, []))
        baselined = 0
        if _ALERT_BASELINE_MARKER not in notified:
            for job in existing_jobs:
                if job.get('verification', {}).get('state') == 'verified' and is_actionable_listing(job):
                    notified.update(_listing_alert_keys(job))
                    baselined += 1
            notified.add(_ALERT_BASELINE_MARKER)
            atomic_write_json(NOTIFIED_FILE, sorted(notified))
        update_scraper_status('discovery_alerts', {'mode': 'per_listing', 'baseline_ready': True,
                                                 'existing_listings_baselined_this_start': baselined})


def notify_discoveries(jobs):
    # Alerts run only after the scan has saved, deduplicated and checked its feed.
    with _DISCOVERY_ALERT_LOCK:
        return _notify_discoveries(jobs)


def _notify_discoveries(jobs):
    from sheets import get_applied_jobs_set
    from core.normalization import normalize_company, normalize_url, fuzzy_roles_match
    from scrapers_engine.quality import is_actionable_listing
    applied, _ = get_applied_jobs_set()
    notified = set(load_json_safe(NOTIFIED_FILE, []))
    candidates = [j for j in jobs if j.get('verification', {}).get('state') == 'verified'
                  and is_actionable_listing(j)
                  and not any(normalize_company(j.get('company')) == c and fuzzy_roles_match(j.get('title'), r, threshold=.90) for c, r in applied)]
    candidates.sort(key=lambda j: (j.get('deadline') or '9999', -j.get('match_score', 0)))
    queued = 0
    for job in candidates:
        keys = _listing_alert_keys(job)
        if keys.intersection(notified) or normalize_url(job['link']) in notified:
            # Upgrade legacy URL receipts and remember newly discovered aliases.
            notified.update(keys)
            continue
        identity = next((k for k in sorted(keys) if k.startswith('id:')), 'url:' + normalize_url(job['link']))
        send_notification(**listing_alert_payload(job),
                          event_id='listing:' + hashlib.sha256(identity.encode()).hexdigest())
        # The durable outbox owns retries; don't enqueue the same listing again.
        notified.update(keys)
        atomic_write_json(NOTIFIED_FILE, sorted(notified))
        queued += 1
    atomic_write_json(NOTIFIED_FILE, sorted(notified))
    update_scraper_status('discovery_alerts', {'mode': 'per_listing', 'baseline_ready': _ALERT_BASELINE_MARKER in notified,
                                             'last_scan_queued': queued})
    return queued


def send_heartbeat_ping():
    if HEALTHCHECKS_PING_URL and "YOUR_HEALTHCHECKS_UUID" not in HEALTHCHECKS_PING_URL:
        try:
            requests.get(HEALTHCHECKS_PING_URL, timeout=10).raise_for_status()
            print("💓 Sent Watchdog Heartbeat Ping to Healthchecks.io")
        except Exception as e:
            print(f"⚠️ Heartbeat Ping Error: {e}")

import time
from datetime import datetime, timedelta

def generate_apple_calendar_ics(summary, description="ApplicationTrackr Reminder", location="Online / Email", start_dt=None):
    """Generates standard RFC 5545 iCalendar (.ics) format compatible with Apple Calendar on iOS and macOS."""
    if not start_dt:
        start_dt = datetime.now() + timedelta(days=2)

    dtstart = start_dt.strftime("%Y%m%dT%H%M00Z")
    dtend = (start_dt + timedelta(hours=1)).strftime("%Y%m%dT%H%M00Z")
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M00Z")

    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//ApplicationTrackr//Apple Calendar Sync//EN
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:apptrackr-{int(time.time())}@the-trackr.com
DTSTAMP:{dtstamp}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{summary}
DESCRIPTION:{description}
LOCATION:{location}
STATUS:CONFIRMED
BEGIN:VALARM
TRIGGER:-PT24H
ACTION:DISPLAY
DESCRIPTION:Reminder: {summary} in 24 hours
END:VALARM
END:VEVENT
END:VCALENDAR"""
    return ics_content
