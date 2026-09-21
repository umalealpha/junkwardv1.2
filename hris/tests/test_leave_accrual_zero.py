"""The falsy-zero accrual defect (Oprah Mogomotsi, ref 3c45e603; CFO 2026-08-08).

`accrued = float(ob.accrued_days) or entitlement` handed anyone with nothing
accrued their WHOLE annual entitlement. The fields are not nullable — they
default to 0 — so that fallback never guarded missing data; it only ever fired
on a real zero.

The trap in fixing it: 413 of the 416 uploaded rows carrying a zero are sick /
study / compassionate / special, which do NOT accrue and are supposed to show
their full entitlement. Zeroing those would have been worse than the bug. Only
annual accrues.
"""
import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from hris.leave_balance import balances_for_profile
from hris.models import HRISProfile, LeaveOpeningBalance, LeaveType
from payroll.models import Employee


class AccrualZeroTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ACZ', name='ADIC (accrual test)')
        cls.user = User.objects.create_user('aczstaff', 'aczstaff@alphadirect.co.bw', 'x')
        cls.emp = Employee.objects.create(
            company=cls.co, employee_number='ACZ-1', full_name='Zero Accrual',
            email='aczstaff@alphadirect.co.bw', user=cls.user)
        cls.prof = HRISProfile.objects.create(employee=cls.emp)
        for code, name in [('annual', 'Annual leave'), ('sick', 'Sick leave'),
                           ('study', 'Study leave')]:
            LeaveType.objects.get_or_create(code=code, defaults={'name': name})

    def _opening(self, code, *, entitlement, accrued, opening):
        # as-at TODAY, not 30 days ago. The annual accrual engine adds one
        # twelfth per whole month completed SINCE the as-at date, so an opening
        # dated 30 days back leaked ~1 month of accrual into the figure — 0.00
        # read as 1.5 at a month-end, and this test drifted with the calendar.
        # As-at today means "no month completed since upload", so the displayed
        # accrued equals the stored one, which is exactly what these tests check.
        return LeaveOpeningBalance.objects.create(
            profile=self.prof, leave_type_code=code,
            entitlement_days=Decimal(entitlement),
            accrued_days=Decimal(accrued),
            opening_balance_days=Decimal(opening),
            as_at_date=timezone.localdate())

    def _row(self, code):
        for b in balances_for_profile(self.prof):
            if b['code'] == code:
                return b
        return None

    # ── the defect ──────────────────────────────────────────────────────────
    def test_zero_accrued_annual_leave_no_longer_shows_the_whole_year(self):
        self._opening('annual', entitlement='18.00', accrued='0.00', opening='0.00')
        row = self._row('annual')
        self.assertIsNotNone(row)
        self.assertEqual(row['accrued'], 0.0,
                         'nothing accrued must read as nothing, not a full year')
        self.assertNotEqual(row['accrued'], 18.0)

    def test_a_real_annual_accrual_is_untouched(self):
        self._opening('annual', entitlement='18.00', accrued='1.50', opening='1.50')
        self.assertEqual(self._row('annual')['accrued'], 1.5)

    # ── the clock: Botswana date, not the UTC date ──────────────────────────
    def test_opening_uploaded_after_midnight_botswana_is_not_ignored_until_2am(self):
        """Between 00:00 and 02:00 Botswana the UTC date is still yesterday.
        The engine compared as_at_date (a Botswana date) with the UTC date, so a
        balance dated today was invisible for two hours — and 38 HR tests went
        red on any branch built in that window (CI 31 Aug 22:02 UTC)."""
        one_thirty_am_gaborone = dt.datetime(2026, 9, 2, 23, 30, tzinfo=dt.timezone.utc)
        with patch('django.utils.timezone.now', return_value=one_thirty_am_gaborone):
            self.assertEqual(timezone.localdate(), dt.date(2026, 9, 3))
            self._opening('annual', entitlement='18.00', accrued='1.50', opening='1.50')
            self.assertEqual(self._row('annual')['accrued'], 1.5,
                             'an opening balance as at today must count from midnight '
                             'Botswana time, not from 02:00 when the UTC date catches up')

    # ── the trap: do NOT zero the types that never accrue ───────────────────
    def test_sick_leave_still_shows_its_full_entitlement(self):
        """Sick leave is granted in full, not accrued. 136 uploaded rows carry a
        zero here and must keep showing 20 days."""
        self._opening('sick', entitlement='20.00', accrued='0.00', opening='19.00')
        self.assertEqual(self._row('sick')['accrued'], 20.0)

    def test_study_leave_still_shows_its_full_entitlement(self):
        self._opening('study', entitlement='10.00', accrued='0.00', opening='7.00')
        self.assertEqual(self._row('study')['accrued'], 10.0)

    # ── entitlement fallback ────────────────────────────────────────────────
    def test_a_blank_entitlement_still_falls_back_to_the_policy_default(self):
        """Nobody has an entitlement of nothing — a stored 0 means the upload
        column was blank, so the policy figure still stands in."""
        self._opening('annual', entitlement='0.00', accrued='2.00', opening='2.00')
        self.assertGreater(self._row('annual')['days'], 0,
                           'a blank entitlement must not become zero days')
