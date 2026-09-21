"""The FNB incremental reject controls (CFO brief, 17-Sep-2026).

Every shape here was measured on production on 17-Sep-2026, on the 23 failed
batches holding BWP 677,284.93:

  16 payment requests marked PAID   with a rejected batch behind them
   2 marked CANCELLED                with a rejected batch behind them
   1 marked PENDING_CFO              with a rejected batch behind it
   1 supplier request (Choppies, BWP 13,560.02) split into FOUR individual FNB
     instructions, all four rejected AG01 — and the request's single fnb_batch
     FK pointed at one of them, so three were invisible to the cockpit
 863 bank notifications stuck on 'received', none of them a rejection

🔴 Nothing in these controls blocks a payment. CFO, 17-Sep-2026: *"you will
never block a payment, if there is a blocker the exception committee kicks
in."* The tests below assert that directly — a human is never stopped.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from banking.models import BankAccount
from fnb.models import FNBBatchSubmission, FNBWebhookEvent
from fnb.reject_codes import KNOWN_REASONS, describe_rejection
from fnb.three_way_check import notification_health, rejected_but_request_not_open
from ledger.models import Account
from taskboard.models import PaymentRequest
from taskboard.payment_views import _batches_all_rejected, _mark_paid_from_bank


class _Fixture(TestCase):

    def setUp(self):
        gl = Account.objects.create(code='280100', name='FNB Current Account',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345678',
            bank_name='FNB Botswana', gl_account=gl)
        self.who = User.objects.create_user('fin2', 'fin2@alphadirect.co.bw', 'x')

    def _batch(self, status, request=None, amount='1000.00', reason='',
               days_old=1, key=None):
        b = FNBBatchSubmission.objects.create(
            idempotency_key=key or f'K-{status}-{FNBBatchSubmission.objects.count()}',
            source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal(amount), status=status,
            failure_reason=reason, submitted_by=self.who,
            payment_request=request)
        FNBBatchSubmission.objects.filter(pk=b.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=days_old))
        b.refresh_from_db()
        return b

    def _request(self, status, total='1000.00', method='bulk', notes=''):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{PaymentRequest.objects.count():04d}',
            entity='Alpha Direct Insurance', category='supplier',
            currency='BWP', subject='Test payment', payee='A Supplier',
            total=Decimal(total), status=status, processing_method=method,
            decision_notes=notes)


class SplitPaymentLineageTests(_Fixture):
    """Control 4 — one business problem, not four unrelated failures."""

    def test_four_instructions_from_one_request_group_into_one_row(self):
        # The live Choppies case: BWP 13,560.02 as four AG01 rejects.
        pr = self._request('paid', total='13560.02', method='individual')
        for amount, key in (('2963.46', 'CHOPPIES 290 (O)'),
                            ('3502.69', 'CHOPPIES 289 (O)'),
                            ('3319.48', 'CHOPPIES 288 (O)'),
                            ('3774.39', 'CHOPPIES 287 (O)')):
            self._batch('failed', request=pr, amount=amount, key=key,
                        reason='AG01: TRANSACTION FORBIDDEN')

        out = rejected_but_request_not_open()
        self.assertEqual(out['count'], 1, 'four rejects on one request is ONE problem')
        self.assertEqual(out['instruction_count'], 4, 'all four instructions must show')
        self.assertEqual(out['rows'][0]['ref'], pr.ref)
        self.assertEqual(out['rows'][0]['bank_reasons'], ['AG01'])

    def test_the_old_single_fk_would_have_shown_only_one_of_the_four(self):
        # Proves WHY the stamp was needed. contradictions() reads the request's
        # own fnb_batch FK, which can hold exactly one batch.
        from fnb.three_way_check import contradictions
        pr = self._request('paid', total='13560.02', method='individual')
        batches = [self._batch('failed', request=pr, amount='3390.00',
                               key=f'SPLIT-{i}', reason='AG01: TRANSACTION FORBIDDEN')
                   for i in range(4)]
        PaymentRequest.objects.filter(pk=pr.pk).update(fnb_batch=batches[0])

        old_view = contradictions()['paid_but_batch_failed']
        self.assertEqual(len(old_view), 1)
        self.assertEqual(old_view[0]['batch_key'], 'SPLIT-0',
                         'the single FK sees one batch — the other three are '
                         'why the new stamp exists')
        self.assertEqual(rejected_but_request_not_open()['instruction_count'], 4)


class RejectedButRequestNotOpenTests(_Fixture):
    """Control 8 — the read-only reconciliation the CFO asked to see first."""

    def test_it_lists_paid_pending_cfo_and_cancelled(self):
        for status in ('paid', 'pending_cfo', 'cancelled'):
            pr = self._request(status)
            self._batch('failed', request=pr, reason='AC08: BRANCH CODE IS INVALID')
        out = rejected_but_request_not_open()
        self.assertEqual(out['count'], 3)
        self.assertEqual({r['omni_status'] for r in out['rows']},
                         {'paid', 'pending_cfo', 'cancelled'})

    def test_an_unknown_batch_counts_too(self):
        # 'unknown' means the POST left and no clean answer came back — the
        # money MAY have moved. A request closed against one is exactly as
        # wrong as one closed against a rejection.
        pr = self._request('paid')
        self._batch('unknown', request=pr)
        self.assertEqual(rejected_but_request_not_open()['count'], 1)

    def test_an_open_request_is_not_a_finding(self):
        for status in ('pending_finance', 'draft', 'exception'):
            pr = self._request(status)
            self._batch('failed', request=pr)
        self.assertEqual(rejected_but_request_not_open()['count'], 0,
                         'an open request is already actionable — not a finding')

    def test_a_settled_batch_is_not_a_finding(self):
        pr = self._request('paid')
        self._batch('settled', request=pr)
        self.assertEqual(rejected_but_request_not_open()['count'], 0)

    def test_it_changes_nothing(self):
        pr = self._request('paid')
        self._batch('failed', request=pr)
        rejected_but_request_not_open()
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'paid',
                         'the reconciliation is READ ONLY — a person decides')

    def test_it_says_whether_a_human_recorded_evidence(self):
        pr = self._request('paid', notes='Paid by cheque 4471, proof attached.')
        self._batch('failed', request=pr)
        self.assertTrue(rejected_but_request_not_open()['rows'][0]['evidence_recorded'])

    def test_no_evidence_recorded_is_reported_as_such(self):
        pr = self._request('paid')
        self._batch('failed', request=pr)
        self.assertFalse(rejected_but_request_not_open()['rows'][0]['evidence_recorded'])


class NeverPaidJustBecauseABatchExistsTests(_Fixture):
    """Control 3 — and the proof that no human is ever blocked."""

    def test_all_batches_rejected_is_detected_through_the_new_stamp(self):
        pr = self._request('pending_cfo')
        self._batch('failed', request=pr)
        self._batch('unknown', request=pr, key='U-1')
        self.assertTrue(_batches_all_rejected(pr))

    def test_one_settled_batch_means_money_did_move(self):
        pr = self._request('pending_cfo')
        self._batch('failed', request=pr)
        self._batch('settled', request=pr, key='S-1')
        self.assertFalse(_batches_all_rejected(pr))

    def test_no_batch_at_all_is_not_a_rejection(self):
        # A payment made outside the FNB pipe has no batch. A guard that fired
        # on absence would stop every one of those closing.
        pr = self._request('pending_cfo')
        self.assertFalse(_batches_all_rejected(pr))

    def test_the_old_single_fk_is_still_read(self):
        # Batches loaded before the stamp existed carry no payment_request.
        pr = self._request('pending_cfo')
        b = self._batch('failed')
        PaymentRequest.objects.filter(pk=pr.pk).update(fnb_batch=b)
        pr.refresh_from_db()
        self.assertTrue(_batches_all_rejected(pr))

    def test_the_automatic_email_close_refuses_a_rejected_batch(self):
        pr = self._request('pending_cfo')
        self._batch('failed', request=pr, reason='AC08: BRANCH CODE IS INVALID')
        out = _mark_paid_from_bank(pr, self.who, 'FNB said fully processed',
                                   automatic=True)
        self.assertIsNone(out)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'pending_cfo',
                         'it must stay OPEN and actionable, not close as paid')

    def test_a_person_is_never_blocked(self):
        # 🔴 The CFO's rule. The same request, closed by a HUMAN caller
        # (automatic defaults to False), goes through untouched.
        pr = self._request('pending_cfo')
        self._batch('failed', request=pr, reason='AC08: BRANCH CODE IS INVALID')
        out = _mark_paid_from_bank(pr, self.who, 'Paid by cheque — proof filed.')
        self.assertIsNotNone(out)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'paid')

    def test_the_automatic_close_still_works_on_a_settled_batch(self):
        pr = self._request('pending_cfo')
        self._batch('settled', request=pr)
        self.assertIsNotNone(
            _mark_paid_from_bank(pr, self.who, 'FNB fully processed',
                                 automatic=True))


class WebhookBacklogTests(_Fixture):
    """Control 6 — the alert has to be able to go green again."""

    def _event(self, status, days_ago):
        e = FNBWebhookEvent.objects.create(
            event_type='camt054_CRDT_RCDT', status=status,
            external_id=f'E{FNBWebhookEvent.objects.count():05d}')
        FNBWebhookEvent.objects.filter(pk=e.pk).update(
            received_at=timezone.now() - datetime.timedelta(days=days_ago))
        return e

    def test_a_bare_date_still_works(self):
        # Backwards compatible: a plain date means midnight UTC.
        with override_settings(FNB_WEBHOOK_BACKLOG_BEFORE='2026-09-14'):
            self.assertIsInstance(notification_health()['backlog'], int)

    def test_the_cutoff_is_a_TIME_not_just_a_date(self):
        # 🔴 The pile's newest event is 14-Sep 10:30 UTC and processing began at
        # 11:00. A midnight cutoff leaves that morning's backlog counting as a
        # LIVE stall, so the alert can never read zero — an alarm that is always
        # on is an alarm nobody reads, which is what this split exists to fix.
        from fnb.three_way_check import _backlog_before
        self.assertEqual(_backlog_before().hour, 11,
                         'the default cutoff must be 11:00 UTC on 14-Sep, not '
                         'midnight')

    @override_settings(FNB_WEBHOOK_BACKLOG_BEFORE='2026-09-14T11:00:00+00:00')
    def test_the_historic_pile_is_counted_separately_from_the_live_alert(self):
        # Two events from long before the cutoff (the 863) and none after.
        self._event('received', days_ago=400)
        self._event('received', days_ago=380)
        h = notification_health()
        self.assertEqual(h['stuck'], 0,
                         'the live alert must be able to read zero, or nobody '
                         'will ever look at it again')
        self.assertEqual(h['backlog'], 2)
        self.assertTrue(h['backlog_note'])

    @override_settings(FNB_WEBHOOK_BACKLOG_BEFORE='2000-01-01')
    def test_a_new_stall_still_raises_the_alert(self):
        self._event('received', days_ago=1)
        self.assertEqual(notification_health()['stuck'], 1)

    @override_settings(FNB_WEBHOOK_BACKLOG_BEFORE='2026-09-14')
    def test_a_processed_event_is_never_counted(self):
        self._event('processed', days_ago=400)
        h = notification_health()
        self.assertEqual((h['stuck'], h['backlog']), (0, 0))

    def test_a_broken_setting_falls_back_instead_of_crashing(self):
        with override_settings(FNB_WEBHOOK_BACKLOG_BEFORE='not-a-date'):
            self.assertIsInstance(notification_health()['backlog'], int)


class CancelledBeforeSubmissionCountsAsDeadTests(_Fixture):
    """Fable 5.1, 17-Sep-2026: 'cancelled' means the instruction never left us."""

    def test_a_cancelled_batch_means_the_money_did_not_move(self):
        pr = self._request('pending_cfo')
        self._batch('cancelled', request=pr)
        self.assertTrue(_batches_all_rejected(pr))

    def test_the_automatic_close_refuses_a_cancelled_batch(self):
        pr = self._request('pending_cfo')
        self._batch('cancelled', request=pr)
        self.assertIsNone(_mark_paid_from_bank(pr, self.who, 'FNB said processed',
                                               automatic=True))
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'pending_cfo')


class TheDailyEmailCarriesTheReconciliationTests(_Fixture):
    """Control 8 has to REACH him. A report only on a screen is a report nobody
    reads — and before this, a day with zero contradictions and sixteen
    rejected-but-closed payments sent no email at all."""

    def test_a_rejected_but_closed_payment_breaks_the_silence(self):
        from fnb.three_way_check import run
        from fnb.three_way_check_email import build_html, send_report

        pr = self._request('paid')
        self._batch('failed', request=pr, reason='AC08: BRANCH CODE IS INVALID')
        res = run()
        self.assertTrue(res['clean'], 'no contradictions — the old quiet day')
        self.assertEqual(res['rejected_not_open']['count'], 1)

        out = send_report(res, only_when_dirty=True)
        self.assertNotEqual(out.get('reason'), 'nothing disagrees today',
                            'a day with a rejected-but-closed payment is not quiet')

    def test_the_email_body_names_them(self):
        from fnb.three_way_check import run
        from fnb.three_way_check_email import build_html

        pr = self._request('paid')
        self._batch('failed', request=pr, reason='AC08: BRANCH CODE IS INVALID')
        html = build_html(run())
        self.assertIn(pr.ref, html)
        self.assertIn('closed in Omni', html)

    def test_a_genuinely_quiet_day_still_sends_nothing(self):
        from fnb.three_way_check import run
        from fnb.three_way_check_email import send_report
        self.assertEqual(send_report(run(), only_when_dirty=True).get('reason'),
                         'nothing disagrees today')


class RejectCodeWordingTests(TestCase):
    """Control 5 — the bank's real codes, in the bank's own words."""

    def test_the_four_missing_codes_are_now_in_the_table(self):
        for code in ('AC08', 'AC03', 'RR09', 'FF10'):
            self.assertIn(code, KNOWN_REASONS)

    def test_ac08_says_branch_code_not_account_number(self):
        # THE BUG THIS FIXES. With AC08 absent, the AI fallback wrote a
        # different explanation every time and the commonest one said "the
        # account number does not exist". FNB's own text on the same row reads
        # "AC08: BRANCH CODE IS INVALID OR MISSING". Ten rejects worth
        # BWP 677,284.93 were read as an account problem, so nobody ever fixed
        # a branch code.
        plain = KNOWN_REASONS['AC08']['plain'].lower()
        self.assertIn('branch code', plain)
        self.assertIn('not the account number', plain)

    def test_ac08_explains_the_leading_zero(self):
        self.assertIn('064967', KNOWN_REASONS['AC08']['action'])

    def test_ff10_is_the_banks_fault_not_ours(self):
        self.assertEqual(KNOWN_REASONS['FF10']['ours'], 'no')

    def test_ag01_still_takes_no_side(self):
        # Four AG01 rejects on production, all on FNB's own branch with a
        # normal account type — nothing in the instruction was wrong. Claiming
        # a side here is the RR10 mistake, which cost 50 days.
        self.assertEqual(KNOWN_REASONS['AG01']['ours'], 'unknown')

    def test_describe_rejection_uses_the_table_not_the_ai(self):
        # use_ai=True on purpose: with AC08 in the table the AI must never be
        # reached, so the sentence carries no "[AI reading]" marker. That
        # marker is exactly what staff saw on every AC08 before today.
        out = describe_rejection('RJCT', [{'reason': 'AC08'}], use_ai=True)
        self.assertIn('branch code', out.lower())
        self.assertNotIn('AI reading', out)
        self.assertIn('ours to fix', out)
