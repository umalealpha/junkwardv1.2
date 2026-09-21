"""payroll/tests/test_paye_rounding.py — PAYE rounds HALF UP, never HALF EVEN.

House rule (CFO 2026-08-09): VAT and PAYE round HALF UP. Rounding is a TAX
decision, never a language default.

Bug proven 2026-09-20: every `.quantize(Decimal('0.01'))` in payroll/paye.py
was called with no `rounding=` argument, so Python's default ROUND_HALF_EVEN
applied and nothing in the repo sets a decimal context. On the live BURS
bands an exact half-thebe always landed DOWN — one thebe, every time, always
under-collected:

    monthly 7,000.04  -> 150.00 shipped vs 150.01 correct
    monthly 11,000.24 -> 712.54 shipped vs 712.55 correct

payroll/loan_service._q already does this correctly (ROUND_HALF_UP); this is
the same rule applied to the tax engine.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from payroll.paye import calculate_annual_paye, calculate_monthly_paye

D = Decimal

# Live BURS resident-individual schedule: (lower, upper, base, rate_pct).
BURS = [
    (D('0'),        D('48000'),  D('0'),     D('0')),
    (D('48000'),    D('84000'),  D('0'),     D('5')),
    (D('84000'),    D('120000'), D('1800'),  D('12.5')),
    (D('120000'),   D('156000'), D('6300'),  D('18.75')),
    (D('156000'),   None,        D('13050'), D('25')),
]


class PayeRoundsHalfUpTests(SimpleTestCase):
    def test_monthly_half_thebe_rounds_up(self):
        # 7,000.04 x 12 = 84,000.48 -> 1,800 + 12.5% x 0.48 = 1,800.06
        # 1,800.06 / 12 = 150.005 exactly. HALF_EVEN gives 150.00.
        self.assertEqual(calculate_monthly_paye(D('7000.04'), BURS), D('150.01'))

    def test_monthly_half_thebe_rounds_up_in_the_18_75_band(self):
        # 11,000.24 x 12 = 132,002.88 -> 6,300 + 18.75% x 12,002.88 = 8,550.54
        # 8,550.54 / 12 = 712.545 exactly. HALF_EVEN gives 712.54.
        self.assertEqual(calculate_monthly_paye(D('11000.24'), BURS), D('712.55'))

    def test_annual_half_thebe_rounds_up(self):
        # 84,000.04 -> 1,800 + 12.5% x 0.04 = 1,800.005. HALF_EVEN gives 1,800.00.
        self.assertEqual(calculate_annual_paye(D('84000.04'), BURS), D('1800.01'))

    def test_ordinary_figures_are_unchanged(self):
        # The rule must only move the exact-half cases.
        self.assertEqual(calculate_annual_paye(D('100000'), BURS), D('3800.00'))
        self.assertEqual(calculate_monthly_paye(D('4000'), BURS), D('0.00'))
