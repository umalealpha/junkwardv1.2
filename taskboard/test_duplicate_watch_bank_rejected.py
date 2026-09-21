"""PAY-DUP-01 must keep watching money the bank never paid.

Measured on production 21-Sep-2026. Sixteen payment requests, BWP 553,301.24,
were cleared on the CFO's instruction after FNB rejected every instruction
behind them — eight of them AC08, a wrong or missing branch code. The two
largest were 231,840.00 and 97,348.00. The suppliers are not named here: unlike
`test_payment_duplicates`, where the real 3-Aug rows ARE the evidence, nothing
in this file turns on who was being paid, so the counterparties are synthetic.

Clearing them was his decision, taken with the cost of it in front of him. But
`DEAD_STATUSES` drops every cancelled request out of the duplicate control, so
the moment they were cleared the control stopped watching half a million pula
of invoices that suppliers will send again. Pay one by hand, let the invoice be
re-raised, and nothing clashes — which is the shape of the BWP 399,000 that was
paid twice.

The rule is derived, never a list: a CLOSED request whose bank instruction came
back failed or unknown, with nothing settled behind it, is money that never
moved and is therefore still owed.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from banking.models import BankAccount
from fnb.models import FNBBatchSubmission
from ledger.models import Account
from taskboard.models import PaymentRequest
from taskboard.payment_duplicates import exclude_dead, find_duplicates


def _L(description, amount, **kw):
    return {'description': description, 'amount': amount, **kw}


class MoneyTheBankNeverPaidStaysWatched(TestCase):

    def setUp(self):
        gl = Account.objects.create(code='280100', name='FNB Current Account',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345678',
            bank_name='FNB Botswana', gl_account=gl)
        self.who = User.objects.create_user('dupwatch', 'dw@alphadirect.co.bw', 'x')
        # The real rejection that produced eight of the sixteen.
        self.AC08 = ('AC08: BRANCH CODE IS INVALID OR MISSING — The bank '
                     'rejected it. No money moved.')

    def _batch(self, status, request=None, reason='', link=True):
        b = FNBBatchSubmission.objects.create(
            idempotency_key=f'K-{status}-{FNBBatchSubmission.objects.count()}',
            source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal('231840.00'), status=status,
            failure_reason=reason, submitted_by=self.who,
            payment_request=request if link else None)
        return b

    def _request(self, status, *, batch=None, lines=None):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{PaymentRequest.objects.count():04d}',
            entity='Alpha Direct Insurance', category='supplier',
            currency='BWP', subject='A SUPPLIER',
            payee='A SUPPLIER',
            total=Decimal('231840.00'), status=status, fnb_batch=batch,
            line_items=lines or [_L('G2099000001 A SUPPLIER', '231840.00')])

    def _clash(self):
        return find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')], currency='BWP')['hard']

    # ── the sixteen ──────────────────────────────────────────────────────────

    def test_a_cleared_request_the_bank_rejected_still_clashes(self):
        """The live case: cleared on the CFO's instruction, never paid, and the
        supplier's invoice comes in again."""
        r = self._request('cancelled', batch=self._batch('failed', reason=self.AC08))
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertTrue(hard, 'money the bank never paid must still be watched')
        self.assertIn(r.ref, str(hard))

    def test_the_same_holds_when_the_bank_never_answered(self):
        """'unknown' means the money MAY have moved — all the more reason a
        second attempt is looked at by a person."""
        self._request('cancelled', batch=self._batch('unknown'))
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertTrue(hard)

    def test_a_split_request_is_seen_through_the_reverse_stamp_too(self):
        """A request processed line by line produces one batch per line, and
        the single `fnb_batch` FK holds only one of them."""
        r = self._request('cancelled')
        self._batch('failed', request=r, reason=self.AC08)
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertTrue(hard)

    # ── and the boundaries, so the widening does not swallow everything ──────

    def test_a_cleared_request_the_bank_DID_pay_stays_out(self):
        """It was paid and then closed. The money is gone; a later invoice for
        the same thing is a different question and not this control's."""
        self._request('cancelled', batch=self._batch('settled'))
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertFalse(hard)

    def test_one_line_settling_takes_the_whole_request_back_out(self):
        """Some lines rejected, one settled: money DID move on this request, so
        it is not the untouched-and-still-owed case."""
        r = self._request('cancelled')
        self._batch('failed', request=r, reason=self.AC08)
        self._batch('settled', request=r)
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertFalse(hard)

    def test_a_cleared_request_with_no_batch_at_all_stays_out(self):
        """"Nothing was loaded" is not "the bank said no". Treating absence as
        a rejection would drag every payment made outside the FNB pipe back
        into the control."""
        self._request('cancelled')
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertFalse(hard)

    def test_an_ordinary_live_request_is_unaffected(self):
        """The change must not alter what the control already did."""
        self._request('pending_cfo')
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertTrue(hard)

    def test_a_draft_is_still_never_a_duplicate(self):
        self._request('draft', batch=self._batch('failed', reason=self.AC08))
        hard = find_duplicates(
            [_L('G2099000001 A SUPPLIER', '231840.00')],
            currency='BWP')['hard']
        self.assertFalse(hard)

    # ── the two callers must agree ───────────────────────────────────────────

    def test_the_same_loader_check_sees_it_too(self):
        """The OTHER caller of this rule — "you loaded this same amount in the
        last 48 hours" — must agree. Exercised through its real behaviour, not
        by reading its source: a test that greps for a function name passes
        just as happily when the call does nothing.
        """
        from taskboard.payment_views import _same_loader_recent_same_amount

        r = self._request('cancelled', batch=self._batch('failed', reason=self.AC08))
        PaymentRequest.objects.filter(pk=r.pk).update(created_by=self.who)

        clashes = _same_loader_recent_same_amount(
            [_L('A second attempt at the same thing', '231840.00')],
            currency='BWP', user=self.who)
        self.assertTrue(clashes,
                        'the loader warning must still fire for money the bank '
                        'never paid')
        self.assertEqual(clashes[0]['clash_ref'], r.ref)

    def test_the_same_loader_check_still_ignores_a_genuinely_dead_request(self):
        """The boundary on that same caller: a cancelled request the bank DID
        pay stays out, so the widening has not simply disabled the filter."""
        from taskboard.payment_views import _same_loader_recent_same_amount

        r = self._request('cancelled', batch=self._batch('settled'))
        PaymentRequest.objects.filter(pk=r.pk).update(created_by=self.who)

        self.assertEqual(
            _same_loader_recent_same_amount(
                [_L('A second attempt at the same thing', '231840.00')],
                currency='BWP', user=self.who),
            [])

    def test_exclude_dead_keeps_it(self):
        """The helper itself, directly. The two tests above are what prove the
        two CALLERS use it; this one only pins the helper."""
        r = self._request('cancelled', batch=self._batch('failed', reason=self.AC08))
        kept = exclude_dead(PaymentRequest.objects.all()).values_list('ref', flat=True)
        self.assertIn(r.ref, list(kept))

    # ── fix 1: cancelled-before-submission is money that did not move ────────

    def test_a_batch_cancelled_before_it_was_ever_sent_still_clashes(self):
        """fnb/payments.py sets a batch to CANCELLED when FNB is unconfigured
        or auth fails — the instruction never left us, so the money certainly
        did not move and the supplier is still owed. `_batches_all_rejected`
        already counted this (Fable 5.1, 17-Sep-2026); this rule did not, and
        silently dropped every one of them."""
        self._request('cancelled', batch=self._batch('cancelled'))
        self.assertTrue(self._clash())

    def test_the_same_through_the_reverse_stamp(self):
        r = self._request('cancelled')
        self._batch('cancelled', request=r)
        self.assertTrue(self._clash())

    # ── fix 4: the REJECTED half of DEAD_STATUSES, which had no coverage ────

    def test_a_rejected_request_the_bank_never_paid_also_clashes(self):
        """`rejected` carries a person's "do not pay" — but if the bank had
        already thrown the instruction out, the supplier is still owed and the
        invoice comes back. Clashing is the safe direction: it ROUTES to the
        committee, it does not block. G2026004718 was rejected and then paid
        three times."""
        self._request('rejected', batch=self._batch('failed', reason=self.AC08))
        self.assertTrue(self._clash())

    def test_a_rejected_request_with_no_batch_stays_out(self):
        """Rejected at finance sign-off before it ever reached the bank is a
        clean "do not pay" and the re-raise flow is legitimate."""
        self._request('rejected')
        self.assertFalse(self._clash())

    # ── fix 5: the settled leg reads both sources, like the dead leg ────────

    def test_a_settled_fk_batch_takes_it_back_out_even_if_another_failed(self):
        """Money DID move on this request. Reading the settled leg only through
        the reverse stamp would have kept it in."""
        r = self._request('cancelled', batch=self._batch('settled'))
        self._batch('failed', request=r, reason=self.AC08)
        self.assertFalse(self._clash())

    # ── fix 2: what the committee actually reads ────────────────────────────

    def test_the_committee_is_not_told_it_was_already_raised_or_paid(self):
        """The message six people receive. For this case the old wording said
        "already raised or paid ... (cancelled)" and told them to use "Clear
        from queue" on a request that was already cleared — a headline
        contradicting its own evidence, pointing at a door that is not there."""
        from taskboard.payment_duplicates import blocking_message
        hard = [{'line': 1, 'label': 'A SUPPLIER', 'amount': '231840.00',
                 'clash_kind': 'existing_request', 'clash_ref': 'PAY/TEST/0000',
                 'clash_status': 'cancelled', 'detail': 'clashes with PAY/TEST/0000'}]
        msg = blocking_message(hard, currency='BWP',
                               total_hard=Decimal('231840.00'), context='authorise')
        self.assertNotIn('already raised or paid', msg)
        self.assertIn('BANK REJECTED', msg)
        self.assertIn('paid by hand outside Omni', msg)
        self.assertNotIn('(cancelled)', msg)

    def test_an_ordinary_paid_clash_still_reads_the_old_way(self):
        """The new branch must not swallow the case the message already had."""
        from taskboard.payment_duplicates import blocking_message
        hard = [{'line': 1, 'label': 'A SUPPLIER', 'amount': '100.00',
                 'clash_kind': 'existing_request', 'clash_ref': 'PAY/TEST/0001',
                 'clash_status': 'paid', 'detail': 'clashes with PAY/TEST/0001'}]
        msg = blocking_message(hard, currency='BWP',
                               total_hard=Decimal('100.00'), context='authorise')
        self.assertIn('already raised or paid', msg)
        self.assertNotIn('BANK REJECTED', msg)
