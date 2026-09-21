"""taskboard/test_payment_two_stage.py — two-stage payment authorisation.

CFO 2026-07-23: a payment request must be signed off by a finance approver
(Pako / Kago / Legakwa) BEFORE it reaches the CFO's view. Only once one of
them approves does it become a CFO task.

Run in CI (needs a DB): manage.py test taskboard.test_payment_two_stage
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open


@window_always_open
class PaymentTwoStageTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.pako = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        self.kago = User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        self.lega = User.objects.create_user('lntabeni', email='lntabeni@alphadirect.co.bw')
        self.clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _create(self, user):
        self.client.force_authenticate(user)
        return self.client.post(self.list_url, {
            'subject': 'Claims payable batch',
            # The category is no longer guessed from the description
            # (CFO 2026-07-29). These tests are about the two-stage routing, so
            # use the ungated 'other'.
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Vendor A', 'amount': '1000.00'}],
            # Bank details are mandatory on a request now (PAY-BANK-02, CFO
            # 2026-08-20); FNB needs no branch code. These tests are about the
            # two-stage routing, not the bank-detail gate.
            'account_name': 'Vendor A', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json')

    def _decide_url(self, pr_id):
        return reverse('v1-payment-request-decide', args=[pr_id])

    # ── stage 1: routing ────────────────────────────────────────────────────
    def test_new_request_goes_to_finance_not_cfo(self):
        r = self._create(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(id=r.json()['id'])
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        # the task is assigned to a finance approver, NOT the CFO
        self.assertIn(pr.task.assignee_id, {self.pako.id, self.kago.id, self.lega.id})
        self.assertNotEqual(pr.task.assignee_id, self.cfo.id)

    def test_cfo_does_not_see_pending_finance(self):
        self._create(self.clerk)
        self.client.force_authenticate(self.cfo)
        rows = self.client.get(self.list_url).json()['requests']
        self.assertEqual(rows, [])

    def test_finance_approver_sees_queue_with_can_approve(self):
        self._create(self.clerk)
        self.client.force_authenticate(self.pako)
        data = self.client.get(self.list_url).json()
        self.assertTrue(data['is_first_approver'])
        self.assertEqual(len(data['requests']), 1)
        self.assertTrue(data['requests'][0]['can_approve'])

    # ── stage 1 → stage 2: approve routes to CFO ────────────────────────────
    def test_approve_routes_to_cfo(self):
        pr_id = self._create(self.clerk).json()['id']
        old_task = PaymentRequest.objects.get(id=pr_id).task_id
        self.client.force_authenticate(self.kago)
        r = self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)
        self.assertEqual(pr.first_approver_id, self.kago.id)
        self.assertEqual(pr.task.assignee_id, self.cfo.id)      # now a CFO task
        self.assertNotEqual(pr.task_id, old_task)               # a new task, not the finance one
        # the original finance task is closed
        self.assertEqual(OmniTask.objects.get(id=old_task).status, OmniTask.Status.DONE)
        # and now the CFO sees it
        self.client.force_authenticate(self.cfo)
        rows = self.client.get(self.list_url).json()['requests']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'pending_cfo')

    # ── guards ──────────────────────────────────────────────────────────────
    def test_requester_cannot_sign_off_own(self):
        # a finance approver raises the request → cannot then approve it (SoD)
        pr_id = self._create(self.pako).json()['id']
        self.client.force_authenticate(self.pako)
        r = self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(PaymentRequest.objects.get(id=pr_id).status,
                         PaymentRequest.Status.PENDING_FINANCE)

    def test_non_approver_cannot_decide(self):
        pr_id = self._create(self.clerk).json()['id']
        self.client.force_authenticate(self.clerk)   # requester, not a finance approver
        r = self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_cfo_cannot_shortcut_finance(self):
        pr_id = self._create(self.clerk).json()['id']
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        # Refused: the CFO is not a stage-1 finance approver. Since the late-reject
        # leg (CFO 2026-09-01) the refusal reads 409 "not at this stage".
        self.assertIn(r.status_code, (403, 409), r.content)
        self.assertEqual(PaymentRequest.objects.get(id=pr_id).status,
                         PaymentRequest.Status.PENDING_FINANCE)

    def test_reject_requires_reason_and_sets_state(self):
        pr_id = self._create(self.clerk).json()['id']
        self.client.force_authenticate(self.lega)
        # no reason → 400
        self.assertEqual(self.client.post(self._decide_url(pr_id),
                         {'decision': 'reject'}, format='json').status_code, 400)
        # with reason → rejected, no CFO task
        r = self.client.post(self._decide_url(pr_id),
                             {'decision': 'reject', 'notes': 'Missing supporting invoices.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.status, PaymentRequest.Status.REJECTED)
        self.assertEqual(pr.rejected_by_id, self.lega.id)
        self.assertFalse(OmniTask.objects.filter(assignee=self.cfo, source='payment_request').exists())

    def test_cannot_decide_twice(self):
        pr_id = self._create(self.clerk).json()['id']
        self.client.force_authenticate(self.kago)
        self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        # second attempt (no longer pending_finance) → 409
        r = self.client.post(self._decide_url(pr_id), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409)


@window_always_open
class PaymentTerminalStateTests(APITestCase):
    """Terminal states — paid / cleared requests must LEAVE the queue
    (bug ktshutlhedi 2026-07-25: PAY/ADIC/2026/07/18/0001 was paid but stayed
    'pending_cfo' in the queue for ever, with no way to clear it)."""

    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.kago = User.objects.create_user('ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw')
        self.pako = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        self.clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _to_pending_cfo(self):
        """Raise (clerk) → finance sign-off (kago) → PENDING_CFO."""
        self.client.force_authenticate(self.clerk)
        pr_id = self.client.post(self.list_url, {
            'subject': 'Claims payable batch',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Vendor A', 'amount': '1000.00'}],
            # Bank details are mandatory on a request now (PAY-BANK-02, CFO
            # 2026-08-20); FNB needs no branch code. These tests are about the
            # two-stage routing, not the bank-detail gate.
            'account_name': 'Vendor A', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json').json()['id']
        self.client.force_authenticate(self.kago)
        self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                         {'decision': 'approve'}, format='json')
        return PaymentRequest.objects.get(id=pr_id)

    def test_paying_marks_request_paid_and_leaves_queue(self):
        from taskboard.services import complete_task
        pr = self._to_pending_cfo()
        # CFO pays by completing the linked "mark as paid" task.
        complete_task(pr.task, self.cfo, 'Paid via FNB bulk upload today.', 0)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PAID)
        # gone from the active queue…
        self.client.force_authenticate(self.cfo)
        self.assertEqual(self.client.get(self.list_url).json()['requests'], [])
        # …but still visible in history (?all=1) so nothing is lost.
        allrows = self.client.get(self.list_url + '?all=1').json()['requests']
        self.assertEqual([r['status'] for r in allrows], ['paid'])

    def test_cfo_can_clear_pending_cfo_request(self):
        pr = self._to_pending_cfo()
        task_id = pr.task_id
        self.client.force_authenticate(self.cfo)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr.id]),
                             {'notes': 'Already paid outside Omni — duplicate.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.CANCELLED)
        self.assertEqual(OmniTask.objects.get(id=task_id).status, OmniTask.Status.CANCELLED)
        self.assertEqual(self.client.get(self.list_url).json()['requests'], [])

    def test_clear_requires_reason(self):
        pr = self._to_pending_cfo()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr.id]),
                             {'notes': '   '}, format='json')
        self.assertEqual(r.status_code, 400)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_ordinary_staff_cannot_clear(self):
        # The control is NOT open to everyone: a normal member of staff — even
        # the person who raised the request — still gets 403.
        pr = self._to_pending_cfo()
        self.client.force_authenticate(self.clerk)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr.id]),
                             {'notes': 'nope'}, format='json')
        self.assertEqual(r.status_code, 403)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_finance_approver_cannot_clear_own_signoff(self):
        # Segregation of duties (CFO 2026-07-26): kago signed this off at stage
        # 1, so she may not also make it vanish before the CFO sees it.
        pr = self._to_pending_cfo()
        self.assertEqual(pr.first_approver_id, self.kago.id)
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr.id]),
                             {'notes': 'paid outside Omni'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_other_finance_approver_can_clear(self):
        # A finance approver who did NOT sign this one off may clear it
        # (CFO decision 2026-07-26) — that is the widened right.
        pr = self._to_pending_cfo()
        task_id = pr.task_id
        self.client.force_authenticate(self.pako)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr.id]),
                             {'notes': 'Duplicate of PAY/ADIC/2026/07/18/0001.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.CANCELLED)
        self.assertEqual(OmniTask.objects.get(id=task_id).status, OmniTask.Status.CANCELLED)

    def test_can_clear_flag_matches_the_rule(self):
        # The button the approver sees must follow the same rule as the
        # endpoint. This is the real shape of the bug kago reported: she raises
        # a request, pako signs it off, it then sits in her queue as PENDING_CFO
        # with no way out. She did not sign it, so she may clear it.
        self.client.force_authenticate(self.kago)
        pr_id = self.client.post(self.list_url, {
            'subject': 'Reinsurance instalment',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Q3 instalment', 'amount': '2500.00'}],
            'account_name': 'Munich Re', 'bank_name': 'FNB',   # PAY-BANK-02
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json').json()['id']
        self.client.force_authenticate(self.pako)
        self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                         {'decision': 'approve'}, format='json')

        self.client.force_authenticate(self.kago)
        row = next(r for r in self.client.get(self.list_url).json()['requests']
                   if r['id'] == pr_id)
        self.assertTrue(row['can_clear'])
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr_id]),
                             {'notes': 'Settled by direct debit.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)

        # …and the clerk who is not in finance never gets the button.
        self.client.force_authenticate(self.clerk)
        rows = self.client.get(self.list_url + '?all=1').json()['requests']
        self.assertTrue(all(not x['can_clear'] for x in rows))

    def test_cannot_clear_before_finance_signoff(self):
        # a request still awaiting finance is not the CFO's to clear (409).
        self.client.force_authenticate(self.clerk)
        pr_id = self.client.post(self.list_url, {
            'subject': 'Batch',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'X', 'amount': '5.00'}],
            'account_name': 'Vendor X', 'bank_name': 'FNB',   # PAY-BANK-02
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json').json()['id']
        self.client.force_authenticate(self.cfo)
        r = self.client.post(reverse('v1-payment-request-clear', args=[pr_id]),
                             {'notes': 'x'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_completing_finance_task_does_not_mark_paid(self):
        # Completing the stage-1 finance sign-off task (via the generic task
        # screen, not decide()) must NEVER mark a request paid — payment only
        # happens at the CFO stage.
        from taskboard.services import complete_task
        self.client.force_authenticate(self.clerk)
        pr_id = self.client.post(self.list_url, {
            'subject': 'Batch',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'X', 'amount': '5.00'}],
            'account_name': 'Vendor X', 'bank_name': 'FNB',   # PAY-BANK-02
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json').json()['id']
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        complete_task(pr.task, pr.task.assignee, 'Marked done from my task list.', 0)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
