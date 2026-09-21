"""
Weekly manager objectives — the behaviours that must never regress.

The two that matter most, because both have already cost real trust:
  * a task must CLOSE itself when the numbers were hit (Omni's monthly cycles
    do not, and 14 managers were nagged daily for work already filed);
  * a counter that cannot be READ must never be recorded as a miss.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import OmniTask
from hris.models import HRISProfile
from hris.objective_counters import CounterUnavailable
from hris.weekly_objective_models import Direction, WeeklyObjective, WeeklyObjectiveRun
from payroll.models import Employee

MONDAY = dt.date(2026, 9, 14)
CMD = 'weekly_objectives_cycle'
# The command imports `read` inside the method body, so it resolves from the
# counters module at call time — patch it there, not on the command module.
READ = 'hris.objective_counters.read'


class WeeklyObjectiveTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'mgr.test', email='mgr.test@alphadirect.co.bw', password='x')
        self.employee = Employee.objects.create(
            full_name='Test Manager', email='mgr.test@alphadirect.co.bw')
        self.profile = HRISProfile.objects.create(employee=self.employee)

    def make(self, key='backlog', counter='gph_kyc_failed_active',
             direction=Direction.REDUCE, target=25):
        return WeeklyObjective.objects.create(
            profile=self.profile, key=key, title=f'Clear {key}',
            counter=counter, direction=direction, target=target)


class WeekStartTests(TestCase):
    """The Sunday raise must book against the week it starts, not the one ending."""

    def test_sunday_books_the_following_monday(self):
        from hris.management.commands.weekly_objectives_cycle import week_start_for
        self.assertEqual(week_start_for(dt.date(2026, 9, 13)), MONDAY)   # Sunday

    def test_wednesday_settles_the_same_row_the_sunday_raise_wrote(self):
        from hris.management.commands.weekly_objectives_cycle import week_start_for
        self.assertEqual(week_start_for(dt.date(2026, 9, 16)), MONDAY)   # Wednesday


class RaiseTests(WeeklyObjectiveTestBase):
    def test_raises_one_task_due_wednesday_4pm_with_a_frozen_baseline(self):
        self.make()
        with mock.patch(READ, return_value=2644):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
        task = OmniTask.objects.get(assignee=self.user)
        self.assertEqual(task.due_at, dt.date(2026, 9, 16))     # Wednesday
        self.assertEqual(task.due_time, dt.time(16, 0))
        self.assertEqual(task.week_of, MONDAY)
        self.assertEqual(WeeklyObjectiveRun.objects.get().baseline, 2644)

    def test_second_run_does_not_raise_a_duplicate(self):
        self.make()
        with mock.patch(READ, return_value=2644):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
        self.assertEqual(OmniTask.objects.filter(assignee=self.user).count(), 1)

    def test_dry_run_writes_nothing(self):
        self.make()
        with mock.patch(READ, return_value=2644):
            call_command(CMD, '--raise', f'--period={MONDAY}')
        self.assertEqual(OmniTask.objects.count(), 0)
        self.assertEqual(WeeklyObjectiveRun.objects.count(), 0)

    def test_a_baseline_that_cannot_be_read_is_skipped_not_guessed(self):
        self.make()
        with mock.patch(READ, side_effect=CounterUnavailable('Graphite down')):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
        self.assertEqual(OmniTask.objects.count(), 0)
        self.assertEqual(WeeklyObjectiveRun.objects.count(), 0)


class SettleTests(WeeklyObjectiveTestBase):
    def _raise(self, baseline):
        with mock.patch(READ, return_value=baseline):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')

    def _settle(self, actual):
        with mock.patch(READ, return_value=actual):
            call_command(CMD, '--settle', '--commit', f'--period={MONDAY}')

    def test_hitting_the_number_closes_the_task_by_itself(self):
        self.make(target=25)
        self._raise(2644)
        self._settle(2619)                                   # cleared exactly 25
        task = OmniTask.objects.get(assignee=self.user)
        self.assertEqual(task.status, OmniTask.Status.DONE)
        self.assertEqual(task.completion_pct, 100)
        self.assertTrue(WeeklyObjectiveRun.objects.get().met)

    def test_missing_the_number_leaves_the_task_open(self):
        self.make(target=25)
        self._raise(2644)
        self._settle(2630)                                   # cleared only 14
        task = OmniTask.objects.get(assignee=self.user)
        self.assertEqual(task.status, OmniTask.Status.PENDING)
        self.assertFalse(WeeklyObjectiveRun.objects.get().met)

    def test_a_counter_that_cannot_be_read_is_not_a_miss(self):
        self.make(target=25)
        self._raise(2644)
        with mock.patch(READ, side_effect=CounterUnavailable('Graphite down')):
            call_command(CMD, '--settle', '--commit', f'--period={MONDAY}')
        run = WeeklyObjectiveRun.objects.get()
        self.assertIsNone(run.met)                           # never convicted
        self.assertIsNone(run.actual)
        self.assertIn('Graphite down', run.settle_error)
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.PENDING)

    def test_one_missed_number_keeps_the_whole_task_open(self):
        self.make(key='backlog', target=25)
        self.make(key='suppliers', counter='omni_supplier_kyc_done',
                  direction=Direction.INCREASE, target=5)
        # Both counters are mocked through one call; use a side effect per key.
        with mock.patch(READ, side_effect=[2644, 0]):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
        with mock.patch(READ, side_effect=[2619, 2]):        # backlog met, suppliers short
            call_command(CMD, '--settle', '--commit', f'--period={MONDAY}')
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.PENDING)

    def test_an_unmeasured_number_keeps_the_task_open_even_if_the_rest_passed(self):
        self.make(key='backlog', target=25)
        self.make(key='suppliers', counter='omni_supplier_kyc_done',
                  direction=Direction.INCREASE, target=5)
        with mock.patch(READ, side_effect=[2644, 0]):
            call_command(CMD, '--raise', '--commit', f'--period={MONDAY}')
        with mock.patch(READ, side_effect=[2619, CounterUnavailable('down')]):
            call_command(CMD, '--settle', '--commit', f'--period={MONDAY}')
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.PENDING)

    def test_settle_is_idempotent_and_does_not_reopen_or_rescore(self):
        self.make(target=25)
        self._raise(2644)
        self._settle(2619)
        self._settle(9999)                                   # a later blow-out
        run = WeeklyObjectiveRun.objects.get()
        self.assertTrue(run.met)                             # verdict stands
        self.assertEqual(run.actual, 2619)
        self.assertEqual(OmniTask.objects.get().status, OmniTask.Status.DONE)


class DirectionTests(WeeklyObjectiveTestBase):
    """The three shapes of 'good', each judged on its own arithmetic."""

    _seq = 0

    def _run(self, direction, target, baseline, actual):
        DirectionTests._seq += 1
        obj = self.make(key=f'k{DirectionTests._seq}', direction=direction, target=target)
        return WeeklyObjectiveRun(objective=obj, period_start=MONDAY, baseline=baseline,
                                  target=target, direction=direction, actual=actual)

    def test_reduce_needs_the_backlog_down_by_the_target(self):
        self.assertTrue(self._run(Direction.REDUCE, 25, 2644, 2619).evaluate())
        self.assertFalse(self._run(Direction.REDUCE, 25, 2644, 2620).evaluate())

    def test_reduce_is_not_satisfied_by_the_backlog_growing(self):
        self.assertFalse(self._run(Direction.REDUCE, 25, 2644, 2700).evaluate())

    def test_increase_needs_the_count_up_by_the_target(self):
        self.assertTrue(self._run(Direction.INCREASE, 5, 0, 5).evaluate())
        self.assertFalse(self._run(Direction.INCREASE, 5, 0, 4).evaluate())

    def test_nil_means_zero_and_nothing_else(self):
        self.assertTrue(self._run(Direction.NIL, 0, 3, 0).evaluate())
        self.assertFalse(self._run(Direction.NIL, 0, 3, 1).evaluate())

    def test_an_emptied_backlog_counts_as_met(self):
        # Once the job is finished there is nothing left to clear, so a plain
        # 'moved >= target' test would mark the manager who DID the work as
        # missing their number every week from then on.
        self.assertTrue(self._run(Direction.REDUCE, 25, 3, 0).evaluate())

    def test_an_almost_empty_backlog_is_still_judged_on_movement(self):
        # 3 -> 1 is not finished and is not 25 cleared. It is a miss.
        self.assertFalse(self._run(Direction.REDUCE, 25, 3, 1).evaluate())

    def test_an_unsettled_run_refuses_to_be_judged(self):
        run = self._run(Direction.REDUCE, 25, 2644, None)
        with self.assertRaises(ValueError):
            run.evaluate()


class CounterSqlTests(TestCase):
    """The SQL itself — the traps that have already produced false findings."""

    def test_kyc_counters_read_the_commercial_table_too(self):
        """Reading only customer_kyc falsely flagged 17 commercial policies on
        2026-08-19. Every KYC counter must consult customer_kyc_dom_com."""
        from hris import objective_counters as oc
        for sql_fn in (oc.gph_kyc_failed_active, oc.gph_claims_paid_kyc_failed_7d):
            with mock.patch.object(oc, '_graphite_scalar', return_value=0) as m:
                sql_fn()
            sql = m.call_args[0][0]
            self.assertIn('customer_kyc_dom_com', sql)
            self.assertIn('customer_kyc ', sql)

    def test_claims_counter_does_not_use_the_unpopulated_paid_amount_column(self):
        """new_claims.paid_amount is filled on 29 rows in the whole database, so
        a counter built on it would read a comfortable zero forever."""
        from hris import objective_counters as oc
        with mock.patch.object(oc, '_graphite_scalar', return_value=0) as m:
            oc.gph_claims_paid_kyc_failed_7d()
        sql = m.call_args[0][0]
        self.assertIn('claim_reserves', sql)
        self.assertNotIn('paid_amount', sql)

    def test_paid_not_issued_requires_a_null_activation_date(self):
        """status = 0 alone is not 'never issued' — 44,401 of those rows were
        activated at some point and later fell back."""
        from hris import objective_counters as oc
        with mock.patch.object(oc, '_graphite_scalar', return_value=0) as m:
            oc.gph_paid_not_issued()
        sql = m.call_args[0][0]
        self.assertIn('policyActivatedDate IS NULL', sql)
        self.assertIn('l.credit > 0', sql)

    def test_an_unknown_counter_name_is_unavailable_not_a_crash(self):
        from hris.objective_counters import read
        with self.assertRaises(CounterUnavailable):
            read('no_such_counter')

    def test_supplier_counter_ignores_records_nobody_screened(self):
        from billing.models import Contact
        from hris.objective_counters import omni_supplier_kyc_done
        from procurement.kyc_models import VendorKYC

        opened = Contact.objects.create(name='Opened but never screened')
        VendorKYC.objects.create(contact=opened,
                                 sanctions_status=VendorKYC.SanctionsStatus.UNCHECKED)
        self.assertEqual(omni_supplier_kyc_done(), 0)

        screened = Contact.objects.create(name='Actually screened')
        VendorKYC.objects.create(contact=screened,
                                 sanctions_status=VendorKYC.SanctionsStatus.CLEAN)
        self.assertEqual(omni_supplier_kyc_done(), 1)
