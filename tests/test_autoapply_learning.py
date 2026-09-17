import unittest
from autoapply.learning import normalize_label, predict_mapping

class LearningTests(unittest.TestCase):
    def test_builtin_mapping(self):
        key, confidence, _ = predict_mapping("Candidate Email Address")
        self.assertEqual(key, "personal.email")
        self.assertGreaterEqual(confidence, .9)
    def test_normalize(self):
        self.assertEqual(normalize_label("  LinkedIn URL (required)  "), "linkedin url required")

if __name__ == "__main__": unittest.main()
