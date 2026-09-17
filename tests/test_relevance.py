import unittest
from core.relevance import contains_phrase, evaluate_job
from core.storage import _merge_settings, SETTINGS_SCHEMA_VERSION

BASE = {
    "grad_years_allowed": ["2027", "2028", "2029"],
    "target_programmes": ["internship", "placement"],
    "target_role_categories": ["software", "ai_ml", "quant", "cyber"],
    "strict_location_filter": True, "allow_unknown_location": False,
    "allow_special_international": False, "relevance_min_score": 72,
    "exclude_keywords": [], "exclude_locations": [],
    "location_keywords": ["london", "uk", "united kingdom", "birmingham", "cambridge", "manchester"],
    "my_skills": ["python", "java", "sql", "docker", "machine learning", "pytorch", "algorithms"],
}

class RelevanceTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertFalse(contains_phrase("Vice President, Internal Audit", "intern"))
        self.assertFalse(contains_phrase("Software Engineer, International", "intern"))

    def test_good_swe(self):
        d = evaluate_job(
            "Software Engineering Internship - Summer 2027", "Example", "London, United Kingdom",
            {"description":"Build Python services and APIs using SQL and Docker.","department":"Engineering","employment_type":"Intern"},
            BASE,
        )
        self.assertTrue(d.eligible)
        self.assertGreaterEqual(d.score, 72)

    def test_barclays_style_technology_developer_title(self):
        d = evaluate_job(
            "2027 Technology Developer Summer Internship Programme London", "Barclays", "London, United Kingdom",
            {"description":"Develop software and technology solutions using Java and Python.", "department":"Technology", "employment_type":"Intern"},
            BASE,
        )
        self.assertTrue(d.eligible)

    def test_noise(self):
        for title in ("Vice President, Internal Audit", "Brand Social Media Intern", "Accounting Intern"):
            self.assertFalse(evaluate_job(title, "Cloudflare", "London, UK", settings=BASE).eligible)

    def test_non_cs_engineering_intern_filtered(self):
        for title in ("Mechanical Engineering Intern", "Civil Engineer Internship", "Electrical Engineering Intern"):
            d = evaluate_job(
                title, "Example", "London, UK",
                {"description":"Engineering team uses software tools and Python for analysis.", "department":"Engineering", "employment_type":"Intern"},
                BASE,
            )
            self.assertFalse(d.eligible, title)

    def test_us_filtered(self):
        d = evaluate_job("Software Engineer Intern", "Example", "New York, NY", {"country":"United States","description":"Python backend engineering","employment_type":"Intern"}, BASE)
        self.assertFalse(d.eligible)

    def test_phd_filtered(self):
        self.assertFalse(evaluate_job("Quantitative Research Intern (PhD) - Summer 2027", "Example", "London, UK", settings=BASE).eligible)

    def test_v5_migrates_permissive_persisted_settings(self):
        migrated = _merge_settings({
            "settings_schema_version": 4,
            "target_programmes": ["internship", "placement", "graduate"],
            "strict_location_filter": False,
            "allow_unknown_location": True,
            "allow_special_international": True,
            "relevance_min_score": 55,
            "exclude_keywords": [],
            "exclude_locations": [],
            "location_keywords": ["remote"],
        })
        self.assertEqual(migrated["settings_schema_version"], SETTINGS_SCHEMA_VERSION)
        self.assertEqual(migrated["target_programmes"], ["internship", "placement"])
        self.assertTrue(migrated["strict_location_filter"])
        self.assertFalse(migrated["allow_unknown_location"])
        self.assertFalse(migrated["allow_special_international"])
        self.assertGreaterEqual(migrated["relevance_min_score"], 72)
        self.assertNotIn("remote", [x.lower() for x in migrated["location_keywords"]])

if __name__ == "__main__":
    unittest.main()
