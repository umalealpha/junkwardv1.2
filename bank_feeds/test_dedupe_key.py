"""bank_feeds/test_dedupe_key.py — persist_parsed() is a THIRD writer of
banking.BankStatementLine (alongside banking.services.BankStatementImporter
and fnb.statements._persist_statement) and must honour the same
no-double-import guard: populate dedupe_key with a real occurrence count,
and skip-and-count a collision instead of crashing.

Before this fix, persist_parsed() never set dedupe_key at all, so
BankStatementLine.save() defaulted every line to occurrence 0 — two
identical same-day lines in one feed file (the exact "customer debited
twice" case) collided and the second raised IntegrityError, which run_feed()
records as a failed run for the WHOLE FILE.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from bank_feeds.models import BankFeedConfig
from bank_feeds.services import ParsedLine, ParsedStatement, persist_parsed
from banking.models import BankAccount, BankStatementLine


class BankFeedsDedupeTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'feed_tester', 'feed@example.com', 'x')
        gl = Account.objects.create(
            code='1122', name='Feed test bank clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Feed current',
            account_number='000000004', currency_code_id='BWP')
        cls.config = BankFeedConfig.objects.create(
            name='Test feed', bank_account=gl, protocol=BankFeedConfig.Protocol.MANUAL,
            env_prefix='TEST_FEED',
        )

    def _parsed(self, lines):
        return ParsedStatement(
            statement_date='2026-09-06', opening_balance='0.00',
            closing_balance='0.00', lines=lines,
        )

    def test_two_identical_same_day_lines_both_land(self):
        # The genuine repeat case: two identical debit-order pulls in one
        # feed file must NOT collide — both are real transactions.
        lines = [
            ParsedLine(transaction_date='2026-09-06', description='DEBIT ORDER',
                       amount='-100.00', reference='DO-1'),
            ParsedLine(transaction_date='2026-09-06', description='DEBIT ORDER',
                       amount='-100.00', reference='DO-1'),
        ]
        stmt = persist_parsed(self._parsed(lines), self.config,
                               file_name='feed1.csv', user=self.user)
        self.assertEqual(stmt.lines.count(), 2)
        keys = set(stmt.lines.values_list('dedupe_key', flat=True))
        self.assertEqual(len(keys), 2)

    def test_rerunning_the_same_file_does_not_double_up_or_raise(self):
        lines = [
            ParsedLine(transaction_date='2026-09-07', description='MONTHLY FEE',
                       amount='-25.00', reference='FEE-1'),
        ]
        stmt1 = persist_parsed(self._parsed(lines), self.config,
                                file_name='feed2.csv', user=self.user)
        self.assertEqual(stmt1.lines.count(), 1)

        # A second persist_parsed() call for a NEW BankStatement (e.g. the
        # feed re-ran and re-pulled the same source file) with identical
        # line content must not raise, and must not create a second copy of
        # the same transaction.
        stmt2 = persist_parsed(self._parsed(lines), self.config,
                                file_name='feed2.csv', user=self.user)
        self.assertNotEqual(stmt2.id, stmt1.id)
        self.assertEqual(stmt2.lines.count(), 0)
        # line_count must match rows actually stored (0), not entries merely
        # seen (1) — this is what run_feed() folds into BankFeedRun.lines_created.
        stmt2.refresh_from_db()
        self.assertEqual(stmt2.line_count, 0)

        total = BankStatementLine.objects.filter(
            statement__bank_account=self.bank_account,
            transaction_date=dt.date(2026, 9, 7),
        ).count()
        self.assertEqual(total, 1)
