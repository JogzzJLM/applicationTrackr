import requests
from config import NTFY_BASE_URL, NTFY_TOPIC, NTFY_TOKEN, HEALTHCHECKS_PING_URL

def send_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime"):
    clean_title = title.encode("ascii", "ignore").decode("ascii").strip()
    if not clean_title:
        clean_title = "ApplicationTrackr Alert"

    if not NTFY_TOPIC:
        return False

    headers = {
        "Title": clean_title,
        "Tags": tags,
        "Priority": str(priority),
        "Sound": sound
    }
    if link:
        headers["Click"] = link
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    try:
        requests.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        print(f"✅ Notification Sent [{sound}]: {clean_title}")
        return True
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")
        return False

def send_heartbeat_ping():
    if HEALTHCHECKS_PING_URL and "YOUR_HEALTHCHECKS_UUID" not in HEALTHCHECKS_PING_URL:
        try:
            requests.get(HEALTHCHECKS_PING_URL, timeout=10)
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