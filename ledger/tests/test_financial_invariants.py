"""
ledger/tests/test_financial_invariants.py

Regression guardrails for the GL invariants that the whole ERP trusts.
Added 2026-06-11 after a full forensic audit found the ledger core was
solid but UNTESTED (ledger/tests.py was a 3-line stub) — so every
"it's enforced" lived in exactly one place with nothing guarding it.
These tests are the CI gate: if a future change reopens any of these
holes, the build fails before it reaches prod.

Each test maps to one of the steering invariants in
.claude/steering/erp-relationships.md (#1 post to GL, #6 TB balances,
#7 BS balances) plus the immutability + entity-isolation rules the CFO
relies on for the FROZEN-NUMBERS register.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

ZERO = Decimal('0.00')


class FinancialInvariantTests(TestCase):
    """The non-negotiable double-entry contract, asserted end to end."""

    @classmethod
    def setUpTestData(cls):
        # A real user — post() writes an AuditLog row referencing the actor.
        cls.user = User.objects.create_superuser('inv_tester', 'inv@test.local', 'x')
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TST', name='Test Entity')
        cls.other = Company.objects.create(code='OTH', name='Other Entity')

        today = date.today()
        # Open period covering last year + this year, so a safely-past test
        # date (today-2d) lands inside it even across a year boundary.
        cls.period = FiscalPeriod.objects.create(
            company=cls.company, period_name=today.strftime('%Y-%m'),
            start_date=date(today.year - 1, 1, 1), end_date=date(today.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )
        # Same period_name is fine — uniqueness is per (company, period_name).
        cls.period_other = FiscalPeriod.objects.create(
            company=cls.other, period_name=today.strftime('%Y-%m'),
            start_date=date(today.year - 1, 1, 1), end_date=date(today.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )

        cls.cash = Account.objects.create(
            code='1000', name='Cash', account_type='asset', sub_type='test',
            is_active=True,
        )
        cls.revenue = Account.objects.create(
            code='4000', name='Revenue', account_type='income', sub_type='test',
            is_active=True,
        )
        # A calculated subtotal — posting to it would double-count the MA P&L.
        cls.summary = Account.objects.create(
            code='4300', name='Net Earned Premium (summary)', account_type='income',
            sub_type='test', is_active=True, is_summary_only=True,
        )

    # -- helpers ------------------------------------------------------------

    def _entry(self, *, company=None, entry_date=None, status=None):
        # Default a couple of days back: the model rejects future-dated
        # entries, and CI can run at the UTC/Gaborone midnight crossing where
        # date.today() reads as "tomorrow" to the server's notion of today.
        return JournalEntry.objects.create(
            entry_date=entry_date or (date.today() - timedelta(days=2)),
            description='test entry',
            company=company or self.company,
            currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            journal_type=JournalEntry.JournalType.GENERAL,
            status=status or JournalEntry.Status.DRAFT,
            created_by=self.user,          # NOT NULL on JournalEntry
        )

    def _line(self, je, account, dr=ZERO, cr=ZERO):
        return JournalEntryLine.objects.create(
            journal_entry=je, account=account, description='test line',
            debit_amount=dr, credit_amount=cr, debit_bwp=dr, credit_bwp=cr,
        )

    # -- invariant 1: balanced entry posts --------------------------------

    def test_balanced_entry_posts(self):
        je = self._entry()
        self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        je.post(user=self.user, _allow_direct=True)
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.Status.POSTED)

    # -- invariant 6: TB must balance — unbalanced entry CANNOT post -------

    def test_unbalanced_entry_rejected(self):
        je = self._entry()
        self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('90.00'))   # 10 short
        with self.assertRaises(ValidationError):
            je.post(user=self.user, _allow_direct=True)
        je.refresh_from_db()
        self.assertEqual(je.status, JournalEntry.Status.DRAFT)

    def test_bwp_imbalance_rejected(self):
        """Original currency balances but BWP doesn't — must still reject."""
        je = self._entry()
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.cash, description='x',
            debit_amount=Decimal('100.00'), credit_amount=ZERO,
            debit_bwp=Decimal('100.00'), credit_bwp=ZERO,
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=self.revenue, description='x',
            debit_amount=ZERO, credit_amount=Decimal('100.00'),
            debit_bwp=ZERO, credit_bwp=Decimal('95.00'),   # BWP leg off
        )
        with self.assertRaises(ValidationError):
            je.post(user=self.user, _allow_direct=True)

    # -- summary-only accounts reject postings (MA roll-up integrity) ------

    def test_summary_account_rejected(self):
        je = self._entry()
        self._line(je, self.summary, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        with self.assertRaises(ValidationError):
            je.post(user=self.user, _allow_direct=True)

    # -- period gate: no open period → cannot post -------------------------

    def test_no_open_period_rejected(self):
        # Past date with NO covering period — isolates the period gate from
        # the separate future-date guard.
        je = self._entry(entry_date=date(2000, 1, 1))
        self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        with self.assertRaises(ValidationError):
            je.post(user=self.user, _allow_direct=True)

    # -- immutability: a posted entry cannot be deleted or edited ----------

    def test_posted_entry_cannot_be_deleted(self):
        je = self._entry()
        self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        je.post(user=self.user, _allow_direct=True)
        with self.assertRaises(ValidationError):
            je.delete()
        self.assertTrue(JournalEntry.objects.filter(pk=je.pk).exists())

    def test_posted_entry_line_immutable(self):
        je = self._entry()
        line = self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        je.post(user=self.user, _allow_direct=True)
        line.refresh_from_db()
        line.debit_amount = Decimal('999.00')
        with self.assertRaises(ValidationError):
            line.save()

    def test_double_post_blocked(self):
        je = self._entry()
        self._line(je, self.cash, dr=Decimal('100.00'))
        self._line(je, self.revenue, cr=Decimal('100.00'))
        je.post(user=self.user, _allow_direct=True)
        with self.assertRaises(ValidationError):
            je.post(user=self.user, _allow_direct=True)   # already POSTED

    # -- entity isolation: lines are queryable per-company -----------------

    def test_lines_isolate_by_company(self):
        """A posted line belongs to exactly one company and a company-scoped
        query never returns another entity's lines — the data-layer backstop
        behind the ADIC-standalone FROZEN-NUMBERS guarantee."""
        je_a = self._entry(company=self.company)
        self._line(je_a, self.cash, dr=Decimal('100.00'))
        self._line(je_a, self.revenue, cr=Decimal('100.00'))
        je_a.post(user=self.user, _allow_direct=True)

        je_b = self._entry(company=self.other)
        self._line(je_b, self.cash, dr=Decimal('250.00'))
        self._line(je_b, self.revenue, cr=Decimal('250.00'))
        je_b.post(user=self.user, _allow_direct=True)

        a_lines = JournalEntryLine.objects.filter(
            journal_entry__company=self.company,
            journal_entry__status=JournalEntry.Status.POSTED,
        )
        self.assertEqual(a_lines.count(), 2)
        self.assertTrue(all(l.journal_entry.company_id == self.company.id
                            for l in a_lines))
        # The other entity's 250 must never leak into TST's scope.
        self.assertEqual(
            sum(l.debit_amount for l in a_lines), Decimal('100.00')
        )
