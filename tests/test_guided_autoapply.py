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
        def click(self): pass
        def clear(self): pass
        def send_keys(self, value): pass
        def find_elements(self, *args): return self.options
    yes, no = Option('Yes'), Option('No')
    assert _set_value(Combo([yes, no]), 'text', 'No')
    assert no.clicked and not yes.clicked
    first, second = Option('London UK'), Option('London Canada')
    assert not _set_value(Combo([first, second]), 'text', 'London')
    assert not first.clicked and not second.clicked


def test_supplied_graduation_date_can_choose_month_and_year():
    from autoapply.browser_agent import _matching_option
    class Option:
        def __init__(self, text): self.text = text
    previous, expected = Option('June 2027'), Option('June 2028')
    assert _matching_option([previous, expected], '23/06/2028') is expected
    assert _matching_option([previous], '23/06/2028') is None


def test_menu_text_is_read_in_one_browser_request():
    from autoapply.browser_agent import _visible_options
    class Option:
        def __init__(self): self.clicked = False
        def click(self): self.clicked = True
    option = Option()
    class Driver:
        calls = 0
        def execute_script(self, script):
            self.calls += 1
            return [{'element': option, 'text': 'Backend/Infrastructure'}]
    class Field:
        parent = Driver()
        def find_elements(self, *args): raise AssertionError('Should not inspect options one by one')
    element = Field()
    result = _visible_options(element)
    assert result[0].text == 'Backend/Infrastructure'
    result[0].click()
    assert option.clicked and element.parent.calls == 1


def test_restart_does_not_leave_runs_stuck_or_allow_uncertain_submit_retry(monkeypatch):
    from autoapply import service
    saved = {}
    monkeypatch.setattr(service, '_load_runs', lambda: {'draft': {'status': 'running', 'auto_submit_requested': False}, 'submit': {'status': 'running', 'auto_submit_requested': True}})
    monkeypatch.setattr(service, '_save_run', lambda rid, data: saved.update({rid: data}))
    service.recover_interrupted_runs()
    assert saved['draft']['status'] == 'interrupted'
    assert saved['submit']['status'] == 'submission_unconfirmed'


def test_profile_editor_preserves_legacy_date_and_custom_eligibility_answer(monkeypatch):
    from bs4 import BeautifulSoup
    from web import profile_view
    monkeypatch.setattr(profile_view, 'ensure_profile', lambda: {'education': {'graduation_date': '23/06/2028'}, 'eligibility': {'right_to_work_uk': 'Confirmed by employer'}})
    page = BeautifulSoup(profile_view.render_profile_html(), 'html.parser')
    assert page.find('input', {'name': 'education.graduation_date'})['value'] == '2028-06-23'
    assert page.find('select', {'name': 'eligibility.right_to_work_uk'}).find('option', selected=True)['value'] == 'Confirmed by employer'
