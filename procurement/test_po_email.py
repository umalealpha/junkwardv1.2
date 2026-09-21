"""
procurement/test_po_email.py — POEmailView "Sent ✓" stamp + supplier-email
write-back (CFO claims-PO email flow, 2026-07-07).

Covers POST /api/v1/purchase-orders/<id>/email/:

  * a successful send stamps last_emailed_at + last_emailed_to on the PO;
  * a supplier Contact with NO email on file gets the typed address saved
    (remember it for next time) — but an existing email is NEVER overwritten;
  * the stamp survives on an APPROVED PO (the terminal-status immutability
    guard in PurchaseOrder.save() is lifted for the email stamp only);
  * an invalid address is a 400 and nothing is stamped or sent;
  * the detail serializer exposes supplier_email + the two stamp fields.

Fixtures are DB-real but minimal, mirroring test_claims_api.py: BWP, one
Company, a vendor Contact, a claims PO. Django's test runner swaps the mail
backend to locmem, so EmailMessage.send() lands in django.core.mail.outbox.
"""

from datetime import date

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from billing.models import Contact
from core.models import Company, Currency
from procurement.models import PurchaseOrder
from procurement.pdf_view import POEmailView
from procurement.serializers import PurchaseOrderDetailSerializer


class POEmailViewTest(TestCase):
    """POST /api/v1/purchase-orders/{id}/email/ — stamp + write-back."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TESTE', name='Email Test Co.')
        cls.user = User.objects.create_user(
            'poemailer', email='cfo@alphadirect.co.bw', password='x',
            first_name='Prathap', last_name='G', is_superuser=True,
        )
        cls.factory = APIRequestFactory()

    def _make_po(self, supplier_email=None, **overrides):
        supplier = Contact.objects.create(
            name='Carfil Panel Beaters (Pty) Ltd', contact_type='vendor',
            company=self.company, email=supplier_email,
        )
        po = PurchaseOrder.objects.create(
            department='claims',
            supplier=supplier,
            company=self.company,
            issue_date=date(2026, 7, 1),
            created_by=self.user,
            **overrides,
        )
        return po, supplier

    def _email(self, po, body):
        view = POEmailView.as_view()
        request = self.factory.post(
            f'/api/v1/purchase-orders/{po.pk}/email/', body, format='json',
        )
        force_authenticate(request, user=self.user)
        return view(request, pk=str(po.pk))

    # ------------------------------------------------------------------
    # 1. Success — stamp set, blank supplier email remembered
    # ------------------------------------------------------------------

    def test_send_stamps_po_and_remembers_blank_supplier_email(self):
        po, supplier = self._make_po(supplier_email=None)
        self.assertIsNone(po.last_emailed_at)

        response = self._email(po, {'to': 'workshop@carfil.co.bw'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['sent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['workshop@carfil.co.bw'])

        po.refresh_from_db()
        self.assertIsNotNone(po.last_emailed_at)
        self.assertEqual(po.last_emailed_to, 'workshop@carfil.co.bw')

        # F3 write-back: the blank Contact email now carries the typed one.
        supplier.refresh_from_db()
        self.assertEqual(supplier.email, 'workshop@carfil.co.bw')

    def test_send_never_overwrites_existing_supplier_email(self):
        po, supplier = self._make_po(supplier_email='accounts@carfil.co.bw')

        response = self._email(po, {'to': 'different@carfil.co.bw'})
        self.assertEqual(response.status_code, 200, response.data)

        po.refresh_from_db()
        self.assertEqual(po.last_emailed_to, 'different@carfil.co.bw')
        # The email on file is authoritative — the typed one must NOT clobber it.
        supplier.refresh_from_db()
        self.assertEqual(supplier.email, 'accounts@carfil.co.bw')

    # ------------------------------------------------------------------
    # 2. Approved PO — the immutability guard must not break the stamp
    # ------------------------------------------------------------------

    def test_approved_po_can_still_be_stamped(self):
        po, _ = self._make_po()
        # queryset.update bypasses PurchaseOrder.save() — puts the PO in a
        # terminal status without tripping the edit guard.
        PurchaseOrder.objects.filter(pk=po.pk).update(
            status=PurchaseOrder.Status.APPROVED,
        )

        response = self._email(po, {'to': 'workshop@carfil.co.bw'})
        self.assertEqual(response.status_code, 200, response.data)

        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        self.assertIsNotNone(po.last_emailed_at)
        self.assertEqual(po.last_emailed_to, 'workshop@carfil.co.bw')

    # ------------------------------------------------------------------
    # 3. Failure paths — nothing stamped, nothing remembered
    # ------------------------------------------------------------------

    def test_invalid_address_is_400_and_nothing_is_stamped(self):
        po, supplier = self._make_po(supplier_email=None)

        response = self._email(po, {'to': 'not-an-email'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

        po.refresh_from_db()
        self.assertIsNone(po.last_emailed_at)
        self.assertEqual(po.last_emailed_to, '')
        supplier.refresh_from_db()
        self.assertIsNone(supplier.email)

    # ------------------------------------------------------------------
    # 4. Serializer exposure — the frontend contract
    # ------------------------------------------------------------------

    def test_detail_serializer_exposes_supplier_email_and_stamp(self):
        po, _ = self._make_po(supplier_email=None)
        data = PurchaseOrderDetailSerializer(po).data
        self.assertEqual(data['supplier_email'], '')      # None coerced to ''
        self.assertIsNone(data['last_emailed_at'])
        self.assertEqual(data['last_emailed_to'], '')

        self._email(po, {'to': 'workshop@carfil.co.bw'})
        po.refresh_from_db()
        data = PurchaseOrderDetailSerializer(po).data
        self.assertEqual(data['supplier_email'], 'workshop@carfil.co.bw')
        self.assertIsNotNone(data['last_emailed_at'])
        self.assertEqual(data['last_emailed_to'], 'workshop@carfil.co.bw')
