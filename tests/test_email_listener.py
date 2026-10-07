import unittest

from email_listener import classify_email_stage, extract_company_name, _rank_apps_from_email


BARCLAYS_ASSESSMENT = """
Barclays Email Classification: Restricted - External

Dear, Joga,

You have reached the next stage of the assessment process in your application for the position of
JR-0000129397 2027 Technology Developer Summer Internship Programme London (Evergreen) (Open)
that requires an online assessment.

We now invite you to our Experience Platform to complete your assessment.

Kind regards,
Barclays Talent Acquisition Team
"""


class EmailListenerTests(unittest.TestCase):
    def test_barclays_assessment_is_online_assessment(self):
        self.assertEqual(classify_email_stage(BARCLAYS_ASSESSMENT), "Online Assessment")

    def test_company_can_be_recovered_from_body_signature(self):
        self.assertEqual(
            extract_company_name("Fwd: Invitation to complete an online assessment", "me@gmail.com", BARCLAYS_ASSESSMENT),
            "Barclays",
        )

    def test_forwarded_barclays_email_matches_logged_role(self):
        apps = [
            {"company": "Barclays", "role": "2027 Technology Developer Summer Internship Programme London"},
            {"company": "Cohere", "role": "Software Engineer Intern"},
        ]
        ranked = _rank_apps_from_email(
            "Fwd: Invitation to complete an online assessment",
            BARCLAYS_ASSESSMENT,
            apps,
        )
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][1]["company"], "Barclays")
        self.assertGreaterEqual(ranked[0][0], 10)


ODEON_NEWSLETTER = """Other Mommy in cinemas Friday
Joga, secrets, scares and scandals hit ODEON this week.
Interviews with the director and cast of Digger
Interviews with the cast of Verity
Book your tickets to enter our prize draw. Terms and conditions apply.
Facebook Twitter Instagram Apple Store Google Play
Please do not reply directly to this email. ODEON Cinemas Limited.
Unsubscribe | Privacy policy | View in browser
"""

class NonRecruitmentEmailTests(unittest.TestCase):
    def test_odeon_newsletter_is_not_an_application_event(self):
        self.assertIsNone(classify_email_stage(ODEON_NEWSLETTER))
    def test_generic_next_step_or_call_is_not_an_interview(self):
        for text in ('The next step is to book cinema tickets', 'Speaking with the cast', 'Join our video call', 'Read these interview tips'):
            self.assertIsNone(classify_email_stage(text))
    def test_actual_interview_invitation_and_confirmation_still_work(self):
        for text in ('We invite you to interview for the Software Engineering Intern role.', 'Your second interview is scheduled for Friday.', 'Interview invitation: Google Software Engineer Intern', 'Please book your interview using the link below.'):
            self.assertEqual(classify_email_stage(text),'Interview')
    def test_google_play_footer_does_not_match_google_application(self):
        from core.jobs import Job, match_email_to_job
        job=Job('google-test','Google','Software Engineering Intern',stages=['Applied'])
        result,candidates,_=match_email_to_job([job],'Odeon','Other Mommy in cinemas Friday','news@odeon.co.uk',ODEON_NEWSLETTER)
        self.assertIsNone(result);self.assertEqual(candidates,[])
    def test_real_google_recruitment_mail_matches_even_with_footer(self):
        from core.jobs import Job, match_email_to_job
        job=Job('google-test','Google','Software Engineering Intern',stages=['Applied'])
        result,_,_=match_email_to_job([job],'Google','Interview invitation','Recruiter <recruiter@careers.google.com>','Your interview is confirmed. Google Play')
        self.assertEqual(result.id,job.id)
    def test_newsletter_ingestion_never_records_or_notifies(self):
        from unittest.mock import patch
        from email_listener import _process_message
        with patch('email_listener.handle_incoming_email_update') as handle, patch('email_listener.save_seen_emails'):
            seen=set();self.assertEqual(_process_message('Gmail','odeon-test','Other Mommy in cinemas Friday','news@odeon.co.uk',ODEON_NEWSLETTER,seen),1)
        handle.assert_not_called();self.assertIn('odeon-test',seen)


if __name__ == "__main__":
    unittest.main()
