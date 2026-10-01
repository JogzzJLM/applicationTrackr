from pathlib import Path
import pytest
from autoapply import profile, learning


def test_profile_answers_merge_without_erasing_existing_values(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, 'PROFILE_FILE', tmp_path / 'profile.json')
    profile.update_profile({'personal.first_name': 'Jane'})
    profile.update_profile({'personal.city': 'London'})
    saved = profile.ensure_profile()
    assert saved['personal']['first_name'] == 'Jane'
    assert saved['personal']['city'] == 'London'
    with pytest.raises(ValueError):
        profile.update_profile({'documents.resume_path': '/etc/passwd'})


def test_document_is_saved_in_volume_under_controlled_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, 'AUTOAPPLY_DIR', tmp_path)
    monkeypatch.setattr(profile, 'PROFILE_FILE', tmp_path / 'profile.json')
    stored = profile.save_document('resume', '../../private.pdf', b'%PDF-1.7\nexample')
    assert Path(stored) == tmp_path / 'resume.pdf'
    assert profile.ensure_profile()['documents']['resume_path'] == stored
    assert Path(stored).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        profile.save_document('resume', 'resume.pdf', b'<html>not a PDF</html>')
    assert Path(stored).read_bytes().startswith(b'%PDF-')


def test_job_specific_answer_cannot_be_reused_at_another_company(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, 'LEARNING_FILE', tmp_path / 'learning.json')
    learning.learn_answer('Why do you want to join us?', 'Figma answer', domain='https://boards.greenhouse.io/figma/jobs/123')
    answer, _, _ = learning.predict_answer('Why do you want to join us?', domain='https://boards.greenhouse.io/other/jobs/456')
    assert answer is None
    assert learning.predict_answer('Why do you want to join us?', domain='https://boards.greenhouse.io/figma/jobs/123')[0] == 'Figma answer'


def test_preferred_first_name_is_distinct_from_legal_first_name():
    assert learning.predict_mapping('Preferred first name')[0] == 'personal.preferred_name'


def test_document_upload_and_profile_merge_over_http(tmp_path, monkeypatch):
    import threading
    import requests
    from web.handlers import ThreadedHTTPServer, CleanHandler
    monkeypatch.setattr(profile, 'AUTOAPPLY_DIR', tmp_path)
    monkeypatch.setattr(profile, 'PROFILE_FILE', tmp_path / 'profile.json')
    server = ThreadedHTTPServer(('127.0.0.1', 0), CleanHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        response = requests.post(url + '/api/autoapply/document', data={'kind': 'resume'}, files={'file': ('CV.pdf', b'%PDF-1.7\nexample', 'application/pdf')})
        assert response.status_code == 200
        assert (tmp_path / 'resume.pdf').exists()
        response = requests.post(url + '/api/autoapply/profile', data={'values': '{"personal.first_name":"Jane"}'})
        assert response.status_code == 200
        assert profile.ensure_profile()['personal']['first_name'] == 'Jane'
        assert profile.ensure_profile()['documents']['resume_path'] == str(tmp_path / 'resume.pdf')
        assert 'Access-Control-Allow-Origin' not in requests.get(url + '/api/autoapply/profile').headers
        response = requests.post(url + '/api/autoapply/profile', data={'values': '{}'}, headers={'Origin': 'https://other.example'})
        assert response.status_code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_previous_employer_rule_uses_confirmed_history():
    from autoapply.browser_agent import previous_employer_answer
    saved = {'employment': {'previous_employers': 'Tesco'}}
    assert previous_employer_answer('Have you ever worked for Figma before?', 'Figma', saved) == 'No'
    assert previous_employer_answer('Have you ever worked for Tesco before?', 'Tesco', saved) == 'Yes'
    assert previous_employer_answer('Have you worked for a proprietary trading firm?', 'Figma', saved) is None
    assert previous_employer_answer('Have you ever worked for Figma before?', 'Figma', {}) is None


def test_guided_questions_include_listing_and_saved_answers(monkeypatch):
    from autoapply import service
    monkeypatch.setattr(service, 'get_run', lambda rid: {'status': 'form_inspected', 'url': 'https://example.com/figma', 'job_id': '1', 'page_context': 'Full role description', 'inspected_fields': [{'label': 'first name', 'type': 'text'}, {'label': 'have you ever worked for figma before?', 'type': 'text'}]})
    monkeypatch.setattr(service, '_find_job', lambda jid: {'company': 'Figma', 'title': 'Intern'})
    monkeypatch.setattr(service, 'ensure_profile', lambda: {'personal': {'first_name': 'Jane'}, 'employment': {'previous_employers': 'Tesco'}})
    plan = service.guided_questions('run')
    assert plan['context'] == 'Full role description'
    assert plan['questions'][0]['answer'] == 'Jane'
    assert plan['questions'][1]['answer'] == 'No'


def test_searchable_dropdown_selects_option_and_rejects_ambiguous_match():
    from autoapply.browser_agent import _set_value
    class Option:
        def __init__(self, text): self.text, self.clicked = text, False
        def is_displayed(self): return True
        def click(self): self.clicked = True
    class Combo:
        tag_name = 'input'
        def __init__(self, options): self.options = options
        def get_attribute(self, key): return 'combobox' if key == 'role' else None
        def clear(self): pass
        def send_keys(self, value): pass
        def find_elements(self, *args): return self.options
    yes, no = Option('Yes'), Option('No')
    assert _set_value(Combo([yes, no]), 'text', 'No')
    assert no.clicked and not yes.clicked
    first, second = Option('London UK'), Option('London Canada')
    assert not _set_value(Combo([first, second]), 'text', 'London')
    assert not first.clicked and not second.clicked
