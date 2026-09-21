"""
The custodian amending a voucher — Keetile's request, CFO approved 5 August 2026.

"Please allow the petty cash custodian (approver) to edit or amend both the amount requested by
the requestor and the GL line selected."

Allowing it is the easy part. These tests are mostly about the three things that keep it safe:
a reason is compulsory, the requester's original figure survives, and a signature given to the
OLD figure is cleared — otherwise a voucher could be signed at P25 and posted at P2,500.
"""
import datetime as dt
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from petty_cash import services
from petty_cash.models import PettyCashVoucher

User = get_user_model()
MEDIA = tempfile.mkdtemp(prefix='petty-amend-test-')


@override_settings(MEDIA_ROOT=MEDIA)
class AmendVoucherTests(TestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from core.models import Company, UserProfile
        from ledger.models import Account, Currency
        from petty_cash.models import PettyCashLocation

        self.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        company, _ = Company.objects.get_or_create(
            code='PCAM', defaults={'name': 'Petty Cash Amend Test', 'base_currency': self.bwp})
        self.expense, _ = Account.objects.get_or_create(
            code='PC-EXP-AM', defaults={'name': 'Refreshments', 'account_type': 'expense',
                                        'currency_code': self.bwp})
        self.other_expense, _ = Account.objects.get_or_create(
            code='PC-EXP-AM2', defaults={'name': 'Staff welfare', 'account_type': 'expense',
                                         'currency_code': self.bwp})
        self.not_expense, _ = Account.objects.get_or_create(
            code='PC-ASSET-AM', defaults={'name': 'Some asset', 'account_type': 'asset',
                                          'currency_code': self.bwp})
        float_acc, _ = Account.objects.get_or_create(
            code='PC-FLOAT-AM', defaults={'name': 'Float', 'account_type': 'asset',
                                          'currency_code': self.bwp})
        bank, _ = Account.objects.get_or_create(
            code='PC-BANK-AM', defaults={'name': 'Bank', 'account_type': 'asset',
                                         'currency_code': self.bwp, 'is_bank_account': True})
        self.location = PettyCashLocation.objects.create(
            company=company, name='Amend Test Office', float_amount=Decimal('5000.00'),
            petty_cash_account=float_acc, reimbursing_bank_account=bank)

        # A custodian the service will accept, and a requester it will not.
        self.custodian = User.objects.create_user('amend-custodian', password='x',
                                                 email='cust@example.co.bw', is_superuser=True)
        self.requester = User.objects.create_user('amend-requester', password='x',
                                                 email='req@example.co.bw')
        self.voucher = PettyCashVoucher.objects.create(
            location=self.location, voucher_date=dt.date(2026, 8, 5), payee='Shop',
            amount=Decimal('25.00'), expense_account=self.expense,
            description='Cash to buy refreshments',
            status=PettyCashVoucher.Status.PENDING_APPROVAL,
            submitted_by=self.requester)

    def _amend(self, **kw):
        kw.setdefault('reason', 'Till slip says 30.00, not 25.00.')
        return services.amend_voucher(self.voucher, self.custodian, **kw)

    # ── what Keetile asked for ───────────────────────────────────────────────
    def test_the_custodian_can_correct_the_amount(self):
        v = self._amend(amount=Decimal('30.00'))
        self.assertEqual(v.amount, Decimal('30.00'))

    def test_the_custodian_can_change_the_gl_line(self):
        v = self._amend(expense_account=self.other_expense)
        self.assertEqual(v.expense_account_id, self.other_expense.id)

    def test_both_at_once(self):
        v = self._amend(amount=Decimal('40.00'), expense_account=self.other_expense)
        self.assertEqual(v.amount, Decimal('40.00'))
        self.assertEqual(v.expense_account_id, self.other_expense.id)

    # ── the three things that keep it safe ───────────────────────────────────
    def test_the_original_figure_is_kept(self):
        v = self._amend(amount=Decimal('30.00'))
        self.assertEqual(v.original_amount, Decimal('25.00'))
        self.assertEqual(v.amended_by_id, self.custodian.id)
        self.assertIsNotNone(v.amended_at)
        self.assertIn('Till slip', v.amend_reason)

    def test_the_original_survives_a_SECOND_amendment(self):
        # The first figure is the one the requester asked for; later corrections must not
        # overwrite it with an intermediate value.
        self._amend(amount=Decimal('30.00'))
        v = self._amend(amount=Decimal('35.00'), reason='Second look at the slip.')
        self.assertEqual(v.original_amount, Decimal('25.00'))
        self.assertEqual(v.amount, Decimal('35.00'))

    def test_a_reason_is_compulsory(self):
        with self.assertRaises(ValidationError):
            services.amend_voucher(self.voucher, self.custodian,
                                   amount=Decimal('30.00'), reason='   ')

    def test_a_signature_already_given_is_cleared(self):
        self.voucher.first_approved_by = self.custodian
        self.voucher.first_approved_at = dt.datetime(2026, 8, 5, 9, 0)
        self.voucher.status = PettyCashVoucher.Status.ONE_SIGNATURE
        self.voucher.save()

        v = self._amend(amount=Decimal('2500.00'), reason='Wrong by a factor of a hundred.')
        # Signing means agreeing to a FIGURE — the old signature cannot carry to a new one.
        self.assertIsNone(v.first_approved_by_id)
        self.assertIsNone(v.approved_by_id)
        self.assertEqual(v.status, PettyCashVoucher.Status.PENDING_APPROVAL)

    # ── what it refuses ─────────────────────────────────────────────────────
    def test_a_non_custodian_cannot_amend(self):
        with self.assertRaises(ValidationError):
            services.amend_voucher(self.voucher, self.requester,
                                   amount=Decimal('30.00'), reason='I want more.')

    def test_a_posted_voucher_cannot_be_amended(self):
        self.voucher.status = PettyCashVoucher.Status.POSTED
        self.voucher.save()
        with self.assertRaises(ValidationError):
            self._amend(amount=Decimal('30.00'))

    def test_a_zero_or_negative_amount_is_refused(self):
        for bad in (Decimal('0.00'), Decimal('-5.00')):
            with self.assertRaises(ValidationError):
                services.amend_voucher(self.voucher, self.custodian, amount=bad,
                                       reason='Trying it on.')

    def test_a_non_expense_gl_account_is_refused(self):
        with self.assertRaises(ValidationError):
            self._amend(expense_account=self.not_expense)

    def test_changing_nothing_is_refused(self):
        with self.assertRaises(ValidationError):
            self._amend(amount=Decimal('25.00'))      # same as it already is
