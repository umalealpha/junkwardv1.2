"""procurement/test_claim_po_api.py — the PO-in-Graphite doorway.

GET /api/v1/purchase-orders/by-claim/?claim_ref=... — the one read-only endpoint
Graphite calls to show a claim's purchase order(s). Pins the agreed contract
(2026-08-24) and the two security promises: the key is read-only, and it must be
the DEDICATED key, not any read-only key.

Every test here fails without the view + url + scope wiring (the endpoint 404s
or the scope does not exist), so the suite goes red if the feature is reverted.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from billing.models import Contact
from core.models import ApiKey, Company, Currency, TaxRate
from procurement.models import PurchaseOrder, PurchaseOrderLine

PATH = '/api/v1/purchase-orders/by-claim/'
_ALLOWED = {'uuid', 'po_number', 'status', 'total', 'currency',
            'date', 'supplier_name', 'deep_link'}


class ClaimPOApiTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TCLM', name='Claim PO Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%', rate=Decimal('14.00'),
            is_active=True, effective_from=date(2026, 1, 1))
        cls.supplier = Contact.objects.create(
            name='Gaborone Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=cls.company)
        cls.user = User.objects.create_user('svc-po-claim', password='x')

    # ---- helpers ----------------------------------------------------------
    def _po(self, claim_ref, department='claims', status='approved'):
        po = PurchaseOrder.objects.create(
            department=department, supplier=self.supplier, company=self.company,
            issue_date=date(2026, 7, 8), created_by=self.user,
            related_claim_reference=claim_ref)
        PurchaseOrderLine.objects.create(
            purchase_order=po, description='Repair work',
            quantity=Decimal('1'), unit_price=Decimal('5000'), tax_code=self.vat)
        po.recalculate_totals()
        po.save()
        if status != po.status:
            PurchaseOrder.objects.filter(pk=po.pk).update(status=status)
            po.refresh_from_db()
        return po

    def _key(self, scopes, prefix):
        plaintext = prefix + '0' * (64 - len(prefix))
        ApiKey.objects.create(
            label='claim-po test', key_prefix=plaintext[:12],
            key_hash=make_password(plaintext), service_user=self.user,
            allowed_scopes=scopes, is_active=True)
        return plaintext

    def _get(self, claim_ref, key, method='get'):
        auth = f'ApiKey {key}'
        return getattr(self.client, method)(
            PATH, {'claim_ref': claim_ref} if claim_ref is not None else {},
            HTTP_AUTHORIZATION=auth)

    # ---- the happy path ---------------------------------------------------
    def test_matching_claim_returns_the_po(self):
        po = self._po('G2026004801')
        key = self._key(['po-claim-read'], 'aaaaaaaaaaaa')
        r = self._get('G2026004801', key)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['claim_ref'], 'G2026004801')
        self.assertEqual(len(body['purchase_orders']), 1)
        row = body['purchase_orders'][0]
        self.assertEqual(row['po_number'], po.po_number)
        self.assertEqual(row['status'], 'Approved')
        self.assertEqual(row['currency'], 'BWP')
        self.assertTrue(row['deep_link'].endswith(f'/purchase-orders/{po.id}'))

    def test_more_than_one_po_per_reference(self):
        self._po('G2026004801')
        self._po('G2026004801')
        key = self._key(['po-claim-read'], 'bbbbbbbbbbbb')
        r = self._get('G2026004801', key)
        self.assertEqual(len(r.json()['purchase_orders']), 2)

    def test_case_and_whitespace_insensitive(self):
        self._po('G2026004801')
        key = self._key(['po-claim-read'], 'cccccccccccc')
        r = self._get('  g2026004801  ', key)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['purchase_orders']), 1)

    # ---- honest quiet states ---------------------------------------------
    def test_no_po_returns_200_empty_list_not_404(self):
        key = self._key(['po-claim-read'], 'dddddddddddd')
        r = self._get('G9999999999', key)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['purchase_orders'], [])

    def test_missing_claim_ref_is_400(self):
        key = self._key(['po-claim-read'], 'eeeeeeeeeeee')
        r = self._get(None, key)
        self.assertEqual(r.status_code, 400)

    # ---- hard filters -----------------------------------------------------
    def test_only_claims_department_is_returned(self):
        self._po('G2026004801', department='admin')
        key = self._key(['po-claim-read'], 'ffffffffffff')
        r = self._get('G2026004801', key)
        self.assertEqual(r.json()['purchase_orders'], [])

    def test_no_fuzzy_match(self):
        self._po('G2026004801')
        key = self._key(['po-claim-read'], 'a1a1a1a1a1a1')
        r = self._get('G202600480', key)          # one digit short
        self.assertEqual(r.json()['purchase_orders'], [])

    # ---- no PII / allow-list ---------------------------------------------
    def test_response_carries_only_allowlisted_fields(self):
        self._po('G2026004801')
        key = self._key(['po-claim-read'], 'b2b2b2b2b2b2')
        row = self._get('G2026004801', key).json()['purchase_orders'][0]
        self.assertEqual(set(row.keys()), _ALLOWED)

    # ---- security: read-only, dedicated key only --------------------------
    def test_write_method_is_refused(self):
        self._po('G2026004801')
        key = self._key(['po-claim-read'], 'c3c3c3c3c3c3')
        r = self._get('G2026004801', key, method='post')
        self.assertNotEqual(r.status_code, 200)     # read-only key blocked at auth

    def test_a_plain_read_only_key_is_refused_the_dedicated_endpoint(self):
        self._po('G2026004801')
        key = self._key(['read-only'], 'd4d4d4d4d4d4')
        r = self._get('G2026004801', key)
        self.assertEqual(r.status_code, 403)

    def test_no_key_is_unauthorized(self):
        self._po('G2026004801')
        r = self.client.get(PATH, {'claim_ref': 'G2026004801'})
        self.assertIn(r.status_code, (401, 403))
