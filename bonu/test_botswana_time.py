"""The three date helpers that were still reading the SERVER clock.

Fourth time this module family has hit the same defect. `bonu/confirm.py` was
fixed for it, the ledger entry-date guards were fixed for it (PR #688), the
claim-intake guards were fixed for it on 9 Sep 2026 (see
`bonu/test_cases_timezone.py`) — and Fable flagged three more at the same gate
that were left as non-blocking:

  * `legal_rules.days_to_process(...)`  — the default `as_of`
  * `LegalCase.days_quiet(...)`         — the default `as_of`
  * `QueryLetter.is_overdue(...)`       — the default `as_of`

`settings.TIME_ZONE` is Africa/Gaborone; the prod box runs UTC. So between
00:00 and 02:00 Gaborone the server clock still reads YESTERDAY, and each of
these three reports a day short:

  * a claim opened today gives -1 days to process, which `max(0, ...)` then
    flattens to 0 — reading as "processed the same day", the exact false-met
    SLA the function's own docstring says it must never produce;
  * a case last touched today reports -1 days quiet, so the "slipping out"
    board moves it for no reason;
  * a query letter that fell due yesterday reads as still in time for two
    hours, on the very morning the chase should start.

None of the three is a disaster on its own. Together they are the same bug
shipped four times, which is why they are being closed rather than carried.

Frozen at 23:30 UTC = 01:30 Gaborone the next day — the exact window.

RED-FIRST: all three resolve `datetime.date` at call time, so patching
`datetime.date` on the module object reaches them. Revert any one fix and its
own tests fail.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.test import TestCase

from bonu import legal_rules as rules
from bonu.models import LawFirm, LegalCase, QueryLetter

INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)

_REAL_DATE = dt.date


class _StillADate(type):
    """Keep `isinstance(<a real date>, datetime.date)` True while patched, so
    Django's DateField still recognises a date on its way to the DB."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


class _ServerClockDate(_REAL_DATE, metaclass=_StillADate):
    """A `date` whose `.today()` is the SERVER's date, not Botswana's."""

    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_the_window():
    return (mock.patch('django.utils.timezone.now', return_value=INSIDE_WINDOW_UTC),
            mock.patch('datetime.date', _ServerClockDate))


class DaysToProcessTests(TestCase):

    def test_a_claim_received_today_is_nought_days_not_minus_one(self):
        # On the server clock this is -1, which max(0, ...) flattens to 0 — the
        # same number a genuinely same-day claim gives. The bug is invisible in
        # the output, which is what makes it worth a test rather than a glance.
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(
                rules.days_to_process(received_on=GABORONE_TODAY, closed_on=None), 0)

    def test_a_claim_received_yesterday_is_one_day_not_nought(self):
        # THE ONE THAT MATTERS. Server clock says 0 — "processed the same day",
        # a met SLA that never happened. Botswana's clock says 1.
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(
                rules.days_to_process(received_on=GABORONE_TODAY - dt.timedelta(days=1),
                                      closed_on=None),
                1)

    def test_an_explicit_as_of_still_wins(self):
        # The fix must not take the caller's own date away.
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(
                rules.days_to_process(received_on=dt.date(2026, 8, 1), closed_on=None,
                                      as_of=dt.date(2026, 8, 11)),
                10)

    def test_a_closed_claim_is_still_frozen_at_its_closure_date(self):
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(
                rules.days_to_process(received_on=dt.date(2026, 8, 1),
                                      closed_on=dt.date(2026, 8, 4)),
                3)

    def test_no_received_date_is_still_not_known(self):
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertIsNone(rules.days_to_process(received_on=None, closed_on=None))


class DaysQuietTests(TestCase):

    def setUp(self):
        self.firm = LawFirm.objects.create(name='Quiet & Co', is_active=True)

    def _case(self, last_activity):
        return LegalCase.objects.create(
            case_ref='Q-1', firm_type=LegalCase.FirmType.EXTERNAL, firm=self.firm,
            member_ref='BONU-Q-1', instructed_on=dt.date(2026, 8, 1),
            received_on=dt.date(2026, 8, 1), last_activity_on=last_activity)

    def test_a_case_touched_today_is_nought_days_quiet_not_minus_one(self):
        c = self._case(GABORONE_TODAY)
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(c.days_quiet(), 0)

    def test_a_case_touched_yesterday_is_one_day_quiet(self):
        # A negative day count sorts a case to the wrong end of the "slipping
        # out" board — it moves for no reason other than the hour.
        c = self._case(GABORONE_TODAY - dt.timedelta(days=1))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(c.days_quiet(), 1)

    def test_an_explicit_as_of_still_wins(self):
        c = self._case(dt.date(2026, 8, 1))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(c.days_quiet(as_of=dt.date(2026, 8, 6)), 5)

    def test_a_case_never_touched_falls_back_to_the_instruction_date(self):
        # `days_quiet` reads last_activity_on, then first_action_on, then
        # instructed_on. The last of those is NOT NULL on the table, so the
        # "no dates at all" branch is unreachable in practice — this covers the
        # branch that actually runs, and it uses the same clock.
        c = LegalCase.objects.create(
            case_ref='Q-2', firm_type=LegalCase.FirmType.EXTERNAL, firm=self.firm,
            member_ref='BONU-Q-2', instructed_on=GABORONE_TODAY - dt.timedelta(days=3))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertEqual(c.days_quiet(), 3)


class QueryLetterOverdueTests(TestCase):

    def setUp(self):
        self.firm = LawFirm.objects.create(name='Overdue & Co', is_active=True)

    def _letter(self, due):
        return QueryLetter.objects.create(
            firm=self.firm, reference='QL-1', status=QueryLetter.Status.SENT,
            reply_due_on=due)

    def test_a_letter_that_fell_due_yesterday_is_overdue(self):
        # Server clock: due date == server today, so `today > due` is False and
        # the letter reads as still in time — for two hours every night, on the
        # very morning the chase should start.
        ql = self._letter(GABORONE_TODAY - dt.timedelta(days=1))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertTrue(ql.is_overdue())

    def test_a_letter_due_today_is_not_yet_overdue(self):
        # The fix must not make it fire a day early either.
        ql = self._letter(GABORONE_TODAY)
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertFalse(ql.is_overdue())

    def test_an_unsent_letter_is_never_overdue(self):
        ql = QueryLetter.objects.create(
            firm=self.firm, reference='QL-2', status=QueryLetter.Status.DRAFT,
            reply_due_on=dt.date(2026, 1, 1))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertFalse(ql.is_overdue())

    def test_an_explicit_as_of_still_wins(self):
        ql = self._letter(dt.date(2026, 8, 1))
        tz, dp = _in_the_window()
        with tz, dp:
            self.assertFalse(ql.is_overdue(as_of=dt.date(2026, 7, 20)))
            self.assertTrue(ql.is_overdue(as_of=dt.date(2026, 8, 2)))
