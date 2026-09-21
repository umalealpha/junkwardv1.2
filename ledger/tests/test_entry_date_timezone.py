"""The midnight-window bug: "today" must be Botswana's today, not the server's.

Found 18-Aug-2026. The future-date guards compared entry_date against the
SERVER clock's date (timezone.now().date() / date.today()). On a UTC box —
which both CI and prod are — that is yesterday between 00:00 and 02:00
Africa/Gaborone, so every journal dated "today" was rejected as future-dated:
30 petty-cash CI errors at 00:01, and a real nightly 2-hour window in which
users could not post. Fixed to timezone.localdate() (respects TIME_ZONE).

These tests freeze the clock INSIDE that window (23:30 UTC = 01:30 Gaborone
next day) and prove a today-dated entry passes.

RED-FIRST: both layers must fail on the OLD code, so both old clocks are
mocked — timezone.now() (the old model guard) AND datetime.date.today() (the
old serializer guard). Patching only timezone.now would leave the serializer
tests green against the old code, i.e. proving nothing.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework import serializers as drf_serializers

from ledger.serializers import JournalEntryDetailSerializer

# 23:30 UTC on the 17th == 01:30 on the 18th in Africa/Gaborone (UTC+2):
# the exact window that burned CI. tzinfo must be UTC — timezone.localdate()
# converts using settings.TIME_ZONE.
INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)      # what a UTC box calls "today" in the window


class _FakeDate(dt.date):
    """date.today() as the SERVER saw it in the window — so a regression back to
    datetime.date.today() reads the 17th and the test goes red."""
    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_window():
    """Freeze BOTH clocks the old code could have used."""
    return (mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC),
            # Patch the datetime MODULE attribute: the old serializer did
            # `from datetime import date` INSIDE the function, so it resolved
            # datetime.date at call time — patching ledger.serializers.date
            # would have missed it entirely.
            mock.patch('datetime.date', _FakeDate))


class EntryDateTimezoneTests(SimpleTestCase):
    """Serializer-level guard: pure function, no DB needed."""

    def _validate(self, value):
        return JournalEntryDetailSerializer().validate_entry_date(value)

    def test_todays_date_is_accepted_inside_the_midnight_window(self):
        # 01:30 Gaborone: a JE dated "today" (18th) must NOT be "in the future",
        # even though the server's UTC clock still says the 17th. Goes RED on the
        # old serializer, which compared against date.today() (= the 17th).
        now_p, date_p = _in_window()
        with now_p, date_p:
            self.assertEqual(timezone.localdate(), GABORONE_TODAY)  # window is real
            self.assertEqual(self._validate(GABORONE_TODAY), GABORONE_TODAY)

    def test_genuinely_future_dates_are_still_rejected(self):
        # The guard must not have been weakened: tomorrow is still refused.
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(drf_serializers.ValidationError):
                self._validate(GABORONE_TODAY + dt.timedelta(days=1))

    def test_yesterday_still_accepted(self):
        now_p, date_p = _in_window()
        with now_p, date_p:
            y = GABORONE_TODAY - dt.timedelta(days=1)
            self.assertEqual(self._validate(y), y)


class ModelGuardTimezoneTests(TestCase):
    """DB-backed: the guard sits inside JournalEntry.post()."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from core.models import Company
        cls.user = get_user_model().objects.create_user(
            username='tzguard', email='tzguard@alphadirect.co.bw', password='x')
        cls.company = Company.objects.create(code='TZG', name='TZ Guard Co')

    def test_post_guard_uses_gaborone_today_not_server_today(self):
        from decimal import Decimal
        from ledger.models import JournalEntry
        from django.core.exceptions import ValidationError

        je = JournalEntry.objects.create(
            entry_date=GABORONE_TODAY, description='midnight window guard',
            company=self.company, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.DRAFT, created_by=self.user)

        with mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC):
            try:
                je.post(user=self.user, _allow_direct=True)
            except ValidationError as exc:
                # A later failure (no lines, unbalanced, period…) is expected —
                # the one thing that must NOT happen at 01:30 Gaborone is the
                # today-dated entry being called "in the future".
                self.assertNotIn('is in the future', str(exc))
