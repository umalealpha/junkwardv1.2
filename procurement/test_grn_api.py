"""
procurement/test_grn_api.py — the GRN create→post round-trip through the REST API.

Oprah Mogomotsi 2026-08-29 (PO-CLM-2026-000350, PO-ADM-2026-000036, -000042):
"Receive Goods" created the GRN (POST /api/v1/goods-receipt-notes/ → 201) but the
follow-up POST /api/v1/goods-receipt-notes/undefined/post_grn/ 404'd — the literal
string "undefined" was in the URL because the 201 response carried no `id`.

Root cause: GoodsReceiptNoteCreateSerializer.Meta.fields omitted 'id', and DRF
re-serialises the created object through that same serializer for the 201 body, so
the frontend read grn.id === undefined.

The FIRST test below fails without the fix (the 201 body has no 'id'). It exercises
the exact API contract the frontend relies on, which the pre-existing ORM-only GRN
test (procurement/tests.py) never touched.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from billing.models import Contact
from core.models import Company, Currency, UserProfile
from ledger.models import Account, FiscalPeriod
from procurement.models import (
    GoodsReceiptNote, PurchaseOrder, PurchaseOrderLine,
)


class GRNCreateThenPostApiTest(APITestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TGRN', name='GRN API Test Co.')

        # An open period covering the receipt date so post_grn's JE can post.
        cls.fiscal = FiscalPeriod.objects.create(
            period_name='2026-08', start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31), status=FiscalPeriod.Status.OPEN)

        # GR-IR clearing account — post_grn credits (and, for account-less PO
        # lines, also debits) code 2145.
        Account.objects.create(
            code='2145', name='Goods Received Not Invoiced',
            account_type='liability', sub_type='current_liability', is_active=True)

        cls.user = User.objects.create_user('grn-api', password='x', is_superuser=True)
        UserProfile.objects.create(user=cls.user, title=UserProfile.Title.CFO, is_active=True)

        cls.supplier = Contact.objects.create(
            name='Choppies Distribution (Pty) Ltd', contact_type='vendor',
            is_related_party=False, company=cls.company)

        # An APPROVED PO with one account-less line (current CFO directive: POs
        # carry no GL account; receipt nets against GR-IR).
        cls.po = PurchaseOrder.objects.create(
            po_number='PO-ADM-2026-090001', issue_date=date(2026, 8, 10),
            department='admin', supplier=cls.supplier, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'), company=cls.company,
            created_by=cls.user, justification='ops')
        cls.line = PurchaseOrderLine.objects.create(
            purchase_order=cls.po, description='White Sugar 12.5kg',
            quantity=Decimal('3'), unit_price=Decimal('100.00'),
            line_total=Decimal('300.00'))
        cls.po.recalculate_totals()
        cls.po.save()
        PurchaseOrder.objects.filter(pk=cls.po.pk).update(
            status=PurchaseOrder.Status.APPROVED)
        cls.po.refresh_from_db()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_payload(self, qty='3'):
        return {
            'purchase_order': str(self.po.pk),
            'receipt_date': '2026-08-29',
            'delivery_note_reference': 'Choppies',
            'received_by': self.user.pk,
            'lines': [{'po_line': str(self.line.pk),
                       'quantity_received': qty, 'condition': 'good'}],
        }

    def test_create_response_carries_id(self):
        """The 201 body MUST include the new GRN id — this is the bug fix.

        Without 'id' in GoodsReceiptNoteCreateSerializer.Meta.fields the frontend
        builds .../undefined/post_grn/ and the GRN can never be posted.
        """
        res = self.client.post('/api/v1/goods-receipt-notes/',
                               self._create_payload(), format='json')
        self.assertEqual(res.status_code, 201, res.content[:400])
        body = res.json()
        self.assertIn('id', body, "201 response is missing 'id' — post_grn URL would be /undefined/")
        self.assertTrue(body['id'], "GRN id came back empty/null")
        # grn_number is the human ref the success toast shows.
        self.assertIn('grn_number', body)

    def test_create_then_post_round_trip(self):
        """End-to-end: the id from create routes a real post_grn (not a 404)."""
        create = self.client.post('/api/v1/goods-receipt-notes/',
                                  self._create_payload(), format='json')
        self.assertEqual(create.status_code, 201, create.content[:400])
        grn_id = create.json().get('id')
        self.assertTrue(grn_id, "no id to post with — the reported bug")

        post = self.client.post(
            f'/api/v1/goods-receipt-notes/{grn_id}/post_grn/', {}, format='json')
        self.assertNotEqual(post.status_code, 404,
                            "post_grn 404'd — the GRN id did not route (the reported defect)")
        self.assertEqual(post.status_code, 200, post.content[:400])

        grn = GoodsReceiptNote.objects.get(pk=grn_id)
        self.assertEqual(grn.status, GoodsReceiptNote.Status.POSTED)

    def test_post_with_undefined_id_404s(self):
        """Guard: the literal 'undefined' (the pre-fix URL) must 404, proving the
        symptom was a missing id, not a broken endpoint."""
        res = self.client.post(
            '/api/v1/goods-receipt-notes/undefined/post_grn/', {}, format='json')
        self.assertEqual(res.status_code, 404)


class MatchBillDropdownFilterTest(APITestCase):
    """The 'Match bill' picker on a PO must list the vendor bills captured against
    THAT PO (Invoice.purchase_order), not every bill of the supplier.

    Omogomotsi 2026-09-02 (PO-ADM-2026-000007): the picker queried by supplier
    only and showed nothing. Red-first: without the new `purchase_order` filter on
    /invoices/, this test fails because the other PO's bill also comes back.
    """

    @classmethod
    def setUpTestData(cls):
        from billing.models import Invoice
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TMB', name='Match Bill Co.')
        cls.user = User.objects.create_user('mb-api', password='x', is_superuser=True)
        UserProfile.objects.create(user=cls.user, title=UserProfile.Title.CFO, is_active=True)
        cls.supplier = Contact.objects.create(
            name='OMEGA AUTOWORLD', contact_type='vendor',
            is_related_party=False, company=cls.company)

        def _po(num):
            return PurchaseOrder.objects.create(
                po_number=num, issue_date=date(2026, 8, 10), department='admin',
                supplier=cls.supplier, currency_code_id='BWP',
                exchange_rate=Decimal('1.0'), company=cls.company,
                created_by=cls.user, justification='ops')
        cls.po_a = _po('PO-ADM-2026-090010')
        cls.po_b = _po('PO-ADM-2026-090011')

        def _bill(po, num):
            return Invoice.objects.create(
                invoice_number=num, invoice_type='vendor_bill', contact=cls.supplier,
                company=cls.company, issue_date=date(2026, 8, 15),
                currency_code_id='BWP', total_amount=Decimal('4500.00'),
                purchase_order=po, created_by=cls.user)
        cls.bill_a = _bill(cls.po_a, 'BILL-A-1')
        cls.bill_b = _bill(cls.po_b, 'BILL-B-1')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_dropdown_lists_only_this_pos_bills(self):
        res = self.client.get(
            '/api/v1/invoices/',
            {'invoice_type': 'vendor_bill', 'purchase_order': str(self.po_a.pk),
             'page_size': '100'})
        self.assertEqual(res.status_code, 200, res.content[:300])
        ids = {r['id'] for r in res.json()['results']}
        self.assertIn(str(self.bill_a.id), ids, "the PO's own bill must appear in the picker")
        self.assertNotIn(str(self.bill_b.id), ids,
                         "another PO's bill leaked into the picker (purchase_order filter missing)")
