import time
import threading

from notifications import send_notification, send_heartbeat_ping
from sheets import generate_sankey_from_google_sheets
from email_listener import check_email_inbox
from scrapers import run_all_scrapers
from scheduler import scheduler_loop
from web_server import start_web_server

if __name__ == "__main__":
    generate_sankey_from_google_sheets()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

    time.sleep(1)

    print("\n🚀 ApplicationTrackr Modular Engine Online!")
    send_notification(
        title="ApplicationTrackr Online",
        message="Modular Engine Active: Scrapers + Email Inbox + Web Dashboard + Watchdog.",
        tags="rocket,uk",
        priority=3,
        sound="chime"
    )

    while True:
        check_email_inbox()
        run_all_scrapers()
        generate_sankey_from_google_sheets()
        send_heartbeat_ping()
        time.sleep(1800)