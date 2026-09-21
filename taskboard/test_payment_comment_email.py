"""taskboard/test_payment_comment_email.py — a task comment must reach the loader.

CFO 2026-07-29, on a live payment authorisation task: "I have authorized the
known payments, but I don't know who Johannes is. Please email me the supporting
documents." Nobody was notified — the comment sat in Omni, so the request for
documents reached no one.

The person who can actually produce those documents is whoever RAISED the
request, and on a CFO-stage task that person is NOT the assigner: a clerk loads
it, finance signs it off, and only finance appears on the CFO's task. So the
comment must reach the raiser as well as both sides of the task.

Run in CI (needs a DB): manage.py test taskboard.test_payment_comment_email
"""
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from taskboard.models import PaymentRequest
from taskboard.test_helpers import seed_adic, window_always_open


@window_always_open
class PaymentCommentEmailTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', email='pganesharajah@alphadirect.co.bw',
            first_name='Prathap', last_name='Ganesharajah', is_superuser=True)
        self.kago = User.objects.create_user(
            'ktshutlhedi', email='ktshutlhedi@alphadirect.co.bw',
            first_name='Kago', last_name='Tshutlhedi')
        self.loader = User.objects.create_user(
            'bontle.tendani', email='btendani@alphadirect.co.bw',
            first_name='Bontle', last_name='Tendani')
        self.list_url = reverse('v1-payment-requests')
        # Raising a CLAIMS pack needs the real ADIC row — the claims-are-ADIC
        # control requires a positive match, never a default.
        seed_adic()

    def _pending_cfo(self):
        """The real shape: the loader raises it, Kago signs it off, it lands on
        the CFO. The loader is NOT on the CFO's task."""
        self.client.force_authenticate(self.loader)
        pr_id = self.client.post(self.list_url, {
            'subject': 'Claims payable batch',
            # Every request must now say what it is (CFO 2026-07-29). This one
            # settles a claim straight to the policyholder, so it is a claims
            # payment to the client — no third-party invoice to age, ungated.
            'category': PaymentRequest.Category.CLAIM,
            'claim_payee_type': PaymentRequest.ClaimPayeeType.CLIENT,
            'payee': 'Thapelo Johannes Selemela',
            'approver_id': str(self.kago.id),
            # A claims pack must name the claim on every line (CFO 2026-07-29).
            'line_items': [{'description': 'Claim 10030299', 'amount': '55800.60',
                            'claim_number': 'G2026010299'}],
            # Bank details mandatory now (PAY-BANK-02); FNB needs no branch code.
            'account_name': 'Thapelo Johannes Selemela', 'bank_name': 'FNB',
            'account_number': '62012345678', 'new_payee_confirmed': True,
        }, format='json').json()['id']
        self.client.force_authenticate(self.kago)
        self.client.post(reverse('v1-payment-request-decide', args=[pr_id]),
                         {'decision': 'approve'}, format='json')
        pr = PaymentRequest.objects.get(id=pr_id)
        self.assertEqual(pr.task.assignee_id, self.cfo.id)
        self.assertEqual(pr.task.assigner_id, self.kago.id)     # NOT the loader
        return pr

    def _comment(self, task, user, text, **extra):
        self.client.force_authenticate(user)
        mail.outbox = []
        r = self.client.patch(reverse('v1-omni-task-detail', args=[task.id]),
                              {'comment': text, **extra}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        return r

    @staticmethod
    def _recipients():
        return {a for m in mail.outbox for a in m.to}

    # ── the reported case ───────────────────────────────────────────────────
    def test_cfo_comment_reaches_the_loader_and_the_signer(self):
        pr = self._pending_cfo()
        self._comment(pr.task, self.cfo,
                      "I have authorized the known payments, but I don't know who "
                      "Johannes is. Please email me the supporting documents.")
        self.assertEqual(len(mail.outbox), 1, [m.subject for m in mail.outbox])
        self.assertEqual(self._recipients(),
                         {'btendani@alphadirect.co.bw',       # who loaded it
                          'ktshutlhedi@alphadirect.co.bw'})   # who signed it off
        m = mail.outbox[0]
        blob = (m.body or '') + ''.join(a[0] for a in m.alternatives)
        self.assertIn("don't know who Johannes is", blob)
        self.assertIn('Prathap Ganesharajah', blob)
        self.assertIn('/tasks', blob)

    def test_the_author_is_never_emailed_his_own_comment(self):
        pr = self._pending_cfo()
        self._comment(pr.task, self.cfo, 'Please send the supporting documents.')
        self.assertNotIn('pganesharajah@alphadirect.co.bw', self._recipients())

    def test_the_loader_replying_reaches_the_cfo(self):
        # It works both ways: the loader answers, the CFO is told.
        pr = self._pending_cfo()
        # the loader is neither assigner nor assignee of the CFO task, so give
        # the reply through the task they DO own — the finance sign-off task.
        fin_task = OmniTask.objects.filter(source='payment_request',
                                           assignee=self.kago).first()
        self._comment(fin_task, self.loader, 'Invoice and claim form attached in Omni.')
        self.assertIn('ktshutlhedi@alphadirect.co.bw', self._recipients())

    def test_a_status_change_is_named_in_the_email(self):
        pr = self._pending_cfo()
        self._comment(pr.task, self.cfo, 'Part-authorised — Johannes is on hold.',
                      status=OmniTask.Status.PARTIAL)
        blob = ''.join(a[0] for a in mail.outbox[0].alternatives)
        self.assertIn('Partial', blob)

    # ── scope + safety ──────────────────────────────────────────────────────
    def test_an_ordinary_task_comment_is_not_mailed(self):
        # Scoped to payment authorisations for now — an ordinary hand-typed task
        # keeps its existing quiet behaviour until that is asked for.
        t = OmniTask.objects.create(
            assigner=self.kago, assignee=self.cfo, title='Send me the lease file',
            status=OmniTask.Status.PENDING, source='manual')
        self._comment(t, self.cfo, 'Will look at this tomorrow.')
        self.assertEqual(mail.outbox, [])

    def test_a_broken_mailer_still_saves_the_comment(self):
        from unittest import mock
        pr = self._pending_cfo()
        self.client.force_authenticate(self.cfo)
        with mock.patch('core.notifications.send_html_with_cfo_cc',
                        side_effect=RuntimeError('SMTP down')):
            r = self.client.patch(reverse('v1-omni-task-detail', args=[pr.task.id]),
                                  {'comment': 'Please send the documents.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(pr.task.comments.filter(body__startswith='Please send').exists())
