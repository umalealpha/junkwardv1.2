"""Local CV↔job matcher tests — deterministic, no external AI."""
from django.test import TestCase

from recruitment.matching import extract_skills, normalise_required, score_candidate


class MatchingTest(TestCase):
    CV = ("Experienced insurance underwriter with 6 years in short-term insurance. "
          "Strong claims handling, reinsurance treaty knowledge, advanced Excel and "
          "Power BI. IFRS 17 reporting exposure. Fluent communication and leadership.")

    def test_extract_skills_finds_domain_skills(self):
        s = set(extract_skills(self.CV))
        for expected in ("underwriting", "claims", "reinsurance", "excel", "power bi", "ifrs"):
            self.assertIn(expected, s)

    def test_normalise_required_maps_synonyms(self):
        self.assertEqual(normalise_required(["Underwriter", "IFRS 17", "custom skill"]),
                         ["underwriting", "ifrs", "custom skill"])

    def test_score_full_match(self):
        r = score_candidate(["underwriting", "claims", "reinsurance"], self.CV)
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["missing"], [])

    def test_score_partial_with_gap(self):
        r = score_candidate(["underwriting", "actuarial", "sql"], self.CV)
        self.assertEqual(r["matched"], ["underwriting"])
        self.assertIn("actuarial", r["missing"])
        self.assertIn("sql", r["missing"])
        self.assertEqual(r["score"], 33)

    def test_custom_required_phrase_matched_from_text(self):
        # a required phrase not in the taxonomy still matches if it's in the CV
        r = score_candidate(["short-term insurance"], self.CV)
        self.assertEqual(r["score"], 100)

    def test_no_required_ranks_by_breadth(self):
        r = score_candidate([], self.CV)
        self.assertGreater(r["score"], 0)
        self.assertEqual(r["missing"], [])
