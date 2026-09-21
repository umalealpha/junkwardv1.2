"""Half-day leave tests (Kago Tshutlhedi feature request 2026-07-13).

Locks the calculation rules from the spec:
  * half day = 0.5, deducted per half boundary
  * 13/07 (PM half) → 15/07 (full) = 2.5 days   (the email's worked example)
  * single-day half application = 0.5
  * weekend/public-holiday exclusion happens BEFORE the half-day adjustment
  * a half-day that lands on a non-working boundary deducts nothing
  * maternity (calendar-day entitlement) ignores the half-day flags
  * a PM-start on the same date as an AM-end is a zero-length request (blocked)

Dates used are real 2026 weekdays: 13 Jul = Mon, 15 Jul = Wed, 17 Jul = Fri,
18 Jul = Sat, 20 Jul = Mon.
"""
import datetime as _dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from hris.models import LeaveRequest, LeaveType, PublicHoliday

D = _dt.date
FULL, AM, PM = 'full', 'am', 'pm'


def _lr(start, end, sdt=FULL, edt=FULL, leave_type=None):
    return LeaveRequest(
        start_date=start, end_date=end,
        start_day_type=sdt, end_day_type=edt, leave_type=leave_type,
    )


class HalfDayComputeTest(TestCase):
    def test_single_full_day(self):
        self.assertEqual(_lr(D(2026, 7, 13), D(2026, 7, 13)).compute_days(), Decimal('1'))

    def test_single_half_day_am(self):
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 13), sdt=AM).compute_days(), Decimal('0.5'))

    def test_single_half_day_pm(self):
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 13), sdt=PM).compute_days(), Decimal('0.5'))

    def test_range_full(self):
        # Mon..Wed = 3 working days
        self.assertEqual(_lr(D(2026, 7, 13), D(2026, 7, 15)).compute_days(), Decimal('3'))

    def test_email_example_pm_start(self):
        # 13/07 (PM half) → 15/07 (full) = 2.5
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 15), sdt=PM).compute_days(), Decimal('2.5'))

    def test_range_half_both_ends(self):
        # Mon(PM) .. Wed(AM) = 3 - 0.5 - 0.5 = 2.0
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 15), sdt=PM, edt=AM).compute_days(), Decimal('2'))

    def test_weekend_excluded_before_half(self):
        # Mon 13 (PM half) .. Fri 17 (full): 5 working days - 0.5 = 4.5
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 17), sdt=PM).compute_days(), Decimal('4.5'))

    def test_sat_pm_boundary_deducts_nothing(self):
        # end on Sat 18 marked PM half: Mon..Fri = 5, the Saturday AFTERNOON is
        # never worked so a PM boundary there adds nothing (Sat morning uncharged
        # because only the afternoon was booked off). Total stays 5.
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 18), edt=PM).compute_days(), Decimal('5'))

    # --- Saturday = half a working day (CFO directive 2026-08-12) -------------
    def test_single_saturday_am_is_half(self):
        # The reported case: a Sat-morning half-day must charge 0.5, not 0.
        # Sat 18 Jul 2026.
        self.assertEqual(
            _lr(D(2026, 7, 18), D(2026, 7, 18), sdt=AM).compute_days(), Decimal('0.5'))

    def test_single_saturday_full_is_half(self):
        # A whole Saturday off = only the worked morning = 0.5.
        self.assertEqual(
            _lr(D(2026, 7, 18), D(2026, 7, 18)).compute_days(), Decimal('0.5'))

    def test_single_saturday_pm_is_zero(self):
        # Saturday afternoon is never worked → 0.
        self.assertEqual(
            _lr(D(2026, 7, 18), D(2026, 7, 18), sdt=PM).compute_days(), Decimal('0'))

    def test_single_sunday_is_zero(self):
        # Sun 19 Jul 2026 — still fully non-working.
        self.assertEqual(
            _lr(D(2026, 7, 19), D(2026, 7, 19)).compute_days(), Decimal('0'))

    def test_range_spanning_saturday_counts_half(self):
        # Fri 10 (full) .. Mon 13 (full): Fri 1 + Sat 0.5 + Sun 0 + Mon 1 = 2.5.
        # (Avoids 20/21 Jul — Botswana President's Day public holidays.)
        self.assertEqual(
            _lr(D(2026, 7, 10), D(2026, 7, 13)).compute_days(), Decimal('2.5'))

    def test_public_holiday_excluded_before_half(self):
        PublicHoliday.objects.create(
            country_code='BW', holiday_date=D(2026, 7, 14), name='Test Holiday')
        # Mon 13 (PM) .. Wed 15: Tue is a holiday → working days 13,15 = 2; -0.5 = 1.5
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 15), sdt=PM).compute_days(), Decimal('1.5'))

    def test_maternity_ignores_half_flags(self):
        mat = LeaveType.objects.create(code='maternity', name='Maternity Leave',
                                       default_annual_days=98)
        # Calendar-day count 13..15 inclusive = 3, half flag ignored.
        self.assertEqual(
            _lr(D(2026, 7, 13), D(2026, 7, 15), sdt=AM, leave_type=mat).compute_days(),
            Decimal('3'))


class HalfDayValidationTest(TestCase):
    def test_zero_length_pm_start_am_end_same_day_blocked(self):
        lr = _lr(D(2026, 7, 13), D(2026, 7, 13), sdt=PM, edt=AM)
        with self.assertRaises(ValidationError):
            lr.clean()

    def test_full_same_day_is_valid(self):
        # No raise — a normal single full day.
        _lr(D(2026, 7, 13), D(2026, 7, 13)).clean()

    def test_breakdown_string(self):
        lr = _lr(D(2026, 7, 13), D(2026, 7, 15), sdt=PM)
        lr.days = lr.compute_days()
        s = lr.day_breakdown()
        self.assertIn('2.5 days', s)
        self.assertIn('PM', s)
