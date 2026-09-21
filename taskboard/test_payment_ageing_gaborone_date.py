"""The payment-ageing escalation mixed two calendars.

`candidates()` takes "today" from `timezone.localtime()` (Gaborone) but read
each request's age from `pr.created_at.date()`, which on a UTC-aware datetime is
the UTC date. Gaborone is UTC+2, so between 22:00 and midnight Gaborone the two
dates differ and every request raised in that window counted a day older than it
was — escalating to the CFO early.

It also turned CI red in exactly that window with the product right and the test
wrong: shard 2 failed at 22:2x UTC on 14-Sep-2026 on
`test_default_threshold_comes_from_the_finance_editable_setting`, a 2-day-old
request that measured as 3. Third recurrence of this class (MACHINE-TALK
12-Sep-2026).

Revert the `timezone.localtime(...)` call in taskboard/escalation.py and
`test_a_request_raised_late_at_night_is_not_aged_a_day_early` goes red.
"""
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase

from taskboard import escalation
from taskboard.models import PaymentRequest

GABS = ZoneInfo("Africa/Gaborone")


class PaymentAgeingUsesGaboroneDatesTests(TestCase):
    """23:30 Gaborone on the 15th is 21:30 UTC on the 15th — same UTC day. But
    00:30 Gaborone on the 16th is 22:30 UTC on the 15th: the Gaborone calendar
    has turned over and the UTC one has not. A request raised at 23:30 Gaborone
    on the 15th is ZERO days old at 00:30... no — it is one day old by the
    Gaborone calendar, and that is what must be measured, consistently, from one
    calendar only.
    """

    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago2', 'kago2@example.invalid', 'x')

    def _raise_at(self, ref, when_local):
        pr = PaymentRequest.objects.create(
            ref=ref, status=PaymentRequest.Status.PENDING_CFO, currency='BWP',
            total=Decimal('1000.00'), entity='ADIC', payee='A Supplier',
            inputter='Kago', created_by=self.loader)
        PaymentRequest.objects.filter(pk=pr.pk).update(created_at=when_local)
        return PaymentRequest.objects.get(pk=pr.pk)

    def test_a_request_raised_late_at_night_is_not_aged_a_day_early(self):
        """Raised 23:30 Gaborone on the 15th (= 21:30 UTC, still the 15th in
        both). Judged at 12:00 Gaborone on the 17th: two days by the Gaborone
        calendar. With the bug the UTC date agreed here, so push it into the
        window that breaks: raise at 00:30 Gaborone on the 16th, which is 22:30
        UTC on the 15th. Gaborone says the 16th, UTC says the 15th — one day of
        phantom age."""
        raised = dt.datetime(2026, 9, 16, 0, 30, tzinfo=GABS)      # 22:30 UTC 15th
        self._raise_at('NIGHT-1', raised)
        judged = dt.datetime(2026, 9, 18, 12, 0, tzinfo=GABS)      # Gaborone 18th

        rows, _ = escalation.candidates(threshold_days=3, now=judged)
        # Gaborone: 16th -> 18th = 2 days, under the 3-day threshold.
        # Reading created_at as UTC gives 15th -> 18th = 3 days and escalates.
        self.assertEqual([r['ref'] for r in rows], [],
                         "a request raised after 22:00 Gaborone aged a day early")

    def test_it_still_escalates_once_genuinely_old(self):
        """The guard must fail in BOTH directions — widening the calendar must
        not stop a genuinely stale request escalating."""
        raised = dt.datetime(2026, 9, 16, 0, 30, tzinfo=GABS)
        self._raise_at('NIGHT-2', raised)
        judged = dt.datetime(2026, 9, 19, 12, 0, tzinfo=GABS)      # 3 days on
        rows, _ = escalation.candidates(threshold_days=3, now=judged)
        self.assertEqual([r['ref'] for r in rows], ['NIGHT-2'])
        self.assertEqual(rows[0]['age_days'], 3)
