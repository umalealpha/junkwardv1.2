"""The CFO's one-tap payment decisions, now carried by the daily payments digest.

WHY THIS FILE EXISTS (CFO 2026-09-13). The one-tap Approve link used to ride on
the morning brief. He then asked for that brief to be about PEOPLE - leave,
staff loans, incentives, commissions - so it now filters every payment out of
itself, and a tested, working gate was left with nothing to send it. The links
moved to the email that was already about payments. These tests hold the two
things that can go wrong with that move:

  1. A rendered button that the server then refuses. That exact bug was fixed
     earlier today: the brief rendered with is_decidable_task(task, actor=ACTOR)
     and the click re-checked WITHOUT the actor, fell back to the CEO handle and
     turned the CFO away from his own payment. It failed closed, so nothing
     unsafe happened - the feature was simply dead, and the tests missed it
     because they tested the predicate rather than the HTTP path. So the test
     below takes the URL OUT OF THE RENDERED EMAIL and feeds it to the real
     view. Nothing here calls is_decidable_task directly, on purpose.

  2. A personal link delivered somewhere shared. A one-tap link is signed for
     one person and anyone holding it can decide as that person, so the digest
     must never cc the shared excoboard@ mailbox, never bcc, and must drop the
     buttons entirely unless it is going to exactly one corporate mailbox.

Nothing in here moves money. Approving records an authorisation in Omni; the
payment is still loaded and released at the bank by two people.

Run: manage.py test taskboard.test_payment_digest_one_tap
"""
from __future__ import annotations

import re
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from core.models import OmniTask
from taskboard.management.commands import payment_daily_digest as cmd
from taskboard.models import PaymentRequest

CFO = 'pganesharajah@alphadirect.co.bw'


class _Base(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'pganesharajah', CFO, 'x',
            first_name='Prathap', last_name='Ganesharajah', is_staff=True)
        # The approver may not be the person who raised it.
        self.loader = User.objects.create_user(
            'kago', 'kago@alphadirect.co.bw', 'x', first_name='Kago')
        self.task = OmniTask.objects.create(
            title='Pay Jere Attorneys', source='payment_request',
            assignee=self.cfo, assigner=self.loader,
            status=OmniTask.Status.PENDING)
        self.pr = PaymentRequest.objects.create(
            ref='PR-2026-0912', status=PaymentRequest.Status.PENDING_CFO,
            currency='BWP', total=Decimal('13662.67'), entity='ADIC',
            payee='Jere Attorneys', inputter='Kago', created_by=self.loader,
            task=self.task)

    def _send(self, to=CFO):
        mail.outbox = []
        call_command('payment_daily_digest', '--to', to,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0]

    @staticmethod
    def _approve_url(html):
        """The Approve link exactly as the CFO's mail client would follow it."""
        m = re.search(r'href="([^"]*/api/ceo-monitor/decide/\?t=[^"]+)"', html)
        return m.group(1) if m else None

    @staticmethod
    def _path(url):
        return url.split('omni.alphadirect.co.bw', 1)[-1]


class TheCfoCanTapApproveOutOfTheDigest(_Base):
    """The whole point: render it, then click the rendered thing."""

    def test_the_digest_renders_an_approve_button_for_the_cfo(self):
        html = self._send().alternatives[0][0]
        self.assertIn('>Approve<', html,
                      'the payments digest carried no Approve button - the '
                      'one-tap gate is live with nothing to send it again')
        self.assertIsNotNone(self._approve_url(html))

    def test_the_rendered_link_is_accepted_by_the_real_view(self):
        """THE REGRESSION. Straight through HTTP, never the predicate."""
        url = self._approve_url(self._send().alternatives[0][0])
        r = self.client.get(self._path(url))
        self.assertEqual(
            r.status_code, 200,
            'the CFO tapped Approve in his payments digest and the server '
            'refused - the actor is not travelling from the render to the click')
        self.assertNotIn(b'has to be done in Omni', r.content)

    def test_the_confirm_post_actually_closes_the_task(self):
        """A confirm page that confirms nothing is the same dead feature."""
        url = self._approve_url(self._send().alternatives[0][0])
        token = url.split('?t=', 1)[1]
        r = self.client.post('/api/ceo-monitor/decide/', {'t': token})
        self.assertEqual(r.status_code, 200, r.content[:400])
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)

    def test_a_get_on_the_link_changes_nothing_by_itself(self):
        """Mail scanners pre-fetch links. A scan must not approve a payment."""
        url = self._approve_url(self._send().alternatives[0][0])
        self.client.get(self._path(url))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)


class ThePersonalLinkGoesToOnePersonOnly(_Base):
    """A one-tap link is only as personal as its delivery."""

    def test_the_digest_copies_nobody(self):
        msg = self._send()
        self.assertEqual(msg.to, [CFO])
        self.assertEqual(
            msg.cc, [],
            'the payments digest copied somebody - a personal Approve link '
            'must never land in a mailbox several people read')
        self.assertEqual(msg.bcc, [])
        self.assertNotIn('excoboard', ' '.join(msg.to + msg.cc + msg.bcc))
        self.assertNotIn('excoboard', msg.alternatives[0][0])

    def test_two_recipients_means_no_buttons_at_all(self):
        html = self._send(
            to=CFO + ',excoboard@alphadirect.co.bw').alternatives[0][0]
        self.assertIsNone(self._approve_url(html),
                          'a personal Approve link went out to two mailboxes')

    def test_a_shared_mailbox_gets_the_digest_but_no_buttons(self):
        html = self._send(to='excoboard@alphadirect.co.bw').alternatives[0][0]
        self.assertIn('13,662.67', html)          # the digest still arrives
        self.assertIsNone(self._approve_url(html))

    def test_the_cfo_handle_at_another_domain_gets_no_buttons(self):
        """The click resolves the actor at alphadirect.co.bw. Matching only the
        part before the @ would post a CFO-signed link to a personal inbox."""
        html = self._send(to='pganesharajah@gmail.com').alternatives[0][0]
        self.assertIsNone(self._approve_url(html))

    def test_anyone_else_gets_no_buttons(self):
        html = self._send(to='kago@alphadirect.co.bw').alternatives[0][0]
        self.assertIsNone(self._approve_url(html))


class TheGateStillApplies(_Base):
    """Moving where the link is SENT from must not move where it is CHECKED."""

    def test_a_closed_request_carries_no_button(self):
        self.task.status = OmniTask.Status.DONE
        self.task.save(update_fields=['status'])
        html = self._send().alternatives[0][0]
        self.assertIsNone(self._approve_url(html))

    def test_a_row_with_no_task_behind_it_says_so_instead(self):
        self.pr.task = None
        self.pr.save(update_fields=['task'])
        html = self._send().alternatives[0][0]
        self.assertIsNone(self._approve_url(html))
        self.assertIn('In Omni', html)

    def test_rows_not_waiting_on_him_never_get_a_button(self):
        self.pr.status = PaymentRequest.Status.PENDING_FINANCE
        self.pr.save(update_fields=['status'])
        html = self._send().alternatives[0][0]
        self.assertIsNone(self._approve_url(html))


class TheEmailReadsRight(_Base):
    def test_it_never_says_the_money_moves(self):
        """Omni records an authorisation. Money leaves at FNB, under two people."""
        html = self._send().alternatives[0][0].lower()
        for phrase in ('release the payment', 'releases the payment',
                       'pay now', 'send the money', 'transfer the money',
                       'money will be paid', 'pays the supplier'):
            self.assertNotIn(phrase, html, 'the digest says it ' + repr(phrase))
        self.assertIn('nothing is paid from this email', html)

    def test_it_carries_the_internal_do_not_reply_banner(self):
        """Internal Omni email (CFO 2026-08-07): a reply here is not tracked."""
        html = self._send().alternatives[0][0]
        self.assertIn('data-omni-noreply', html)
        self.assertIn('Please do not reply to this email.', html)


class ActorSelectionUnit(TestCase):
    """The narrow rule, stated once."""

    def test_only_the_cfos_own_corporate_mailbox_earns_an_actor(self):
        self.assertEqual(cmd.approve_actor_for([CFO]), 'pganesharajah')
        self.assertIsNone(cmd.approve_actor_for(['pganesharajah@gmail.com']))
        self.assertIsNone(cmd.approve_actor_for(['excoboard@alphadirect.co.bw']))
        self.assertIsNone(cmd.approve_actor_for([CFO, 'x@alphadirect.co.bw']))
        self.assertIsNone(cmd.approve_actor_for([]))

    def test_a_display_name_wrapper_is_read_through(self):
        self.assertEqual(
            cmd.approve_actor_for(['Prathap Ganesharajah <' + CFO + '>']),
            'pganesharajah')
