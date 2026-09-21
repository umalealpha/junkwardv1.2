"""A caller cannot choose the exchange rate a payment is measured in.

THE DEFECT (2026-09-20). `exchange_rate` is writable on PaymentDetailSerializer
(it is absent from read_only_fields), and validate() only went looking for the
approved rate when the client sent nothing or exactly 1:

    if currency_id != 'BWP' and _is_one(sent_rate):

Anything else was taken verbatim. Payment.save() then derives amount_bwp from
it, and amount_bwp is what drives:

    * assign_payment_tier()                      — the approval ladder
    * OutboundPaymentPolicy.dual_auth_threshold  — the second-signer rule
    * FNB_BATCH_MAX_BWP                          — the batch ceiling

so a USD 1,000,000 payment posted with exchange_rate "0.5" books as P500,000
instead of ~P13.6m and can drop an approval tier. The earlier `_is_one` fix was
written to close exactly this class and closed only the rate == 1 case.

THE RULE PINNED HERE. For a non-BWP payment the rate is the APPROVED rate on
the ExchangeRate register for that date — the same source resolve_fx_rate
already is for the rate-1 case. A rate sent by the caller is accepted only when
it agrees with that register; anything else is refused with the same sentence
the rate-1 path uses, pointing at /fx. There is no tolerance band, because a
band is just a smaller number an attacker can shade the tier with.

Run: manage.py test payments.tests.test_fx_rate_not_caller_chosen
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers as drf_serializers

from billing.models import Contact
from core.models import Company, Currency, ExchangeRate
from ledger.models import Account
from payments.serializers import PaymentDetailSerializer

PAY_DATE = date(2026, 9, 20)
APPROVED_USD_RATE = Decimal('13.60000000')


class CallerSuppliedFxRateIsCheckedTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.usd, _ = Currency.objects.get_or_create(
            code='USD', defaults={'name': 'US Dollar', 'symbol': '$'})
        cls.company, _ = Company.objects.get_or_create(
            code='FXR1', defaults={'name': 'FX Rate Test Co',
                                   'base_currency': cls.bwp})
        cls.staff = User.objects.create_user('fxr_staff', password='x')
        cls.approver = User.objects.create_user('fxr_approver', password='x')
        cls.bank = Account.objects.create(
            code='FXR-BANK', name='FX rate test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.vendor = Contact.objects.create(
            contact_type='vendor', name='Offshore Supplier Inc',
            currency_code=cls.usd, company=cls.company)

        ExchangeRate.objects.create(
            from_currency=cls.usd, to_currency=cls.bwp,
            rate=APPROVED_USD_RATE, effective_date=PAY_DATE,
            loaded_by=cls.staff, approved_by=cls.approver,
            approved_at=timezone.now(),
        )

    def _payload(self, **over):
        data = {
            'payment_type': 'sent',
            'contact': str(self.vendor.pk),
            'company': str(self.company.pk),
            'bank_account': str(self.bank.pk),
            'payment_date': PAY_DATE.isoformat(),
            'currency_code': 'USD',
            'amount': '1000000.00',
            'payment_method': 'bank_transfer',
            'reference': 'FXR-1',
            'description': 'Offshore supplier settlement',
        }
        data.update(over)
        return data

    def _validated(self, **over):
        ser = PaymentDetailSerializer(data=self._payload(**over))
        ser.is_valid(raise_exception=True)
        return ser.validated_data

    # ── the defect ──────────────────────────────────────────────────────
    def test_an_understated_rate_is_refused_not_taken_verbatim(self):
        with self.assertRaises(drf_serializers.ValidationError) as cm:
            self._validated(exchange_rate='0.5')
        self.assertIn('exchange_rate', cm.exception.detail)

    def test_an_understated_rate_never_reaches_amount_bwp(self):
        """The tier ladder, the dual-auth threshold and the FNB batch ceiling
        all read amount_bwp, so the attacker's number must never get there."""
        ser = PaymentDetailSerializer(data=self._payload(exchange_rate='0.5'))
        self.assertFalse(ser.is_valid(),
                         f'rate 0.5 was accepted: {ser.validated_data!r}')

    def test_an_overstated_rate_is_refused_too(self):
        """Guards fail in both directions — an inflated rate pushes a small
        payment up into the CFO's queue and is just as wrong."""
        with self.assertRaises(drf_serializers.ValidationError):
            self._validated(exchange_rate='99.0')

    # ── the other direction: honest traffic still works ─────────────────
    def test_the_approved_rate_sent_back_is_accepted(self):
        attrs = self._validated(exchange_rate=str(APPROVED_USD_RATE))
        self.assertEqual(Decimal(str(attrs['exchange_rate'])),
                         APPROVED_USD_RATE)

    def test_the_approved_rate_sent_without_trailing_zeros_is_accepted(self):
        """DRF quantises to 8dp; a client that sends "13.6" means the same
        rate and must not be refused on formatting."""
        attrs = self._validated(exchange_rate='13.6')
        self.assertEqual(Decimal(str(attrs['exchange_rate'])),
                         APPROVED_USD_RATE)

    def test_no_rate_sent_still_resolves_the_approved_one(self):
        attrs = self._validated()
        self.assertEqual(Decimal(str(attrs['exchange_rate'])),
                         APPROVED_USD_RATE)

    def test_rate_one_still_resolves_rather_than_booking_usd_as_bwp(self):
        attrs = self._validated(exchange_rate='1')
        self.assertEqual(Decimal(str(attrs['exchange_rate'])),
                         APPROVED_USD_RATE)

    def test_a_bwp_payment_is_untouched(self):
        attrs = self._validated(currency_code='BWP', exchange_rate='1',
                                contact=str(self.vendor.pk))
        self.assertEqual(Decimal(str(attrs['exchange_rate'])), Decimal('1'))

    def test_a_currency_with_no_approved_rate_is_still_blocked(self):
        zar, _ = Currency.objects.get_or_create(
            code='ZAR', defaults={'name': 'Rand', 'symbol': 'R'})
        with self.assertRaises(drf_serializers.ValidationError) as cm:
            self._validated(currency_code='ZAR', exchange_rate='0.75')
        self.assertIn('exchange_rate', cm.exception.detail)
