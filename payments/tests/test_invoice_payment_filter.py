"""Bug 6631d0cf (Oprah, 2026-09-04): the Payment History table on an individual
vendor bill showed the SAME global payment list on every bill.

/api/v1/payments/?invoice=<id> must return ONLY payments allocated to that
invoice. Without the get_queryset `invoice` filter the endpoint ignored the
param and returned every payment (the reported defect).
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Contact, Invoice
from core.models import Company, Currency
from ledger.models import Account
from payments.models import Payment, PaymentAllocation
from payments.api_views import PaymentViewSet


class InvoicePaymentFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='IPF1', defaults={'name': 'Inv Pay Filter Co', 'base_currency': cls.bwp})
        cls.vendor = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Filter Vendor',
            currency_code=cls.bwp, company=cls.company)
        cls.bank, _ = Account.objects.get_or_create(
            code='IPF-BANK', defaults={'name': 'IPF Bank', 'account_type': 'asset',
                                       'currency_code': cls.bwp, 'is_bank_account': True})
        cls.user = User.objects.create_superuser('ipf_admin', 'a@b.co', 'x')

        def bill(total):
            return Invoice.objects.create(
                invoice_type=Invoice.InvoiceType.VENDOR_BILL, contact=cls.vendor,
                company=cls.company, issue_date=date.today(), due_date=date.today(),
                currency_code=cls.bwp, subtotal=Decimal(total), total_amount=Decimal(total),
                balance_due=Decimal(total), status=Invoice.Status.POSTED, created_by=cls.user)

        def pay(total, inv):
            p = Payment.objects.create(
                payment_type=Payment.PaymentType.SENT, contact=cls.vendor,
                company=cls.company, bank_account=cls.bank, payment_date=date.today(),
                currency_code=cls.bwp, amount=Decimal(total),
                payment_method=Payment.PaymentMethod.BANK_TRANSFER,
                status=Payment.Status.DRAFT, created_by=cls.user)
            PaymentAllocation.objects.create(payment=p, invoice=inv, amount_allocated=Decimal(total))
            return p

        cls.bill_a = bill('100.00'); cls.bill_b = bill('200.00')
        cls.pay_a = pay('100.00', cls.bill_a)
        cls.pay_b = pay('200.00', cls.bill_b)

    def _list(self, **params):
        req = APIRequestFactory().get('/api/v1/payments/', params)
        force_authenticate(req, user=self.user)
        resp = PaymentViewSet.as_view({'get': 'list'})(req)
        resp.render()
        return resp

    def test_filter_by_invoice_returns_only_that_bills_payments(self):
        resp = self._list(invoice=str(self.bill_a.id))
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data['results']}
        self.assertEqual(ids, {str(self.pay_a.id)},
                         "invoice filter must return only that bill's payments")

    def test_no_invoice_param_still_returns_all(self):
        resp = self._list()
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data['results']}
        self.assertTrue({str(self.pay_a.id), str(self.pay_b.id)} <= ids)
