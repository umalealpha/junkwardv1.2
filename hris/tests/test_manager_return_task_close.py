"""The Monthly Manager Return task must CLOSE when the return is filed.

9-Sep-2026. manager_return_cycle raises a `manager_return:<year>-<month>` task
per people-manager, and nothing ever closed it — manager_return_service.submit()
flipped the return to SUBMITTED and never touched the task. So
sweep_due_and_overdue re-raised an OVERDUE reminder every day and
email_open_reminders emailed a nudge every day, for ever, to managers who had
already filed. Identical bug to the monthly performance feedback one fixed the
same day (hris/feedback_task_close.py).

Half of this file is the COUNTERWEIGHT: proving the closer is not just a blanket
"mark everything done". A task must survive when the work is genuinely still
outstanding — no return at all, a draft, or one the reviewer sent back — and
must never reach across to another manager, another period, or a state a human
chose (blocked / cancelled).
"""
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings

from core.models import Company, OmniTask
from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus
from hris.manager_return_service import (
    get_or_create_draft, save_draft, send_back, submit,
)
from hris.manager_return_task_close import (
    close_completed_manager_return_tasks, close_if_complete, is_outstanding,
)
from hris.models import HRISProfile
from payroll.models import Employee
from taskboard.services import sweep_due_and_overdue

YEAR, MONTH = 2026, 6
TAG = f'manager_return:{YEAR}-{MONTH:02d}'


@override_settings(ELRA_PERF_ENABLED=True)
class ManagerReturnTaskCloseTest(TestCase):
    """One manager, one period, the task the cycle command would have raised."""

    def setUp(self):
        self.company = Company.objects.create(code='MRC', name='Return Close Co.')
        self.boss_user = User.objects.create_user('close-boss', email='closeboss@alphadirect.co.bw')
        self.mgr_user = User.objects.create_user('close-mgr', email='closemgr@alphadirect.co.bw')
        self.boss = Employee.objects.create(
            employee_number='CB1', full_name='The Boss', company=self.company,
            email='closeboss@alphadirect.co.bw', user=self.boss_user,
            department='Executive', job_title='Chief Executive Officer')
        self.mgr = Employee.objects.create(
            employee_number='CM1', full_name='Team Lead', company=self.company,
            email='closemgr@alphadirect.co.bw', user=self.mgr_user,
            department='Information Technology', job_title='IT Manager')
        self.report = Employee.objects.create(
            employee_number='CR1', full_name='Team Member', company=self.company,
            department='Information Technology', job_title='Developer')
        HRISProfile.objects.create(employee=self.mgr, manager=self.boss)
        HRISProfile.objects.create(employee=self.report, manager=self.mgr)

    # ── helpers, copied in shape from test_manager_return.ReturnWorkflowTest ──
    def _task(self, *, assignee=None, source=TAG, status=OmniTask.Status.PENDING):
        return OmniTask.objects.create(
            assigner=self.boss_user, assignee=assignee or self.mgr_user,
            title='Monthly manager return — June 2026', body='',
            priority=OmniTask.Priority.HIGH, status=status, source=source)

    def _filled_draft(self, manager=None, year=YEAR, month=MONTH):
        ret = get_or_create_draft(manager or self.mgr, year, month)
        extras = {k: 'Answered.' for k, _ in ret._shown_extra_questions()}
        save_draft(ret, {
            'leave_action': 'Docked two days and spoke to both of them.',
            'innovation': 'Automated the nightly backup verification.',
            'fy27_actions': 'Cutting licence spend and cross-training the team.',
            'work_finished_on_time': True,
            'dashboard_cleared_on_time': True,
            'overstaffed': False,
            'fy27_aligned': True,
        }, extra_answers=extras, allowed_extra_keys=set(extras.keys()))
        return ret

    def _status(self, task):
        task.refresh_from_db()
        return task.status

    # ── THE BUG ─────────────────────────────────────────────────────────────
    def test_filing_the_return_closes_the_task_immediately(self):
        """The reported symptom: filed on the 1st, still nagged every morning.

        The nag has to stop the moment they file, not on tomorrow's sweep.
        """
        task = self._task()
        submit(self._filled_draft())
        self.assertEqual(self._status(task), OmniTask.Status.DONE,
                         'submit() left the task open — the manager keeps being '
                         'chased for a return they have already filed')
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_the_daily_sweep_closes_a_task_already_stuck(self):
        """The catch-up path — clears what is ALREADY stuck, from any write path."""
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        task = self._task()
        self.assertEqual(close_completed_manager_return_tasks(), 1)
        self.assertEqual(self._status(task), OmniTask.Status.DONE)

    def test_sweep_due_and_overdue_runs_the_closer(self):
        """One call site, wired into the real daily sweep — not a module nobody calls."""
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        task = self._task()
        result = sweep_due_and_overdue()
        self.assertEqual(result.get('cycle_closed'), 1, result)
        self.assertEqual(self._status(task), OmniTask.Status.DONE)

    def test_a_cleared_return_also_counts_as_done(self):
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.CLEARED, is_locked=True)
        task = self._task()
        close_completed_manager_return_tasks()
        self.assertEqual(self._status(task), OmniTask.Status.DONE)

    def test_in_progress_and_partial_tasks_are_closeable_too(self):
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        a = self._task(status=OmniTask.Status.IN_PROGRESS)
        b = self._task(status=OmniTask.Status.PARTIAL)
        self.assertEqual(close_completed_manager_return_tasks(), 2)
        self.assertEqual(self._status(a), OmniTask.Status.DONE)
        self.assertEqual(self._status(b), OmniTask.Status.DONE)

    # ── THE COUNTERWEIGHT: real work must never be closed ───────────────────
    def test_no_return_at_all_leaves_the_task_open(self):
        """The manager has filed nothing. Chase them."""
        task = self._task()
        self.assertTrue(is_outstanding(self.mgr, YEAR, MONTH))
        self.assertEqual(close_completed_manager_return_tasks(), 0)
        self.assertEqual(self._status(task), OmniTask.Status.PENDING)

    def test_a_draft_return_leaves_the_task_open(self):
        """A draft is not a filed return — this is the whole risk of a closer."""
        get_or_create_draft(self.mgr, YEAR, MONTH)
        task = self._task()
        self.assertEqual(close_completed_manager_return_tasks(), 0)
        self.assertEqual(self._status(task), OmniTask.Status.PENDING)

    def test_a_returned_return_leaves_the_task_open(self):
        """Sent back for more detail — the work is the FILER's again."""
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.RETURNED)
        task = self._task()
        self.assertEqual(close_completed_manager_return_tasks(), 0)
        self.assertEqual(self._status(task), OmniTask.Status.PENDING)

    def test_send_back_reopens_the_task_closed_at_submit(self):
        """Otherwise closing on submit strands the filer with no task at all:
        manager_return_cycle skips any manager who already HAS one."""
        task = self._task()
        ret = submit(self._filled_draft())
        self.assertEqual(self._status(task), OmniTask.Status.DONE)
        send_back(ret, self.boss_user, 'Say more about the SLA breaches.')
        self.assertEqual(self._status(task), OmniTask.Status.PENDING,
                         'the reviewer sent it back but the filer has no task')
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_another_managers_task_is_never_touched(self):
        other_user = User.objects.create_user('close-other', email='closeother@alphadirect.co.bw')
        other = Employee.objects.create(
            employee_number='CM2', full_name='Other Lead', company=self.company,
            email='closeother@alphadirect.co.bw', user=other_user,
            department='Claims', job_title='Claims Manager')
        HRISProfile.objects.create(employee=other, manager=self.boss)
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        mine, theirs = self._task(), self._task(assignee=other_user)
        self.assertEqual(close_completed_manager_return_tasks(), 1)
        self.assertEqual(self._status(mine), OmniTask.Status.DONE)
        self.assertEqual(self._status(theirs), OmniTask.Status.PENDING,
                         'closed another manager who has filed nothing')

    def test_another_periods_task_is_never_touched(self):
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        june = self._task()
        july = self._task(source=f'manager_return:{YEAR}-07')
        self.assertEqual(close_completed_manager_return_tasks(), 1)
        self.assertEqual(self._status(june), OmniTask.Status.DONE)
        self.assertEqual(self._status(july), OmniTask.Status.PENDING)

    def test_submit_only_closes_its_own_period(self):
        june = self._task()
        july = self._task(source=f'manager_return:{YEAR}-07')
        submit(self._filled_draft())
        self.assertEqual(self._status(june), OmniTask.Status.DONE)
        self.assertEqual(self._status(july), OmniTask.Status.PENDING)

    def test_a_human_chosen_state_is_not_overwritten(self):
        """Blocked and cancelled are deliberate. Leave them exactly as they are."""
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        blocked = self._task(status=OmniTask.Status.BLOCKED)
        cancelled = self._task(status=OmniTask.Status.CANCELLED)
        close_completed_manager_return_tasks()
        self.assertEqual(self._status(blocked), OmniTask.Status.BLOCKED)
        self.assertEqual(self._status(cancelled), OmniTask.Status.CANCELLED)

    def test_a_foreign_source_tag_is_left_alone(self):
        """The sweep must only ever act on its own tag shape."""
        ManagerMonthlyReturn.objects.create(
            manager=self.mgr, period_year=YEAR, period_month=MONTH,
            status=ReturnStatus.SUBMITTED)
        junk = self._task(source='manager_return:not-a-period')
        feedback = self._task(source=f'monthly_feedback:{YEAR}-{MONTH:02d}')
        self.assertEqual(close_completed_manager_return_tasks(), 0)
        self.assertEqual(self._status(junk), OmniTask.Status.PENDING)
        self.assertEqual(self._status(feedback), OmniTask.Status.PENDING)

    def test_a_duplicate_email_refuses_to_guess_the_manager(self):
        """Omni has re-hire / leaver duplicates sharing one address. Two ACTIVE
        rows on one email must resolve to nobody rather than the wrong person."""
        loginless = User.objects.create_user('close-dup', email='closedup@alphadirect.co.bw')
        for n in ('CD1', 'CD2'):
            Employee.objects.create(
                employee_number=n, full_name='Twice Entered', company=self.company,
                email='closedup@alphadirect.co.bw',
                department='Claims', job_title='Claims Manager')
        task = self._task(assignee=loginless)
        self.assertEqual(close_completed_manager_return_tasks(), 0)
        self.assertEqual(self._status(task), OmniTask.Status.PENDING)

    def test_send_back_never_resurrects_a_cancelled_task(self):
        """Cancelled is a human decision. A send-back must not undo it."""
        task = self._task(status=OmniTask.Status.CANCELLED)
        ret = submit(self._filled_draft())
        send_back(ret, self.boss_user, 'Say more about the SLA breaches.')
        self.assertEqual(self._status(task), OmniTask.Status.CANCELLED)

    # ── the closer must never cost a manager their filed return ─────────────
    def test_close_if_complete_never_raises_on_a_missing_manager(self):
        """Housekeeping hung off submit() must never lose the filed return."""
        self.assertEqual(close_if_complete(None, YEAR, MONTH), 0)

    def test_a_database_error_in_the_closer_does_not_lose_the_return(self):
        """The savepoint. submit() is atomic, so a REAL database error swallowed
        by the closer would otherwise poison the OUTER transaction: on Postgres
        the transaction is aborted, the exception is caught, submit() returns
        happily, and the return the manager just filed never commits. The manager
        sees "filed" and it is a draft again.

        This has to be a genuine database error, not a mocked Python exception —
        a mock does not abort the Postgres transaction, so it passes with or
        without the savepoint and proves nothing.
        """
        def real_db_error(_qs):
            with connection.cursor() as cur:
                cur.execute('SELECT 1 / 0')      # aborts the transaction for real

        self._task()
        ret = get_or_create_draft(self.mgr, YEAR, MONTH)
        extras = {k: 'Answered.' for k, _ in ret._shown_extra_questions()}
        save_draft(ret, {
            'leave_action': 'Docked two days and spoke to both of them.',
            'innovation': 'Automated the nightly backup verification.',
            'fy27_actions': 'Cutting licence spend and cross-training the team.',
            'work_finished_on_time': True,
            'dashboard_cleared_on_time': True,
            'overstaffed': False,
            'fy27_aligned': True,
        }, extra_answers=extras, allowed_extra_keys=set(extras.keys()))

        with mock.patch('hris.manager_return_task_close._close',
                        side_effect=real_db_error):
            submit(ret)          # must not raise

        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnStatus.SUBMITTED,
                         'the closer failed and took the filed return down with it')
