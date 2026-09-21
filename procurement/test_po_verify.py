"""
procurement/test_po_verify.py — public PO verification page (verify_view.py).

The page the QR code opens. Assertions on the rendered HTML via the test
client: a real PO verifies as Authentic with its supplier/total/status; an
unknown or malformed code shows the branded "not recognised" page (404); the
internal "Pending FM/CFO Approval" wording never leaks to this external page.
"""
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, Client

from billing.models import Contact
from core.models import Company, Currency, TaxRate
from procurement.models import PurchaseOrder, PurchaseOrderLine


class POVerifyViewTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TESTV', name='Verify Test Co.')
        cls.vat = TaxRate.objects.create(
            tax_code='VAT_STD', name='Standard Rate 14%', rate=Decimal('14.00'),
            is_active=True, effective_from=date(2026, 1, 1))
        cls.supplier = Contact.objects.create(
            name='Gaborone Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=cls.company)
        cls.user = User.objects.create_user('verifyuser', password='x')

    def _po(self, status='approved'):
        # Build as a draft (an APPROVED PO is locked against edits by the
        # model's save() guard), then move it to the target status directly.
        po = PurchaseOrder.objects.create(
            department='claims', supplier=self.supplier, company=self.company,
            issue_date=date(2026, 7, 8), created_by=self.user)
        PurchaseOrderLine.objects.create(
            purchase_order=po, description='Repair work',
            quantity=Decimal('1'), unit_price=Decimal('5000'), tax_code=self.vat)
        po.recalculate_totals()
        po.save()
        if status != po.status:
            PurchaseOrder.objects.filter(pk=po.pk).update(status=status)
            po.refresh_from_db()
        return po

    def test_authentic_page_shows_details(self):
        po = self._po(status='approved')
        r = Client().get(f'/api/verify/po/{po.pk}/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Authentic', html)
        self.assertIn('Gaborone Panel Beaters', html)
        self.assertIn('Purchase Order', html)          # approved => PO, not RFQ
        self.assertIn('Approved', html)
        self.assertIn('P5,700.00', html)               # 5000 + 14% VAT
        self.assertIn('omni.alphadirect.co.bw', html)

    def test_draft_reads_as_rfq_and_hides_internal_status(self):
        po = self._po(status='pending_cfo_approval')
        html = Client().get(f'/api/verify/po/{po.pk}/').content.decode()
        self.assertIn('Request for Quotation', html)
        self.assertIn('Awaiting approval', html)
        # Internal approval wording must never surface on the external page.
        # (Check exact leak phrases, not bare 'CFO' — that substring occurs by
        # chance inside the base64-embedded logo.)
        self.assertNotIn('Pending CFO', html)
        self.assertNotIn('Pending FM', html)
        self.assertNotIn('CFO Approval', html)

    def test_unknown_uuid_shows_branded_not_found(self):
        r = Client().get(f'/api/verify/po/{uuid.uuid4()}/')
        self.assertEqual(r.status_code, 404)
        self.assertIn('not recognised', r.content.decode())

    def test_malformed_code_shows_branded_not_found(self):
        r = Client().get('/api/verify/po/not-a-real-code/')
        self.assertEqual(r.status_code, 404)
        self.assertIn('not recognised', r.content.decode())

    def test_no_buttons_view_only(self):
        po = self._po()
        html = Client().get(f'/api/verify/po/{po.pk}/').content.decode().lower()
        for banned in ('try again', 'resend', 'view details', '<button'):
            self.assertNotIn(banned, html)
