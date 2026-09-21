"""taskboard/test_payment_email.py — payment authorisations must EMAIL the approver.

CFO 2026-07-29: four payment authorisations landed in the CFO's Omni inbox that
morning (06:53, 08:00, 08:00, 08:01) and no email went out for any of them.
Payment authorisation was the only approval workflow that created its OmniTask
directly, so it never went through the assign-email helper — the money queue was
silent while every other module mailed. Both legs must now notify:

  stage 1  raise      -> the nominated finance approver is emailed
  stage 2  sign-off   -> the CFO is emailed

And the email must NOT carry the bank account number or the liquidity block —
those stay inside Omni (the mail guard cannot un-send a mailbox).

Run in CI (needs a DB): manage.py test taskboard.test_payment_email
"""
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest
from taskboard.test_helpers import window_always_open


@window_always_open
class PaymentAuthorisationEmailTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw',
            first_name='Prathap', last_name='Ganesharajah')
        self.pako = User.objects.create_user(
            'pkago', email='pkago@alphadirect.co.bw', first_name='Pako', last_name='Kago')
        self.kago = User.objects.create_user(
            'ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw',
            first_name='Kago', last_name='Tshutlhedi')
        self.clerk = User.objects.create_user(
            'lthebe', email='lthebe@alphadirect.co.bw', first_name='Laone', last_name='Thebe')
        self.list_url = reverse('v1-payment-requests')

    def _raise(self, approver=None, **extra):
        self.client.force_authenticate(self.clerk)
        body = {
            'subject': 'Claims payable batch',
            # The category is no longer guessed from the description
            # (CFO 2026-07-29) — every request must say what it is. These tests
            # are about routing and email, so use the ungated 'other'.
            'category': PaymentRequest.Category.OTHER,
            'payee': 'Gaborone Panel Beaters',
            'line_items': [{'description': 'Vendor A', 'amount': '1000.00'}],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            # These tests are about routing and email, not the bank-detail gate.
            'account_name': 'Gaborone Panel Beaters', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
            **extra,
        }
        if approver is not None:
            body['approver_id'] = str(approver.id)
        r = self.client.post(self.list_url, body, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()['id']

    # ── stage 1: the finance approver is told ───────────────────────────────
    def test_raising_emails_the_finance_approver(self):
        mail.outbox = []
        self._raise(approver=self.kago)
        self.assertEqual(len(mail.outbox), 1, [m.subject for m in mail.outbox])
        m = mail.outbox[0]
        self.assertEqual(m.to, ['ktshutlhedi@alphadirect.co.bw'])
        self.assertIn('payment authorisation', m.subject.lower())
        # the person who raised it and what is being paid are both in the mail
        blob = (m.body or '') + ''.join(a[0] for a in m.alternatives)
        self.assertIn('Laone Thebe', blob)
        self.assertIn('Gaborone Panel Beaters', blob)
        self.assertIn('1,000.00', blob)
        self.assertIn('/payment-requests', blob)

    def test_email_never_carries_the_bank_account_number(self):
        mail.outbox = []
        self._raise(approver=self.kago,
                    account_name='ADIC Operating', bank_name='FNB Botswana',
                    account_number='62912345678', opening_balance='500000.00')
        blob = (mail.outbox[0].body or '') + ''.join(a[0] for a in mail.outbox[0].alternatives)
        self.assertNotIn('62912345678', blob)
        # …while the task inside Omni still holds the full pack
        pr = PaymentRequest.objects.get()
        self.assertIn('62912345678', pr.task.body)

    def test_funds_already_moved_is_flagged_in_the_email(self):
        mail.outbox = []
        self._raise(approver=self.kago, funds_already_moved=True)
        blob = ''.join(a[0] for a in mail.outbox[0].alternatives)
        self.assertIn('BEFORE authorisation', blob)

    def test_self_raised_request_is_not_mailed_back_to_the_raiser(self):
        # Pako raises it; it routes to another approver, so Pako is not the
        # assignee. Nobody is ever emailed a task they gave themselves.
        self.client.force_authenticate(self.pako)
        mail.outbox = []
        r = self.client.post(self.list_url, {
            'subject': 'Reinsurance instalment',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Q3', 'amount': '2500.00'}],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            'account_name': 'Munich Re', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        recipients = [addr for m in mail.outbox for addr in m.to]
        self.assertNotIn('pkago@alphadirect.co.bw', recipients)

    # ── stage 2: the CFO is told once finance signs off ─────────────────────
    def test_finance_signoff_emails_the_cfo(self):
        pr_id = self._raise(approver=self.kago)
        mail.outbox = []
        self.client.force_authenticate(self.kago)
        r = self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(mail.outbox), 1, [m.subject for m in mail.outbox])
        m = mail.outbox[0]
        self.assertEqual(m.to, ['pganesharajah@alphadirect.co.bw'])
        blob = (m.body or '') + ''.join(a[0] for a in m.alternatives)
        self.assertIn('Kago Tshutlhedi', blob)      # who signed it off
        self.assertIn('Gaborone Panel Beaters', blob)
        self.assertIn('1,000.00', blob)

    def test_rejection_does_not_email_the_cfo(self):
        pr_id = self._raise(approver=self.kago)
        mail.outbox = []
        self.client.force_authenticate(self.kago)
        self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                         {'decision': 'reject', 'notes': 'Missing invoices.'}, format='json')
        recipients = [addr for m in mail.outbox for addr in m.to]
        self.assertNotIn('pganesharajah@alphadirect.co.bw', recipients)

    # ── the mail must never be able to block the money workflow ────────────
    def test_a_broken_mailer_still_lets_the_request_through(self):
        from unittest import mock
        with mock.patch('core.notifications.send_html_with_cfo_cc',
                        side_effect=RuntimeError('SMTP down')):
            pr_id = self._raise(approver=self.kago)
        self.assertEqual(PaymentRequest.objects.get(id=pr_id).status,
                         PaymentRequest.Status.PENDING_FINANCE)
