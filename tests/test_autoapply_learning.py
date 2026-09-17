import unittest

from autoapply.learning import _nb_predict, normalize_label, predict_mapping


class LearningTests(unittest.TestCase):
    def test_builtin_mapping(self):
        key, confidence, _ = predict_mapping("Candidate Email Address")
        self.assertEqual(key, "personal.email")
        self.assertGreaterEqual(confidence, .9)

    def test_normalize(self):
        self.assertEqual(normalize_label("  LinkedIn URL (required)  "), "linkedin url required")

    def test_naive_bayes_classifier(self):
        examples = [
            ("personal.email", "candidate contact email address"),
            ("personal.email", "preferred email for correspondence"),
            ("personal.phone", "candidate mobile telephone number"),
            ("personal.phone", "best contact phone number"),
        ]
        key, confidence = _nb_predict("contact email", examples)
        self.assertEqual(key, "personal.email")
        self.assertGreater(confidence, 0.5)

    def test_builtin_alias_beats_ambiguous_ml_context(self):
        key, confidence, note = predict_mapping(
            "Email address",
            context="Contact details phone mobile address",
            domain="jobs.example.com",
        )
        self.assertEqual(key, "personal.email")
        self.assertGreaterEqual(confidence, .9)
        self.assertIn("builtin", note)


if __name__ == "__main__":
    unittest.main()
