"""
procurement/test_po_attachment_file.py — the phone opens PO attachments
(CFO 2026-09-04: "PO attachments should be able to open from the phone").

The attachments list used to hand the phone `url` = /media/po_attachments/…,
which prod never serves (Django's DEBUG static() only) — every tap 404'd.
GET /api/v1/purchase-orders/<po>/attachments/<att>/file/ streams the file
behind the SAME gate as GET /purchase-orders/<po>/ (PurchaseOrderViewSet's
company-scoped queryset): an approver who may read the PO may open its quote;
anyone outside that entity gets the same 404 the PO itself gives them.

Run: manage.py test procurement.test_po_attachment_file
"""
import shutil
import tempfile
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from billing.models import Contact
from core.models import Company, Currency, UserCompanyAccess
from procurement.models import POAttachment, PurchaseOrder

PDF_BYTES = b'%PDF-1.4 supplier quote'
_MEDIA = tempfile.mkdtemp(prefix='po-att-test-')


@override_settings(MEDIA_ROOT=_MEDIA)
class POAttachmentFileTests(APITestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.adic = Company.objects.create(code='TADIC', name='Test ADIC')
        cls.adsa = Company.objects.create(code='TADSA', name='Test ADSA')
        cls.supplier = Contact.objects.create(
            name='Quote Co (Pty) Ltd', contact_type='vendor', company=cls.adic)
        cls.raiser = User.objects.create_user('raiser', password='x')
        # The approver: an explicit view grant on the PO's entity, nothing else.
        cls.approver = User.objects.create_user('approver', password='x')
        UserCompanyAccess.objects.create(
            user=cls.approver, company=cls.adic, can_view=True)
        # Someone from another entity — may never see an ADIC PO or its files.
        cls.outsider = User.objects.create_user('outsider', password='x')
        UserCompanyAccess.objects.create(
            user=cls.outsider, company=cls.adsa, can_view=True)
        cls.cfo = User.objects.create_superuser('cfo', password='x')

    def setUp(self):
        self.po = PurchaseOrder.objects.create(
            department='finance', supplier=self.supplier, company=self.adic,
            issue_date=date(2026, 9, 4), created_by=self.raiser)
        self.att = POAttachment.objects.create(
            purchase_order=self.po, label='Supplier quote',
            file=SimpleUploadedFile('quote.pdf', PDF_BYTES, content_type='application/pdf'),
            content_type='application/pdf', size_bytes=len(PDF_BYTES),
            uploaded_by=self.raiser)
        self.url = reverse('v1-po-attachment-file',
                           kwargs={'po_pk': self.po.pk, 'pk': self.att.pk})

    def test_approver_opens_the_file_inline_with_its_type(self):
        self.client.force_authenticate(self.approver)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r['Content-Disposition'].startswith('inline;'), r['Content-Disposition'])
        self.assertIn('quote.pdf', r['Content-Disposition'])
        self.assertEqual(b''.join(r.streaming_content), PDF_BYTES)

    def test_cfo_unrestricted_bucket_opens_it(self):
        self.client.force_authenticate(self.cfo)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_other_entity_gets_the_same_404_as_the_po_itself(self):
        self.client.force_authenticate(self.outsider)
        # The gate is the PO detail view's own queryset — prove they match.
        detail = self.client.get(reverse('purchase-order-detail', kwargs={'pk': self.po.pk}))
        self.assertEqual(detail.status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_attachment_from_a_different_po_is_404_not_idor(self):
        other = PurchaseOrder.objects.create(
            department='finance', supplier=self.supplier, company=self.adic,
            issue_date=date(2026, 9, 4), created_by=self.raiser)
        self.client.force_authenticate(self.approver)
        # Right attachment id, wrong PO in the path → not found, never the file.
        r = self.client.get(reverse('v1-po-attachment-file',
                                    kwargs={'po_pk': other.pk, 'pk': self.att.pk}))
        self.assertEqual(r.status_code, 404)

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_list_returns_file_url_pointing_at_the_endpoint(self):
        self.client.force_authenticate(self.approver)
        r = self.client.get(reverse('v1-po-attachments', kwargs={'po_pk': self.po.pk}))
        self.assertEqual(r.status_code, 200)
        row = r.json()['attachments'][0]
        self.assertEqual(row['id'], str(self.att.id))
        self.assertEqual(row['file_url'], self.url)
        self.assertTrue(row['url'].startswith('/media/'))   # desktop still gets the old key


@override_settings(MEDIA_ROOT=_MEDIA)
class POAttachmentEntityGateTests(POAttachmentFileTests):
    """Security review 2026-09-04: list/create/destroy were reachable across
    entities. Every action on another company's PO must answer 404."""

    def _base(self):
        return f'/api/v1/purchase-orders/{self.po.pk}/attachments/'

    def test_outsider_cannot_list_another_entitys_attachments(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self._base()).status_code, 404)

    def test_outsider_cannot_upload_onto_another_entitys_po(self):
        self.client.force_authenticate(self.outsider)
        r = self.client.post(self._base(), {'file': SimpleUploadedFile('x.pdf', PDF_BYTES, content_type='application/pdf')}, format='multipart')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(POAttachment.objects.filter(purchase_order=self.po).count(), 1)

    def test_outsider_cannot_delete_another_entitys_attachment(self):
        self.client.force_authenticate(self.outsider)
        r = self.client.delete(f'{self._base()}{self.att.pk}/')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(POAttachment.objects.filter(pk=self.att.pk).exists())

    def test_approver_still_lists_and_sees_file_url(self):
        self.client.force_authenticate(self.approver)
        r = self.client.get(self._base())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['attachments'][0]['file_url'].endswith('/file/'))

    def test_html_typed_upload_is_never_rendered_inline(self):
        # An uploader-declared text/html on an extension-less name must not become
        # same-origin HTML in the approver's browser.
        att = POAttachment.objects.create(
            purchase_order=self.po, label='sneaky',
            file=SimpleUploadedFile('quote', b'<script>alert(1)</script>', content_type='text/html'),
            content_type='text/html', size_bytes=25, uploaded_by=self.raiser)
        self.client.force_authenticate(self.approver)
        r = self.client.get(reverse('v1-po-attachment-file', kwargs={'po_pk': self.po.pk, 'pk': att.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/octet-stream')
        self.assertTrue(r['Content-Disposition'].startswith('attachment;'), r['Content-Disposition'])
