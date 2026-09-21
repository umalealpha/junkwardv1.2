"""banking/test_dedupe_key.py — the no-double-import guard on BankStatementLine.

CFO directive 2026-09-12: the store already existed (banking.BankStatement /
BankStatementLine, the CSV importer, the FNB API pull path). What did NOT
exist was a DATABASE-LEVEL unique constraint stopping the same line from
landing twice — the old guard (services.py) only checked the STATEMENT as a
whole (account + date + closing balance + line count), never the line itself.

dedupe_key = sha256(bank_account_id | transaction_date | amount | description
             | reference | occurrence)

``occurrence`` (0, 1, 2, ...) — NOT the line's position in the file — is how
many times this exact content has already been seen, counted fresh at the
start of every import. This was corrected after review: keying on
line_number looked right for two identical same-day debits (they land on
different rows, so different line_numbers, so different keys), but it
defeats the MOST COMMON real duplicate-import case — an overlapping
date-range re-pull (e.g. 1-15 Sep, then 1-30 Sep). The repeated transaction
lands at a DIFFERENT line_number in the second pull, so a position-keyed
guard never catches it. An occurrence count that resets to 0 per import does
catch it: a transaction that appears once in the second pull is occurrence 0
again, which collides with the occurrence-0 key already on file.

These tests exercise the real importer (banking.services.BankStatementImporter)
and the real FNB pull path (fnb.statements._persist_statement), not a stand-in
— per rule 7 ("test what you ship"), a passing unit test on a stub proves
nothing about the actual save path.
"""
from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from banking.api_views import BankStatementLineViewSet
from banking.models import (
    BankAccount, BankStatement, BankStatementFormat, BankStatementLine,
    compute_line_dedupe_key,
)
from banking.services import BankStatementImporter


class _BaseBankTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'dedupe_tester', 'dedupe@example.com', 'x')
        gl = Account.objects.create(
            code='1120', name='Dedupe test bank clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Dedupe current',
            account_number='000000002', currency_code_id='BWP')
        cls.fmt = BankStatementFormat.objects.create(
            name='Dedupe test format', bank_name='FNB', date_column='Date',
            date_format='%d/%m/%Y', description_column='Description',
            amount_column='Amount')


# ---------------------------------------------------------------------------
# 1. Same line imported twice lands once — the CSV importer path.
# ---------------------------------------------------------------------------

class CsvImporterDedupeTests(_BaseBankTestCase):
    """The importer's own line-creation loop must skip an already-present
    fingerprint rather than crash on the DB's unique constraint.
    """

    def test_creating_the_identical_line_twice_lands_once(self):
        stmt = BankStatement.objects.create(
            bank_account=self.bank_account, statement_date=dt.date(2026, 9, 1),
            opening_balance=Decimal('0'), closing_balance=Decimal('100'),
            file_name='dupe.csv', imported_by=self.user, line_count=1,
        )
        key = compute_line_dedupe_key(
            self.bank_account.id, dt.date(2026, 9, 1), Decimal('100.00'),
            'Rent', 'REF1', occurrence=0)

        BankStatementLine.objects.create(
            statement=stmt, line_number=1, transaction_date=dt.date(2026, 9, 1),
            description='Rent', reference='REF1', amount=Decimal('100.00'),
            dedupe_key=key,
        )
        # Second write attempting the SAME fingerprint must not be silently
        # allowed to double the ledger — the DB constraint below is what
        # actually stops it; this proves the constraint exists and fires.
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BankStatementLine.objects.create(
                    statement=stmt, line_number=2,
                    transaction_date=dt.date(2026, 9, 1), description='Rent',
                    reference='REF1', amount=Decimal('100.00'), dedupe_key=key,
                )
        self.assertEqual(BankStatementLine.objects.filter(dedupe_key=key).count(), 1)

    def test_import_csv_skips_a_pre_existing_line_gracefully(self):
        """The importer's pre-check (services.py) must skip the collision
        itself — never let the DB raise IntegrityError up to the caller."""
        # A line with this exact fingerprint already exists (e.g. imported by
        # an earlier pull that touched the same transaction).
        earlier_stmt = BankStatement.objects.create(
            bank_account=self.bank_account, statement_date=dt.date(2026, 9, 2),
            opening_balance=Decimal('0'), closing_balance=Decimal('50'),
            file_name='earlier.csv', imported_by=self.user, line_count=1,
        )
        BankStatementLine.objects.create(
            statement=earlier_stmt, line_number=1,
            transaction_date=dt.date(2026, 9, 2), description='Fuel',
            reference='REF9', amount=Decimal('-50.00'),
            dedupe_key=compute_line_dedupe_key(
                self.bank_account.id, dt.date(2026, 9, 2), Decimal('-50.00'),
                'Fuel', 'REF9', occurrence=0),
        )

        csv_text = 'Date,Description,Reference,Amount\n02/09/2026,Fuel,REF9,-50.00\n'
        fmt = BankStatementFormat.objects.create(
            name='Dedupe test format 2', bank_name='FNB', date_column='Date',
            date_format='%d/%m/%Y', description_column='Description',
            reference_column='Reference', amount_column='Amount')

        # Must not raise — the duplicate line is skipped, not crashed on.
        new_stmt = BankStatementImporter(fmt).import_csv(
            csv_text, self.bank_account, user=self.user, file_name='dup2.csv')

        self.assertEqual(new_stmt.lines.count(), 0)   # the only row was a dupe
        self.assertEqual(BankStatementLine.objects.filter(
            statement__bank_account=self.bank_account,
            transaction_date=dt.date(2026, 9, 2),
        ).count(), 1)   # still just the one from `earlier_stmt`


# ---------------------------------------------------------------------------
# 2. Two genuinely identical-looking but separate transactions BOTH survive.
# ---------------------------------------------------------------------------

class GenuineRepeatTransactionTests(_BaseBankTestCase):

    def test_two_same_day_same_amount_debits_both_survive(self):
        # A real customer debited BWP 250.00 twice on the same day with the
        # identical narrative (two debit-order pulls). They must NOT be
        # collapsed into one line.
        csv_text = (
            'Date,Description,Reference,Amount\n'
            '05/09/2026,DEBIT ORDER,DO-1,-250.00\n'
            '05/09/2026,DEBIT ORDER,DO-1,-250.00\n'
        )
        fmt = BankStatementFormat.objects.create(
            name='Repeat-txn format', bank_name='FNB', date_column='Date',
            date_format='%d/%m/%Y', description_column='Description',
            reference_column='Reference', amount_column='Amount')

        stmt = BankStatementImporter(fmt).import_csv(
            csv_text, self.bank_account, user=self.user, file_name='repeat.csv')

        self.assertEqual(stmt.lines.count(), 2)
        keys = set(stmt.lines.values_list('dedupe_key', flat=True))
        self.assertEqual(len(keys), 2)   # occurrence 0 vs 1 -> different key


# ---------------------------------------------------------------------------
# 3. FNB API pull: re-run and OVERLAPPING re-pull do not double up.
# ---------------------------------------------------------------------------

class FnbPullDedupeTests(_BaseBankTestCase):
    """fnb.statements._persist_statement wipes and reinserts lines for the
    SAME statement (unaffected by this change — see the docstring in
    statements.py). These prove that an overlapping/rerun pull that lands the
    same underlying transaction into a DIFFERENT statement row does not
    double-count it — the case a line_number-keyed guard would have missed.
    """

    def _payload(self, entries):
        return {
            'statement': {
                'account': {'accountNumber': self.bank_account.account_number},
                'balance': [
                    {'typeCode': 'OPBD', 'amountValue': 0, 'creditDebitIndicator': 'Credit'},
                    {'typeCode': 'CLBD', 'amountValue': 0, 'creditDebitIndicator': 'Credit'},
                ],
                'entry': entries,
            }
        }

    def test_repull_of_the_same_statement_replaces_not_doubles(self):
        from fnb.statements import _persist_statement

        entries = [{
            'amountValue': 75.00, 'creditDebitIndicator': 'Debit',
            'bookingDateTime': '2026-09-03', 'servicerReference': 'MONTHLY FEE',
        }]
        d = dt.date(2026, 9, 3)

        stmt1 = _persist_statement(self.bank_account, self._payload(entries), d, d)
        self.assertEqual(stmt1.lines.count(), 1)

        # Re-pull the identical period/content (the legitimate wipe+reinsert
        # flow — must not crash, must not double up).
        stmt2 = _persist_statement(self.bank_account, self._payload(entries), d, d)
        self.assertEqual(stmt2.id, stmt1.id)   # same statement, matched by (account, date, file_name)
        self.assertEqual(stmt2.lines.count(), 1)

    def test_overlapping_1_to_15_then_1_to_30_pull_does_not_duplicate_the_overlap(self):
        """The case that broke the line_number-keyed key: pull 1 covers
        1-15 Sep and captures a transaction on the 10th at line_number 1.
        Pull 2 covers 1-30 Sep — a wider, overlapping re-pull that legitimately
        also has other, later entries, so the SAME 10th transaction now sits
        at a DIFFERENT line_number in that second payload. It must still be
        recognised as the same transaction and skipped, not re-imported.
        """
        from fnb.statements import _persist_statement

        shared_entry = {
            'amountValue': 40.00, 'creditDebitIndicator': 'Debit',
            'bookingDateTime': '2026-09-10', 'servicerReference': 'AIRTIME',
        }
        later_entry = {
            'amountValue': 15.00, 'creditDebitIndicator': 'Debit',
            'bookingDateTime': '2026-09-22', 'servicerReference': 'DATA BUNDLE',
        }

        # Pull 1: 1-15 Sep. shared_entry is the only entry -> line_number 1.
        stmt1 = _persist_statement(
            self.bank_account, self._payload([shared_entry]),
            dt.date(2026, 9, 1), dt.date(2026, 9, 15))
        self.assertEqual(stmt1.lines.count(), 1)

        # Pull 2: 1-30 Sep (overlapping, wider re-pull). A different, EARLIER
        # entry in this payload pushes shared_entry to line_number 2 — the
        # exact scenario a position-keyed guard would miss.
        stmt2 = _persist_statement(
            self.bank_account, self._payload([later_entry, shared_entry]),
            dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        self.assertNotEqual(stmt2.id, stmt1.id)

        # Only the genuinely NEW entry (later_entry) should have landed;
        # shared_entry must have been recognised and skipped.
        self.assertEqual(stmt2.lines.count(), 1)
        self.assertEqual(stmt2.lines.get().description, 'DATA BUNDLE')
        # line_count must match rows actually stored, not entries merely
        # seen (the payload had 2 entries; 1 was a skipped duplicate).
        stmt2.refresh_from_db()
        self.assertEqual(stmt2.line_count, 1)

        total_10th = BankStatementLine.objects.filter(
            statement__bank_account=self.bank_account,
            transaction_date=dt.date(2026, 9, 10),
        ).count()
        self.assertEqual(total_10th, 1)   # never doubled, despite the position change


# ---------------------------------------------------------------------------
# 4. Date-range filter on BankStatementLineViewSet.
# ---------------------------------------------------------------------------

class DateRangeFilterTests(_BaseBankTestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stmt = BankStatement.objects.create(
            bank_account=cls.bank_account, statement_date=dt.date(2026, 9, 10),
            opening_balance=Decimal('0'), closing_balance=Decimal('0'),
            file_name='range.csv', imported_by=cls.user, line_count=3,
        )
        for i, d in enumerate(
            (dt.date(2026, 9, 1), dt.date(2026, 9, 5), dt.date(2026, 9, 10)), start=1
        ):
            BankStatementLine.objects.create(
                statement=cls.stmt, line_number=i, transaction_date=d,
                description='x', amount=Decimal('1.00'),
                dedupe_key=compute_line_dedupe_key(
                    cls.bank_account.id, d, Decimal('1.00'), 'x', '', occurrence=0),
            )

    def _get(self, **params):
        factory = APIRequestFactory()
        req = factory.get('/api/v1/bank-statement-lines/', params)
        force_authenticate(req, user=self.user)
        view = BankStatementLineViewSet.as_view({'get': 'list'})
        return view(req)

    @staticmethod
    def _dates(resp):
        rows = resp.data['results'] if isinstance(resp.data, dict) and 'results' in resp.data \
            else resp.data
        return sorted(r['transaction_date'] for r in rows)

    def test_date_from_excludes_earlier_lines(self):
        resp = self._get(date_from='2026-09-05')
        self.assertEqual(self._dates(resp), ['2026-09-05', '2026-09-10'])

    def test_date_to_excludes_later_lines(self):
        resp = self._get(date_to='2026-09-05')
        self.assertEqual(self._dates(resp), ['2026-09-01', '2026-09-05'])

    def test_date_from_and_to_together_narrow_to_one(self):
        resp = self._get(date_from='2026-09-02', date_to='2026-09-09')
        self.assertEqual(self._dates(resp), ['2026-09-05'])


# ---------------------------------------------------------------------------
# 5. The migration itself applies cleanly against a database that ALREADY
#    holds duplicate-content lines (Defect 2 fix: report, never crash, never
#    delete).
# ---------------------------------------------------------------------------

class MigrationBackfillDuplicateHandlingTests(TestCase):
    """Runs the actual migration module's backfill function (not a
    reimplementation) against rows that simulate pre-existing duplicate
    content, then proves the unique constraint can still be added afterwards.
    """

    @classmethod
    def setUpTestData(cls):
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.user = User.objects.create_superuser(
            'migration_tester', 'migration@example.com', 'x')
        gl = Account.objects.create(
            code='1121', name='Migration test bank clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.bank_account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Migration current',
            account_number='000000003', currency_code_id='BWP')

    def test_backfill_reports_and_survives_pre_existing_duplicate_content(self):
        migration_module = importlib.import_module(
            'banking.migrations.0006_bankstatementline_dedupe_key')

        stmt = BankStatement.objects.create(
            bank_account=self.bank_account, statement_date=dt.date(2026, 8, 1),
            opening_balance=Decimal('0'), closing_balance=Decimal('0'),
            file_name='legacy.csv', imported_by=self.user, line_count=2,
        )
        # Two rows with IDENTICAL business content, as if an old, pre-guard
        # double import had already happened. Give them distinct placeholder
        # dedupe_keys up front so creating them doesn't hit today's live
        # unique constraint (that constraint did not exist at the point in
        # history this simulates).
        line_a = BankStatementLine.objects.create(
            statement=stmt, line_number=1, transaction_date=dt.date(2026, 8, 1),
            description='LEGACY DUPLICATE', reference='LEG-1',
            amount=Decimal('10.00'), dedupe_key='placeholder-a',
        )
        line_b = BankStatementLine.objects.create(
            statement=stmt, line_number=2, transaction_date=dt.date(2026, 8, 1),
            description='LEGACY DUPLICATE', reference='LEG-1',
            amount=Decimal('10.00'), dedupe_key='placeholder-b',
        )

        from django.apps import apps as real_apps

        # Must not raise, must not delete either row.
        migration_module.backfill_dedupe_keys(real_apps, None)

        line_a.refresh_from_db()
        line_b.refresh_from_db()
        self.assertEqual(BankStatementLine.objects.filter(pk__in=[line_a.pk, line_b.pk]).count(), 2)
        # Distinct keys were assigned (occurrence 0 and 1) — no data lost,
        # no collision, both rows kept exactly as they were otherwise.
        self.assertNotEqual(line_a.dedupe_key, line_b.dedupe_key)
        self.assertNotEqual(line_a.dedupe_key, 'placeholder-a')
        self.assertNotEqual(line_b.dedupe_key, 'placeholder-b')

    def test_unique_constraint_can_still_be_added_after_backfilling_duplicates(self):
        """The real end-to-end proof: drop the live unique constraint (so the
        schema matches the migration's starting point), insert pre-existing
        duplicate content, run the actual backfill, then re-add the DB-level
        unique constraint the same way step 4 of the migration does — and
        confirm it succeeds rather than raising."""
        from django.db import connection

        migration_module = importlib.import_module(
            'banking.migrations.0006_bankstatementline_dedupe_key')

        with connection.cursor() as cur:
            cur.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'banking_bankstatementline'::regclass
                AND contype = 'u' AND conname LIKE '%dedupe_key%'
            """)
            row = cur.fetchone()
        self.assertIsNotNone(row, 'expected the live unique constraint to exist')
        constraint_name = row[0]

        with connection.cursor() as cur:
            cur.execute(
                f'ALTER TABLE banking_bankstatementline DROP CONSTRAINT "{constraint_name}"')

        try:
            stmt = BankStatement.objects.create(
                bank_account=self.bank_account, statement_date=dt.date(2026, 8, 2),
                opening_balance=Decimal('0'), closing_balance=Decimal('0'),
                file_name='legacy2.csv', imported_by=self.user, line_count=3,
            )
            for i in range(3):   # three duplicate rows of the same content
                BankStatementLine.objects.create(
                    statement=stmt, line_number=i + 1,
                    transaction_date=dt.date(2026, 8, 2),
                    description='TRIPLICATE', reference='LEG-2',
                    amount=Decimal('5.00'), dedupe_key=f'placeholder-{i}',
                )

            from django.apps import apps as real_apps
            migration_module.backfill_dedupe_keys(real_apps, None)   # must not raise

            # This is the exact operation step 4 of the migration performs —
            # proving it does not fail against data that had duplicates.
            # SET CONSTRAINTS ALL IMMEDIATE first: the deferred FK triggers
            # from the .create() calls above are still pending inside this
            # test's transaction, and Postgres refuses an ALTER TABLE while
            # they are outstanding — a test-harness artefact of TestCase
            # wrapping everything in one transaction, not something the real
            # migration (which runs outside any such wrapper) ever hits.
            with connection.cursor() as cur:
                cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cur.execute(
                    'ALTER TABLE banking_bankstatementline '
                    'ADD CONSTRAINT banking_bsl_dedupe_key_test_uniq UNIQUE (dedupe_key)'
                )
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    'ALTER TABLE banking_bankstatementline '
                    'DROP CONSTRAINT IF EXISTS banking_bsl_dedupe_key_test_uniq'
                )
                cur.execute(
                    f'ALTER TABLE banking_bankstatementline '
                    f'ADD CONSTRAINT "{constraint_name}" UNIQUE (dedupe_key)'
                )
