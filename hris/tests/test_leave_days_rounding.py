"""Leave-day figures are never rounded UP (EXCO change request, 2026-08-11).

A 21-day entitlement earns 1.75 days a month. Every screen showed 1.8, so the
report credited a fraction of a day nobody had worked for. These assertions fail
on the pre-change code (`round(x, 1)`) — 1.75 → 1.8 — and pass on `days_out`.
"""
from __future__ import annotations

import datetime as _dt

from django.test import SimpleTestCase

from hris.leave_balance import (COS_ANNUAL_ENTITLEMENTS, accrued_to_date,
                                days_out)


class DaysOutTests(SimpleTestCase):
    def test_does_not_round_up_a_half(self):
        # The exact case EXCO raised: 21 / 12 = 1.75, shown as 1.8.
        self.assertEqual(days_out(1.75), 1.75)

    def test_truncates_rather_than_rounding_up(self):
        self.assertEqual(days_out(2.0833333), 2.08)
        self.assertEqual(days_out(1.999), 1.99)

    def test_keeps_two_decimals_not_one(self):
        # Truncating to ONE decimal would show 1.7 — further from 1.75 than the
        # 1.8 being corrected. Two decimals is the point of the change.
        self.assertNotEqual(days_out(1.75), 1.7)

    def test_exact_values_are_unchanged(self):
        self.assertEqual(days_out(1.5), 1.5)
        self.assertEqual(days_out(0.0), 0.0)
        self.assertEqual(days_out(18.0), 18.0)

    def test_a_negative_balance_is_not_nudged_upwards(self):
        # An over-taken position must not drift toward the employee: floor, not
        # truncate-toward-zero.
        self.assertEqual(days_out(-1.755), -1.76)


class AccruedToDateTests(SimpleTestCase):
    def test_twenty_one_day_entitlement_accrues_one_seventy_five(self):
        # One completed month (January), calendar leave year.
        one_month = _dt.date(2026, 1, 31)
        self.assertEqual(accrued_to_date(21.0, one_month), 1.75)

    def test_eighteen_day_entitlement_accrues_one_point_five(self):
        self.assertEqual(accrued_to_date(18.0, _dt.date(2026, 1, 31)), 1.5)

    def test_twenty_five_day_entitlement_accrues_two_point_zero_eight(self):
        # Was 2.1 — the figure a manager was shown for a month worth 2.0833.
        self.assertEqual(accrued_to_date(25.0, _dt.date(2026, 1, 31)), 2.08)

    def test_accrual_never_exceeds_the_entitlement_over_a_full_year(self):
        for ent in COS_ANNUAL_ENTITLEMENTS:
            earned = accrued_to_date(ent, _dt.date(2026, 12, 31))
            self.assertLessEqual(earned, ent, f'{ent} over-credited: {earned}')


class GateMessageTests(SimpleTestCase):
    """The refusal message must not round the figure it is enforcing."""

    def test_available_is_not_rounded_up_in_the_message(self):
        # 25-day entitlement, 7 completed months → 14.58, not "14.6".
        available = accrued_to_date(25.0, _dt.date(2026, 8, 1))
        self.assertEqual(f'{float(available):g}', '14.58')

    def test_a_whole_day_prints_without_a_decimal(self):
        self.assertEqual(f'{float(days_out(3.0)):g}', '3')
