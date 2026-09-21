"""fnb/test_audit_fixes.py — the Fable 5.1 audit fixes on the FNB link (2026-09-02).

Omni loads batches; nothing leaves the bank until the CFO authorises it at FNB
with two-factor. Each test fails without its fix.

Run: manage.py test fnb.test_audit_fixes
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient, APIRequestFactory

from fnb.tests import _PaymentFixture


class _Resp:
    json = {'instructionId': 'R1G66R-audit'}
    status_code = 200


class DoubleLoadTests(_PaymentFixture):
    """H5 — two submits inside one click-window posted the same payment twice."""

    def test_the_payment_is_claimed_before_the_bank_call_and_never_posted_twice(self):
        from django.core.exceptions import ValidationError
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        seen = {}

        def _post(*args, **kwargs):
            seen['stamped_during_post'] = (
                Payment.objects.get(pk=p.pk).bank_submitted_at is not None)
            return _Resp()

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = _post
            submit_eft_batch(Payment.objects.filter(pk=p.pk),
                             source_account=self.source_account, user=self.releaser)
            self.assertTrue(seen['stamped_during_post'], 'claim must precede the bank call')
            with self.assertRaises(ValidationError):
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account, user=self.releaser)
            self.assertEqual(client.return_value.post.call_count, 1)

    def test_a_sign_in_failure_cancels_the_batch_and_frees_the_payment(self):
        """M9 — the error used to escape, leaving a PENDING batch nobody saw."""
        from fnb.client import FNBAuthError
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = FNBAuthError('token expired')
            with self.assertRaises(FNBAuthError):
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account, user=self.releaser)
        b = FNBBatchSubmission.objects.filter(payments=p).first()
        self.assertIsNotNone(b)
        self.assertEqual(b.status, FNBBatchSubmission.Status.CANCELLED)
        self.assertIn('sign-in failed', b.failure_reason)
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)

    def test_a_clean_reject_frees_the_payment_for_a_retry(self):
        from fnb.client import FNBAPIError
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = FNBAPIError(400, 'bad branch code')
            with self.assertRaises(FNBAPIError):
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account, user=self.releaser)
        b = FNBBatchSubmission.objects.filter(payments=p).first()
        self.assertEqual(b.status, FNBBatchSubmission.Status.FAILED)
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)

    def test_an_indeterminate_outcome_keeps_the_claim(self):
        """A timeout may have reached the bank — never retry-eligible blind."""
        from fnb.client import FNBAPIError
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = FNBAPIError(503, 'gateway timeout')
            with self.assertRaises(FNBAPIError):
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account, user=self.releaser)
        b = FNBBatchSubmission.objects.filter(payments=p).first()
        self.assertEqual(b.status, FNBBatchSubmission.Status.UNKNOWN)
        p.refresh_from_db()
        self.assertIsNotNone(p.bank_submitted_at)


class GateTests(_PaymentFixture):
    def test_a_deactivated_administrator_loses_fnb_authority(self):
        """H8 — Deactivate user flips only the profile flag; the gate ignored it."""
        from core.models import UserProfile
        from fnb.api_views import _can_manage_fnb
        u = User.objects.create_user('exadmin', email='exadmin@example.com')
        prof, _ = UserProfile.objects.get_or_create(
            user=u, defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER,
                              'is_active': True})
        prof.is_administrator = True
        prof.is_active = True
        prof.save()
        self.assertTrue(_can_manage_fnb(User.objects.get(pk=u.pk)))
        prof.is_active = False
        prof.save()
        self.assertFalse(_can_manage_fnb(User.objects.get(pk=u.pk)))

    def test_hiding_a_paying_account_needs_fnb_authority(self):
        """M7 — any signed-in staffer could hide a paying account from the picker."""
        plain = User.objects.create_user('plain', email='plain@example.com')
        c = APIClient()
        c.force_authenticate(plain)
        r = c.patch(f'/api/v1/banking/bank-accounts/{self.source_account.pk}/toggle-hide/',
                    {'hide': True}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.source_account.refresh_from_db()
        self.assertFalse(self.source_account.hide_in_banking_ui)

    def test_the_status_badge_stays_open_but_hides_the_bank_host(self):
        """LOW — the /banking/fnb badge must still load for ordinary staff (a
        readiness gate depends on it), but the bank API host and auth mode are
        for finance admins only; everyone else gets a boolean."""
        plain = User.objects.create_user('plain2', email='plain2@example.com')
        c = APIClient()
        c.force_authenticate(plain)
        r = c.get('/api/v1/fnb/status/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn('.', r.json().get('api_base', ''))   # no real host leaked
        # A finance admin (superuser fixture) sees the real host.
        c.force_authenticate(self.staff)
        self.assertEqual(c.get('/api/v1/fnb/status/').status_code, 200)


@override_settings(FNB_WEBHOOK_SECRET='test-secret')
class WebhookTests(_PaymentFixture):
    """M8 — redelivery crashed; a valid 'settled' could flip a rejected batch."""

    def _batch(self, status):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key='Guard Test Supplier 000001 (O)',
            source_account=self.source_account, payment_count=1,
            total_amount_bwp=Decimal('100'), currency_code='BWP',
            status=status, payload_snapshot={}, submitted_by=self.releaser)

    def _send(self, payload):
        body = json.dumps(payload).encode()
        sig = hmac.new(b'test-secret', body, hashlib.sha256).hexdigest()
        return APIClient().generic('POST', '/api/v1/fnb/webhook/', body,
                                   content_type='application/json',
                                   HTTP_X_FNB_SIGNATURE=sig)

    def test_a_redelivered_event_is_acknowledged_not_crashed(self):
        from fnb.models import FNBBatchSubmission, FNBWebhookEvent
        b = self._batch(FNBBatchSubmission.Status.SUBMITTED)
        ev = {'id': 'evt-1', 'event_type': 'batch.acknowledged', 'reference': b.idempotency_key}
        self.assertEqual(self._send(ev).status_code, 200)
        r = self._send(ev)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json().get('duplicate'))
        self.assertEqual(FNBWebhookEvent.objects.filter(external_id='evt-1').count(), 1)
        b.refresh_from_db()
        self.assertEqual(b.status, FNBBatchSubmission.Status.ACKNOWLEDGED)

    def test_settled_does_not_flip_a_failed_batch(self):
        from fnb.models import FNBBatchSubmission, FNBWebhookEvent
        b = self._batch(FNBBatchSubmission.Status.FAILED)
        r = self._send({'id': 'evt-2', 'event_type': 'batch.settled',
                        'reference': b.idempotency_key})
        self.assertEqual(r.status_code, 200, r.content)
        b.refresh_from_db()
        self.assertEqual(b.status, FNBBatchSubmission.Status.FAILED)
        ev = FNBWebhookEvent.objects.get(external_id='evt-2')
        self.assertEqual(ev.status, FNBWebhookEvent.Status.FAILED)
        self.assertIn('kept failed', (ev.error_message or '').lower())

    def test_a_late_acknowledged_does_not_pull_a_settled_batch_back(self):
        from fnb.models import FNBBatchSubmission
        b = self._batch(FNBBatchSubmission.Status.SETTLED)
        self._send({'id': 'evt-3', 'event_type': 'batch.acknowledged',
                    'reference': b.idempotency_key})
        b.refresh_from_db()
        self.assertEqual(b.status, FNBBatchSubmission.Status.SETTLED)


class MaskingTests(_PaymentFixture):
    """M7 — full account numbers in bulk views."""

    def test_batch_recipients_show_last_four_only(self):
        from fnb.models import FNBBatchSubmission
        from fnb.serializers import FNBBatchSubmissionSerializer
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)      # acct 1234567
        b = FNBBatchSubmission.objects.create(
            idempotency_key='Guard Test Supplier 000002 (O)',
            source_account=self.source_account, payment_count=1,
            total_amount_bwp=Decimal('100'), currency_code='BWP',
            status=FNBBatchSubmission.Status.SUBMITTED, payload_snapshot={},
            submitted_by=self.releaser)
        b.payments.set([p.pk])
        acct = FNBBatchSubmissionSerializer(b).data['recipients'][0]['account_number']
        self.assertTrue(acct.endswith('4567'))
        self.assertIn('*', acct)

    def test_payment_detail_masks_the_payee_account_for_non_exporters(self):
        from payments.models import Payment
        from payments.serializers import PaymentDetailSerializer
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        viewer = User.objects.create_user('viewer', email='viewer@example.com')
        req = APIRequestFactory().get('/')
        req.user = viewer
        masked = PaymentDetailSerializer(p, context={'request': req}).data['payee_account_number']
        self.assertIn('*', masked)
        self.assertTrue(masked.endswith('4567'))
        req2 = APIRequestFactory().get('/')
        req2.user = self.staff                                   # superuser: may export
        full = PaymentDetailSerializer(p, context={'request': req2}).data['payee_account_number']
        self.assertEqual(full, '1234567')
