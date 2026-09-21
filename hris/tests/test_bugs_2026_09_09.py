"""hris/tests/test_bugs_2026_09_09.py

The two bugs staff filed on the Omni board on 9-Sep-2026.

  c82def7f — Natasha Nthite: "the morning brief indicates that I worked 3.24
  hours, whereas the Omni portal reflects only 1.7 hours". A power cut took her
  machine offline; Time Doctor BACK-FILLED the buffered time after the 06:30
  snapshot pull. send_morning_brief (07:00) read Time Doctor live and emailed
  3.24 h; send_daily_brief (07:05) read the stale snapshot and wrote 1.74 h into
  WorkdayJustification.tracked_hours — the figure the portal shows and the one
  that docks leave from 1-Sep. The stored record must carry the live figure.

  13869f41 — Kakale Botana: "I keep getting the Omni email notification that
  there is a task pending ... it takes me to page that confirms that there is
  nothing outstanding". monthly_feedback_cycle raises a
  monthly_feedback:<period> OmniTask and NOTHING ever closed it. She filed all
  five of her team's August check-ins on 1-Sep and was still chased on 6, 7, 8
  and 9-Sep. The task must close once no report is owed.

Needs Postgres (the omni suite dies on sqlite at ledger migration 0022).
"""
from __future__ import annotations

import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import OmniTask, User
from hris.models import HRISProfile, WorkdayJustification
from hris.performance_feedback_models import MonthlyCheckIn, PerformanceCheckRating
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Employee
from taskboard.models import Notification

# A fixed ordinary weekday (Tuesday) — required hours > 0, not a Botswana
# public holiday, so the day is actually assessed.
DAY = datetime.date(2026, 9, 8)

SNAPSHOT_HOURS = 1.74      # what the 06:30 pull captured (machine still offline)
LIVE_HOURS = 3.24          # what Time Doctor reports once the buffer uploaded

WORKER_EMAIL = 'powercut.worker@alphadirect.co.bw'


def _member(uid, name, email, hours):
    """A snapshot/aggregate member row, in the shape hours_by_employee reads."""
    return {'user_id': uid, 'name': name, 'email': email,
            'hours_tracked': hours, 'productive_hours': hours,
            'productive_pct': 100.0, 'manual_hours': None}


class LateSyncedHoursTests(TestCase):
    """c82def7f — the permanent record must not be written from a stale snapshot."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='PC-001', full_name='Power Cut Worker',
            email=WORKER_EMAIL, status='active', department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def setUp(self):
        # The snapshot the 06:30 pull left behind: a FLOOR, not the truth.
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY,
            payload=[_member('u-pc', 'Power Cut Worker', WORKER_EMAIL, SNAPSHOT_HOURS)],
            settle_samples={'0300': {'u-pc': 6264}, '0400': {'u-pc': 6264},
                            '0830': {'u-pc': 6264}})

    def _run(self, live_members):
        """Run send_daily_brief with the shared live reader returning `live_members`."""
        with mock.patch('integrations.td_live.live_members',
                        return_value=live_members), \
             mock.patch('hris.eligibility._paid_employee_ids',
                        return_value={self.emp.id}):
            call_command('send_daily_brief', '--date', DAY.isoformat(),
                         '--force', '--no-email',
                         stdout=StringIO(), stderr=StringIO())
        return WorkdayJustification.objects.filter(
            profile=self.profile, work_date=DAY).first()

    @override_settings(WORKFORCE_DATA_GUARD_ENABLED=False)
    def test_stored_hours_are_the_live_figure_not_the_stale_snapshot(self):
        """THE BUG: she was shown 3.24 h in the brief and 1.74 h on the portal."""
        row = self._run([_member('u-pc', 'Power Cut Worker', WORKER_EMAIL, LIVE_HOURS)])
        self.assertIsNotNone(row, 'no attendance record was written for the day')
        self.assertEqual(
            float(row.tracked_hours), LIVE_HOURS,
            f'the portal would show {row.tracked_hours} h while the brief emailed '
            f'{LIVE_HOURS} h — the discrepancy Natasha Nthite reported')

    @override_settings(WORKFORCE_DATA_GUARD_ENABLED=False)
    def test_snapshot_is_the_fallback_when_time_doctor_is_unreachable(self):
        """A failed live pull must degrade to the snapshot, never to zero hours
        — scoring the whole company against 0 h would dock real leave."""
        row = self._run(None)
        self.assertIsNotNone(row)
        self.assertEqual(float(row.tracked_hours), SNAPSHOT_HOURS)


class FeedbackTaskClosesTests(TestCase):
    """13869f41 — the task that chases a manager must close when the work is done."""

    PERIOD = (2026, 8)
    TAG = 'monthly_feedback:2026-08'

    @classmethod
    def setUpTestData(cls):
        cls.mgr_user = User.objects.create_user(
            username='kb.test', email='kb.test@alphadirect.co.bw', password='x')
        cls.mgr = Employee.objects.create(
            employee_number='FB-MGR', full_name='Feedback Manager',
            email='kb.test@alphadirect.co.bw', status='active',
            department='Compliance', user=cls.mgr_user)
        cls.reports = []
        for i in range(2):
            emp = Employee.objects.create(
                employee_number=f'FB-R{i}', full_name=f'Report {i}',
                email=f'fb.report{i}@alphadirect.co.bw', status='active',
                department='Compliance')
            cls.reports.append(HRISProfile.objects.create(
                employee=emp, manager=cls.mgr))

    def setUp(self):
        self.task = OmniTask.objects.create(
            assigner=self.mgr_user, assignee=self.mgr_user,
            title='Monthly performance feedback - 2 team member(s) for 2026-08',
            status=OmniTask.Status.PENDING, due_at=datetime.date(2026, 9, 6),
            source=self.TAG)
        self.notice = Notification.objects.create(
            recipient=self.mgr_user, task=self.task,
            type=Notification.Type.OVERDUE)

    def _check_in(self, profile):
        MonthlyCheckIn.objects.create(
            profile=profile, reviewer=self.mgr_user,
            period_year=self.PERIOD[0], period_month=self.PERIOD[1],
            conversation_date=datetime.date(2026, 9, 1),
            overall_rating=PerformanceCheckRating.MEETS,
            strengths='Did the work well.',
            concerns='', evidence='', support_provided='')

    def test_task_closes_once_every_report_has_a_check_in(self):
        """THE BUG: she filed all of them and was still emailed for four days."""
        for p in self.reports:
            self._check_in(p)

        from taskboard import services
        result = services.sweep_due_and_overdue(today=datetime.date(2026, 9, 9))

        self.task.refresh_from_db()
        self.assertEqual(
            self.task.status, OmniTask.Status.DONE,
            'the feedback task is still open, so the daily nudge keeps emailing a '
            'manager whose team feedback is already recorded')
        self.assertIsNotNone(self.task.completed_at)
        self.assertEqual(result['feedback_closed'], 1)

        # …and the standing force-modal reminder is cleared in the same run.
        self.notice.refresh_from_db()
        self.assertTrue(self.notice.acknowledged)

        # No force-modal reminder is left standing on it. Scoped to the two nag
        # types on purpose: the post_save signal also raises a gentle, dismissible
        # ASSIGN_DAY toast when a task is created. That toast is cleared too (see
        # `toasts_cleared`), but it is not the nag she reported and it is counted
        # separately, so this assertion stays about the nag.
        self.assertFalse(Notification.objects.filter(
            task=self.task, acknowledged=False,
            type__in=[Notification.Type.DUE_DAY,
                      Notification.Type.OVERDUE]).exists())

    def test_task_stays_open_while_one_report_is_still_owed(self):
        """The counterweight: closing early would let a manager off the hook."""
        self._check_in(self.reports[0])          # one done, one owed

        from taskboard import services
        services.sweep_due_and_overdue(today=datetime.date(2026, 9, 9))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)

    def test_a_terminated_report_does_not_hold_the_task_open(self):
        """Feedback is never owed for someone who has left."""
        self._check_in(self.reports[0])
        leaver = self.reports[1].employee
        leaver.status = Employee.Status.TERMINATED
        leaver.save(update_fields=['status'])

        from taskboard import services
        services.sweep_due_and_overdue(today=datetime.date(2026, 9, 9))

        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
