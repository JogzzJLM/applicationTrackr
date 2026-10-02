import pytest
from autoapply import browser_handoff, profile, learning


def test_browser_payload_excludes_other_jobs_answers_and_only_includes_owned_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_handoff, '_find_job', lambda job_id: {'link': 'https://boards.greenhouse.io/figma/jobs/123', 'company': 'Figma', 'title': 'Intern'})
    monkeypatch.setattr(browser_handoff, 'AUTOAPPLY_DIR', tmp_path)
    cv = tmp_path / 'resume.pdf'
    cv.write_bytes(b'%PDF-1.7 example')
    monkeypatch.setattr(browser_handoff, 'ensure_profile', lambda: {'personal': {'first_name': 'Joga'}, 'documents': {'resume_path': str(cv)}})
    monkeypatch.setattr(browser_handoff, '_load', lambda: {'question_answers': [
        {'question': 'why join us', 'answer': 'Figma answer', 'domain': 'https://boards.greenhouse.io/figma/jobs/123'},
        {'question': 'other question', 'answer': 'Other company private answer', 'domain': 'https://boards.greenhouse.io/other/jobs/456'}]})
    bundle = browser_handoff.browser_bundle('123')
    assert bundle['profile']['personal.first_name'] == 'Joga'
    assert bundle['answers'] == {'why join us': 'Figma answer'}
    assert bundle['documents'][0]['base64'].startswith('JVBER')
    assert not any(key.startswith('documents.') for key in bundle['profile'])


def test_browser_payload_rejects_bad_job_and_unsafe_destinations(monkeypatch):
    monkeypatch.setattr(browser_handoff, '_find_job', lambda job_id: None)
    with pytest.raises(ValueError): browser_handoff.browser_bundle('missing')
    monkeypatch.setattr(browser_handoff, '_find_job', lambda job_id: {'link': 'javascript:alert(1)'})
    with pytest.raises(ValueError): browser_handoff.browser_bundle('bad')
