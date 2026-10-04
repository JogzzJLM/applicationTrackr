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


def test_mobile_bundle_has_document_names_without_loading_file_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_handoff, '_find_job', lambda _: {'link': 'https://example.com/jobs/123', 'company': 'Example'})
    monkeypatch.setattr(browser_handoff, 'AUTOAPPLY_DIR', tmp_path)
    cv = tmp_path / 'resume.pdf'
    cv.write_bytes(b'%PDF-1.7 example')
    monkeypatch.setattr(browser_handoff, 'ensure_profile', lambda: {'personal': {'first_name': 'Jane'}, 'documents': {'resume_path': str(cv)}})
    monkeypatch.setattr(browser_handoff, '_load', lambda: {})
    bundle = browser_handoff.browser_bundle('123', include_documents=False)
    assert bundle['documents'] == [{'key': 'documents.resume_path', 'name': 'resume.pdf', 'mime': 'application/pdf'}]
    assert browser_handoff.owned_document('resume_path') == cv
    assert browser_handoff.owned_document('../../private.txt') is None


def test_document_download_rejects_external_files_and_symlinks(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    external = tmp_path / 'private.pdf'
    external.write_bytes(b'%PDF-1.7 private')
    link = uploads / 'resume.pdf'
    link.symlink_to(external)
    monkeypatch.setattr(browser_handoff, 'AUTOAPPLY_DIR', uploads)
    for path in (external, link):
        monkeypatch.setattr(browser_handoff, 'ensure_profile', lambda: {'documents': {'resume_path': str(path)}})
        assert browser_handoff.owned_document('resume_path') is None


def test_saved_document_download_is_private_and_rejects_arbitrary_paths(tmp_path, monkeypatch):
    import threading
    import requests
    from web.handlers import ThreadedHTTPServer, CleanHandler
    monkeypatch.setattr(browser_handoff, 'AUTOAPPLY_DIR', tmp_path)
    cv = tmp_path / 'resume.pdf'
    cv.write_bytes(b'%PDF-1.7 example')
    monkeypatch.setattr(browser_handoff, 'ensure_profile', lambda: {'documents': {'resume_path': str(cv)}})
    server = ThreadedHTTPServer(('127.0.0.1', 0), CleanHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        response = requests.get(url + '/api/autoapply/document?kind=resume_path')
        assert response.content == cv.read_bytes()
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['Content-Disposition'].startswith('attachment;')
        assert 'Access-Control-Allow-Origin' not in response.headers
        profile_page = requests.get(url + '/profile')
        assert profile_page.status_code == 200
        assert 'Access-Control-Allow-Origin' not in profile_page.headers
        assert 'no-store' in profile_page.headers['Cache-Control']
        assert requests.get(url + '/api/autoapply/document?kind=../../private.pdf').status_code == 404
    finally:
        server.shutdown()
        server.server_close()
