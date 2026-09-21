"""
procurement/test_po_create_returns_id.py — the create call must hand back the id.

Motlatsi Molefe, 2026-09-10, "the system is currently down". What the prod
access log actually showed:

    POST /api/v1/purchase-orders/                    201
    POST /api/v1/purchase-orders/undefined/submit/   404
    GET  /api/v1/purchase-orders/undefined/          404

The New PO page does `const po = await createPurchaseOrder(...)` and then
`submitPO(po.id)` / `router.replace('/purchase-orders/' + po.id)`.
PurchaseOrderCreateSerializer answers the create call, and its field list did
not include 'id' — so `po.id` was undefined on EVERY "Create and submit".
The PO was written as a DRAFT, was never submitted for approval, and the
raiser was dropped on a dead page.

These tests fail if 'id' or 'po_number' ever leave the create response again.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from billing.models import Contact
from core.models import Company, Currency
from procurement.serializers import PurchaseOrderCreateSerializer


class _Req:
    """Minimal stand-in for the DRF request the serializer reads."""

    def __init__(self, user):
        self.user = user


class POCreateResponseTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TESTPOID', name='PO Id Test Co.')
        cls.supplier = Contact.objects.create(
            name='Test Supplier (Pty) Ltd', contact_type='vendor',
            company=cls.company)
        cls.user = User.objects.create_superuser('poiduser', 'poid@x.co', 'x')

    def _create(self):
        payload = {
            'department': 'admin',
            'supplier': self.supplier.pk,
            'company': self.company.pk,
            'issue_date': date(2026, 9, 10),
            'justification': 'Replacing the office kettle.',
            'lines': [{'description': 'Kettle', 'quantity': Decimal('1'),
                       'unit_price': Decimal('450.00')}],
        }
        ser = PurchaseOrderCreateSerializer(
            data=payload, context={'request': _Req(self.user)})
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        return ser.data

    def test_create_response_carries_the_id(self):
        # Without this the page submits /purchase-orders/undefined/submit/.
        data = self._create()
        self.assertIn('id', data)
        self.assertTrue(data['id'])
        self.assertNotEqual(str(data['id']), 'undefined')

    def test_create_response_carries_the_po_number(self):
        data = self._create()
        self.assertIn('po_number', data)
        self.assertTrue(data['po_number'])

    def test_id_and_po_number_cannot_be_set_by_the_caller(self):
        # Read-only: a caller must not choose its own PO number or primary key.
        ser = PurchaseOrderCreateSerializer(
            data={
                'id': '00000000-0000-0000-0000-000000000001',
                'po_number': 'ADMIN-9999',
                'department': 'admin',
                'supplier': self.supplier.pk,
                'company': self.company.pk,
                'issue_date': date(2026, 9, 10),
                'justification': 'Replacing the office kettle.',
                'lines': [{'description': 'Kettle', 'quantity': Decimal('1'),
                           'unit_price': Decimal('450.00')}],
            },
            context={'request': _Req(self.user)})
        self.assertTrue(ser.is_valid(), ser.errors)
        po = ser.save()
        self.assertNotEqual(str(po.pk), '00000000-0000-0000-0000-000000000001')
        self.assertNotEqual(po.po_number, 'ADMIN-9999')
