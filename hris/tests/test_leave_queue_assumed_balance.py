"""The approver queue must say when the balance behind a request is a GUESS.

CFO 2026-09-07. With no hire date on the employee's record the accrual engine
falls back to 1 January and credits a full year to date — 14.00 annual days on
7 September, whoever you are and however recently you joined. 58 active
employees read exactly that figure.

`leave_encash_service.apply_encashment` already refuses to turn such a balance
into a payment. Ordinary leave can still be BOOKED against it, and the approver
is the only check left until HR loads the start date, so the queue row has to
say so rather than present the number as fact.

The flag is deliberately NOT raised for a profile carrying an HR-uploaded annual
opening balance: that figure is HR's own, and the engine accrues forward from its
as-at date, which post-dates the hire. Flagging those would cry wolf on the very
rows that are sound.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from core.models import Company
from hris.models import (HRISProfile, LeaveOpeningBalance, LeaveRequest,
                         LeaveType)
from payroll.models import Employee

QUEUE_URL = '/hris/api/leave-requests/queue/'


class AssumedBalanceFlagTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TASB', name='Assumed Balance Co (test)')
        cls.annual, _ = LeaveType.objects.get_or_create(
            code='annual',
            defaults={'name': 'Annual Leave', 'default_annual_days': 21})

        cls.mgr_user, cls.mgr = cls._staff('asb_mgr', 'The Manager', None)
        HRISProfile.objects.create(employee=cls.mgr)

    @classmethod
    def _staff(cls, username, name, hire_date):
        user = User.objects.create_user(username, f'{username}@test.example', 'x')
        emp = Employee.objects.create(
            employee_number=username.upper(), full_name=name, company=cls.co,
            email=user.email, user=user, status='active', hire_date=hire_date)
        return user, emp

    def _request_from(self, username, name, hire_date, with_opening=False):
        user, emp = self._staff(username, name, hire_date)
        profile = HRISProfile.objects.create(employee=emp, manager=self.mgr)
        if with_opening:
            LeaveOpeningBalance.objects.create(
                profile=profile, leave_type_code='annual',
                entitlement_days=Decimal('21'), opening_balance_days=Decimal('5'),
                accrued_days=Decimal('5'), as_at_date=dt.date(2026, 6, 30))
        return LeaveRequest.objects.create(
            profile=profile, leave_type=self.annual,
            start_date=dt.date(2026, 10, 1), end_date=dt.date(2026, 10, 1), days=1,
            status=LeaveRequest.Status.PENDING, requested_approver=self.mgr_user,
            reason='One day of annual leave for a personal errand.')

    def _rows(self):
        self.client.force_authenticate(self.mgr_user)
        resp = self.client.get(QUEUE_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_no_hire_date_is_flagged(self):
        """The Amantle case. FAILS before the flag existed — the row carried the
        14.00-day balance with nothing to say it was counted from 1 January."""
        lr = self._request_from('asb_nodate', 'No Start Date', None)
        row = next(r for r in self._rows()['pending'] if r['id'] == str(lr.id))
        self.assertTrue(row['balance_is_assumed'])

    def test_known_hire_date_is_not_flagged(self):
        lr = self._request_from('asb_dated', 'Has Start Date', dt.date(2024, 3, 1))
        row = next(r for r in self._rows()['pending'] if r['id'] == str(lr.id))
        self.assertFalse(row['balance_is_assumed'])

    def test_hr_uploaded_opening_is_not_flagged_even_with_no_hire_date(self):
        """Do not cry wolf: HR stated this balance as at 30 June, and the engine
        accrues forward from that date, so the figure is sound."""
        lr = self._request_from('asb_opening', 'Uploaded Opening', None,
                                with_opening=True)
        row = next(r for r in self._rows()['pending'] if r['id'] == str(lr.id))
        self.assertFalse(row['balance_is_assumed'])

    def test_the_queue_counts_how_many_are_assumed(self):
        self._request_from('asb_c1', 'Guess One', None)
        self._request_from('asb_c2', 'Guess Two', None)
        self._request_from('asb_c3', 'Solid One', dt.date(2023, 1, 9))
        self.assertEqual(self._rows()['assumed_balances'], 2)
