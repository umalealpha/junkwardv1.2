"""
customer_refunds/tests.py — model + inbound + gating.

Runs under the Docker backend (no local Django env on the Mac). Exercises the
PII encryption, the Graphite inbound handoff (auth + idempotency + last-4
masking), and the FNB preview gate — without moving money.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import CustomerRefund
from .services import fnb_send_enabled, load_refund_to_fnb


class AccountEncryptionTests(TestCase):
    def test_account_number_encrypted_and_last4(self):
        r = CustomerRefund(graphite_ref='g1', policy_number='MIS2026000001',
                           refund_amount=Decimal('99.00'))
        r.set_account_number('60123456789')
        r.save()
        self.assertEqual(r.account_last4, '6789')
        self.assertNotIn('60123456789', r.account_number_enc)   # ciphertext, not clear
        self.assertEqual(r.get_account_number(), '60123456789')  # round-trips

    def test_empty_account_number(self):
        r = CustomerRefund(graphite_ref='g2', policy_number='MIS2', refund_amount=1)
        r.set_account_number('')
        self.assertEqual(r.account_number_enc, '')
        self.assertEqual(r.get_account_number(), '')


@override_settings(REFUND_INBOUND_TOKEN='test-secret-123')
class InboundTests(TestCase):
    URL = '/api/v1/customer-refunds/inbound/'

    def _payload(self, **kw):
        base = {'graphite_ref': 'GR-1', 'policy_number': 'MIS2099999999',
                'refund_amount': 693.00, 'customer_name': 'Test Customer',
                'account_number': '60123456789', 'bank_name': 'FNB',
                'ai_greenlight': True}
        base.update(kw)
        return base

    def test_rejects_bad_token(self):
        resp = self.client.post(self.URL, self._payload(),
                                content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer wrong')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(CustomerRefund.objects.count(), 0)

    @override_settings(AZURE_SSO_ENABLED=True)
    def test_handoff_works_with_azure_sso_enabled(self):
        """AZURE_SSO_ENABLED is TRUE on prod, and AzureJWTAuthentication raises on
        any non-JWT Bearer value — which made this endpoint answer 403 "Bad token
        header" before the view ran, so Graphite's handoff could never
        authenticate whatever token it sent. Proven on prod 2026-07-25 and fixed
        by emptying authentication_classes. This test is the tripwire."""
        resp = self.client.post(self.URL, self._payload(),
                                content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer test-secret-123')
        self.assertEqual(resp.status_code, 201, resp.content[:200])

    @override_settings(AZURE_SSO_ENABLED=True)
    def test_bad_token_is_401_not_403_with_sso_on(self):
        resp = self.client.post(self.URL, self._payload(),
                                content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer wrong')
        self.assertEqual(resp.status_code, 401, resp.content[:200])

    def test_accepts_and_masks(self):
        resp = self.client.post(self.URL, self._payload(),
                                content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer test-secret-123')
        self.assertEqual(resp.status_code, 201)
        body = resp.json()['refund']
        self.assertEqual(body['account_last4'], '6789')
        self.assertNotIn('account_number', body)         # full number never emitted
        self.assertEqual(body['status'], 'finance_queue')
        r = CustomerRefund.objects.get(graphite_ref='GR-1')
        self.assertEqual(r.get_account_number(), '60123456789')

    def test_idempotent(self):
        h = {'HTTP_AUTHORIZATION': 'Bearer test-secret-123'}
        r1 = self.client.post(self.URL, self._payload(),
                              content_type='application/json', **h)
        r2 = self.client.post(self.URL, self._payload(),
                              content_type='application/json', **h)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)            # already received
        self.assertEqual(CustomerRefund.objects.count(), 1)

    def test_rejects_zero_amount(self):
        resp = self.client.post(self.URL, self._payload(refund_amount=0),
                                content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer test-secret-123')
        self.assertEqual(resp.status_code, 400)


@override_settings(REFUND_INBOUND_TOKEN='t')
class SegmentAreaTests(TestCase):
    """MIS (UniCoin) and D&C are separate areas — no cross-access."""
    def setUp(self):
        from django.contrib.auth.models import Group, User
        self.mis_user = User.objects.create_user('unicoin1', password='x')
        self.mis_user.groups.add(Group.objects.create(name='refund_area_mis'))
        self.dc_user = User.objects.create_user('fin1', password='x')
        self.dc_user.groups.add(Group.objects.create(name='refund_area_dc'))
        self.noone = User.objects.create_user('outsider', password='x')
        self.mis_r = CustomerRefund.objects.create(
            segment='mis', graphite_ref='m1', policy_number='MISa',
            refund_amount=Decimal('50'), status=CustomerRefund.Status.FINANCE_QUEUE)
        self.dc_r = CustomerRefund.objects.create(
            segment='commercial', graphite_ref='c1', policy_number='ADCa',
            refund_amount=Decimal('9000'), status=CustomerRefund.Status.FINANCE_QUEUE)

    def test_no_area_forbidden(self):
        self.client.force_login(self.noone)
        self.assertEqual(self.client.get('/api/v1/customer-refunds/queue/').status_code, 403)

    def test_unicoin_cannot_see_dc(self):
        self.client.force_login(self.mis_user)
        # queue shows only MIS
        q = self.client.get('/api/v1/customer-refunds/queue/').json()
        self.assertTrue(all(r['segment'] == 'mis' for r in q['results']))
        # direct hit on a D&C refund → 403
        self.assertEqual(
            self.client.get(f'/api/v1/customer-refunds/{self.dc_r.pk}/').status_code, 403)

    def test_dc_cannot_see_mis(self):
        self.client.force_login(self.dc_user)
        self.assertEqual(
            self.client.get(f'/api/v1/customer-refunds/{self.mis_r.pk}/').status_code, 403)
        self.assertEqual(
            self.client.get(f'/api/v1/customer-refunds/{self.dc_r.pk}/').status_code, 200)

    def test_dc_user_cannot_live_send_without_money_authority(self):
        # D&C area member may approve, but live FNB send needs money authority.
        self.client.force_login(self.dc_user)
        # approve may 200 / 400 (config) / 409 (fraud hold) — never 403 for a D&C member
        ap = self.client.post(f'/api/v1/customer-refunds/{self.dc_r.pk}/approve/')
        self.assertIn(ap.status_code, (200, 400, 409))
        self.assertNotEqual(ap.status_code, 403)
        # a live FNB send must never succeed for a non-money-authority user
        live = self.client.post(f'/api/v1/customer-refunds/{self.dc_r.pk}/load-to-fnb/?live=1')
        self.assertIn(live.status_code, (400, 403))


class RoleTests(TestCase):
    """Inputter vs reviewer vs >P50k CFO gate."""
    def setUp(self):
        from django.contrib.auth.models import Group, User
        self.inputter = User.objects.create_user('phatsimo_x', password='x')
        self.inputter.groups.add(Group.objects.create(name='refund_input_mis'))
        self.reviewer = User.objects.create_user('keetile_x', password='x')
        self.reviewer.groups.add(Group.objects.create(name='refund_area_dc'))
        self.mis = CustomerRefund.objects.create(
            segment='mis', graphite_ref='rm1', policy_number='MISr',
            refund_amount=Decimal('100'), status=CustomerRefund.Status.FINANCE_QUEUE)
        self.big = CustomerRefund.objects.create(
            segment='commercial', graphite_ref='rc1', policy_number='ADCr',
            refund_amount=Decimal('60000'), status=CustomerRefund.Status.FINANCE_QUEUE)

    def test_inputter_can_view_but_not_approve(self):
        self.client.force_login(self.inputter)
        self.assertEqual(self.client.get('/api/v1/customer-refunds/queue/').status_code, 200)
        r = self.client.post(f'/api/v1/customer-refunds/{self.mis.pk}/approve/')
        self.assertEqual(r.status_code, 403)          # separation of duties
        self.assertIn('not approve', r.json()['detail'])

    def test_reviewer_blocked_over_50k(self):
        self.client.force_login(self.reviewer)         # D&C reviewer, no money authority
        r = self.client.post(f'/api/v1/customer-refunds/{self.big.pk}/approve/')
        self.assertEqual(r.status_code, 403)
        self.assertIn('CFO', r.json()['detail'])

    def test_money_authority_can_approve_over_50k(self):
        from django.contrib.auth.models import Group
        # D&C reviewer + refund_money authority → may approve the >P50k refund.
        self.reviewer.groups.add(Group.objects.create(name='refund_money'))
        self.client.force_login(self.reviewer)
        r = self.client.post(f'/api/v1/customer-refunds/{self.big.pk}/approve/')
        self.assertNotEqual(r.status_code, 403)         # money-authority passes the >50k gate
        self.assertIn(r.status_code, (200, 400, 409))   # 400 config / 409 fraud, never 403


class FraudTests(TestCase):
    def _mk(self, **kw):
        base = dict(graphite_ref=kw.pop('ref'), policy_number=kw.pop('pol', 'P1'),
                    refund_amount=kw.pop('amt', Decimal('100')),
                    customer_name=kw.pop('name', 'Alice A'), segment='domestic',
                    status=CustomerRefund.Status.FINANCE_QUEUE)
        acct = kw.pop('acct', None)
        base.update(kw)
        r = CustomerRefund(**base)
        if acct:
            r.set_account_number(acct)
        r.save()
        return r

    def test_fingerprint_matches_same_account(self):
        from .models import account_fingerprint
        # digits-only, formatting-insensitive
        self.assertEqual(account_fingerprint('6000 123'), account_fingerprint('6000123'))
        self.assertNotEqual(account_fingerprint('6000123'), account_fingerprint('6000124'))
        self.assertEqual(account_fingerprint(''), '')

    def test_same_account_different_names_is_critical(self):
        from .fraud import scan_refund
        self._mk(ref='f1', name='Alice A', acct='60001234')
        r2 = self._mk(ref='f2', name='Bob B', acct='60001234')   # SAME account, diff name
        res = scan_refund(r2)
        codes = {f['code'] for f in res['flags']}
        self.assertIn('account_shared_diff_names', codes)
        self.assertTrue(any(f['severity'] == 'CRITICAL' for f in res['flags']))
        self.assertGreaterEqual(res['score'], 25)

    def test_same_account_same_name_not_flagged_for_names(self):
        from .fraud import scan_refund
        self._mk(ref='g1', name='Alice A', acct='60009999')
        r2 = self._mk(ref='g2', name='alice   a', acct='60009999')  # same person, spacing
        codes = {f['code'] for f in scan_refund(r2)['flags']}
        self.assertNotIn('account_shared_diff_names', codes)

    def test_no_premium_history_skipped_when_feed_empty(self):
        # Empty Graphite mirror → we can't judge → must NOT flag every refund.
        from .fraud import scan_refund
        r = self._mk(ref='np1', pol='POLX', acct='60007777')
        codes = {f['code'] for f in scan_refund(r)['flags']}
        self.assertNotIn('no_premium_history', codes)
        self.assertNotIn('refund_exceeds_premiums_paid', codes)

    def test_premium_stats_ignore_failed_and_reversed(self):
        from integrations.models import GraphitePaymentTransaction as G
        from .fraud import scan_refund
        G.objects.create(graphite_id=1, policy_number='POLY', amount=Decimal('100'),
                         status_norm='success', is_refund=False, is_reverse=False)
        G.objects.create(graphite_id=2, policy_number='POLY', amount=Decimal('500'),
                         status_norm='failed', is_refund=False, is_reverse=False)
        G.objects.create(graphite_id=3, policy_number='POLY', amount=Decimal('300'),
                         status_norm='success', is_refund=False, is_reverse=True)
        # Only the 100 success/non-reversed counts → a 150 refund exceeds premiums.
        r = self._mk(ref='pp1', pol='POLY', amt=Decimal('150'), acct='60008888')
        codes = {f['code'] for f in scan_refund(r)['flags']}
        self.assertIn('refund_exceeds_premiums_paid', codes)

    def test_controls_bypass_tripwire(self):
        from django.utils import timezone
        from .fraud import scan_refund
        r = self._mk(ref='b1', acct='60005555')
        r.status = CustomerRefund.Status.PAID
        r.paid_at = timezone.now()      # paid, no approver, no payment/batch
        codes = {f['code'] for f in scan_refund(r)['flags']}
        self.assertIn('paid_without_controls', codes)


class AiFraudReviewTests(TestCase):
    def test_pii_free_input_and_parsed_verdict(self):
        from unittest.mock import patch
        r = CustomerRefund.objects.create(
            segment='mis', graphite_ref='ai1', policy_number='SECRETPOL9',
            customer_name='John Secret', refund_amount=Decimal('700'),
            reason='Cooling-off', status=CustomerRefund.Status.FINANCE_QUEUE)
        r.set_account_number('60011112222'); r.save()
        captured = {}

        def fake(prompt, **kw):
            captured['prompt'] = prompt
            return '{"risk":"low","reasons":["nothing unusual"],"recommend":"approve"}'

        with patch('core.ai_assist.reasoning_complete', side_effect=fake):
            from customer_refunds.ai_fraud import deepseek_fraud_review
            out = deepseek_fraud_review(r)

        self.assertTrue(out['ok'])
        self.assertEqual(out['risk'], 'low')
        self.assertEqual(out['recommend'], 'approve')
        # No customer PII may appear in what is sent to the AI.
        for leak in ('John Secret', '60011112222', 'SECRETPOL9'):
            self.assertNotIn(leak, captured['prompt'])
        # But the useful non-PII context IS there.
        self.assertIn('mis', captured['prompt'])
        self.assertIn('P100', captured['prompt'])   # amount band


class FnbGateTests(TestCase):
    @override_settings(REFUND_FNB_SEND_ENABLED=False)
    def test_send_disabled_flag(self):
        self.assertFalse(fnb_send_enabled())

    @override_settings(REFUND_FNB_SEND_ENABLED=False)
    def test_load_is_preview_when_flag_off(self):
        # A refund with a payment stub — preview must NOT call FNB.
        from payments.models import Payment
        r = CustomerRefund.objects.create(
            graphite_ref='GR-P', policy_number='MISx', refund_amount=Decimal('50'),
            currency='BWP', bank_name='FNB', branch_code='250655')
        r.set_account_number('62000000000'); r.save()
        # fake minimal payment
        r.payment = Payment(payee_name='X', payee_bank_name='FNB',
                            payee_branch_code='250655', amount=Decimal('50'),
                            reference='Refund MISx')
        # don't save Payment (no full FK graph); load_refund_to_fnb only reads attrs
        out = load_refund_to_fnb(r, user=None, live=True)   # live requested…
        self.assertEqual(out['mode'], 'preview')            # …but flag off → preview
        self.assertIn('would_send', out)
