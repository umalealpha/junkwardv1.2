"""B9 / B10 — the wording the CFO signed off, enforced on the real renderers.

Two defects were already sitting in the dark build when this was written:

  * The repudiation letter told the client to write back "within 30 days".
    The CFO: "THE APPEAL WINDOW IS SIX MONTHS TO THE PRINCIPAL OFFICER OR CHIEF
    EXECUTIVE OFFICER, NOT THIRTY DAYS ... Any thirty-day wording anywhere in
    the build is wrong."
  * The Agreement of Loss carried a "For Alpha Direct Insurance" signature line.
    The CFO: signed by the CLAIMANT AND A WITNESS, with NO Alpha Direct
    signatory.

Both the HTML body and the PDF story are checked, because the client is sent the
PDF and a fix applied to only one of the two is not a fix.
"""

import unittest

from claims_automation import letters

AOL_CTX = {
    "draft": False,
    "insured_name": "Test Claimant",
    "claim_number": "DEMO-CLAIM-0001",
    "policy_number": "DEMO-POLICY-0001",
    "vehicle": "Toyota Hilux B 123 ABC",
    "date_of_loss": "2026-05-22",
    "excess_wording": "Less Excess as per policy schedule (5% of claim, minimum P3,000)",
    "figures": {
        "net": "85107.50",
        "net_display": "85,107.50",
        "total_claim": "90107.50",
        "excess": "5000.00",
        "lines": [
            {"label": "Total Claim", "amount": "90107.50"},
            {"label": "Less Excess", "amount": "-5000.00"},
            {"label": "Total Claim Payable", "amount": "85107.50"},
        ],
    },
}

REP_CTX = {
    "draft": False,
    "insured_name": "Test Claimant",
    "postal_address": "DEMO ADDRESS LINE 1, DEMO TOWN",
    "claim_number": "DEMO-CLAIM-0001",
    "policy_number": "DEMO-POLICY-0001",
    "vehicle": "Toyota Hilux B 123 ABC",
    "date_of_loss": "2026-05-22",
    "reasons": ["The driver was not licensed to drive the insured vehicle."],
    "policy_section": "Section 3 — General Exceptions",
    "clause_number": "3.2(b)",
    "clause_text": "The Company shall not be liable in respect of any accident "
                   "while the vehicle is being driven by any person not holding "
                   "a licence to drive such vehicle.",
    "claims_manager_name": "Test Manager",
}


def _pdf_text(kind, ctx):
    """Flatten the PDF story to searchable text without building a real PDF."""
    story = (letters._aol_pdf_story if kind == "aol" else letters._repudiation_pdf_story)
    from reportlab.lib.styles import getSampleStyleSheet
    parts = []
    for flowable in story(ctx, getSampleStyleSheet()):
        text = getattr(flowable, "text", None)
        if text:
            parts.append(str(text))
            continue
        rows = getattr(flowable, "_cellvalues", None)
        if rows:
            for row in rows:
                for cell in row:
                    parts.append(str(getattr(cell, "text", cell)))
    return " ".join(parts)


class RepudiationWording(unittest.TestCase):
    """B10 — the appeal window, who signs, and the clause quoted verbatim."""

    def setUp(self):
        self.html = letters.render_html("repudiation", REP_CTX)
        self.pdf = _pdf_text("repudiation", REP_CTX)

    def test_no_thirty_day_wording_in_the_html(self):
        low = self.html.lower()
        self.assertNotIn("30 days", low)
        self.assertNotIn("thirty days", low)
        self.assertNotIn("30-day", low)

    def test_no_thirty_day_wording_in_the_pdf(self):
        low = self.pdf.lower()
        self.assertNotIn("30 days", low)
        self.assertNotIn("thirty days", low)

    def test_six_month_appeal_window_in_both(self):
        for body in (self.html.lower(), self.pdf.lower()):
            self.assertIn("six months", body)

    def test_appeal_goes_to_the_principal_officer_or_ceo(self):
        for body in (self.html.lower(), self.pdf.lower()):
            self.assertIn("principal officer", body)
            self.assertIn("chief executive officer", body)

    def test_without_prejudice_at_the_top(self):
        low = self.html.lower()
        self.assertIn("without prejudice", low)
        # "at the top" — before the body of the letter, i.e. before the salutation.
        self.assertLess(low.index("without prejudice"), low.index("dear "))
        self.assertIn("without prejudice", self.pdf.lower())

    def test_signed_by_the_claims_manager_for_the_company(self):
        for body in (self.html, self.pdf):
            self.assertIn("Test Manager", body)
            self.assertIn("Claims Manager", body)
            self.assertIn("Alpha Direct Insurance Company", body)

    def test_the_clause_is_quoted_word_for_word_with_its_numbering(self):
        for body in (self.html, self.pdf):
            self.assertIn("3.2(b)", body)
            self.assertIn("not holding a licence to drive such vehicle", body)
            self.assertIn("Section 3 — General Exceptions", body)

    def test_the_subject_line_carries_policy_name_and_claim(self):
        low = self.html.lower()
        self.assertIn("repudiation", low)
        self.assertIn("DEMO-POLICY-0001", self.html)
        self.assertIn("DEMO-CLAIM-0001", self.html)


class AgreementOfLossWording(unittest.TestCase):
    """B9 — the title, the three-line money block, the words, the signatures."""

    def setUp(self):
        self.html = letters.render_html("aol", AOL_CTX)
        self.pdf = _pdf_text("aol", AOL_CTX)

    def test_title_is_agreement_of_loss_slash_tax_invoice(self):
        for body in (self.html.upper(), self.pdf.upper()):
            self.assertIn("AGREEMENT OF LOSS", body)
            self.assertIn("TAX INVOICE", body)

    def test_without_prejudice_and_the_approval_caveat(self):
        for body in (self.html.upper(), self.pdf.upper()):
            self.assertIn("WITHOUT PREJUDICE", body)
            self.assertIn("PAYMENT IS SUBJECT TO SENIOR MANAGEMENT'S APPROVAL", body)

    def test_money_block_is_exactly_three_lines(self):
        for label in ("Total Claim", "Less Excess", "Total Claim Payable"):
            self.assertIn(label, self.html)
            self.assertIn(label, self.pdf)
        # Nothing else may be deducted until Finance confirms what else comes off.
        for banned in ("outstanding premium", "salvage retained"):
            self.assertNotIn(banned, self.html.lower())
            self.assertNotIn(banned, self.pdf.lower())

    def test_the_excess_is_worded_exactly_as_the_policy_states_it(self):
        self.assertIn("minimum P3,000", self.html)
        self.assertIn("minimum P3,000", self.pdf)

    def test_the_amount_appears_in_figures_and_in_words(self):
        for body in (self.html, self.pdf):
            self.assertIn("85,107.50", body)
            self.assertIn(
                "Eighty-five thousand one hundred and seven pula and fifty thebe", body
            )

    def test_signed_by_the_claimant_and_a_witness_only(self):
        for body in (self.html, self.pdf):
            self.assertIn("Claimant", body)
            self.assertIn("Witness", body)
        # NO Alpha Direct signatory on this document.
        self.assertNotIn("For Alpha Direct Insurance", self.html)
        self.assertNotIn("For Alpha Direct Insurance", self.pdf)


class ADeclineLetterRefusesToRenderHalfEmpty(unittest.TestCase):
    """B10 — a decline that quotes no clause is not a decline, it is a defect.

    Raised by the review panel: every repudiation field defaulted to blank and
    the letter still rendered, so a customer could receive a decline naming no
    section, quoting no clause and signed by nobody.
    """

    def _without(self, key):
        ctx = {k: v for k, v in REP_CTX.items()}
        ctx[key] = ""
        return ctx

    def test_each_required_field_refuses_when_blank(self):
        for key in ("policy_section", "clause_number", "clause_text",
                    "claims_manager_name", "insured_name", "claim_number"):
            with self.subTest(field=key):
                with self.assertRaises(ValueError):
                    letters.render_html("repudiation", self._without(key))

    def test_a_decline_with_no_reason_refuses(self):
        ctx = {k: v for k, v in REP_CTX.items()}
        ctx["reasons"] = []
        with self.assertRaises(ValueError):
            letters.render_html("repudiation", ctx)

    def test_the_pdf_refuses_on_the_same_fields(self):
        with self.assertRaises(ValueError):
            _pdf_text("repudiation", self._without("clause_text"))

    def test_the_refusal_says_in_plain_words_what_is_missing(self):
        try:
            letters.render_html("repudiation", self._without("clause_text"))
        except ValueError as exc:
            self.assertIn("word for word", str(exc))
        else:
            self.fail("a blank clause rendered a decline letter")


class ExcessIsNeverDefaulted(unittest.TestCase):
    """B9 — "THE EXCESS IS READ FROM THE POLICY AND NEVER DEFAULTED."""

    def test_a_missing_excess_refuses_to_render_rather_than_assuming_zero(self):
        ctx = {k: v for k, v in AOL_CTX.items()}
        ctx["figures"] = dict(ctx["figures"])
        ctx["figures"].pop("excess")
        ctx.pop("excess_wording")
        with self.assertRaises(ValueError):
            letters.render_html("aol", ctx)

    def test_a_blank_excess_wording_refuses_to_render(self):
        ctx = {k: v for k, v in AOL_CTX.items()}
        ctx["excess_wording"] = ""
        with self.assertRaises(ValueError):
            letters.render_html("aol", ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
