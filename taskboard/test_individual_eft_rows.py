"""taskboard/test_individual_eft_rows.py — Individual writes N rows in the bank file.

Kelvin Kimani's spec left this open: "Does the toggle drive an execution file, or
only the document?" The CFO answered on 2026-09-08: **Individual must produce the
separate bank rows.** Before this, an eight-invoice request under Individual said
"INDIVIDUAL (8 payments)" on the pack and then loaded ONE payment for the lump —
the document and the bank file disagreed, and the person keying FNB had to know
to ignore one of them.

What these tests pin:
  - Bulk is unchanged: one payment, the request total. Nothing about the old
    behaviour may drift.
  - Individual writes one payment per PAYABLE line, each with its own amount.
  - EVERY row goes into the batch — that is what puts N lines in the file.
  - The sum of the rows equals TOTAL PAYABLE exactly. A request that does not
    reconcile is refused BEFORE anything reaches the bank.
  - A cancelled line is never paid.
  - Each row carries its own invoice on the bank reference, inside FNB's 35
    characters, and the reference never exceeds it.
  - A retry reuses the rows instead of raising a second set of N payments.

Omni moves no money here. The load hands FNB a batch which the CFO then approves
on his phone with two factors. No real payee or staff names.

Run: manage.py test taskboard.test_individual_eft_rows
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from banking.models import BankAccount
from core.models import Company, Currency
from ledger.models import Account
from payments.models import Payment
from taskboard.models import PaymentRequest

SOURCE_ACCT = '8' * 11

# Kelvin's E.G Couriers shape: eight invoices, one payee, one total.
EIGHT = [
    {'description': f'Courier run {n}', 'amount': amt,
     'invoice_number': f'IN10298{n}', 'ref': f'IN10298{n}'}
    for n, amt in enumerate(
        ['2000.00', '2000.00', '2000.00', '2000.00',
         '2000.00', '2000.00', '2000.00', '2556.09'], start=1)
]
EIGHT_TOTAL = Decimal('16556.09')


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='INDIV', defaults={'name': 'Individual Rows Test Co',
                                    'base_currency': cls.bwp})
        cls.raiser = User.objects.create_user('indiv_raiser', password='x')
        cls.approver = User.objects.create_user('indiv_approver', password='x')
        cls.gl = Account.objects.create(
            code='INDIV-BANK', name='Individual test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True, owner_company=cls.company)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='Indiv test current',
            account_number=SOURCE_ACCT, currency_code_id='BWP')

    def _request(self, ref='PR-INDIV-0001', **over):
        kwargs = dict(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='MCS 1162 JULY', payee='Test Courier Co',
            line_items=EIGHT, total=EIGHT_TOTAL,
            processing_method=PaymentRequest.ProcessingMethod.INDIVIDUAL,
            account_name='Test Courier Co (Pty) Ltd', account_number='1234567',
            bank_name='FNB Botswana', branch_code='293567', account_type='CACC',
            status=PaymentRequest.Status.PENDING_CFO, created_by=self.raiser,
        )
        kwargs.update(over)
        return PaymentRequest.objects.create(**kwargs)

    def _build(self, pr):
        from taskboard.fnb_autoload import build_payments_for_request
        return build_payments_for_request(pr, self.company, self.source)


class BuildRowsTests(_Base):

    def test_individual_writes_one_payment_per_invoice_line(self):
        rows = self._build(self._request())
        self.assertEqual(len(rows), 8)
        self.assertEqual([str(r.amount) for r in rows],
                         ['2000.00'] * 7 + ['2556.09'])

    def test_the_rows_sum_to_total_payable_exactly(self):
        rows = self._build(self._request())
        self.assertEqual(sum((r.amount for r in rows), Decimal('0.00')),
                         EIGHT_TOTAL)

    def test_bulk_is_unchanged_one_payment_for_the_whole_request(self):
        rows = self._build(self._request(
            ref='PR-INDIV-BULK',
            processing_method=PaymentRequest.ProcessingMethod.BULK))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, EIGHT_TOTAL)

    def test_a_single_line_request_is_one_payment_even_when_set_to_individual(self):
        # The choice is not a live decision on one line, so it must not split.
        rows = self._build(self._request(
            ref='PR-INDIV-ONE',
            line_items=[{'description': 'One repair', 'amount': '250.00',
                         'invoice_number': 'IN1'}],
            total=Decimal('250.00')))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, Decimal('250.00'))

    def test_a_cancelled_line_is_never_paid(self):
        lines = [dict(ln) for ln in EIGHT]
        lines[2] = {**lines[2], 'cancelled': True}
        rows = self._build(self._request(
            ref='PR-INDIV-CANC', line_items=lines,
            total=EIGHT_TOTAL - Decimal('2000.00')))
        self.assertEqual(len(rows), 7)
        self.assertEqual(sum((r.amount for r in rows), Decimal('0.00')),
                         Decimal('14556.09'))

    def test_a_request_that_does_not_reconcile_is_refused_before_the_bank(self):
        from taskboard.fnb_autoload import AutoLoadNotPossible
        # One thebe out is out — this is the guard that must never soften.
        with self.assertRaises(AutoLoadNotPossible):
            self._build(self._request(ref='PR-INDIV-BAD',
                                      total=EIGHT_TOTAL + Decimal('0.01')))

    def test_each_row_names_its_own_invoice_within_fnb_s_35_characters(self):
        rows = self._build(self._request())
        refs = [r.bank_our_reference for r in rows]
        self.assertEqual(len(set(refs)), 8, f'references not unique: {refs}')
        for r, ln in zip(rows, EIGHT):
            self.assertIn(ln['invoice_number'], r.bank_our_reference)
            self.assertLessEqual(len(r.bank_our_reference), 35,
                                 r.bank_our_reference)

    def test_a_long_subject_is_trimmed_but_the_invoice_survives(self):
        rows = self._build(self._request(
            ref='PR-INDIV-LONG',
            # 32 chars: fits the request's own 35-char column, but adding a
            # 9-char invoice would overflow FNB's 35 — so the trim is exercised.
            bank_our_reference='COURIER SERVICES AUGUST 2026 RUN'))
        for r, ln in zip(rows, EIGHT):
            self.assertLessEqual(len(r.bank_our_reference), 35)
            self.assertTrue(r.bank_our_reference.endswith(ln['invoice_number']),
                            r.bank_our_reference)

    def test_building_twice_reuses_the_rows_rather_than_doubling_them(self):
        pr = self._request(ref='PR-INDIV-RETRY')
        first = self._build(pr)
        second = self._build(pr)
        self.assertEqual([p.pk for p in first], [p.pk for p in second])
        self.assertEqual(
            Payment.objects.filter(company=self.company,
                                   reference__startswith='PR-INDIV-RETRY').count(),
            8)


@override_settings(PAYMENT_REQUEST_AUTO_FNB=True,
                   PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={'default': SOURCE_ACCT})
class LoadToBankTests(_Base):

    def _load(self, pr):
        from taskboard.fnb_autoload import load_request_to_fnb

        class _Resp:
            json = {'instructionId': 'INDIV-BATCH-1'}
            status_code = 200

        self.sent_all = []
        self.sent = {}
        real = None
        from fnb import payments as fnbp
        real = fnbp.build_batch_payload

        def _spy(payments, **kw):
            payload = real(payments, **kw)
            self.sent_all.append(payload)
            self.sent = payload
            return payload

        with mock.patch('fnb.payments.FNBClient') as client, \
             mock.patch('fnb.payments.build_batch_payload', side_effect=_spy):
            client.return_value.post.return_value = _Resp()
            return load_request_to_fnb(pr, self.approver)

    def test_all_eight_rows_reach_the_bank_file_not_just_the_first(self):
        pr = self._request(ref='PR-INDIV-FILE')
        out = self._load(pr)
        self.assertTrue(out['loaded'], out)
        total_txns = sum(
            len((p.get('paymentInformation') or [{}])[0].get(
                'creditTransferTransactionInformation') or [])
            for p in self.sent_all)
        self.assertEqual(total_txns, 8, f'{total_txns} txns across {len(self.sent_all)} batches, expected 8')

    def test_the_file_control_sum_equals_total_payable(self):
        pr = self._request(ref='PR-INDIV-SUM')
        self.assertTrue(self._load(pr)['loaded'])
        total = sum(Decimal(str(p['groupHeader']['totalControlSum']))
                    for p in self.sent_all)
        self.assertEqual(total, EIGHT_TOTAL)

    def test_bulk_still_sends_exactly_one_row(self):
        pr = self._request(ref='PR-INDIV-BULKFILE',
                           processing_method=PaymentRequest.ProcessingMethod.BULK)
        self.assertTrue(self._load(pr)['loaded'])
        txns = (self.sent.get('paymentInformation') or [{}])[0].get(
            'creditTransferTransactionInformation') or []
        self.assertEqual(len(txns), 1)

    # ------------------------------------------------------------------
    # A RETRY must still send all N lines (2026-09-20)
    # ------------------------------------------------------------------
    # The first attempt stamps pr.payment with line 1 BEFORE the submit loop
    # runs. The retry then short-circuited on `if pr.payment_id: payments =
    # [pr.payment]` and sent that ONE line — P2,000.00 of a P16,556.09 request
    # — reported loaded, and blanked the error. Seven invoices were never sent
    # and the request read as fully loaded.

    def _fail_all(self, pr):
        """One whole attempt that FNB cleanly refuses (HTTP 400).

        A clean 4xx means nothing moved, so the claim is released and the
        request is retry-eligible — which is exactly the state that lost the
        other seven lines.
        """
        from fnb.client import FNBAPIError
        from taskboard.fnb_autoload import load_request_to_fnb
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = FNBAPIError(
                400, 'RJCT test reject')
            return load_request_to_fnb(pr, self.approver)

    def test_a_retry_after_a_clean_bank_reject_still_sends_all_eight_lines(self):
        pr = self._request(ref='PR-INDIV-RETRY-ALL')
        first = self._fail_all(pr)
        self.assertFalse(first['loaded'], first)

        pr.refresh_from_db()
        self.assertIsNotNone(pr.payment_id,
                             'precondition: the first attempt stamps pr.payment')

        out = self._load(pr)
        self.assertTrue(out['loaded'], out)
        total_txns = sum(
            len((p.get('paymentInformation') or [{}])[0].get(
                'creditTransferTransactionInformation') or [])
            for p in self.sent_all)
        self.assertEqual(total_txns, 8,
                         f'retry sent {total_txns} lines, not 8')
        total = sum(Decimal(str(p['groupHeader']['totalControlSum']))
                    for p in self.sent_all)
        self.assertEqual(total, EIGHT_TOTAL,
                         f'retry sent {total}, not the full {EIGHT_TOTAL}')

    def test_a_retry_does_not_raise_a_second_set_of_payment_rows(self):
        """Rebuilding on the retry must reuse the same eight rows."""
        pr = self._request(ref='PR-INDIV-RETRY-ROWS')
        self._fail_all(pr)
        pr.refresh_from_db()
        self._load(pr)
        self.assertEqual(
            Payment.objects.filter(company=self.company,
                                   reference__startswith='PR-INDIV-RETRY-ROWS').count(),
            8)

    # ------------------------------------------------------------------
    # A PARTIAL failure must survive being recorded (2026-09-20)
    # ------------------------------------------------------------------
    # The partial-failure branch wrote "5 of 8 payments loaded to FNB…" onto
    # the request and then fell through to _record(True, ''), whose first line
    # is `pr.fnb_load_error = '' if loaded else reason` — with fnb_load_error in
    # update_fields. The stored message was '' every time: Finance was told the
    # load succeeded and never learned which three lines need hand-keying.

    def _load_with_failures(self, pr, n_fail):
        """Load where the last `n_fail` of the eight submissions are refused."""
        from fnb.client import FNBAPIError
        from taskboard.fnb_autoload import load_request_to_fnb

        class _Resp:
            json = {'instructionId': 'INDIV-BATCH-PARTIAL'}
            status_code = 200

        outcomes = [_Resp() for _ in range(8 - n_fail)]
        outcomes += [FNBAPIError(400, 'RJCT test reject') for _ in range(n_fail)]
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = outcomes
            return load_request_to_fnb(pr, self.approver)

    def test_a_partial_load_keeps_the_message_naming_the_lines_that_failed(self):
        pr = self._request(ref='PR-INDIV-PARTIAL')
        out = self._load_with_failures(pr, n_fail=3)
        self.assertTrue(out['loaded'], out)

        pr.refresh_from_db()
        self.assertIn('5 of 8', pr.fnb_load_error or '',
                      'the partial-load message was erased by the call that '
                      f'records it: stored {pr.fnb_load_error!r}')
        self.assertIn('Load the rest by hand', pr.fnb_load_error or '')

    def test_a_partial_load_returns_the_same_reason_it_stores(self):
        pr = self._request(ref='PR-INDIV-PARTIAL-RET')
        out = self._load_with_failures(pr, n_fail=3)
        pr.refresh_from_db()
        self.assertEqual(out['reason'], pr.fnb_load_error)
        self.assertIn('5 of 8', out['reason'])

    def test_a_clean_full_load_still_clears_the_error(self):
        """The guard must not start leaving stale messages on a good load."""
        pr = self._request(ref='PR-INDIV-CLEAN', fnb_load_error='an old failure')
        self.assertTrue(self._load(pr)['loaded'])
        pr.refresh_from_db()
        self.assertEqual(pr.fnb_load_error, '')
