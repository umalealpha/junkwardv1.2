"""BANK-007 / BANK-008 — Kelvin Kimani & Lefika, 15-Sep-2026.

Two complaints off one screen:

  * "The GL balance is higher than the bank statement balance, with no
    indication on the system of why the difference exists."
  * "She needs to filter by date on the bank accounts to view statements on a
    per-month basis, which is not currently possible."

The first is NOT a data error. ``book_balance`` is the GL as of TODAY (CFO
directive BANK-001, deliberately), while ``statement_balance`` is the closing
balance of the newest uploaded statement at ITS date. Subtracting one from the
other is an apples-to-oranges comparison: any journal posted after the last
statement date inflates the GL by design, and the page said nothing about it.

RED-FIRST NOTE: the assertions below target ``statement_difference``, the
timing-correct figure (bank closing minus GL cut at the statement date,
straight out of services.get_reconciliation_report — code that already existed
and was never wired to any screen). Before the fix the field does not exist, so
the first test fails on the missing key; after a naive "just subtract the two
visible columns" implementation it fails on the VALUE (-250.00 instead of
0.00). That second half is the point: a regression to the obvious-but-wrong
implementation must go red, not merely the absence of the feature.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from banking.models import BankAccount, BankStatement

STATEMENT_DATE = dt.date(2026, 8, 31)
CLOSING = Decimal('1000.00')


class BankReconciliationDifferenceTests(TestCase):
    """The /banking page must explain the GL-vs-bank gap, not just show one."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account, JournalEntry, JournalEntryLine

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'bank_rec_tester', 'bank_rec@example.com', 'x')
        gl = Account.objects.create(
            code='1121', name='Recon test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.gl = gl
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Recon current',
            account_number='000000777', currency_code_id='BWP')

        def _post(entry_date, amount, desc):
            je = JournalEntry.objects.create(
                entry_date=entry_date, description=desc,
                journal_type=JournalEntry.JournalType.BANK,
                status=JournalEntry.Status.POSTED,
                created_by=cls.user, currency_code_id='BWP',
            )
            JournalEntryLine.objects.create(
                journal_entry=je, account=gl, description=desc,
                debit_bwp=amount, credit_bwp=Decimal('0.00'),
            )

        # Agrees with the bank exactly AS AT the statement date.
        _post(dt.date(2026, 8, 15), CLOSING, 'August receipt')
        # Posted AFTER the statement date. This is the whole complaint: it
        # makes the GL-as-of-today column read 1250 against a bank 1000.
        _post(dt.date(2026, 9, 5), Decimal('250.00'), 'September receipt')

        BankStatement.objects.create(
            bank_account=cls.bank_account,
            statement_date=STATEMENT_DATE,
            opening_balance=Decimal('0.00'),
            closing_balance=CLOSING,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _row(self):
        resp = self.client.get('/api/v1/bank-accounts/')
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        body = resp.json()
        rows = body['results'] if isinstance(body, dict) else body
        match = [r for r in rows if str(r['id']) == str(self.bank_account.id)]
        self.assertTrue(match, 'bank account missing from /bank-accounts/')
        return match[0]

    def test_post_statement_journal_does_not_create_a_difference(self):
        """The reported bug, stated as a fact the system must get right.

        GL-as-of-today is 1250 and the bank says 1000, but nothing is actually
        wrong: the 250 was posted after the statement date. The difference the
        page reports must therefore be ZERO. A naive book_balance - closing
        reports -250.00 here and goes red, which is the intent.
        """
        row = self._row()
        self.assertEqual(Decimal(row['book_balance']), Decimal('1250.00'))
        self.assertEqual(Decimal(row['statement_balance']), CLOSING)
        self.assertEqual(
            Decimal(row['statement_difference']), Decimal('0.00'),
            'a journal posted AFTER the statement date must not read as a '
            'reconciling difference',
        )
        self.assertTrue(row['statement_reconciled'])

    def test_gl_at_statement_date_is_exposed_so_the_user_can_see_the_cut_off(self):
        row = self._row()
        self.assertEqual(
            Decimal(row['statement_gl_balance']), CLOSING,
            'the like-for-like GL figure must be cut at the statement date',
        )
        self.assertEqual(row['statement_as_of'], STATEMENT_DATE.isoformat())

    def test_a_real_difference_is_still_reported(self):
        """The guard must fail in BOTH directions: a genuine gap still shows."""
        from ledger.models import JournalEntry, JournalEntryLine
        je = JournalEntry.objects.create(
            entry_date=dt.date(2026, 8, 20), description='Unrecorded bank fee',
            journal_type=JournalEntry.JournalType.BANK,
            status=JournalEntry.Status.POSTED,
            created_by=self.user, currency_code_id='BWP',
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.gl, description='Unrecorded bank fee',
            debit_bwp=Decimal('0.00'), credit_bwp=Decimal('40.00'),
        )
        row = self._row()
        self.assertEqual(Decimal(row['statement_difference']), Decimal('40.00'))
        self.assertFalse(row['statement_reconciled'])

    def test_reconciliation_endpoint_explains_the_difference(self):
        stmt = BankStatement.objects.get(bank_account=self.bank_account)
        resp = self.client.get(f'/api/v1/bank-statements/{stmt.id}/reconciliation/')
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        body = resp.json()
        self.assertEqual(Decimal(body['gl_balance']), CLOSING)
        self.assertEqual(Decimal(body['difference']), Decimal('0.00'))
        self.assertTrue(body['is_reconciled'])
        self.assertIn('unmatched', body['lines'])
        self.assertIn('unmatched_lines', body)


class BankStatementDateFilterTests(TestCase):
    """BANK-008: statements must be viewable one month at a time."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'bank_filter_tester', 'bank_filter@example.com', 'x')
        gl = Account.objects.create(
            code='1122', name='Filter test bank', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Filter current',
            account_number='000000888', currency_code_id='BWP')
        # 25 statements across Jun/Jul/Aug 2026 — deliberately more than the
        # 20 the page used to cap at, so the cap itself is under test.
        for month, days in ((6, 8), (7, 8), (8, 9)):
            for day in range(1, days + 1):
                BankStatement.objects.create(
                    bank_account=cls.bank_account,
                    statement_date=dt.date(2026, month, day),
                    opening_balance=Decimal('0.00'),
                    closing_balance=Decimal('1.00'),
                )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _dates(self, **params):
        params.setdefault('page_size', '200')
        resp = self.client.get('/api/v1/bank-statements/', params)
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        body = resp.json()
        rows = body['results'] if isinstance(body, dict) else body
        return [r['statement_date'] for r in rows]

    def test_from_and_to_date_return_only_that_month(self):
        got = self._dates(from_date='2026-07-01', to_date='2026-07-31')
        self.assertEqual(len(got), 8, got)
        self.assertTrue(all(d.startswith('2026-07') for d in got), got)

    def test_from_date_alone_is_an_open_ended_lower_bound(self):
        got = self._dates(from_date='2026-08-01')
        self.assertEqual(len(got), 9, got)

    def test_to_date_alone_is_an_open_ended_upper_bound(self):
        got = self._dates(to_date='2026-06-30')
        self.assertEqual(len(got), 8, got)

    def test_a_malformed_date_is_a_400_not_a_500(self):
        """Fable, 15-Sep-2026: the first cut passed the raw string to the ORM.

        banking/api_views.py already carries _parse_query_date() for exactly
        this — it exists so a bad date is a 400 instead of Django raising from
        inside the queryset as a 500. The lines endpoint already used it; the
        statements filter must too.
        """
        resp = self.client.get('/api/v1/bank-statements/', {'from_date': 'not-a-date'})
        self.assertEqual(resp.status_code, 400, resp.content[:200])
        resp = self.client.get('/api/v1/bank-statements/', {'to_date': '2026-13-01'})
        self.assertEqual(resp.status_code, 400, resp.content[:200])

    def test_all_statements_are_reachable_not_just_the_newest_twenty(self):
        """The cap nobody reported: June was invisible behind the newest 20."""
        got = self._dates()
        self.assertEqual(len(got), 25, got)
        self.assertTrue(any(d.startswith('2026-06') for d in got), got)
