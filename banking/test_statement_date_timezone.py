"""The midnight-window bug, banking half: "today" must be Botswana's today.

Same defect as the ledger entry-date guards fixed in f9ff6d56 (PR #688). Both
banking future-date guards compare against the SERVER clock's date, and on a
UTC box — which CI and prod both are — that is YESTERDAY between 00:00 and
02:00 Africa/Gaborone. Inside that window:

  * ``BankStatement.clean()`` rejects a statement dated today.
  * ``BankStatementImporter.import_csv()`` rejects the WHOLE upload, because
    today's transaction lines look future-dated (BUG-004's guard). A manual
    out-of-hours statement upload therefore fails outright. The 06:00
    automated feed is unaffected; a person uploading at 01:00 is not.

These tests freeze the clock at 23:30 UTC = 01:30 Gaborone the next day — the
exact window that burned CI on 18-Aug-2026.

RED-FIRST: both guards do ``from datetime import date as _date`` INSIDE the
function, so they resolve ``datetime.date`` at call time. Patching the module
attribute (``banking.models.date``) would miss them entirely and the tests
would pass against the broken code, proving nothing. Patch ``datetime.date``
globally, exactly as ledger/tests/test_entry_date_timezone.py does.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from banking.models import BankAccount, BankStatement, BankStatementFormat
from banking.services import BankStatementImporter

# 23:30 UTC on the 17th == 01:30 on the 18th in Africa/Gaborone (UTC+2).
INSIDE_WINDOW_UTC = dt.datetime(2026, 8, 17, 23, 30, tzinfo=dt.timezone.utc)
GABORONE_TODAY = dt.date(2026, 8, 18)
SERVER_TODAY = dt.date(2026, 8, 17)      # what a UTC box calls "today" in the window


_REAL_DATE = dt.date


class _StillADate(type):
    """Keep ``isinstance(<a real date>, datetime.date)`` True while patched.

    Django's ``DateField.to_python`` runs exactly that check before handing a
    value to the DB. Without this, a plain date reaching the ORM falls through
    to ``parse_date()`` and dies with "fromisoformat: argument must be str" —
    a test failing for the wrong reason, which certifies nothing.
    """

    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)


class _FakeDate(dt.date, metaclass=_StillADate):
    """date.today() as the SERVER saw it in the window — so a regression back to
    datetime.date.today() reads the 17th and the test goes red."""

    @classmethod
    def today(cls):
        return SERVER_TODAY


def _in_window():
    """Freeze BOTH clocks the old code could have used."""
    return (mock.patch.object(timezone, 'now', return_value=INSIDE_WINDOW_UTC),
            mock.patch('datetime.date', _FakeDate))


class StatementDateGuardTests(SimpleTestCase):
    """BankStatement.clean() — pure validation, no DB needed."""

    def test_statement_dated_today_is_accepted_inside_the_midnight_window(self):
        # 01:30 Gaborone: a statement dated today (18th) is not "in the future",
        # even though the server's UTC clock still reads the 17th. RED on the
        # old guard, which compared against date.today() (= the 17th).
        now_p, date_p = _in_window()
        with now_p, date_p:
            self.assertEqual(timezone.localdate(), GABORONE_TODAY)  # window is real
            BankStatement(statement_date=GABORONE_TODAY).clean()    # must not raise

    def test_statement_dated_tomorrow_is_still_rejected(self):
        # BUG-004 stands: the guard is retimezoned, not weakened.
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(ValidationError):
                BankStatement(
                    statement_date=GABORONE_TODAY + dt.timedelta(days=1)).clean()


class StatementUploadFutureLineTests(TestCase):
    """BankStatementImporter.import_csv() — the whole-upload rejection."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'bank_tz_tester', 'bank_tz@example.com', 'x')
        gl = Account.objects.create(
            code='1119', name='Test bank clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Test current',
            account_number='000000001', currency_code_id='BWP')
        cls.fmt = BankStatementFormat.objects.create(
            name='TZ test format', bank_name='FNB', date_column='Date',
            date_format='%d/%m/%Y', description_column='Description',
            amount_column='Amount')

    @staticmethod
    def _csv(*dates):
        rows = ''.join(f'{d:%d/%m/%Y},Rent,100.00\n' for d in dates)
        return f'Date,Description,Amount\n{rows}'

    def test_upload_with_todays_lines_is_accepted_inside_the_midnight_window(self):
        # The worst of the four: at 01:00 Gaborone every line dated today looks
        # future-dated, so the ENTIRE upload is refused. RED on the old guard.
        now_p, date_p = _in_window()
        with now_p, date_p:
            statement = BankStatementImporter(self.fmt).import_csv(
                self._csv(GABORONE_TODAY - dt.timedelta(days=1), GABORONE_TODAY),
                self.bank_account, user=self.user, file_name='tz.csv')
        self.assertEqual(statement.line_count, 2)
        self.assertEqual(statement.statement_date, GABORONE_TODAY)

    def test_upload_with_genuinely_future_lines_is_still_rejected(self):
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(ValueError) as ctx:
                BankStatementImporter(self.fmt).import_csv(
                    self._csv(GABORONE_TODAY + dt.timedelta(days=1)),
                    self.bank_account, user=self.user, file_name='tz.csv')
        self.assertIn('in the future', str(ctx.exception))

    def test_the_rejection_message_quotes_botswanas_today(self):
        # The message tells the uploader what "today" is. Quoting the server's
        # 17th at 01:30 Gaborone on the 18th is the confusing half of the bug.
        now_p, date_p = _in_window()
        with now_p, date_p:
            with self.assertRaises(ValueError) as ctx:
                BankStatementImporter(self.fmt).import_csv(
                    self._csv(GABORONE_TODAY + dt.timedelta(days=2)),
                    self.bank_account, user=self.user, file_name='tz.csv')
        self.assertIn(f'{GABORONE_TODAY:%d-%b-%Y}', str(ctx.exception))

    def test_amounts_are_untouched(self):
        # Guard-only change: nothing about parsing moved.
        now_p, date_p = _in_window()
        with now_p, date_p:
            statement = BankStatementImporter(self.fmt).import_csv(
                self._csv(GABORONE_TODAY), self.bank_account,
                user=self.user, file_name='tz.csv')
        self.assertEqual(statement.lines.get().amount, Decimal('100.00'))
