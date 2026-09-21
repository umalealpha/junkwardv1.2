"""taskboard/test_payment_line_approval.py — per-line CFO authorisation.

CFO 2026-08-31: a batch pack (e.g. 10 claims to 10 payees) must be authorisable
line by line — "I approve 9 of them and wait for 1, and because of that I am
unable to close the whole batch." The CFO now approves / holds / rejects
individual lines; a held line keeps the request open on its own; the request
closes (PAID / CANCELLED) once every line is approved or rejected.

Money never moves here — the FNB batch was already loaded at finance sign-off.
This records the CFO's decision so his Omni queue matches what he ticks at FNB.

Run in CI (needs a DB): manage.py test taskboard.test_payment_line_approval
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open


@window_always_open
class PaymentLineApprovalTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw')
        self.pako = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        self.clerk = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _pending_cfo_request(self):
        """Create a 3-line batch and push it to PENDING_CFO (clerk raises,
        finance approves), returning its id."""
        self.client.force_authenticate(self.clerk)
        r = self.client.post(self.list_url, {
            'subject': 'Claims payable batch',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [
                {'description': 'Payee A', 'amount': '1000.00'},
                {'description': 'Payee B', 'amount': '2000.00'},
                {'description': 'Payee C', 'amount': '3000.00'},
            ],
            'account_name': 'Batch', 'bank_name': 'FNB', 'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        pr_id = r.json()['id']
        # finance sign-off → PENDING_CFO
        self.client.force_authenticate(self.pako)
        d = self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(d.status_code, 200, d.content)
        self.assertEqual(PaymentRequest.objects.get(id=pr_id).status,
                         PaymentRequest.Status.PENDING_CFO)
        return pr_id

    def _lines_url(self, pr_id):
        return reverse('v1-payment-request-decide-lines', args=[pr_id])

    # ── the headline behaviour: approve 2, hold 1, request stays open ────────
    def test_approve_two_hold_one_keeps_request_open(self):
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.cfo)

        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [0, 1], 'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['closed'])
        self.assertEqual(r.json()['line_progress']['approved'], 2)

        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [2], 'action': 'hold', 'note': 'checking this one'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['closed'])

        pr = PaymentRequest.objects.get(id=pr_id)
        # A held line keeps the whole request open — the CFO can wait on line 3.
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)
        self.assertEqual(pr.line_progress(), {'total': 3, 'approved': 2, 'held': 1,
                                              'rejected': 0, 'pending': 0})
        self.assertNotIn(pr.task.status, (OmniTask.Status.DONE, OmniTask.Status.CANCELLED))

    # ── deciding the held line closes the request as PAID ────────────────────
    def test_deciding_last_line_closes_as_paid(self):
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.cfo)
        self.client.post(self._lines_url(pr_id),
                         {'line_indexes': [0, 1], 'action': 'approve'}, format='json')
        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [2], 'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['closed'])
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.status, PaymentRequest.Status.PAID)
        self.assertEqual(pr.task.status, OmniTask.Status.DONE)

    # ── rejecting every line closes the request as CANCELLED, not PAID ───────
    def test_rejecting_all_lines_cancels(self):
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [0, 1, 2], 'action': 'reject', 'note': 'not happy'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['closed'])
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.status, PaymentRequest.Status.CANCELLED)
        self.assertEqual(pr.task.status, OmniTask.Status.CANCELLED)

    # ── only the CFO may decide lines ────────────────────────────────────────
    def test_non_cfo_cannot_decide_lines(self):
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.clerk)
        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [0], 'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    # ── reject with no reason is refused ─────────────────────────────────────
    def test_reject_requires_reason(self):
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self._lines_url(pr_id),
                             {'line_indexes': [0], 'action': 'reject'}, format='json')
        self.assertEqual(r.status_code, 400, r.content)

    # ── a duplicate at close must 409 AND roll back the line decisions ───────
    # Fable 5 2026-08-31: catching a ValidationError INSIDE transaction.atomic()
    # and returning a 409 normally still COMMITS the block — the lines would be
    # persisted as 'approved' on a request the duplicate control refused. Fix:
    # transaction.set_rollback(True). This test fails without that fix.
    def test_dup_block_at_close_rolls_back_line_decisions(self):
        # Request A: one line carrying a Graphite claim token in its description.
        self.client.force_authenticate(self.clerk)
        r = self.client.post(self.list_url, {
            'subject': 'Bonu claim', 'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Payee X G2026009999', 'amount': '1000.00'}],
            'account_name': 'X', 'bank_name': 'FNB', 'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        a_id = r.json()['id']
        self.client.force_authenticate(self.pako)
        self.assertEqual(self.client.post(
            reverse('v1-payment-request-decide', args=[a_id]),
            {'decision': 'approve'}, format='json').status_code, 200)
        # A SEPARATE request is already PAID carrying the same line — created
        # directly so it exists only AFTER A reached PENDING_CFO (the sign-off
        # dup-check had nothing to catch at the time A was signed off).
        PaymentRequest.objects.create(
            ref='PAY/ADIC/TEST/PAID/0001', entity='ADIC',
            category=PaymentRequest.Category.OTHER, currency='BWP',
            subject='already paid', status=PaymentRequest.Status.PAID,
            line_items=[{'description': 'G2026009999', 'amount': '1000.00'}],
            total='1000.00')
        # CFO approves A's line → close runs the dup check → hard clash → 409.
        self.client.force_authenticate(self.cfo)
        resp = self.client.post(self._lines_url(a_id),
                                {'line_indexes': [0], 'action': 'approve'}, format='json')
        self.assertEqual(resp.status_code, 409, resp.content)
        # The line decision must have rolled back — no line_status persisted, and
        # the request stays PENDING_CFO (not silently closed).
        a = PaymentRequest.objects.get(id=a_id)
        self.assertEqual(a.status, PaymentRequest.Status.PENDING_CFO)
        self.assertEqual(PaymentRequest.line_state(a.line_items[0]), '')

    # ── a held line can't be swept into PAID by the whole-request path ───────
    def test_whole_request_paid_blocked_when_a_line_is_held(self):
        from django.core.exceptions import ValidationError
        from taskboard.services import complete_task
        pr_id = self._pending_cfo_request()
        self.client.force_authenticate(self.cfo)
        self.client.post(self._lines_url(pr_id),
                         {'line_indexes': [2], 'action': 'hold', 'note': 'checking'},
                         format='json')
        pr = PaymentRequest.objects.get(id=pr_id)
        with self.assertRaises(ValidationError):
            complete_task(pr.task, self.cfo, 'pay it all', 0)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)  # not swept to PAID
