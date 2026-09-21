"""
claims/reconciliation/tests.py — worked examples for the month-end claims
reconciliation engine (claims/reconciliation/engine.py).

Pure Python engine, no ORM involved — plain unittest so these run with no DB.

Run:  python manage.py test claims.reconciliation
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from claims.reconciliation.engine import (
    ClaimPayment,
    degross_vat,
    money,
    reconcile_period,
    split_claim_ri,
)


class CoreFormulaTests(unittest.TestCase):
    """Incurred Claims (P&L) = Closing − Opening − Claims Paid (excl. VAT)."""

    def test_core_formula_no_claims(self):
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('1000000.00'),
            closing_claims_payable=Decimal('1200000.00'),
            claim_payments=[],
            vat_rate=Decimal('0.14'),
        )
        # Closing 1,200,000 - Opening 1,000,000 - Paid 0 = 200,000
        self.assertEqual(result.incurred_claims, Decimal('200000.00'))
        self.assertEqual(result.claims_paid_excl_vat, Decimal('0.00'))
        self.assertEqual(result.exceptions, [])

    def test_core_formula_with_a_payment(self):
        claim = ClaimPayment(
            claim_number='CLM-001', policy_number='COMG-1', treaty_name='FMRE MQS',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('70'), gqs_pct=Decimal('0'),
            mqs_pct=Decimal('30'), surplus_pct=Decimal('0'),
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('1000000.00'),
            closing_claims_payable=Decimal('1100000.00'),
            claim_payments=[claim],
            vat_rate=Decimal('0.14'),
        )
        # Payment excl VAT = 114000 / 1.14 = 100000.00 exactly
        self.assertEqual(result.claims_paid_excl_vat, Decimal('100000.00'))
        # 1,100,000 - 1,000,000 - 100,000 = 0
        self.assertEqual(result.incurred_claims, Decimal('0.00'))

    def test_formula_breaks_without_the_paid_term(self):
        """A test must fail without its fix: this proves the formula actually
        SUBTRACTS claims paid — if the engine forgot that term (a plausible
        off-by-one bug: closing - opening only), this test goes red. See the
        report for the before/after run."""
        claim = ClaimPayment(
            claim_number='CLM-002', policy_number='COMG-2', treaty_name='FMRE MQS',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('100'),
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('1000000.00'),
            closing_claims_payable=Decimal('1100000.00'),
            claim_payments=[claim],
            vat_rate=Decimal('0.14'),
        )
        # closing - opening (=100,000) would wrongly equal incurred if the
        # paid term were dropped; the correct answer nets it to zero.
        self.assertNotEqual(result.incurred_claims, Decimal('100000.00'))
        self.assertEqual(result.incurred_claims, Decimal('0.00'))


class VatDegrossingTests(unittest.TestCase):
    """VAT rate is a PARAMETER — proven by running it at two different rates."""

    def test_degross_at_14_percent(self):
        excl_vat, vat = degross_vat(Decimal('114000.00'), Decimal('0.14'))
        self.assertEqual(excl_vat, Decimal('100000.00'))
        self.assertEqual(vat, Decimal('14000.00'))

    def test_degross_at_a_different_rate_15_percent(self):
        """Same payment, a DIFFERENT rate, a DIFFERENT answer — proves the
        rate is a true parameter, not secretly 1.14 under the hood."""
        excl_vat, vat = degross_vat(Decimal('115000.00'), Decimal('0.15'))
        self.assertEqual(excl_vat, Decimal('100000.00'))
        self.assertEqual(vat, Decimal('15000.00'))
        # And 14% on the same gross gives a DIFFERENT excl-VAT figure.
        excl_vat_14, _ = degross_vat(Decimal('115000.00'), Decimal('0.14'))
        self.assertNotEqual(excl_vat_14, excl_vat)

    def test_half_up_rounding_at_a_boundary(self):
        """A figure whose exact division lands on .xx5 must round HALF UP,
        never HALF EVEN (Python's Decimal default) or truncated."""
        # 100.005 rounds to 100.01 under HALF_UP (Python default HALF_EVEN
        # would give 100.00 — this is what proves the rate is set explicitly).
        self.assertEqual(money(Decimal('100.005')), Decimal('100.01'))
        self.assertEqual(money(Decimal('100.015')), Decimal('100.02'))

    def test_half_up_rounding_fails_under_default_rounding(self):
        """Fail-without-fix proof: Decimal's own default (ROUND_HALF_EVEN)
        gives a DIFFERENT, wrong answer for the same boundary figure — so the
        explicit ROUND_HALF_UP in `money()` is genuinely doing something."""
        from decimal import ROUND_HALF_EVEN
        wrong = Decimal('100.005').quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
        self.assertEqual(wrong, Decimal('100.00'))  # banker's rounding: down
        self.assertNotEqual(wrong, money(Decimal('100.005')))  # engine: up


class ReinsuranceSplitTests(unittest.TestCase):
    """Retention + Total RI (GQS+MQS+Surplus) must equal 100% per claim."""

    def test_split_summing_to_100_is_valid(self):
        claim = ClaimPayment(
            claim_number='CLM-010', policy_number='COMG-10', treaty_name='Fire Surplus',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('40'), gqs_pct=Decimal('20'),
            mqs_pct=Decimal('10'), surplus_pct=Decimal('30'),
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0.14'))
        result = split_claim_ri(claim, excl_vat)
        self.assertTrue(result.is_valid_split)
        self.assertEqual(claim.total_ri_pct, Decimal('60.0000'))
        # Retention 40% + GQS 20% + MQS 10% + Surplus 30% of 100,000
        self.assertEqual(result.retention_amount, Decimal('40000.00'))
        self.assertEqual(result.total_ri_amount, Decimal('60000.00'))
        self.assertEqual(result.retention_amount + result.total_ri_amount,
                          result.payment_excl_vat)

    def test_split_not_summing_to_100_is_flagged(self):
        """A claim whose split is short (e.g. 40% Retention + 50% RI = 90%,
        a data error upstream) must be flagged, never silently absorbed."""
        claim = ClaimPayment(
            claim_number='CLM-011', policy_number='COMG-11', treaty_name='Fire Surplus',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('40'), gqs_pct=Decimal('20'),
            mqs_pct=Decimal('10'), surplus_pct=Decimal('20'),  # 90% total
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('0'),
            closing_claims_payable=Decimal('0'),
            claim_payments=[claim],
            vat_rate=Decimal('0.14'),
        )
        self.assertEqual(len(result.exceptions), 1)
        exc = result.exceptions[0]
        self.assertEqual(exc.claim_number, 'CLM-011')
        self.assertEqual(exc.split_pct_total, Decimal('90.0000'))

    def test_exceptions_do_not_flag_a_good_claim(self):
        """A batch with one bad and one good claim only flags the bad one —
        the good claim's exact 100% split must never spill into the
        exceptions list."""
        good = ClaimPayment(
            claim_number='CLM-012', policy_number='COMG-12', treaty_name='Fire Surplus',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('100'),
        )
        bad = ClaimPayment(
            claim_number='CLM-013', policy_number='COMG-13', treaty_name='Fire Surplus',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('50'),
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('0'),
            closing_claims_payable=Decimal('0'),
            claim_payments=[good, bad],
            vat_rate=Decimal('0.14'),
        )
        flagged_numbers = [e.claim_number for e in result.exceptions]
        self.assertIn('CLM-013', flagged_numbers)
        self.assertNotIn('CLM-012', flagged_numbers)

    def test_9999_percent_is_flagged(self):
        """99.99% is 0.01 percentage points short — a real gap (P100 on a
        P1m claim), not float noise, and must be flagged."""
        claim = ClaimPayment(
            claim_number='CLM-014', policy_number='COMG-14', treaty_name='Fire Surplus',
            payment_gross=Decimal('1140000.00'),
            retention_pct=Decimal('99.99'),
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('0'),
            closing_claims_payable=Decimal('0'),
            claim_payments=[claim],
            vat_rate=Decimal('0.14'),
        )
        flagged = [e.claim_number for e in result.exceptions]
        self.assertIn('CLM-014', flagged)

    def test_99_99995_percent_is_not_flagged(self):
        """99.99995% is genuine float/export noise at the 1e-5 level — within
        the one-quantum-unit (0.0001) tolerance — and must NOT be flagged."""
        claim = ClaimPayment(
            claim_number='CLM-015', policy_number='COMG-15', treaty_name='Fire Surplus',
            payment_gross=Decimal('1140000.00'),
            retention_pct=Decimal('99.99995'),
        )
        result = reconcile_period(
            period_label='FY26-Aug',
            opening_claims_payable=Decimal('0'),
            closing_claims_payable=Decimal('0'),
            claim_payments=[claim],
            vat_rate=Decimal('0.14'),
        )
        flagged = [e.claim_number for e in result.exceptions]
        self.assertNotIn('CLM-015', flagged)


class RoundingResidualTests(unittest.TestCase):
    """Finance outputs must balance — the four RI shares of a VALID split must
    always sum to exactly the (rounded) payment, never leak a thebe."""

    def test_thirds_split_balances_exactly(self):
        claim = ClaimPayment(
            claim_number='CLM-020', policy_number='COMG-20', treaty_name='Fire Surplus',
            payment_gross=Decimal('100.01'),
            retention_pct=Decimal('0'),
            gqs_pct=Decimal('33.3333'), mqs_pct=Decimal('33.3333'),
            surplus_pct=Decimal('33.3334'),
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0'))
        result = split_claim_ri(claim, excl_vat)
        self.assertTrue(result.is_valid_split)
        self.assertEqual(result.retention_amount + result.total_ri_amount,
                          result.payment_excl_vat)

    def test_tiny_gross_zero_retention_split_never_goes_negative(self):
        """gross 0.01, Retention 0%, GQS 50%/MQS 50%: each share rounds HALF
        UP to 0.01, so pro-rata retention (payment - RI) would be
        0.01 - 0.02 = -0.01. The largest-remainder method must instead give
        the -0.01 residual to GQS or MQS (tied largest, at 50% each), never
        to Retention, and no share may end up negative."""
        claim = ClaimPayment(
            claim_number='CLM-022', policy_number='COMG-22', treaty_name='Fire Surplus',
            payment_gross=Decimal('0.01'),
            retention_pct=Decimal('0'),
            gqs_pct=Decimal('50'), mqs_pct=Decimal('50'),
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0'))
        result = split_claim_ri(claim, excl_vat)
        self.assertTrue(result.is_valid_split)
        self.assertGreaterEqual(result.retention_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.gqs_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.mqs_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.surplus_amount, Decimal('0.00'))
        self.assertEqual(
            result.retention_amount + result.total_ri_amount,
            result.payment_excl_vat,
        )

    def test_two_shares_rounding_up_together_still_never_go_negative(self):
        """excl-VAT 0.02, four shares at 25% each: each share is 0.005, which
        would round HALF UP to 0.01 x4 = 0.04 against a 0.02 payment — a
        residual of -0.02 that no single 0.01 share can absorb (the case that
        broke the largest-remainder-on-top-of-HALF-UP approach). Flooring
        first must make this structurally impossible: no share negative, and
        the four still sum to the exact payment."""
        claim = ClaimPayment(
            claim_number='CLM-023', policy_number='COMG-23', treaty_name='Fire Surplus',
            payment_gross=Decimal('0.02'),
            retention_pct=Decimal('25'), gqs_pct=Decimal('25'),
            mqs_pct=Decimal('25'), surplus_pct=Decimal('25'),
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0'))
        result = split_claim_ri(claim, excl_vat)
        self.assertTrue(result.is_valid_split)
        self.assertGreaterEqual(result.retention_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.gqs_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.mqs_amount, Decimal('0.00'))
        self.assertGreaterEqual(result.surplus_amount, Decimal('0.00'))
        self.assertEqual(
            result.retention_amount + result.total_ri_amount,
            result.payment_excl_vat,
        )

    def test_tie_break_order_is_retention_gqs_mqs_surplus(self):
        """All four shares tied (25% each) on a payment with 2 leftover cents
        (0.02 excl-VAT): the fixed tie-break order (retention, gqs, mqs,
        surplus) must give the extra cents to retention then gqs, never
        drift to a different pair or order."""
        claim = ClaimPayment(
            claim_number='CLM-024', policy_number='COMG-24', treaty_name='Fire Surplus',
            payment_gross=Decimal('0.02'),
            retention_pct=Decimal('25'), gqs_pct=Decimal('25'),
            mqs_pct=Decimal('25'), surplus_pct=Decimal('25'),
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0'))
        result = split_claim_ri(claim, excl_vat)
        # Floors: 0.005 -> 0.00 each, 4 floors sum to 0.00, leftover = 2 cents.
        # Fixed tie order retention -> gqs -> mqs -> surplus gets the 2 cents.
        self.assertEqual(result.retention_amount, Decimal('0.01'))
        self.assertEqual(result.gqs_amount, Decimal('0.01'))
        self.assertEqual(result.mqs_amount, Decimal('0.00'))
        self.assertEqual(result.surplus_amount, Decimal('0.00'))

    def test_invalid_split_keeps_pro_rata_retention_so_the_gap_shows(self):
        """For an INVALID split, retention stays pro-rata (not plugged) so the
        real shortfall is still visible in the exception, not hidden by a
        balancing residual."""
        claim = ClaimPayment(
            claim_number='CLM-021', policy_number='COMG-21', treaty_name='Fire Surplus',
            payment_gross=Decimal('114000.00'),
            retention_pct=Decimal('40'), gqs_pct=Decimal('20'),
            mqs_pct=Decimal('10'), surplus_pct=Decimal('20'),  # 90% total
        )
        excl_vat, _ = degross_vat(claim.payment_gross, Decimal('0.14'))
        result = split_claim_ri(claim, excl_vat)
        self.assertFalse(result.is_valid_split)
        # Pro-rata retention (40% of 100,000 = 40,000) — NOT plugged to make
        # retention + RI equal the payment; the 10,000 gap must stay visible.
        self.assertEqual(result.retention_amount, Decimal('40000.00'))
        self.assertNotEqual(result.retention_amount + result.total_ri_amount,
                             result.payment_excl_vat)


class FloatRejectionTests(unittest.TestCase):
    """A float silently coerces to a near-miss Decimal (0.14 -> 0.1400...133);
    reject it outright instead."""

    def test_degross_vat_rejects_float_rate(self):
        with self.assertRaises(TypeError):
            degross_vat(Decimal('114000.00'), 0.14)

    def test_degross_vat_rejects_float_gross(self):
        with self.assertRaises(TypeError):
            degross_vat(114000.00, Decimal('0.14'))

    def test_reconcile_period_rejects_float_vat_rate(self):
        with self.assertRaises(TypeError):
            reconcile_period(
                period_label='FY26-Aug',
                opening_claims_payable=Decimal('0'),
                closing_claims_payable=Decimal('0'),
                claim_payments=[],
                vat_rate=0.14,
            )


if __name__ == '__main__':
    unittest.main()
