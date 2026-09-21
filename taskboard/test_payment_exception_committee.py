"""taskboard/test_payment_exception_committee.py — the exception committee.

CFO 2026-09-02. A fraud-risk exception (a changed bank account) NEVER blocks the
raiser — the request is entered and a committee of THREE of the six-member pool
decides it. Rules that must never regress:

  - three approvals release the payment into the normal flow (pending_finance);
  - any reject among the three rejects the payment;
  - the raiser cannot sign their own exception (segregation of duties);
  - a non-committee user cannot sign;
  - a member cannot sign the same exception twice;
  - approving a changed bank account needs the call-back recorded (not a tick);
  - the CFO's clear is records-only — it NEVER changes the payment's status.

Run: manage.py test taskboard.test_payment_exception_committee
"""
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest


def _member(username, email):
    return User.objects.create_user(username, email=email, password='x')


class ExceptionCommitteeTests(TestCase):
    def setUp(self):
        self.raiser = _member('btendani', 'btendani@alphadirect.co.bw')
        self.pako = _member('pako', 'pkago@alphadirect.co.bw')
        self.kago = _member('kago', 'ktshutlhedi@alphadirect.co.bw')
        self.oprah = _member('oprah', 'omogomotsi@alphadirect.co.bw')
        self.outsider = _member('outsider', 'nobody@alphadirect.co.bw')
        self.cfo = User.objects.create_superuser(
            'cfo', email='pganesharajah@alphadirect.co.bw', password='x')

    def _exception(self):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{uuid4().hex[:8]}',
            entity='Alpha Direct Insurance Company',
            subject='Changed bank account', payee='ABC Traders',
            currency='BWP', total='4500.00',
            line_items=[{'description': 'x', 'amount': '4500.00'}],
            status=PaymentRequest.Status.EXCEPTION,
            exception_control='PAY-BANK-01',
            exception_reason='The account changed.',
            created_by=self.raiser)

    def _sign(self, user, pk, **over):
        self.client.force_login(user)
        body = {'decision': 'approve',
                'called_who': 'Mr Traders', 'called_number': '+267 300 0000'}
        body.update(over)
        return self.client.post(
            reverse('v1-payment-exception-signoff', args=[pk]),
            body, content_type='application/json')

    def test_three_approvals_release_to_pending_finance(self):
        pr = self._exception()
        self.assertEqual(self._sign(self.pako, pr.id).status_code, 200)
        self.assertEqual(self._sign(self.kago, pr.id).status_code, 200)
        r = self._sign(self.oprah, pr.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['decided'])
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        self.assertEqual(pr.exception_decision, 'approve')

    def test_a_reject_among_three_rejects_the_payment(self):
        pr = self._exception()
        self._sign(self.pako, pr.id)
        self._sign(self.kago, pr.id)
        self._sign(self.oprah, pr.id, decision='reject')
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.REJECTED)
        self.assertEqual(pr.exception_decision, 'reject')

    def test_raiser_cannot_sign_their_own_exception(self):
        # Make the raiser a committee member so they pass membership and hit the
        # raiser check — the segregation rule, not the membership rule.
        self.raiser.email = 'tchimidza@alphadirect.co.bw'
        self.raiser.save()
        pr = self._exception()
        self.assertEqual(self._sign(self.raiser, pr.id).status_code, 403)

    def test_non_committee_user_cannot_sign(self):
        pr = self._exception()
        self.assertEqual(self._sign(self.outsider, pr.id).status_code, 403)

    def test_a_member_cannot_sign_twice(self):
        pr = self._exception()
        self.assertEqual(self._sign(self.pako, pr.id).status_code, 200)
        self.assertEqual(self._sign(self.pako, pr.id).status_code, 409)

    def test_approving_bank_change_requires_the_callback(self):
        pr = self._exception()
        r = self._sign(self.pako, pr.id, called_who='', called_number='')
        self.assertEqual(r.status_code, 400, r.content)

    def test_cfo_clear_is_records_only_and_cfo_gated(self):
        pr = self._exception()
        self._sign(self.pako, pr.id)
        self._sign(self.kago, pr.id)
        self._sign(self.oprah, pr.id)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)
        clear_url = reverse('v1-payment-exception-clear', args=[pr.id])
        # A committee member is not the CFO.
        self.client.force_login(self.pako)
        self.assertEqual(self.client.post(clear_url).status_code, 403)
        # The CFO clears it — records only, the status is UNCHANGED.
        self.client.force_login(self.cfo)
        r = self.client.post(clear_url)
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertIsNotNone(pr.exception_cleared_at)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_FINANCE)

    def test_open_exceptions_board_lists_it(self):
        pr = self._exception()
        self.client.force_login(self.pako)
        r = self.client.get(reverse('v1-payment-exceptions'))
        self.assertEqual(r.status_code, 200, r.content)
        refs = [row['ref'] for row in r.json()['open']]
        self.assertIn(pr.ref, refs)
