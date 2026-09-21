"""
procurement/test_po_discount.py — PO discount (PR #336, restored 2026-07-08).

The feature shipped WITHOUT tests and was silently wiped by an unrelated
commit (22e2a11) the same day — nobody noticed until a PO needed it. These
tests exist so that can never happen quietly again.

Math contract (CFO 2026-07-08, Wame/Native Events 5%):
  * one uniform discount_percent on the PO, netted off each line's gross
    BEFORE VAT — so VAT, subtotal, total_amount and the commitment GL are
    all struck on the discounted figures;
  * subtotal is stored NET of the discount; discount_total is display-only;
  * discount_percent = 0 leaves legacy maths bit-for-bit unchanged.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from billing.models import Contact
from core.models import Company, Currency, TaxRate
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.serializers import PurchaseOrderCreateSerializer


class PODiscountMathTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TESTD2', name='Discount Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%', rate=Decimal('14.00'),
            is_active=True, effective_from=date(2026, 1, 1))
        cls.supplier = Contact.objects.create(
            name='Native Events (Pty) Ltd', contact_type='vendor',
            company=cls.company)
        cls.user = User.objects.create_user('discuser', password='x')

    def _po(self, discount, line_specs):
        po = PurchaseOrder.objects.create(
            department='admin', supplier=self.supplier, company=self.company,
            issue_date=date(2026, 7, 8), created_by=self.user,
            discount_percent=Decimal(str(discount)))
        for spec in line_specs:
            PurchaseOrderLine.objects.create(
                purchase_order=po, description=spec['d'],
                quantity=Decimal(str(spec.get('q', 1))),
                unit_price=Decimal(str(spec['p'])),
                tax_code=self.vat if spec.get('vat') else None)
        po.recalculate_totals()
        po.save()
        return po

    def test_five_percent_nets_before_vat(self):
        # Wame's case: 5% off, VAT struck on the discounted amount.
        po = self._po(5, [
            {'d': 'Event package', 'p': 1000, 'vat': True},
            {'d': 'Zero-rated levy', 'p': 500},
        ])
        l1, l2 = list(po.lines.order_by('created_at'))
        self.assertEqual(l1.discount_amount, Decimal('50.00'))
        self.assertEqual(l1.line_total,      Decimal('950.00'))
        self.assertEqual(l1.tax_amount,      Decimal('133.00'))   # 14% of 950
        self.assertEqual(l2.discount_amount, Decimal('25.00'))
        self.assertEqual(l2.line_total,      Decimal('475.00'))
        self.assertEqual(l2.tax_amount,      Decimal('0.00'))
        # Header: subtotal NET of discount; discount_total display-only.
        self.assertEqual(po.subtotal,       Decimal('1425.00'))
        self.assertEqual(po.discount_total, Decimal('75.00'))
        self.assertEqual(po.tax_total,      Decimal('133.00'))
        self.assertEqual(po.total_amount,   Decimal('1558.00'))

    def test_zero_discount_is_legacy_identical(self):
        po = self._po(0, [{'d': 'Repair work', 'p': 5000, 'vat': True}])
        ln = po.lines.first()
        self.assertEqual(ln.discount_amount, Decimal('0.00'))
        self.assertEqual(ln.line_total,      Decimal('5000.00'))
        self.assertEqual(po.subtotal,        Decimal('5000.00'))
        self.assertEqual(po.discount_total,  Decimal('0.00'))
        self.assertEqual(po.total_amount,    Decimal('5700.00'))

    def test_serializer_rejects_out_of_range_discount(self):
        for bad in ('-1', '101'):
            ser = PurchaseOrderCreateSerializer(data={
                'department': 'admin', 'supplier': str(self.supplier.pk),
                'company': str(self.company.pk), 'issue_date': '2026-07-08',
                'currency_code': 'BWP', 'discount_percent': bad,
                'lines': [{'description': 'X', 'quantity': '1',
                           'unit_price': '100'}],
            })
            self.assertFalse(ser.is_valid(), bad)
            self.assertIn('discount_percent', ser.errors, bad)
