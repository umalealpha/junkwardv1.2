"""L-AGENT (CFO 18-Sep-2026): an Aria payment approve/reject completes only when
the user taps Confirm, is checked server-side with a short-lived intent, and goes
through the payment screen's own decide code so every control applies."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.aria.action_intents import issue_intent
from core.aria.qc_tools import approve_payment, reject_payment
from core.models import AuditLog
from taskboard.models import PaymentRequest

FIRST = 'taskboard.payment_views._is_first_approver'


class AriaConfirmTests(TestCase):
    def setUp(self):
        cache.clear()
        U = get_user_model()
        self.raiser = U.objects.create_user(username='raiser', email='raiser@alphadirect.co.bw', password='x')
        self.pako = U.objects.create_user(username='pkago', email='pkago@alphadirect.co.bw', password='x')
        self.legakwa = U.objects.create_user(username='lntabeni', email='lntabeni@alphadirect.co.bw', password='x')
        self.cfo = U.objects.create_user(username='pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        self.hr = U.objects.create_user(username='hrperson', email='ktshutlhedi@alphadirect.co.bw', password='x')
        self.n = 0

    def pay(self, status, **kw):
        self.n += 1
        data = dict(ref=f'PAY-T{self.n:05d}', entity='Alpha Direct Insurance Company', category='supplier',
                    subject='Test', payee='Vendor X', currency='BWP', total=Decimal('1000.00'),
                    status=status, created_by=self.raiser)
        data.update(kw)
        return PaymentRequest.objects.create(**data)

    def first(self, *users):
        emails = {u.email for u in users}
        return mock.patch(FIRST, side_effect=lambda u: getattr(u, 'email', '') in emails)

    # ── the card never acts ────────────────────────────────────────────────
    def test_first_call_changes_nothing_and_returns_a_card(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            card = approve_payment(ref=p.ref, user=self.pako)
        self.assertTrue(card['needs_confirmation'])
        self.assertTrue(card['confirm_token'])
        self.assertEqual(card['currency'], 'BWP')
        p.refresh_from_db()
        self.assertEqual(p.status, 'pending_finance')
        self.assertFalse(AuditLog.objects.filter(record_id=str(p.pk)).exists())

    def test_card_shows_the_real_currency(self):
        p = self.pay('pending_finance', currency='USD')
        with self.first(self.pako):
            self.assertEqual(approve_payment(ref=p.ref, user=self.pako)['currency'], 'USD')

    # ── Aria never marks a payment paid ────────────────────────────────────
    def test_cfo_stage_approve_is_refused_never_marked_paid(self):
        p = self.pay('pending_cfo', first_approver=self.pako)
        out = approve_payment(ref=p.ref, user=self.cfo)
        self.assertFalse(out['ok'])
        self.assertIn('FNB', out['error'])
        p.refresh_from_db()
        self.assertEqual(p.status, 'pending_cfo')

    # ── the tap completes through the screen's own code ───────────────────
    def test_tap_signs_off_through_the_payment_screen(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            with mock.patch('core.aria.qc_tools._decide_on_the_screen',
                            wraps=__import__('core.aria.qc_tools', fromlist=['x'])._decide_on_the_screen) as screen:
                out = approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        screen.assert_called_once()
        self.assertTrue(out['ok'], out)
        p.refresh_from_db()
        self.assertEqual(p.status, 'pending_cfo')
        a = AuditLog.objects.get(record_id=str(p.pk), description__contains='Aria')
        self.assertEqual(a.new_values['confirmed_via'], 'server_intent')

    def test_screen_refusal_is_reported_and_nothing_logged_as_aria(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            with mock.patch('core.aria.qc_tools._decide_on_the_screen',
                            return_value=(False, 409, {'detail': 'Bank details not confirmed'})):
                out = approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        self.assertFalse(out['ok'])
        self.assertIn('Bank details', out['error'])
        self.assertFalse(AuditLog.objects.filter(record_id=str(p.pk), description__contains='Aria').exists())

    # ── SOD and who may act ────────────────────────────────────────────────
    def test_raiser_cannot_sign_off_own_payment(self):
        p = self.pay('pending_finance', created_by=self.pako)
        with self.first(self.pako):
            out = approve_payment(ref=p.ref, user=self.pako)
        self.assertFalse(out['ok'])
        self.assertNotIn('confirm_token', out)

    def test_non_approver_power_user_cannot_reject(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            out = reject_payment(ref=p.ref, user=self.hr, reason='no')
        self.assertFalse(out['ok'])

    def test_approver_cannot_reject_own_signoff_at_cfo_stage(self):
        p = self.pay('pending_cfo', first_approver=self.pako)
        with self.first(self.pako, self.legakwa):
            self.assertFalse(reject_payment(ref=p.ref, user=self.pako, reason='x')['ok'])
            self.assertTrue(reject_payment(ref=p.ref, user=self.legakwa, reason='x')['ok'])
        self.assertTrue(reject_payment(ref=p.ref, user=self.cfo, reason='x')['ok'])

    def test_reject_needs_a_reason(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            self.assertFalse(reject_payment(ref=p.ref, user=self.pako, reason='')['ok'])

    # ── token rules ────────────────────────────────────────────────────────
    def test_token_for_another_user_is_refused(self):
        p = self.pay('pending_finance')
        with self.first(self.pako, self.legakwa):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            out = approve_payment(ref=p.ref, user=self.legakwa, confirm_token=token)
        self.assertFalse(out['ok'])
        p.refresh_from_db()
        self.assertEqual(p.status, 'pending_finance')

    def test_stale_record_is_refused(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            p.total = Decimal('999999.00'); p.save()
            out = approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        self.assertFalse(out['ok'])
        self.assertIn('changed', out['error'])

    def test_reason_changed_after_the_card_is_refused(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = reject_payment(ref=p.ref, user=self.pako, reason='duplicate')['confirm_token']
            out = reject_payment(ref=p.ref, user=self.pako, reason='something else', confirm_token=token)
        self.assertFalse(out['ok'])

    def test_expired_token_is_refused(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            with mock.patch('django.core.signing.time.time', return_value=__import__('time').time() + 3600):
                out = approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        self.assertFalse(out['ok'])
        self.assertIn('expired', out['error'])

    def test_forged_token_is_refused(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            self.assertFalse(approve_payment(ref=p.ref, user=self.pako, confirm_token='x:y')['ok'])

    def test_replay_refused_even_when_the_cache_is_gone(self):
        p = self.pay('pending_cfo', first_approver=self.pako)
        token = reject_payment(ref=p.ref, user=self.cfo, reason='dup')['confirm_token']
        self.assertTrue(reject_payment(ref=p.ref, user=self.cfo, reason='dup', confirm_token=token)['ok'])
        cache.clear()
        self.assertFalse(reject_payment(ref=p.ref, user=self.cfo, reason='dup', confirm_token=token)['ok'])

    # ── the model can never complete it ────────────────────────────────────
    def test_model_passing_a_token_still_gets_only_a_card(self):
        from core.ai_assist import _aria_execute_tool
        p = self.pay('pending_cfo', first_approver=self.pako)
        token = reject_payment(ref=p.ref, user=self.cfo, reason='dup')['confirm_token']
        out = _aria_execute_tool('reject_payment', {'ref': p.ref, 'reason': 'dup', 'confirm_token': token},
                                 user=self.cfo, dashboard_context={})
        self.assertTrue(out.get('needs_confirmation'))
        p.refresh_from_db()
        self.assertEqual(p.status, 'pending_cfo')

    # ── the endpoint is the tap ────────────────────────────────────────────
    def post(self, user, body):
        c = APIClient(); c.force_authenticate(user)
        return c.post('/api/v1/ai/aria/confirm-action/', body, format='json')

    def test_endpoint_completes_the_tap(self):
        p = self.pay('pending_cfo', first_approver=self.pako)
        token = reject_payment(ref=p.ref, user=self.cfo, reason='dup')['confirm_token']
        r = self.post(self.cfo, {'action': 'reject', 'ref': p.ref, 'reason': 'dup', 'confirm_token': token})
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual(p.status, 'rejected')

    def test_endpoint_refuses_non_power_user(self):
        p = self.pay('pending_cfo', first_approver=self.pako)
        token = reject_payment(ref=p.ref, user=self.cfo, reason='dup')['confirm_token']
        r = self.post(self.raiser, {'action': 'reject', 'ref': p.ref, 'reason': 'dup', 'confirm_token': token})
        self.assertEqual(r.status_code, 403)

    def test_endpoint_requires_a_token(self):
        p = self.pay('pending_cfo')
        r = self.post(self.cfo, {'action': 'reject', 'ref': p.ref, 'reason': 'x', 'confirm_token': ''})
        self.assertEqual(r.status_code, 400)


class AriaDuplicateGoesToCommitteeTests(AriaConfirmTests):
    def test_duplicate_sent_to_committee_is_not_reported_as_approved(self):
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            with mock.patch('core.aria.qc_tools._decide_on_the_screen',
                            return_value=(True, 200, {'control': 'PAY-DUP-01'})):
                out = approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        self.assertFalse(out['ok'])
        self.assertIn('exception committee', out['error'])
        self.assertFalse(AuditLog.objects.filter(record_id=str(p.pk), description__contains='Aria').exists())

    def test_screen_decide_runs_outside_our_transaction(self):
        from django.db import connection
        seen = {}
        def spy(p, user, decision, notes):
            seen['depth'] = len(connection.savepoint_ids)
            return (False, 409, {'detail': 'stop'})
        p = self.pay('pending_finance')
        with self.first(self.pako):
            token = approve_payment(ref=p.ref, user=self.pako)['confirm_token']
            baseline = len(connection.savepoint_ids)
            with mock.patch('core.aria.qc_tools._decide_on_the_screen', side_effect=spy):
                approve_payment(ref=p.ref, user=self.pako, confirm_token=token)
        # No transaction of ours is still open when the screen code runs.
        self.assertEqual(seen['depth'], baseline)
