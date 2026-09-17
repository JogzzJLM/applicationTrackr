import unittest
from core.relevance import contains_phrase, evaluate_job

BASE = {
    "grad_years_allowed": ["2027", "2028", "2029"],
    "target_programmes": ["internship", "placement", "graduate"],
    "target_role_categories": ["software", "ai_ml", "quant", "cyber"],
    "strict_location_filter": True, "allow_unknown_location": False,
    "allow_special_international": False, "relevance_min_score": 55,
    "exclude_keywords": [], "exclude_locations": [],
    "location_keywords": ["london", "uk", "united kingdom", "birmingham", "cambridge", "manchester"],
    "my_skills": ["python", "java", "sql", "docker", "machine learning", "pytorch", "algorithms"],
}

class RelevanceTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertFalse(contains_phrase("Vice President, Internal Audit", "intern"))
        self.assertFalse(contains_phrase("Software Engineer, International", "intern"))
    def test_good_swe(self):
        d = evaluate_job("Software Engineering Internship - Summer 2027", "Example", "London, United Kingdom", {"description":"Build Python services and APIs using SQL and Docker.","department":"Engineering","employment_type":"Intern"}, BASE)
        self.assertTrue(d.eligible); self.assertGreaterEqual(d.score, 68)
    def test_noise(self):
        for title in ("Vice President, Internal Audit", "Brand Social Media Intern", "Accounting Intern"):
            self.assertFalse(evaluate_job(title, "Cloudflare", "London, UK", settings=BASE).eligible)
    def test_us_filtered(self):
        d = evaluate_job("Software Engineer Intern", "Example", "New York, NY", {"country":"United States","description":"Python backend engineering","employment_type":"Intern"}, BASE)
        self.assertFalse(d.eligible)
    def test_phd_filtered(self):
        self.assertFalse(evaluate_job("Quantitative Research Intern (PhD) - Summer 2027", "Example", "London, UK", settings=BASE).eligible)

if __name__ == "__main__": unittest.main()
