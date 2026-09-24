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
    # B9 (CFO 21-Sep-2026): the money block is exactly three lines — Total Claim,
    # Less Excess worded as the policy states it, Total Claim Payable — and the
    # excess is read from the policy, never defaulted.
    "excess_wording": "Less Excess as per policy schedule (5% of claim, minimum P3,000)",
    "figures": {
        "lines": [
            {"label": "Sum insured", "amount": "100000.00"},
            {"label": "Less: excess", "amount": "-5000.00"},
        ],
        "net": "95000.00",
        "net_display": "95,000.00",
        "requires_review": True,
        "note": "",
        "total_claim": "100000.00",
        "excess": "5000.00",
        "other_deductions": "0.00",
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
    # B10 (CFO 21-Sep-2026): a decline letter names the policy section, quotes the
    # clause word for word with its own numbering, and is signed by the Claims
    # Manager. It refuses to render without them.
    "policy_section": "Section 3 - General Exceptions",
    "clause_number": "3.2(a)",
    "clause_text": "The Company shall not be liable while the vehicle is being "
                   "driven by any person under the influence of intoxicating liquor.",
    "claims_manager_name": "Test Manager",
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
        # The money block now reads the three approved figures, so the bad value
        # goes where the letter actually reads it.
        bad = dict(AOL, figures=dict(AOL["figures"], total_claim="abc"))
        with self.assertRaises(ValueError):
            render_html("aol", bad)

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            render_html("other", AOL)
        with self.assertRaises(ValueError):
            render_pdf("other", AOL)


if __name__ == "__main__":
    unittest.main()
