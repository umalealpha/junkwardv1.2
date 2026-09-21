"""Overdue tasks HARD-BLOCK a leave application — CFO ruling 2026-08-12.

Background: omni let an employee apply for leave while they had overdue work, because
the leave endpoint never looked at tasks — it only SHOWED an applicant's
at-risk tasks to the approver. The CFO's ruling: nobody (the CFO included) may
apply for leave of ANY type while they hold an overdue task. Finish it or hand
it over first.

Locks:
  1. An overdue open task blocks the application (any leave type).
  2. It blocks EVERYONE — no CFO/role exemption.
  3. A task that is not yet due, or already done, does NOT block.
  4. With no overdue tasks, leave applies normally (proves the block is the
     only thing failing case 1 — the test would 201 without the fix).
"""
import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, OmniTask, UserProfile
from hris.models import HRISProfile, LeaveType
from payroll.models import Employee

APPLY_URL = '/hris/api/leave-requests/'
REASON = 'Family time off booked well in advance for a planned personal trip away.'


def _unlock(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


def _staff(username, name, company, *, email=None):
    user = User.objects.create_user(
        username=username, email=email or f'{username}@example.com', password='x')
    emp = Employee.objects.create(
        employee_number=username.upper(), full_name=name,
        company=company, email=user.email, user=user)
    _unlock(user)
    return user, emp


class LeaveOverdueTaskGateTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TADIC', name='Alpha Direct (test)')
        cls.mgr_user, cls.mgr = _staff('mgr', 'The Manager', cls.co)
        HRISProfile.objects.create(employee=cls.mgr)
        cls.user, cls.emp = _staff('worker', 'Busy Worker', cls.co)
        HRISProfile.objects.create(employee=cls.emp, manager=cls.mgr)

        LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 22})
        LeaveType.objects.get_or_create(
            code='sick', defaults={'name': 'Sick Leave', 'default_annual_days': 14})

    def _overdue_task(self, assignee, *, title='Reconcile the bank', days_ago=3,
                      status=OmniTask.Status.PENDING):
        return OmniTask.objects.create(
            assigner=self.mgr_user, assignee=assignee, title=title,
            due_at=timezone.localdate() - dt.timedelta(days=days_ago),
            status=status,
        )

    def _apply(self, user, leave_type='annual', *, start='2026-08-20', end='2026-08-20'):
        self.client.force_authenticate(user)
        return self.client.post(APPLY_URL, {
            'type': leave_type,
            'start_date': start,
            'end_date': end,
            'approver_id': str(self.mgr_user.id),
            'reason': REASON,
        })

    # 1 — the core block (would 201 without the fix)
    def test_overdue_task_blocks_annual_leave(self):
        self._overdue_task(self.user)
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('overdue', resp.json()['detail'].lower())
        self.assertEqual(len(resp.json()['overdue_tasks']), 1)

    # 1b — every type, even sick (gate fires before the cert requirement)
    def test_overdue_task_blocks_sick_leave_too(self):
        self._overdue_task(self.user)
        resp = self._apply(self.user, 'sick')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('overdue', resp.json()['detail'].lower())

    # 1c — a BLOCKED (still-open) overdue task also gates, not just PENDING —
    # pins _OPEN_STATUSES membership (Fable L2: guard the constant).
    def test_blocked_status_overdue_task_also_blocks(self):
        self._overdue_task(self.user, status=OmniTask.Status.BLOCKED)
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 400, resp.content)

    # 2 — no role exemption: a CFO is blocked exactly the same
    def test_overdue_task_blocks_even_the_cfo(self):
        prof = UserProfile.objects.get(user=self.user)
        prof.title = UserProfile.Title.CFO
        prof.save(update_fields=['title'])
        self._overdue_task(self.user)
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 400, resp.content)

    # 3a — a task not yet due does not block
    def test_future_task_does_not_block(self):
        OmniTask.objects.create(
            assigner=self.mgr_user, assignee=self.user, title='Later task',
            due_at=timezone.localdate() + dt.timedelta(days=5),
            status=OmniTask.Status.PENDING)
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 201, resp.content)

    # 3b — a completed task never blocks, even if its due date has passed
    def test_done_overdue_task_does_not_block(self):
        self._overdue_task(self.user, status=OmniTask.Status.DONE)
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 201, resp.content)

    # 4 — control: a clean worker applies normally
    def test_no_tasks_applies_normally(self):
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 201, resp.content)

    # 5 — someone else's overdue task must not block me
    def test_other_persons_overdue_task_does_not_block_me(self):
        self._overdue_task(self.mgr_user)   # the manager's task, not the worker's
        resp = self._apply(self.user, 'annual')
        self.assertEqual(resp.status_code, 201, resp.content)


class OverdueTasksForHelperTest(APITestCase):
    """Direct unit cover for taskboard.services.overdue_tasks_for."""
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='THLP', name='Helper Co (test)')
        cls.boss, _ = _staff('boss', 'Boss', cls.co)
        cls.u, _ = _staff('u1', 'User One', cls.co)

    def _task(self, **kw):
        base = dict(assigner=self.boss, assignee=self.u, title='t',
                    status=OmniTask.Status.PENDING)
        base.update(kw)
        return OmniTask.objects.create(**base)

    def test_picks_only_open_overdue(self):
        from taskboard.services import overdue_tasks_for
        today = timezone.localdate()
        self._task(title='overdue', due_at=today - dt.timedelta(days=1))
        self._task(title='future', due_at=today + dt.timedelta(days=1))
        self._task(title='no-date', due_at=None)
        self._task(title='done-overdue', due_at=today - dt.timedelta(days=1),
                   status=OmniTask.Status.DONE)
        got = {t.title for t in overdue_tasks_for(self.u)}
        self.assertEqual(got, {'overdue'})
