"""TASK-DATE-01 — OmniTask due_at sanity range (CFO 2026-08-15, Manus QC F4).

The audit found a payment-authorisation task with due_at = 0206-08-01 (year 206)
attached to a BWP 66,880.13 payment. Nothing rejected it, and it displayed on
every board 1800+ years overdue.

The guard rejects a nonsense YEAR, not a "distance from today", so that
legitimate long-fuse sentinels (hris.ghost_payroll uses year 2099) still save.
Rule: 1900 <= year <= today.year + 100. Also coerces a datetime to date so
callers that pass a datetime.datetime (e.g. the Graphite refund importer)
don't blow up on comparison.
"""
from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import OmniTask


class OmniTaskDueAtRangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.assigner = User.objects.create_user('sender', password='x')
        cls.assignee = User.objects.create_user('receiver', password='x')

    def _task(self, due_at):
        return OmniTask(
            assigner=self.assigner, assignee=self.assignee,
            title='Test task', due_at=due_at)

    def test_null_due_at_saves(self):
        t = self._task(None)
        t.save()
        self.assertIsNotNone(t.pk)

    def test_today_saves(self):
        t = self._task(timezone.localdate())
        t.save()
        self.assertIsNotNone(t.pk)

    def test_two_years_out_saves(self):
        t = self._task(timezone.localdate() + timedelta(days=365 * 2))
        t.save()
        self.assertIsNotNone(t.pk)

    def test_far_future_sentinel_2099_saves(self):
        # hris.ghost_payroll uses year 2099 as a "never expires" sentinel.
        # The guard must not break that pattern.
        t = self._task(date(2099, 1, 1))
        t.save()
        self.assertIsNotNone(t.pk)

    def test_datetime_input_is_coerced_to_date(self):
        # The Graphite refund importer passes a datetime.datetime for due_at.
        # The guard must not throw on the comparison AND must persist a date.
        t = self._task(datetime(2026, 8, 15, 16, 0))
        t.save()
        self.assertEqual(t.due_at, date(2026, 8, 15))

    def test_year_206_rejected(self):
        # The actual prod row that triggered the rule.
        t = self._task(date(206, 8, 1))
        with self.assertRaises(ValidationError) as cm:
            t.save()
        self.assertIn('TASK-DATE-01', str(cm.exception))
        # BaseModel stamps the UUID pk on __init__, so `.pk` is set before save
        # runs. The right check is that the row is NOT in the DB.
        self.assertFalse(OmniTask.objects.filter(pk=t.pk).exists())

    def test_year_1800_rejected(self):
        # Another typical typo shape: year < 1900.
        t = self._task(date(1800, 1, 1))
        with self.assertRaises(ValidationError) as cm:
            t.save()
        self.assertIn('TASK-DATE-01', str(cm.exception))

    def test_year_far_beyond_ceiling_rejected(self):
        # today.year + 200 is well past the 100-year ceiling — reject it.
        t = self._task(date(timezone.localdate().year + 200, 1, 1))
        with self.assertRaises(ValidationError):
            t.save()
