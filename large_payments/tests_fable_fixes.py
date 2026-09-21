"""Regressions for the defects Fable 5.1 found in the ship-gate review, 2026-09-11.

Each test here was written against a real defect in the first cut of this module,
and each was proven red by reverting its fix before being kept.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import OmniTask

from large_payments.models import LargePaymentLine, LargePaymentRequest
from large_payments.tests import _payment

User = get_user_model()


class FableFixTests(TestCase):

    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x',
            is_superuser=True, is_staff=True)
        self.finance = User.objects.create_user(
            'lntabeni', 'lntabeni@alphadirect.co.bw', 'x')
        self.client = APIClient()

    def _req(self, state=LargePaymentRequest.Status.PENDING_CFO, ref='LPR/FIX/0001'):
        return LargePaymentRequest.objects.create(
            ref=ref, raised_by=self.finance, status=state,
            total=Decimal('30000.00'))

    # ── Fix 2: holding superuser is not being the CFO ────────────────────────

    def test_a_superuser_who_is_not_the_cfo_cannot_decide(self):
        """Holding superuser in Omni must not carry the power to sign off the
        CFO's own authorisation: his instruction was that it 'sits for MY
        approval'. The QC robots are superusers, so this is not hypothetical."""
        other = User.objects.create_user('qc-robot', 'qc@alphadirect.co.bw', 'x',
                                         is_superuser=True, is_staff=True)
        req = self._req()
        self.client.force_authenticate(other)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_the_cfo_himself_can_still_decide(self):
        req = self._req(ref='LPR/FIX/0002')
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200)

    # ── Fix 4a: never a second collection thread beside a live one ───────────

    def test_a_run_that_is_still_moving_refuses_a_retry(self):
        req = self._req(LargePaymentRequest.Status.ENRICHING, ref='LPR/FIX/0003')
        self.client.force_authenticate(self.finance)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/retry/')
        self.assertEqual(r.status_code, 409)

    def test_a_run_that_has_stopped_says_so_and_can_be_retried(self):
        from large_payments import api_views as v
        req = self._req(LargePaymentRequest.Status.ENRICHING, ref='LPR/FIX/0004')
        stale = timezone.now() - timedelta(seconds=v.STALL_SECONDS + 5)
        LargePaymentRequest.objects.filter(pk=req.pk).update(updated_at=stale)

        self.client.force_authenticate(self.finance)
        r = self.client.get(f'/api/v1/large-payments/{req.id}/')
        self.assertTrue(r.data['stalled'],
                        'the screen must be told the run has stopped')

        with patch('large_payments.api_views.enrich_in_background'):
            r = self.client.post(f'/api/v1/large-payments/{req.id}/retry/')
        self.assertEqual(r.status_code, 200)

    # ── Fix 4b: one CFO task per request, even from two threads ──────────────

    def test_two_finishing_threads_raise_one_cfo_task(self):
        from large_payments.tasks import raise_cfo_task
        req = self._req(LargePaymentRequest.Status.ENRICHING, ref='LPR/FIX/0005')
        stale_copy = LargePaymentRequest.objects.get(pk=req.pk)   # both read None

        raise_cfo_task(req)
        raise_cfo_task(stale_copy)

        req.refresh_from_db()
        self.assertIsNotNone(req.cfo_task_id)
        self.assertEqual(
            OmniTask.objects.filter(source='large_payment_request').count(), 1,
            'the CFO must not get two identical tasks for one request')

    # ── Fix 5: every lookup failing is an outage, not a finished run ─────────

    def test_when_every_claim_lookup_fails_it_is_not_marked_ready(self):
        from large_payments import enrich as enrich_mod
        req = self._req(LargePaymentRequest.Status.ENRICHING, ref='LPR/FIX/0006')
        LargePaymentLine.objects.create(
            request=req,
            payment_request=_payment('PAY/ERR', '30000.00', claim='G2026000077'),
            payment_ref='PAY/ERR', amount=Decimal('30000.00'),
            claim_number='G2026000077')

        class _Dead:
            def cursor(self):
                raise RuntimeError('connection gone')

        @contextmanager
        def _cx():
            yield _Dead()

        with patch.object(enrich_mod.graphite_ro, 'is_configured', return_value=True), \
             patch.object(enrich_mod.graphite_ro, 'connection', _cx):
            enrich_mod.enrich(req.id)

        req.refresh_from_db()
        self.assertEqual(req.status, LargePaymentRequest.Status.ENRICH_FAILED)
        self.assertIsNone(req.cfo_task_id,
                          'a dead connection must never reach the CFO as ready')

    def test_one_bad_claim_among_several_is_only_a_flag(self):
        """The opposite mistake, and the common case: one claim failing must not
        throw away a run that otherwise worked."""
        from large_payments import enrich as enrich_mod
        req = self._req(LargePaymentRequest.Status.ENRICHING, ref='LPR/FIX/0007')
        for ref, claim in (('PAY/G1', 'G2026000081'), ('PAY/G2', 'G2026000082')):
            LargePaymentLine.objects.create(
                request=req,
                payment_request=_payment(ref, '30000.00', claim=claim),
                payment_ref=ref, amount=Decimal('30000.00'), claim_number=claim)

        calls = {'n': 0}

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise RuntimeError('one bad claim')

            def fetchone(self):
                return {'claim_number': 'G2026000082', 'claim_status': 'Open',
                        'claim_sub_status': '', 'policy_number': 'COMG1',
                        'business_name': None, 'first_name': 'Boipuso',
                        'last_name': 'Pelo', 'company_name': None,
                        'date_of_loss': None, 'loss_description': 'Thumb injury',
                        'reserve_amount': '50000.00', 'paid_amount': None}

        class _Cx:
            def cursor(self):
                return _Cur()

        @contextmanager
        def _cx():
            yield _Cx()

        with patch.object(enrich_mod.graphite_ro, 'is_configured', return_value=True), \
             patch.object(enrich_mod.graphite_ro, 'connection', _cx):
            enrich_mod.enrich(req.id)

        req.refresh_from_db()
        self.assertEqual(req.status, LargePaymentRequest.Status.PENDING_CFO)
        self.assertEqual(req.lines.filter(enrich_status='error').count(), 1)
        self.assertEqual(req.lines.filter(enrich_status='ok').count(), 1)
