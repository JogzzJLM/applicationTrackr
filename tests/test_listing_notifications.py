import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import notifications
import sheets


@pytest.fixture
def alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, 'NOTIFIED_FILE', str(tmp_path / 'notified.json'))
    monkeypatch.setattr(notifications, 'OUTBOX_FILE', str(tmp_path / 'outbox.json'))
    monkeypatch.setattr(notifications, 'update_scraper_status', Mock())
    monkeypatch.setattr(sheets, 'get_applied_jobs_set', lambda: (set(), set()))
    publish = Mock(return_value=True)
    monkeypatch.setattr(notifications, '_publish_notification', publish)
    return publish


def listing(number, company='Acme', **extra):
    return {'id': f'job-{number}', 'company': company, 'title': 'Software Engineering Intern',
            'location': 'London, UK', 'link': f'https://boards.greenhouse.io/acme/jobs/{number}',
            'verification': {'state': 'verified'},
            'deadline': (datetime.now() + timedelta(days=20)).date().isoformat(), **extra}


def test_each_new_listing_gets_its_own_direct_link_alert_without_backlog(alerts):
    old, first, second = listing(1), listing(2, 'Figma'), listing(3, 'Monzo')
    notifications.initialize_discovery_alerts([old])
    assert notifications.notify_discoveries([old, first, second]) == 2
    assert alerts.call_count == 2
    messages = {call.kwargs['link']: call.kwargs for call in alerts.call_args_list}
    assert set(messages) == {first['link'], second['link']}
    for job in (first, second):
        sent = messages[job['link']]
        assert job['company'] in sent['title'] and job['title'] in sent['title']
        assert job['location'] in sent['message'] and job['deadline'] in sent['message']
        assert 'Verified and saved' in sent['message']


def test_rescan_duplicate_sources_and_restarts_do_not_repeat_alerts(alerts):
    job = listing(1)
    notifications.initialize_discovery_alerts([])
    assert notifications.notify_discoveries([job, job]) == 1
    alternate = {**job, 'id': 'trackr-other-id',
                 'link': 'https://job-boards.greenhouse.io/acme/jobs/1?utm_source=trackr'}
    notifications.initialize_discovery_alerts([job])
    assert notifications.notify_discoveries([alternate]) == 0
    # The remembered alias also survives a later link change.
    assert notifications.notify_discoveries([{**alternate, 'link': 'https://acme.com/internship'}]) == 0
    assert alerts.call_count == 1


def test_different_requisitions_with_the_same_title_both_notify(alerts):
    notifications.initialize_discovery_alerts([])
    assert notifications.notify_discoveries([listing(1), listing(2)]) == 2
    assert alerts.call_count == 2


def test_unknown_expired_unsuitable_and_applied_listings_do_not_notify(alerts, monkeypatch):
    unknown = listing(1, verification={'state': 'unknown'})
    expired = listing(2, deadline=(datetime.now() - timedelta(days=1)).date().isoformat())
    unsuitable = listing(3, title='Marketing Internship')
    applied = listing(4)
    monkeypatch.setattr(sheets, 'get_applied_jobs_set', lambda: ({('acme', 'software engineering intern')}, {'acme'}))
    notifications.initialize_discovery_alerts([])
    assert notifications.notify_discoveries([unknown, expired, unsuitable, applied]) == 0
    alerts.assert_not_called()


def test_previously_unverified_listing_notifies_when_it_becomes_usable(alerts):
    job = listing(1, verification={'state': 'unknown'})
    notifications.initialize_discovery_alerts([job])
    assert notifications.notify_discoveries([job]) == 0
    assert notifications.notify_discoveries([{**job, 'verification': {'state': 'verified'}}]) == 1
    assert alerts.call_count == 1


def test_saved_job_not_yet_queued_survives_a_restart(alerts):
    notifications.initialize_discovery_alerts([])
    # A scan saved this job, then the process stopped before alerting it.
    job = listing(1)
    notifications.initialize_discovery_alerts([job])
    assert notifications.notify_discoveries([job]) == 1
    assert alerts.call_count == 1


def test_failed_individual_alert_retries_without_being_queued_twice(alerts):
    alerts.return_value = False
    job = listing(1)
    notifications.initialize_discovery_alerts([])
    assert notifications.notify_discoveries([job]) == 1
    notifications.initialize_discovery_alerts([job])
    assert notifications.notify_discoveries([job]) == 0
    state = json.loads(open(notifications.OUTBOX_FILE).read())
    assert len(state['pending']) == 1
    next(iter(state['pending'].values()))['next_retry'] = 0
    with open(notifications.OUTBOX_FILE, 'w') as handle:
        json.dump(state, handle)
    alerts.return_value = True
    notifications.flush_notification_outbox()
    assert notifications.notify_discoveries([job]) == 0
    state = json.loads(open(notifications.OUTBOX_FILE).read())
    assert not state['pending'] and len(state['delivered']) == 1
    assert alerts.call_count == 2


def test_backing_off_messages_do_not_block_new_listing_delivery(alerts):
    notifications.initialize_discovery_alerts([])
    alerts.return_value = False
    for number in range(10):
        notifications.send_notification('Old alert', 'Retry later', event_id=f'old-{number}')
    alerts.return_value = True
    assert notifications.notify_discoveries([listing(11)]) == 1
    assert alerts.call_count == 11
    state = json.loads(open(notifications.OUTBOX_FILE).read())
    assert len(state['pending']) == 10 and len(state['delivered']) == 1
