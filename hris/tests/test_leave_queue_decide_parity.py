"""H50 — the leave LIST must show exactly what the caller can DECIDE.

Bug (flagged 2026-08-21, fixed 2026-08-22): leave_queue's manager branch only
matched when requested_approver IS NULL (and matched the manager by user link),
while decide_leave let the employee's manager act regardless of who was picked
as approver (resolving the manager by user OR email link). So a line manager
could approve — via a direct link or the notification email — a request that
never appeared in their own queue.

These tests lock the two predicates together: a request routed to another
approver, for an employee whose manager is the caller, must appear in the
caller's queue AND be decidable by them; an unrelated manager must see neither.
"""
import datetime as dt

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from core.models import Company
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee

QUEUE_URL = '/hris/api/leave-requests/queue/'


def _staff(username, name, company):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x')
    emp = Employee.objects.create(
        employee_number=username.upper(), full_name=name,
        company=company, email=user.email, user=user)
    return user, emp


class LeaveQueueDecideParityTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TPAR', name='Parity Co (test)')

        # The employee's real line manager (has a direct report → role 'mgr').
        cls.mgr_user, cls.mgr = _staff('par_mgr', 'Real Manager', cls.co)
        HRISProfile.objects.create(employee=cls.mgr)

        # A DIFFERENT person who was picked as the request's approver.
        cls.other_user, cls.other = _staff('par_other', 'Picked Approver', cls.co)
        HRISProfile.objects.create(employee=cls.other)

        # An unrelated manager (own report, so also role 'mgr') who should see
        # nothing of this request — guards that the fix does not over-widen.
        cls.mgr2_user, cls.mgr2 = _staff('par_mgr2', 'Other Manager', cls.co)
        HRISProfile.objects.create(employee=cls.mgr2)
        _, cls.mgr2_report = _staff('par_mgr2_rep', 'Other Report', cls.co)
        HRISProfile.objects.create(employee=cls.mgr2_report, manager=cls.mgr2)

        # The employee whose manager is mgr, whose leave was routed to `other`.
        cls.staff_user, cls.staff = _staff('par_staff', 'The Employee', cls.co)
        cls.staff_profile = HRISProfile.objects.create(
            employee=cls.staff, manager=cls.mgr)

        cls.annual, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 22})

    def _new_request(self):
        return LeaveRequest.objects.create(
            profile=self.staff_profile, leave_type=self.annual,
            start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 1), days=1,
            status=LeaveRequest.Status.PENDING, requested_approver=self.other_user,
            reason='Personal leave routed to another approver for this parity test case.')

    def test_manager_sees_request_routed_to_another_approver(self):
        """The core H50 assertion — FAILS on the old requested_approver IS NULL
        filter: the employee's manager must see the pending request in their own
        queue even though someone else was picked as approver."""
        lr = self._new_request()
        self.client.force_authenticate(self.mgr_user)
        resp = self.client.get(QUEUE_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row['id'] for row in resp.json()['pending']}
        self.assertIn(str(lr.id), ids)

    def test_manager_can_decide_that_same_request(self):
        """Proves the list now equals the decide authority: the request the
        manager sees is one they can actually approve."""
        lr = self._new_request()
        self.client.force_authenticate(self.mgr_user)
        resp = self.client.post(f'/hris/api/leave-requests/{lr.id}/decide/',
                                {'decision': 'approve'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_unrelated_manager_sees_neither_and_cannot_decide(self):
        """The fix is bounded: a manager who is neither the picked approver nor
        the employee's manager still sees nothing and is refused at decide."""
        lr = self._new_request()
        self.client.force_authenticate(self.mgr2_user)
        resp = self.client.get(QUEUE_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row['id'] for row in resp.json()['pending']}
        self.assertNotIn(str(lr.id), ids)
        decide = self.client.post(f'/hris/api/leave-requests/{lr.id}/decide/',
                                  {'decision': 'approve'}, format='json')
        self.assertEqual(decide.status_code, 403, decide.content)
