"""The bank must be instructed the amount the GL says leaves the bank.

THE DEFECT (2026-09-20). A P100,000 payment to a non-exempt broker books:

    Dr  Broker payable      100,000
    Cr  Bank                 90,000     <- the cash that actually leaves
    Cr  WHT payable (2170)   10,000     <- withheld, remitted to BURS later

and both bank rails then instructed the GROSS:

    fnb/payments.py       amount = Decimal(p.amount)
    payments/eft_export.py cents = _amount_to_cents(p.amount_bwp or p.amount)

So the broker is paid the tax as well, and a BURS liability is booked against
cash that never stayed with us. Neither file contained the string 'wht'.

WHICH FIGURE IS CORRECT TO SEND: the one the GL credits the bank — gross less
withholding tax less any early-settlement discount. That is, by definition, the
cash leaving the account, and it is what the payee is owed: the 10% is ours to
hold and hand to BURS, and the discount is money the supplier agreed we may
keep. Instructing the gross does not merely mis-state a report — it puts the
wrong number in front of the person approving on their phone, who sees it
presented as correct. Omni still moves no money; the release is the bank app
with two factors. The wrong AMOUNT reaching that app is the loss.

The two rails are tested together on purpose: they must agree with the ledger
and with each other, or the same payment settles differently depending on which
file Finance used.

Run: manage.py test payments.tests.test_bank_amount_net_of_wht
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from banking.models import BankAccount
from billing.models import Contact
from core.models import Company, Currency
from ledger.models import Account, FiscalPeriod
from payments.eft_export import build_fnb_bol
from payments.models import Payment

GROSS = Decimal('100000.00')
WHT = Decimal('10000.00')          # 10% BURS withholding on broker commission
NET = Decimal('90000.00')


class BankAmountIsNetOfWhtTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='WHT1', defaults={'name': 'WHT Rail Test Co',
                                   'base_currency': cls.bwp})
        cls.maker = User.objects.create_user('wht_maker', password='x')
        cls.approver = User.objects.create_user('wht_approver', password='x')

        cls.gl = Account.objects.create(
            code='WHT-BANK', name='WHT test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='WHT test current',
            account_number='8' * 11, currency_code_id='BWP')

        # An OPEN period covering payment_date, or confirm() refuses before the
        # arithmetic under test runs.
        today = date.today()
        FiscalPeriod.objects.create(
            company=cls.company, period_name=today.strftime('%Y-%m'),
            start_date=date(today.year - 1, 1, 1),
            end_date=date(today.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )
        # 2130 (broker payable) and 2170 (WHT payable) must exist.
        call_command('setup_chart_of_accounts')

        cls.broker = Contact.objects.create(
            contact_type='broker', name='Non-exempt Broker (Pty) Ltd',
            currency_code=cls.bwp, company=cls.company, wht_exempt=False)
        cls.exempt_broker = Contact.objects.create(
            contact_type='broker', name='Exempt Broker (Pty) Ltd',
            currency_code=cls.bwp, company=cls.company, wht_exempt=True)

    # ------------------------------------------------------------------
    def _payment(self, contact, amount=GROSS, ref='WHT-RAIL-1'):
        """A once-off outbound payment — destination captured inline, so both
        rails read the same payee fields and no vendor-bank register is
        needed."""
        p = Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=contact, company=self.company, bank_account=self.gl,
            payment_date=date.today(), currency_code=self.bwp,
            amount=amount,
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            reference=ref, description='Broker commission',
            status=Payment.Status.DRAFT, created_by=self.maker,
            is_once_off=True, payee_name=contact.name,
            payee_bank_name='FNB Botswana', payee_account_number='1234567',
            payee_branch_code='293567',
            bank_beneficiary_name=contact.name[:140],
            bank_our_reference=ref, bank_narration='Broker commission',
        )
        return p

    def _confirmed(self, contact, amount=GROSS, ref='WHT-RAIL-1'):
        p = self._payment(contact, amount, ref)
        # _system=True: this is the automated path's confirm, not an
        # interactive one — the dual-control quorum is not what is under test.
        p.confirm(self.approver, _system=True)
        p.refresh_from_db()
        return p

    def _fnb_amount(self, p):
        from fnb.payments import build_batch_payload
        payload = build_batch_payload(
            [p], source_account=self.source, idempotency_key='WHT-RAIL-TEST')
        txns = payload['paymentInformation'][0][
            'creditTransferTransactionInformation']
        self.assertEqual(len(txns), 1)
        return Decimal(str(txns[0]['amount']['value'])), payload

    def _bol_cents(self, p):
        text, summary = build_fnb_bol(
            [p], source_account_number='62000000000',
            run_date=date.today(), batch_ref='WHT-RAIL-TEST')
        return summary['total_cents'], text

    # ── context: the GL side is right and must stay right ───────────────
    def test_the_gl_credits_the_bank_net_and_books_the_tax(self):
        """Not the defect — the invariant the rails have to match.

        Pinned so that a future 'fix' cannot make the two sides agree by
        breaking the ledger instead of the file.
        """
        p = self._confirmed(self.broker, ref='WHT-GL')
        lines = {ln.account.code: ln for ln in p.journal_entry.lines.all()}
        self.assertEqual(lines[self.gl.code].credit_bwp, NET)
        self.assertEqual(lines['2170'].credit_bwp, WHT)
        self.assertEqual(p.wht_record.wht_amount, WHT)

    # ── the defect ──────────────────────────────────────────────────────
    def test_the_fnb_rail_instructs_the_cash_that_leaves_not_the_gross(self):
        p = self._confirmed(self.broker, ref='WHT-FNB')
        amount, payload = self._fnb_amount(p)
        self.assertEqual(
            amount, NET,
            f'FNB was told {amount}; the GL credits the bank {NET} and books '
            f'{WHT} to BURS. The broker is overpaid by the withholding.')
        self.assertEqual(
            Decimal(str(payload['groupHeader']['totalControlSum'])), NET)

    def test_the_bol_file_instructs_the_cash_that_leaves_not_the_gross(self):
        p = self._confirmed(self.broker, ref='WHT-BOL')
        cents, text = self._bol_cents(p)
        self.assertEqual(cents, 9000000,
                         f'BOL file total {cents} cents, expected 9,000,000')
        self.assertIn(str(9000000).rjust(15, '0'), text)

    def test_the_two_rails_cannot_disagree(self):
        p = self._confirmed(self.broker, ref='WHT-BOTH')
        amount, _ = self._fnb_amount(p)
        cents, _ = self._bol_cents(p)
        self.assertEqual(int(amount * 100), cents)

    # ── the other direction: nothing withheld, nothing deducted ─────────
    def test_a_wht_exempt_broker_is_still_instructed_the_full_amount(self):
        p = self._confirmed(self.exempt_broker, ref='WHT-EXEMPT')
        amount, _ = self._fnb_amount(p)
        cents, _ = self._bol_cents(p)
        self.assertEqual(amount, GROSS)
        self.assertEqual(cents, 10000000)

    def test_an_unconfirmed_broker_payment_is_instructed_GROSS(self):
        """Netting keys on a BOOKED WithholdingTaxRecord, never on a guess.

        The first version also computed the tax speculatively from
        contact_type='broker' and wht_exempt — which DEFAULTS to False — so a
        broker payment loaded to the bank before confirm() would have gone out
        10% lighter than its invoice on Omni's assumption that withholding
        applies. Whether Alpha Direct operates the 10% BURS withholding is a
        TAX POSITION and not this function's to assume; guessing it underpays
        a real supplier.

        Keyed on the record the two sides cannot disagree: confirm() books the
        record and credits Bank net in the SAME transaction, so before confirm
        there is no journal entry to be out of step with.
        """
        p = self._payment(self.broker, ref='WHT-DRAFT')
        amount, _ = self._fnb_amount(p)
        self.assertEqual(amount, GROSS)

    # ── the same arithmetic on the discount leg ─────────────────────────
    def test_an_early_settlement_discount_comes_off_the_bank_instruction_too(self):
        """Cr Bank = gross - WHT - discount, so the file must say the same.

        The pay-run hands the computed discount to confirm() on the payment
        object; the same figure has to reach the bank, or we instruct the full
        amount and then book a discount we never took.
        """
        p = self._payment(self.broker, ref='WHT-DISC')
        p._early_pay_discount_bwp = Decimal('5000.00')
        amount, _ = self._fnb_amount(p)
        # No WHT record booked on this draft, so only the discount comes off —
        # exactly what the GL would credit at this moment.
        self.assertEqual(amount, GROSS - Decimal('5000.00'))

    def test_an_unsaved_preview_stand_in_still_instructs_its_amount(self):
        """amount_bwp defaults to 0 and is only filled in by save(), and the
        payload builder is called against unsaved stand-ins for the preview
        screen. Reading amount_bwp blindly would instruct P0.00."""
        vendor = Contact.objects.create(
            contact_type='vendor', name='Preview Vendor Ltd',
            currency_code=self.bwp, company=self.company)
        p = Payment(
            payment_type=Payment.PaymentType.SENT,
            contact=vendor, company=self.company, bank_account=self.gl,
            payment_date=date.today(), currency_code=self.bwp,
            amount=Decimal('750.00'),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            reference='WHT-PREVIEW', is_once_off=True,
            payee_name=vendor.name, payee_bank_name='FNB Botswana',
            payee_account_number='1234567', payee_branch_code='293567',
            bank_beneficiary_name=vendor.name, bank_our_reference='WHT-PREVIEW',
            bank_narration='Preview', created_by=self.maker)
        self.assertEqual(p.amount_bwp, Decimal('0.00'))  # precondition
        amount, _ = self._fnb_amount(p)
        self.assertEqual(amount, Decimal('750.00'))

    def test_a_plain_vendor_payment_is_unchanged(self):
        """No withholding, no discount — the rails must send exactly what they
        always sent. This is the case every existing test exercises."""
        vendor = Contact.objects.create(
            contact_type='vendor', name='Plain Vendor Ltd',
            currency_code=self.bwp, company=self.company)
        p = self._payment(vendor, amount=Decimal('1234.56'), ref='WHT-PLAIN')
        amount, _ = self._fnb_amount(p)
        self.assertEqual(amount, Decimal('1234.56'))
