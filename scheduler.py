"""Useful scheduled job digests, with catch-up and persistent delivery identities."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from config import APP_BASE_URL, APP_TIMEZONE, SCRAPER_STATUS, DISCOVERY_SCAN_READY
from notifications import send_notification, job_alert_line, flush_notification_outbox
from core.storage import DATA_DIR, load_json_safe, atomic_write_json
from scrapers_engine.verifier import deadline_date
from sheets import parse_sheet_stats

SCHEDULE_FILE = str(DATA_DIR / 'notification_schedule.json')


def actionable_jobs():
    from scrapers_engine.audit import load_discovered_jobs
    from core.normalization import normalize_company, fuzzy_roles_match
    from sheets import get_applied_jobs_set
    from scrapers_engine.quality import is_actionable_listing
    applied, _ = get_applied_jobs_set()
    today = datetime.now(ZoneInfo(APP_TIMEZONE)).date()
    return [j for j in load_discovered_jobs() if j.get('verification', {}).get('state') == 'verified'
            and is_actionable_listing(j, today=today)
            and not any(normalize_company(j['company']) == c and fuzzy_roles_match(j['title'], r, threshold=.90) for c, r in applied)]


def trigger_daily_briefing(period='morning', now=None):
    now = now or datetime.now(ZoneInfo(APP_TIMEZONE))
    jobs = actionable_jobs()
    recent = [j for j in jobs if str(j.get('date_found', '')) >= (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')]
    soon = [j for j in jobs if deadline_date(j.get('deadline')) and 0 <= (deadline_date(j['deadline']) - now.date()).days <= 7]
    soon.sort(key=lambda j: j['deadline'])
    highlights = (soon + sorted(recent, key=lambda j: -j.get('match_score', 0)))
    if not highlights: highlights = sorted(jobs, key=lambda j: -j.get('match_score', 0))
    unique = {j['link']: j for j in highlights}
    lines = [f'{len(jobs)} verified, unapplied jobs | {len(recent)} found in the last 24h | {len(soon)} close within 7 days.']
    lines.extend(job_alert_line(j) for j in list(unique.values())[:5])
    sources = SCRAPER_STATUS.get('source_status', {})
    issues = [name for name, state in sources.items() if state.startswith('🟠') or any(word in state.lower() for word in ('unavailable', 'blocked', 'error', 'failed'))]
    if issues: lines.append('Sources needing attention: ' + ', '.join(issues))
    lines.append('Last discovery scan: ' + str(SCRAPER_STATUS.get('last_run', 'Not yet completed')))
    return send_notification('Morning jobs briefing' if period == 'morning' else 'Evening jobs & deadlines', '\n\n'.join(lines),
                             link=f'{APP_BASE_URL}/jobs', tags='briefcase,calendar', priority=3,
                             event_id=f'digest:{now.date()}:{period}')


def trigger_weekly_report():
    stats = parse_sheet_stats()
    return send_notification('Weekly application progress',
        f"Applied: {stats['total']} | Active rounds: {stats['active']} | Offers: {stats['offers']} | Rejections: {stats['rejections']}",
        link=f'{APP_BASE_URL}/', tags='bar_chart', priority=3,
        event_id=f'weekly:{datetime.now(ZoneInfo(APP_TIMEZONE)).date()}')


def scheduled_tick(now=None):
    flush_notification_outbox()
    now = now or datetime.now(ZoneInfo(APP_TIMEZONE))
    state = load_json_safe(SCHEDULE_FILE, {})
    # Catch up the most recent slot if a restart missed the exact minute.
    period = 'evening' if now.hour >= 18 else ('morning' if now.hour >= 8 else None)
    key = f'{now.date()}:{period}'
    if period and key not in state and DISCOVERY_SCAN_READY.is_set():
        trigger_daily_briefing(period, now)
        # Delivery is now owned by the persistent outbox, including failures.
        state[key] = True
    if now.weekday() == 6 and now.hour >= 18 and f'weekly:{now.date()}' not in state:
        trigger_weekly_report(); state[f'weekly:{now.date()}'] = True
    atomic_write_json(SCHEDULE_FILE, dict(list(state.items())[-60:]))


def scheduler_loop():
    while True:
        try: scheduled_tick()
        except Exception as exc: print(f'Scheduler error ({type(exc).__name__}); retrying next minute')
        time.sleep(30)
