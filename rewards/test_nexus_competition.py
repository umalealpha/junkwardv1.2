"""rewards/test_nexus_competition.py — the Nexus competition tracker window.

CFO 2026-09-08: "wipe the records clean, we can hide the demo accounts, the
competition actually started from yesterday ... start new tracker".

So the board must:
  * count ONLY activity dated on/after COMPETITION_START (everything earned
    while we were testing reads as zero — the records are wiped from the
    tracker without destroying a single row);
  * hide the app-store demo / review accounts, which were sitting #1;
  * still let a demo account use the app (hidden from the board only).
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import nexus_standings
from .models import (
    RewardMember, PointsTransaction, CustomerDriveTrip, HealthMetric,
)

START = nexus_standings.COMPETITION_START


def _row(rows, email):
    return next((r for r in rows if (r['email'] or '') == email), None)


class CompetitionWindowTests(TestCase):
    def setUp(self):
        self.m = RewardMember.objects.create(
            customer_name='Window Tester', email='window@gmail.com', is_active=True)

    def _ptx(self, points, day):
        PointsTransaction.objects.create(
            member=self.m, kind=PointsTransaction.Kind.EARN, points=points,
            detail='test', occurred_at=timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, 12, 0)))

    def _trip(self, day):
        CustomerDriveTrip.objects.create(
            member=self.m, started_at=timezone.make_aware(
                timezone.datetime(day.year, day.month, day.day, 8, 0)),
            distance_km=10, duration_min=20, score=80, points_awarded=5)

    def _scan(self, day):
        HealthMetric.objects.create(member=self.m, date=day, resting_hr=62)

    # --- points ------------------------------------------------------------
    def test_points_before_start_do_not_count(self):
        self.m.points_balance = 4200          # earned while testing
        self.m.save(update_fields=['points_balance'])
        self._ptx(4200, START - timedelta(days=30))
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertIsNotNone(row)
        self.assertEqual(row['points'], 0)
        self.assertEqual(row['nexusScore'], 0)

    def test_points_on_start_day_count(self):
        self._ptx(30, START)
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['points'], 30)
        self.assertEqual(row['nexusScore'], 30)

    def test_redemption_inside_window_nets_off_but_never_below_zero(self):
        self._ptx(10, START)
        PointsTransaction.objects.create(
            member=self.m, kind=PointsTransaction.Kind.REDEEM, points=-50,
            detail='redeem', occurred_at=timezone.make_aware(
                timezone.datetime(START.year, START.month, START.day, 13, 0)))
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['points'], 0)

    # --- trips + scans -----------------------------------------------------
    def test_trips_before_start_do_not_count(self):
        self._trip(START - timedelta(days=2))
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['trips'], 0)
        self.assertEqual(row['nexusScore'], 0)

    def test_trip_inside_window_pays_the_bonus(self):
        self._trip(START)
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['trips'], 1)
        self.assertEqual(row['nexusScore'], nexus_standings.TRIP_BONUS)

    def test_scans_before_start_do_not_count(self):
        self._scan(START - timedelta(days=1))
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['wellnessScans'], 0)
        self.assertEqual(row['nexusScore'], 0)

    def test_scan_inside_window_pays_the_bonus(self):
        self._scan(START)
        row = _row(nexus_standings.ranked_testers(), 'window@gmail.com')
        self.assertEqual(row['wellnessScans'], 1)
        self.assertEqual(row['nexusScore'], nexus_standings.SCAN_BONUS)


class DemoAccountsHiddenTests(TestCase):
    def test_named_demo_member_is_hidden(self):
        m = RewardMember.objects.create(
            customer_name='DEMO - Thabo M.', email='', is_active=True)
        PointsTransaction.objects.create(
            member=m, kind=PointsTransaction.Kind.EARN, points=4200, detail='d',
            occurred_at=timezone.now())
        names = [r['name'] for r in nexus_standings.ranked_testers()]
        self.assertNotIn('DEMO - Thabo M.', names)

    def test_review_emails_are_hidden(self):
        for email in ('appreview@alphadirect.co.bw', 'nexus.tester@gmail.com'):
            m = RewardMember.objects.create(
                customer_name=email.split('@')[0], email=email, is_active=True)
            PointsTransaction.objects.create(
                member=m, kind=PointsTransaction.Kind.EARN, points=99, detail='d',
                occurred_at=timezone.now())
        emails = [(r['email'] or '') for r in nexus_standings.ranked_testers()]
        self.assertNotIn('appreview@alphadirect.co.bw', emails)
        self.assertNotIn('nexus.tester@gmail.com', emails)

    def test_a_real_tester_is_still_on_the_board(self):
        m = RewardMember.objects.create(
            customer_name='Real Person', email='real@gmail.com', is_active=True)
        PointsTransaction.objects.create(
            member=m, kind=PointsTransaction.Kind.EARN, points=15, detail='d',
            occurred_at=timezone.now())
        emails = [(r['email'] or '') for r in nexus_standings.ranked_testers()]
        self.assertIn('real@gmail.com', emails)
