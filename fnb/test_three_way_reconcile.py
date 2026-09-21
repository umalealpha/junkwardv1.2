"""Two changes the CFO asked for on 21-Sep-2026, on the morning's report of 23
disagreements: correct the statuses to match the bank, and stop the daily email
talking about anything over 7 days old.

The shapes here are the live ones measured that morning:
   5 cancelled in Omni / batch SETTLED     (the money had already gone)
   2 rejected or pending_cfo / SETTLED
  16 paid in Omni / batch FAILED           (the bank threw it out; no money moved)
   0 paid / batch unconfirmed
  ages 3d to 29d — only 5 of the 23 inside a 7-day window.
"""
import datetime
from decimal import Decimal
from unittest import mock
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from banking.models import BankAccount
from core.models import AuditLog
from fnb.models import FNBBatchSubmission
from fnb.three_way_check import contradictions, run
from fnb.three_way_check_email import build_html
from ledger.models import Account
from taskboard.models import OmniTask, PaymentRequest, PaymentRequestChange


class _Fixture(TestCase):

    def setUp(self):
        gl = Account.objects.create(code='280100', name='FNB Current Account',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345678',
            bank_name='FNB Botswana', gl_account=gl)
        self.who = User.objects.create_user('recon', 'recon@alphadirect.co.bw', 'x')

    def _batch(self, status, days_old=1, reason=''):
        b = FNBBatchSubmission.objects.create(
            idempotency_key=f'K-{status}-{FNBBatchSubmission.objects.count()}',
            source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal('1000.00'), status=status,
            failure_reason=reason, submitted_by=self.who)
        FNBBatchSubmission.objects.filter(pk=b.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=days_old))
        b.refresh_from_db()
        return b

    def _request(self, status, batch, total='1000.00', task=None):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{PaymentRequest.objects.count():04d}',
            entity='Alpha Direct Insurance', category='supplier',
            currency='BWP', subject='Test payment', payee='A Supplier',
            total=Decimal(total), status=status, fnb_batch=batch, task=task)

    def _reconcile(self, *args):
        out = StringIO()
        call_command('fnb_three_way_reconcile', *args, stdout=out, stderr=out)
        return out.getvalue()


class TheStatusIsCorrectedToWhatTheBankDid(_Fixture):

    def test_cancelled_in_omni_but_the_bank_settled_it_becomes_paid(self):
        """The money left the account. 'cancelled' is also the dangerous
        answer: payment_duplicates treats a cancelled request as dead, so the
        same invoice could be raised and paid a second time."""
        r = self._request('cancelled', self._batch('settled', days_old=20))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')

    def test_rejected_in_omni_but_the_bank_settled_it_becomes_paid(self):
        r = self._request('rejected', self._batch('settled', days_old=6))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')

    def test_paid_in_omni_but_the_bank_rejected_it_is_cleared(self):
        """No money moved, so Omni must stop saying paid. The CFO chose
        'cleared' on 21-Sep-2026 with the cost in front of him — see the
        command's docstring — so it lands on CANCELLED, carrying the reason
        the model requires of every cancel."""
        r = self._request('paid', self._batch('failed', days_old=16,
                                              reason='AC08: BRANCH CODE IS INVALID'))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'cancelled')
        self.assertIn('bank rejected', r.cancelled_reason)
        self.assertIsNotNone(r.cancelled_at)

    def test_a_cleared_payment_gets_a_row_in_the_immutable_change_log(self):
        """A cancel that only moves a status column is invisible in the one
        log an auditor reads."""
        r = self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon')
        change = r.changes.first()
        self.assertIsNotNone(change)
        self.assertEqual(change.action, PaymentRequestChange.Action.CANCEL_REQUEST)
        self.assertEqual(change.value_before, 'paid')
        self.assertEqual(change.value_after, 'cancelled')
        self.assertTrue(change.reason.strip())

    def test_a_note_a_person_wrote_is_kept_not_overwritten(self):
        """decision_notes holds a finance approver's own words. A machine
        correction that assigns over it destroys the record this command
        exists to preserve."""
        r = self._request('rejected', self._batch('settled', days_old=6))
        PaymentRequest.objects.filter(pk=r.pk).update(
            decision_notes='Finance: wrong invoice attached, do not pay.')
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertIn('wrong invoice attached', r.decision_notes)
        self.assertIn('corrected from', r.decision_notes)

    def test_a_long_existing_note_never_swallows_the_new_one(self):
        """Truncating from the right cuts off the line being ADDED, leaving a
        request whose status moved with no record of why."""
        from fnb.management.commands.fnb_three_way_reconcile import _append_note
        out = _append_note('x' * 1990, 'status corrected from "paid"')
        self.assertLessEqual(len(out), 2000)
        self.assertTrue(out.endswith('status corrected from "paid"'))
        self.assertTrue(out.startswith('\u2026'), 'say plainly that older text was dropped')

    def test_the_audit_log_carries_every_field_it_overwrote(self):
        r = self._request('rejected', self._batch('settled', days_old=6))
        PaymentRequest.objects.filter(pk=r.pk).update(decision_notes='Finance said no.')
        self._reconcile('--apply', '--as-user', 'recon')
        entry = AuditLog.objects.filter(record_id=str(r.id)).first()
        self.assertEqual(entry.old_values['decision_notes'], 'Finance said no.')

    def test_a_person_who_moved_it_first_is_not_trampled(self):
        """The rows are listed OUTSIDE the transaction. If somebody acts on
        one before the write lands, the command must stand down rather than
        apply a plan made before their decision."""
        r = self._request('paid', self._batch('failed', days_old=16))
        from fnb.management.commands.fnb_three_way_reconcile import _reconcile_one
        PaymentRequest.objects.filter(pk=r.pk).update(status='pending_cfo')
        self.assertIsNone(
            _reconcile_one(r.ref, 'cancelled', expected='paid', note='x'))
        r.refresh_from_db()
        self.assertEqual(r.status, 'pending_cfo')

    def test_apply_refuses_to_write_without_naming_a_person(self):
        """cancelled_by=None renders on the screen as "Cancelled by a removed
        account" — a false statement on every row. A correction this size is
        somebody's decision and has to name them."""
        r = self._request('paid', self._batch('failed', days_old=16))
        with self.assertRaises(CommandError):
            self._reconcile('--apply')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid', 'nothing may be written without a name')

    def test_a_dry_run_still_needs_no_name(self):
        self._request('paid', self._batch('failed', days_old=16))
        self.assertIn('Dry run', self._reconcile())

    def test_the_attribution_is_read_off_the_user_never_typed(self):
        r = self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon')
        change = r.changes.first()
        self.assertEqual(change.actor, self.who)
        self.assertEqual(change.actor_name, self.who.get_username())

    def test_the_decision_is_stamped_with_whose_it_was(self):
        r = self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon@alphadirect.co.bw')
        r.refresh_from_db()
        self.assertEqual(r.cancelled_by, self.who)

    def test_a_payment_a_person_already_ruled_a_duplicate_is_left_alone(self):
        """Measured on the first live run: PAY/ADIC/2026/09/14/0003 carried
        "Duplicate of PAY/ADIC/2026/09/11/0011 - PAYE only paid once. CFO
        2026-09-17." and the generic settled->paid rule flipped it, making Omni
        say BWP 132,042 of PAYE went out twice."""
        r = self._request('rejected', self._batch('settled', days_old=6))
        PaymentRequest.objects.filter(pk=r.pk).update(
            decision_notes='Duplicate of PAY/ADIC/2026/09/11/0011 - '
                           'PAYE only paid once. CFO 2026-09-17.')
        out = self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'rejected',
                         "a machine rule must not overturn a person's ruling")
        self.assertIn('LEFT ALONE', out)

    def test_the_cancel_that_stops_a_second_payment_survives(self):
        r = self._request('cancelled', self._batch('settled', days_old=11))
        PaymentRequest.objects.filter(pk=r.pk).update(
            decision_notes='Cleared to prevent a second bank payment to the '
                           'custodian; paid once through petty cash.')
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'cancelled')

    def test_an_ordinary_note_does_not_block_the_correction(self):
        """The guard must catch a duplicate ruling, not every note ever
        written — a guard that blocks everything corrects nothing."""
        r = self._request('cancelled', self._batch('settled', days_old=20))
        PaymentRequest.objects.filter(pk=r.pk).update(
            decision_notes='Invoice attached, approved by Finance.')
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')

    def test_our_own_stamp_never_becomes_a_ruling_that_blocks_a_re_run(self):
        r = self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'cancelled')
        from fnb.management.commands.fnb_three_way_reconcile import _prior_ruling
        self.assertEqual(_prior_ruling(r.ref), '',
                         'the line this command writes is not a human ruling')

    def test_the_banks_own_words_never_become_a_fake_human_ruling(self):
        """FNB reject code AM05 reads "the bank treated this as a DUPLICATE of
        a payment it has already received". The reason text is raw and can
        carry a newline — which would split the stamped line and leave FNB's
        words sitting unstamped in the notes, where the guard reads them back
        as somebody's ruling and freezes the row for ever."""
        from fnb.management.commands.fnb_three_way_reconcile import _prior_ruling
        r = self._request('paid', self._batch(
            'failed', days_old=16,
            reason='AM05: DUPLICATION\nThe bank treated this as a duplicate '
                   'of a payment it has already received.'))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'cancelled')
        self.assertEqual(_prior_ruling(r.ref), '',
                         "the bank's own text is not a person's ruling")

    def test_a_later_run_under_a_new_instruction_still_ignores_our_own_lines(self):
        """ON_WHOSE_INSTRUCTION is dated and WILL change next time somebody
        runs this. When it does, every line stamped under the old wording must
        still be recognised as ours — otherwise the command starts reading its
        own history as human rulings."""
        from fnb.management.commands import fnb_three_way_reconcile as mod
        r = self._request('paid', self._batch(
            'failed', days_old=16, reason='AM05: duplicate payment received'))
        self._reconcile('--apply', '--as-user', 'recon')

        # What the module looks like on the NEXT release, when somebody edits
        # the constant for a different instruction: the tuple is rebuilt at
        # import with the new wording, and every line stamped under the OLD
        # wording is suddenly unrecognised — unless the structural marker is
        # in there too. Patching the tuple is the only faithful simulation;
        # patching ON_WHOSE_INSTRUCTION alone leaves the already-built tuple
        # holding the old string, so the test would pass either way.
        next_release = tuple(m for m in mod._OUR_OWN_STAMP
                             if m != mod.ON_WHOSE_INSTRUCTION) + (
                                 'On the CFO\'s instruction, 14-Oct-2026: "do it again".',)
        with mock.patch.object(mod, '_OUR_OWN_STAMP', next_release):
            self.assertEqual(mod._prior_ruling(r.ref), '',
                             'our own past line must not become a ruling '
                             'just because the instruction wording moved on')

    def test_a_note_saying_it_is_NOT_a_duplicate_does_not_block(self):
        """A guard that blocks a correction it should have made leaves a
        settled payment on `cancelled`, out of PAY-DUP-01 — the same failure
        in the other direction."""
        r = self._request('cancelled', self._batch('settled', days_old=20))
        PaymentRequest.objects.filter(pk=r.pk).update(
            decision_notes='Confirmed this is NOT a duplicate of '
                           'PAY/ADIC/2026/09/11/0011. Safe to pay.')
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')

    def test_an_unconfirmed_batch_is_never_guessed_at(self):
        """The bank has not answered, so the money MAY have moved. Touching
        this either way is how a payment gets made twice."""
        r = self._request('paid', self._batch('submitted', days_old=9))
        out = self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid', 'an unanswered batch must be left alone')
        self.assertIn('LEFT ALONE', out)

    def test_the_before_value_survives_in_the_audit_log(self):
        r = self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon')
        entry = AuditLog.objects.filter(record_id=str(r.id)).first()
        self.assertIsNotNone(entry, 'a status correction with no trail is not reversible')
        self.assertEqual(entry.old_values['status'], 'paid')
        self.assertEqual(entry.new_values['status'], 'cancelled')

    def test_the_request_says_on_its_own_face_why_it_moved(self):
        r = self._request('cancelled', self._batch('settled', days_old=20))
        self._reconcile('--apply', '--as-user', 'recon')
        r.refresh_from_db()
        self.assertIn('cancelled', r.decision_notes)
        self.assertIn('paid', r.decision_notes)

    def test_a_dry_run_writes_nothing(self):
        r = self._request('paid', self._batch('failed', days_old=16))
        out = self._reconcile()
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')
        self.assertEqual(AuditLog.objects.filter(record_id=str(r.id)).count(), 0)
        self.assertIn('Dry run', out)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self._request('paid', self._batch('failed', days_old=16))
        self._reconcile('--apply', '--as-user', 'recon')
        out = self._reconcile('--apply', '--as-user', 'recon')
        self.assertIn('0 payment request(s) changed', out)

    def test_an_open_task_on_a_now_finished_payment_is_closed(self):
        t = OmniTask.objects.create(
            assigner=self.who, assignee=self.who, title='Authorise payment',
            status=OmniTask.Status.PENDING, source='payment_request')
        r = self._request('cancelled', self._batch('settled', days_old=20), task=t)
        self._reconcile('--apply', '--as-user', 'recon')
        t.refresh_from_db()
        r.refresh_from_db()
        self.assertEqual(r.status, 'paid')
        self.assertEqual(t.status, OmniTask.Status.CANCELLED,
                         'a terminal payment must not leave somebody a live task')

    def test_afterwards_there_is_nothing_left_to_report(self):
        """The CFO's actual ask: the next morning's email reads clean."""
        self._request('cancelled', self._batch('settled', days_old=20))
        self._request('rejected', self._batch('settled', days_old=6))
        self._request('paid', self._batch('failed', days_old=16))
        self.assertEqual(sum(len(v) for v in contradictions().values()), 3)
        self._reconcile('--apply', '--as-user', 'recon')
        self.assertEqual(sum(len(v) for v in contradictions().values()), 0)
        self.assertTrue(run()['clean'])


class TheEmailIsSilentOnAnythingOlderThanTheWindow(_Fixture):

    def test_an_old_disagreement_is_not_counted(self):
        self._request('paid', self._batch('failed', days_old=16))
        self.assertEqual(run(max_age_days=7)['contradiction_count'], 0)

    def test_a_recent_disagreement_still_is(self):
        self._request('paid', self._batch('failed', days_old=3))
        self.assertEqual(run(max_age_days=7)['contradiction_count'], 1)

    def test_the_edge_agrees_with_the_age_column_the_email_prints(self):
        """A batch 7.5 days old prints as '7d'. Cutting at now-7d would drop a
        row the same email calls 7 days old — a report contradicting itself."""
        b = self._batch('failed', days_old=7)
        FNBBatchSubmission.objects.filter(pk=b.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=7, hours=12))
        self._request('paid', b)
        res = run(max_age_days=7)
        self.assertEqual(res['contradiction_count'], 1)
        self.assertEqual(res['contradictions']['paid_but_batch_failed'][0]['age_days'], 7)

    def test_nothing_older_reaches_the_html_at_all(self):
        """'Strictly nothing older than 7 days' — not the reference, not a
        count, not a footnote (CFO's choice, 21-Sep-2026)."""
        old = self._request('paid', self._batch('failed', days_old=16))
        html = build_html(run(max_age_days=7))
        self.assertNotIn(old.ref, html)
        self.assertIn('last 7 days only', html)

    def test_the_over_n_days_column_goes_when_the_window_is_on(self):
        """Inside a 7-day window, 'Over 7 days' can only describe a sliver
        while reading like the whole backlog."""
        self._request('paid', self._batch('failed', days_old=3))
        self.assertNotIn('Over 7 days', build_html(run(max_age_days=7)))
        self.assertIn('Over 7 days', build_html(run()))

    def test_the_screen_is_not_blinded_by_the_email_s_window(self):
        """The cap belongs to the email. If it reached the exception cockpit
        too, an unfixed problem would age out of Omni altogether and nobody
        would ever see it again."""
        self._request('paid', self._batch('failed', days_old=16))
        self.assertEqual(run()['contradiction_count'], 1)

    def test_the_log_still_says_what_the_window_left_out(self):
        """The email is silent; the run log must not be, or a dropped problem
        and a broken job look identical."""
        self._request('paid', self._batch('failed', days_old=16))
        self.assertEqual(run(max_age_days=7)['excluded_older'], 1)
