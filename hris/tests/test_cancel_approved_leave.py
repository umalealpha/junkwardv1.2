"""Cancelling APPROVED leave before it starts (Oprah Mogomotsi; CFO 2026-08-07).

The balance frees itself — it counts only APPROVED/PENDING. Attendance does NOT:
approving stamped those days "on leave", so cancelling has to put them back or
the workforce brief stops chasing hours the person now owes.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, UserCompanyAccess
from hris.models import HRISProfile, LeaveRequest, LeaveType, WorkdayJustification
from payroll.models import Employee

MINE = '/hris/api/leave-requests/mine/'


def _cancel(pk):
    return f'/hris/api/leave-requests/{pk}/cancel/'


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CAL', name='ADIC (cancel test)')
        cls.staff = User.objects.create_user(
            'calstaff', 'calstaff@alphadirect.co.bw', 'x',
            first_name='Otto', last_name='Staff')
        cls.mgr = User.objects.create_user(
            'calmgr', 'calmgr@alphadirect.co.bw', 'x', first_name='Mo', last_name='Manager')
        cls.emp = Employee.objects.create(
            company=cls.co, employee_number='CAL-1', full_name='Otto Staff',
            email='calstaff@alphadirect.co.bw', user=cls.staff)
        cls.mgr_emp = Employee.objects.create(
            company=cls.co, employee_number='CAL-2', full_name='Mo Manager',
            email='calmgr@alphadirect.co.bw', user=cls.mgr)
        cls.prof = HRISProfile.objects.create(employee=cls.emp, manager=cls.mgr_emp)
        for u in (cls.staff, cls.mgr):
            UserCompanyAccess.objects.get_or_create(user=u, company=cls.co)
        cls.annual = LeaveType.objects.create(code='annual', name='Annual leave')

    def leave(self, *, status, starts_in_days=7, days=2):
        start = timezone.localdate() + dt.timedelta(days=starts_in_days)
        return LeaveRequest.objects.create(
            profile=self.prof, leave_type=self.annual,
            start_date=start, end_date=start + dt.timedelta(days=days - 1),
            days=days, status=status, approver=self.mgr,
            decided_at=timezone.now() if status == LeaveRequest.Status.APPROVED else None)


class CancellingApprovedLeaveTest(Base):
    def test_approved_leave_that_has_not_started_can_be_cancelled(self):
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        self.client.force_authenticate(self.staff)
        r = self.client.post(_cancel(lr.pk), {}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.CANCELLED)
        self.assertTrue(r.data['was_approved'])

    def test_leave_that_has_already_started_cannot(self):
        lr = self.leave(status=LeaveRequest.Status.APPROVED, starts_in_days=-1)
        self.client.force_authenticate(self.staff)
        r = self.client.post(_cancel(lr.pk), {}, format='json')
        self.assertEqual(r.status_code, 409, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)
        self.assertIn('already started', r.data['detail'])

    def test_leave_starting_today_cannot_be_cancelled(self):
        """The boundary. Today counts as started."""
        lr = self.leave(status=LeaveRequest.Status.APPROVED, starts_in_days=0)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(_cancel(lr.pk), {}, format='json').status_code, 409)

    def test_a_pending_request_still_cancels_as_before(self):
        lr = self.leave(status=LeaveRequest.Status.PENDING)
        self.client.force_authenticate(self.staff)
        r = self.client.post(_cancel(lr.pk), {}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data['was_approved'])

    def test_a_refused_request_still_cannot(self):
        lr = self.leave(status=LeaveRequest.Status.REFUSED)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(_cancel(lr.pk), {}, format='json').status_code, 409)

    def test_nobody_can_cancel_someone_elses_leave(self):
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        other = User.objects.create_user('calother', 'calother@alphadirect.co.bw', 'x')
        UserCompanyAccess.objects.get_or_create(user=other, company=self.co)
        self.client.force_authenticate(other)
        self.assertIn(self.client.post(_cancel(lr.pk), {}, format='json').status_code,
                      (403, 404))
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)


class TheButtonMatchesTheRuleTest(Base):
    """can_cancel drives the button. If it disagrees with the endpoint the user
    either sees a dead button or is refused after clicking a live one."""

    def test_can_cancel_is_true_for_approved_future_leave(self):
        self.leave(status=LeaveRequest.Status.APPROVED)
        self.client.force_authenticate(self.staff)
        r = self.client.get(MINE)
        self.assertTrue(r.data['requests'][0]['can_cancel'])

    def test_can_cancel_is_false_once_it_has_started(self):
        self.leave(status=LeaveRequest.Status.APPROVED, starts_in_days=-1)
        self.client.force_authenticate(self.staff)
        r = self.client.get(MINE)
        self.assertFalse(r.data['requests'][0]['can_cancel'])

    def test_can_cancel_is_false_for_refused(self):
        self.leave(status=LeaveRequest.Status.REFUSED)
        self.client.force_authenticate(self.staff)
        r = self.client.get(MINE)
        self.assertFalse(r.data['requests'][0]['can_cancel'])


class AttendanceGoesBackTest(Base):
    """The part that would rot silently: approving marked those days "on leave"."""

    def _workday(self, day, *, required='8.00', tracked='0.00'):
        return WorkdayJustification.objects.create(
            profile=self.prof, work_date=day,
            required_hours=Decimal(required), tracked_hours=Decimal(tracked),
            status=WorkdayJustification.Status.UNJUSTIFIED)

    def test_cancelling_puts_the_days_back_to_unjustified(self):
        from hris.leave_backfill import backfill_workdays_for_leave
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        row = self._workday(lr.start_date)
        backfill_workdays_for_leave(lr)
        row.refresh_from_db()
        self.assertEqual(row.reason, WorkdayJustification.Reason.ON_LEAVE)
        self.assertEqual(row.linked_leave_id, lr.pk)

        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(_cancel(lr.pk), {}, format='json').status_code, 200)

        row.refresh_from_db()
        self.assertIsNone(row.linked_leave_id, 'the day is still linked to cancelled leave')
        self.assertEqual(row.reason, '')
        self.assertEqual(Decimal(row.justified_hours), Decimal('0'))
        self.assertEqual(row.status, WorkdayJustification.Status.UNJUSTIFIED,
                         'a day with missing hours must be chased again')

    def test_a_day_justified_for_another_reason_is_left_alone(self):
        from hris.leave_backfill import backfill_workdays_for_leave
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        row = self._workday(lr.start_date)
        backfill_workdays_for_leave(lr)
        # Someone re-justifies it a different way afterwards.
        row.refresh_from_db()
        row.reason = WorkdayJustification.Reason.OTHER if hasattr(
            WorkdayJustification.Reason, 'OTHER') else 'other'
        row.save(update_fields=['reason'])

        self.client.force_authenticate(self.staff)
        self.client.post(_cancel(lr.pk), {}, format='json')
        row.refresh_from_db()
        self.assertNotEqual(row.reason, '', 'another reason must not be wiped')

    def test_a_day_they_actually_worked_is_never_touched(self):
        from hris.leave_backfill import backfill_workdays_for_leave
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        worked = self._workday(lr.start_date, tracked='8.00')
        worked.status = WorkdayJustification.Status.MET
        worked.save(update_fields=['status'])
        backfill_workdays_for_leave(lr)

        self.client.force_authenticate(self.staff)
        self.client.post(_cancel(lr.pk), {}, format='json')
        worked.refresh_from_db()
        self.assertEqual(worked.status, WorkdayJustification.Status.MET)


class PeopleAreToldTest(Base):
    def test_the_employee_and_the_approving_manager_are_both_told(self):
        lr = self.leave(status=LeaveRequest.Status.APPROVED)
        self.client.force_authenticate(self.staff)
        mail.outbox = []
        self.assertEqual(self.client.post(_cancel(lr.pk), {}, format='json').status_code, 200)
        to_emp = [m for m in mail.outbox if 'calstaff@alphadirect.co.bw' in m.to]
        to_mgr = [m for m in mail.outbox if 'calmgr@alphadirect.co.bw' in m.to]
        self.assertTrue(to_emp, 'the employee was not told')
        self.assertTrue(to_mgr, 'the manager who approved it was not told')
        self.assertIn('cancelled', to_mgr[0].subject.lower())

    def test_cancelling_a_pending_request_does_not_email_a_manager(self):
        """Nobody approved it, so there is nobody to tell."""
        lr = self.leave(status=LeaveRequest.Status.PENDING)
        self.client.force_authenticate(self.staff)
        mail.outbox = []
        self.client.post(_cancel(lr.pk), {}, format='json')
        self.assertFalse([m for m in mail.outbox if 'calmgr@alphadirect.co.bw' in m.to])
