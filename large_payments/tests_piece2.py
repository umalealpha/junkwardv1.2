"""Piece 2 — the CEO's authorisation email and its no-login buttons.

The most important test in this file is
test_the_ceo_is_actually_on_his_own_email. `aiyer@alphadirect.co.bw` sits in
core.notifications._NEVER_CC and is stripped from TO and CC unless the sender is
told otherwise. On 2026-08-10 the CFO asked for Arun on an IT ticket, the send
returned SUCCESS, and Arun was not on the message. Piece 2 is an email TO Arun,
so that trap would have made the entire feature deliver nothing while reporting
that it worked.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import OmniTask

from large_payments.models import LargePaymentLine, LargePaymentRequest
from large_payments.tests import _payment

User = get_user_model()

CEO = 'aiyer@alphadirect.co.bw'


def _mk_user(username, email, **kw):
    return User.objects.create_user(username, email, 'x', **kw)


class Piece2Base(TestCase):
    def setUp(self):
        self.cfo = _mk_user('pganesharajah', 'pganesharajah@alphadirect.co.bw',
                            is_superuser=True, is_staff=True)
        self.finance = _mk_user('lntabeni', 'lntabeni@alphadirect.co.bw')
        self.ceo = _mk_user('arun.iyer', CEO)
        self.wangu = _mk_user('wmoses', 'wmoses@alphadirect.co.bw')
        self.client = APIClient()

        self.req = LargePaymentRequest.objects.create(
            ref='LPR/P2/0001', title='Claims Payments 12 September 2026',
            raised_by=self.finance,
            status=LargePaymentRequest.Status.PENDING_CFO,
            total=Decimal('231840.00'))
        LargePaymentLine.objects.create(
            request=self.req,
            payment_request=_payment('PAY/P2A', '231840.00', claim='G2026004923'),
            payment_ref='PAY/P2A', amount=Decimal('231840.00'),
            claim_number='G2026004923', payee='Optimum Panel Beaters',
            insured_name='Sefalana Holdings', enrich_status='ok')

    def _approve(self):
        self.client.force_authenticate(self.cfo)
        return self.client.post(f'/api/v1/large-payments/{self.req.id}/decide/',
                                {'decision': 'approve'}, format='json')


class SendTests(Piece2Base):

    def test_the_ceo_is_actually_on_his_own_email(self):
        """The trap that would have made Piece 2 deliver nothing.

        aiyer@ is on Omni's NEVER_CC list. If allow_named_exec is not passed he
        is silently removed and the send still reports success.
        """
        r = self._approve()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn(CEO, [a.lower() for a in msg.to],
                      'the CEO was stripped from his own authorisation email')

    def test_the_standing_cc_list_is_on_it(self):
        self._approve()
        cc = [a.lower() for a in mail.outbox[0].cc]
        for who in ('pbeka@alphadirect.co.bw', 'btendani@alphadirect.co.bw',
                    'ktshutlhedi@alphadirect.co.bw', 'pkago@alphadirect.co.bw',
                    'wmoses@alphadirect.co.bw'):
            self.assertIn(who, cc, f'{who} must be cc-d on every large-payment email')

    def test_the_whole_document_is_in_the_body_not_an_attachment(self):
        """CFO, 2026-09-01: a summary in the body with the detail attached is not
        acceptable. The body must stand alone."""
        self._approve()
        html = mail.outbox[0].alternatives[0][0]
        for expected in ('G2026004923', 'Sefalana Holdings',
                         'Optimum Panel Beaters', '231,840.00',
                         'Mr Arun Iyer', 'Payment Authorisation'):
            self.assertIn(expected, html, f'{expected!r} missing from the body')

    def test_the_email_carries_the_four_no_login_buttons(self):
        self._approve()
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Approve all', html)
        self.assertIn('Refuse this request', html)
        self.assertIn('Ask for more detail', html)
        self.assertIn('Wangu Moses', html)
        self.assertIn('/api/magic/', html)

    def test_no_do_not_reply_banner_on_a_mail_that_asks_him_a_question(self):
        """House rule: the red do-not-reply banner never goes on an email that
        asks the recipient something."""
        self._approve()
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('do not reply', html.lower())

    def test_data_quality_findings_do_not_reach_the_ceo(self):
        """Flags go to Wangu and Kago. The CEO gets a count, never the findings."""
        line = self.req.lines.first()
        line.flags = [{'code': 'not_in_graphite',
                       'message': 'Claim G2026004923 was not found in Graphite.'}]
        line.save()
        self._approve()
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('not found in Graphite', html)
        self.assertIn('query raised with Claims and Finance', html)

    def test_approving_moves_it_to_with_the_ceo_and_raises_his_task(self):
        self._approve()
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.SENT_TO_CEO)
        self.assertIsNotNone(self.req.ceo_task_id)
        self.assertEqual(self.req.ceo_task.assignee_id, self.ceo.id)
        # due_at is the switch between a silent task and a chased one.
        self.assertIsNotNone(self.req.ceo_task.due_at)
        self.assertIn(CEO, [a.lower() for a in self.req.sent_recipients])

    def test_a_failed_send_is_not_reported_as_success(self):
        """Approved-but-not-sent must never look like Arun has it."""
        with patch('core.notifications.send_html_with_cfo_cc',
                   side_effect=RuntimeError('smtp down')):
            r = self._approve()
        self.assertEqual(r.status_code, 502)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.APPROVED)
        self.assertIn('smtp down', self.req.send_error)
        self.assertIsNone(self.req.ceo_task_id)

    def test_a_send_that_reaches_nobody_is_a_failure(self):
        with patch('core.notifications.send_html_with_cfo_cc', return_value=0):
            r = self._approve()
        self.assertEqual(r.status_code, 502)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.APPROVED)

    def test_it_can_be_sent_again_after_a_failure(self):
        with patch('core.notifications.send_html_with_cfo_cc',
                   side_effect=RuntimeError('smtp down')):
            self._approve()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{self.req.id}/resend/')
        self.assertEqual(r.status_code, 200, r.data)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.SENT_TO_CEO)

    def test_resending_does_not_stack_a_second_ceo_task(self):
        self._approve()
        self.client.force_authenticate(self.cfo)
        self.client.post(f'/api/v1/large-payments/{self.req.id}/resend/')
        self.assertEqual(
            OmniTask.objects.filter(source='large_payment_ceo',
                                    assignee=self.ceo).count(), 1)

    def test_it_cannot_be_resent_once_he_has_decided(self):
        self._approve()
        LargePaymentRequest.objects.filter(pk=self.req.pk).update(
            status=LargePaymentRequest.Status.CEO_APPROVED)
        self.client.force_authenticate(self.cfo)
        r = self.client.post(f'/api/v1/large-payments/{self.req.id}/resend/')
        self.assertEqual(r.status_code, 409)


class ButtonTests(Piece2Base):
    """The CEO's no-login buttons — the whole interface, since he won't use Omni."""

    def setUp(self):
        super().setUp()
        self._approve()
        self.req.refresh_from_db()
        from large_payments import magic
        self.magic = magic
        self.ctx = {'r': str(self.req.id)}

    def test_the_actions_are_registered_on_omnis_magic_registry(self):
        from core.magic_action import ACTIONS
        for kind in ('lp_approve_all', 'lp_reject', 'lp_ask', 'lp_more_detail'):
            self.assertIn(kind, ACTIONS, f'{kind} never registered')

    def test_describe_changes_nothing(self):
        """GET is inert, so a link scanner following the URL cannot approve a run."""
        before = self.req.status
        for kind in ('lp_approve_all', 'lp_reject', 'lp_more_detail'):
            self.magic.HANDLERS[kind]['describe'](self.ceo, self.ctx)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, before)

    def test_only_the_ceo_can_press_the_buttons(self):
        for who in (self.cfo, self.finance, self.wangu):
            ok, msg = self.magic.HANDLERS['lp_approve_all']['act'](who, self.ctx)
            self.assertFalse(ok, f'{who.username} must not be able to approve')
            self.assertIn('Chief Executive', msg)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.SENT_TO_CEO)

    def test_approve_all_authorises_and_closes(self):
        ok, msg = self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        self.assertTrue(ok, msg)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_APPROVED)
        self.assertIsNotNone(self.req.ceo_decided_at)
        self.req.cfo_task and self.req.cfo_task.refresh_from_db()
        self.req.ceo_task.refresh_from_db()
        self.assertEqual(self.req.ceo_task.status, OmniTask.Status.DONE)

    def test_approve_all_twice_records_one_approval(self):
        """He taps it twice on a phone, or clicks after replying."""
        self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        ok, msg = self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        self.assertTrue(ok)
        self.assertIn('Already approved', msg)

    def test_approve_all_changes_no_payment(self):
        """The invariant survives Piece 2: the CEO's approval closes the REQUEST,
        never the payments. Omni's own close function records that the BANK
        confirmed the money left, which the CEO's click is not."""
        from taskboard.models import PaymentRequest
        before = {p.id: (p.total, p.status, p.updated_at)
                  for p in PaymentRequest.objects.all()}
        self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        after = {p.id: (p.total, p.status, p.updated_at)
                 for p in PaymentRequest.objects.all()}
        self.assertEqual(before, after)

    def test_refusing_frees_the_payments_for_a_corrected_request(self):
        from large_payments.selection import candidates
        ok, _ = self.magic.HANDLERS['lp_reject']['act'](self.ceo, self.ctx)
        self.assertTrue(ok)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_REJECTED)
        rows, _cut = candidates()
        self.assertIn('PAY/P2A', {r['ref'] for r in rows},
                      'a refused payment must be raisable again')

    def test_refusing_tells_the_cfo_urgently(self):
        self.magic.HANDLERS['lp_reject']['act'](self.ceo, self.ctx)
        task = OmniTask.objects.filter(assignee=self.cfo,
                                       title__contains='REFUSED').first()
        self.assertIsNotNone(task, 'the CFO must be told the CEO refused')
        self.assertEqual(task.priority, OmniTask.Priority.URGENT)

    def test_asking_a_question_raises_a_task_and_holds_the_request(self):
        ctx = dict(self.ctx, a='wmoses@alphadirect.co.bw')
        ok, msg = self.magic.HANDLERS['lp_ask']['act'](self.ceo, ctx)
        self.assertTrue(ok, msg)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.QUESTION)
        self.assertEqual(len(self.req.ceo_questions), 1)
        self.assertTrue(OmniTask.objects.filter(assignee=self.wangu).exists())

    def test_a_second_question_does_not_erase_the_first(self):
        for email in ('wmoses@alphadirect.co.bw', 'ktshutlhedi@alphadirect.co.bw'):
            _mk_user(email.split('@')[0] + '_x', email) if not User.objects.filter(
                email__iexact=email).exists() else None
            self.magic.HANDLERS['lp_ask']['act'](self.ceo, dict(self.ctx, a=email))
        self.req.refresh_from_db()
        self.assertEqual(len(self.req.ceo_questions), 2)

    def test_he_can_still_approve_after_asking_a_question(self):
        self.magic.HANDLERS['lp_ask']['act'](
            self.ceo, dict(self.ctx, a='wmoses@alphadirect.co.bw'))
        ok, msg = self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        self.assertTrue(ok, msg)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_APPROVED)

    def test_asking_someone_with_no_omni_account_says_so(self):
        self.wangu.is_active = False
        self.wangu.save()
        ok, msg = self.magic.HANDLERS['lp_ask']['act'](
            self.ceo, dict(self.ctx, a='wmoses@alphadirect.co.bw'))
        self.assertFalse(ok)
        self.assertIn('no active Omni account', msg)

    def test_more_detail_holds_the_request_and_tells_the_cfo(self):
        ok, msg = self.magic.HANDLERS['lp_more_detail']['act'](self.ceo, self.ctx)
        self.assertTrue(ok, msg)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.QUESTION)
        self.assertTrue(OmniTask.objects.filter(
            assignee=self.cfo, title__contains='more detail').exists())

    def test_approving_a_request_he_already_refused_is_refused(self):
        self.magic.HANDLERS['lp_reject']['act'](self.ceo, self.ctx)
        ok, msg = self.magic.HANDLERS['lp_approve_all']['act'](self.ceo, self.ctx)
        self.assertFalse(ok)
        self.assertIn('can no longer be approved', msg)


class DuplicateControlTests(TestCase):
    """The gap found on the LIVE list minutes after Piece 1 shipped.

    Claim G2026004923 appeared twice at exactly BWP 231,840.00 and the duplicate
    check said nothing, because it only compared PAYEE and the payee field is
    empty on those records.
    """

    def setUp(self):
        self.finance = _mk_user('lntabeni', 'lntabeni@alphadirect.co.bw')
        self.req = LargePaymentRequest.objects.create(
            ref='LPR/DUP/0001', raised_by=self.finance, total=Decimal('0'))

    def _line(self, ref, amount, *, claim='', payee=''):
        return LargePaymentLine.objects.create(
            request=self.req,
            payment_request=_payment(ref, amount, claim=claim or 'G0000000000',
                                     payee=payee or 'x'),
            payment_ref=ref, amount=Decimal(amount),
            claim_number=claim, payee=payee)

    def _run(self):
        from large_payments.enrich import _duplicate_flags
        lines = list(self.req.lines.all())
        _duplicate_flags(lines)
        return lines

    def _codes(self, lines):
        return [f['code'] for ln in lines for f in ln.flags]

    def test_same_claim_same_amount_is_flagged_even_with_no_payee(self):
        """The exact live case: G2026004923 twice at 231,840.00, payee blank."""
        self._line('PAY/D1', '231840.00', claim='G2026004923', payee='')
        self._line('PAY/D2', '231840.00', claim='G2026004923', payee='')
        lines = self._run()
        self.assertEqual(self._codes(lines).count('possible_duplicate'), 2)
        self.assertIn('G2026004923', lines[0].flags[0]['message'])

    def test_the_one_thebe_trick_does_not_defeat_it(self):
        self._line('PAY/D3', '88099.65', claim='G2026000111', payee='')
        self._line('PAY/D4', '88099.64', claim='G2026000111', payee='')
        self.assertEqual(self._codes(self._run()).count('possible_duplicate'), 2)

    def test_same_payee_across_different_claims_still_flagged(self):
        """The old behaviour is kept, not replaced."""
        self._line('PAY/D5', '5000.00', claim='G2026000222', payee='Nors Botswana')
        self._line('PAY/D6', '5000.00', claim='G2026000333', payee='Nors Botswana')
        self.assertEqual(self._codes(self._run()).count('possible_duplicate'), 2)

    def test_two_identical_amounts_with_nothing_to_tell_them_apart(self):
        self._line('PAY/D7', '7000.00', claim='', payee='')
        self._line('PAY/D8', '7000.00', claim='', payee='')
        codes = self._codes(self._run())
        self.assertEqual(codes.count('possible_duplicate'), 2)

    def test_different_claims_and_different_payees_are_not_flagged(self):
        self._line('PAY/D9', '9000.00', claim='G2026000444', payee='Alpha Panel')
        self._line('PAY/DA', '9000.00', claim='G2026000555', payee='Beta Panel')
        self.assertNotIn('possible_duplicate', self._codes(self._run()))

    def test_different_amounts_on_one_claim_are_not_flagged(self):
        """A part-payment and a balance on one claim is normal."""
        self._line('PAY/DB', '1000.00', claim='G2026000666', payee='')
        self._line('PAY/DC', '2500.00', claim='G2026000666', payee='')
        self.assertNotIn('possible_duplicate', self._codes(self._run()))


class FableRound1FixTests(Piece2Base):
    """The defects Fable 5.1 found on the first Piece 2 review, 2026-09-12."""

    # Fix 2 — the email claimed a query was raised; nothing raised it.
    def test_the_claims_query_the_email_promises_is_actually_raised(self):
        line = self.req.lines.first()
        line.flags = [{'code': 'not_in_graphite',
                       'message': 'Claim G2026004923 was not found in Graphite.'}]
        line.save()
        kago = _mk_user('ktshutlhedi', 'ktshutlhedi@alphadirect.co.bw')

        self._approve()

        for who in (self.wangu, kago):
            task = OmniTask.objects.filter(assignee=who,
                                           source='large_payment_query').first()
            self.assertIsNotNone(task, f'{who.username} was never told')
            self.assertIn('not found in Graphite', task.body)
            self.assertEqual(task.priority, OmniTask.Priority.URGENT)
        # …and the CEO still gets only the count, never the finding.
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('not found in Graphite', html)

    def test_no_query_task_when_nothing_is_flagged(self):
        self._approve()
        self.assertFalse(OmniTask.objects.filter(source='large_payment_query').exists())

    # Fix 3 — escape('&mdash;') printed a literal "&mdash;".
    def test_a_blank_payee_shows_a_dash_not_the_word_mdash(self):
        """Payee is EMPTY on the live G2026004923 records, so this is the first
        thing the real email would have got wrong."""
        line = self.req.lines.first()
        line.payee = ''
        line.insured_name = ''
        line.policy_number = ''
        line.save()
        self._approve()
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('&amp;mdash;', html)

    # Fix 6 — the CFO's task stayed open after he decided.
    def test_deciding_closes_the_cfos_own_task(self):
        from large_payments.tasks import raise_cfo_task
        task = raise_cfo_task(self.req)
        self.assertIsNotNone(task)
        self._approve()
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE,
                         'Omni would chase him daily for work he finished')

    def test_rejecting_also_closes_his_task(self):
        from large_payments.tasks import raise_cfo_task
        task = raise_cfo_task(self.req)
        self.client.force_authenticate(self.cfo)
        self.client.post(f'/api/v1/large-payments/{self.req.id}/decide/',
                         {'decision': 'reject', 'note': 'amounts wrong'},
                         format='json')
        task.refresh_from_db()
        self.assertEqual(task.status, OmniTask.Status.DONE)

    # Fix 5 — the screen could never show why a send failed or who he asked.
    def test_the_screen_is_told_why_a_send_failed(self):
        with patch('core.notifications.send_html_with_cfo_cc',
                   side_effect=RuntimeError('smtp down')):
            r = self._approve()
        self.assertEqual(r.status_code, 502)
        self.assertIn('smtp down', r.data['detail'])
        self.client.force_authenticate(self.cfo)
        d = self.client.get(f'/api/v1/large-payments/{self.req.id}/')
        self.assertIn('smtp down', d.data['send_error'])

    def test_the_screen_is_told_who_the_ceo_asked(self):
        self._approve()
        from large_payments import magic
        magic.HANDLERS['lp_ask']['act'](
            self.ceo, {'r': str(self.req.id), 'a': 'wmoses@alphadirect.co.bw'})
        self.client.force_authenticate(self.cfo)
        d = self.client.get(f'/api/v1/large-payments/{self.req.id}/')
        self.assertEqual(len(d.data['ceo_questions']), 1)
        self.assertIn('Wangu', d.data['ceo_questions'][0]['name'])

    # Fix 4 — a question must not regress a request he already decided.
    def test_a_question_cannot_reopen_a_request_he_already_approved(self):
        self._approve()
        from large_payments import magic
        ctx = {'r': str(self.req.id)}
        magic.HANDLERS['lp_approve_all']['act'](self.ceo, ctx)
        ok, msg = magic.HANDLERS['lp_ask']['act'](
            self.ceo, dict(ctx, a='wmoses@alphadirect.co.bw'))
        self.assertFalse(ok, 'a stale Ask link must not re-open a decided request')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_APPROVED)

    def test_more_detail_cannot_reopen_a_request_he_already_refused(self):
        self._approve()
        from large_payments import magic
        ctx = {'r': str(self.req.id)}
        magic.HANDLERS['lp_reject']['act'](self.ceo, ctx)
        ok, _ = magic.HANDLERS['lp_more_detail']['act'](self.ceo, ctx)
        self.assertFalse(ok)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_REJECTED)

    # Advisory — a name clash must be loud, not silent.
    def test_registering_over_someone_elses_action_raises(self):
        from core.magic_action import ACTIONS
        from large_payments.magic import register
        original = ACTIONS.get('lp_ask')
        ACTIONS['lp_ask'] = {'describe': lambda u, c: ('x', 'y', 'z'),
                             'act': lambda u, c: (True, 'someone else')}
        try:
            with self.assertRaises(RuntimeError):
                register()
        finally:
            if original is not None:
                ACTIONS['lp_ask'] = original

    def test_the_row_lock_itself_refuses_a_decided_request(self):
        """Isolates the LOCK, not the early guard.

        The sequential test above passes even with the lock's status filter
        removed, because _ask_act's own `status not in _OPEN` check already
        returned first — so it proves a stale link is refused, NOT that the
        concurrent race is closed. Fable 5.1 raised the real case: two POSTs in
        flight, approve wins the conditional UPDATE, and the ask then writes
        status='question' back over a decided request.

        Calling _record_question directly is the only way to reach the lock the
        way a second in-flight request would, so this is the test that actually
        fails if the status filter comes off.
        """
        from large_payments.magic import _record_question
        self._approve()
        LargePaymentRequest.objects.filter(pk=self.req.pk).update(
            status=LargePaymentRequest.Status.CEO_APPROVED)
        self.req.refresh_from_db()

        recorded = _record_question(self.req, {'asked_at': 'x', 'of': 'y',
                                               'name': 'z'})
        self.assertFalse(recorded, 'the lock must refuse a request already decided')
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, LargePaymentRequest.Status.CEO_APPROVED)
        self.assertEqual(len(self.req.ceo_questions or []), 0)
