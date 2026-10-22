"""Manual dismissal must stay reversible, quiet and separate from applications."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import threading
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup

from core import storage
from web.handlers import CleanHandler, ThreadedHTTPServer
from scrapers_engine.verifier import listing_deadline_date


def listing(id, days=3):
    return {'id':id, 'company':'Lloyds Banking Group', 'title':'Software Engineering Placement '+id,
            'link':'https://example.com/jobs/'+id, 'source':'Manual', 'location':'London, UK',
            'metadata':{'closing_date':(datetime.now(ZoneInfo('Europe/London')).date()+timedelta(days=days)).isoformat()},
            'verification':{'state':'verified'}, 'match_score':80}


def test_hide_survives_id_and_tracking_changes_and_restore_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'HIDDEN_JOBS_FILE', str(tmp_path/'hidden.json'))
    job=listing('a'); other=listing('b')
    storage.set_listing_visibility([job], True)
    changed={**job, 'id':'new-source-id', 'link':job['link']+'?utm_source=other'}
    assert storage.is_job_hidden(changed)
    assert not storage.is_job_hidden(other)
    storage.set_listing_visibility([changed], False)
    assert not storage.is_job_hidden(job)
    assert not storage.is_job_hidden(changed)
    storage.hide_job('legacy')
    storage.set_listing_visibility([{'id':'legacy'}], False)
    assert 'legacy' not in storage.load_hidden_jobs()


def test_hide_restore_http_persists_and_rejects_unknown_batch_and_cross_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'HIDDEN_JOBS_FILE',str(tmp_path/'hidden.json'))
    jobs=[listing('a'),listing('b')]
    server=ThreadedHTTPServer(('127.0.0.1',0), CleanHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with patch('web.handlers.load_discovered_jobs',return_value=jobs), patch('web.handlers.update_google_sheet_via_webhook') as sheet:
            assert requests.get(base+'/api/unhide-job?id=a').status_code==405
            assert requests.post(base+'/api/hide-job',data={'id':'a'},headers={'Origin':'https://evil.example'}).status_code==403
            assert requests.post(base+'/api/hide-job',data=[('id','a'),('id','missing')]).status_code==404
            assert not storage.load_hidden_jobs()
            assert requests.post(base+'/api/hide-job',data=[('id','a'),('id','b')]).json()['status']=='ok'
            assert all(storage.is_job_hidden(job) for job in jobs)
            assert requests.post(base+'/api/unhide-job',data={'id':'a'}).json()['status']=='ok'
            assert not storage.is_job_hidden(jobs[0]) and storage.is_job_hidden(jobs[1])
            sheet.assert_not_called()
    finally:
        server.shutdown();server.server_close()


def test_deadline_falls_back_to_metadata_and_ignores_rolling_marker():
    job=listing('metadata')
    assert listing_deadline_date({**job,'deadline':'Rolling'})==listing_deadline_date(job)
    assert listing_deadline_date({**job,'deadline':'2036-01-01'})==listing_deadline_date(job)


def test_closing_panel_orders_deadlines_excludes_applied_and_hidden_and_always_exists(monkeypatch):
    from web import views
    jobs=[listing('later',5),listing('soon',1),listing('hidden',0),listing('applied',2)]
    jobs[-1]['title']='Software Engineering & Placement applied'
    fixture='Company,Role,Stage 1\nLloyds Banking Group,Software Engineering &amp; Placement applied,Applied\n'
    monkeypatch.setattr(views,'fetch_google_sheet_csv',lambda:fixture)
    monkeypatch.setattr(views,'load_discovered_jobs',lambda:jobs)
    monkeypatch.setattr(views,'load_hidden_jobs',lambda:{'hidden'})
    monkeypatch.setattr('core.jobs.JobRepository.sync',lambda *args,**kwargs:[])
    page=BeautifulSoup(views.render_unified_dashboard_html(),'html.parser')
    assert [item['data-closing-id'] for item in page.select('#closing-soon-section [data-closing-id]')]==['soon','later']
    assert page.select_one('.job-card[data-id="hidden"]')['data-status']=='hidden'
    assert page.select_one('.applications-scroll[tabindex="0"]')
    monkeypatch.setattr(views,'load_discovered_jobs',lambda:[listing('future',20)])
    page=BeautifulSoup(views.render_unified_dashboard_html(),'html.parser')
    assert 'None close in the next 7 days' in page.select_one('#closing-soon-section').get_text()
    assert page.select_one('[data-closing-id="future"]')
    monkeypatch.setattr(views,'load_discovered_jobs',lambda:[])
    assert 'No known upcoming deadlines' in BeautifulSoup(views.render_unified_dashboard_html(),'html.parser').select_one('#closing-soon-section').get_text()


def test_hidden_listings_do_not_reappear_in_digests_or_new_listing_alerts(tmp_path, monkeypatch):
    import scheduler
    import notifications
    jobs=[listing('hidden'),listing('visible')]
    monkeypatch.setattr(storage,'HIDDEN_JOBS_FILE',str(tmp_path/'hidden.json'))
    storage.set_listing_visibility(jobs[:1],True)
    monkeypatch.setattr('scrapers_engine.audit.load_discovered_jobs',lambda:jobs)
    monkeypatch.setattr('sheets.get_applied_jobs_set',lambda:(set(),set()))
    assert [job['id'] for job in scheduler.actionable_jobs()]==['visible']
    monkeypatch.setattr(notifications,'NOTIFIED_FILE',str(tmp_path/'notified.json'))
    monkeypatch.setattr(notifications,'update_scraper_status',lambda *args:None)
    with patch.object(notifications,'send_notification') as notify:
        assert notifications._notify_discoveries(jobs)==1
        assert '/visible' in notify.call_args.kwargs['link']


def test_encoded_saved_role_is_the_same_application():
    from core.normalization import normalize_role, fuzzy_roles_match
    plain='Software Engineering (Graduate & Internship Opportunites)'
    encoded='Software Engineering (Graduate &amp; Internship Opportunites)'
    assert normalize_role(plain)==normalize_role(encoded)
    assert fuzzy_roles_match(plain,encoded,threshold=.90)
