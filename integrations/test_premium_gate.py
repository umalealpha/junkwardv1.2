"""
Objective check for the Motor-Claims "Gate 0" premium-status classifier
(integrations/premium_gate.py).

Encodes Kago Tshutlhedi's approved thresholds (memo v2, 6-Sep-2026), which apply
General Condition 3.B (Continuation of cover — debit order): cover is deemed
cancelled at the END of the last paid period if premium is not received by due
date. Model assumption (deterministic, no AI): one SUCCESSFUL debit buys
``period_days`` of cover (monthly debit order => 30 days by default).

  GREEN  premium for the period of loss was received.
  AMBER  current debit failed / not received, 1..30 days overdue at the loss date.
  RED    more than 30 days overdue, OR the loss falls in an unpaid period.

Pure function — no Django, no DB. Uses ``unittest.TestCase`` so Django's
``manage.py test`` runner (CI) discovers and runs it.
"""
import unittest
from datetime import date
from decimal import Decimal

from integrations.premium_gate import classify_premium_gate


def _d(s):
    return date.fromisoformat(s)


def _deb(due, status, amount="500.00"):
    return {"due_date": _d(due), "status": status, "amount": Decimal(amount)}


class PremiumGateTests(unittest.TestCase):

    # ---- shape ----------------------------------------------------------

    def test_returns_expected_keys_and_types(self):
        r = classify_premium_gate(_d("2026-08-10"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertGreaterEqual(set(r), {"level", "code", "days_overdue", "cover_paid_to", "label"})
        self.assertIn(r["level"], {"green", "amber", "red"})
        self.assertTrue(isinstance(r["label"], str) and r["label"].strip())

    # ---- GREEN: premium for the period of loss received -----------------

    def test_green_when_loss_within_cover(self):
        r = classify_premium_gate(_d("2026-08-10"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertEqual(r["level"], "green")
        self.assertEqual(r["code"], "premium_paid")
        self.assertEqual(r["days_overdue"], 0)
        self.assertEqual(r["cover_paid_to"], _d("2026-08-31"))

    def test_green_at_exact_cover_end(self):
        r = classify_premium_gate(_d("2026-08-31"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertEqual(r["level"], "green")

    def test_future_debits_are_ignored_but_still_green(self):
        debits = [_deb("2026-08-01", "SUCCESSFUL"), _deb("2026-09-01", "SUCCESSFUL")]
        r = classify_premium_gate(_d("2026-08-10"), debits)
        self.assertEqual(r["level"], "green")
        self.assertEqual(r["cover_paid_to"], _d("2026-08-31"))

    # ---- AMBER: 1..30 days overdue --------------------------------------

    def test_amber_one_day_over(self):
        r = classify_premium_gate(_d("2026-09-01"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertEqual(r["level"], "amber")
        self.assertEqual(r["code"], "premium_arrears")
        self.assertEqual(r["days_overdue"], 1)
        self.assertEqual(r["cover_paid_to"], _d("2026-08-31"))

    def test_amber_thirty_days_over_is_still_amber(self):
        r = classify_premium_gate(_d("2026-09-30"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertEqual(r["level"], "amber")
        self.assertEqual(r["days_overdue"], 30)

    def test_amber_current_debit_failed_after_earlier_paid(self):
        debits = [_deb("2026-08-01", "SUCCESSFUL"), _deb("2026-09-01", "FAILED")]
        r = classify_premium_gate(_d("2026-09-15"), debits)
        self.assertEqual(r["level"], "amber")
        self.assertEqual(r["days_overdue"], 15)

    # ---- RED: >30 days overdue, or loss in an unpaid period -------------

    def test_red_thirty_one_days_over(self):
        r = classify_premium_gate(_d("2026-10-01"), [_deb("2026-08-01", "SUCCESSFUL")])
        self.assertEqual(r["level"], "red")
        self.assertEqual(r["code"], "premium_lapsed")
        self.assertEqual(r["days_overdue"], 31)
        self.assertEqual(r["cover_paid_to"], _d("2026-08-31"))

    def test_red_when_no_successful_debit_ever(self):
        r = classify_premium_gate(_d("2026-08-10"), [_deb("2026-08-01", "FAILED")])
        self.assertEqual(r["level"], "red")
        self.assertEqual(r["code"], "premium_unpaid")
        self.assertIsNone(r["cover_paid_to"])
        self.assertIsNone(r["days_overdue"])

    def test_red_when_no_debits_at_all(self):
        r = classify_premium_gate(_d("2026-08-10"), [])
        self.assertEqual(r["level"], "red")
        self.assertEqual(r["code"], "premium_unpaid")

    # ---- robustness -----------------------------------------------------

    def test_unsorted_input_gives_same_answer(self):
        debits = [_deb("2026-09-01", "FAILED"), _deb("2026-08-01", "SUCCESSFUL")]
        r = classify_premium_gate(_d("2026-09-15"), debits)
        self.assertEqual(r["level"], "amber")
        self.assertEqual(r["days_overdue"], 15)

    def test_status_is_case_insensitive(self):
        r = classify_premium_gate(_d("2026-08-10"), [_deb("2026-08-01", "successful")])
        self.assertEqual(r["level"], "green")

    def test_annual_period_days_parameter(self):
        r = classify_premium_gate(_d("2027-01-01"),
                                  [_deb("2026-06-01", "SUCCESSFUL")], period_days=365)
        self.assertEqual(r["level"], "green")
