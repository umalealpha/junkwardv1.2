"""MY-APPR-01 — /my-approvals must include PaymentRequest queue (CFO 2026-08-15,
Manus QC F1).

The audit found 21 payment authorisations sitting in /tasks with
source='payment_request', worth ~BWP 1.35m — while /my-approvals, the page that
claims to show everything awaiting sign-off, showed none. The two-stage
PaymentRequest queue was never wired into pending_approvals_for(). This test
locks in the wiring: CFO sees PENDING_CFO count, finance approver sees
PENDING_FINANCE they did NOT raise, everyone else sees nothing.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.approvals_views import pending_approvals_for
from taskboard.models import PaymentRequest


@override_settings(PAYMENT_FIRST_APPROVER_EMAILS=['pako@ex.com'])
class MyApprovalsPaymentRequestStreamTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # CFO: superuser (matches _is_cfo's superuser branch)
        cls.cfo = User.objects.create_user(
            'cfo', email='cfo@ex.com', password='x', is_superuser=True)
        # Finance approver: matches PAYMENT_FIRST_APPROVER_EMAILS above
        cls.finance = User.objects.create_user(
            'pako', email='pako@ex.com', password='x')
        # Ordinary raiser: nothing to authorise
        cls.raiser = User.objects.create_user(
            'clerk', email='clerk@ex.com', password='x')

    def _pr(self, ref, status, created_by=None):
        return PaymentRequest.objects.create(
            ref=ref, status=status, created_by=created_by or self.raiser)

    def _payment_request_stream(self, streams):
        return next((s for s in streams if s['key'] == 'payment_requests'), None)

    def test_cfo_sees_pending_cfo(self):
        self._pr('R1', PaymentRequest.Status.PENDING_CFO)
        self._pr('R2', PaymentRequest.Status.PENDING_CFO)
        self._pr('R3', PaymentRequest.Status.PENDING_FINANCE)  # not on CFO desk
        self._pr('R4', PaymentRequest.Status.PAID)             # terminal
        streams = pending_approvals_for(self.cfo)
        stream = self._payment_request_stream(streams)
        self.assertIsNotNone(stream, 'CFO must have a payment_requests stream')
        self.assertEqual(stream['count'], 2)
        self.assertEqual(stream['href'], '/payment-requests')

    def test_finance_approver_sees_pending_finance_not_own_raises(self):
        self._pr('R1', PaymentRequest.Status.PENDING_FINANCE)  # counted
        self._pr('R2', PaymentRequest.Status.PENDING_FINANCE,  # SoD — excluded
                 created_by=self.finance)
        self._pr('R3', PaymentRequest.Status.PENDING_CFO)      # not their stage
        streams = pending_approvals_for(self.finance)
        stream = self._payment_request_stream(streams)
        self.assertIsNotNone(stream)
        self.assertEqual(stream['count'], 1)

    def test_ordinary_user_sees_no_stream(self):
        self._pr('R1', PaymentRequest.Status.PENDING_FINANCE)
        self._pr('R2', PaymentRequest.Status.PENDING_CFO)
        streams = pending_approvals_for(self.raiser)
        self.assertIsNone(self._payment_request_stream(streams))

    def test_empty_queue_no_stream(self):
        # CFO with nothing pending should not get a zero-count noisy row.
        streams = pending_approvals_for(self.cfo)
        self.assertIsNone(self._payment_request_stream(streams))
