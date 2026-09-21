"""taskboard/test_fnb_file_audit_log.py — the FNB bulk-payment file download
writes its audit row instead of crashing.

payment_request_fnb_file builds the CSV and then records who took it, because a
bank-loading file leaving the building is exactly the event the audit trail
exists for. The name `AuditLog` was used there but never imported into the
module (ruff F821, blame 48d6ec39), so the handler raised NameError AFTER the
whole file had been built — the download never reached the browser and the
trail never recorded the attempt.

What this pins:
  - The download returns the CSV (200, text/csv), not a NameError.
  - The audit row is actually written, against the request, as a DOWNLOAD by
    the person who took it.

Omni moves no money here: this is a read-only file download plus its audit row.
The CFO approves the batch in the FNB app on his phone with two factors. No real
payee or staff names.

Run: manage.py test taskboard.test_fnb_file_audit_log
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AuditLog, Company, Currency
from taskboard.models import PaymentRequest

SOURCE_ACCT = '6' * 11


@override_settings(
    PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={'default': SOURCE_ACCT})
class FnbFileAuditLogTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='FNBAUD', defaults={'name': 'FNB File Audit Test Co',
                                     'base_currency': cls.bwp})
        # Superuser so _can_view_request passes on identity alone — this test is
        # about the audit write, not about who may read a pack.
        cls.taker = User.objects.create_superuser('fnbfile_taker', password='x')
        cls.pr = PaymentRequest.objects.create(
            ref='PR-FNBAUD-0001', entity=cls.company.name,
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='FNB FILE AUDIT', payee='Test Supplier Co',
            line_items=[{'description': 'Test line', 'amount': '1500.00',
                         'invoice_number': 'IN9001'}],
            total=Decimal('1500.00'),
            account_name='Test Supplier Co (Pty) Ltd', account_number='1234567',
            bank_name='FNB Botswana', branch_code='293567', account_type='1',
            status=PaymentRequest.Status.PENDING_CFO, created_by=cls.taker)

    def setUp(self):
        self.client.force_login(self.taker)
        self.url = reverse('v1-payment-request-fnb-file', args=[self.pr.id])

    def test_the_download_returns_the_csv(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        self.assertIn('RECIPIENT ACCOUNT', resp.content.decode())

    def test_taking_the_file_is_recorded_in_the_audit_trail(self):
        self.client.get(self.url)
        row = AuditLog.objects.filter(
            table_name='taskboard_paymentrequest',
            record_id=str(self.pr.id),
            action=AuditLog.Action.DOWNLOAD).first()
        self.assertIsNotNone(
            row, 'the FNB file download wrote no audit row')
        self.assertEqual(row.user_id, self.taker.id)
        self.assertIn(self.pr.ref, row.description)
