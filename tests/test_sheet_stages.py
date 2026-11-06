"""Real regression contracts: repeated mail, concurrent writes, explicit rounds and row identity."""
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
import sheets
from core.jobs import JobRepository, role_identity
from core.stages import canonical_stage, compact_stage_history, stage_recorded, email_stage_conflicts, valid_stage
from core.mail_outcomes import classify_email_stage


@pytest.mark.parametrize('stage,history,duplicate', [
    ('Applied',['Applied'],True), ('applied',['Applied'],True),
    ('Rejected',['Applied','Online Assessment','Rejected'],True),
    ('Online Assessment',['Applied','Assessment 1'],True),
    ('Assessment 1',['Applied','Online Assessment'],True),
    ('Online Assessment 2',['Applied','Online Assessment'],False),
    ('Online Assessment',['Applied','Online Assessment 2'],True),
    ('Interview',['Applied','Digital Interview'],True),
    ('Interview 2',['Applied','Interview 1'],False),
    ('Group Assessment',['Applied','Online Assessment'],False),
    ('Assessment Centre',['Applied','Interview'],False),
])
def test_reminders_and_explicit_rounds(stage,history,duplicate):
    assert stage_recorded(stage,history) is duplicate


@pytest.mark.parametrize('stage,history,conflict',[
    ('Online Assessment 1',['Applied','Online Assessment 2'],True),
    ('Applied',['Applied','Rejected'],False), # Duplicate evidence is recorded, not appended/reopened.
    ('Interview',['Applied','Rejected'],True),
    ('Group Assessment',['Applied','Online Assessment'],False),
])
def test_automatic_updates_do_not_undo_progress(stage,history,conflict):
    assert email_stage_conflicts(stage,history) is conflict


def test_cleanup_only_collapses_adjacent_equivalent_stages():
    assert compact_stage_history(['Applied','Applied','OA','Assessment 1','Assessment 2','Rejected','Rejected']) == ['Applied','Online Assessment','Online Assessment 2','Rejected']
    assert compact_stage_history(['Rejected','Reopened','Rejected']) == ['Rejected','Reopened','Rejected']


@pytest.mark.parametrize('text,stage',[
    ('Your second interview is scheduled for Friday.','Interview 2'),
    ('We invite you to complete online assessment 2 for your application.','Online Assessment 2'),
    ('Please complete your second online assessment.','Online Assessment 2'),
    ('We invite you to a group assessment for your application.','Group Assessment'),
    ('We invite you to your assessment centre.','Assessment Centre'),
    ('Your interview is scheduled on 20 November.','Interview'),
    ('Your interview 20 November is confirmed.','Interview'),
    ('Please complete your online assessment 2 days before your interview.','Online Assessment'),
    ('Prepare for a group assessment with our practice tips.',None),
])
def test_employer_event_types_and_rounds(text,stage):
    assert classify_email_stage(text) == stage


@pytest.mark.parametrize('stage,custom,allowed',[
    ('Group Assessment',False,True), ('Online Assessment 2',False,True),
    ('Assessment Centre 2',False,True), ('Interview 0',False,False),
    ('Case Study Presentation',True,True), ('random',False,False),
    ('=IMPORTXML("https://example.com")',True,False), ('<script>',True,False),
    ('Applied\nRejected',True,False), ('x'*81,True,False),
])
def test_manual_stage_boundary(stage,custom,allowed):
    assert valid_stage(stage,custom=custom) is allowed


def test_every_caller_uses_live_atomic_deduplication_without_inventing_rounds():
    apps=[{'company':'Acme','role':'Intern 2027','stages':['Applied','Online Assessment']}]
    response=Mock(status_code=200,headers={},json=lambda:{'status':'duplicate'})
    with patch.object(sheets,'GOOGLE_SHEET_WEBHOOK_URL','https://example.com/webhook'), patch.object(sheets,'get_detailed_applications',return_value=apps), patch.object(sheets,'fetch_google_sheet_csv'), patch.object(sheets.requests,'post',return_value=response) as post:
        assert sheets.update_google_sheet_via_webhook('Acme','Applied',role='Intern 2027',resolve_sequential=False)
        assert sheets.update_google_sheet_via_webhook('Acme','Online Assessment',role='Intern 2027')
        assert sheets.resolve_smart_stage('Acme','Online Assessment 2','Intern 2027') == 'Online Assessment 2'
        assert [call.kwargs['json']['stage'] for call in post.call_args_list]==['Applied','Online Assessment']


def test_explicit_round_write_retains_round_and_opaque_event_identity():
    response=Mock(status_code=200,headers={},json=lambda:{'status':'success'})
    with patch.object(sheets,'GOOGLE_SHEET_WEBHOOK_URL','https://example.com/webhook'), patch.object(sheets,'get_detailed_applications',return_value=[]), patch.object(sheets,'fetch_google_sheet_csv'), patch.object(sheets.requests,'post',return_value=response) as post:
        assert sheets.update_google_sheet_via_webhook('Acme','Online Assessment 2',role='Intern 2027',event_id='private-mailbox:<message>',automatic=True)
        payload=post.call_args.kwargs['json']
        assert payload['stage']=='Online Assessment 2' and payload['automatic'] is True
        assert len(payload['event_id'])==64 and 'private' not in payload['event_id']


@pytest.mark.parametrize('ack', [{}, {'success':False}, {'status':'error'}, {'error':'full'}, None])
def test_unknown_or_failed_acknowledgements_never_claim_success(ack):
    response=Mock(status_code=200,headers={},json=lambda:ack)
    with patch.object(sheets,'GOOGLE_SHEET_WEBHOOK_URL','https://example.com/webhook'), patch.object(sheets,'get_detailed_applications',return_value=[]), patch.object(sheets.requests,'post',return_value=response):
        assert not sheets.update_google_sheet_via_webhook('Acme','Applied',role='Intern')


def test_writer_keeps_different_roles_and_intake_years_separate():
    apps=[{'company':'Acme','role':'Intern 2027','stages':['Applied']}]
    with patch.object(sheets,'get_detailed_applications',return_value=apps):
        assert sheets.resolve_smart_stage('Acme','Applied','Intern 2026')=='Applied'
        assert sheets.resolve_smart_stage('Acme','Applied','Analyst 2027')=='Applied'
    assert role_identity('Graduate &amp; Internship 2027')==role_identity('Graduate & Internship 2027')


def test_encoded_sheet_role_merges_with_listing_and_keeps_email_evidence(tmp_path,monkeypatch):
    from core import jobs
    monkeypatch.setattr(jobs,'JOBS_FILE',str(tmp_path/'jobs.json'))
    repo=JobRepository()
    old=repo.sync([{'id':'source','company':'Acme','title':'Graduate & Internship 2027','link':'https://example.com/jobs/1'}],[])[0]
    repo.record_email(old,'message','Applied','Confirmation',synced=True)
    merged=repo.sync([], [{'company':'Acme','role':'Graduate &amp; Internship 2027','stages':['Applied']}],reconcile=True)
    assert len(merged)==1 and merged[0].sheet_present and merged[0].email_events[0]['sheet_synced']


def test_sheet_script_and_python_share_company_aliases_and_round_vocabulary():
    import re
    from core.normalization import COMPANY_ALIASES
    from core.stages import _ALIASES, ROUND_STAGES
    source=Path('integrations/google-sheets.gs').read_text()
    for name,value in [('TRACKR_COMPANY_ALIASES',COMPANY_ALIASES),('TRACKR_STAGE_ALIASES',_ALIASES),('TRACKR_ROUND_STAGES',sorted(ROUND_STAGES))]:
        assert json.loads(re.search(r'var '+name+r' = (.*);',source)[1])==value


def test_manual_update_accepts_specific_events_and_custom_labels_without_rewriting_them():
    from web.handlers import CleanHandler
    handler=object.__new__(CleanHandler);handler._json=Mock()
    with patch('web.handlers.update_google_sheet_via_webhook',return_value=True) as write, patch('web.handlers.generate_sankey_from_google_sheets'):
        for stage,custom in [('Online Assessment 2',False),('Group Assessment',False),('Case Study Presentation',True)]:
            handler._mutate('/api/mark-applied',{'company':['Acme'],'title':['Intern 2027'],'stage':[stage],'custom_stage':[str(custom).lower()]})
            assert handler._json.call_args.args[0]['stage']==stage
            assert write.call_args.args[:2]==('Acme',stage)
            assert write.call_args.kwargs['resolve_sequential'] is False
        count=write.call_count
        handler._mutate('/api/mark-applied',{'company':['Acme'],'title':['Intern'],'stage':['=IMPORTXML("url")'],'custom_stage':['true']})
        assert handler._json.call_args.args[1]==400 and write.call_count==count


def test_email_with_stale_pre_read_passes_atomic_validation_to_the_writer():
    import email_listener as listener
    from core.jobs import Job
    job=Job('one','Acme','Software Intern',stages=['Applied'])
    with patch.object(listener,'JobRepository') as repository, patch.object(listener,'_get_sheet_apps_with_retry',return_value=[]), patch.object(listener,'fetch_google_sheet_csv',return_value=''), patch.object(listener,'match_email_to_job',return_value=(job,[job],'role title')), patch.object(listener,'update_google_sheet_via_webhook',return_value=False) as write, patch.object(listener,'add_pending_email_update') as pending, patch.object(listener,'send_notification'), patch('scrapers_engine.audit.load_discovered_jobs',return_value=[]):
        repository.return_value.email_synced.return_value=False
        assert listener.handle_incoming_email_update('Acme','Online Assessment 2','Assessment invitation',event_id='message1')
        assert write.call_args.kwargs['automatic'] is True
        assert write.call_args.kwargs['event_id']=='message1'
        assert pending.call_args.args[0]['stage']=='Online Assessment 2'


def test_real_sheet_employer_repairs_legacy_role_as_company_without_losing_events(tmp_path,monkeypatch):
    from core import jobs
    monkeypatch.setattr(jobs,'JOBS_FILE',str(tmp_path/'jobs.json'))
    repo=JobRepository(); title='Global Technology Industrial Placement 2027 Analyst'
    old=repo.sync([{'id':'bad-source','company':title,'title':title}],[])[0]
    repo.record_email(old,'mail','Rejected','Bank application update',synced=True)
    repaired=repo.sync([], [{'company':'Example Bank','role':title,'stages':['Applied','Rejected']}],reconcile=True)
    assert len(repaired)==1 and repaired[0].company=='Example Bank'
    assert repaired[0].email_events[0]['id']=='mail' and repaired[0].sheet_present
    refreshed=repo.sync([{'id':'bad-source','company':title,'title':title}], [{'company':'Example Bank','role':title,'stages':['Applied','Rejected']}],reconcile=True)
    assert len(refreshed)==1 and refreshed[0].company=='Example Bank'


def test_explicit_manual_reopening_starts_a_new_progression_without_erasing_old_history():
    history=['Applied','Rejected','Reopened']
    assert not stage_recorded('Rejected',history)
    assert stage_recorded('Reopened',history)
    assert not stage_recorded('Reopened',history+['Rejected'])


def test_unknown_custom_event_requires_review_instead_of_guessing_progression():
    assert email_stage_conflicts('Online Assessment',['Applied','Case Study Presentation'])
