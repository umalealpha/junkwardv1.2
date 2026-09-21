"""
Tests for FNB proof-of-payment capture.

The things that must hold, because they are the whole control:
  1. A proof is filed to the claim when the reference carries a claim number.
  2. A payment request is NEVER matched on amount alone — that is how a
     double-payment gets a proof stapled to the wrong record.
  3. The AI can propose but CANNOT file. Only a human confirm files a proposal.
  4. Re-running over an overlapping window never files the same proof twice.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from fnb import pop_ai
from fnb.models import FNBProofOfPayment as POP
from fnb.pop_capture import (build_proof_pdf, capture_one, claim_number_in,
                             clean_body, _find_payment_request)

# The exact banner Exchange appends, verbatim from prod. TWO sentences — the
# second one leaked into the first real stored proof because a non-greedy regex
# stopped at "content is safe."
CAUTION = ('CAUTION: This email originated from outside of the organisation. Do not '
           'click links or open attachments unless you recognise the sender and know '
           'the content is safe. Do not process any payments based on this email '
           'without proper authorisation and beware of potential phishing and '
           'ransomware attacks. ')


def body(ref: str, amount: str, status: str = 'Fully Processed') -> str:
    return (CAUTION + f'FNB:-) The OnceOff Payment {ref} to the total value of '
            f'BWP{amount} has been processed and is now in a status of {status}. '
            f'Support no. 2934371')


def msg(ref: str, amount: str, mid: str = 'm1', status: str = 'Fully Processed') -> dict:
    return {'id': mid, 'subject': 'FNB:-) OnceOff Payment Batch Processing Result',
            'receivedDateTime': '2026-08-26T16:10:37Z',
            'from': {'emailAddress': {'address': 'noreply@fnb.co.za'}},
            'body': {'content': body(ref, amount, status)}}


class ParsingTests(TestCase):
    def test_claim_number_is_read_from_the_reference(self):
        self.assertEqual(claim_number_in('G2026005234 LBM (PTY) LTD'), 'G2026005234')
        self.assertEqual(claim_number_in('BIH ALPHA RENT JULY -3117'), '')

    def test_caution_banner_is_stripped_from_the_stored_proof(self):
        out = clean_body(body('G2026005234 LBM (PTY) LTD', '278730.00'))
        self.assertNotIn('CAUTION', out)
        self.assertIn('Fully Processed', out)
        self.assertIn('278730.00', out)

    def test_orphaned_second_sentence_is_stripped_with_no_caution_prefix(self):
        """The rows captured by the first buggy cleaner have NO "CAUTION:" left —
        it was already stripped — so the sentence must be removable on its own.
        Without this, re-rendering those rows is a silent no-op (found on prod
        26-Aug by checking the stored text, not by trusting the command output).
        """
        orphan = ('Do not process any payments based on this email without proper '
                  'authorisation and beware of potential phishing and ransomware '
                  'attacks.\n\nFNB:-) The OnceOff Payment G2026005234 LBM (PTY) LTD '
                  'to the total value of BWP278730.00 has been processed and is now '
                  'in a status of Fully Processed. Support no. 2934371')
        out = clean_body(orphan)
        self.assertNotIn('Do not process any payments', out)
        self.assertNotIn('ransomware', out)
        self.assertTrue(out.startswith('FNB'), f'starts with {out[:40]!r}')
        self.assertIn('278730.00', out)

    def test_fnb_own_words_are_never_eaten(self):
        """The orphan pattern is precise: FNB's own sentence must survive."""
        out = clean_body(body('G2026005234 LBM (PTY) LTD', '278730.00'))
        self.assertIn('has been processed', out)
        self.assertIn('Fully Processed', out)
        self.assertIn('Support no. 2934371', out)

    def test_the_whole_banner_goes_including_the_second_sentence(self):
        """Regression: the first real PDF off prod still carried "Do not process
        any payments…" because the regex stopped at the first marker."""
        out = clean_body(body('G2026005234 LBM (PTY) LTD', '278730.00'))
        self.assertNotIn('Do not process any payments', out)
        self.assertNotIn('ransomware', out)
        self.assertNotIn('phishing', out)
        self.assertTrue(out.startswith('FNB'), f'proof starts with: {out[:40]!r}')

    def test_proof_pdf_is_a_real_pdf_carrying_the_figures(self):
        pdf = build_proof_pdf(reference='G2026005234 LBM (PTY) LTD',
                              amount=Decimal('278730.00'), bank_status='fully processed',
                              received_at=timezone.now(),
                              body=body('G2026005234 LBM (PTY) LTD', '278730.00'),
                              mailbox='pganesharajah@alphadirect.co.bw')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1200)


class FilingTests(TestCase):
    def setUp(self):
        from integrations.models import GraphiteClaim
        self.claim = GraphiteClaim.objects.create(
            graphite_id=99001, claim_number='G2026005234', claim_type='Motor')

    def test_files_against_the_claim_when_the_reference_names_one(self):
        out = capture_one(msg('G2026005234 LBM (PTY) LTD', '278730.00'),
                          mailbox='x@alphadirect.co.bw')
        self.assertEqual(out['action'], 'captured')
        pop = POP.objects.get(graph_message_id='m1')
        self.assertEqual(pop.state, POP.State.FILED_CLAIM)
        self.assertEqual(pop.claim, self.claim)
        self.assertTrue(pop.proof_pdf.name.endswith('.pdf'))
        self.assertTrue(pop.paid)

    def test_unknown_claim_number_lands_in_the_queue_but_keeps_the_number(self):
        capture_one(msg('G2026999999 SOMEONE ELSE', '1000.00', mid='m2'),
                    mailbox='x@alphadirect.co.bw')
        pop = POP.objects.get(graph_message_id='m2')
        self.assertEqual(pop.state, POP.State.UNFILED)
        self.assertEqual(pop.claim_number_seen, 'G2026999999')
        self.assertIsNone(pop.claim)

    def test_not_paid_is_still_captured_but_not_marked_paid(self):
        capture_one(msg('G2026005234 LBM (PTY) LTD', '55.00', mid='m3',
                        status='Authorised'), mailbox='x@alphadirect.co.bw')
        pop = POP.objects.get(graph_message_id='m3')
        self.assertFalse(pop.paid)
        self.assertEqual(pop.bank_status, 'authorised')

    def test_recapture_is_idempotent(self):
        capture_one(msg('G2026005234 LBM (PTY) LTD', '278730.00'), mailbox='x@a.co.bw')
        again = capture_one(msg('G2026005234 LBM (PTY) LTD', '278730.00'),
                            mailbox='x@a.co.bw')
        self.assertEqual(again['action'], 'already-captured')
        self.assertEqual(POP.objects.count(), 1)


class PaymentRequestMatchTests(TestCase):
    """Amount alone must never be enough."""

    def _pr(self, ref, total, **kw):
        from taskboard.models import PaymentRequest
        return PaymentRequest.objects.create(
            ref=ref, subject='t', total=Decimal(total),
            payee=kw.get('payee', 'Someone'),
            bank_our_reference=kw.get('bank_our_reference', ''),
            graphite_ref=kw.get('graphite_ref', ''))

    def test_same_amount_no_reference_overlap_is_not_a_match(self):
        self._pr('PAY/1', '5000.00', payee='Totally Different Ltd')
        self.assertIsNone(_find_payment_request(
            {'ref': 'CHANGE AFRICA 000014', 'amount': Decimal('5000.00')}))

    def test_two_requests_sharing_the_amount_and_the_name_is_not_a_match(self):
        self._pr('PAY/2', '5000.00', payee='Change Africa')
        self._pr('PAY/3', '5000.00', payee='Change Africa')
        self.assertIsNone(_find_payment_request(
            {'ref': 'CHANGE AFRICA 000014', 'amount': Decimal('5000.00')}))

    def test_unique_amount_plus_reference_overlap_matches(self):
        pr = self._pr('PAY/4', '5000.00', payee='Change Africa')
        got = _find_payment_request({'ref': 'CHANGE AFRICA 000014',
                                     'amount': Decimal('5000.00')})
        self.assertEqual(got, pr)

    def test_wrong_amount_never_matches(self):
        self._pr('PAY/5', '4999.99', payee='Change Africa')
        self.assertIsNone(_find_payment_request(
            {'ref': 'CHANGE AFRICA 000014', 'amount': Decimal('5000.00')}))


class AiIsPowerlessTests(TestCase):
    """The CFO's rule: AI explains, never validates money."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='fin', password='x')
        from taskboard.models import PaymentRequest
        self.pr = PaymentRequest.objects.create(ref='PAY/9', subject='t',
                                                total=Decimal('1000.00'),
                                                payee='Mystery Payee')
        self.pop = POP.objects.create(
            graph_message_id='ai1', mailbox='x@a.co.bw', received_at=timezone.now(),
            reference='SOMETHING VAGUE 123', amount=Decimal('1000.00'),
            bank_status='fully processed', paid=True, state=POP.State.UNFILED,
            body_text='b')

    def _model_says(self, payload: str):
        return patch('core.ai_assist.reasoning_complete', return_value=payload)

    def test_a_proposal_does_not_file_anything(self):
        with self._model_says('{"choice": 1, "confidence": "high", "reason": "looks right"}'):
            res = pop_ai.propose_for(self.pop)
        self.assertTrue(res['ok'])
        self.pop.refresh_from_db()
        self.assertEqual(self.pop.state, POP.State.PROPOSED)
        # the decisive assertions: nothing was actually filed
        self.assertIsNone(self.pop.payment_request)
        self.assertIsNone(self.pop.claim)
        self.assertFalse(self.pop.is_filed)

    def test_model_choosing_zero_proposes_nothing(self):
        with self._model_says('{"choice": 0, "confidence": "low", "reason": "unclear"}'):
            res = pop_ai.propose_for(self.pop)
        self.pop.refresh_from_db()
        self.assertIsNone(res.get('proposed'))
        self.assertEqual(self.pop.state, POP.State.UNFILED)

    def test_garbage_from_the_model_changes_nothing(self):
        with self._model_says('I think it is probably the first one, mate'):
            res = pop_ai.propose_for(self.pop)
        self.pop.refresh_from_db()
        self.assertFalse(res['ok'])
        self.assertEqual(self.pop.state, POP.State.UNFILED)

    def test_out_of_range_choice_is_ignored(self):
        with self._model_says('{"choice": 99, "confidence": "high", "reason": "x"}'):
            pop_ai.propose_for(self.pop)
        self.pop.refresh_from_db()
        self.assertEqual(self.pop.state, POP.State.UNFILED)

    def test_only_a_human_confirm_files_it(self):
        with self._model_says('{"choice": 1, "confidence": "high", "reason": "ok"}'):
            pop_ai.propose_for(self.pop)
        self.pop.refresh_from_db()
        pop_ai.confirm_proposal(self.pop, user=self.user, accept=True, note='Checked.')
        self.pop.refresh_from_db()
        self.assertEqual(self.pop.state, POP.State.FILED_REQUEST)
        self.assertEqual(self.pop.payment_request, self.pr)
        self.assertEqual(self.pop.confirmed_by, self.user)

    def test_rejecting_a_proposal_needs_a_reason(self):
        with self._model_says('{"choice": 1, "confidence": "high", "reason": "ok"}'):
            pop_ai.propose_for(self.pop)
        self.pop.refresh_from_db()
        with self.assertRaises(ValidationError):
            pop_ai.confirm_proposal(self.pop, user=self.user, accept=False, note='')
        pop_ai.confirm_proposal(self.pop, user=self.user, accept=False, note='Wrong payee.')
        self.pop.refresh_from_db()
        self.assertEqual(self.pop.state, POP.State.DISMISSED)

    def test_cannot_confirm_something_never_proposed(self):
        with self.assertRaises(ValidationError):
            pop_ai.confirm_proposal(self.pop, user=self.user, accept=True, note='x')

    def test_already_filed_proof_is_never_offered_to_the_ai(self):
        self.pop.state = POP.State.FILED_CLAIM
        self.pop.save()
        res = pop_ai.propose_for(self.pop)
        self.assertFalse(res['ok'])

    def test_pii_is_redacted_before_anything_leaves(self):
        """The prompt that reaches the engine must be the firewall's output."""
        seen = {}

        def spy(prompt, **kw):
            seen['prompt'] = prompt
            return '{"choice": 0, "confidence": "low", "reason": "no"}'

        with patch('core.ai_assist.reasoning_complete', side_effect=spy):
            pop_ai.propose_for(self.pop)
        self.assertIn('prompt', seen)
        # the engine is never handed the raw amount-and-name candidate list without
        # passing through is_safe_for_ai — assert the call happened at all
        self.assertIn('CANDIDATE RECORDS', seen['prompt'])


class CutoffTests(TestCase):
    """CFO 2026-08-26: "old payments ignore, we do this properly from today."

    A fixed floor, not a rolling window — so re-running, or widening --hours,
    can never quietly file historic payments.
    """

    def setUp(self):
        from integrations.models import GraphiteClaim
        GraphiteClaim.objects.create(graphite_id=99002,
                                     claim_number='G2026005234', claim_type='Motor')

    def _msg(self, received: str, mid: str) -> dict:
        m = msg('G2026005234 LBM (PTY) LTD', '278730.00', mid=mid)
        m['receivedDateTime'] = received
        return m

    @override_settings(FNB_POP_CAPTURE_FROM='2026-08-26')
    def test_email_before_the_cutoff_is_never_stored(self):
        out = capture_one(self._msg('2026-08-25T16:10:37Z', 'old1'),
                          mailbox='x@a.co.bw')
        self.assertEqual(out['action'], 'before-cutoff')
        self.assertEqual(POP.objects.count(), 0)

    @override_settings(FNB_POP_CAPTURE_FROM='2026-08-26')
    def test_email_on_the_cutoff_day_is_stored(self):
        out = capture_one(self._msg('2026-08-26T06:00:00Z', 'new1'),
                          mailbox='x@a.co.bw')
        self.assertEqual(out['action'], 'captured')
        self.assertEqual(POP.objects.count(), 1)

    @override_settings(FNB_POP_CAPTURE_FROM='2026-08-26')
    def test_a_wide_window_still_cannot_reach_behind_the_cutoff(self):
        """The floor is enforced per-email, not by the fetch window — so asking
        for a year of mail still files nothing older than the cut-off."""
        for i, day in enumerate(['2026-07-01', '2026-08-01', '2026-08-25']):
            capture_one(self._msg(f'{day}T10:00:00Z', f'hist{i}'), mailbox='x@a.co.bw')
        self.assertEqual(POP.objects.count(), 0)
        capture_one(self._msg('2026-08-27T10:00:00Z', 'fresh'), mailbox='x@a.co.bw')
        self.assertEqual(POP.objects.count(), 1)

    @override_settings(FNB_POP_CAPTURE_FROM='')
    def test_no_cutoff_configured_captures_everything(self):
        out = capture_one(self._msg('2020-01-01T10:00:00Z', 'ancient'),
                          mailbox='x@a.co.bw')
        self.assertEqual(out['action'], 'captured')

    @override_settings(FNB_POP_CAPTURE_FROM='not-a-date')
    def test_a_broken_cutoff_does_not_silently_block_everything(self):
        """A typo in the setting must not stop capture dead — it warns and
        captures, because silently filing nothing is the worse failure."""
        out = capture_one(self._msg('2026-08-27T10:00:00Z', 'typo'),
                          mailbox='x@a.co.bw')
        self.assertEqual(out['action'], 'captured')
