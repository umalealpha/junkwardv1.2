"""taskboard/test_payment_task_detail.py — the pack behind a payment task.

CFO 2026-08-03: on the Nexus phone app a payment-authorisation card showed the
title and a grand total only. The approver could not see which suppliers,
invoices and amounts were inside it, so several BWP-thousand authorisations
were sitting there to be signed blind.

Two things make the detail reachable from the phone:
  * my-tasks/ now carries payment_request_id + payment_ref on a payment task,
  * the pack (and its attachments) is readable by the person the task is
    assigned to — not just the CFO, a finance approver, or the raiser.

Run in CI (needs a DB): manage.py test taskboard.test_payment_task_detail
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open


@window_always_open
class PaymentTaskDetailTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.kago = User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        self.clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.stranger = User.objects.create_user('nobody', email='nobody@alphadirect.co.bw')

    def _raise_request(self):
        """Clerk raises a two-line request; it lands on a finance approver."""
        self.client.force_authenticate(self.clerk)
        r = self.client.post(reverse('v1-payment-requests'), {
            'subject': 'Vendor payments',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [
                {'description': 'Botswana Power Corporation', 'amount': '12730.23'},
                {'description': 'Water Utilities', 'amount': '2682.05'},
            ],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            'account_name': 'Botswana Power Corporation', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return PaymentRequest.objects.get(pk=r.data['id'])

    # ── my-tasks carries the link ───────────────────────────────────────────
    def test_my_tasks_exposes_the_payment_link(self):
        pr = self._raise_request()
        assignee = pr.task.assignee
        self.client.force_authenticate(assignee)
        r = self.client.get(reverse('v1-taskboard-my-tasks'))
        self.assertEqual(r.status_code, 200, r.content)
        row = next(t for t in r.data if t['id'] == str(pr.task_id))
        self.assertEqual(row['payment_request_id'], str(pr.id))
        self.assertEqual(row['payment_ref'], pr.ref)

    def test_non_payment_task_has_no_payment_link(self):
        t = OmniTask.objects.create(
            assigner=self.cfo, assignee=self.clerk, title='Call the broker',
            status=OmniTask.Status.PENDING,
        )
        self.client.force_authenticate(self.clerk)
        r = self.client.get(reverse('v1-taskboard-my-tasks'))
        row = next(x for x in r.data if x['id'] == str(t.id))
        self.assertIsNone(row['payment_request_id'])
        self.assertEqual(row['payment_ref'], '')

    # ── the assignee can read the pack ──────────────────────────────────────
    def test_task_assignee_can_read_the_lines(self):
        pr = self._raise_request()
        self.client.force_authenticate(pr.task.assignee)
        r = self.client.get(reverse('v1-payment-request-detail', args=[pr.id]))
        self.assertEqual(r.status_code, 200, r.content)
        descriptions = [ln['description'] for ln in r.data['line_items']]
        self.assertEqual(descriptions, ['Botswana Power Corporation', 'Water Utilities'])
        self.assertEqual(r.data['total'], '15412.28')

    def test_someone_with_no_claim_on_it_still_cannot_read_it(self):
        pr = self._raise_request()
        self.client.force_authenticate(self.stranger)
        r = self.client.get(reverse('v1-payment-request-detail', args=[pr.id]))
        self.assertEqual(r.status_code, 403, r.content)

    def test_assignee_of_an_unrelated_task_cannot_read_it(self):
        """The rule is 'assignee of THIS request's task', not 'has any task'."""
        pr = self._raise_request()
        OmniTask.objects.create(
            assigner=self.cfo, assignee=self.stranger, title='Something else',
            source='payment_request', status=OmniTask.Status.PENDING,
        )
        self.client.force_authenticate(self.stranger)
        r = self.client.get(reverse('v1-payment-request-detail', args=[pr.id]))
        self.assertEqual(r.status_code, 403, r.content)
