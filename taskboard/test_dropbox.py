"""taskboard/test_dropbox.py — the payments Drop Box (CFO handover 2026-09-02).

Drop an invoice -> a filled DRAFT payment request the raiser checks + submits.
Reuses the invoice reader, supplier memory and the normal create endpoint, so a
draft is never a way around a control.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest

READ = {  # what the invoice reader hands back
    'total_amount': '1500.00', 'account_number': '62001234567',
    'bank_name': 'FNB Botswana', 'branch_code': '282267',
    'invoice_number': 'INV-9001', 'payee_name': 'Gaborone Panel Beaters',
}


@override_settings(
    PAYMENT_FIRST_APPROVER_EMAILS=['fin-a@example.invalid', 'fin-b@example.invalid'])
class DropBoxTests(APITestCase):
    def setUp(self):
        User.objects.create_superuser('cfo-d', 'cfo-d@example.invalid', 'x')
        User.objects.create_user('fin-a', email='fin-a@example.invalid')
        User.objects.create_user('fin-b', email='fin-b@example.invalid')
        self.clerk = User.objects.create_user('clerk-d', email='clerk-d@example.invalid')
        self.client.force_authenticate(self.clerk)
        self.drop_url = reverse('v1-payment-request-drop-box')
        self.drafts_url = reverse('v1-payment-request-drafts')

    def _drop(self, read=None):
        f = SimpleUploadedFile('acme-invoice.pdf', b'%PDF-1.4 fake', content_type='application/pdf')
        with mock.patch('payments.invoice_read.read_invoice', return_value=(read or READ)):
            return self.client.post(self.drop_url, {'files': [f]}, format='multipart')

    def test_drop_creates_a_prefilled_draft(self):
        r = self._drop()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['created'], 1)
        pr = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self.assertEqual(pr.payee, 'Gaborone Panel Beaters')
        self.assertEqual(pr.account_number, '62001234567')
        self.assertEqual(pr.line_items[0]['amount'], '1500.00')
        self.assertEqual(pr.draft_source_file, 'acme-invoice.pdf')
        # category is never guessed — always on the check list
        self.assertIn('category', pr.draft_needs_check)

    def test_reader_miss_is_flagged_for_the_human(self):
        r = self._drop({**READ, 'payee_name': None, 'total_amount': None,
                        'account_number': None})
        pr = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        for f in ('payee', 'amount', 'account_number', 'category'):
            self.assertIn(f, pr.draft_needs_check)

    def test_supplier_memory_fills_the_bank_the_reader_missed(self):
        # We paid this payee before, into a known account.
        PaymentRequest.objects.create(
            ref='PAY/X/2026/09/01/0001', status=PaymentRequest.Status.PAID,
            subject='prior', payee='Gaborone Panel Beaters',
            account_name='Gaborone Panel Beaters', account_number='62009999999',
            bank_name='FNB Botswana', created_by=self.clerk,
            line_items=[{'description': 'x', 'amount': '10'}])
        # This read has NO bank details — supplier memory must fill them.
        r = self._drop({**READ, 'account_number': None, 'bank_name': None})
        pr = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self.assertEqual(pr.account_number, '62009999999')  # from what we last paid
        self.assertNotIn('account_number', pr.draft_needs_check)

    def test_drafts_are_hidden_from_the_approval_board(self):
        self._drop()
        board = self.client.get(reverse('v1-payment-requests')).json()
        refs = [row.get('ref') for row in (board.get('requests') or board.get('results') or [])]
        draft_ref = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT).ref
        self.assertNotIn(draft_ref, refs)
        # but it IS on my drafts list
        self.assertEqual(len(self.client.get(self.drafts_url).json()['drafts']), 1)

    def test_edit_a_draft_clears_the_check_flag(self):
        self._drop({**READ, 'payee_name': None})
        pr = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self.assertIn('payee', pr.draft_needs_check)
        url = reverse('v1-payment-request-draft-detail', args=[pr.id])
        self.client.patch(url, {'payee': 'Confirmed Payee Ltd', 'category': 'operations'},
                          format='json')
        pr.refresh_from_db()
        self.assertEqual(pr.payee, 'Confirmed Payee Ltd')
        self.assertNotIn('payee', pr.draft_needs_check)

    def test_delete_a_draft(self):
        self._drop()
        pr = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        r = self.client.delete(reverse('v1-payment-request-draft-detail', args=[pr.id]))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PaymentRequest.objects.filter(id=pr.id).exists())

    def test_submit_a_complete_draft_becomes_a_real_request(self):
        self._drop()
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        # the human confirms the one thing never read: claims vs operations
        self.client.patch(reverse('v1-payment-request-draft-detail', args=[draft.id]),
                          {'category': PaymentRequest.Category.OTHER}, format='json')
        # a first-ever payee needs the new-payee tick (PAY-BANK-03), same as a
        # hand-typed request — the human answers it on submit.
        r = self.client.post(reverse('v1-payment-request-draft-submit', args=[draft.id]),
                             {'new_payee_confirmed': True}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        # the draft is gone; a submitted (pending finance) request now exists
        self.assertFalse(PaymentRequest.objects.filter(
            id=draft.id, status=PaymentRequest.Status.DRAFT).exists())
        self.assertTrue(PaymentRequest.objects.filter(
            status=PaymentRequest.Status.PENDING_FINANCE, payee='Gaborone Panel Beaters').exists())

    def test_submit_an_incomplete_draft_returns_the_errors_and_keeps_it(self):
        # No category picked -> the create control PAY-SUP-01 refuses it. (The
        # new-payee tick is given so this isolates the category, not the bank.)
        self._drop()
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        r = self.client.post(reverse('v1-payment-request-draft-submit', args=[draft.id]),
                             {'new_payee_confirmed': True}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        # the draft is NOT lost — the raiser fixes it and tries again
        self.assertTrue(PaymentRequest.objects.filter(
            id=draft.id, status=PaymentRequest.Status.DRAFT).exists())

    # ── Safety catch (Feature C) ─────────────────────────────────────────────
    def _submit(self, draft, **body):
        return self.client.post(
            reverse('v1-payment-request-draft-submit', args=[draft.id]),
            body, format='json')

    def _pick_category(self, draft):
        self.client.patch(reverse('v1-payment-request-draft-detail', args=[draft.id]),
                          {'category': PaymentRequest.Category.OTHER}, format='json')

    def test_amount_mismatch_blocks_submit_until_confirmed(self):
        # The reader saw 1500 on the invoice; the human types 2000. The catch
        # stops the submit until they confirm the change (PAY-AMT-01).
        self._drop()
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self.assertEqual(draft.draft_read_amount, Decimal('1500.00'))
        self.client.patch(
            reverse('v1-payment-request-draft-detail', args=[draft.id]),
            {'category': PaymentRequest.Category.OTHER,
             'line_items': [{'description': 'Invoice INV-9001', 'amount': '2000.00',
                             'invoice_number': 'INV-9001'}]}, format='json')

        r = self._submit(draft, new_payee_confirmed=True)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-AMT-01')
        self.assertTrue(PaymentRequest.objects.filter(
            id=draft.id, status=PaymentRequest.Status.DRAFT).exists())

        # confirming the changed amount lets it through
        r2 = self._submit(draft, new_payee_confirmed=True, confirm_amount=True)
        self.assertEqual(r2.status_code, 201, r2.content)

    def test_amount_that_matches_the_invoice_submits_clean(self):
        # The unchanged read amount must NOT trip the catch.
        self._drop()
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self._pick_category(draft)
        r = self._submit(draft, new_payee_confirmed=True)
        self.assertEqual(r.status_code, 201, r.content)

    def test_duplicate_invoice_goes_to_the_committee_not_a_block(self):
        # Same invoice, same payee, same amount already paid -> PAY-DUP-01.
        # CFO 2026-09-04: a duplicate is never refused; the request is created
        # as an exception and the committee decides whether it is paid.
        PaymentRequest.objects.create(
            ref='PAY/X/2026/09/01/0009', status=PaymentRequest.Status.PAID,
            subject='prior', payee='Gaborone Panel Beaters',
            account_name='Gaborone Panel Beaters', account_number='62001234567',
            bank_name='FNB Botswana', created_by=self.clerk,
            line_items=[{'description': 'Invoice INV-9001', 'amount': '1500.00',
                         'invoice_number': 'INV-9001'}])
        self._drop()
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self._pick_category(draft)
        r = self._submit(draft)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-DUP-01')
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.exception_control, 'PAY-DUP-01')
        self.assertIn('PAY/X/2026/09/01/0009', pr.exception_reason)

    def test_changed_bank_goes_to_the_committee_not_a_block(self):
        # We paid this payee into one account before; the invoice reads a
        # different one -> PAY-BANK-01. Since CFO 2026-09-02 that never blocks:
        # the request is CREATED as an exception for the committee and the
        # raiser is told so in the reply (Fable 5.1 audit, H6).
        PaymentRequest.objects.create(
            ref='PAY/X/2026/09/01/0007', status=PaymentRequest.Status.PAID,
            subject='prior', payee='Gaborone Panel Beaters',
            account_name='Gaborone Panel Beaters', account_number='62009999999',
            bank_name='FNB Botswana', created_by=self.clerk,
            line_items=[{'description': 'older', 'amount': '999.00',
                         'invoice_number': 'INV-0001'}])
        self._drop()  # READ carries account 62001234567 — a different account
        draft = PaymentRequest.objects.get(status=PaymentRequest.Status.DRAFT)
        self.assertEqual(draft.account_number, '62001234567')
        self._pick_category(draft)

        r = self._submit(draft)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json().get('status'), PaymentRequest.Status.EXCEPTION)
        self.assertEqual((r.json().get('exception') or {}).get('control'), 'PAY-BANK-01')
        self.assertIn('committee', (r.json().get('exception') or {}).get('message', ''))
        # the draft is gone; the request is with the committee, not blocked
        self.assertFalse(PaymentRequest.objects.filter(
            id=draft.id, status=PaymentRequest.Status.DRAFT).exists())
        self.assertTrue(PaymentRequest.objects.filter(
            status=PaymentRequest.Status.EXCEPTION, payee='Gaborone Panel Beaters').exists())
