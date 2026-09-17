from logging_utils import install_quiet_logging

install_quiet_logging()

import time
import threading

from notifications import send_notification, send_heartbeat_ping
from sheets import generate_sankey_from_google_sheets
from email_listener import check_email_inbox
from scrapers import run_all_scrapers
from scheduler import scheduler_loop
from web_server import start_web_server
from config import SCRAPER_INTERVAL_SECONDS, EMAIL_POLL_SECONDS


def email_listener_loop():
    """Poll application inboxes independently of slow scraper cycles."""
    while True:
        started = time.time()
        try:
            check_email_inbox()
        except Exception as exc:
            print(f"⚠️ Email listener loop error: {type(exc).__name__}: {exc}")
        elapsed = time.time() - started
        time.sleep(max(5, EMAIL_POLL_SECONDS - elapsed))


if __name__ == "__main__":
    generate_sankey_from_google_sheets()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

    email_thread = threading.Thread(target=email_listener_loop, daemon=True, name="email-listener")
    email_thread.start()

    time.sleep(1)

    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 🚀 APPLICATIONTRACKR MODULAR ENGINE ONLINE                             │
│    UK Early Career Job Discovery & Application Automation              │
└────────────────────────────────────────────────────────────────────────┘""")

    send_notification(
        title="ApplicationTrackr Online",
        message="Modular Engine Active: Scrapers + Email Inbox + Web Dashboard + Watchdog.",
        tags="rocket,uk",
        priority=3,
        sound="chime"
    )

    while True:
        run_all_scrapers()
        generate_sankey_from_google_sheets()
        send_heartbeat_ping()
        time.sleep(SCRAPER_INTERVAL_SECONDS)
