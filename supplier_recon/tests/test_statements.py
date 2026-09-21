"""Supplier-statement ingestion + statement-to-ledger matching tests.

Covers the acceptance conditions from the 2026-08-24 usability test:
clean match, amount variance, missing invoice, duplicate, credit note, payment,
already-paid, unapproved-hold, omni-only, currency mismatch, malformed file, and
rerun idempotency — plus the hard safety rule that nothing unmatched, duplicated,
unapproved or already-paid is ever proposed for payment.
"""
from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from billing.models import Invoice
from supplier_recon.constants import (
    MatchStatus,
    NEVER_PAY_MATCH_TYPES,
    PaymentProposal,
    PaymentStatus,
    StatementLineType,
    StatementStatus,
    StmtMatchType,
)
from supplier_recon.statement_matching import match_statement
from supplier_recon.models import InvoiceReconItem
from supplier_recon.statement_models import (
    StatementMatch,
    SupplierStatement,
    SupplierStatementLine,
)
from supplier_recon.statement_parser import StatementParseError, parse_statement

from .test_supplier_recon import ReconTestBase


# ---------------------------------------------------------------------------
# Parser — pure, no database
# ---------------------------------------------------------------------------
class StatementParserTests(SimpleTestCase):

    def _csv(self, body):
        return body.encode('utf-8')

    def test_debit_credit_layout(self):
        csv = ('Date,Invoice No,Description,Debit,Credit,Balance\n'
               '2026-07-03,INV-1001,Repair,1500.00,,1500.00\n'
               '2026-07-10,INV-1002,Parts,500.00,,2000.00\n'
               '2026-07-20,PMT,Payment received EFT,,800.00,1200.00\n')
        p = parse_statement(self._csv(csv), 'stmt.csv')
        self.assertEqual(len(p.lines), 3)
        inv = [l for l in p.lines if l.line_type == StatementLineType.INVOICE]
        pmt = [l for l in p.lines if l.line_type == StatementLineType.PAYMENT]
        self.assertEqual(len(inv), 2)
        self.assertEqual(len(pmt), 1)
        self.assertEqual(inv[0].reference, 'INV-1001')
        self.assertEqual(inv[0].amount, Decimal('1500.00'))
        self.assertEqual(pmt[0].amount, Decimal('800.00'))   # stored positive

    def test_single_amount_layout_and_credit_note(self):
        csv = ('Date,Reference,Details,Amount\n'
               '2026-07-03,INV-1,Repair,1500.00\n'
               '2026-07-05,CN-1,Credit note return,-200.00\n'
               '2026-07-09,RCPT,Payment,-1000.00\n')
        p = parse_statement(self._csv(csv), 'stmt.csv')
        types = {l.reference: l.line_type for l in p.lines}
        self.assertEqual(types['INV-1'], StatementLineType.INVOICE)
        self.assertEqual(types['CN-1'], StatementLineType.CREDIT_NOTE)
        self.assertEqual(types['RCPT'], StatementLineType.PAYMENT)

    def test_opening_and_closing_balance(self):
        csv = ('Date,Reference,Description,Debit,Credit,Balance\n'
               '2026-07-01,,Balance brought forward,,,1000.00\n'
               '2026-07-03,INV-9,Repair,500.00,,1500.00\n'
               '2026-07-31,,Closing balance,,,1500.00\n')
        p = parse_statement(self._csv(csv), 'stmt.csv')
        self.assertEqual(p.opening_balance, Decimal('1000.00'))
        self.assertEqual(p.closing_balance, Decimal('1500.00'))
        self.assertEqual(len(p.lines), 1)   # only the real invoice line

    def test_malformed_no_header_raises(self):
        with self.assertRaises(StatementParseError):
            parse_statement(b'just some text\nwith no columns\n', 'x.csv')

    def test_empty_file_raises(self):
        with self.assertRaises(StatementParseError):
            parse_statement(b'', 'x.csv')

    def test_unsupported_type_raises(self):
        with self.assertRaises(StatementParseError):
            parse_statement(b'%PDF-1.4', 'statement.pdf')

    def test_xlsx_roundtrip(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Date', 'Invoice No', 'Description', 'Amount'])
        ws.append(['2026-07-03', 'INV-77', 'Repair', 1500])
        buf = BytesIO()
        wb.save(buf)
        p = parse_statement(buf.getvalue(), 'stmt.xlsx')
        self.assertEqual(len(p.lines), 1)
        self.assertEqual(p.lines[0].reference, 'INV-77')
        self.assertEqual(p.lines[0].amount, Decimal('1500.00'))


# ---------------------------------------------------------------------------
# Matching engine — against real recon items
# ---------------------------------------------------------------------------
class StatementMatchTests(ReconTestBase):

    def setUp(self):
        # A built run gives us a real supplier line; we set each bill's amount
        # and match_status explicitly so the matcher's mapping is what is tested.
        self.b_ok   = self.make_bill(total='1500.00')
        self.b_var  = self.make_bill(total='500.00')
        self.b_nopo = self.make_bill(total='700.00')
        self.b_paid = self.make_bill(total='900.00')
        self.pay(self.b_paid, '900.00', payment_date=self.b_paid.issue_date)
        run = self.build()
        self.line = run.lines.get(supplier=self.vendor)
        self.items = {it.invoice.invoice_number: it for it in self.line.items.all()}
        self._set(self.b_ok,   MatchStatus.MATCHED)
        self._set(self.b_var,  MatchStatus.MATCHED)
        self._set(self.b_nopo, MatchStatus.NO_PO)

    def _set(self, bill, match_status):
        it = self.items[bill.invoice_number]
        it.match_status = match_status
        it.save(update_fields=['match_status', 'updated_at'])

    def _statement(self):
        return SupplierStatement.objects.create(
            line=self.line, currency='BWP', status=StatementStatus.PARSED)

    def _add(self, stmt, ref, amount, line_type=StatementLineType.INVOICE, row=1):
        return SupplierStatementLine.objects.create(
            statement=stmt, row_number=row, line_type=line_type,
            reference=ref, amount=Decimal(amount))

    def _match_by_ref(self, stmt, ref):
        return stmt.matches.get(statement_line__reference=ref)

    def test_clean_match_proposes_pay_now(self):
        stmt = self._statement()
        self._add(stmt, self.b_ok.invoice_number, '1500.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_ok.invoice_number)
        self.assertEqual(mm.match_type, StmtMatchType.MATCHED)
        self.assertEqual(mm.proposal, PaymentProposal.PAY_NOW)
        self.assertEqual(mm.proposed_amount, Decimal('1500.00'))

    def test_amount_variance_is_investigate_not_pay(self):
        stmt = self._statement()
        self._add(stmt, self.b_var.invoice_number, '650.00')   # Omni says 500
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_var.invoice_number)
        self.assertEqual(mm.match_type, StmtMatchType.AMOUNT_VARIANCE)
        self.assertEqual(mm.proposal, PaymentProposal.INVESTIGATE)
        self.assertEqual(mm.variance, Decimal('150.00'))

    def test_unapproved_bill_is_held(self):
        stmt = self._statement()
        self._add(stmt, self.b_nopo.invoice_number, '700.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_nopo.invoice_number)
        self.assertEqual(mm.proposal, PaymentProposal.HOLD)

    def test_already_paid_is_do_not_pay(self):
        stmt = self._statement()
        self._add(stmt, self.b_paid.invoice_number, '900.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_paid.invoice_number)
        self.assertEqual(mm.match_type, StmtMatchType.ALREADY_PAID)
        self.assertEqual(mm.proposal, PaymentProposal.DO_NOT_PAY)

    def test_statement_only_is_investigate(self):
        stmt = self._statement()
        self._add(stmt, 'GHOST-999', '250.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, 'GHOST-999')
        self.assertEqual(mm.match_type, StmtMatchType.STATEMENT_ONLY)
        self.assertEqual(mm.proposal, PaymentProposal.INVESTIGATE)

    def test_duplicate_reference_is_do_not_pay(self):
        stmt = self._statement()
        self._add(stmt, self.b_ok.invoice_number, '1500.00', row=1)
        self._add(stmt, self.b_ok.invoice_number, '1500.00', row=2)
        match_statement(stmt)
        for mm in stmt.matches.filter(statement_line__isnull=False):
            self.assertEqual(mm.match_type, StmtMatchType.DUPLICATE)
            self.assertEqual(mm.proposal, PaymentProposal.DO_NOT_PAY)

    def test_credit_note_is_do_not_pay(self):
        stmt = self._statement()
        self._add(stmt, 'CN-5', '200.00', line_type=StatementLineType.CREDIT_NOTE)
        match_statement(stmt)
        mm = self._match_by_ref(stmt, 'CN-5')
        self.assertEqual(mm.match_type, StmtMatchType.CREDIT_NOTE)
        self.assertEqual(mm.proposal, PaymentProposal.DO_NOT_PAY)

    def test_omni_only_bill_is_flagged(self):
        stmt = self._statement()
        self._add(stmt, self.b_ok.invoice_number, '1500.00')   # only lists one bill
        match_statement(stmt)
        omni_only = stmt.matches.filter(match_type=StmtMatchType.OMNI_ONLY)
        # b_var, b_nopo, b_paid were not on the statement.
        self.assertEqual(omni_only.count(), 3)
        for mm in omni_only:
            self.assertEqual(mm.proposal, PaymentProposal.INVESTIGATE)

    def test_safety_never_pay_the_never_pay_types(self):
        stmt = self._statement()
        self._add(stmt, 'GHOST-1', '100.00', row=1)                     # statement_only
        self._add(stmt, self.b_paid.invoice_number, '900.00', row=2)    # already_paid
        self._add(stmt, self.b_ok.invoice_number, '1500.00', row=3)
        self._add(stmt, self.b_ok.invoice_number, '1500.00', row=4)     # duplicate pair
        match_statement(stmt)
        for mm in stmt.matches.all():
            if mm.match_type in NEVER_PAY_MATCH_TYPES:
                self.assertNotEqual(
                    mm.proposal, PaymentProposal.PAY_NOW,
                    f'{mm.match_type} must never be auto-pay')

    # -- Fable 5 hardening (2026-08-24): the never-pay safety rule -------- #
    def _mk_item(self, invoice_number, amount, *,
                 match_status=MatchStatus.MATCHED,
                 payment_status=PaymentStatus.UNPAID, amount_paid='0.00'):
        from datetime import date
        inv = Invoice.objects.create(
            invoice_type=Invoice.InvoiceType.VENDOR_BILL, contact=self.vendor,
            company=self.company, issue_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31), currency_code=self.bwp,
            exchange_rate=Decimal('1.00000000'), subtotal=Decimal(amount),
            total_amount=Decimal(amount),
            amount_paid=Decimal(amount_paid), balance_due=Decimal(amount),
            status=Invoice.Status.POSTED, invoice_number=invoice_number,
            created_by=self.staff)
        return InvoiceReconItem.objects.create(
            line=self.line, invoice=inv, amount=Decimal(amount),
            amount_paid=Decimal(amount_paid), match_status=match_status,
            payment_status=payment_status, due_date=date(2026, 7, 31))

    def test_never_pay_set_membership(self):
        # L2: the frozenset IS the safety rule — pin its exact members so a
        # future edit that drops one is caught here.
        self.assertEqual(NEVER_PAY_MATCH_TYPES, frozenset({
            StmtMatchType.STATEMENT_ONLY, StmtMatchType.DUPLICATE,
            StmtMatchType.ALREADY_PAID, StmtMatchType.UNMATCHED_PAYMENT,
            StmtMatchType.CREDIT_NOTE, StmtMatchType.OMNI_ONLY,
        }))

    def test_paid_after_build_is_already_paid(self):
        # Bill unpaid at run-build (snapshot shows it owing) but paid live in
        # Omni afterwards → must be ALREADY_PAID, never PAY_NOW (Break 2).
        # A posted bill can't be modified via .save(); a live payment lands on
        # amount_paid by a direct DB update (payments._update_invoice_from_
        # allocations), so do the same here.
        Invoice.objects.filter(pk=self.b_ok.pk).update(
            amount_paid=self.b_ok.total_amount)
        stmt = self._statement()
        self._add(stmt, self.b_ok.invoice_number, '1500.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_ok.invoice_number)
        self.assertEqual(mm.match_type, StmtMatchType.ALREADY_PAID)
        self.assertEqual(mm.proposal, PaymentProposal.DO_NOT_PAY)

    def test_held_bill_is_hold_not_pay(self):
        it = self.items[self.b_var.invoice_number]
        it.payment_status = PaymentStatus.HELD
        it.save(update_fields=['payment_status', 'updated_at'])
        stmt = self._statement()
        self._add(stmt, self.b_var.invoice_number, '500.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, self.b_var.invoice_number)
        self.assertEqual(mm.proposal, PaymentProposal.HOLD)

    def test_short_reference_does_not_fuzzy_match_wrong_bill(self):
        # A 4-char tail must NOT claim a longer bill number (Break 3).
        self._mk_item('INV-21001', '250.00')
        stmt = self._statement()
        self._add(stmt, '1001', '250.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, '1001')
        self.assertEqual(mm.match_type, StmtMatchType.STATEMENT_ONLY)
        self.assertNotEqual(mm.proposal, PaymentProposal.PAY_NOW)

    def test_ambiguous_fuzzy_match_fails_closed(self):
        self._mk_item('99-12345', '300.00')
        self._mk_item('88-12345', '300.00')
        stmt = self._statement()
        self._add(stmt, '12345', '300.00')
        match_statement(stmt)
        mm = self._match_by_ref(stmt, '12345')
        self.assertEqual(mm.match_type, StmtMatchType.STATEMENT_ONLY)

    def test_two_spellings_of_one_bill_pay_once(self):
        # Exact + fuzzy spelling of the SAME bill on two lines: only one Pay-now,
        # the second is a duplicate (Break 1).
        self._mk_item('INV-55555', '400.00')
        stmt = self._statement()
        self._add(stmt, 'INV-55555', '400.00', row=1)   # exact
        self._add(stmt, '55555', '400.00', row=2)        # fuzzy → same bill
        match_statement(stmt)
        proposals = [m.proposal for m in stmt.matches.filter(statement_line__isnull=False)]
        self.assertEqual(proposals.count(PaymentProposal.PAY_NOW), 1)
        self.assertIn(PaymentProposal.DO_NOT_PAY, proposals)

    def test_rematch_is_idempotent(self):
        stmt = self._statement()
        self._add(stmt, self.b_ok.invoice_number, '1500.00')
        s1 = match_statement(stmt)
        count1 = StatementMatch.objects.filter(statement=stmt).count()
        s2 = match_statement(stmt)
        count2 = StatementMatch.objects.filter(statement=stmt).count()
        self.assertEqual(count1, count2)
        self.assertEqual(s1['by_proposal'], s2['by_proposal'])


# ---------------------------------------------------------------------------
# Upload API
# ---------------------------------------------------------------------------
class StatementUploadApiTests(ReconTestBase):

    def setUp(self):
        self.b_ok = self.make_bill(total='1500.00')
        run = self.build()
        self.line = run.lines.get(supplier=self.vendor)
        item = self.line.items.get(invoice=self.b_ok)
        item.match_status = MatchStatus.MATCHED
        item.save(update_fields=['match_status', 'updated_at'])
        self.url = f'/api/v1/supplier-recon/lines/{self.line.id}/statements/upload/'
        self.client = APIClient()

    def _file(self, name='stmt.csv', amount='1500.00', ref=None):
        ref = ref or self.b_ok.invoice_number
        body = (f'Date,Invoice No,Description,Amount\n'
                f'2026-07-03,{ref},Repair,{amount}\n').encode('utf-8')
        return SimpleUploadedFile(name, body, content_type='text/csv')

    def test_anonymous_blocked(self):
        r = self.client.post(self.url, {'file': self._file()}, format='multipart')
        self.assertIn(r.status_code, (401, 403))

    def test_upload_parses_and_matches(self):
        self.client.force_authenticate(self.reviewer)
        r = self.client.post(self.url, {'file': self._file()}, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.data['lines']), 1)
        proposals = [m['proposal'] for m in r.data['matches']]
        self.assertIn(PaymentProposal.PAY_NOW, proposals)

    def test_duplicate_file_rejected(self):
        self.client.force_authenticate(self.reviewer)
        self.client.post(self.url, {'file': self._file()}, format='multipart')
        r2 = self.client.post(self.url, {'file': self._file()}, format='multipart')
        self.assertEqual(r2.status_code, 409, r2.content)

    def test_wrong_currency_rejected(self):
        self.client.force_authenticate(self.reviewer)
        r = self.client.post(self.url,
                             {'file': self._file(), 'currency': 'USD'},
                             format='multipart')
        self.assertEqual(r.status_code, 400, r.content)
