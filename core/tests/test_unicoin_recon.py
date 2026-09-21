"""Tests for the UniCoin reconciliation-exception spine (CFO direction 2026-09-01)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import OmniTask, UniCoinReconException
from core.unicoin_recon import (
    CFO_EMAIL, RECON_SOURCE, SLA_DAYS, clear_exception, raise_recon_exception,
)


class RaiseReconExceptionTests(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('cfo', email=CFO_EMAIL, password='x')
        self.owner = User.objects.create_user(
            'ktshut', email='ktshutlhedi@alphadirect.co.bw', password='x')

    def test_raise_creates_exception_and_owned_sla_task(self):
        exc = raise_recon_exception(
            policy_number='MIS-123', kind='debit_past_plan',
            reason='3 debits collected on a once-off plan', amount=Decimal('1500'),
            period='2026-09', recommended_action='Stop the mandate at RealPay',
            owner_email=self.owner.email, raised_by=self.cfo)
        self.assertEqual(exc.status, UniCoinReconException.Status.OPEN)
        self.assertEqual(exc.owner, self.owner)
        self.assertIsNotNone(exc.task)
        t = exc.task
        self.assertEqual(t.assignee, self.owner)
        self.assertEqual(t.assigner, self.cfo)
        self.assertEqual(t.source, RECON_SOURCE)
        self.assertEqual(t.priority, OmniTask.Priority.HIGH)
        self.assertEqual(t.due_at, timezone.localdate() + timedelta(days=SLA_DAYS))
        self.assertIn('MIS-123', t.title)
        self.assertIn('does not stop debits', t.body)          # never auto-acts
        self.assertIn('Stop the mandate at RealPay', t.body)

    def test_idempotent_same_finding_refreshes_no_second_task(self):
        e1 = raise_recon_exception(policy_number='P1', kind='not_posted',
                                   reason='r1', period='2026-09', raised_by=self.cfo)
        tasks_before = OmniTask.objects.count()
        e2 = raise_recon_exception(policy_number='P1', kind='not_posted',
                                   reason='r2 updated', amount=Decimal('9'),
                                   period='2026-09', raised_by=self.cfo)
        self.assertEqual(e1.id, e2.id)
        self.assertEqual(OmniTask.objects.count(), tasks_before)   # no duplicate task
        e2.refresh_from_db()
        self.assertEqual(e2.reason, 'r2 updated')
        self.assertEqual(e2.amount, Decimal('9'))

    def test_different_period_is_a_separate_exception(self):
        raise_recon_exception(policy_number='P1', kind='not_posted', reason='x',
                              period='2026-09', raised_by=self.cfo)
        raise_recon_exception(policy_number='P1', kind='not_posted', reason='x',
                              period='2026-10', raised_by=self.cfo)
        self.assertEqual(UniCoinReconException.objects.filter(policy_number='P1').count(), 2)

    def test_cleared_exception_is_not_reopened(self):
        e = raise_recon_exception(policy_number='P2', kind='other', reason='x',
                                  period='2026-09', raised_by=self.cfo)
        clear_exception(e, by=self.owner, note='fixed at realpay')
        e.refresh_from_db()
        self.assertEqual(e.status, UniCoinReconException.Status.CLEARED)
        self.assertEqual(e.task.status, OmniTask.Status.DONE)
        tasks_before = OmniTask.objects.count()
        e2 = raise_recon_exception(policy_number='P2', kind='other', reason='again',
                                   period='2026-09', raised_by=self.cfo)
        self.assertEqual(e2.status, UniCoinReconException.Status.CLEARED)  # stays cleared
        self.assertEqual(OmniTask.objects.count(), tasks_before)           # no new task

    @override_settings(UNICOIN_RECON_DEFAULT_OWNER='')
    def test_owner_falls_back_to_cfo_when_unresolved(self):
        exc = raise_recon_exception(policy_number='P3', kind='other', reason='x',
                                    owner_email='nobody@nowhere.tld', raised_by=self.cfo)
        self.assertEqual(exc.owner, self.cfo)   # never lost — routes to the CFO

    def test_validation(self):
        with self.assertRaises(ValidationError):
            raise_recon_exception(policy_number='', kind='other', reason='x', raised_by=self.cfo)
        with self.assertRaises(ValidationError):
            raise_recon_exception(policy_number='P', kind='other', reason='', raised_by=self.cfo)
        with self.assertRaises(ValidationError):
            raise_recon_exception(policy_number='P', kind='bogus', reason='x', raised_by=self.cfo)

    def test_string_amount_ok_but_garbage_amount_rejected(self):
        # The JSON API sends amount as a string — a good one parses, a bad one is
        # a 400, never a silent zero and never a 500 in the task-body formatter.
        exc = raise_recon_exception(policy_number='P9', kind='other', reason='x',
                                    amount='1,250.50', period='2026-09', raised_by=self.cfo)
        self.assertEqual(exc.amount, Decimal('1250.50'))
        with self.assertRaises(ValidationError):
            raise_recon_exception(policy_number='P10', kind='other', reason='x',
                                  amount='not-a-number', raised_by=self.cfo)


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['*'])
class ReconEndpointTests(TestCase):
    def setUp(self):
        User.objects.create_user('cfo', email=CFO_EMAIL, password='x')
        self.admin = User.objects.create_superuser('boss', email='boss@alphadirect.co.bw', password='x')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_post_get_and_clear(self):
        r = self.client.post('/api/v1/unicoin/recon-exceptions/', {
            'policy_number': 'MIS-900', 'kind': 'collecting_cancelled',
            'reason': 'Still collecting 41 days after cancellation', 'amount': '250.00',
            'period': '2026-09',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'open')
        exc_id = r.json()['id']
        self.assertTrue(OmniTask.objects.filter(source=RECON_SOURCE).exists())

        g = self.client.get('/api/v1/unicoin/recon-exceptions/?status=open')
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()['count'], 1)

        c = self.client.post(f'/api/v1/unicoin/recon-exceptions/{exc_id}/clear/',
                             {'note': 'stopped the mandate'}, format='json')
        self.assertEqual(c.status_code, 200)
        self.assertEqual(c.json()['status'], 'cleared')

    def test_bad_input_is_400(self):
        r = self.client.post('/api/v1/unicoin/recon-exceptions/',
                             {'policy_number': 'X', 'kind': 'other'}, format='json')  # no reason
        self.assertEqual(r.status_code, 400, r.content)
