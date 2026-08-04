import time
from datetime import datetime
from config import HP_STREAM_TAILSCALE_IP, PORT, SCRAPER_STATUS
from notifications import send_notification
from sheets import parse_sheet_stats

def trigger_daily_briefing():
    stats = parse_sheet_stats()
    new_jobs_today = SCRAPER_STATUS.get("last_new_jobs_found", 0)

    send_notification(
        title="Daily Morning Briefing",
        message=f"Good morning! Total Logged: {stats['total']} | Active/Pending Rounds: {stats['active']} | New Schemes Found Yesterday: {new_jobs_today}",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/sankey",
        tags="sun,briefcase",
        priority=3,
        sound="bing"
    )

def trigger_weekly_report():
    stats = parse_sheet_stats()
    total = stats["total"]
    conv_rate = round((stats["active"] + stats["offers"]) / total * 100, 1) if total > 0 else 0.0

    send_notification(
        title="Sunday Weekly Funnel Report",
        message=f"Weekly Funnel Summary: Applied Total: {total} | Active Rounds: {stats['active']} | Offers: {stats['offers']} | Rejections: {stats['rejections']} | Conversion Rate: {conv_rate}%",
        link=f"http://{HP_STREAM_TAILSCALE_IP}:{PORT}/sankey",
        tags="bar_chart,trophy",
        priority=4,
        sound="fanfare"
    )

def scheduler_loop():
    last_daily_date = ""
    last_weekly_date = ""

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            if now.hour == 8 and now.minute == 0 and last_daily_date != today_str:
                trigger_daily_briefing()
                last_daily_date = today_str

            if now.weekday() == 6 and now.hour == 18 and now.minute == 0 and last_weekly_date != today_str:
                trigger_weekly_report()
                last_weekly_date = today_str

        except Exception as e:
            print(f"⚠️ Scheduler Error: {e}")

        time.sleep(30)