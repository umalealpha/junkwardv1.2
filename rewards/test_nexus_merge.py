"""rewards/test_nexus_merge.py — merging one Nexus member into another.

CFO 2026-09-08: "merge them as one" — he had two logins on the competition
board. The dangerous part is the per-day rows: a health metric and a step day
are unique per (member, day), and adding two step totals for one day is how a
double-count starts. So the kept row WINS and the duplicate's clashing row is
left where it is.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from .models import (
    RewardMember, PointsTransaction, CustomerDriveTrip, HealthMetric, CustomerStepDay,
)
from .management.commands.nexus_merge_members import merge_member
from . import nexus_standings

START = nexus_standings.COMPETITION_START


class MergeMemberTests(TestCase):
    def setUp(self):
        self.keep = RewardMember.objects.create(
            customer_name='Real Person', email='real@alphadirect.co.bw',
            is_active=True, points_balance=10)
        self.dup = RewardMember.objects.create(
            customer_name='Real Person Gmail', email='real@gmail.com',
            is_active=True, points_balance=7)

    def _trip(self, m, day):
        return CustomerDriveTrip.objects.create(
            member=m, started_at=timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, 9, 0)),
            distance_km=8, duration_min=15, score=75, points_awarded=5)

    def test_dry_run_changes_nothing(self):
        self._trip(self.dup, START)
        merge_member(self.keep, self.dup, apply=False)
        self.assertEqual(CustomerDriveTrip.objects.filter(member=self.dup).count(), 1)
        self.assertEqual(CustomerDriveTrip.objects.filter(member=self.keep).count(), 0)
        self.dup.refresh_from_db()
        self.assertTrue(self.dup.is_active)

    def test_trips_and_points_move_and_balances_add(self):
        self._trip(self.dup, START)
        PointsTransaction.objects.create(
            member=self.dup, kind=PointsTransaction.Kind.EARN, points=7,
            detail='d', occurred_at=timezone.now())
        merge_member(self.keep, self.dup, apply=True)
        self.keep.refresh_from_db(); self.dup.refresh_from_db()
        self.assertEqual(CustomerDriveTrip.objects.filter(member=self.keep).count(), 1)
        self.assertEqual(PointsTransaction.objects.filter(member=self.keep).count(), 1)
        self.assertEqual(self.keep.points_balance, 17)
        self.assertEqual(self.dup.points_balance, 0)
        self.assertFalse(self.dup.is_active)
        self.assertTrue(self.dup.customer_name.endswith('(merged)'))

    def test_same_day_step_row_is_left_behind_never_summed(self):
        CustomerStepDay.objects.create(member=self.keep, day=START, steps_total=6000, points_awarded=6)
        CustomerStepDay.objects.create(member=self.dup, day=START, steps_total=9000, points_awarded=9)
        moved, left = merge_member(self.keep, self.dup, apply=True)
        rows = CustomerStepDay.objects.filter(member=self.keep)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().steps_total, 6000)   # kept row wins
        self.assertEqual(left.get('CustomerStepDay'), 1)
        self.assertEqual(CustomerStepDay.objects.filter(member=self.dup).count(), 1)

    def test_non_clashing_day_rows_still_move(self):
        CustomerStepDay.objects.create(member=self.keep, day=START, steps_total=6000)
        CustomerStepDay.objects.create(member=self.dup, day=START + timedelta(days=1), steps_total=9000)
        merge_member(self.keep, self.dup, apply=True)
        self.assertEqual(CustomerStepDay.objects.filter(member=self.keep).count(), 2)

    def test_same_day_scan_is_left_behind(self):
        HealthMetric.objects.create(member=self.keep, date=START, resting_hr=60)
        HealthMetric.objects.create(member=self.dup, date=START, resting_hr=70)
        moved, left = merge_member(self.keep, self.dup, apply=True)
        self.assertEqual(HealthMetric.objects.filter(member=self.keep).count(), 1)
        self.assertEqual(HealthMetric.objects.filter(member=self.keep).first().resting_hr, 60)
        self.assertEqual(left.get('HealthMetric'), 1)

    def test_merged_member_leaves_the_board_and_the_score_lands_on_one_row(self):
        self._trip(self.dup, START)
        self._trip(self.keep, START)
        merge_member(self.keep, self.dup, apply=True)
        rows = nexus_standings.ranked_testers()
        emails = [(r['email'] or '') for r in rows]
        self.assertNotIn('real@gmail.com', emails)          # inactive → off the board
        me = next(r for r in rows if r['email'] == 'real@alphadirect.co.bw')
        self.assertEqual(me['trips'], 2)                     # both trips on one row
