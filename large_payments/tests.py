"""Tests for the Large Payment Authorisation request.

The tests that matter most here are the ones that fail if this module ever grows
a way to touch money: test_raising_a_request_changes_no_payment and
test_module_never_saves_a_payment_request. Everything else is behaviour.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from taskboard.models import PaymentRequest

from large_payments.models import (LargePaymentLine, LargePaymentRequest,
                                   default_threshold)
from large_payments.selection import candidates, claim_number_for

User = get_user_model()


def _payment(ref, total, *, claim='', loaded=True, category='claim',
             status='paid', payee='Optimum Panel Beaters'):
    return PaymentRequest.objects.create(
        ref=ref,
        subject=f'{claim} {payee}'.strip(),
        payee=payee,
        category=category,
        status=status,
        total=Decimal(total),
        line_items=[{'description': f'{claim} {payee}'.strip(),
                     'claim_number': claim, 'amount': str(total)}],
        fnb_loaded_at=timezone.now() if loaded else None,
    )


class SelectionTests(TestCase):
    """Which payments the box offers, and which it must never offer."""

    def test_claim_number_read_from_the_line_field(self):
        p = _payment('PAY/1', '30000.00', claim='G2026005105')
        self.assertEqual(claim_number_for(p), 'G2026005105')

    def test_claim_number_read_from_the_front_of_the_description(self):
        """Roughly half the live lines carry the claim this way, not in the field."""
        p = PaymentRequest.objects.create(
            ref='PAY/2', subject='CARFIL', payee='CARFIL SERVICES',
            category='claim', status='paid', total=Decimal('25000.00'),
            line_items=[{'description': 'G2026004287 CARFIL SERVICES',
                         'amount': '25000.00'}],
            fnb_loaded_at=timezone.now())
        self.assertEqual(claim_number_for(p), 'G2026004287')

    def test_a_payment_with_no_claim_number_is_not_invented(self):
        p = _payment('PAY/3', '40000.00', claim='')
        self.assertEqual(claim_number_for(p), '')

    def test_only_claim_payments_that_reached_fnb_are_offered(self):
        wanted   = _payment('PAY/OK',   '30000.00', claim='G2026000001')
        _payment('PAY/NOTLOADED', '30000.00', claim='G2026000002', loaded=False)
        _payment('PAY/SUPPLIER',  '30000.00', claim='G2026000003', category='supplier')
        _payment('PAY/REJECTED',  '30000.00', claim='G2026000004', status='rejected')

        rows, _ = candidates()
        refs = {r['ref'] for r in rows}
        self.assertEqual(refs, {wanted.ref})

    @override_settings(LARGE_PAYMENT_THRESHOLD_BWP='20000.00')
    def test_small_payments_are_shown_but_not_ticked(self):
        """He asked to be able to add a smaller one by hand, so it must be visible."""
        _payment('PAY/BIG',   '30000.00', claim='G2026000010')
        _payment('PAY/SMALL',  '5000.00', claim='G2026000011')

        rows, cut = candidates()
        self.assertEqual(cut, Decimal('20000.00'))
        by_ref = {r['ref']: r for r in rows}
        self.assertEqual(len(by_ref), 2)
        self.assertTrue(by_ref['PAY/BIG']['over_threshold'])
        self.assertFalse(by_ref['PAY/SMALL']['over_threshold'])


class ApiTests(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', 'pganesharajah@alphadirect.co.bw',
                                            'x', is_superuser=True, is_staff=True)
        self.finance = User.objects.create_user('lntabeni', 'lntabeni@alphadirect.co.bw', 'x')
        self.outsider = User.objects.create_user('someone', 'someone@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.p1 = _payment('PAY/A', '88099.65', claim='G2026005105')
        self.p2 = _payment('PAY/B', '30000.00', claim='G2026004941', payee='Nors Botswana')

    # ── who may do what ──────────────────────────────────────────────────────

    def test_an_outsider_cannot_see_the_candidates(self):
        self.client.force_authenticate(self.outsider)
        r = self.client.get('/api/v1/large-payments/candidates/')
        self.assertEqual(r.status_code, 403)

    def test_a_finance_approver_can_see_the_candidates(self):
        self.client.force_authenticate(self.finance)
        r = self.client.get('/api/v1/large-payments/candidates/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 2)

    def test_only_the_cfo_may_decide(self):
        req = self._raise()
        req.status = LargePaymentRequest.Status.PENDING_CFO
        req.save()
        self.client.force_authenticate(self.finance)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403)

    # ── raising one ──────────────────────────────────────────────────────────

    def _raise(self, ids=None):
        self.client.force_authenticate(self.finance)
        with patch('large_payments.api_views.enrich_in_background') as bg:
            r = self.client.post('/api/v1/large-payments/', {
                'payment_request_ids': ids or [str(self.p1.id), str(self.p2.id)],
                'title': 'Claims Payments 11 September 2026',
            }, format='json')
            self.bg = bg
        self.assertEqual(r.status_code, 201, r.data)
        return LargePaymentRequest.objects.get(pk=r.data['id'])

    def test_raising_a_request_snapshots_the_payments(self):
        req = self._raise()
        self.assertEqual(req.total, Decimal('118099.65'))
        self.assertEqual(req.lines.count(), 2)
        line = req.lines.get(claim_number='G2026005105')
        self.assertEqual(line.amount, Decimal('88099.65'))
        self.assertEqual(line.payee, 'Optimum Panel Beaters')

    def test_the_snapshot_does_not_follow_a_later_amendment(self):
        """An authorisation must say tomorrow what it said when it was approved."""
        req = self._raise()
        self.p1.total = Decimal('1.00')
        self.p1.payee = 'Someone Else'
        self.p1.save()
        line = req.lines.get(claim_number='G2026005105')
        line.refresh_from_db()
        self.assertEqual(line.amount, Decimal('88099.65'))
        self.assertEqual(line.payee, 'Optimum Panel Beaters')

    def test_the_threshold_in_force_is_written_onto_the_request(self):
        with override_settings(LARGE_PAYMENT_THRESHOLD_BWP='15000.00'):
            req = self._raise()
        self.assertEqual(req.threshold, Decimal('15000.00'))
        with override_settings(LARGE_PAYMENT_THRESHOLD_BWP='99999.00'):
            req.refresh_from_db()
            self.assertEqual(req.threshold, Decimal('15000.00'))

    def test_raising_starts_the_graphite_pull(self):
        req = self._raise()
        self.assertEqual(req.status, LargePaymentRequest.Status.ENRICHING)
        self.bg.assert_called_once()

    def test_a_double_tap_creates_one_line_per_payment(self):
        req = self._raise(ids=[str(self.p1.id), str(self.p1.id)])
        self.assertEqual(req.lines.count(), 1)

    def test_the_same_payment_cannot_go_on_two_live_requests(self):
        self._raise(ids=[str(self.p1.id)])
        self.client.force_authenticate(self.finance)
        with patch('large_payments.api_views.enrich_in_background'):
            r = self.client.post('/api/v1/large-payments/',
                                 {'payment_request_ids': [str(self.p1.id)]},
                                 format='json')
        self.assertEqual(r.status_code, 409)

    def test_a_cancelled_request_releases_its_payments(self):
        req = self._raise(ids=[str(self.p1.id)])
        req.status = LargePaymentRequest.Status.CANCELLED
        req.save()
        rows, _ = candidates()
        self.assertIn('PAY/A', {r['ref'] for r in rows})

    def test_raising_with_nothing_ticked_is_refused_in_plain_words(self):
        self.client.force_authenticate(self.finance)
        r = self.client.post('/api/v1/large-payments/',
                             {'payment_request_ids': []}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Tick at least one', r.data['detail'])

    # ── the CFO's decision ───────────────────────────────────────────────────

    def test_approving_now_sends_it_to_the_ceo(self):
        """SUPERSEDED BY PIECE 2 (CFO 2026-09-12). This test used to assert the
        opposite — that approving recorded the CFO's authorisation and emailed
        nobody, because the CEO leg was not built. Piece 2 built it, so approving
        now SENDS, and the request moves to "with the CEO". The old assertion is
        replaced rather than deleted so the change of behaviour is visible."""
        req = self._raise()
        req.status = LargePaymentRequest.Status.PENDING_CFO
        req.save()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        req.refresh_from_db()
        self.assertEqual(req.status, LargePaymentRequest.Status.SENT_TO_CEO)

    def test_a_rejection_must_carry_a_reason(self):
        req = self._raise()
        req.status = LargePaymentRequest.Status.PENDING_CFO
        req.save()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'reject'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_a_request_still_collecting_cannot_be_approved(self):
        req = self._raise()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{req.id}/decide/',
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409)

    # ── the line that must never be crossed ──────────────────────────────────

    def test_raising_a_request_changes_no_payment(self):
        """The whole design in one assertion.

        Omni's payment create path carries ~600 lines of money controls. This
        module is a reader; if it ever starts writing to a payment it will have
        found a second way in, past every one of them. Fails the moment that
        becomes true.
        """
        before = {
            p.id: (p.total, p.status, p.payee, p.fnb_loaded_at, p.updated_at)
            for p in PaymentRequest.objects.all()
        }
        self._raise()
        after = {
            p.id: (p.total, p.status, p.payee, p.fnb_loaded_at, p.updated_at)
            for p in PaymentRequest.objects.all()
        }
        self.assertEqual(before, after)

    def test_module_never_saves_a_payment_request(self):
        """Belt and braces: no PaymentRequest.save() anywhere in the flow."""
        with patch.object(PaymentRequest, 'save',
                          side_effect=AssertionError(
                              'large_payments must never write to a payment')):
            self._raise()


class ProgressTests(TestCase):
    """The percentage on his screen must be the real position, never an animation."""

    def setUp(self):
        self.finance = User.objects.create_user('lntabeni2', 'lntabeni@alphadirect.co.bw', 'x')
        self.req = LargePaymentRequest.objects.create(
            ref='LPR/TEST/0001', raised_by=self.finance, total=Decimal('0'))

    def test_an_unreachable_graphite_fails_loudly_and_is_retryable(self):
        LargePaymentLine.objects.create(
            request=self.req,
            payment_request=_payment('PAY/Z', '30000.00', claim='G2026000099'),
            payment_ref='PAY/Z', amount=Decimal('30000.00'),
            claim_number='G2026000099')

        from large_payments import enrich as enrich_mod
        with patch.object(enrich_mod.graphite_ro, 'is_configured', return_value=False):
            enrich_mod.enrich(self.req.id)

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.ENRICH_FAILED)
        self.assertIn('Graphite', self.req.enrich_error)
        # Never silently "ready": it must not have reached the CFO.
        self.assertIsNone(self.req.cfo_task_id)

    def test_a_request_with_no_lines_finishes_rather_than_hanging(self):
        from large_payments import enrich as enrich_mod
        enrich_mod.enrich(self.req.id)
        self.req.refresh_from_db()
        self.assertEqual(self.req.progress_pct, 100)
        self.assertEqual(self.req.status, LargePaymentRequest.Status.PENDING_CFO)
