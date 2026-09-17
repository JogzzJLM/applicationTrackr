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


if __name__ == "__main__":
    unittest.main()
