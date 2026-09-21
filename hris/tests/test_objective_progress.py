"""
"She can see how the numbers are reducing" (CFO 2026-09-09).

His decision on Kakale's 2,627-policy backlog was to leave it with her and let
her use her team, "until then it will put tasks for her, and she can see how the
numbers are reducing."

That last clause was NOT true when he said it: the task only ever showed where a
number stood, never where it had been. 25 against 2,627 reads as hopeless when
the same figure turns up every Sunday. These tests pin the progress line that
fixes it.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase

from hris.management.commands.weekly_objectives_cycle import Command
from hris.models import HRISProfile
from hris.weekly_objective_models import (
    Cadence, Direction, WeeklyObjective, WeeklyObjectiveRun,
)
from payroll.models import Employee

WEEK1 = dt.date(2026, 9, 14)
WEEK2 = dt.date(2026, 9, 21)
WEEK3 = dt.date(2026, 9, 28)


class ProgressLineTests(TestCase):
    def setUp(self):
        self.cmd = Command()
        employee = Employee.objects.create(
            full_name='Progress Tester', email='prog@alphadirect.co.bw',
            employee_number='P-0001')
        self.profile = HRISProfile.objects.create(employee=employee)

    def obj(self, direction=Direction.REDUCE, target=25, key='backlog'):
        return WeeklyObjective.objects.create(
            profile=self.profile, key=key, title=f'Objective {key}',
            counter='gph_kyc_failed_active', direction=direction, target=target)

    def settled(self, obj, period, baseline, actual, met):
        """A finished period. NOT named `run` — that is TestCase's own method."""
        return WeeklyObjectiveRun.objects.create(
            objective=obj, period_start=period, cadence=Cadence.WEEKLY,
            baseline=baseline, target=obj.target, direction=obj.direction,
            actual=actual, met=met)

    # ── week one ────────────────────────────────────────────────────────────
    def test_the_first_week_shows_no_progress_line(self):
        """'0 so far' on week one is discouraging noise, not information."""
        o = self.obj()
        self.assertEqual(self.cmd._progress(o, 2652), '')
        self.assertNotIn('Last time', self.cmd._line(o, 2652))

    def test_an_unsettled_run_is_not_history(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, None, None)      # raised, never settled
        self.assertEqual(self.cmd._progress(o, 2627), '')

    # ── the reducing backlog: the CFO's actual case ─────────────────────────
    def test_it_shows_what_moved_last_week(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2627, True)
        line = self.cmd._progress(o, 2627)
        self.assertIn('2,652 to 2,627', line)
        self.assertIn('cleared 25', line)
        self.assertIn('hit', line)

    def test_it_shows_the_distance_travelled_since_the_start(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2627, True)
        self.settled(o, WEEK2, 2627, 2602, True)
        line = self.cmd._progress(o, 2602)
        self.assertIn('Since 14 Sep', line)
        self.assertIn('cleared 50', line)
        self.assertIn('of 2,652', line)
        self.assertIn('2,602 left', line)

    def test_the_total_is_measured_from_the_first_baseline_not_summed(self):
        """Summing weekly movements double-counts a week where the underlying
        data moved for reasons other than this manager's work."""
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2627, True)      # -25
        self.settled(o, WEEK2, 2700, 2650, True)      # data jumped up, then -50
        line = self.cmd._progress(o, 2650)
        # Summed movements would claim 75. The truth from the first baseline is 2.
        self.assertIn('cleared 2 of 2,652', line)
        self.assertNotIn('cleared 75', line)

    def test_a_missed_week_says_so_and_still_shows_the_total(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2627, True)
        self.settled(o, WEEK2, 2627, 2620, False)     # only 7 of 25
        line = self.cmd._progress(o, 2620)
        self.assertIn('cleared 7', line)
        self.assertIn('missed', line)
        self.assertIn('Since 14 Sep', line)

    def test_no_since_line_while_the_backlog_has_not_actually_moved(self):
        """A '0% done' line is worse than none — it reads as failure."""
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2652, False)
        line = self.cmd._progress(o, 2652)
        self.assertIn('Last time', line)
        self.assertNotIn('Since', line)

    def test_a_backlog_that_grew_does_not_claim_progress(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2700, False)
        line = self.cmd._progress(o, 2700)
        self.assertNotIn('Since', line)

    def test_the_percentage_is_honest(self):
        o = self.obj()
        self.settled(o, WEEK1, 100, 75, True)
        line = self.cmd._progress(o, 50)
        self.assertIn('50% done', line)
        self.assertIn('50 left', line)

    # ── the growing count ───────────────────────────────────────────────────
    def test_an_increase_objective_reads_as_added(self):
        o = self.obj(direction=Direction.INCREASE, target=5, key='suppliers')
        self.settled(o, WEEK1, 0, 5, True)
        line = self.cmd._progress(o, 5)
        self.assertIn('0 to 5', line)
        self.assertIn('added 5', line)
        self.assertNotIn('cleared', line)

    def test_an_increase_shows_the_running_total(self):
        o = self.obj(direction=Direction.INCREASE, target=5, key='suppliers')
        self.settled(o, WEEK1, 0, 5, True)
        self.settled(o, WEEK2, 5, 11, True)
        line = self.cmd._progress(o, 11)
        self.assertIn('added 11', line)
        self.assertNotIn('left', line)          # no backlog to run out

    # ── zero tolerance ──────────────────────────────────────────────────────
    def test_a_nil_objective_reports_a_clean_run_count(self):
        o = self.obj(direction=Direction.NIL, target=0, key='breaches')
        self.settled(o, WEEK1, 0, 0, True)
        self.settled(o, WEEK2, 1, 1, False)
        self.settled(o, WEEK3, 0, 0, True)
        line = self.cmd._progress(o, 0)
        self.assertIn('Last time: 0 — hit', line)
        self.assertIn('Clean 2 of the last 3', line)

    def test_a_nil_objective_never_claims_a_backlog_percentage(self):
        o = self.obj(direction=Direction.NIL, target=0, key='breaches')
        self.settled(o, WEEK1, 3, 0, True)
        line = self.cmd._progress(o, 0)
        self.assertNotIn('%', line)
        self.assertNotIn('Since', line)

    # ── it reaches the task the manager opens ───────────────────────────────
    def test_the_progress_reaches_the_task_body(self):
        o = self.obj()
        self.settled(o, WEEK1, 2652, 2627, True)
        body = self.cmd._line(o, 2627)
        self.assertIn('backlog now: 2,627', body)   # where it stands
        self.assertIn('cleared 25', body)           # where it came from
