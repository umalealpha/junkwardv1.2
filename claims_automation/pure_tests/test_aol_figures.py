"""aol_figures.settlement — the Agreement of Loss arithmetic. Decimal, HALF UP,
2 dp, never float. Review-only: every result carries requires_review=True."""

import unittest
from decimal import Decimal

from claims_automation.aol_figures import settlement


class SettlementTests(unittest.TestCase):
    def test_basic(self):
        r = settlement("100000", "5000")
        self.assertEqual(r["net"], "95000.00")
        self.assertEqual(r["net_display"], "95,000.00")
        self.assertTrue(r["requires_review"])
        self.assertEqual(r["note"], "")
        self.assertEqual(
            r["lines"],
            [
                {"label": "Sum insured", "amount": "100000.00"},
                {"label": "Less: excess", "amount": "-5000.00"},
            ],
        )

    def test_optional_deductions_only_when_positive(self):
        r = settlement(
            Decimal("80000"),
            3500,
            outstanding_premium="1234.565",
            salvage_retained=2000,
        )
        self.assertEqual(
            [l["label"] for l in r["lines"]],
            [
                "Sum insured",
                "Less: excess",
                "Less: outstanding premium",
                "Less: salvage retained by insured",
            ],
        )
        self.assertEqual(r["lines"][2]["amount"], "-1234.57")
        self.assertEqual(r["net"], "73265.43")

    def test_half_up(self):
        self.assertEqual(settlement("0.005", 0)["net"], "0.01")

    def test_float_read_at_printed_value(self):
        self.assertEqual(settlement(0.1, 0)["net"], "0.10")

    def test_none_deductions_are_zero(self):
        self.assertEqual(settlement("100", None, None, None)["net"], "100.00")

    def test_never_negative(self):
        r = settlement("1000", "5000")
        self.assertEqual(r["net"], "0.00")
        self.assertIn("review", r["note"])

    def test_sum_insured_required(self):
        with self.assertRaises(ValueError):
            settlement(None, 0)

    def test_negative_input_rejected(self):
        with self.assertRaises(ValueError):
            settlement("100", "-1")


if __name__ == "__main__":
    unittest.main()


class AMissingExcessStaysMissing(unittest.TestCase):
    """B9 — THE EXCESS IS READ FROM THE POLICY AND NEVER DEFAULTED.

    settlement() used to quantise a missing excess to "0.00", which slipped past
    the letter's own guard and printed "Less Excess -0.00" on a real Agreement of
    Loss. That figure sets the client's payout AND the 20% invoice to Veritas.
    """

    def test_settlement_reports_a_missing_excess_as_missing(self):
        self.assertIsNone(settlement("100000", None)["excess"])

    def test_a_supplied_zero_excess_is_still_a_real_zero(self):
        self.assertEqual(settlement("100000", "0")["excess"], "0.00")

    def test_the_letter_refuses_the_settlement_of_a_policy_with_no_excess(self):
        from claims_automation.letters import render_html
        ctx = {
            "insured_name": "Test Claimant",
            "claim_number": "DEMO-CLAIM-0002",
            "policy_number": "DEMO-POLICY-0002",
            "vehicle": "Demo vehicle",
            "date_of_loss": "2026-05-22",
            "excess_wording": "Less Excess as per policy schedule",
            "figures": settlement("100000", None),
        }
        with self.assertRaises(ValueError):
            render_html("aol", ctx)
