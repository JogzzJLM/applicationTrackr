import email
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import core.jobs as jobs
import core.storage as storage
import email_listener as listener
import notifications
import sheets
from autoapply import browser_agent


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for module, name, filename in ((jobs, 'JOBS_FILE', 'jobs.json'), (jobs, 'MANUAL_JOBS_FILE', 'manual.json'),
                                      (notifications, 'OUTBOX_FILE', 'outbox.json'),
                                      (storage, 'PENDING_EMAILS_FILE', 'pending.json'), (listener, 'SEEN_EMAILS_FILE', 'seen.json')):
            patcher = patch.object(module, name, str(Path(self.temp.name) / filename))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_rejection_after_interview_is_not_interview(self):
        self.assertEqual(listener.classify_email_stage('Thank you for your interview. We regret to inform you we will not be proceeding.'), 'Rejected')

    def test_consideration_alone_is_not_rejection(self):
        self.assertEqual(listener.classify_email_stage('After careful consideration, invitation to interview'), 'Interview')

    def test_unique_role_allocates_among_two_same_company_jobs(self):
        roles = [jobs.Job('one', 'Acme', 'Software Engineering Intern'), jobs.Job('two', 'Acme', 'Quant Research Intern')]
        match, _, _ = jobs.match_email_to_job(roles, 'Acme', 'Software Engineering Intern application', 'careers@acme.com', '')
        self.assertEqual(match.id, 'one')
        match, options, _ = jobs.match_email_to_job(roles, 'Acme', 'Your application', 'careers@acme.com', '')
        self.assertIsNone(match)
        self.assertEqual(len(options), 2)

    def test_job_url_matches_with_tracking_parameters(self):
        role = jobs.Job('one', 'Acme', 'Software Intern', link='https://jobs.lever.co/acme/12345678-1234-abcd-1234-123456789000')
        match, _, _ = jobs.match_email_to_job([role], 'Application Company', 'Update', 'noreply@lever.co', role.link + '?utm_source=email')
        self.assertEqual(match.id, role.id)

    def test_repository_retains_custom_data_and_deduplicates_events(self):
        repo = jobs.JobRepository()
        job = repo.sync([{'id':'one','company':'Acme','title':'Software Intern'}], [])[0]
        jobs.save_job_details(job.id, 'Ask about remote work', {'contact':'Recruiter'})
        repo.record_email(job, 'message1', 'Interview', 'Invitation', synced=True)
        repo.record_email(job, 'message1', 'Interview', 'Invitation', synced=True)
        updated = repo.sync([{'id':'one','company':'Acme','title':'Software Intern'}], [])[0]
        self.assertEqual(updated.notes, 'Ask about remote work')
        self.assertEqual(updated.custom_fields['contact'], 'Recruiter')
        self.assertEqual(len(updated.email_events), 1)

    def test_manual_job_accepts_no_url_and_rejects_unsafe_url(self):
        row = jobs.add_manual_job('Acme', 'Software Intern', notes='Contact directly')
        self.assertEqual(row['source'], 'Manual')
        with self.assertRaises(ValueError):
            jobs.add_manual_job('Acme', 'Software Intern', link='javascript:alert(1)')

    def test_failed_sheet_write_is_queued_and_attached(self):
        with patch('scrapers_engine.audit.load_discovered_jobs', return_value=[{'id':'one','company':'Acme','title':'Software Intern'}]), \
             patch.object(listener, '_get_sheet_apps_with_retry', return_value=[]), \
             patch.object(listener, 'update_google_sheet_via_webhook', return_value=False), \
             patch.object(listener, 'send_notification'):
            for _ in range(2):
                listener.handle_incoming_email_update('Acme', 'Interview', 'Software Intern interview', event_id='message1')
        pending = storage.load_pending_email_updates()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['job_id'], 'one')
        saved = json.loads(Path(jobs.JOBS_FILE).read_text())['one']
        self.assertFalse(saved['email_events'][0]['sheet_synced'])

    def test_message_not_marked_seen_when_ingestion_raises(self):
        seen = set()
        with patch.object(listener, 'handle_incoming_email_update', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                listener._process_message('Test', 'msg', 'Application received', 'careers@acme.com', '', seen)
        self.assertNotIn('msg', seen)

    def test_html_email_body_preserves_job_links(self):
        msg = email.message_from_string('Content-Type: text/html; charset=utf-8\n\n<p>Interview &amp; details</p><a href="https://example.com/job">Job</a>')
        text = listener._extract_plain_text(msg)
        self.assertIn('Interview & details', text)
        self.assertIn('https://example.com/job', text)

    def test_ntfy_rejection_never_reports_success(self):
        with patch.object(notifications, 'NTFY_TOPIC', 'test-topic'), patch.object(notifications, 'update_scraper_status') as status, \
             patch.object(notifications.requests, 'post', return_value=Mock(status_code=403)):
            self.assertFalse(notifications.send_notification('Test', 'body'))
            ntfy_updates = [call.args[1] for call in status.call_args_list if call.args[0] == 'ntfy']
            self.assertIn('403', ntfy_updates[-1]['error'])

    def test_ntfy_requires_message_receipt(self):
        with patch.object(notifications, 'NTFY_TOPIC', 'test-topic'), patch.object(notifications, 'update_scraper_status'), \
             patch.object(notifications.requests, 'post', return_value=Mock(status_code=200, json=lambda:{'id':'123','event':'message'})):
            self.assertTrue(notifications.send_notification('Test', 'body'))

    def test_sheet_html_login_page_is_not_success(self):
        response = Mock(status_code=200, headers={'Content-Type':'text/html'}, text='<html>Sign in</html>')
        with patch.object(sheets, 'GOOGLE_SHEET_WEBHOOK_URL', 'https://example.com/webhook'), patch.object(sheets.requests, 'post', return_value=response):
            self.assertFalse(sheets.update_google_sheet_via_webhook('Acme', 'Applied', resolve_sequential=False))

    def test_explicit_numbered_interview_does_not_increment(self):
        with patch.object(sheets, 'get_detailed_applications', return_value=[{'company':'Acme','role':'Intern','stages':['Applied','Interview 1']}]):
            self.assertEqual(sheets.resolve_smart_stage('Acme', 'Interview 2', 'Intern'), 'Interview 2')
            self.assertIsNone(sheets.resolve_smart_stage('Acme', 'Interview 1', 'Intern'))

    def test_captcha_library_alone_is_not_a_challenge(self):
        driver = Mock(page_source='<script src="recaptcha/api.js"></script>')
        driver.find_elements.return_value = []
        driver.find_element.return_value.text = 'Apply for Software Engineering Internship'
        self.assertFalse(browser_agent._page_has_captcha(driver))
        driver.find_element.return_value.text = 'Please verify you are human'
        self.assertTrue(browser_agent._page_has_captcha(driver))

    def test_jobs_in_different_recruitment_years_are_distinct(self):
        repo = jobs.JobRepository()
        rows = repo.sync([{'id':'2026','company':'Acme','title':'Software Intern 2026'},
                          {'id':'2027','company':'Acme','title':'Software Intern 2027'}], [])
        self.assertEqual(len(rows), 2)

    def test_dashboard_renders_sheet_link_and_manual_job_controls(self):
        from web.views import render_unified_dashboard_html
        with patch('web.views.fetch_google_sheet_csv', return_value='Company,Role,Stage 1\nAcme,Software Intern,Applied\n'), \
             patch('web.views.load_discovered_jobs', return_value=[]), \
             patch('web.views.get_sheet_edit_url', return_value='https://docs.google.com/spreadsheets/d/example/edit'):
            markup = render_unified_dashboard_html('jobs')
        self.assertIn('Open Sheet', markup)
        self.assertIn('manual-job-fields', markup)
        self.assertIn('job-details-fields', markup)
        self.assertIn('form.elements.notes', markup)

    def test_pending_email_content_is_escaped(self):
        from web.pending_view import render_pending_updates
        markup = render_pending_updates([{'id':'one','company':'Acme','subject':'<script>alert(1)</script>', 'options':[]}])
        self.assertNotIn('<script>', markup)
        self.assertIn('&lt;script&gt;', markup)
        self.assertIn('Assign another role', markup)

    def test_short_company_name_does_not_alias_unrelated_employer(self):
        from core.normalization import normalize_company
        self.assertEqual(normalize_company('O'), 'o')
        self.assertEqual(normalize_company('MW'), 'marshallwace')

    def test_driver_start_failure_returns_error_result(self):
        with patch.object(browser_agent, '_make_driver', side_effect=RuntimeError('No browser')):
            self.assertEqual(browser_agent.run_application('https://example.com', {}, inspect_only=True).status, 'error')

    def test_inspection_does_not_fill_fields(self):
        driver = Mock(current_url='https://example.com')
        element = Mock(tag_name='input')
        element.get_attribute.return_value = None
        element.find_element.side_effect = Exception('No dropdown ancestor')
        with patch.object(browser_agent, '_make_driver', return_value=driver), \
             patch.object(browser_agent, '_page_has_captcha', return_value=False), \
             patch.object(browser_agent, '_discover_fields', return_value=[(element,'Email','email','')]), \
             patch.object(browser_agent.time, 'sleep'):
            result = browser_agent.run_application('https://example.com', {}, inspect_only=True)
        self.assertEqual(result.status, 'form_inspected')
        element.send_keys.assert_not_called()
        element.click.assert_not_called()
        self.assertFalse(result.submitted)
        driver.quit.assert_called_once()

    def test_submit_click_without_receipt_is_unconfirmed(self):
        driver, submit = Mock(current_url='https://example.com'), Mock()
        driver.find_element.return_value.text = 'There was an error processing your application'
        with patch.object(browser_agent, '_make_driver', return_value=driver), \
             patch.object(browser_agent, '_page_has_captcha', return_value=False), \
             patch.object(browser_agent, '_discover_fields', return_value=[]), \
             patch.object(browser_agent, '_find_progress_button', return_value=(None,'')), \
             patch.object(browser_agent, '_find_submit_button', return_value=(submit,'submit')), \
             patch.dict('os.environ', {'AUTOAPPLY_AUTO_SUBMIT':'true'}), patch.object(browser_agent.time, 'sleep'):
            result = browser_agent.run_application('https://example.com', {}, auto_submit=True)
        self.assertEqual(result.status, 'submission_unconfirmed')
        self.assertFalse(result.submitted)

if __name__ == '__main__':
    unittest.main()
