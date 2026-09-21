"""Supplier Payables Reconciliation — rule + arithmetic tests.

These tests exist because the first cut of this module (delivered as a zip,
scaffolded against a description of omni rather than the code) read the bill
amount from a field that does not exist and therefore showed every supplier at
P0.00 without raising an error. So the first thing proven here is that the money
is real, followed by the as-at-date cut, the reason/justification rules, the
3-way match, segregation of duties and entity scoping.
"""

from datetime import date, timedelta
from decimal import Decimal
import os
from io import StringIO

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import Contact, Invoice
from core.models import Company, Currency, UserCompanyAccess, UserProfile
from ledger.models import Account, JournalEntry
from payments.models import Payment, PaymentAllocation
from procurement.models import GoodsReceiptNote, PurchaseOrder

from supplier_recon import services
from supplier_recon.constants import (
    LedgerStage,
    LineStatus,
    MatchStatus,
    PaymentStatus,
    RunStatus,
    SupplierCategory,
)
from supplier_recon.models import (
    Escalation,
    InvoiceReconItem,
    ReasonCode,
    ReconOwner,
    ReconSupplierProfile,
    SupplierReconRun,
)

JULY = '2026-07'
JULY_START = date(2026, 7, 1)
JULY_END = date(2026, 7, 31)
LONG_REASON = 'Panel beater has not returned the signed job card yet.'


class ReconTestBase(TestCase):
    """Shared fixtures: one entity, one vendor, a bank account, reason codes."""

    @classmethod
    def setUpTestData(cls):
        # Migrations seed currencies and may seed companies — never assume an
        # empty database.
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='RCNA', defaults={'name': 'Recon Test Entity A',
                                   'base_currency': cls.bwp})
        cls.other_company, _ = Company.objects.get_or_create(
            code='RCNB', defaults={'name': 'Recon Test Entity B',
                                   'base_currency': cls.bwp})
        cls.staff = User.objects.create_user('clerk', password='x')
        cls.reviewer = User.objects.create_user('fc', password='x')
        UserProfile.objects.update_or_create(
            user=cls.staff, defaults={'title': UserProfile.Title.ACCOUNTANT})
        UserProfile.objects.update_or_create(
            user=cls.reviewer,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER})

        # Entity access mirrors prod: 156 of 188 real users carry
        # UserCompanyAccess rows, finance staff typically exactly one. Company
        # scoping is enforced in the engine as well as the API, so a clerk with
        # no access row can do nothing — same as everywhere else in omni.
        for u in (cls.staff, cls.reviewer):
            UserCompanyAccess.objects.get_or_create(
                user=u, company=cls.company,
                defaults={'can_view': True, 'can_write': True})

        cls.bank, _ = Account.objects.get_or_create(
            code='RCN-BANK',
            defaults={'name': 'Recon Test Bank', 'account_type': 'asset',
                      'currency_code': cls.bwp, 'is_bank_account': True})

        cls.vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Gaborone Panel Beaters',
            currency_code=cls.bwp, company=cls.company, payment_terms_days=30)
        ReconSupplierProfile.objects.create(
            contact=cls.vendor, category=SupplierCategory.PANEL_BEATER)

        # Reason codes are seeded by migration 0002 — use the real ones rather
        # than inventing test-only codes, so the seed itself is under test.
        cls.reason_dispute = ReasonCode.objects.get(code='QUERY_DISPUTE')
        cls.reason_escalate = ReasonCode.objects.get(code='CASHFLOW_HOLD')

    # -- helpers ---------------------------------------------------------- #
    def _journal(self, entry_date):
        """A real posted journal, so a 'posted' bill in a test is genuinely in
        the GL. ledger_stage reads journal_entry, not status — see
        services._ledger_stage."""
        return JournalEntry.objects.create(
            entry_date=entry_date, description='Recon test vendor bill',
            journal_type='purchase', status='posted', company=self.company,
            currency_code=self.bwp, created_by=self.staff)

    def make_bill(self, *, total='10000.00', issue_date=JULY_START,
                  due_date=None, status=None, contact=None, company=None,
                  purchase_order=None, rate='1.00000000', posted=None):
        status = status or Invoice.Status.POSTED
        # A bill is "in the ledger" when it carries a journal entry. Default:
        # anything not draft/pending-approval is posted, matching real posting.
        if posted is None:
            posted = status not in (Invoice.Status.DRAFT,
                                    Invoice.Status.PENDING_APPROVAL)
        return Invoice.objects.create(
            invoice_type=Invoice.InvoiceType.VENDOR_BILL,
            contact=contact or self.vendor,
            company=company or self.company,
            issue_date=issue_date,
            due_date=due_date if due_date is not None else issue_date + timedelta(days=30),
            currency_code=self.bwp,
            exchange_rate=Decimal(rate),
            subtotal=Decimal(total),
            total_amount=Decimal(total),
            balance_due=Decimal(total),
            status=status,
            purchase_order=purchase_order,
            journal_entry=self._journal(issue_date) if posted else None,
            created_by=self.staff,
        )

    def pay(self, invoice, amount, payment_date):
        payment = Payment.objects.create(
            payment_type='payment', contact=invoice.contact,
            company=invoice.company, bank_account=self.bank,
            payment_date=payment_date, currency_code=self.bwp,
            amount=Decimal(amount), payment_method='eft',
            status=Payment.Status.CONFIRMED, created_by=self.staff)
        PaymentAllocation.objects.create(payment=payment, invoice=invoice,
                                         amount_allocated=Decimal(amount))
        return payment

    def make_po(self, *, claim_ref='', department=PurchaseOrder.Department.CLAIMS,
                status=None):
        return PurchaseOrder.objects.create(
            supplier=self.vendor, company=self.company, department=department,
            currency_code=self.bwp, issue_date=JULY_START,
            status=status or PurchaseOrder.Status.APPROVED,
            related_claim_reference=claim_ref, created_by=self.staff)

    def build(self):
        return services.build_recon_run(self.company, JULY,
                                       prepared_by=self.staff)


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------

class PeriodBoundsTests(TestCase):

    def test_month_bounds(self):
        self.assertEqual(services.period_bounds('2026-07'), (JULY_START, JULY_END))

    def test_february_leap_year(self):
        self.assertEqual(services.period_bounds('2028-02'),
                         (date(2028, 2, 1), date(2028, 2, 29)))

    def test_rejects_rubbish(self):
        for bad in ['', '2026', '2026-13', 'July', '2026-00', '1999-05', 'x-y']:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                services.period_bounds(bad)


# ---------------------------------------------------------------------------
# The money — this is the bug that made the first version useless
# ---------------------------------------------------------------------------

class MoneyTests(ReconTestBase):

    def test_bill_amount_is_the_real_total_not_zero(self):
        self.make_bill(total='12345.67')
        run = self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount, Decimal('12345.67'))
        self.assertEqual(run.total_invoiced, Decimal('12345.67'))

    def test_unpaid_bill_shows_as_unpaid_with_full_outstanding(self):
        self.make_bill(total='5000.00')
        run = self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.payment_status, PaymentStatus.UNPAID)
        self.assertEqual(item.amount_paid, Decimal('0.00'))
        self.assertEqual(item.amount_outstanding, Decimal('5000.00'))
        self.assertEqual(run.total_unpaid, Decimal('5000.00'))

    def test_part_payment_is_partially_paid(self):
        bill = self.make_bill(total='5000.00')
        self.pay(bill, '2000.00', date(2026, 7, 20))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.payment_status, PaymentStatus.PARTIALLY_PAID)
        self.assertEqual(item.amount_paid, Decimal('2000.00'))
        self.assertEqual(item.amount_outstanding, Decimal('3000.00'))

    def test_full_payment_is_paid(self):
        bill = self.make_bill(total='5000.00')
        self.pay(bill, '5000.00', date(2026, 7, 20))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.payment_status, PaymentStatus.PAID)
        self.assertEqual(item.amount_outstanding, Decimal('0.00'))

    def test_payment_after_period_end_does_not_count_in_the_month(self):
        """A July board must not be flattered by an August payment."""
        bill = self.make_bill(total='5000.00')
        self.pay(bill, '5000.00', date(2026, 8, 5))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount_paid, Decimal('0.00'))
        self.assertEqual(item.payment_status, PaymentStatus.UNPAID)

    def test_draft_payment_does_not_count(self):
        bill = self.make_bill(total='5000.00')
        payment = Payment.objects.create(
            payment_type='payment', contact=self.vendor, company=self.company,
            bank_account=self.bank, payment_date=date(2026, 7, 10),
            currency_code=self.bwp, amount=Decimal('5000.00'),
            payment_method='eft', status=Payment.Status.DRAFT,
            created_by=self.staff)
        PaymentAllocation.objects.create(payment=payment, invoice=bill,
                                         amount_allocated=Decimal('5000.00'))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount_paid, Decimal('0.00'))

    def test_foreign_currency_bill_is_converted_to_pula(self):
        self.make_bill(total='1000.00', rate='1.35000000')
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount, Decimal('1350.00'))

    def test_foreign_currency_paid_leg_uses_the_same_rate_as_the_billed_leg(self):
        """Both legs are in the bill's transaction currency, so both convert once.

        payments._update_invoice_from_allocations computes
        ``balance = invoice.total_amount - SUM(amount_allocated)``, which proves
        PaymentAllocation.amount_allocated is denominated in the INVOICE's
        currency, not in BWP. Converting only one leg — or treating the paid leg
        as already-BWP — would misstate what is still owed on every FX bill
        without raising an error. This test pins the invariant down.
        """
        bill = self.make_bill(total='1000.00', rate='1.35000000')
        self.pay(bill, '400.00', date(2026, 7, 15))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount, Decimal('1350.00'))       # 1000 @ 1.35
        self.assertEqual(item.amount_paid, Decimal('540.00'))   #  400 @ 1.35
        self.assertEqual(item.amount_outstanding, Decimal('810.00'))
        self.assertEqual(item.payment_status, PaymentStatus.PARTIALLY_PAID)

    def test_fully_settled_foreign_currency_bill_shows_nothing_outstanding(self):
        bill = self.make_bill(total='1000.00', rate='1.35000000')
        self.pay(bill, '1000.00', date(2026, 7, 15))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.amount_paid, item.amount)
        self.assertEqual(item.amount_outstanding, Decimal('0.00'))
        self.assertEqual(item.payment_status, PaymentStatus.PAID)

    def test_run_totals_equal_the_sum_of_its_items(self):
        self.make_bill(total='1000.00')
        b2 = self.make_bill(total='2500.50', issue_date=date(2026, 7, 5))
        self.pay(b2, '500.50', date(2026, 7, 15))
        run = self.build()
        items = InvoiceReconItem.objects.filter(line__run=run)
        self.assertEqual(run.total_invoiced,
                         sum(i.amount for i in items))
        self.assertEqual(run.total_paid, sum(i.amount_paid for i in items))
        self.assertEqual(run.total_unpaid,
                         run.total_invoiced - run.total_paid)


# ---------------------------------------------------------------------------
# Population — which bills belong to the month
# ---------------------------------------------------------------------------

class PopulationTests(ReconTestBase):

    def test_prior_month_bill_still_unpaid_is_carried_into_the_month(self):
        self.make_bill(total='800.00', issue_date=date(2026, 3, 10))
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 1)

    def test_prior_month_bill_settled_before_the_month_is_excluded(self):
        bill = self.make_bill(total='800.00', issue_date=date(2026, 3, 10),
                              status=Invoice.Status.PAID)
        self.pay(bill, '800.00', date(2026, 4, 2))
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)

    def test_prior_month_bill_settled_during_the_month_is_included(self):
        bill = self.make_bill(total='800.00', issue_date=date(2026, 3, 10),
                              status=Invoice.Status.PAID)
        self.pay(bill, '800.00', date(2026, 7, 9))
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 1)

    def test_bill_issued_after_the_month_is_excluded(self):
        self.make_bill(total='800.00', issue_date=date(2026, 9, 1))
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)

    def test_cancelled_bills_are_not_payables(self):
        self.make_bill(total='900.00', status=Invoice.Status.CANCELLED)
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)

    def test_rejected_bills_are_not_payables(self):
        self.make_bill(total='900.00', status=Invoice.Status.REJECTED)
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)


class DraftBillTests(ReconTestBase):
    """Every vendor bill in prod omni was still a draft on 2026-07-25 — raised,
    PO-matched, owed, but never posted. Excluding drafts would have shown the CFO
    an empty board and implied nothing was owed."""

    def test_draft_bill_appears_on_the_board_and_is_flagged(self):
        self.make_bill(total='800.00', status=Invoice.Status.DRAFT)
        run = self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.ledger_stage, LedgerStage.DRAFT)
        self.assertFalse(item.is_posted)
        self.assertEqual(item.amount, Decimal('800.00'))
        self.assertEqual(run.total_invoiced, Decimal('800.00'))

    def test_draft_value_is_totalled_separately_from_posted_value(self):
        self.make_bill(total='800.00', status=Invoice.Status.DRAFT)
        self.make_bill(total='200.00', status=Invoice.Status.POSTED,
                       issue_date=date(2026, 7, 2))
        run = self.build()
        self.assertEqual(run.total_invoiced, Decimal('1000.00'))
        self.assertEqual(run.total_not_posted, Decimal('800.00'))

    def test_pending_approval_bill_counts_as_not_posted(self):
        self.make_bill(total='500.00', status=Invoice.Status.PENDING_APPROVAL)
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.get().ledger_stage,
                         LedgerStage.DRAFT)
        self.assertEqual(run.total_not_posted, Decimal('500.00'))

    def test_a_draft_bill_always_needs_explaining(self):
        self.make_bill(total='800.00', status=Invoice.Status.DRAFT,
                       due_date=timezone.localdate() + timedelta(days=30))
        run = self.build()
        item = InvoiceReconItem.objects.get()
        self.assertTrue(item.needs_justification)
        problems = services.blocking_exceptions(run)
        self.assertEqual(len(problems), 1)
        self.assertIn('bill still in draft — never posted to the ledger',
                      problems[0]['problems'])

    def test_a_bill_marked_paid_with_no_journal_still_blocks_sign_off(self):
        """Posting state and payment state are independent.

        payments._update_invoice_from_allocations rewrites a bill's status to
        PAID by direct DB update as soon as an allocation confirms — even on a
        bill that was never posted. Keying off status would let unrecorded
        liability slip through looking settled; keying off journal_entry does
        not."""
        bill = self.make_bill(total='800.00', status=Invoice.Status.DRAFT,
                              posted=False)
        self.pay(bill, '800.00', date(2026, 7, 10))
        bill.refresh_from_db()
        self.assertEqual(bill.status, Invoice.Status.PAID)  # omni did this
        self.assertIsNone(bill.journal_entry_id)            # but never posted
        run = self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.payment_status, PaymentStatus.PAID)
        self.assertFalse(item.is_posted)
        self.assertTrue(item.needs_justification)
        self.assertEqual(len(services.blocking_exceptions(run)), 1)

    def test_bill_not_posted_reason_code_exists_and_escalates(self):
        rc = ReasonCode.objects.get(code='BILL_NOT_POSTED')
        self.assertTrue(rc.requires_escalation)

    def test_posted_subset_still_ties_to_ap_aging_population(self):
        """The AP Aging tie-out is a claim about the POSTED subset only."""
        self.make_bill(total='800.00', status=Invoice.Status.DRAFT)
        self.make_bill(total='200.00', status=Invoice.Status.POSTED,
                       issue_date=date(2026, 7, 2))
        run = self.build()
        posted = InvoiceReconItem.objects.filter(
            line__run=run, ledger_stage=LedgerStage.POSTED)
        self.assertEqual(sum(i.amount for i in posted), Decimal('200.00'))
        self.assertEqual(run.total_invoiced - run.total_not_posted,
                         Decimal('200.00'))

    def test_customer_invoices_are_never_pulled_in(self):
        Invoice.objects.create(
            invoice_type=Invoice.InvoiceType.CUSTOMER_INVOICE, contact=self.vendor,
            company=self.company, issue_date=JULY_START, currency_code=self.bwp,
            subtotal=Decimal('999.00'), total_amount=Decimal('999.00'),
            balance_due=Decimal('999.00'), status=Invoice.Status.POSTED,
            created_by=self.staff)
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)

    def test_another_entitys_bill_is_not_pulled_in(self):
        other_vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='ADSA Parts',
            currency_code=self.bwp, company=self.other_company)
        ReconSupplierProfile.objects.create(contact=other_vendor,
                                           category=SupplierCategory.PARTS)
        self.make_bill(total='4000.00', contact=other_vendor,
                       company=self.other_company)
        run = self.build()
        self.assertEqual(InvoiceReconItem.objects.filter(line__run=run).count(), 0)

    def test_unclassified_vendor_is_reported_not_silently_dropped(self):
        stranger = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Unknown Supplier',
            currency_code=self.bwp, company=self.company)
        self.make_bill(total='7000.00', contact=stranger)
        self.build()
        rows = list(services.unclassified_vendors(self.company, JULY_START, JULY_END))
        self.assertEqual([r['contact__name'] for r in rows], ['Unknown Supplier'])


# ---------------------------------------------------------------------------
# 3-way match
# ---------------------------------------------------------------------------

class ThreeWayMatchTests(ReconTestBase):

    def test_no_po_is_flagged(self):
        self.make_bill()
        self.build()
        self.assertEqual(InvoiceReconItem.objects.get().match_status,
                         MatchStatus.NO_PO)

    def test_claim_supplier_with_po_but_no_claim_reference_is_flagged(self):
        self.make_bill(purchase_order=self.make_po(claim_ref=''))
        self.build()
        self.assertEqual(InvoiceReconItem.objects.get().match_status,
                         MatchStatus.NO_CLAIM)

    def test_claim_reference_present_and_no_goods_receipt_still_matches(self):
        # CFO decision 2026-07-27: the match is 2-way (PO + claim ref), a goods
        # receipt is NOT required — Alpha Direct barely captures GRNs and
        # claims/service bills have no goods-receipt step. So a PO with a claim
        # reference and no variance is MATCHED even without a GRN.
        self.make_bill(purchase_order=self.make_po(claim_ref='CLM-2026-001234'))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.match_status, MatchStatus.MATCHED)
        self.assertEqual(item.claim_reference, 'CLM-2026-001234')
        self.assertIsNone(item.goods_receipt)

    def test_full_three_way_match(self):
        po = self.make_po(claim_ref='CLM-2026-001234')
        GoodsReceiptNote.objects.create(
            purchase_order=po, receipt_date=JULY_START, received_by=self.staff,
            status=GoodsReceiptNote.Status.POSTED, created_by=self.staff)
        self.make_bill(purchase_order=po)
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.match_status, MatchStatus.MATCHED)
        self.assertIsNotNone(item.goods_receipt)

    def test_a_goods_receipt_is_informational_and_does_not_gate_the_match(self):
        # Under the 2-way match a goods receipt (draft or otherwise) is linked
        # for information but never gates the match — PO + claim ref is enough.
        po = self.make_po(claim_ref='CLM-2026-001234')
        GoodsReceiptNote.objects.create(
            purchase_order=po, receipt_date=JULY_START, received_by=self.staff,
            status=GoodsReceiptNote.Status.DRAFT, created_by=self.staff)
        self.make_bill(purchase_order=po)
        self.build()
        self.assertEqual(InvoiceReconItem.objects.get().match_status,
                         MatchStatus.MATCHED)

    def test_general_supplier_does_not_need_a_claim_reference(self):
        office = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Office Depot',
            currency_code=self.bwp, company=self.company)
        ReconSupplierProfile.objects.create(contact=office,
                                           category=SupplierCategory.GENERAL)
        po = PurchaseOrder.objects.create(
            supplier=office, company=self.company,
            department=PurchaseOrder.Department.ADMIN, currency_code=self.bwp,
            issue_date=JULY_START, status=PurchaseOrder.Status.APPROVED,
            created_by=self.staff)
        GoodsReceiptNote.objects.create(
            purchase_order=po, receipt_date=JULY_START, received_by=self.staff,
            status=GoodsReceiptNote.Status.POSTED, created_by=self.staff)
        self.make_bill(contact=office, purchase_order=po)
        self.build()
        self.assertEqual(
            InvoiceReconItem.objects.get(line__supplier=office).match_status,
            MatchStatus.MATCHED)


# ---------------------------------------------------------------------------
# Ageing — must agree with the existing AP Aging report
# ---------------------------------------------------------------------------

class AgeingTests(ReconTestBase):

    def test_buckets_match_the_ap_aging_report(self):
        from reporting.reports import _aging_bucket
        today = timezone.localdate()
        for days in [0, 1, 30, 31, 60, 61, 90, 91, 120, 121, 400]:
            bill = self.make_bill(total='100.00',
                                  issue_date=date(2026, 7, 1),
                                  due_date=today - timedelta(days=days))
            item = InvoiceReconItem(line=None, invoice=bill,
                                    due_date=bill.due_date,
                                    amount=Decimal('100.00'),
                                    amount_paid=Decimal('0.00'),
                                    payment_status=PaymentStatus.UNPAID)
            with self.subTest(days=days):
                self.assertEqual(item.ageing_bucket, _aging_bucket(days))

    def test_not_yet_due_is_current_and_not_overdue(self):
        bill = self.make_bill(due_date=timezone.localdate() + timedelta(days=10))
        item = InvoiceReconItem(invoice=bill, due_date=bill.due_date,
                                amount=Decimal('1.00'),
                                payment_status=PaymentStatus.UNPAID)
        self.assertEqual(item.ageing_bucket, 'current')
        self.assertFalse(item.is_overdue)
        self.assertEqual(item.days_past_due, 0)


# ---------------------------------------------------------------------------
# Reason + justification rules
# ---------------------------------------------------------------------------

class ActionRuleTests(ReconTestBase):

    def setUp(self):
        self.make_bill(total='5000.00',
                       due_date=timezone.localdate() + timedelta(days=20))
        self.run = self.build()
        self.item = InvoiceReconItem.objects.get()

    def test_unpaid_bill_cannot_be_actioned_without_a_reason_code(self):
        with self.assertRaises(ValidationError) as ctx:
            services.action_item(self.item, self.staff, justification=LONG_REASON)
        self.assertIn('reason_code', ctx.exception.message_dict)

    def test_unpaid_bill_cannot_be_actioned_without_a_justification(self):
        with self.assertRaises(ValidationError) as ctx:
            services.action_item(self.item, self.staff,
                                reason_code=self.reason_dispute)
        self.assertIn('justification', ctx.exception.message_dict)

    def test_tick_box_justification_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            services.action_item(self.item, self.staff,
                                reason_code=self.reason_dispute,
                                justification='n/a')
        self.assertIn('justification', ctx.exception.message_dict)

    def test_valid_action_is_recorded_with_who_and_when(self):
        item = services.action_item(self.item, self.staff,
                                   reason_code=self.reason_dispute,
                                   justification=LONG_REASON)
        self.assertTrue(item.actioned)
        self.assertEqual(item.actioned_by, self.staff)
        self.assertIsNotNone(item.actioned_at)
        self.assertEqual(item.action_logs.count(), 1)

    def test_hold_sets_held_and_survives_a_rebuild(self):
        services.action_item(self.item, self.staff, hold=True,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.payment_status, PaymentStatus.HELD)
        self.assertEqual(item.reason_code, self.reason_dispute)
        self.assertEqual(item.justification, LONG_REASON)
        self.assertTrue(item.actioned)

    def test_releasing_a_hold_falls_back_to_what_the_money_says(self):
        services.action_item(self.item, self.staff, hold=True,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        item = services.action_item(InvoiceReconItem.objects.get(), self.staff,
                                   hold=False,
                                   reason_code=self.reason_dispute,
                                   justification=LONG_REASON)
        self.assertEqual(item.payment_status, PaymentStatus.UNPAID)

    def test_escalation_reason_code_auto_raises_an_escalation(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_escalate,
                            justification=LONG_REASON)
        self.assertEqual(Escalation.objects.count(), 1)

    def test_action_is_refused_once_the_run_is_finalised(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        services.finalise_run(self.run, self.reviewer)
        with self.assertRaises(ValidationError):
            services.action_item(InvoiceReconItem.objects.get(), self.staff,
                                reason_code=self.reason_dispute,
                                justification='A different explanation entirely.')


class OverdueEscalationTests(ReconTestBase):

    def test_overdue_bill_auto_escalates_on_action(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertTrue(item.is_overdue)
        self.assertTrue(item.requires_escalation)
        services.action_item(item, self.staff, reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        self.assertEqual(Escalation.objects.count(), 1)
        self.assertEqual(Escalation.objects.get().amount, Decimal('3000.00'))

    def test_second_action_does_not_stack_duplicate_escalations(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        self.build()
        item = InvoiceReconItem.objects.get()
        services.action_item(item, self.staff, reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        services.action_item(InvoiceReconItem.objects.get(), self.staff,
                            reason_code=self.reason_dispute,
                            justification='Still waiting on the supplier reply.')
        self.assertEqual(Escalation.objects.count(), 1)

    def test_escalation_needs_a_real_justification(self):
        self.make_bill()
        self.build()
        with self.assertRaises(ValidationError):
            services.raise_escalation(InvoiceReconItem.objects.get(),
                                     self.staff, 'x')


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------

class FinaliseTests(ReconTestBase):

    def setUp(self):
        self.make_bill(total='5000.00',
                       due_date=timezone.localdate() + timedelta(days=20))
        self.run = self.build()
        self.item = InvoiceReconItem.objects.get()

    def test_cannot_finalise_with_an_unexplained_bill(self):
        with self.assertRaises(ValidationError):
            services.finalise_run(self.run, self.reviewer)

    def test_blockers_name_the_bill_and_what_is_missing(self):
        problems = services.blocking_exceptions(self.run)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]['supplier'], 'Gaborone Panel Beaters')
        self.assertIn('not actioned by payables', problems[0]['problems'])

    def test_finalise_succeeds_once_everything_is_explained(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        run = services.finalise_run(self.run, self.reviewer)
        self.assertEqual(run.status, RunStatus.FINALISED)
        self.assertEqual(run.reviewed_by, self.reviewer)
        self.assertIsNotNone(run.finalised_at)

    def test_preparer_cannot_sign_off_their_own_run(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        with self.assertRaises(ValidationError) as ctx:
            services.finalise_run(self.run, self.staff)
        self.assertIn('Segregation of duties', str(ctx.exception))

    def test_fully_paid_bill_needs_no_explanation(self):
        bill = Invoice.objects.get()
        self.pay(bill, '5000.00', date(2026, 7, 20))
        run = self.build()
        self.assertEqual(services.blocking_exceptions(run), [])
        self.assertEqual(services.finalise_run(run, self.reviewer).status,
                         RunStatus.FINALISED)

    def test_escalation_worthy_bill_blocks_until_escalated(self):
        self.item.due_date = date(2026, 2, 1)
        self.item.save(skip_audit=True)
        problems = services.blocking_exceptions(self.run)
        self.assertIn('escalation required but none raised',
                      problems[0]['problems'])

    def test_rebuild_is_refused_while_finalised(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        services.finalise_run(self.run, self.reviewer)
        with self.assertRaises(ValidationError):
            self.build()

    def test_preparer_cannot_reopen_a_month_someone_else_signed_off(self):
        """Otherwise the lock is decorative: the preparer could reopen, change a
        decision, and have it re-signed."""
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        run = services.finalise_run(self.run, self.reviewer)
        with self.assertRaises(ValidationError) as ctx:
            services.reopen_run(
                run, self.staff,
                reason='I want to change what I wrote earlier about this bill.')
        self.assertIn('Segregation of duties', str(ctx.exception))
        # A different reviewer still can.
        self.assertEqual(
            services.reopen_run(
                run, self.reviewer,
                reason='Supplier produced the missing job card after sign-off.'
            ).status,
            RunStatus.REOPENED)

    def test_a_decision_must_be_attributable_to_a_person(self):
        """The nightly cron may build a run, but it may never record a decision:
        an unattributable payables decision is worthless."""
        with self.assertRaises(ValidationError):
            services.action_item(self.item, None,
                                reason_code=self.reason_dispute,
                                justification=LONG_REASON)
        with self.assertRaises(ValidationError):
            services.raise_escalation(self.item, None, LONG_REASON)
        with self.assertRaises(ValidationError):
            services.finalise_run(self.run, None)

    def test_the_nightly_build_may_still_run_with_no_user(self):
        run = services.build_recon_run(self.company, '2026-05')
        self.assertIsNone(run.prepared_by)

    def test_reopen_requires_a_written_reason(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        run = services.finalise_run(self.run, self.reviewer)
        with self.assertRaises(ValidationError):
            services.reopen_run(run, self.reviewer, reason='oops')
        run = services.reopen_run(
            run, self.reviewer,
            reason='Supplier produced the missing job card after sign-off.')
        self.assertEqual(run.status, RunStatus.REOPENED)


# ---------------------------------------------------------------------------
# Roll-ups and idempotency
# ---------------------------------------------------------------------------

class RollupTests(ReconTestBase):

    def test_line_status_walks_pending_to_cleared(self):
        bill = self.make_bill(total='1000.00',
                              due_date=timezone.localdate() + timedelta(days=5))
        run = self.build()
        line = run.lines.get()
        self.assertEqual(line.status, LineStatus.PENDING)
        self.assertEqual(line.unactioned_count, 1)

        services.action_item(InvoiceReconItem.objects.get(), self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        line.refresh_from_db()
        self.assertEqual(line.status, LineStatus.EXCEPTION)

        self.pay(bill, '1000.00', date(2026, 7, 20))
        run = self.build()
        self.assertEqual(run.lines.get().status, LineStatus.CLEARED)

    def test_rebuild_is_idempotent(self):
        self.make_bill(total='2000.00')
        first = self.build()
        counts = (SupplierReconRun.objects.count(), first.lines.count(),
                  InvoiceReconItem.objects.count())
        second = self.build()
        self.assertEqual(
            (SupplierReconRun.objects.count(), second.lines.count(),
             InvoiceReconItem.objects.count()), counts)
        self.assertEqual(first.pk, second.pk)

    def test_empty_month_builds_cleanly(self):
        run = self.build()
        self.assertEqual(run.lines.count(), 0)
        self.assertEqual(run.total_invoiced, Decimal('0.00'))


# ---------------------------------------------------------------------------
# API — access control and entity scoping
# ---------------------------------------------------------------------------

class ApiAccessTests(ReconTestBase):

    def setUp(self):
        self.make_bill(total='4000.00',
                       due_date=timezone.localdate() + timedelta(days=15))
        self.run = self.build()
        self.item = InvoiceReconItem.objects.get()

    def client_as(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_signed_out_is_refused(self):
        r = APIClient().get('/api/v1/supplier-recon/runs/')
        self.assertIn(r.status_code, (401, 403))

    def test_operations_staff_can_read_the_module(self):
        # CFO directive 2026-08-26: operations (and claims) staff may READ the
        # supplier recon module. Still scoped to the entities they may see.
        ops = User.objects.create_user('ops', password='x')
        UserProfile.objects.update_or_create(
            user=ops, defaults={'title': UserProfile.Title.OPERATIONS})
        UserCompanyAccess.objects.create(user=ops, company=self.company,
                                         can_view=True)
        r = self.client_as(ops).get('/api/v1/supplier-recon/runs/')
        self.assertEqual(r.status_code, 200)

    def test_operations_staff_still_cannot_finalise(self):
        # Read-only widening: prepare / sign-off stay with finance + owner.
        ops = User.objects.create_user('ops_ro', password='x')
        UserProfile.objects.update_or_create(
            user=ops, defaults={'title': UserProfile.Title.OPERATIONS})
        UserCompanyAccess.objects.create(user=ops, company=self.company,
                                         can_view=True)
        r = self.client_as(ops).post(
            f'/api/v1/supplier-recon/runs/{self.run.pk}/finalise/')
        self.assertEqual(r.status_code, 403)

    def test_payables_clerk_can_read_the_run(self):
        r = self.client_as(self.staff).get('/api/v1/supplier-recon/runs/')
        self.assertEqual(r.status_code, 200)

    def test_user_of_another_entity_cannot_read_the_run_by_id(self):
        outsider = User.objects.create_user('adsa_clerk', password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'title': UserProfile.Title.ACCOUNTANT})
        UserCompanyAccess.objects.create(user=outsider,
                                         company=self.other_company,
                                         can_view=True)
        r = self.client_as(outsider).get(
            f'/api/v1/supplier-recon/runs/{self.run.pk}/')
        self.assertEqual(r.status_code, 404)

    def test_other_entity_cannot_see_the_bill_items(self):
        outsider = User.objects.create_user('adsa2', password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'title': UserProfile.Title.ACCOUNTANT})
        UserCompanyAccess.objects.create(user=outsider,
                                         company=self.other_company,
                                         can_view=True)
        r = self.client_as(outsider).get('/api/v1/supplier-recon/items/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get('count', len(r.json().get('results', []))), 0)

    def test_clerk_cannot_finalise(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        r = self.client_as(self.staff).post(
            f'/api/v1/supplier-recon/runs/{self.run.pk}/finalise/')
        self.assertEqual(r.status_code, 403)

    def test_reviewer_can_finalise(self):
        services.action_item(self.item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        r = self.client_as(self.reviewer).post(
            f'/api/v1/supplier-recon/runs/{self.run.pk}/finalise/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], RunStatus.FINALISED)

    def test_action_endpoint_is_reachable_and_enforces_the_rule(self):
        url = f'/api/v1/supplier-recon/items/{self.item.pk}/action/'
        c = self.client_as(self.staff)
        bad = c.post(url, {'justification': 'too short'}, format='json')
        self.assertEqual(bad.status_code, 400)
        good = c.post(url, {'reason_code': str(self.reason_dispute.pk),
                            'justification': LONG_REASON}, format='json')
        self.assertEqual(good.status_code, 200, good.content)
        self.assertTrue(good.json()['actioned'])

    def test_dashboard_returns_tiles_that_match_the_run(self):
        r = self.client_as(self.staff).get(
            f'/api/v1/supplier-recon/runs/{self.run.pk}/dashboard/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(Decimal(body['kpis']['total_invoiced']),
                         Decimal('4000.00'))
        self.assertEqual(body['kpis']['invoice_count'], 1)
        self.assertEqual(body['kpis']['unactioned_count'], 1)

    def test_build_endpoint_refuses_an_entity_the_user_cannot_access(self):
        r = self.client_as(self.staff).post(
            '/api/v1/supplier-recon/runs/build/',
            {'company': str(self.other_company.pk), 'period_label': JULY},
            format='json')
        self.assertEqual(r.status_code, 403)

    def test_service_layer_blocks_a_wrong_entity_write_on_its_own(self):
        """Second lock: even bypassing the API, the engine refuses the entity."""
        outsider = User.objects.create_user('adsa3', password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'title': UserProfile.Title.ACCOUNTANT})
        UserCompanyAccess.objects.create(user=outsider,
                                         company=self.other_company,
                                         can_view=True, can_write=True)
        with self.assertRaises(ValidationError):
            services.action_item(self.item, outsider,
                                reason_code=self.reason_dispute,
                                justification=LONG_REASON)
        with self.assertRaises(ValidationError):
            services.raise_escalation(self.item, outsider, LONG_REASON)
        with self.assertRaises(ValidationError):
            services.finalise_run(self.run, outsider)
        with self.assertRaises(ValidationError):
            services.build_recon_run(self.company, JULY, prepared_by=outsider)

    def test_api_build_always_stamps_who_prepared_the_run(self):
        """Segregation of duties leans on prepared_by, so it must never be blank
        on anything built through the app."""
        r = self.client_as(self.staff).post(
            '/api/v1/supplier-recon/runs/build/',
            {'company': str(self.company.pk), 'period_label': '2026-06'},
            format='json')
        self.assertEqual(r.status_code, 201, r.content)
        built = SupplierReconRun.objects.get(company=self.company,
                                             period_label='2026-06')
        self.assertEqual(built.prepared_by, self.staff)
        # …and that same person is then refused sign-off.
        with self.assertRaises(ValidationError):
            services.finalise_run(built, self.staff)

    def test_reason_codes_are_listed(self):
        r = self.client_as(self.staff).get('/api/v1/supplier-recon/reason-codes/')
        self.assertEqual(r.status_code, 200)
        codes = {row['code'] for row in r.json()}
        # Seeded by migration 0002 — a deploy must never land with none.
        self.assertIn('QUERY_DISPUTE', codes)
        self.assertIn('SALVAGE_NOT_IN_YARD', codes)
        self.assertGreaterEqual(len(codes), 14)

    def test_salvage_not_in_yard_is_escalation_worthy(self):
        """CFO standing rule: salvage must be in the yard before settlement."""
        rc = ReasonCode.objects.get(code='SALVAGE_NOT_IN_YARD')
        self.assertTrue(rc.requires_escalation)


# ---------------------------------------------------------------------------
# Management commands — these had no coverage and the classifier broke on its
# first real prod run (FieldError: aggregate filter written from the wrong
# model). Both commands are now exercised end to end.
# ---------------------------------------------------------------------------

class ManagementCommandTests(ReconTestBase):

    def setUp(self):
        # A vendor with bills but no profile — the classifier's whole job.
        self.stranger = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Unclassified Motors',
            currency_code=self.bwp, company=self.company)
        self.make_bill(total='2500.00', contact=self.stranger)

    def _run(self, *args):
        out = StringIO()
        call_command('classify_recon_suppliers', *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_classifier_dry_run_reports_without_writing(self):
        before = ReconSupplierProfile.objects.count()
        out = self._run('--company', self.company.code, '--dry-run')
        self.assertIn('Unclassified Motors', out)
        self.assertIn('Would create', out)
        self.assertEqual(ReconSupplierProfile.objects.count(), before)

    def test_classifier_creates_a_profile_for_a_vendor_with_bills(self):
        self._run('--company', self.company.code)
        profile = ReconSupplierProfile.objects.get(contact=self.stranger)
        self.assertTrue(profile.in_scope)
        # No PO history for this vendor, so it must land on the conservative
        # side: `general` never raises a false "No Claim Authorisation".
        self.assertEqual(profile.category, SupplierCategory.GENERAL)

    def test_classifier_marks_a_claims_supplier_as_claim_backed(self):
        claims_vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Claims Panel Co',
            currency_code=self.bwp, company=self.company)
        self.make_bill(total='900.00', contact=claims_vendor)
        PurchaseOrder.objects.create(
            supplier=claims_vendor, company=self.company,
            department=PurchaseOrder.Department.CLAIMS, currency_code=self.bwp,
            issue_date=JULY_START, status=PurchaseOrder.Status.APPROVED,
            related_claim_reference='CLM-2026-777', created_by=self.staff)
        self._run('--company', self.company.code)
        self.assertEqual(
            ReconSupplierProfile.objects.get(contact=claims_vendor).category,
            SupplierCategory.CLAIMS_OTHER)

    def test_classifier_never_overwrites_a_human_classification(self):
        ReconSupplierProfile.objects.create(
            contact=self.stranger, category=SupplierCategory.PANEL_BEATER)
        self._run('--company', self.company.code)
        self.assertEqual(
            ReconSupplierProfile.objects.get(contact=self.stranger).category,
            SupplierCategory.PANEL_BEATER)

    def test_classifier_rejects_an_unknown_company(self):
        out = self._run('--company', 'NOPE')
        self.assertIn('No company', out)

    def test_build_command_builds_the_month(self):
        out = StringIO()
        call_command('build_supplier_recon', '--period', JULY,
                     '--company', self.company.code, stdout=out, stderr=out)
        self.assertTrue(SupplierReconRun.objects.filter(
            company=self.company, period_label=JULY).exists())

    def test_build_command_dry_run_writes_nothing(self):
        out = StringIO()
        call_command('build_supplier_recon', '--period', JULY,
                     '--company', self.company.code, '--dry-run',
                     stdout=out, stderr=out)
        self.assertIn('Dry run', out.getvalue())
        self.assertFalse(SupplierReconRun.objects.filter(
            company=self.company, period_label=JULY).exists())

    def test_build_command_skips_a_finalised_month(self):
        run = services.build_recon_run(self.company, JULY,
                                      prepared_by=self.staff)
        for item in InvoiceReconItem.objects.filter(line__run=run):
            services.action_item(item, self.staff,
                                reason_code=self.reason_dispute,
                                justification=LONG_REASON)
        services.finalise_run(run, self.reviewer)
        out = StringIO()
        call_command('build_supplier_recon', '--period', JULY,
                     '--company', self.company.code, stdout=out, stderr=out)
        self.assertIn('SKIP', out.getvalue())


# ---------------------------------------------------------------------------
# Excel export — payables take this to the auditors, so it must not crash on
# the awkward rows (no PO, no goods receipt, no reason yet, FX bill).
# ---------------------------------------------------------------------------

class ExportTests(ReconTestBase):

    def test_export_produces_a_real_workbook(self):
        from openpyxl import load_workbook
        from supplier_recon.export import build_workbook
        self.make_bill(total='4000.00')
        self.make_bill(total='1000.00', rate='1.35000000',
                       issue_date=date(2026, 7, 3))
        run = self.build()
        wb = load_workbook(build_workbook(run))
        ws = wb.active
        text = ' | '.join(
            str(c.value) for row in ws.iter_rows() for c in row if c.value)
        self.assertIn('Supplier Payables Reconciliation', text)
        self.assertIn('Gaborone Panel Beaters', text)
        self.assertIn('NOT POSTED', text)   # prod reality is draft bills

    def test_export_endpoint_streams_a_spreadsheet(self):
        self.make_bill(total='4000.00')
        run = self.build()
        UserCompanyAccess.objects.get_or_create(
            user=self.staff, company=self.company,
            defaults={'can_view': True, 'can_write': True})
        c = APIClient()
        c.force_authenticate(user=self.staff)
        r = c.get(f'/api/v1/supplier-recon/runs/{run.pk}/export/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheet', r['Content-Type'])
        self.assertIn('.xlsx', r['Content-Disposition'])

    def test_export_is_refused_across_entities(self):
        self.make_bill(total='4000.00')
        run = self.build()
        outsider = User.objects.create_user('adsa_export', password='x')
        UserProfile.objects.update_or_create(
            user=outsider, defaults={'title': UserProfile.Title.ACCOUNTANT})
        UserCompanyAccess.objects.create(user=outsider,
                                         company=self.other_company,
                                         can_view=True)
        c = APIClient()
        c.force_authenticate(user=outsider)
        r = c.get(f'/api/v1/supplier-recon/runs/{run.pk}/export/')
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# Regression tests for the Fable 5 review (2026-07-25).
# ---------------------------------------------------------------------------

class ClaimGateConstantTests(ReconTestBase):
    """The claim-authorisation gate was INERT on prod: classify_recon_suppliers
    seeded claims-heavy vendors as `other`, which was not in
    CLAIM_BACKED_CATEGORIES, so `no_claim` could never fire. These tests pin the
    CONSTANT, not just the happy-path category."""

    def test_every_category_the_classifier_assigns_is_honoured(self):
        from supplier_recon.constants import CLAIM_BACKED_CATEGORIES
        # The exact value classify_recon_suppliers writes for a claims vendor.
        self.assertIn(SupplierCategory.CLAIMS_OTHER, CLAIM_BACKED_CATEGORIES)

    def test_plain_other_is_not_claim_backed(self):
        """A human picking "Other" for a stationery supplier must not inherit
        the claim rule and start raising false alarms."""
        from supplier_recon.constants import CLAIM_BACKED_CATEGORIES
        self.assertNotIn(SupplierCategory.OTHER, CLAIM_BACKED_CATEGORIES)
        self.assertNotIn(SupplierCategory.GENERAL, CLAIM_BACKED_CATEGORIES)

    def test_a_claims_supplier_with_no_claim_reference_is_flagged(self):
        """End to end, through the category the classifier actually assigns."""
        ReconSupplierProfile.objects.filter(contact=self.vendor).update(
            category=SupplierCategory.CLAIMS_OTHER)
        self.make_bill(purchase_order=self.make_po(claim_ref=''))
        self.build()
        self.assertEqual(InvoiceReconItem.objects.get().match_status,
                         MatchStatus.NO_CLAIM)

    def test_the_classifier_assigns_a_claim_backed_category(self):
        from supplier_recon.constants import CLAIM_BACKED_CATEGORIES
        claims_vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Gate Test Panel',
            currency_code=self.bwp, company=self.company)
        self.make_bill(total='500.00', contact=claims_vendor)
        PurchaseOrder.objects.create(
            supplier=claims_vendor, company=self.company,
            department=PurchaseOrder.Department.CLAIMS, currency_code=self.bwp,
            issue_date=JULY_START, status=PurchaseOrder.Status.APPROVED,
            related_claim_reference='CLM-2026-999', created_by=self.staff)
        out = StringIO()
        call_command('classify_recon_suppliers', '--company', self.company.code,
                     stdout=out, stderr=out)
        profile = ReconSupplierProfile.objects.get(contact=claims_vendor)
        self.assertIn(profile.category, CLAIM_BACKED_CATEGORIES)


class EscalationDedupeTests(ReconTestBase):

    def test_escalating_twice_does_not_double_count_the_exposure(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        run = self.build()
        item = InvoiceReconItem.objects.get()
        services.raise_escalation(item, self.staff, LONG_REASON)
        services.raise_escalation(InvoiceReconItem.objects.get(), self.staff,
                                 'A second click on the Escalate button.')
        self.assertEqual(Escalation.objects.count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.total_escalated, Decimal('3000.00'))

    def test_a_resolved_escalation_can_be_raised_again(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        self.build()
        item = InvoiceReconItem.objects.get()
        esc = services.raise_escalation(item, self.staff, LONG_REASON)
        services.resolve_escalation(
            esc, self.reviewer, status='resolved',
            note='Supplier finally produced the signed job card.')
        services.raise_escalation(InvoiceReconItem.objects.get(), self.staff,
                                 'It has gone wrong a second time this month.')
        self.assertEqual(Escalation.objects.count(), 2)


class RebuildDoesNotClobberDecisionsTests(ReconTestBase):

    def test_a_rebuild_writes_observed_facts_only(self):
        """A rebuild racing a clerk must not wipe their reason/justification."""
        self.make_bill(total='5000.00',
                       due_date=timezone.localdate() + timedelta(days=20))
        self.build()
        item = InvoiceReconItem.objects.get()
        services.action_item(item, self.staff,
                            reason_code=self.reason_dispute,
                            justification=LONG_REASON)
        self.build()
        after = InvoiceReconItem.objects.get()
        self.assertTrue(after.actioned)
        self.assertEqual(after.reason_code, self.reason_dispute)
        self.assertEqual(after.justification, LONG_REASON)
        self.assertEqual(after.actioned_by, self.staff)


class UnclassifiedVendorCurrencyTests(ReconTestBase):

    def test_unclassified_totals_are_converted_to_pula(self):
        """The figure is rendered with a P sign, so it must not be raw Rand."""
        stranger = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='FX Stranger',
            currency_code=self.bwp, company=self.company)
        self.make_bill(total='1000.00', rate='1.35000000', contact=stranger)
        rows = list(services.unclassified_vendors(
            self.company, JULY_START, JULY_END))
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]['invoiced']).quantize(Decimal('0.01')),
                         Decimal('1350.00'))
        self.assertEqual(rows[0]['bills'], 1)


class BuildEndpointRobustnessTests(ReconTestBase):

    def setUp(self):
        self.client_ = APIClient()
        self.client_.force_authenticate(user=self.staff)

    def test_building_by_company_code_works_and_does_not_500(self):
        r = self.client_.post('/api/v1/supplier-recon/runs/build/',
                              {'company': self.company.code,
                               'period_label': JULY}, format='json')
        self.assertEqual(r.status_code, 201, r.content)

    def test_an_unknown_company_string_is_a_clean_400(self):
        r = self.client_.post('/api/v1/supplier-recon/runs/build/',
                              {'company': 'not-a-company',
                               'period_label': JULY}, format='json')
        self.assertEqual(r.status_code, 400, r.content)


# ---------------------------------------------------------------------------
# Ownership — CFO directive 2026-07-25: the board is owned by Bharath.
# ---------------------------------------------------------------------------

class OwnershipTests(ReconTestBase):

    def setUp(self):
        self.owner = User.objects.create_user(
            'board_owner', password='x', first_name='Board', last_name='Owner')
        UserProfile.objects.update_or_create(
            user=self.owner,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER})
        ReconOwner.objects.create(company=self.company, owner=self.owner)

    def test_a_new_run_is_stamped_with_the_owner(self):
        self.make_bill(total='1000.00')
        run = self.build()
        self.assertEqual(run.owner, self.owner)

    def test_a_refresh_picks_up_a_change_of_owner(self):
        self.make_bill(total='1000.00')
        self.build()
        new_owner = User.objects.create_user('owner2', password='x')
        ReconOwner.objects.filter(company=self.company).update(owner=new_owner)
        run = self.build()
        self.assertEqual(run.owner, new_owner)

    def test_escalations_default_to_the_board_owner(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        self.build()
        esc = services.raise_escalation(InvoiceReconItem.objects.get(),
                                      self.staff, LONG_REASON)
        self.assertEqual(esc.raised_to, self.owner)

    def test_an_explicit_recipient_still_wins(self):
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        self.build()
        esc = services.raise_escalation(InvoiceReconItem.objects.get(),
                                      self.staff, LONG_REASON,
                                      raised_to=self.reviewer)
        self.assertEqual(esc.raised_to, self.reviewer)

    def test_no_owner_configured_is_not_an_error(self):
        """An entity with no owner must still build and still escalate."""
        ReconOwner.objects.filter(company=self.company).delete()
        self.make_bill(total='3000.00', issue_date=date(2026, 1, 5),
                       due_date=date(2026, 2, 5))
        run = self.build()
        self.assertIsNone(run.owner)
        esc = services.raise_escalation(InvoiceReconItem.objects.get(),
                                      self.staff, LONG_REASON)
        self.assertIsNone(esc.raised_to)

    def test_ownership_carries_the_board_but_never_sign_off(self):
        """Bharath is Operations Manager in training, so ownership must carry
        view + prepare on its own — otherwise correcting his omni title would
        lock him out of the board he owns. It must NEVER carry sign-off:
        locking a payables month stays a finance act."""
        ops = User.objects.create_user('ops_only_owner', password='x')
        UserProfile.objects.update_or_create(
            user=ops, defaults={'title': UserProfile.Title.OPERATIONS})
        from supplier_recon.permissions import (can_prepare_recon,
                                                can_review_recon,
                                                can_view_recon)
        # Operations staff may now READ by title (CFO 2026-08-26), but preparing
        # the board still needs a payables title or board ownership.
        self.assertTrue(can_view_recon(ops))
        self.assertFalse(can_prepare_recon(ops))
        ReconOwner.objects.filter(company=self.company).update(owner=ops)
        self.assertTrue(can_view_recon(ops))
        self.assertTrue(can_prepare_recon(ops))
        self.assertFalse(can_review_recon(ops))

    def test_the_api_reports_the_owner(self):
        self.make_bill(total='1000.00')
        run = self.build()
        c = APIClient()
        c.force_authenticate(user=self.staff)
        r = c.get(f'/api/v1/supplier-recon/runs/{run.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['owner_name'], 'Board Owner')


# ---------------------------------------------------------------------------
# Name-based trade inference (explicitly an assumption, per CFO 2026-07-25)
# ---------------------------------------------------------------------------

class TradeInferenceTests(TestCase):

    def test_names_map_to_the_expected_trade(self):
        from supplier_recon.management.commands.refine_supplier_types import infer
        cases = {
            'Optimum Panel beaters': SupplierCategory.PANEL_BEATER,
            'SPECIALISED PANEL BEATERS': SupplierCategory.PANEL_BEATER,
            'PG GLASS GABS': SupplierCategory.GLASS,
            'PG WINDSCREENS': SupplierCategory.GLASS,
            'Auto Glassworks': SupplierCategory.GLASS,
            'Mancon Windscreen Centre (Pty) Ltd': SupplierCategory.GLASS,
            'COMMERCIAL AUTO GLASS': SupplierCategory.GLASS,
            'SMG TOYOTA CRESTA': SupplierCategory.PANEL_BEATER,
            'CFAO MOBILITY (PTY) LTD': SupplierCategory.PANEL_BEATER,
            'Korean Auto Service': SupplierCategory.PANEL_BEATER,
            'Rolling Wheels (Pty) Ltd': SupplierCategory.PANEL_BEATER,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(infer(name), expected)

    def test_glass_beats_the_generic_motor_rule(self):
        """Ordering matters: 'COMMERCIAL AUTO GLASS' contains both 'auto' and
        'glass' and must land on glass."""
        from supplier_recon.management.commands.refine_supplier_types import infer
        self.assertEqual(infer('COMMERCIAL AUTO GLASS'), SupplierCategory.GLASS)

    def test_an_unrecognisable_name_is_left_alone(self):
        from supplier_recon.management.commands.refine_supplier_types import infer
        self.assertIsNone(infer('ZEYNE ENTERPRISES PTY LTD'))
        self.assertIsNone(infer('CUBIX PROJECTS'))

    def test_a_non_motor_services_company_is_not_called_a_panel_beater(self):
        """A bare "services" keyword caught mining and cleaning contractors."""
        from supplier_recon.management.commands.refine_supplier_types import infer
        self.assertIsNone(infer('TATSAND MINING SERVICES'))
        self.assertIsNone(infer('NATIVE EVENTS'))
        # ...while a real motor name still resolves.
        self.assertEqual(infer('Korean Auto Service'),
                         SupplierCategory.PANEL_BEATER)


class RefineCommandTests(ReconTestBase):

    def test_the_command_narrows_and_records_it_as_an_assumption(self):
        vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR,
            name='Kgale Panel Beaters (Pty) Ltd',
            currency_code=self.bwp, company=self.company)
        ReconSupplierProfile.objects.create(
            contact=vendor, category=SupplierCategory.CLAIMS_OTHER,
            notes='Seeded by classify_recon_suppliers from PO history.')
        out = StringIO()
        call_command('refine_supplier_types', '--company', self.company.code,
                     stdout=out, stderr=out)
        profile = ReconSupplierProfile.objects.get(contact=vendor)
        self.assertEqual(profile.category, SupplierCategory.PANEL_BEATER)
        self.assertIn('assumption', profile.notes)

    def test_it_never_touches_a_category_a_human_set(self):
        vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Human Set Motors',
            currency_code=self.bwp, company=self.company)
        ReconSupplierProfile.objects.create(
            contact=vendor, category=SupplierCategory.CLAIMS_OTHER,
            notes='')  # no seeder marker => a person owns this row
        call_command('refine_supplier_types', '--company', self.company.code,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(
            ReconSupplierProfile.objects.get(contact=vendor).category,
            SupplierCategory.CLAIMS_OTHER)

    def test_dry_run_changes_nothing(self):
        vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Dry Run Motors',
            currency_code=self.bwp, company=self.company)
        ReconSupplierProfile.objects.create(
            contact=vendor, category=SupplierCategory.CLAIMS_OTHER,
            notes='Seeded by classify_recon_suppliers from PO history.')
        call_command('refine_supplier_types', '--company', self.company.code,
                     '--dry-run', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(
            ReconSupplierProfile.objects.get(contact=vendor).category,
            SupplierCategory.CLAIMS_OTHER)

    def test_every_inferred_trade_stays_claim_backed(self):
        """Narrowing must never move a supplier OUT of the claim rule."""
        from supplier_recon.constants import CLAIM_BACKED_CATEGORIES
        from supplier_recon.management.commands.refine_supplier_types import RULES
        for category, _pattern in RULES:
            with self.subTest(category=category):
                self.assertIn(category, CLAIM_BACKED_CATEGORIES)


# ---------------------------------------------------------------------------
# Duplicate vendor pruning — deletes live master data, so tested hard.
# ---------------------------------------------------------------------------

class DuplicateVendorPruneTests(ReconTestBase):

    def _vendor(self, name, company=None):
        return Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name=name,
            currency_code=self.bwp, company=company or self.company)

    def test_name_normalisation_folds_case_punctuation_and_suffixes(self):
        from supplier_recon.management.commands.prune_duplicate_vendors import normalise
        self.assertEqual(normalise('Rolling Wheels (Pty) Ltd'),
                         normalise('Rolling Wheels (Pty) Ltd.'))
        self.assertEqual(normalise('BB MOTORS'), normalise('Bb Motors (Pty) Ltd'))
        self.assertNotEqual(normalise('BB Motors'), normalise('CC Motors'))

    def test_an_empty_duplicate_in_the_same_entity_is_deleted(self):
        busy = self._vendor('Rolling Wheels (Pty) Ltd')
        self.make_bill(total='900.00', contact=busy)
        empty = self._vendor('Rolling Wheels (Pty) Ltd.')
        call_command('prune_duplicate_vendors', '--company', self.company.code,
                     '--apply', stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=busy.pk).exists())
        self.assertFalse(Contact.objects.filter(pk=empty.pk).exists())

    def test_a_duplicate_WITH_transactions_is_never_deleted(self):
        a = self._vendor('Twin Motors')
        self.make_bill(total='100.00', contact=a)
        b = self._vendor('Twin Motors (Pty) Ltd')
        self.make_bill(total='200.00', contact=b, issue_date=date(2026, 7, 2))
        call_command('prune_duplicate_vendors', '--company', self.company.code,
                     '--apply', stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=a.pk).exists())
        self.assertTrue(Contact.objects.filter(pk=b.pk).exists())

    def test_the_same_name_in_another_entity_is_not_a_duplicate(self):
        """Vendors legitimately exist once per entity - CFO 2026-05-18."""
        mine = self._vendor('Cross Entity Motors')
        self.make_bill(total='500.00', contact=mine)
        theirs = self._vendor('Cross Entity Motors', company=self.other_company)
        call_command('prune_duplicate_vendors', '--apply',
                     stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=mine.pk).exists())
        self.assertTrue(Contact.objects.filter(pk=theirs.pk).exists())

    def test_dry_run_deletes_nothing(self):
        busy = self._vendor('Dry Motors')
        self.make_bill(total='900.00', contact=busy)
        empty = self._vendor('Dry Motors (Pty) Ltd')
        out = StringIO()
        call_command('prune_duplicate_vendors', '--company', self.company.code,
                     stdout=out, stderr=out)
        self.assertIn('would delete', out.getvalue())
        self.assertTrue(Contact.objects.filter(pk=empty.pk).exists())

    def test_a_unique_vendor_is_untouched(self):
        solo = self._vendor('Only One Of Me')
        call_command('prune_duplicate_vendors', '--company', self.company.code,
                     '--apply', stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=solo.pk).exists())

    def test_a_recon_profile_counts_as_a_transaction(self):
        """A classified duplicate is referenced by this module, so it stays."""
        busy = self._vendor('Profiled Motors')
        self.make_bill(total='900.00', contact=busy)
        dup = self._vendor('Profiled Motors (Pty) Ltd')
        ReconSupplierProfile.objects.create(
            contact=dup, category=SupplierCategory.PANEL_BEATER)
        call_command('prune_duplicate_vendors', '--company', self.company.code,
                     '--apply', stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=dup.pk).exists())


# ---------------------------------------------------------------------------
# Merging duplicates that BOTH carry transactions (CFO: "merge", 2026-07-25).
# This rewrites live financial references, so it is tested hard.
# ---------------------------------------------------------------------------

class MergeDuplicateVendorTests(ReconTestBase):

    def _vendor(self, name, company=None):
        return Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name=name,
            currency_code=self.bwp, company=company or self.company)

    def test_bills_move_to_the_survivor_and_the_loser_goes(self):
        big = self._vendor('Merge Motors')
        self.make_bill(total='900.00', contact=big)
        self.make_bill(total='800.00', contact=big, issue_date=date(2026, 7, 2))
        small = self._vendor('Merge Motors (Pty) Ltd')
        moved = self.make_bill(total='100.00', contact=small,
                               issue_date=date(2026, 7, 3))
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        moved.refresh_from_db()
        self.assertEqual(moved.contact_id, big.id)
        self.assertFalse(Contact.objects.filter(pk=small.pk).exists())
        self.assertEqual(Invoice.objects.filter(contact=big).count(), 3)

    def test_no_bill_is_lost_in_the_merge(self):
        a = self._vendor('Total Motors')
        self.make_bill(total='500.00', contact=a)
        b = self._vendor('Total Motors Ltd')
        self.make_bill(total='250.00', contact=b, issue_date=date(2026, 7, 4))
        before = Invoice.objects.count()
        before_value = sum(i.total_amount for i in Invoice.objects.all())
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(Invoice.objects.count(), before)
        self.assertEqual(sum(i.total_amount for i in Invoice.objects.all()),
                         before_value)

    def test_purchase_orders_move_too(self):
        """The invariant is that the PO ends up on whichever row survives — not
        on a row I picked in advance. The survivor is chosen by history, and a
        PO counts as history."""
        a = self._vendor('PO Motors')
        self.make_bill(total='900.00', contact=a)
        b = self._vendor('PO Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=b, issue_date=date(2026, 7, 2))
        po = PurchaseOrder.objects.create(
            supplier=b, company=self.company,
            department=PurchaseOrder.Department.CLAIMS, currency_code=self.bwp,
            issue_date=JULY_START, status=PurchaseOrder.Status.APPROVED,
            created_by=self.staff)
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        remaining = Contact.objects.filter(pk__in=[a.pk, b.pk])
        self.assertEqual(remaining.count(), 1, 'exactly one row should survive')
        survivor = remaining.first()
        po.refresh_from_db()
        self.assertEqual(po.supplier_id, survivor.id)
        # And every bill from both rows now hangs off that survivor.
        self.assertEqual(Invoice.objects.filter(contact=survivor).count(), 2)

    def test_the_row_with_the_purchase_order_wins_when_bills_are_equal(self):
        """A PO is history too, so it decides the survivor on a tie."""
        plain = self._vendor('Tie Break Motors')
        self.make_bill(total='500.00', contact=plain)
        with_po = self._vendor('Tie Break Motors (Pty) Ltd')
        self.make_bill(total='500.00', contact=with_po,
                       issue_date=date(2026, 7, 2))
        PurchaseOrder.objects.create(
            supplier=with_po, company=self.company,
            department=PurchaseOrder.Department.CLAIMS, currency_code=self.bwp,
            issue_date=JULY_START, status=PurchaseOrder.Status.APPROVED,
            created_by=self.staff)
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=with_po.pk).exists())
        self.assertFalse(Contact.objects.filter(pk=plain.pk).exists())

    def test_the_survivor_is_the_row_with_more_history(self):
        small = self._vendor('Busy Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=small)
        big = self._vendor('Busy Motors')
        for i, amt in enumerate(['900.00', '800.00', '700.00']):
            self.make_bill(total=amt, contact=big,
                           issue_date=date(2026, 7, 2 + i))
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=big.pk).exists())
        self.assertFalse(Contact.objects.filter(pk=small.pk).exists())

    def test_a_cross_entity_namesake_is_never_merged(self):
        mine = self._vendor('Shared Name Motors')
        self.make_bill(total='500.00', contact=mine)
        theirs = self._vendor('Shared Name Motors', company=self.other_company)
        call_command('merge_duplicate_vendors', '--apply',
                     '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Contact.objects.filter(pk=mine.pk).exists())
        self.assertTrue(Contact.objects.filter(pk=theirs.pk).exists())

    def test_dry_run_moves_nothing(self):
        big = self._vendor('Dry Merge Motors')
        self.make_bill(total='900.00', contact=big)
        small = self._vendor('Dry Merge Motors (Pty) Ltd')
        bill = self.make_bill(total='100.00', contact=small,
                              issue_date=date(2026, 7, 2))
        out = StringIO()
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--journal', os.devnull, stdout=out, stderr=out)
        self.assertIn('would merge', out.getvalue())
        bill.refresh_from_db()
        self.assertEqual(bill.contact_id, small.id)
        self.assertTrue(Contact.objects.filter(pk=small.pk).exists())

    def test_a_recon_profile_on_the_loser_is_dropped_not_blocked(self):
        """Profiles are derived data, rebuilt from source - they must not stop a
        merge, and must not collide with the survivor's own profile."""
        big = self._vendor('Profiled Merge Motors')
        self.make_bill(total='900.00', contact=big)
        ReconSupplierProfile.objects.create(
            contact=big, category=SupplierCategory.PANEL_BEATER)
        small = self._vendor('Profiled Merge Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=small, issue_date=date(2026, 7, 2))
        ReconSupplierProfile.objects.create(
            contact=small, category=SupplierCategory.PARTS)
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        self.assertFalse(Contact.objects.filter(pk=small.pk).exists())
        self.assertEqual(
            ReconSupplierProfile.objects.filter(contact=big).count(), 1)

    def test_the_classification_survives_the_merge(self):
        """Dropping the loser's profile when the survivor had none took the
        supplier out of scope and its bills silently off the board — P41,989.02
        vanished on the first prod run (2026-07-25). The classification must be
        carried onto whichever row survives.

        The survivor is NOT predicted here: adding a profile itself counts as a
        reference and can flip the outcome. The invariant is simply that exactly
        one row remains and it is still classified.
        """
        a = self._vendor('Carry Motors')
        self.make_bill(total='900.00', contact=a)
        b = self._vendor('Carry Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=b, issue_date=date(2026, 7, 2))
        # Exactly ONE of the two carries the classification.
        ReconSupplierProfile.objects.create(
            contact=b, category=SupplierCategory.PANEL_BEATER)
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        remaining = Contact.objects.filter(pk__in=[a.pk, b.pk])
        self.assertEqual(remaining.count(), 1, 'exactly one row should survive')
        survivor = remaining.first()
        profile = ReconSupplierProfile.objects.filter(contact=survivor).first()
        self.assertIsNotNone(profile, 'the classification was lost in the merge')
        self.assertEqual(profile.category, SupplierCategory.PANEL_BEATER)
        # …and both bills are still on the board through that survivor.
        self.assertEqual(Invoice.objects.filter(contact=survivor).count(), 2)

    def test_no_value_leaves_the_board_when_duplicates_merge(self):
        """The whole point: a merge tidies names, it never changes the money."""
        a = self._vendor('Value Motors')
        self.make_bill(total='900.00', contact=a)
        ReconSupplierProfile.objects.create(
            contact=a, category=SupplierCategory.PANEL_BEATER)
        b = self._vendor('Value Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=b, issue_date=date(2026, 7, 2))
        ReconSupplierProfile.objects.create(
            contact=b, category=SupplierCategory.PANEL_BEATER)
        before = self.build().total_invoiced
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        after = self.build()
        self.assertEqual(after.total_invoiced, before)
        self.assertEqual(after.total_invoiced,
                         sum(i.amount for i in
                             InvoiceReconItem.objects.filter(line__run=after)))

    def test_the_board_still_ties_after_a_merge(self):
        big = self._vendor('Tie Motors')
        self.make_bill(total='900.00', contact=big)
        small = self._vendor('Tie Motors (Pty) Ltd')
        self.make_bill(total='100.00', contact=small, issue_date=date(2026, 7, 2))
        ReconSupplierProfile.objects.create(
            contact=big, category=SupplierCategory.PANEL_BEATER)
        call_command('merge_duplicate_vendors', '--company', self.company.code,
                     '--apply', '--journal', os.devnull,
                     stdout=StringIO(), stderr=StringIO())
        run = self.build()
        items = InvoiceReconItem.objects.filter(line__run=run)
        self.assertEqual(run.total_invoiced, sum(i.amount for i in items))
        self.assertEqual(run.total_invoiced,
                         sum(l.invoiced for l in run.lines.all()))


class OwnerAccessTests(ReconTestBase):
    """Bharath is Operations Manager in training, so board ownership must carry
    access on its own - see reference_bharath_role."""

    def setUp(self):
        self.ops = User.objects.create_user('ops_owner', password='x')
        UserProfile.objects.update_or_create(
            user=self.ops, defaults={'title': UserProfile.Title.OPERATIONS})
        UserCompanyAccess.objects.create(user=self.ops, company=self.company,
                                         can_view=True, can_write=True)

    def test_an_operations_user_can_view_but_not_prepare_without_ownership(self):
        # CFO directive 2026-08-26: operations staff read the module by title;
        # preparing it still needs ownership (or a payables title).
        from supplier_recon.permissions import (can_prepare_recon,
                                                can_view_recon)
        self.assertTrue(can_view_recon(self.ops))
        self.assertFalse(can_prepare_recon(self.ops))

    def test_the_board_owner_can_view_and_prepare_despite_an_ops_title(self):
        from supplier_recon.permissions import (can_prepare_recon,
                                                can_view_recon)
        ReconOwner.objects.create(company=self.company, owner=self.ops)
        self.assertTrue(can_view_recon(self.ops))
        self.assertTrue(can_prepare_recon(self.ops))

    def test_the_owner_still_cannot_sign_off_a_month(self):
        from supplier_recon.permissions import can_review_recon
        ReconOwner.objects.create(company=self.company, owner=self.ops)
        self.assertFalse(can_review_recon(self.ops))

    def test_the_owner_is_refused_sign_off_through_the_api(self):
        ReconOwner.objects.create(company=self.company, owner=self.ops)
        self.make_bill(total='1000.00')
        run = self.build()
        c = APIClient()
        c.force_authenticate(user=self.ops)
        self.assertEqual(c.get('/api/v1/supplier-recon/runs/').status_code, 200)
        r = c.post(f'/api/v1/supplier-recon/runs/{run.pk}/finalise/')
        self.assertEqual(r.status_code, 403)


class OpsClaimsViewPolicyTests(ReconTestBase):
    """CFO directive 2026-08-26: operations staff — and the claims team — may
    READ the supplier recon module (they liaise with payables over it). It is a
    VIEW-only widening: no prepare, no sign-off, and no other financials."""

    def _user_with_title(self, username, title):
        u = User.objects.create_user(username, password='x')
        UserProfile.objects.update_or_create(user=u, defaults={'title': title})
        return u

    def test_operations_and_claims_titles_may_view_but_not_write(self):
        from supplier_recon.permissions import (can_prepare_recon,
                                                can_review_recon,
                                                can_view_recon)
        for name, title in (
            ('ops_view',   UserProfile.Title.OPERATIONS),
            ('clm_lead',   UserProfile.Title.CLAIMS_TEAM_LEADER),
            ('clm_snr',    UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE),
            ('clm_jnr',    UserProfile.Title.JUNIOR_CLAIMS_ASSOCIATE),
            ('clm_intern', UserProfile.Title.CLAIMS_INTERN),
        ):
            u = self._user_with_title(name, title)
            self.assertTrue(can_view_recon(u), f'{title} should be able to view')
            self.assertFalse(can_prepare_recon(u), f'{title} must not prepare')
            self.assertFalse(can_review_recon(u), f'{title} must not sign off')

    def test_this_widening_does_not_open_the_rest_of_the_financials(self):
        # The whole point of scoping it in supplier_recon (not core's
        # FINANCIALS_VIEW_TITLES) is that GL / reports / dashboards stay closed.
        u = self._user_with_title('ops_no_fin', UserProfile.Title.OPERATIONS)
        self.assertFalse(u.profile.can_view_financials)


# ---------------------------------------------------------------------------
# Due-status colouring + the 30-day escalation tier (Bharath 2026-07-27)
# ---------------------------------------------------------------------------

class DueStatusTests(ReconTestBase):
    """A payment not yet due reads green; overdue reads red; 30+ days past due
    is the serious tier that must be explained and escalated. The 'overdue by'
    figure is cached per supplier so the board can show it."""

    def _item_due(self, days_offset):
        """A single bill due `days_offset` days from today (negative = overdue).
        Issued well in the past so an overdue bill is not issued after its own
        due date."""
        self.make_bill(total='1000.00', issue_date=date(2026, 5, 1),
                       due_date=timezone.localdate() + timedelta(days=days_offset))
        self.build()
        return InvoiceReconItem.objects.get()

    def test_not_yet_due_bill_is_green_and_does_not_escalate(self):
        item = self._item_due(15)
        self.assertEqual(item.due_state, 'not_due')
        self.assertFalse(item.is_overdue)
        self.assertFalse(item.requires_escalation)

    def test_overdue_under_30_days_is_red_but_does_not_escalate(self):
        item = self._item_due(-10)
        self.assertEqual(item.due_state, 'overdue')
        self.assertTrue(item.is_overdue)
        self.assertFalse(item.requires_escalation)

    def test_exactly_30_days_past_due_is_the_escalation_threshold(self):
        item = self._item_due(-30)
        self.assertEqual(item.days_past_due, 30)
        self.assertEqual(item.due_state, 'overdue_30')
        self.assertTrue(item.requires_escalation)

    def test_well_overdue_bill_escalates(self):
        item = self._item_due(-45)
        self.assertEqual(item.due_state, 'overdue_30')
        self.assertTrue(item.requires_escalation)

    def test_paid_bill_is_never_flagged_overdue(self):
        # The payment has to fall INSIDE the period being reconciled. This test
        # used to pay "five days ago", which sat inside July only while the clock
        # did: from 5 Aug 2026 it fell outside, the payment correctly stopped
        # counting (that rule is its own test — see
        # test_payment_after_period_end_does_not_count_in_the_month) and the bill
        # read overdue. A time-bomb in the test, not a product bug, and it held
        # main red. Pay within July, which is what this test means to check.
        self.make_bill(total='1000.00', issue_date=date(2026, 5, 1),
                       due_date=JULY_START - timedelta(days=10))
        self.pay(Invoice.objects.get(), '1000.00', JULY_END - timedelta(days=5))
        self.build()
        item = InvoiceReconItem.objects.get()
        self.assertEqual(item.due_state, 'paid')
        self.assertFalse(item.requires_escalation)

    def test_line_caches_the_overdue_amount_and_oldest_days(self):
        # One overdue bill (40 days) and one not-yet-due bill on the same
        # supplier: only the overdue one counts towards `overdue`.
        self.make_bill(total='1000.00', issue_date=date(2026, 5, 1),
                       due_date=timezone.localdate() - timedelta(days=40))
        self.make_bill(total='500.00', issue_date=date(2026, 7, 2),
                       due_date=timezone.localdate() + timedelta(days=20))
        run = self.build()
        line = run.lines.get()
        self.assertEqual(line.unpaid, Decimal('1500.00'))
        self.assertEqual(line.overdue, Decimal('1000.00'))
        self.assertEqual(line.max_days_past_due, 40)

    def test_line_with_nothing_overdue_reports_zero(self):
        self._item_due(15)
        line = SupplierReconRun.objects.get().lines.get()
        self.assertEqual(line.overdue, Decimal('0.00'))
        self.assertEqual(line.max_days_past_due, 0)

    def test_dashboard_counts_the_30_day_tier_separately(self):
        self.make_bill(total='1000.00', issue_date=date(2026, 5, 1),
                       due_date=timezone.localdate() - timedelta(days=45))
        run = self.build()
        from supplier_recon.api_views import _dashboard_payload
        k = _dashboard_payload(run)['kpis']
        self.assertEqual(k['overdue_count'], 1)
        self.assertEqual(k['overdue_30_count'], 1)


# ---------------------------------------------------------------------------
# Parts-supplier reclassification (Bharath 2026-07-27, point 4)
# ---------------------------------------------------------------------------

class ReclassifyPartsSuppliersTests(ReconTestBase):

    def _profile(self, name, category=SupplierCategory.PANEL_BEATER):
        c = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name=name,
            currency_code=self.bwp, company=self.company, payment_terms_days=30)
        return ReconSupplierProfile.objects.create(contact=c, category=category)

    def test_cfao_moves_to_parts(self):
        p = self._profile('CFAO MOBILITY (PTY) LTD')
        call_command('reclassify_parts_suppliers', '--company', 'RCNA',
                     stdout=StringIO())
        p.refresh_from_db()
        self.assertEqual(p.category, SupplierCategory.PARTS)
        self.assertIn('Reclassified', p.notes)

    def test_naledi_motors_moves_to_parts(self):
        p = self._profile('Naledi Motors (Pty) Ltd')
        call_command('reclassify_parts_suppliers', '--all', stdout=StringIO())
        p.refresh_from_db()
        self.assertEqual(p.category, SupplierCategory.PARTS)

    def test_a_genuine_panel_beater_is_left_alone(self):
        call_command('reclassify_parts_suppliers', '--all', stdout=StringIO())
        profile = ReconSupplierProfile.objects.get(contact=self.vendor)
        self.assertEqual(profile.category, SupplierCategory.PANEL_BEATER)

    def test_refuses_to_run_without_a_scope(self):
        from django.core.management.base import CommandError
        self._profile('CFAO MOBILITY (PTY) LTD')
        with self.assertRaises(CommandError):
            call_command('reclassify_parts_suppliers', stdout=StringIO())

    def test_scoped_run_leaves_another_entity_untouched(self):
        # Same-named supplier in a different entity must not be flipped by a
        # company-scoped run.
        other = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='CFAO MOBILITY (PTY) LTD',
            currency_code=self.bwp, company=self.other_company,
            payment_terms_days=30)
        other_profile = ReconSupplierProfile.objects.create(
            contact=other, category=SupplierCategory.PANEL_BEATER)
        here = self._profile('CFAO MOBILITY (PTY) LTD')
        call_command('reclassify_parts_suppliers', '--company', 'RCNA',
                     stdout=StringIO())
        here.refresh_from_db(); other_profile.refresh_from_db()
        self.assertEqual(here.category, SupplierCategory.PARTS)
        self.assertEqual(other_profile.category, SupplierCategory.PANEL_BEATER)

    def test_is_idempotent(self):
        p = self._profile('CFAO MOBILITY (PTY) LTD')
        out = StringIO()
        call_command('reclassify_parts_suppliers', '--all', stdout=out)
        call_command('reclassify_parts_suppliers', '--all', stdout=out)
        p.refresh_from_db()
        self.assertEqual(p.category, SupplierCategory.PARTS)
        self.assertIn('already parts', out.getvalue())

    def test_dry_run_changes_nothing(self):
        p = self._profile('CFAO MOBILITY (PTY) LTD')
        call_command('reclassify_parts_suppliers', '--all', '--dry-run',
                     stdout=StringIO())
        p.refresh_from_db()
        self.assertEqual(p.category, SupplierCategory.PANEL_BEATER)


# ---------------------------------------------------------------------------
# Redesign (2026-09-07): S1 "Invoices actioned" filter, S3 dashboard group
# scoping, S5 system-stamped recorder department.
# ---------------------------------------------------------------------------

class RedesignBackendTests(ReconTestBase):
    """The three backend behaviours the Supplier Recon redesign adds. Built
    against the live schema, each proven to fail without its change."""

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    @staticmethod
    def _rows(resp):
        body = resp.json()
        return body['results'] if isinstance(body, dict) and 'results' in body else body

    @staticmethod
    def _ageing_total(payload):
        return sum(Decimal(str(v)) for v in payload['ageing'].values())

    def setUp(self):
        # S5: the recorder carries a department on their OMNI profile.
        UserProfile.objects.update_or_create(
            user=self.staff,
            defaults={'title': UserProfile.Title.ACCOUNTANT,
                      'department': 'Finance'})
        due = timezone.localdate() + timedelta(days=10)
        # A claims-related bill (panel-beater vendor from the base fixture).
        self.claims_bill = self.make_bill(total='5000.00', due_date=due)
        # An operational bill on a general/operations vendor.
        self.ops_vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Office Plus Supplies',
            currency_code=self.bwp, company=self.company, payment_terms_days=30)
        ReconSupplierProfile.objects.create(
            contact=self.ops_vendor, category=SupplierCategory.GENERAL)
        self.ops_bill = self.make_bill(total='2000.00', contact=self.ops_vendor,
                                       due_date=due)
        # A claims-related supplier whose trade is unconfirmed — the 7th claim
        # category the client set used to omit (H96 fork). Must be counted as
        # claims-related by both surfaces.
        self.claims_other_vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Unconfirmed Claims Co',
            currency_code=self.bwp, company=self.company, payment_terms_days=30)
        ReconSupplierProfile.objects.create(
            contact=self.claims_other_vendor,
            category=SupplierCategory.CLAIMS_OTHER)
        self.claims_other_bill = self.make_bill(
            total='1000.00', contact=self.claims_other_vendor, due_date=due)
        self.run = self.build()
        # Guarantee an invoice number is captured (S1 requires it).
        for inv in (self.claims_bill, self.ops_bill, self.claims_other_bill):
            Invoice.objects.filter(pk=inv.pk).update(
                invoice_number=f'INV-{str(inv.pk)[:8]}')

    def _claims_item(self):
        return InvoiceReconItem.objects.get(line__supplier=self.vendor)

    def _ops_item(self):
        return InvoiceReconItem.objects.get(line__supplier=self.ops_vendor)

    # -- S1 --------------------------------------------------------------- #
    def test_s1_invoiced_actioned_returns_only_actioned_bills(self):
        item = self._claims_item()
        services.action_item(item, self.staff, reason_code=self.reason_dispute,
                             justification=LONG_REASON)
        r = self._client(self.staff).get(
            f'/api/v1/supplier-recon/items/?run={self.run.pk}&invoiced_actioned=true')
        self.assertEqual(r.status_code, 200, r.content)
        ids = {row['id'] for row in self._rows(r)}
        self.assertIn(str(item.pk), ids)               # actioned + has invoice no.
        self.assertNotIn(str(self._ops_item().pk), ids)  # not actioned -> excluded

    def test_s1_excludes_a_bill_with_no_invoice_number_even_if_actioned(self):
        item = self._claims_item()
        services.action_item(item, self.staff, reason_code=self.reason_dispute,
                             justification=LONG_REASON)
        Invoice.objects.filter(pk=item.invoice_id).update(invoice_number='')
        r = self._client(self.staff).get(
            f'/api/v1/supplier-recon/items/?run={self.run.pk}&invoiced_actioned=true')
        ids = {row['id'] for row in self._rows(r)}
        self.assertNotIn(str(item.pk), ids)

    # -- S3 --------------------------------------------------------------- #
    def test_s3_dashboard_group_scopes_the_ageing(self):
        c = self._client(self.staff)
        base = f'/api/v1/supplier-recon/runs/{self.run.pk}/dashboard/'
        all_ = c.get(base).json()
        claims = c.get(base + '?group=claims').json()
        ops = c.get(base + '?group=operational').json()
        self.assertEqual(claims['ageing_group'], 'claims')
        self.assertEqual(ops['ageing_group'], 'operational')
        # claims-related = panel-beater 5000 + claims_other 1000; operational 2000.
        self.assertEqual(self._ageing_total(claims), Decimal('6000.00'))
        self.assertEqual(self._ageing_total(ops), Decimal('2000.00'))
        self.assertEqual(self._ageing_total(all_), Decimal('8000.00'))
        # The claims/operational set is emitted for the UI, and claims_other is
        # in it — pins the constant so the client cannot fork from it (H96).
        self.assertIn('claims_other', all_['claim_categories'])
        self.assertIn(SupplierCategory.PANEL_BEATER, all_['claim_categories'])

    # -- S5 --------------------------------------------------------------- #
    def test_s5_serializer_stamps_the_recorder_department(self):
        item = self._claims_item()
        services.action_item(item, self.staff, reason_code=self.reason_dispute,
                             justification=LONG_REASON)
        r = self._client(self.staff).get(
            f'/api/v1/supplier-recon/items/{item.pk}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['actioned_by_department'], 'Finance')
