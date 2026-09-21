"""taskboard/test_read_invoice_endpoint.py — the payment-request invoice reader.

CFO 2026-09-01: finance were hand-typing bank account numbers off invoices on
/payment-requests while a working reader (payments/invoice_read.py, live since
2026-08-22) sat wired into a different screen.

The endpoint added here reuses that reader. What must never regress:
  - it needs a login, but NOT a maker title. The reader on the payments app is
    maker-gated (Financial Controller / Senior Accountant / Accountant); eight
    people who genuinely raise payment requests today — Finance Managers among
    them — would be refused by that gate, so this endpoint matches the gate on
    the action it serves (raising a request = IsAuthenticated);
  - it SAVES NOTHING — reading an invoice must never create a PaymentRequest;
  - a missing file is a clean 400, not a 500;
  - an unreadable file degrades to ok:false with a plain-English message, so a
    bad scan can never take the form down — the person can still type it in.

Run: manage.py test taskboard.test_read_invoice_endpoint
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest


class ReadInvoiceEndpointTests(TestCase):
    def setUp(self):
        self.url = reverse('v1-payment-request-read-invoice')
        self.user = User.objects.create_user('someone', password='x')

    def _pdf(self, name='invoice.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 not-a-real-pdf',
                                  content_type='application/pdf')

    # ── who may use it ───────────────────────────────────────────────────────
    def test_anonymous_is_refused(self):
        r = self.client.post(self.url, {'file': self._pdf()})
        self.assertIn(r.status_code, (401, 403))

    def test_an_ordinary_logged_in_raiser_may_read_an_invoice(self):
        """The whole point: no maker title required. A Finance Manager raises
        payment requests every day and must be able to use this."""
        self.client.force_login(self.user)
        with mock.patch('payments.invoice_read.read_invoice',
                        return_value={'ok': True, 'tier': 'text',
                                      'message': 'Read it.', 'fields': {}}) as rd:
            r = self.client.post(self.url, {'file': self._pdf()})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(rd.called)

    # ── it must not create anything ──────────────────────────────────────────
    def test_reading_an_invoice_creates_no_payment_request(self):
        self.client.force_login(self.user)
        with mock.patch('payments.invoice_read.read_invoice',
                        return_value={'ok': True, 'tier': 'text',
                                      'message': 'Read it.', 'fields': {}}):
            self.client.post(self.url, {'file': self._pdf()})
        self.assertFalse(PaymentRequest.objects.exists())

    # ── bad input is handled, not thrown ─────────────────────────────────────
    def test_missing_file_is_a_clean_400(self):
        self.client.force_login(self.user)
        r = self.client.post(self.url, {})
        self.assertEqual(r.status_code, 400)
        self.assertIn('invoice', r.json()['detail'].lower())

    def test_a_reader_crash_degrades_instead_of_500ing(self):
        """A clerk must never see a stack trace, and one bad scan must not take
        the form down — they can always still type the details in."""
        self.client.force_login(self.user)
        with mock.patch('payments.invoice_read.read_invoice',
                        side_effect=RuntimeError('engine exploded')):
            r = self.client.post(self.url, {'file': self._pdf()})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['ok'])
        self.assertIn('type the details in', r.json()['message'])
