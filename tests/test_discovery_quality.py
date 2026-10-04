import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest

from core.relevance import evaluate_job
from core.storage import DEFAULT_SETTINGS
from core.normalization import same_listing, normalize_url
from scrapers_engine.trackr_scraper import trackr_metadata
from scrapers_engine import verifier, uk_boards, ats_scrapers
import notifications
import scheduler
from scrapers_engine.quality import is_actionable_listing


def test_real_software_role_is_not_excluded_by_sparse_keyword_score():
    decision = evaluate_job('Software Engineering Intern', 'Example', 'London', settings=DEFAULT_SETTINGS)
    assert decision.eligible and decision.score == 71


def test_apply_feed_never_exposes_legacy_unverified_expired_or_unsuitable_cards():
    job={'company':'Example','title':'Software Engineering Intern','location':'UK',
         'verification':{'state':'verified'}}
    assert is_actionable_listing(job, DEFAULT_SETTINGS)
    assert not is_actionable_listing({**job,'verification':{}}, DEFAULT_SETTINGS)
    assert not is_actionable_listing({**job,'deadline':(datetime.now()-timedelta(days=1)).date().isoformat()}, DEFAULT_SETTINGS)
    assert not is_actionable_listing({**job,'title':'Electro-Optic Systems Engineer Placement'}, DEFAULT_SETTINGS)
    assert is_actionable_listing({'source':'Manual'}, DEFAULT_SETTINGS)


@pytest.mark.parametrize('title', ['Electro-Optic Seekers & Systems Engineer Undergraduate Placement 2027',
                                   'Quant Trader Internship 2027', 'Software Engineer Intern - Amsterdam'])
def test_snapshot_false_matches_are_rejected(title):
    assert not evaluate_job(title, 'Example', 'UK', settings=DEFAULT_SETTINGS).eligible


def test_trackr_preserves_division_deadline_and_location_exception():
    meta = trackr_metadata({'divisions':['Tech|Software Engineering'], 'closingDate':'2026-10-18',
                           'type':'summer-internships', 'region':'UK'})
    assert meta['closing_date'] == '2026-10-18'
    assert evaluate_job('Engineering - Internship', 'HSBC', 'UK', meta, DEFAULT_SETTINGS).eligible
    meta['location_notes'] = 'Based in Amsterdam, open to UK citizens'
    assert not evaluate_job('Software Engineering Internship', 'IMC', 'UK', meta, DEFAULT_SETTINGS).eligible


@pytest.mark.parametrize('description,eligible', [
    ('Required graduation between September 2027 and July 2028.', True),
    ('Required graduation between 2027 and 2029.', True),
    ('Expected graduation in 2027.', False),
    ('At a minimum, have experience in the following areas: A post-graduate degree in Machine Learning.', False),
    ('A current undergraduate, masters or PhD student in a quantitative subject.', True),
])
def test_requirements_use_users_2028_graduation_without_rejecting_ranges(description, eligible):
    assert evaluate_job('Software Engineering Intern', 'Example', 'UK', {'description':description}, DEFAULT_SETTINGS).eligible == eligible


@pytest.mark.parametrize('status', [403, 429, 503])
def test_blocked_job_is_unknown_never_verified_or_closed(monkeypatch, status):
    monkeypatch.setattr(verifier.requests, 'get', lambda *a, **k: Mock(status_code=status))
    assert verifier._check('https://example.com/jobs/123', 'Software Engineering Intern')['state'] == 'unknown'


def test_employer_portal_cannot_count_as_job(monkeypatch):
    get = Mock()
    monkeypatch.setattr(verifier.requests, 'get', get)
    assert verifier._check('https://www.gradcracker.com/hub/115/mbda')['state'] == 'unknown'
    get.assert_not_called()


def test_greenhouse_html_location_overrides_curated_uk_assumption(monkeypatch):
    markup = '<h1>Software Engineering Intern</h1><div class="job__location">Amsterdam, Netherlands</div><div class="job__description">' + ('Develop Python software. ' * 30) + '</div><button>Apply</button>'
    monkeypatch.setattr(verifier.requests, 'get', lambda *a, **k: Mock(status_code=200, text=markup, url='https://job-boards.eu.greenhouse.io/imc/jobs/123'))
    result = verifier._check('https://job-boards.eu.greenhouse.io/imc/jobs/123', 'Software Engineering Intern')
    assert result['state'] == 'verified' and result['location'] == 'Amsterdam, Netherlands'
    assert not evaluate_job(result['title'], 'IMC', result['location'], result, DEFAULT_SETTINGS).eligible


def test_ld_job_with_expired_deadline_is_closed(monkeypatch):
    post = {'@type':'JobPosting', 'title':'Software Engineering Intern', 'description':'Python',
            'validThrough':(datetime.now() - timedelta(days=1)).date().isoformat()}
    r = Mock(status_code=200, text='<script type="application/ld+json">'+json.dumps(post)+'</script>', url='https://example.com/jobs/123')
    monkeypatch.setattr(verifier.requests, 'get', lambda *a, **k:r)
    assert verifier._check(r.url)['state'] == 'closed'
    assert verifier.deadline_date('2036-01-01') is None


def test_unrelated_script_does_not_close_a_job(monkeypatch):
    post = {'@type':'JobPosting','title':'Software Engineering Intern','description':'Build Python systems'}
    markup = '<script>const x = "job closed";</script><script type="application/ld+json">'+json.dumps(post)+'</script>'
    monkeypatch.setattr(verifier.requests, 'get', lambda *a, **k:Mock(status_code=200,text=markup,url='https://example.com/jobs/123'))
    assert verifier._check('https://example.com/jobs/123')['state'] == 'verified'


def test_unknown_listing_is_quarantined_instead_of_added(monkeypatch):
    monkeypatch.setattr(ats_scrapers, 'load_reported_closed_jobs', lambda:{})
    monkeypatch.setattr(ats_scrapers, 'verify_listing', lambda *a:{'state':'unknown','reason':'HTTP 403'})
    review = Mock();monkeypatch.setattr(ats_scrapers, 'record_review', review)
    jobs=[]
    assert not ats_scrapers.add_discovered_job(jobs, 'one', 'Acme','Software Engineering Intern','UK','https://example.com/jobs/123','Trackr')
    assert not jobs and review.call_args.args[-1] == 'unknown'


def test_distinct_requisitions_years_locations_and_query_ids_stay_distinct():
    base={'company':'Acme','title':'Software Engineering Intern 2027','location':'London','link':'https://jobs.lever.co/acme/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'}
    assert not same_listing(base, {**base,'link':'https://jobs.lever.co/acme/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'})
    assert not same_listing(base, {**base,'title':'Software Engineering Intern 2026','link':'https://higherin.com/jobs/123/acme/intern'})
    assert not same_listing(base, {**base,'location':'Bristol','link':'https://higherin.com/jobs/123/acme/intern'})
    assert normalize_url('https://example.com/apply?jobId=1&utm_source=email') != normalize_url('https://example.com/apply?jobId=2')
    assert same_listing({'company':'Databricks','title':'Software Intern','link':'https://www.databricks.com/careers/intern?gh_jid=8847738002'},
                        {'company':'Databricks','title':'Software Intern','link':'https://job-boards.greenhouse.io/databricks/jobs/8847738002'})


def test_higherin_public_payload_and_grb_card_structure():
    payload={'data':[{'jobTitle':'Software Intern'}],'meta':{'pagination':{'lastPage':3}}}
    assert uk_boards.higherin_payload('<script>window.__RMP_SEARCH_RESULTS_INITIAL_STATE__ = '+json.dumps(payload)+';</script>') == payload
    cards='<div class="e-loop-item grb_job_location-london"><a href="/internships/acme-software-123"><span class="elementor-heading-title">Acme</span><h2>Software Engineering Intern</h2></a></div>'
    rows=uk_boards.parse_grb(cards,'https://www.grb.uk.com/internships/')
    assert rows[0]['company']=='Acme' and rows[0]['location']=='London'


def test_failed_notification_survives_restart_and_retries_once(tmp_path, monkeypatch):
    monkeypatch.setattr(notifications, 'OUTBOX_FILE', str(tmp_path/'outbox.json'))
    monkeypatch.setattr(notifications, 'update_scraper_status', Mock())
    publish=Mock(return_value=False);monkeypatch.setattr(notifications,'_publish_notification',publish)
    assert not notifications.send_notification('Jobs','An actual job',event_id='job1')
    state=json.loads((tmp_path/'outbox.json').read_text());assert 'job1' in state['pending']
    state['pending']['job1']['next_retry']=0;(tmp_path/'outbox.json').write_text(json.dumps(state))
    publish.return_value=True;notifications.flush_notification_outbox()
    assert notifications.send_notification('Jobs','An actual job',event_id='job1')
    assert publish.call_count == 2
    assert not json.loads((tmp_path/'outbox.json').read_text())['pending']


def test_scheduler_catches_up_missed_minute_without_repeating(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler,'SCHEDULE_FILE',str(tmp_path/'schedule.json'))
    monkeypatch.setattr(scheduler,'flush_notification_outbox',Mock())
    monkeypatch.setattr(scheduler,'SCRAPER_STATUS',{'last_run':'2026-10-04 07:00:00'})
    monkeypatch.setattr(scheduler,'DISCOVERY_SCAN_READY',Mock(is_set=Mock(return_value=True)))
    briefing=Mock();monkeypatch.setattr(scheduler,'trigger_daily_briefing',briefing)
    now=datetime(2026,10,5,8,37,tzinfo=timezone.utc)
    scheduler.scheduled_tick(now);scheduler.scheduled_tick(now)
    assert briefing.call_count==1 and briefing.call_args.args[0]=='morning'


def test_scheduler_waits_for_first_completed_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler,'SCHEDULE_FILE',str(tmp_path/'schedule.json'))
    monkeypatch.setattr(scheduler,'flush_notification_outbox',Mock())
    monkeypatch.setattr(scheduler,'DISCOVERY_SCAN_READY',Mock(is_set=Mock(return_value=False)))
    briefing=Mock();monkeypatch.setattr(scheduler,'trigger_daily_briefing',briefing)
    scheduler.scheduled_tick(datetime(2026,10,5,8,37,tzinfo=timezone.utc))
    briefing.assert_not_called()
    assert not json.loads((tmp_path/'schedule.json').read_text())
