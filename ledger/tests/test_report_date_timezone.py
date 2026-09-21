"""The midnight-window bug, ledger reporting half: "today" is Botswana's today.

Companion to test_entry_date_timezone.py (the guards fixed in f9ff6d56 /
PR #688). Three more places default "today" off the SERVER clock, which on a
UTC box is YESTERDAY between 00:00 and 02:00 Africa/Gaborone:

  * ``TrialBalanceView`` — ``end_date`` defaults to ``datetime.date.today()``.
    A TB pulled at 01:00 Gaborone silently EXCLUDES today's postings. No error,
    no warning, just a wrong number — the highest-risk of the set.
  * ``draft_triage.triage_drafts`` — ``as_of`` defaults to the server date, so
    a draft dated today is reported as "-1d old" to the close team.
  * ``generate_recurring_journal_entries`` — ``--target-date`` defaults to the
    server date, so a run inside the window skips templates due today.

Frozen at 23:30 UTC = 01:30 Gaborone the next day, the exact window.

RED-FIRST — the patch target differs per module and getting it wrong makes the
test pass against the broken code:
  * ``ledger/views.py`` does ``import datetime`` and resolves ``datetime.date``
    at call time, so patching ``datetime.date`` globally reaches it.
  * ``draft_triage`` and the management command do ``from datetime import date``
    at MODULE level, binding the name at import — before any patch. Those two
    need the module-local name patched instead.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company, Currency
from ledger.draft_triage import triage_drafts
from ledger.models import Account, JournalEntry, JournalEntryLine
from ledger.views import TrialBalanceView

INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)

ZERO = Decimal('0.00')


_REAL_DATE = dt.date


class _StillADate(type):
    """Keep ``isinstance(<a real date>, datetime.date)`` True while patched.

    Django's ``DateField.to_python`` runs that check before handing a value to
    the DB. Without this the trial-balance test dies inside the ORM with
    "fromisoformat: argument must be str" instead of on the wrong end_date —
    red, but for the wrong reason, which proves nothing.
    """

    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


class _FakeDate(dt.date, metaclass=_StillADate):
    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_window():
    """Freeze every clock the old code could have read: timezone.now(), the
    global datetime.date, and the two module-level `date` names bound at import."""
    return (
        mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC),
        mock.patch('datetime.date', _FakeDate),
        mock.patch('ledger.draft_triage.date', _FakeDate),
        # create=True: the fix removed this module's now-orphaned
        # `from datetime import date`. The red-first run observed the failure
        # with the real import in place; the patch stays so a regression that
        # re-adds `date.today()` here goes red again.
        mock.patch(
            'ledger.management.commands.generate_recurring_journal_entries.date',
            _FakeDate, create=True),
    )


class _WindowMixin:
    def _window(self):
        now_p, date_p, triage_p, cmd_p = _in_window()
        return now_p, date_p, triage_p, cmd_p


class LegacyLedgerTrialBalanceTests(TestCase, _WindowMixin):
    """ledger.views.TrialBalanceView — the LEGACY /api/ledger/trial-balance/.

    Note the route: this class is NOT what the app calls. The frontend hits
    /api/v1/reports/trial-balance/, which api_router.py wires to a different
    class of the same name in reporting.views — covered by
    reporting/test_trial_balance_timezone.py. Two views, one name; the first
    cut of this fix landed only on this dead twin (Fable review of PR #692).
    """

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'tb_tz_tester', 'tb_tz@example.com', 'x')
        cls.company = Company.objects.create(code='TBZ', name='TB TZ Co')
        cls.cash = Account.objects.create(
            code='1001', name='Cash (tz)', account_type='asset',
            sub_type='test', is_active=True)
        cls.revenue = Account.objects.create(
            code='4001', name='Revenue (tz)', account_type='income',
            sub_type='test', is_active=True)
        cls._posted_entry(cls, GABORONE_TODAY, Decimal('250.00'))

    def _posted_entry(cls_or_self, entry_date, amount):  # noqa: N805 - used at class-setup time
        je = JournalEntry.objects.create(
            entry_date=entry_date, description='midnight window TB',
            company=cls_or_self.company, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.DRAFT, created_by=cls_or_self.user)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls_or_self.cash, description='dr',
            debit_bwp=amount, credit_bwp=ZERO)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls_or_self.revenue, description='cr',
            debit_bwp=ZERO, credit_bwp=amount)
        # .update() bypasses save(), which refuses to mutate a posted entry.
        JournalEntry.objects.filter(pk=je.pk).update(
            status=JournalEntry.Status.POSTED)
        return je

    def _default_tb(self):
        req = APIRequestFactory().get('/api/ledger/trial-balance/')
        force_authenticate(req, user=self.user)
        return TrialBalanceView.as_view()(req).data

    def test_todays_postings_are_in_the_default_trial_balance(self):
        # 01:30 Gaborone: the TB must run to Botswana's today (18th). RED on the
        # old view, which defaulted end_date to the server's 17th and filtered
        # entry_date__lte the 17th — dropping the 250.00 posted today.
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p:
            data = self._default_tb()
        self.assertEqual(data['period']['end_date'], str(GABORONE_TODAY))
        self.assertEqual(data['totals']['total_debits'], '250.00')
        self.assertTrue(data['totals']['balanced'])

    def test_an_explicit_end_date_is_still_honoured(self):
        # Guard-only change: an explicit query param still wins.
        req = APIRequestFactory().get(
            '/api/ledger/trial-balance/', {'end_date': '2026-08-17'})
        force_authenticate(req, user=self.user)
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p:
            data = TrialBalanceView.as_view()(req).data
        self.assertEqual(data['period']['end_date'], '2026-08-17')
        self.assertEqual(data['totals']['total_debits'], '0.00')


class DraftTriageAsOfTests(TestCase, _WindowMixin):
    """A draft dated today must be 0 days old, never -1."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'triage_tz_tester', 'triage_tz@example.com', 'x')
        cls.company = Company.objects.create(code='TRZ', name='Triage TZ Co')
        cls.cash = Account.objects.create(
            code='1002', name='Cash (triage tz)', account_type='asset',
            sub_type='test', is_active=True)
        cls.revenue = Account.objects.create(
            code='4002', name='Revenue (triage tz)', account_type='income',
            sub_type='test', is_active=True)
        je = JournalEntry.objects.create(
            entry_date=GABORONE_TODAY, description='fresh draft',
            company=cls.company, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.DRAFT, created_by=cls.user)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls.cash, description='dr',
            debit_bwp=Decimal('10.00'), credit_bwp=ZERO)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls.revenue, description='cr',
            debit_bwp=ZERO, credit_bwp=Decimal('10.00'))

    def test_as_of_defaults_to_botswanas_today(self):
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p:
            result = triage_drafts()
        self.assertEqual(result['as_of'], GABORONE_TODAY.isoformat())
        rows = result['buckets']['POST_READY']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['age_days'], 0)   # was -1 on the server clock

    def test_an_explicit_as_of_is_still_honoured(self):
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p:
            result = triage_drafts(as_of=dt.date(2026, 8, 25))
        self.assertEqual(result['as_of'], '2026-08-25')


class RecurringJournalTargetDateTests(TestCase, _WindowMixin):
    """The daily recurring-JE run must target Botswana's today."""

    @classmethod
    def setUpTestData(cls):
        User.objects.create_superuser('admin', 'recurring_tz@example.com', 'x')

    @staticmethod
    def _fake_result():
        return SimpleNamespace(
            period_end=GABORONE_TODAY, templates_considered=0,
            entries_generated=0, entries_skipped=0, errors=[])

    def test_target_defaults_to_botswanas_today(self):
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p, mock.patch(
            'ledger.management.commands.generate_recurring_journal_entries'
            '.generate_all_due', return_value=self._fake_result()
        ) as gen:
            call_command('generate_recurring_journal_entries')
        self.assertEqual(gen.call_args.args[0], GABORONE_TODAY)

    def test_an_explicit_target_date_is_still_honoured(self):
        now_p, date_p, triage_p, cmd_p = self._window()
        with now_p, date_p, triage_p, cmd_p, mock.patch(
            'ledger.management.commands.generate_recurring_journal_entries'
            '.generate_all_due', return_value=self._fake_result()
        ) as gen:
            call_command('generate_recurring_journal_entries',
                         '--target-date', '2026-08-31')
        self.assertEqual(gen.call_args.args[0], dt.date(2026, 8, 31))
