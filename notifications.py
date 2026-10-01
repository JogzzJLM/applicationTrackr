import re
import requests
from datetime import datetime, timezone
from config import NTFY_BASE_URL, NTFY_TOPIC, NTFY_TOKEN, HEALTHCHECKS_PING_URL, update_scraper_status

def send_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime"):
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