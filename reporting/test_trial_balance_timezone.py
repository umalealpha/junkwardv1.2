"""The midnight-window bug on the trial balance the app actually calls.

PR #692 fixed ``ledger.views.TrialBalanceView`` — but that class only serves
``/api/ledger/trial-balance/``, a route with no caller anywhere in the repo.
The canonical route the frontend uses, ``/api/v1/reports/trial-balance/``
(``frontend/src/lib/api.ts``), is wired in ``alpha_finance/api_router.py`` to
``reporting.views.TrialBalanceView`` — a DIFFERENT class of the same name,
which still defaulted its period end to the server's ``date.today()``.

Two views, one name: the fix landed on the dead twin. Caught by the Fable
review of #692; this is the fix for the live one.

Same defect as f9ff6d56 (PR #688): settings.TIME_ZONE is Africa/Gaborone
(UTC+2) but prod and CI are UTC boxes, so between 00:00 and 02:00 Gaborone
``date.today()`` is yesterday and a trial balance pulled in that window
SILENTLY EXCLUDES today's postings. No error — just a wrong number.

Frozen at 23:30 UTC = 01:30 Gaborone the next day, the exact window.

RED-FIRST: ``reporting/views.py`` binds ``date`` at module level
(``from datetime import date``), so the name must be patched on the module —
patching ``datetime.date`` globally would not reach it, and the test would
pass against the broken code, proving nothing.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company, Currency
from ledger.models import Account, JournalEntry, JournalEntryLine
from reporting.views import TrialBalanceView

INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)

ZERO = Decimal('0.00')


class _FakeDate(dt.date):
    """date.today() as the SERVER saw it in the window. No metaclass needed
    here: only reporting.views.date is patched, so Django's DateField
    isinstance checks still see the real datetime.date."""

    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_window():
    return (mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC),
            mock.patch('reporting.views.date', _FakeDate))


class LiveTrialBalanceTimezoneTests(TestCase):
    """/api/v1/reports/trial-balance/ — the route the frontend calls."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        # Superuser: CanViewFinancials short-circuits on is_superuser.
        cls.user = User.objects.create_superuser(
            'rep_tb_tz', 'rep_tb_tz@example.com', 'x')
        cls.company = Company.objects.create(code='RTZ', name='Reporting TZ Co')
        cls.cash = Account.objects.create(
            code='1003', name='Cash (reporting tz)', account_type='asset',
            sub_type='test', is_active=True)
        cls.revenue = Account.objects.create(
            code='4003', name='Revenue (reporting tz)', account_type='income',
            sub_type='test', is_active=True)

        je = JournalEntry.objects.create(
            entry_date=GABORONE_TODAY, description='midnight window live TB',
            company=cls.company, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.DRAFT, created_by=cls.user)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls.cash, description='dr',
            debit_bwp=Decimal('250.00'), credit_bwp=ZERO)
        JournalEntryLine.objects.create(
            journal_entry=je, account=cls.revenue, description='cr',
            debit_bwp=ZERO, credit_bwp=Decimal('250.00'))
        # .update() bypasses save(), which refuses to mutate a posted entry.
        JournalEntry.objects.filter(pk=je.pk).update(
            status=JournalEntry.Status.POSTED)

    def _get(self, **params):
        req = APIRequestFactory().get(
            '/api/v1/reports/trial-balance/',
            {'company': self.company.code, **params})
        force_authenticate(req, user=self.user)
        return TrialBalanceView.as_view()(req)

    def test_period_end_defaults_to_botswanas_today(self):
        # `from` given, `to` omitted — reporting/views.py:151. At 01:30
        # Gaborone the period must run to the 18th, not the server's 17th.
        # RED on the old default, which stopped at the 17th and dropped the
        # 250.00 posted today with no error at all.
        now_p, date_p = _in_window()
        with now_p, date_p:
            self.assertEqual(timezone.localdate(), GABORONE_TODAY)  # window is real
            data = self._get(**{'from': '2026-08-01'}).data
        self.assertEqual(data['to_date'], str(GABORONE_TODAY))
        self.assertEqual(data['totals']['total_debits'], '250.00')
        self.assertTrue(data['totals']['balanced'])

    def test_legacy_as_of_path_also_defaults_to_botswanas_today(self):
        # No params at all — the legacy branch, reporting/views.py:156.
        now_p, date_p = _in_window()
        with now_p, date_p:
            data = self._get().data
        self.assertEqual(data['to_date'], str(GABORONE_TODAY))

    def test_an_explicit_period_end_is_still_honoured(self):
        # Guard-only change: an explicit `to` still wins, and stopping at the
        # 17th genuinely excludes today's posting.
        now_p, date_p = _in_window()
        with now_p, date_p:
            data = self._get(**{'from': '2026-08-01', 'to': '2026-08-17'}).data
        self.assertEqual(data['to_date'], '2026-08-17')
        self.assertEqual(data['totals']['total_debits'], '0.00')
