"""letters — the Agreement of Loss and repudiation letters, as HTML (email body)
and PDF (attachment). Client-facing: plain, warm, no house-banned phrases, every
value escaped, a visible DRAFT mark until the claims manager signs the wording off."""

import unittest

from claims_automation.letters import render_html, render_pdf

AOL = {
    "claim_number": "G2026000123",
    "insured_name": "Test Insured",
    "policy_number": "DOMG2026000001",
    "vehicle": "TOYOTA HILUX B123ABC",
    "date_of_loss": "2026-09-10",
    "figures": {
        "lines": [
            {"label": "Sum insured", "amount": "100000.00"},
            {"label": "Less: excess", "amount": "-5000.00"},
        ],
        "net": "95000.00",
        "net_display": "95,000.00",
        "requires_review": True,
        "note": "",
    },
    "draft": True,
}
REP = {
    "claim_number": "G2026000999",
    "insured_name": "Test Insured",
    "policy_number": "DOMG2026000002",
    "date_of_loss": "2026-09-10",
    "reasons": [
        "The police report records that the driver was under the influence of alcohol."
    ],
    "draft": False,
}


class LetterTests(unittest.TestCase):
    def test_aol_html_carries_facts(self):
        h = render_html("aol", AOL)
        for s in (
            "G2026000123",
            "Test Insured",
            "DOMG2026000001",
            "95,000.00",
            "5,000.00",
            "Agreement of Loss",
            "DRAFT",
        ):
            self.assertIn(s, h)

    def test_repudiation_html_carries_reasons_and_no_draft_mark(self):
        h = render_html("repudiation", REP)
        self.assertIn("G2026000999", h)
        self.assertIn("under the influence of alcohol", h)
        self.assertNotIn("DRAFT", h)

    def test_values_are_escaped(self):
        h = render_html("repudiation", dict(REP, insured_name="<script>x</script>"))
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_banned_phrases_absent(self):
        for kind, ctx in (("aol", AOL), ("repudiation", REP)):
            h = render_html(kind, ctx).lower()
            for banned in ("good day", "kindly note", "hope this finds you well"):
                self.assertNotIn(banned, h)

    def test_house_colours(self):
        h = render_html("aol", AOL)
        self.assertIn("#0D1B2A", h)
        self.assertIn("#F4A623", h)

    def test_pdf_bytes(self):
        for kind, ctx in (("aol", AOL), ("repudiation", REP)):
            b = render_pdf(kind, ctx)
            self.assertTrue(b.startswith(b"%PDF"))
            self.assertGreater(len(b), 1000)

    def test_bad_amount_is_refused_not_shown_as_zero(self):
        bad = dict(AOL, figures=dict(AOL["figures"], lines=[{"label": "Sum insured", "amount": "abc"}]))
        with self.assertRaises(ValueError):
            render_html("aol", bad)

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            render_html("other", AOL)
        with self.assertRaises(ValueError):
            render_pdf("other", AOL)


if __name__ == "__main__":
    unittest.main()
