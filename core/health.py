"""Observed integration health; process liveness does not imply a healthy scan."""
import re
from datetime import datetime, timezone
from config import SCRAPER_STATUS


def engine_health(status=None):
    status = SCRAPER_STATUS if status is None else status
    online = total = 0
    for message in status.get('source_status', {}).values():
        match = re.search(r'(\d+)/(\d+) employer feeds online', str(message))
        if match:
            online += int(match[1]); total += int(match[2])
    issues = []
    if total and online < total:
        issues.append(f'{total - online} employer feeds unavailable')
    for key, label in (('mail_health', 'Email'), ('sheet_health', 'Sheet'), ('ntfy', 'Notifications')):
        health = status.get(key, {})
        if health and health.get('ok') is False:
            issues.append(f'{label} needs attention')
    scan = status.get('scan_health', {})
    if scan.get('ok') is False:
        issues.append('Last discovery scan failed')
    timestamp = scan.get('completed_at') or scan.get('started_at')
    if timestamp:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
            if age > 3600:
                issues.append('Discovery scan overdue')
        except (ValueError, TypeError):
            issues.append('Discovery timestamp unavailable')
    state = 'degraded' if issues else ('starting' if not timestamp else 'ok')
    label = 'Needs attention' if issues else ('Finding jobs' if scan.get('running') else 'Engine Online' if timestamp else 'Awaiting health check')
    return {'status': state, 'label': label, 'issues': issues, 'scan': scan,
            'employer_feeds_online': online, 'employer_feeds_total': total,
            'source_connection_percent': round(online / total * 100) if total else 0}
