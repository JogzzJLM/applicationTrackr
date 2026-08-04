import requests
from config import NTFY_TOPIC, HEALTHCHECKS_PING_URL

def send_notification(title, message, link=None, tags="briefcase", priority=3, sound="chime"):
    clean_title = title.encode("ascii", "ignore").decode("ascii").strip()
    if not clean_title:
        clean_title = "ApplicationTrackr Alert"

    headers = {
        "Title": clean_title,
        "Tags": tags,
        "Priority": str(priority),
        "Sound": sound
    }
    if link:
        headers["Click"] = link

    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        print(f"✅ Notification Sent [{sound}]: {clean_title}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")

def send_heartbeat_ping():
    if HEALTHCHECKS_PING_URL and "YOUR_HEALTHCHECKS_UUID" not in HEALTHCHECKS_PING_URL:
        try:
            requests.get(HEALTHCHECKS_PING_URL, timeout=10)
            print("💓 Sent Watchdog Heartbeat Ping to Healthchecks.io")
        except Exception as e:
            print(f"⚠️ Heartbeat Ping Error: {e}")