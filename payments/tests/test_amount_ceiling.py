"""PAY-CAP-01 — per-payment amount ceiling (CFO 2026-08-15, Manus QC F3).

The audit found PAY-IN-2026-000001 sitting in Draft at BWP 62,403,392,335.00
(P62.4 billion) with no ceiling anywhere in save(). Cash on hand at the same
date was ~P7.4m, so anything above a sane per-payment cap is a typo, not a
payment. These tests pin the guard so a future refactor cannot remove it
silently.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from billing.models import Contact
from core.models import Company, Currency
from ledger.models import Account
from payments.models import Payment


class PaymentAmountCeilingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='CAP1', defaults={'name': 'Cap Test Co',
                                   'base_currency': cls.bwp})
        cls.staff = User.objects.create_user('capclerk', password='x')
        cls.bank, _ = Account.objects.get_or_create(
            code='CAP-BANK',
            defaults={'name': 'Cap Test Bank', 'account_type': 'asset',
                      'currency_code': cls.bwp, 'is_bank_account': True})
        cls.payer = Contact.objects.create(
            contact_type=Contact.ContactType.CUSTOMER, name='Big Payer Ltd',
            currency_code=cls.bwp, company=cls.company)

    def _payment(self, amount):
        return Payment(
            payment_type=Payment.PaymentType.RECEIVED,
            contact=self.payer, company=self.company, bank_account=self.bank,
            payment_date=date(2026, 8, 15), currency_code=self.bwp,
            amount=Decimal(amount), payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.DRAFT, created_by=self.staff)

    def test_below_ceiling_saves(self):
        p = self._payment('99999999.99')  # just under P100m default cap
        p.save()  # must not raise
        self.assertIsNotNone(p.pk)

    def test_at_ceiling_saves(self):
        p = self._payment('100000000.00')  # exactly at P100m cap
        p.save()
        self.assertIsNotNone(p.pk)

    def test_above_ceiling_rejected(self):
        # The real prod row that triggered the rule: P62.4bn.
        p = self._payment('62403392335.00')
        with self.assertRaises(ValidationError) as cm:
            p.save()
        self.assertIn('PAY-CAP-01', str(cm.exception))
        # NB: BaseModel stamps a UUID pk on __init__, so `.pk` is set before
        # save() runs. The right check is that the row is NOT in the DB.
        self.assertFalse(Payment.objects.filter(pk=p.pk).exists())

    @override_settings(PAYMENT_MAX_AMOUNT_BWP='500000000')
    def test_setting_override_widens_ceiling(self):
        # Someone deliberately raises the ceiling to P500m: P300m now saves.
        p = self._payment('300000000.00')
        p.save()
        self.assertIsNotNone(p.pk)

    @override_settings(PAYMENT_MAX_AMOUNT_BWP='500000000')
    def test_setting_override_still_blocks_absurd(self):
        # Even with the widened P500m ceiling, P62.4bn is still refused.
        p = self._payment('62403392335.00')
        with self.assertRaises(ValidationError):
            p.save()
