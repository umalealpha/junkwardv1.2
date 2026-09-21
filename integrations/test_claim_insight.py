"""Tests for the claim-insight feature (CFO 2026-08-31, Claim-Description-Spec).

Two things must hold and are proven here:
  * FACTS & FLAGS are computed by RULE — already-paid / over-balance / repudiated
    fire deterministically from the mirror, with no AI in the path.
  * The AI half degrades GRACEFULLY — if every engine is down, or the facts can't
    be made PII-safe, summarise_claim returns nothing and a payment is never
    blocked. (Revert the try/except in claim_summary_ai and test_summary_* go red.)
"""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from core.ai_assist import DeepSeekUnavailable, SafetyReport
from integrations.claim_insight import build_facts_text, build_claim_insight, _hard_flags
from integrations import claim_summary_ai


def _claim(**over):
    """An in-memory GraphiteClaim (no DB) for pure logic tests."""
    from integrations.models import GraphiteClaim
    base = dict(
        graphite_id=1, claim_number='', customer_name='COLDLINE (PTY) LTD',
        is_company=True, policy_number='POL123', product_name='Motor',
        claim_type='Accident', status='open', claim_handler='Jane Handler',
        date_of_loss=date(2026, 7, 10), damage_cause='THE INSURED HIT A COW',
        total_reserve=Decimal('15000.00'), total_payment=Decimal('0.00'),
        balance=Decimal('15000.00'),
    )
    base.update(over)
    return GraphiteClaim(**base)


class HardFlagRuleTests(SimpleTestCase):
    """Flags are Omni's own arithmetic — no AI, no DB (claim_number='' skips the
    duplicate DB scan)."""

    def setUp(self):
        # The premium Gate-0 card reads the RealPay mirror (a DB query), which a
        # SimpleTestCase forbids — it would raise and now (H6) surface an amber
        # "could not check" card. It is covered by PremiumGateFlagDBTests; isolate
        # it here so these no-DB flag rules are tested on their own.
        p = mock.patch('integrations.claim_insight.premium_gate_flag', return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_payment_posted_on_open_claim_is_an_amber_note_not_danger(self):
        # CFO 2026-09-04 (Leano's report): Graphite posts the loss payment when
        # Claims raises it, so a posted payment on an OPEN claim is the normal
        # sequence. It used to paint red "already paid" and read as a refusal.
        # Red is now reserved for Omni holding its own request (DB test below).
        flags = _hard_flags(_claim(total_payment=Decimal('5000.00'), balance=Decimal('10000.00')),
                            line_amount=None, currency='BWP', entity='', exclude_pk=None)
        codes = {f['code']: f['level'] for f in flags}
        self.assertEqual(codes.get('graphite_settled'), 'warning')
        self.assertNotIn('already_paid', codes)
        self.assertFalse(any(f['level'] == 'danger' for f in flags))

    def test_closed_claim_is_amber_and_routed_to_the_committee(self):
        flags = _hard_flags(_claim(status='Closed', total_payment=Decimal('15000.00'),
                                   balance=Decimal('0.00')),
                            line_amount=None, currency='BWP', entity='', exclude_pk=None)
        codes = {f['code']: f['level'] for f in flags}
        self.assertEqual(codes.get('graphite_closed'), 'warning')
        self.assertIn('committee', next(f['label'] for f in flags if f['code'] == 'graphite_closed'))

    def test_amount_over_balance_flags_warning(self):
        flags = _hard_flags(_claim(balance=Decimal('1000.00')),
                            line_amount=Decimal('1500.00'), currency='BWP', entity='', exclude_pk=None)
        self.assertIn('amount_over_balance', {f['code'] for f in flags})

    def test_repudiated_flags_danger(self):
        flags = _hard_flags(_claim(status='Repudiated'),
                            line_amount=None, currency='BWP', entity='', exclude_pk=None)
        self.assertIn('repudiated', {f['code'] for f in flags})

    def test_clean_claim_has_no_flags(self):
        flags = _hard_flags(_claim(status='open', total_payment=Decimal('0.00'),
                                   balance=Decimal('5000.00')),
                            line_amount=Decimal('1000.00'), currency='BWP', entity='', exclude_pk=None)
        self.assertEqual(flags, [])

    def test_duplicate_flag_carries_clash_ref(self):
        # compare_lines hard rows use 'clash_ref' — assert the pointer reaches the
        # flag label (the branch that was silently keying off the wrong key).
        hard = {'hard': [{'clash_ref': 'PAY/ADIC/2026/08/0001', 'amount': '1500.00',
                          'clash_kind': 'claim', 'clash_status': 'paid'}], 'soft': []}
        with mock.patch('taskboard.payment_duplicates.find_duplicates', return_value=hard):
            flags = _hard_flags(_claim(claim_number='G2026005213'),
                                line_amount=Decimal('1500.00'), currency='BWP',
                                entity='', exclude_pk=None)
        dup = [f for f in flags if f['code'] == 'duplicate']
        self.assertTrue(dup)
        self.assertIn('PAY/ADIC/2026/08/0001', dup[0]['label'])

    def test_facts_text_states_the_numbers(self):
        text = build_facts_text(_claim(), 'BWP')
        self.assertIn('COLDLINE (PTY) LTD', text)
        self.assertIn('BWP 15,000.00', text)   # reserve stated by rule, not by AI


class ClaimSummaryAITests(SimpleTestCase):
    """The WORDS half. reasoning_complete/is_safe_for_ai are imported inside the
    function from core.ai_assist, so patch them there."""

    def test_parses_json_and_keeps_suggestion(self):
        payload = '{"summary": "Motor claim, loss confirmed, reserve intact.", ' \
                  '"suggestion": "PAY", "reason": "Balance covers the amount."}'
        with mock.patch('core.ai_assist.reasoning_complete', return_value=payload):
            summary, suggestion, reason, engine = claim_summary_ai.summarise_claim(_claim())
        self.assertIn('Motor claim', summary)
        self.assertEqual(suggestion, 'PAY')
        self.assertEqual(engine, 'reasoning_complete')

    def test_graceful_when_all_engines_down(self):
        with mock.patch('core.ai_assist.reasoning_complete',
                        side_effect=DeepSeekUnavailable('all down')):
            result = claim_summary_ai.summarise_claim(_claim())
        self.assertEqual(result, ('', '', '', ''))   # never raises, never blocks

    def test_clamps_to_200_words(self):
        long_summary = ' '.join(['word'] * 300)
        payload = '{"summary": "%s", "suggestion": "HOLD", "reason": "long"}' % long_summary
        with mock.patch('core.ai_assist.reasoning_complete', return_value=payload):
            summary, suggestion, _, _ = claim_summary_ai.summarise_claim(_claim())
        self.assertLessEqual(len(summary.split()), 201)   # 200 words + trailing ellipsis token
        self.assertEqual(suggestion, 'HOLD')

    def test_rejects_bad_suggestion(self):
        payload = '{"summary": "text", "suggestion": "MAYBE", "reason": "x"}'
        with mock.patch('core.ai_assist.reasoning_complete', return_value=payload):
            _, suggestion, _, _ = claim_summary_ai.summarise_claim(_claim())
        self.assertEqual(suggestion, '')   # only PAY/HOLD allowed; never invents a verdict

    def test_facts_for_ai_strips_identity_case_insensitively(self):
        # damage_cause is ALL-CAPS; the claimant name there (different case from
        # customer_name) must still be stripped before anything reaches an engine.
        c = _claim(customer_name='Coldline (Pty) Ltd',
                   damage_cause='COLDLINE (PTY) LTD TRUCK HIT A COW')
        out = claim_summary_ai._facts_for_ai(c, 'BWP')
        self.assertNotIn('coldline', out.lower())
        self.assertIn('the insured', out)

    def test_skips_ai_when_facts_unsafe(self):
        unsafe = SafetyReport(safe=False, redacted_text='x', redactions_made=9, notes=['unsafe'])
        with mock.patch('core.ai_assist.is_safe_for_ai', return_value=unsafe), \
                mock.patch('core.ai_assist.reasoning_complete') as rc:
            result = claim_summary_ai.summarise_claim(_claim())
        self.assertEqual(result, ('', '', '', ''))
        rc.assert_not_called()   # PII never sent when it can't be made safe


class BuildInsightDBTests(TestCase):
    """build_claim_insight against the mirror."""

    def test_missing_claim_returns_not_found(self):
        data = build_claim_insight('G9999999', currency='BWP')
        self.assertFalse(data['found'])
        self.assertEqual(data['flags'], [])

    def test_found_claim_returns_facts_and_stored_summary(self):
        from integrations.models import GraphiteClaim
        GraphiteClaim.objects.create(
            graphite_id=42, claim_number='G2026005213', customer_name='COLDLINE (PTY) LTD',
            is_company=True, policy_number='POL123', product_name='Motor', status='open',
            total_reserve=Decimal('15000.00'), total_payment=Decimal('0.00'),
            balance=Decimal('15000.00'), damage_cause='HIT A COW',
            ai_summary='A short AI read.', ai_suggestion='PAY', ai_reason='Balance covers it.',
        )
        data = build_claim_insight('g2026005213', currency='BWP')   # case-insensitive
        self.assertTrue(data['found'])
        self.assertEqual(data['balance'], '15000.00')
        self.assertEqual(data['ai_summary'], 'A short AI read.')
        self.assertEqual(data['ai_suggestion'], 'PAY')
        self.assertIn('COLDLINE (PTY) LTD', data['facts_text'])


class GraphiteSettledFlagTests(TestCase):
    """PAY-CLAIM-01 (CFO 2026-09-04, Leano Makwapa's report). Graphite posts a
    loss payment when Claims raises it — that is Graphite's word, not proof the
    bank paid. So a claim Graphite shows as settled while Omni holds NO request
    for it must read as an amber note routed to the committee, never a red
    "already paid" that a raiser takes for a refusal. Red is reserved for the
    case Omni itself already holds a live or paid request on the claim.
    Revert the graphite_shows_settled/omni_payments_for_claim split and
    test_settled_in_graphite_only_is_amber_not_red goes red."""

    def _settled(self, claim_number='G2026004646', **over):
        from integrations.models import GraphiteClaim
        base = dict(graphite_id=4646, claim_number=claim_number, customer_name='M M',
                    status='Closed', total_reserve=Decimal('1999.00'),
                    total_payment=Decimal('1999.00'), balance=Decimal('0.00'))
        base.update(over)
        return GraphiteClaim.objects.create(**base)

    def _omni_request(self, claim_number, status):
        from taskboard.models import PaymentRequest
        from django.contrib.auth.models import User
        u, _ = User.objects.get_or_create(username='raiser-x', defaults={'email': 'x@alphadirect.co.bw'})
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{status}', entity='Alpha Direct Insurance Company',
            subject='Shielders', payee='SHIELDERS BOTSWANA', currency='BWP', total='1999.00',
            line_items=[{'description': f'SHIELDERS {claim_number}', 'amount': '1999.00',
                         'claim_number': claim_number}],
            status=status, created_by=u)

    def test_closed_in_graphite_only_is_amber_and_names_the_committee(self):
        self._settled()                                     # status Closed
        data = build_claim_insight('G2026004646', line_amount=Decimal('1999.00'), currency='BWP')
        codes = {f['code']: f['level'] for f in data['flags']}
        self.assertEqual(codes.get('graphite_closed'), 'warning')
        self.assertNotIn('already_paid', codes)
        self.assertFalse(any(f['level'] == 'danger' for f in data['flags']))
        label = next(f['label'] for f in data['flags'] if f['code'] == 'graphite_closed')
        self.assertIn('Omni has no payment on record', label)
        self.assertIn('committee', label)

    def test_posted_on_an_open_claim_is_a_plain_note_not_red_not_committee(self):
        # The normal sequence: Claims posted the loss payment, Finance now pays
        # it (Leano's Randy Taukobong case: Pending, 378,477.01 posted).
        self._settled(status='Pending', total_reserve=Decimal('378477.01'),
                      total_payment=Decimal('378477.01'))
        data = build_claim_insight('G2026004646', line_amount=Decimal('376477.01'), currency='BWP')
        codes = {f['code']: f['level'] for f in data['flags']}
        self.assertEqual(codes.get('graphite_settled'), 'warning')
        self.assertNotIn('graphite_closed', codes)
        self.assertNotIn('already_paid', codes)
        label = next(f['label'] for f in data['flags'] if f['code'] == 'graphite_settled')
        self.assertNotIn('committee', label)
        self.assertIn('You can submit', label)

    def test_omni_already_paying_the_claim_is_amber_and_names_the_request(self):
        # CFO 2026-09-09: a heads-up, never a red refusal — the raiser can still
        # submit and the approver confirms. PAY-DUP-01 owns the true duplicate.
        self._settled()
        self._omni_request('G2026004646', 'paid')
        data = build_claim_insight('G2026004646', currency='BWP')
        red = [f for f in data['flags'] if f['code'] == 'already_paid']
        self.assertEqual(len(red), 1)
        self.assertEqual(red[0]['level'], 'warning')
        self.assertIn('PAY/TEST/paid', red[0]['label'])
        self.assertIn('can', red[0]['label'].lower())  # "you can submit"
        self.assertFalse(any(f['level'] == 'danger' for f in data['flags']))

    def test_same_claim_DIFFERENT_supplier_is_info_not_red(self):
        # Leano Makwapa, bug cbc07b0a (2026-09-07): one claim pays several
        # suppliers. Omni already holds SHIELDERS' request on the claim; loading
        # a SECOND supplier (CARFIL) must NOT paint a red "already paid" that
        # reads as a refusal — it is a different supplier, PAY-DUP-01 lets it
        # through soft. Drop the payee split and this goes red.
        self._settled()
        self._omni_request('G2026004646', 'paid')            # payee SHIELDERS BOTSWANA
        data = build_claim_insight('G2026004646', currency='BWP', payee='CARFIL SERVICES')
        codes = {f['code']: f['level'] for f in data['flags']}
        self.assertNotIn('already_paid', codes)
        self.assertFalse(any(f['level'] == 'danger' for f in data['flags']))
        self.assertEqual(codes.get('other_supplier_same_claim'), 'info')
        label = next(f['label'] for f in data['flags'] if f['code'] == 'other_supplier_same_claim')
        self.assertIn('SHIELDERS BOTSWANA', label)
        self.assertIn('You can submit', label)

    def test_same_claim_SAME_supplier_is_flagged_amber(self):
        # The same supplier back again on the same claim — flagged as a heads-up
        # (amber), never a red refusal (CFO 2026-09-09). Normalisation ignores
        # case/punctuation, so 'shielders  botswana' still matches.
        self._settled()
        self._omni_request('G2026004646', 'paid')            # payee SHIELDERS BOTSWANA
        data = build_claim_insight('G2026004646', currency='BWP', payee='shielders  botswana')
        red = [f for f in data['flags'] if f['code'] == 'already_paid']
        self.assertEqual(len(red), 1)
        self.assertEqual(red[0]['level'], 'warning')
        self.assertNotIn('other_supplier_same_claim', {f['code'] for f in data['flags']})

    def test_omni_paid_with_no_payee_given_is_flagged_amber(self):
        # With nothing to compare the supplier against, still flag it — as a
        # heads-up (amber), not a red refusal (CFO 2026-09-09). PAY-DUP-01 is
        # the real duplicate gate; this only informs.
        self._settled()
        self._omni_request('G2026004646', 'paid')
        data = build_claim_insight('G2026004646', currency='BWP')   # no payee
        codes = {f['code']: f['level'] for f in data['flags']}
        self.assertEqual(codes.get('already_paid'), 'warning')

    def test_rejected_cancelled_and_draft_requests_do_not_count_as_omni_paid(self):
        self._settled()
        for st in ('rejected', 'cancelled', 'draft'):
            self._omni_request('G2026004646', st)
        data = build_claim_insight('G2026004646', currency='BWP')
        codes = {f['code'] for f in data['flags']}
        self.assertIn('graphite_closed', codes)
        self.assertNotIn('already_paid', codes)

    def test_claim_exception_reason_fires_only_for_the_finished_no_omni_case(self):
        from integrations.claim_insight import claim_exception_reason
        self.assertEqual(claim_exception_reason('G0000001'), '')          # unknown claim
        self._settled('G2026004646')                                      # Closed
        self._settled('G2026004552', graphite_id=4552, status='Pending',
                      total_reserve=Decimal('378477.01'), total_payment=Decimal('378477.01'))
        # Open claim with the loss payment posted = the normal sequence, no exception.
        self.assertEqual(claim_exception_reason('G2026004552'), '')
        self._settled('G2026004999', graphite_id=4999, status='Repudiated',
                      total_payment=Decimal('0.00'), balance=Decimal('1999.00'))
        self.assertIn('Repudiated', claim_exception_reason('G2026004999'))
        reason = claim_exception_reason('G2026004646')
        self.assertIn('G2026004646', reason)
        self.assertIn('Closed', reason)
        self.assertIn('Omni has no payment on record', reason)
        self._omni_request('G2026004646', 'pending_cfo')
        self.assertEqual(claim_exception_reason('G2026004646'), '')       # Omni knows it: PAY-DUP-01's case

    def test_stale_mirror_is_flagged_as_info(self):
        from django.utils import timezone
        from datetime import timedelta
        self._settled(detail_synced_at=timezone.now() - timedelta(days=21))
        data = build_claim_insight('G2026004646', currency='BWP')
        stale = [f for f in data['flags'] if f['code'] == 'stale_mirror']
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['level'], 'info')
        self.assertIn('21 days ago', stale[0]['label'])


class PremiumGateFlagDBTests(TestCase):
    """Motor-Claims Gate 0 (Kago memo v2, 6-Sep-2026) — the RealPay-backed premium
    card surfaced by _hard_flags. The pure classifier is covered by
    test_premium_gate.py; THIS proves the ORM path: the client_number == policy_number
    join (space+dash normalised) and the coverage guard that must NOT paint a false
    lapse when the debit-order mirror does not reach the loss date. Drop the coverage
    guard and test_loss_after_data_end_is_unassessable_not_red goes red."""

    def setUp(self):
        from realpay.models import RealPayTransaction
        # Baseline row so the mirror's GLOBAL max reaches 31 Aug — a reasonably
        # current hand-loaded mirror. Each test adds its own policy rows.
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.TRANSACTION, txn_date=date(2026, 8, 31),
            client_number='OTHERPOL', current_status='SUCCESSFUL',
            installment_amount=Decimal('100.00'))

    def _claim_row(self, claim_number, policy_number, loss):
        from integrations.models import GraphiteClaim
        return GraphiteClaim.objects.create(
            graphite_id=1, claim_number=claim_number, customer_name='COLDLINE (PTY) LTD',
            is_company=True, policy_number=policy_number, product_name='Motor', status='open',
            date_of_loss=loss, total_reserve=Decimal('15000.00'),
            total_payment=Decimal('0.00'), balance=Decimal('15000.00'))

    def _debit(self, client_number, when, status):
        from realpay.models import RealPayTransaction
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.TRANSACTION, txn_date=when,
            client_number=client_number, current_status=status,
            installment_amount=Decimal('500.00'))

    def test_paid_for_period_of_loss_is_green_info(self):
        self._claim_row('G0001', 'POL123', date(2026, 7, 10))
        self._debit('POL 123', date(2026, 7, 1), 'SUCCESSFUL')   # spaced — tests the join normalisation
        codes = {f['code']: f['level'] for f in build_claim_insight('G0001', currency='BWP')['flags']}
        self.assertEqual(codes.get('premium_paid'), 'info')

    def test_current_debit_failed_is_amber_warning(self):
        self._claim_row('G0002', 'POL124', date(2026, 8, 20))
        self._debit('POL124', date(2026, 7, 1), 'SUCCESSFUL')
        self._debit('POL124', date(2026, 8, 1), 'FAILED')
        codes = {f['code']: f['level'] for f in build_claim_insight('G0002', currency='BWP')['flags']}
        self.assertEqual(codes.get('premium_arrears'), 'warning')

    def test_loss_after_data_end_is_unassessable_not_red(self):
        # Mirror reaches only 31 Aug (setUp); a 5 Sep loss cannot be assessed.
        # WITHOUT the coverage guard this classifies as premium_lapsed / danger.
        self._claim_row('G0003', 'POL125', date(2026, 9, 5))
        self._debit('POL125', date(2026, 7, 1), 'SUCCESSFUL')
        flags = build_claim_insight('G0003', currency='BWP')['flags']
        codes = {f['code']: f['level'] for f in flags}
        self.assertEqual(codes.get('premium_unassessable'), 'info')
        self.assertFalse(any(f['level'] == 'danger' for f in flags))
        self.assertNotIn('premium_lapsed', codes)

    def test_no_debit_rows_for_policy_gives_no_card(self):
        # Mirror reaches the loss date (setUp baseline), but this policy has no rows.
        self._claim_row('G0004', 'POL126', date(2026, 7, 10))
        flags = build_claim_insight('G0004', currency='BWP')['flags']
        self.assertFalse(any(f['code'].startswith('premium_') for f in flags))

    def test_premium_check_error_shows_an_amber_could_not_check_card(self):
        # A system error inside the gate must be VISIBLE (amber), never a silent
        # "no card" that reads as green — and it must NOT block (fail-open).
        self._claim_row('G0006', 'POL127', date(2026, 7, 10))
        self._debit('POL 127', date(2026, 7, 1), 'SUCCESSFUL')
        with mock.patch('integrations.claim_insight.classify_premium_gate',
                        side_effect=RuntimeError('boom')):
            codes = {f['code']: f['level']
                     for f in build_claim_insight('G0006', currency='BWP')['flags']}
            from integrations.claim_insight import premium_gate_exception_reason
            reason = premium_gate_exception_reason('G0006')
        self.assertEqual(codes.get('premium_unassessable'), 'warning')
        self.assertEqual(reason, '')   # an error is visible but never blocks
