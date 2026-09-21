"""
Self-cancel of a pending leave request (Kago Tshutlhedi feature request,
2026-07-16). Covers: owner cancels pending -> CANCELLED; approved can't be
self-cancelled; a non-owner can't cancel; the re-check (can't cancel twice);
half-day cancels; the can_cancel flag; and the race outcome (deciding an
already-cancelled request is rejected).
"""
import datetime as _dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee

D = _dt.date


class LeaveCancelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.lt = LeaveType.objects.create(code='annual', name='Annual Leave')

        cls.owner = User.objects.create_user('alice', email='alice@ad.co.bw', password='x')
        cls.owner_emp = Employee.objects.create(employee_number='E1', full_name='Alice M',
                                                company=cls.company, user=cls.owner)
        cls.owner_hp = HRISProfile.objects.create(employee=cls.owner_emp)

        cls.other = User.objects.create_user('bob', email='bob@ad.co.bw', password='x')
        cls.other_emp = Employee.objects.create(employee_number='E2', full_name='Bob N',
                                                company=cls.company, user=cls.other)
        cls.other_hp = HRISProfile.objects.create(employee=cls.other_emp)

    def _lr(self, status=LeaveRequest.Status.PENDING, profile=None, sdt='full'):
        return LeaveRequest.objects.create(
            profile=profile or self.owner_hp, leave_type=self.lt,
            start_date=D(2026, 8, 3), end_date=D(2026, 8, 5),
            days=Decimal('3'), start_day_type=sdt, status=status)

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def _cancel(self, user, lr):
        return self._client(user).post(f'/hris/api/leave-requests/{lr.id}/cancel/', {}, format='json')

    def test_owner_cancels_pending(self):
        lr = self._lr()
        r = self._cancel(self.owner, lr)
        self.assertEqual(r.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.CANCELLED)

    def test_cannot_cancel_approved(self):
        lr = self._lr(status=LeaveRequest.Status.APPROVED)
        r = self._cancel(self.owner, lr)
        self.assertEqual(r.status_code, 409)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_non_owner_cannot_cancel(self):
        lr = self._lr()                       # belongs to owner
        r = self._cancel(self.other, lr)      # bob tries
        self.assertEqual(r.status_code, 404)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)

    def test_cannot_cancel_twice(self):
        lr = self._lr()
        self.assertEqual(self._cancel(self.owner, lr).status_code, 200)
        self.assertEqual(self._cancel(self.owner, lr).status_code, 409)   # re-check guard

    def test_half_day_request_cancels(self):
        lr = self._lr(sdt='pm')
        r = self._cancel(self.owner, lr)
        self.assertEqual(r.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.CANCELLED)

    def test_mine_exposes_can_cancel(self):
        self._lr()                                        # pending
        self._lr(status=LeaveRequest.Status.APPROVED)     # approved
        r = self._client(self.owner).get('/hris/api/leave-requests/mine/')
        self.assertEqual(r.status_code, 200)
        flags = {row['status']: row['can_cancel'] for row in r.json()['requests']}
        self.assertTrue(flags['pending'])
        self.assertFalse(flags['approved'])

    def test_decide_on_cancelled_is_rejected(self):
        # Race outcome: employee cancels first, approver's decide then loses.
        boss = User.objects.create_superuser('root', 'root@ad.co.bw', 'x')
        lr = self._lr()
        self.assertEqual(self._cancel(self.owner, lr).status_code, 200)
        r = self._client(boss).post(f'/hris/api/leave-requests/{lr.id}/decide/',
                                    {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.CANCELLED)
