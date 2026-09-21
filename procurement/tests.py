"""
Tests for procurement/services.py commitment JE behaviour — issue #58.
See .claude/specs/po-commitment-je/design.md §"Tests".
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency, UserProfile
from ledger.models import Account, FiscalPeriod, JournalEntry
from procurement.models import (
    GoodsReceiptNote, GoodsReceiptNoteLine,
    PurchaseOrder, PurchaseOrderLine,
)
from procurement.services import (
    COMMITMENT_ASSET_CODE, COMMITMENT_LIABILITY_CODE,
    cfo_approve, fm_approve, cancel, post_grn,
)


ZERO = Decimal('0.00')


class POCommitmentJETest(TestCase):
    """Cover the scenarios in design.md §Tests."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TEST', name='Test Co.')

        cls.fiscal = FiscalPeriod.objects.create(
            period_name='2026-04', start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30), status=FiscalPeriod.Status.OPEN,
        )
        # The PO commitment JE is dated on the approval date (effectively
        # "today" at fm_approve time, since cfo_approved_at is not yet set),
        # so an open period must cover the current date for posting to succeed.
        _t = date.today()
        cls.fiscal_now = FiscalPeriod.objects.create(
            period_name=_t.strftime('%Y-%m'),
            start_date=date(_t.year, 1, 1), end_date=date(_t.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )

        # CoA — the two commitment accounts + an expense account for PO lines + GR-IR
        Account.objects.create(code=COMMITMENT_ASSET_CODE,
                               name='Encumbered Purchase Commitments',
                               account_type='asset', sub_type='commitment_reserve',
                               is_active=True)
        Account.objects.create(code=COMMITMENT_LIABILITY_CODE,
                               name='Reserve for Encumbered Commitments',
                               account_type='liability', sub_type='commitment_reserve',
                               is_active=True)
        Account.objects.create(code='2145', name='GR-IR',
                               account_type='liability', sub_type='current_liability',
                               is_active=True)
        cls.acct_office = Account.objects.create(
            code='6220', name='Office supplies',
            account_type='expense', sub_type='operating_expense', is_active=True,
        )

        # Users — submitter, fm, cfo, all distinct (segregation of duties)
        cls.submitter = User.objects.create_user('submitter', password='x', is_superuser=True)
        cls.fm        = User.objects.create_user('fm',        password='x', is_superuser=True)
        cls.cfo       = User.objects.create_user('cfo',       password='x', is_superuser=True)

    def _make_po(self, total=Decimal('1000.00')):
        from billing.models import Contact
        supplier = Contact.objects.create(
            name='Acme Supplies', contact_type='vendor',
            is_related_party=False, company=self.company,
        )
        po = PurchaseOrder.objects.create(
            po_number=f'PO-{PurchaseOrder.objects.count()+1:04d}',
            issue_date=date(2026, 4, 1),
            department='admin', supplier=supplier, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'),
            company=self.company,
            created_by=self.submitter,
            status=PurchaseOrder.Status.DRAFT,
            justification='ops',
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po, account=self.acct_office,
            description='Test line', quantity=Decimal('1'),
            unit_price=total, line_total=total,
        )
        po.recalculate_totals()
        po.save()
        # Walk through the approval workflow up to PENDING_CFO_APPROVAL
        from procurement.services import submit_for_approval
        po = submit_for_approval(po, self.submitter)
        po.refresh_from_db()
        po = fm_approve(po, self.fm)
        po.refresh_from_db()
        return po

    def test_cfo_approve_creates_commitment_je(self):
        # department='admin' + amount > CFO_APPROVAL_THRESHOLD_BWP (10,000) → two-step CFO path.
        po = self._make_po(Decimal('150000.00'))
        po = cfo_approve(po, self.cfo)
        po.refresh_from_db()
        self.assertIsNotNone(po.commitment_journal_entry_id)
        je = po.commitment_journal_entry
        self.assertEqual(je.journal_type, JournalEntry.JournalType.COMMITMENT)
        # Balanced
        total_dr = sum(ln.debit_bwp  for ln in je.lines.all())
        total_cr = sum(ln.credit_bwp for ln in je.lines.all())
        self.assertEqual(total_dr, total_cr)
        self.assertEqual(total_dr, Decimal('150000.00'))
        # Accounts present
        codes = {ln.account.code for ln in je.lines.all()}
        self.assertEqual(codes, {COMMITMENT_ASSET_CODE, COMMITMENT_LIABILITY_CODE})

    def test_legacy_po_grn_skips_commitment_reversal(self):
        # department='admin' + amount > 10,000 → two-step CFO path (CFO directive 2026-06-29).
        po = self._make_po(Decimal('150000.00'))
        po = cfo_approve(po, self.cfo)
        po.refresh_from_db()
        # Simulate a legacy PO by clearing the FK
        po.commitment_journal_entry = None
        po._allow_status_transition = True
        po.save(update_fields=['commitment_journal_entry'])
        # Build a GRN
        grn = GoodsReceiptNote.objects.create(
            grn_number=f'GRN-{GoodsReceiptNote.objects.count()+1:04d}',
            purchase_order=po, receipt_date=date(2026, 4, 5),
            received_by=self.submitter, created_by=self.submitter,
            status=GoodsReceiptNote.Status.DRAFT,
        )
        for ln in po.lines.all():
            GoodsReceiptNoteLine.objects.create(
                grn=grn, po_line=ln, quantity_received=ln.quantity,
            )
        before = JournalEntry.objects.filter(
            journal_type=JournalEntry.JournalType.COMMITMENT_REVERSAL).count()
        post_grn(grn, self.cfo)
        after = JournalEntry.objects.filter(
            journal_type=JournalEntry.JournalType.COMMITMENT_REVERSAL).count()
        self.assertEqual(before, after)   # no reversal posted for legacy PO

    def test_cancel_after_approval_reverses_full_commitment(self):
        # department='admin' + amount > 10,000 → two-step CFO path (CFO directive 2026-06-29).
        po = self._make_po(Decimal('240000.00'))
        po = cfo_approve(po, self.cfo)
        po.refresh_from_db()
        self.assertIsNotNone(po.commitment_journal_entry_id)
        # Cancel (CFO only; no goods received yet)
        po = cancel(po, self.cfo, reason='changed plan')
        po.refresh_from_db()
        # A COMMITMENT_REVERSAL JE should exist for this PO
        reversals = JournalEntry.objects.filter(
            journal_type=JournalEntry.JournalType.COMMITMENT_REVERSAL,
            source_id=po.pk,
        )
        self.assertEqual(reversals.count(), 1)
        rev = reversals.first()
        total = sum(ln.debit_bwp for ln in rev.lines.all())
        self.assertEqual(total, Decimal('240000.00'))
