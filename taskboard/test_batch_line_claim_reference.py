"""taskboard/test_batch_line_claim_reference.py — every bank row names its OWN claim.

THE INCIDENT. Pako Kago and Kago Tshutlhedi reported this on 9-Sep-2026 against
the live batch `EFT 2 payments 000149 (O)` (BWP 74,078.39, two rows to the same
repairer). The request covered TWO claims:

    line 1   claim G2026004782   invoice 3522   30,288.33
    line 2   claim G2026004829   invoice 3525   43,790.06

and the two bank rows went out reading

    ALPHA DIRECT G2026004782 3522 - 3522
    ALPHA DIRECT G2026004782 3522 - 3525

Claim G2026004829 never reached the bank. The repairer could not allocate the
two amounts (FNB's list shows the HEAD of the reference, and the head was
identical on both rows) and the statement could not be matched back to the
second claim. The cause: the request's wording is built ONCE from the first
payable line, and the per-line code only swapped in the line's own invoice
number — the claim number stayed the first line's on every row.

What these tests pin:
  - Each row's narration and reference carry THAT line's claim and invoice.
  - Neither row mentions the other row's claim.
  - The invoice number still survives on the reference (the discriminator when
    two invoices sit on one claim) — test_individual_eft_rows' rule.
  - Wording a person typed themselves is NOT rebuilt: their words win, and the
    line's invoice is still appended so the rows differ.
  - A payment type the line alone cannot build (OTHER) keeps the old behaviour.
  - Bulk is untouched.

Omni moves no money here. The load hands FNB a batch which is then authorised
in the FNB app with two factors. Claim and invoice numbers below are the real
shape but no payee or staff name is used.

Run: manage.py test taskboard.test_batch_line_claim_reference
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from banking.models import BankAccount
from core.models import Company, Currency
from ledger.models import Account
from taskboard.models import PaymentRequest

SOURCE_ACCT = '7' * 11

# The shape of the reported request: one repairer, two claims, two invoices.
TWO_CLAIMS = [
    {'ref': 'G2026004782-3522 REPAIRER', 'amount': '30288.33',
     'description': 'G2026004782-3522 REPAIRER', 'claim_number': 'G2026004782',
     'invoice_number': '3522', 'invoice_date': '2026-07-22',
     'terms_days': '30', 'terms_basis': 'invoice', 'due_date': '2026-08-21'},
    {'ref': 'G2026004829-3525 REPAIRER', 'amount': '43790.06',
     'description': 'G2026004829-3525 REPAIRER', 'claim_number': 'G2026004829',
     'invoice_number': '3525', 'invoice_date': '2026-07-31',
     'terms_days': '30', 'terms_basis': 'invoice', 'due_date': '2026-08-30'},
]
TWO_TOTAL = Decimal('74078.39')

# What capture stored on the live request: built from the FIRST line only.
REQUEST_NARRATION = 'ALPHA DIRECT G2026004782 3522'
REQUEST_OUR_REF = 'G2026004782 REPAIR'


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='CLMREF', defaults={'name': 'Claim Reference Test Co',
                                     'base_currency': cls.bwp})
        cls.raiser = User.objects.create_user('clmref_raiser', password='x')
        cls.gl = Account.objects.create(
            code='CLMREF-BANK', name='Claim ref test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True, owner_company=cls.company)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='Claim ref current',
            account_number=SOURCE_ACCT, currency_code_id='BWP')

    def _request(self, ref='PR-CLMREF-0001', **over):
        kwargs = dict(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.CLAIM, currency='BWP',
            subject='G2026004782-3522 & 3525 REPAIRER',
            payee='Test Repairer', line_items=TWO_CLAIMS, total=TWO_TOTAL,
            processing_method=PaymentRequest.ProcessingMethod.INDIVIDUAL,
            account_name='Test Repairer (Pty) Ltd', account_number='9060001385270',
            bank_name='FNB Botswana', branch_code='060367', account_type='CACC',
            bank_payment_type='repair',
            bank_narration=REQUEST_NARRATION,
            bank_our_reference=REQUEST_OUR_REF,
            status=PaymentRequest.Status.PENDING_CFO, created_by=self.raiser,
        )
        kwargs.update(over)
        return PaymentRequest.objects.create(**kwargs)

    def _build(self, pr):
        from taskboard.fnb_autoload import build_payments_for_request
        return build_payments_for_request(pr, self.company, self.source)


class PerLineClaimReferenceTests(_Base):

    def test_each_row_carries_its_own_claim_number(self):
        rows = self._build(self._request())
        self.assertEqual(len(rows), 2)
        by_amount = {str(r.amount): r for r in rows}
        first = by_amount['30288.33']
        second = by_amount['43790.06']

        self.assertEqual(first.bank_narration, 'ALPHA DIRECT G2026004782 3522')
        self.assertEqual(second.bank_narration, 'ALPHA DIRECT G2026004829 3525')

    def test_neither_row_quotes_the_other_rows_claim(self):
        # The whole reported defect in one assertion: the second row went out
        # naming claim G2026004782, which belongs to the first row.
        rows = self._build(self._request(ref='PR-CLMREF-0002'))
        by_amount = {str(r.amount): r for r in rows}
        self.assertNotIn('G2026004829', by_amount['30288.33'].bank_narration)
        self.assertNotIn('G2026004782', by_amount['43790.06'].bank_narration)
        self.assertNotIn('G2026004782', by_amount['43790.06'].bank_our_reference)

    def test_the_reference_still_ends_with_the_lines_own_invoice(self):
        rows = self._build(self._request(ref='PR-CLMREF-0003'))
        by_amount = {str(r.amount): r for r in rows}
        self.assertEqual(by_amount['30288.33'].bank_our_reference,
                         'G2026004782 REPAIR 3522')
        self.assertEqual(by_amount['43790.06'].bank_our_reference,
                         'G2026004829 REPAIR 3525')

    def test_no_row_exceeds_fnbs_field_lengths(self):
        rows = self._build(self._request(ref='PR-CLMREF-0004'))
        for r in rows:
            self.assertLessEqual(len(r.bank_our_reference), 35)
            self.assertLessEqual(len(r.bank_narration), 140)

    def test_the_rows_still_sum_to_the_request_total(self):
        rows = self._build(self._request(ref='PR-CLMREF-0005'))
        self.assertEqual(sum((r.amount for r in rows), Decimal('0.00')),
                         TWO_TOTAL)


class OperatorWordingSurvivesTests(_Base):

    def test_wording_a_person_typed_is_not_rebuilt(self):
        # Finance's rule: anyone raising a payment may edit both fields and the
        # control sits at approval. So typed words win, and the line's invoice
        # is still appended so two rows never read identically.
        rows = self._build(self._request(
            ref='PR-CLMREF-TYPED',
            bank_narration='SETTLEMENT AS AGREED WITH THE PANEL'))
        narrations = sorted(r.bank_narration for r in rows)
        self.assertEqual(narrations, [
            'SETTLEMENT AS AGREED WITH THE PANEL - 3522',
            'SETTLEMENT AS AGREED WITH THE PANEL - 3525',
        ])

    def test_a_type_the_line_cannot_build_keeps_the_old_wording(self):
        # OTHER is typed by hand by design (reinsurance, payroll, statutory).
        rows = self._build(self._request(
            ref='PR-CLMREF-OTHER', bank_payment_type='other',
            bank_narration='RI QUARTERLY SETTLEMENT'))
        self.assertEqual(
            sorted(r.bank_narration for r in rows),
            ['RI QUARTERLY SETTLEMENT - 3522', 'RI QUARTERLY SETTLEMENT - 3525'])

    def test_bulk_is_unchanged(self):
        rows = self._build(self._request(
            ref='PR-CLMREF-BULK',
            processing_method=PaymentRequest.ProcessingMethod.BULK))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, TWO_TOTAL)
        self.assertEqual(rows[0].bank_narration, REQUEST_NARRATION)
        self.assertEqual(rows[0].bank_our_reference, REQUEST_OUR_REF)
