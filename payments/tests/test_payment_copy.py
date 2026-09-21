"""payments/tests/test_payment_copy.py — CFO Easy batch PR D acceptance tests
for the Copy action on the payment register (POST /payment-requests/<id>/copy/
== /duplicate/, wired to taskboard.dropbox_views.payment_request_duplicate).

Pins the CFO's verbatim guardrail (2026-09-18):

    "Leave the original untouched. Copy creates a new editable request with
     a new reference; original stays exactly as it was — same reference,
     same status, same approvals."

Each test isolates ONE property and fails if the copy path regresses on it.
Karpathy: no test that could not go red on revert.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, Currency
from ledger.models import JournalEntry
from taskboard.models import PaymentRequest


class _CopyBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.raiser = User.objects.create_user(
            'copy-source-raiser', 'copy-source@alphadirect.co.bw', 'x')
        cls.copier = User.objects.create_user(
            'copy-copier', 'copy-copier@alphadirect.co.bw', 'x',
            is_superuser=True)  # unconditional view
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='PCPY', defaults={'name': 'Payment-Copy Test Co',
                                   'base_currency': cls.bwp})

    def _source(self, *, ref='PAY/ADIC/2026/09/18/CPY1',
                status=PaymentRequest.Status.PAID) -> PaymentRequest:
        return PaymentRequest.objects.create(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.OTHER, currency='BWP',
            subject='September rent — Building A',
            payee='Building A Landlord',
            processing_method=PaymentRequest.ProcessingMethod.INDIVIDUAL,
            line_items=[{'description': 'September rent',
                         'amount': '12500.00', 'invoice_number': 'INV-A9',
                         'gl_account': 'PRDUP-RENT'}],
            total=Decimal('12500.00'),
            account_name='Building A Landlord',
            account_number='62812345678',
            bank_name='FNB',
            branch_code='281267',
            status=status,
            first_approver=self.raiser,
            first_approved_at=timezone.now(),
            created_by=self.raiser,
        )

    def _copy(self, source: PaymentRequest):
        self.client.force_authenticate(self.copier)
        r = self.client.post(
            reverse('v1-payment-request-duplicate', args=[source.id]))
        self.assertEqual(r.status_code, 201, r.content)
        return r


class PaymentCopyAcceptanceTests(_CopyBase):
    """The six acceptance tests the CFO named for PR D."""

    def test_copy_returns_a_new_reference(self):
        source = self._source()
        body = self._copy(source).json()
        self.assertNotEqual(body['ref'], source.ref)
        self.assertTrue(body['ref'], 'copy must have a non-empty ref')
        # And it is a real, allocated reference — not the source's with a
        # decoration — so it stands alone in the register.
        self.assertNotIn(source.ref, body['ref'])

    def test_copy_carries_supplier_gl_banking_description(self):
        source = self._source()
        body = self._copy(source).json()
        # Supplier, description, banking — the fields that repeat month to
        # month and would otherwise be re-typed.
        self.assertEqual(body['payee'], source.payee)
        self.assertEqual(body['subject'], source.subject)
        self.assertEqual(body['account_name'], source.account_name)
        self.assertEqual(body['account_number'], source.account_number)
        self.assertEqual(body['bank_name'], source.bank_name)
        self.assertEqual(body['branch_code'], source.branch_code)
        # GL account travels on the line, not on the header. The AMOUNT is
        # deliberately stripped (CFO 2026-09-15 — a new invoice is a new
        # figure) but the GL code the line was booked to carries.
        self.assertEqual(len(body['line_items']), 1)
        self.assertEqual(body['line_items'][0].get('gl_account'),
                         source.line_items[0]['gl_account'])

    def test_copy_leaves_original_untouched(self):
        source = self._source()
        before = {
            'ref': source.ref, 'status': source.status,
            'first_approver_id': source.first_approver_id,
            'first_approved_at': source.first_approved_at,
            'payee': source.payee, 'account_number': source.account_number,
        }
        self._copy(source)
        source.refresh_from_db()
        self.assertEqual(source.ref, before['ref'])
        self.assertEqual(source.status, before['status'])
        self.assertEqual(source.first_approver_id, before['first_approver_id'])
        self.assertEqual(source.first_approved_at, before['first_approved_at'])
        self.assertEqual(source.payee, before['payee'])
        self.assertEqual(source.account_number, before['account_number'])

    def test_copy_does_not_create_a_gl_entry(self):
        # No journal entries at all should exist on the copier's action — GL
        # only writes when the eventual, human-submitted copy goes through
        # confirm → post via the normal ledger primitive.
        source = self._source()
        before_je = JournalEntry.objects.count()
        body = self._copy(source).json()
        after_je = JournalEntry.objects.count()
        self.assertEqual(after_je, before_je,
                         'a copy must not write any journal entry')
        copy = PaymentRequest.objects.get(id=body['id'])
        # And the copy itself has no payment attached — nothing to post.
        self.assertIsNone(copy.payment_id)

    def test_copy_does_not_carry_approvals(self):
        source = self._source()
        body = self._copy(source).json()
        copy = PaymentRequest.objects.get(id=body['id'])
        # Nobody's signature is inherited — the copy starts blank, whatever
        # the source had signed on it.
        self.assertIsNone(copy.first_approver_id)
        self.assertIsNone(copy.first_approved_at)
        self.assertEqual(copy.status, PaymentRequest.Status.DRAFT)
        # And the copier is its creator — the maker-checker rule on decide
        # is what refuses them approving their own copy later.
        self.assertEqual(copy.created_by_id, self.copier.id)

    def test_copy_endpoint_is_not_a_dropbox_draft(self):
        # A Dropbox-parsed draft carries the invoice filename it was read
        # from, and lands with a `needs_check` list the raiser has to clear.
        # A Copy is a plain-form draft: no source file, no read-in needs.
        source = self._source()
        body = self._copy(source).json()
        self.assertEqual(body.get('source_file', ''), '')
        self.assertEqual(body.get('needs_check', []), [])
        # It DOES carry the audit-trail pointer back to the source so the
        # register can show "copied from X" without touching the source.
        self.assertEqual(body.get('duplicated_from_ref'), source.ref)
