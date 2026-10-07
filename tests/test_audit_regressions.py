"""Regression cases from the dashboard/inbox/source audit."""
import email
import threading
from unittest.mock import Mock, patch

import pytest
import requests

import email_listener as listener
import sheets
from core.jobs import JobRepository
from core import jobs
from core.health import engine_health
from web.handlers import CleanHandler, ThreadedHTTPServer
from web.components import ApplicationCardViewModel, JobCardViewModel


@pytest.mark.parametrize('text, expected', [
    ('We invite you to your video interview using HireVue.', 'Interview'),
    ('Your application has not been unsuccessful; we invite you to your interview.', 'Interview'),
    ('Your interview is confirmed. Previous email: please complete your online assessment.', 'Interview'),
    ('Practice your online assessment with HackerRank.', None),
    ('Regarding your application: no formal offer has been made.', 'Application Update'),
    ('Your interview has been cancelled. Regarding your application we will contact you.', 'Application Update'),
    ('Your interview is confirmed. You will meet other candidates.', 'Interview'),
])
def test_email_outcome_and_current_message(text, expected):
    assert listener.classify_email_stage(text) == expected


def test_html_quoted_assessment_does_not_override_current_interview():
    msg = email.message_from_string('Content-Type: text/html\n\n<p>Your interview is confirmed.</p><div class="gmail_quote">Your application was rejected.</div>')
    assert listener.classify_email_stage(listener._extract_plain_text(msg)) == 'Interview'


def test_sheet_explicit_columns_and_numeric_order():
    csv = 'Company,Role,Stage 10,Notes,Stage 2,Stage 1\nAcme,Intern,Offer,Rejected,Interview,Applied\n'
    application = sheets.get_detailed_applications(csv)[0]
    assert application['stages'] == ['Applied', 'Interview', 'Offer']
    assert sheets.parse_sheet_stats(csv)['offers'] == 1


def test_cleared_and_removed_sheet_rows_clear_stages_keep_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'JOBS_FILE', str(tmp_path / 'jobs.json'))
    repo = JobRepository()
    first = repo.sync([], [{'company':'Acme', 'role':'Intern', 'stages':['Applied', 'Interview']}], reconcile=True)[0]
    repo.record_email(first, 'mail1', 'Interview', 'Invitation', synced=True)
    cleared = repo.sync([], [{'company':'Acme', 'role':'Intern', 'stages':[]}], reconcile=True)[0]
    assert cleared.stages == [] and cleared.sheet_present
    removed = repo.sync([], [], reconcile=True)[0]
    assert removed.stages == [] and not removed.sheet_present
    assert len(removed.email_events) == 1


def test_failed_sheet_read_uses_durable_valid_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(sheets, '_SHEET_SNAPSHOT', str(tmp_path / 'snapshot.json'))
    monkeypatch.setattr(sheets, '_SHEET_CSV_CACHE', {'timestamp':0,'content':''})
    monkeypatch.setattr(sheets, 'GOOGLE_SHEET_CSV_URL', 'https://example.com/sheet.csv')
    csv = 'Company,Role,Stage 1\nAcme,Intern,Applied\n'
    with patch.object(sheets.requests, 'get', return_value=Mock(status_code=200,text=csv)):
        assert sheets.fetch_google_sheet_csv(True) == csv
    sheets._SHEET_CSV_CACHE.update(timestamp=0,content='')
    with patch.object(sheets.requests, 'get', return_value=Mock(status_code=200,text='<html>Please log in</html>')):
        assert sheets.fetch_google_sheet_csv(True) == csv


def test_folder_poll_does_not_advance_checkpoint_on_missing_message(tmp_path, monkeypatch):
    monkeypatch.setattr(listener, 'MAIL_CHECKPOINTS_FILE', str(tmp_path/'checkpoint.json'))
    monkeypatch.setattr(listener, 'SEEN_EMAILS_FILE', str(tmp_path/'seen.json'))
    mail = Mock()
    mail.select.return_value = ('OK', [])
    mail.response.return_value = ('UIDVALIDITY', [b'42'])
    mail.uid.side_effect = [('OK',[b'12']), ('OK',[b')'])]
    with patch.object(listener.imaplib, 'IMAP4_SSL', return_value=mail):
        listener._check_one_imap_inbox({'label':'Test','key':'test','host':'example.com','port':993,'user':'u','password':'p','folder':'INBOX'},set())
    mail.select.assert_called_once_with('INBOX', readonly=True)
    assert mail.uid.call_args_list[1].args[-1] == '(BODY.PEEK[])'
    assert not (tmp_path/'checkpoint.json').exists()


def test_health_reports_partial_failure():
    health = engine_health({'source_status':{'Greenhouse':'🟠 22/33 employer feeds online'}, 'sheet_health':{'ok':False}})
    assert health['status'] == 'degraded'
    assert health['employer_feeds_online'] == 22
    assert 'Sheet needs attention' in health['issues']


def test_terminal_application_has_no_interview_shortcut_and_location_cleaning():
    markup = ApplicationCardViewModel({'company':'Acme','role':'Intern','latest_stage':'Rejected','status_type':'rejected'}).render()
    assert '+ Interview' not in markup and 'Update stage' in markup
    assert JobCardViewModel({'location':'London, GB, GB, UNAVAILABLE'}).location == 'London, UK'


def test_mutations_reject_get_cross_origin_and_invalid_stage():
    server = ThreadedHTTPServer(('127.0.0.1',0), CleanHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with patch('web.handlers.update_google_sheet_via_webhook') as write:
            assert requests.get(base+'/api/mark-applied?company=Acme&title=Intern').status_code == 405
            assert requests.post(base+'/api/mark-applied',data={'company':'Acme','title':'Intern'},headers={'Origin':'https://evil.example'}).status_code == 403
            assert requests.post(base+'/api/mark-applied',data={'company':'Acme','title':'Intern','stage':'random'}).status_code == 400
            write.assert_not_called()
    finally:
        server.shutdown(); server.server_close()


def test_applied_role_matching_never_hides_a_different_intake_year():
    from core.normalization import normalize_role, fuzzy_roles_match
    assert normalize_role('Software Intern 2027') != normalize_role('Software Intern 2026')
    assert not fuzzy_roles_match('Software Engineering Intern 2027', 'Software Engineering Intern 2026', threshold=.5)


def test_official_page_extracts_embedded_board_and_specific_jobs():
    from scrapers_engine.official_careers import career_links
    markup = '<meta property="og:site_name" content="Acme"><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script><a href="/jobs/acme/software-intern">Software Intern</a>'
    employer, boards, roles, _ = career_links(markup,'https://acme.example/careers/')
    assert employer == 'Acme'
    assert 'https://boards.greenhouse.io/embed/job_board/js?for=acme' in boards
    assert roles == [('Software Intern','https://acme.example/jobs/acme/software-intern')]


def test_verified_employer_corrects_aggregator_identity(tmp_path, monkeypatch):
    from scrapers_engine import audit
    listing = {'id':'wrong','company':'Wrong Company','title':'Software Engineering Internship 2027','link':'https://acme.example/jobs/intern','location':'London, UK','source':'Trackr'}
    monkeypatch.setattr(audit, 'load_discovered_jobs', lambda:[listing])
    monkeypatch.setattr(audit, 'save_discovered_jobs', lambda rows:None)
    monkeypatch.setattr(audit, 'verify_listing', lambda *a:{'state':'verified','reason':'JobPosting','checked_at':'2026-10-08T00:00:00+00:00','company':'Acme','title':'Software Engineering Internship 2027','location':'London, UK','description':'Software engineering summer internship in London for undergraduates, Python and Java programming.'})
    audit.purge_expired_jobs()
    assert listing['company'] == 'Acme'
    assert listing['metadata']['source_company'] == 'Wrong Company'


@pytest.mark.parametrize('current, old', [('Rejected','Interview'), ('Interview 2','Online Assessment'), ('Assessment 1','Applied')])
def test_email_catchup_cannot_reopen_or_regress_application(current, old):
    from core.jobs import Job
    job = Job('one','Acme','Software Intern',stages=['Applied',current])
    with patch.object(listener, 'JobRepository') as repository, patch.object(listener, '_get_sheet_apps_with_retry',return_value=[]), patch.object(listener,'fetch_google_sheet_csv',return_value=''), patch.object(listener,'match_email_to_job',return_value=(job,[job],'role title')), patch.object(listener,'update_google_sheet_via_webhook') as write, patch.object(listener,'add_pending_email_update') as pending, patch.object(listener,'send_notification'), patch('scrapers_engine.audit.load_discovered_jobs',return_value=[]):
        repository.return_value.email_synced.return_value=False
        assert listener.handle_incoming_email_update('Acme',old,'Old application email',event_id='old1')
        write.assert_not_called()
        if old not in job.stages:
            assert 'conflicts' in pending.call_args.args[0]['reason']
        else:
            pending.assert_not_called()


def test_assigning_general_email_does_not_add_a_sheet_stage():
    from core.jobs import Job
    handler = object.__new__(CleanHandler)
    handler._json = Mock()
    job = Job('one','Acme','Intern',stages=['Applied'])
    with patch('web.handlers.load_pending_email_updates',return_value=[{'id':'pending1','event_id':'mail1','stage':'Application Update'}]), patch('web.handlers.JobRepository') as repository, patch('web.handlers.load_discovered_jobs',return_value=[]), patch('web.handlers.get_detailed_applications',return_value=[]), patch('web.handlers.fetch_google_sheet_csv',return_value=''), patch('web.handlers.update_google_sheet_via_webhook') as write, patch('core.storage.remove_pending_email_update') as remove:
        repository.return_value.sync.return_value=[job]
        handler._mutate('/api/resolve-pending-update',{'id':['pending1'],'company':['Acme'],'role':['Intern'],'stage':['Application Update']})
        write.assert_not_called()
        remove.assert_called_once_with('pending1')
        repository.return_value.record_email.assert_called_once()


def test_pending_selector_labels_employer_and_role_without_duplicate_choices():
    from web.pending_view import render_pending_updates
    markup = render_pending_updates([{'id':'p1','options':[{'company':'Acme','role':'Intern'},{'company':'Other','role':'Intern'},{'company':'Acme','role':'Intern'}]}])
    assert 'Acme — Intern' in markup and 'Other — Intern' in markup
    assert markup.count('Acme — Intern') == 1
    assert 'Confirm update' in markup


def test_assigning_newly_logged_role_uses_its_job_object():
    from core.jobs import Job
    handler = object.__new__(CleanHandler); handler._json = Mock()
    old = Job('old','Existing','Intern',stages=['Applied'])
    new = Job('new','Acme','Intern',stages=['Applied'])
    with patch('web.handlers.load_pending_email_updates',return_value=[{'id':'p1','event_id':'mail1'}]), patch('web.handlers.JobRepository') as repository, patch('web.handlers.load_discovered_jobs',return_value=[]), patch('web.handlers.get_detailed_applications',return_value=[]), patch('web.handlers.fetch_google_sheet_csv',return_value=''), patch('web.handlers.update_google_sheet_via_webhook',return_value=True), patch('core.storage.remove_pending_email_update'):
        repository.return_value.sync.side_effect=[[old],[old,new]]
        handler._mutate('/api/resolve-pending-update',{'id':['p1'],'company':['Acme'],'role':['Intern'],'stage':['Applied']})
        assert repository.return_value.record_email.call_args.args[0].id == 'new'
