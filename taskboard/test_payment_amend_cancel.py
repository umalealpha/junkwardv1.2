"""taskboard/test_payment_amend_cancel.py — amend / cancel before sign-off.

Kelvin Kimani spec 2026-09-08. A Payment Authorisation Request had no clean way
to correct or pull a payment once raised — the inputter was stuck with what they
typed, so a wrong amount meant abandoning the whole request and re-raising it.

These are the rules that must never regress:
  THE WINDOW    amend and cancel exist ONLY pre-sign-off. The moment finance
                signs off, it locks;
  RECALCULATE   every amend re-derives TOTAL PAYABLE from the lines behind it,
                in Decimal, so the figures never drift — and Section B's
                liquidity is computed from that total, so it follows;
  RE-CHECK      an amend invalidates a completed cross-check, so a request
                cannot be checked, quietly changed, then signed off on a stale
                confirmation;
  LOG           field, before, after, who, when — on every amend;
  REASON        required on a cancel, never demanded on an amend;
  NEVER DELETE  a cancelled line or request is FLAGGED and kept in full, with
                its amount, invoice number and dates intact;
  ATTRIBUTION   "Amended by / Cancelled by [Name] · [Department]" is read from
                the logged-in user, never typed, with a timestamp;
  EMPTIED       cancelling the last payable line falls the request to cancelled;
  PAYEE         changing who is paid is flagged distinctly and re-triggers the
                bank cross-check.

Omni moves no money anywhere in here. No real payee or staff names.

Run: manage.py test taskboard.test_payment_amend_cancel
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import OmniTask
from taskboard.models import PaymentRequest, PaymentRequestChange
from taskboard.payment_amend import AmendError, amend, cancel_line, cancel_request
from taskboard.test_helpers import seed_adic

LINES = [
    {'description': 'Courier run, 1 Aug', 'amount': '2000.00',
     'invoice_number': 'IN102981', 'invoice_date': '2026-08-01',
     'due_date': '2026-08-08', 'gl_code': '500100', 'ref': 'IN102981'},
    {'description': 'Courier run, 8 Aug', 'amount': '2556.09',
     'invoice_number': 'IN102982', 'invoice_date': '2026-08-08',
     'due_date': '2026-08-12', 'gl_code': '500100', 'ref': 'IN102982'},
]
TOTAL = Decimal('4556.09')


class AmendWindowTests(TestCase):
    """It exists only before sign-off."""

    def setUp(self):
        seed_adic()
        self.me = User.objects.create_user('lthebe', password='x',
                                           first_name='Lorato', last_name='Thebe')

    def _pr(self, status):
        return PaymentRequest.objects.create(
            ref=f'PAY/ADIC/2026/09/08/{PaymentRequest.objects.count() + 1:04d}',
            subject='Nonesuch Couriers — July', payee='Nonesuch Couriers',
            total=TOTAL, line_items=list(LINES), status=status,
            created_by=self.me)

    def test_a_pending_finance_request_is_amendable(self):
        self.assertTrue(self._pr(PaymentRequest.Status.PENDING_FINANCE).is_amendable)

    def test_a_draft_is_amendable(self):
        self.assertTrue(self._pr(PaymentRequest.Status.DRAFT).is_amendable)

    def test_a_signed_off_request_is_locked(self):
        pr = self._pr(PaymentRequest.Status.PENDING_CFO)
        self.assertFalse(pr.is_amendable)
        self.assertIn('already signed this request off', pr.amend_lock_reason())

    def test_a_paid_request_is_locked(self):
        self.assertFalse(self._pr(PaymentRequest.Status.PAID).is_amendable)

    def test_a_request_with_the_committee_is_locked_and_says_why(self):
        """Editing a pack three people are deciding would have them signing
        something else."""
        pr = self._pr(PaymentRequest.Status.EXCEPTION)
        self.assertFalse(pr.is_amendable)
        self.assertIn('exception committee', pr.amend_lock_reason())

    def test_amending_a_locked_request_is_refused(self):
        pr = self._pr(PaymentRequest.Status.PENDING_CFO)
        with self.assertRaises(AmendError) as ctx:
            amend(pr, self.me, line_changes=[
                {'line': 1, 'field': 'amount', 'value': '1.00'}])
        self.assertEqual(ctx.exception.control, 'PAY-AMEND-LOCK')
        pr.refresh_from_db()
        self.assertEqual(pr.total, TOTAL)          # and nothing moved

    def test_cancelling_a_locked_request_is_refused(self):
        pr = self._pr(PaymentRequest.Status.PAID)
        with self.assertRaises(AmendError):
            cancel_request(pr, self.me, reason='Paid in error')
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PAID)


class AmendTests(TestCase):
    """What an amend does: recalculate, re-check, log."""

    def setUp(self):
        seed_adic()
        self.me = User.objects.create_user('lthebe', password='x',
                                           first_name='Lorato', last_name='Thebe')
        from core.models import UserProfile
        UserProfile.objects.update_or_create(
            user=self.me, defaults={'role': 'staff', 'department': 'Finance'})
        self.pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/0001', subject='Nonesuch Couriers — July',
            payee='Nonesuch Couriers', total=TOTAL, line_items=list(LINES),
            status=PaymentRequest.Status.PENDING_FINANCE, created_by=self.me,
            opening_balance=Decimal('100000.00'))

    # ── recalculation ────────────────────────────────────────────────────────
    def test_amending_an_amount_recalculates_the_total(self):
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.total, Decimal('1500.00') + Decimal('2556.09'))

    def test_the_recalculated_total_is_exact_to_the_thebe(self):
        amend(self.pr, self.me,
              line_changes=[{'line': 2, 'field': 'amount', 'value': '0.01'}])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.total, Decimal('2000.01'))

    def test_the_pack_is_re_rendered_with_the_new_total(self):
        """An amended request that still printed the old total would be the
        worst possible outcome of this feature."""
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.pr.refresh_from_db()
        self.assertIn('4,056.09', self.pr.formatted_html)
        self.assertNotIn('4,556.09', self.pr.formatted_html)

    def test_the_liquidity_position_follows_the_new_total(self):
        """Section B is derived from the total, so there is no second copy to
        drift. 100,000 - 4,056.09 = 95,943.91."""
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.pr.refresh_from_db()
        self.assertIn('95,943.91', self.pr.formatted_html)

    # ── every editable field ─────────────────────────────────────────────────
    def test_each_field_the_spec_names_can_be_corrected(self):
        for field, value in (('invoice_number', 'IN999'),
                             ('invoice_date', '2026-08-02'),
                             ('due_date', '2026-08-30'),
                             ('gl_code', '500200'),
                             ('description', 'Courier run, corrected')):
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': field, 'value': value}])
            self.pr.refresh_from_db()
            self.assertEqual(self.pr.line_items[0][field], value)

    def test_request_level_fields_can_be_corrected(self):
        amend(self.pr, self.me, request_changes={'payment_date': '2026-09-15',
                                                 'processing_method': 'individual'})
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.payment_date.isoformat(), '2026-09-15')
        self.assertTrue(self.pr.is_individual)
        self.assertEqual(self.pr.payment_count(), 2)

    def test_a_field_nobody_may_edit_is_refused_not_ignored(self):
        """A field that looks editable and silently is not is worse than one
        that says no."""
        with self.assertRaises(AmendError) as ctx:
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': 'claim_number', 'value': 'G1'}])
        self.assertIn('not a field that can be corrected', ctx.exception.detail)

    def test_the_account_number_is_not_amendable_here(self):
        with self.assertRaises(AmendError):
            amend(self.pr, self.me,
                  request_changes={'account_number': '62099998888'})

    def test_a_zero_amount_is_refused_and_points_at_cancel(self):
        """Zeroing a line to drop it would hide it; cancelling keeps it."""
        with self.assertRaises(AmendError) as ctx:
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': 'amount', 'value': '0'}])
        self.assertIn('cancel the line instead', ctx.exception.detail)

    def test_a_blank_subject_is_refused_because_it_titles_the_pop(self):
        with self.assertRaises(AmendError):
            amend(self.pr, self.me, request_changes={'subject': '   '})

    def test_a_line_that_does_not_exist_is_refused(self):
        with self.assertRaises(AmendError):
            amend(self.pr, self.me,
                  line_changes=[{'line': 9, 'field': 'amount', 'value': '5.00'}])

    def test_an_amend_that_changes_nothing_is_refused(self):
        with self.assertRaises(AmendError) as ctx:
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': 'amount', 'value': '2000.00'}])
        self.assertEqual(ctx.exception.detail, 'Nothing changed.')

    def test_the_same_amount_written_differently_is_not_a_change(self):
        """'2000' and '2000.00' are one figure — logging that as an amend would
        fill the history with noise."""
        with self.assertRaises(AmendError):
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': 'amount', 'value': '2000'}])

    # ── the log ──────────────────────────────────────────────────────────────
    def test_the_change_is_logged_with_before_and_after(self):
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        row = PaymentRequestChange.objects.get()
        self.assertEqual(row.action, PaymentRequestChange.Action.AMEND)
        self.assertEqual(row.line, 1)
        self.assertEqual(row.field, 'amount')
        self.assertEqual(row.value_before, '2000.00')
        self.assertEqual(row.value_after, '1500.00')

    def test_the_attribution_is_read_from_the_user_never_typed(self):
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        row = PaymentRequestChange.objects.get()
        self.assertEqual(row.actor_name, 'Lorato Thebe')
        self.assertEqual(row.actor_department, 'Finance')
        self.assertEqual(row.attribution, 'Amended by Lorato Thebe · Finance')
        self.assertIsNotNone(row.created_at)

    def test_no_reason_is_demanded_on_an_amend(self):
        """"the before/after log is the record of what changed." The why is
        only required where a payment is pulled."""
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.assertEqual(PaymentRequestChange.objects.get().reason, '')

    def test_several_changes_in_one_amend_are_each_logged(self):
        amend(self.pr, self.me,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'},
                            {'line': 2, 'field': 'gl_code', 'value': '500200'}],
              request_changes={'processing_method': 'individual'})
        self.assertEqual(PaymentRequestChange.objects.count(), 3)

    # ── the re-check rule ────────────────────────────────────────────────────
    def test_an_amend_invalidates_a_completed_committee_decision(self):
        """A request cannot be checked, quietly changed, then signed off on a
        stale confirmation."""
        self.pr.exception_control = 'PAY-BANK-01'
        self.pr.exception_decision = 'approve'
        self.pr.save(update_fields=['exception_control', 'exception_decision'])
        out = amend(self.pr, self.me,
                    line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.exception_decision, '')
        self.assertIn('the committee decision', out['cross_check_cleared'])

    def test_amending_the_payee_re_triggers_the_bank_cross_check(self):
        """The classic payment-redirection risk. Both bank gates re-derive
        against the NEW payee's own history, so the confirmation genuinely has
        to be given again."""
        from taskboard.payee_bank_history import bank_details_changed
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/07/01/0009', subject='Earlier',
            payee='Nonesuch Motors', account_name='Nonesuch Motors',
            account_number='62011110000', bank_name='FNB Botswana',
            total=Decimal('10.00'), status=PaymentRequest.Status.PAID,
            line_items=[])
        self.pr.account_number = '62099998888'
        self.pr.bank_name = 'FNB Botswana'
        self.pr.save(update_fields=['account_number', 'bank_name'])
        # Before: nothing to compare — this payee has no history.
        self.assertIsNone(bank_details_changed(
            self.pr.payee, bank_name=self.pr.bank_name,
            account_number=self.pr.account_number, exclude_pk=self.pr.pk))
        out = amend(self.pr, self.me, request_changes={'payee': 'Nonesuch Motors'})
        self.assertIn('payee', out['high_risk'])
        self.pr.refresh_from_db()
        # After: it now reads against Nonesuch Motors' account, and differs.
        w = bank_details_changed(self.pr.payee, bank_name=self.pr.bank_name,
                                 account_number=self.pr.account_number,
                                 exclude_pk=self.pr.pk)
        self.assertEqual(w['fields'], ['account number'])

    # ── who may act ──────────────────────────────────────────────────────────
    def test_an_unrelated_user_cannot_amend(self):
        """"a request should not be quietly altered by a user unrelated to it.\""""
        stranger = User.objects.create_user('stranger', password='x')
        with self.assertRaises(AmendError) as ctx:
            amend(self.pr, stranger,
                  line_changes=[{'line': 1, 'field': 'amount', 'value': '1.00'}])
        self.assertEqual(ctx.exception.control, 'PAY-AMEND-PERM')

    def test_a_finance_approver_may_amend(self):
        kago = User.objects.create_user('kago',
                                        email='ktshutlhedi@alphadirect.co.bw',
                                        password='x')
        amend(self.pr, kago,
              line_changes=[{'line': 1, 'field': 'amount', 'value': '1500.00'}])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.total, Decimal('4056.09'))


class CancelTests(TestCase):
    """Cancel flags and keeps. It never deletes."""

    def setUp(self):
        seed_adic()
        self.me = User.objects.create_user('lthebe', password='x',
                                           first_name='Lorato', last_name='Thebe')
        self.task = OmniTask.objects.create(
            assigner=self.me, assignee=self.me, title='Payment authorisation',
            body='x', status=OmniTask.Status.PENDING)
        self.pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/0001', subject='Nonesuch Couriers — July',
            payee='Nonesuch Couriers', total=TOTAL, line_items=list(LINES),
            status=PaymentRequest.Status.PENDING_FINANCE, created_by=self.me,
            task=self.task)

    # ── the reason ───────────────────────────────────────────────────────────
    def test_cancelling_a_line_needs_a_reason(self):
        with self.assertRaises(AmendError) as ctx:
            cancel_line(self.pr, self.me, line_no=1, reason='')
        self.assertEqual(ctx.exception.control, 'PAY-CANCEL-REASON')

    def test_a_token_reason_is_not_enough(self):
        with self.assertRaises(AmendError):
            cancel_line(self.pr, self.me, line_no=1, reason='no')

    def test_cancelling_the_request_needs_a_reason(self):
        with self.assertRaises(AmendError):
            cancel_request(self.pr, self.me, reason='')

    # ── nothing is deleted ───────────────────────────────────────────────────
    def test_a_cancelled_line_is_kept_in_full(self):
        cancel_line(self.pr, self.me, line_no=2,
                    reason='Duplicate of IN102981; the courier re-billed it.')
        self.pr.refresh_from_db()
        self.assertEqual(len(self.pr.line_items), 2)     # nothing removed
        pulled = self.pr.line_items[1]
        self.assertTrue(pulled['cancelled'])
        self.assertEqual(pulled['amount'], '2556.09')    # amount intact
        self.assertEqual(pulled['invoice_number'], 'IN102982')
        self.assertEqual(pulled['due_date'], '2026-08-12')

    def test_a_cancelled_line_records_who_when_and_why(self):
        cancel_line(self.pr, self.me, line_no=2, reason='Billed twice by mistake.')
        pulled = PaymentRequest.objects.get().line_items[1]
        self.assertEqual(pulled['cancelled_by'], 'Lorato Thebe')
        self.assertEqual(pulled['cancelled_reason'], 'Billed twice by mistake.')
        self.assertTrue(pulled['cancelled_at'])

    def test_cancelling_the_request_keeps_every_line(self):
        cancel_request(self.pr, self.me, reason='Raised against the wrong entity.')
        self.pr.refresh_from_db()
        self.assertEqual(len(self.pr.line_items), 2)
        self.assertTrue(all(ln['cancelled'] for ln in self.pr.line_items))
        self.assertEqual(self.pr.line_items[0]['amount'], '2000.00')

    def test_the_cancelled_request_records_who_when_and_why(self):
        cancel_request(self.pr, self.me, reason='Raised against the wrong entity.')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.CANCELLED)
        self.assertEqual(self.pr.cancelled_reason, 'Raised against the wrong entity.')
        self.assertEqual(self.pr.cancelled_by_id, self.me.id)
        self.assertIsNotNone(self.pr.cancelled_at)

    # ── recalculation ────────────────────────────────────────────────────────
    def test_cancelling_a_line_recomputes_the_total(self):
        cancel_line(self.pr, self.me, line_no=2, reason='Billed twice by mistake.')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.total, Decimal('2000.00'))

    def test_a_cancelled_line_no_longer_counts_as_a_payment(self):
        self.pr.processing_method = 'individual'
        self.pr.save(update_fields=['processing_method'])
        cancel_line(self.pr, self.me, line_no=2, reason='Billed twice by mistake.')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.payment_count(), 1)

    def test_the_remaining_lines_still_reconcile_to_the_new_total(self):
        cancel_line(self.pr, self.me, line_no=2, reason='Billed twice by mistake.')
        self.pr.refresh_from_db()
        self.assertIsNone(PaymentRequest.reconciliation_error(
            self.pr.line_items, self.pr.total))

    # ── emptying the request ─────────────────────────────────────────────────
    def test_cancelling_the_last_payable_line_cancels_the_request(self):
        cancel_line(self.pr, self.me, line_no=1, reason='Wrong supplier entirely.')
        out = cancel_line(self.pr, self.me, line_no=2,
                          reason='Wrong supplier entirely.')
        self.assertTrue(out['emptied'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.CANCELLED)
        self.assertIn('nothing is left to pay', self.pr.cancelled_reason)
        # ...and both lines are still there.
        self.assertEqual(len(self.pr.line_items), 2)

    def test_cancelling_a_line_twice_is_refused(self):
        cancel_line(self.pr, self.me, line_no=1, reason='Wrong supplier entirely.')
        with self.assertRaises(AmendError):
            cancel_line(self.pr, self.me, line_no=1, reason='Wrong supplier again.')

    def test_a_cancelled_line_cannot_then_be_amended(self):
        cancel_line(self.pr, self.me, line_no=1, reason='Wrong supplier entirely.')
        with self.assertRaises(AmendError) as ctx:
            amend(self.pr, self.me,
                  line_changes=[{'line': 1, 'field': 'amount', 'value': '99.00'}])
        self.assertIn('cancelled', ctx.exception.detail)

    # ── the log and the task ─────────────────────────────────────────────────
    def test_a_cancel_is_logged_with_its_reason_and_attribution(self):
        cancel_line(self.pr, self.me, line_no=2, reason='Billed twice by mistake.')
        row = PaymentRequestChange.objects.get(
            action=PaymentRequestChange.Action.CANCEL_LINE)
        self.assertEqual(row.line, 2)
        self.assertEqual(row.reason, 'Billed twice by mistake.')
        self.assertEqual(row.value_before, '2556.09')
        self.assertEqual(row.value_after, 'cancelled')
        self.assertEqual(row.attribution, 'Cancelled by Lorato Thebe')

    def test_cancelling_the_request_closes_the_authorisation_task(self):
        """An approver should not be left holding a task for a payment that no
        longer exists."""
        cancel_request(self.pr, self.me, reason='Raised against the wrong entity.')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.CANCELLED)

    def test_a_cancelled_request_cannot_be_cancelled_again(self):
        cancel_request(self.pr, self.me, reason='Raised against the wrong entity.')
        with self.assertRaises(AmendError):
            cancel_request(self.pr, self.me, reason='Again, for luck.')


class AmendCancelEndpointTests(TestCase):
    """The two doors, and what the screen is told."""

    def setUp(self):
        seed_adic()
        self.me = User.objects.create_user('lthebe', password='x',
                                           first_name='Lorato', last_name='Thebe')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x')
        self.pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/0001', subject='Nonesuch Couriers — July',
            payee='Nonesuch Couriers', total=TOTAL, line_items=list(LINES),
            status=PaymentRequest.Status.PENDING_FINANCE, created_by=self.me)
        self.amend_url = reverse('v1-payment-request-amend', args=[self.pr.id])
        self.cancel_url = reverse('v1-payment-request-cancel', args=[self.pr.id])
        self.client.force_login(self.me)

    def test_amend_returns_the_new_total_and_the_history(self):
        r = self.client.post(self.amend_url, {
            'line_changes': [{'line': 1, 'field': 'amount', 'value': '1500.00'}],
        }, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['total'], '4056.09')
        self.assertEqual(len(d['changes']), 1)
        self.assertEqual(d['changes'][0]['value_after'], '1500.00')
        self.assertIn('Amended by Lorato Thebe', d['changes'][0]['attribution'])

    def test_a_payee_change_is_flagged_distinctly(self):
        r = self.client.post(self.amend_url, {
            'request_changes': {'payee': 'Nonesuch Motors'},
        }, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['payee_changed']['control'], 'PAY-AMEND-PAYEE')
        self.assertIn('payment-redirection', r.json()['payee_changed']['message'])

    def test_an_ordinary_amend_is_not_flagged_as_high_risk(self):
        r = self.client.post(self.amend_url, {
            'line_changes': [{'line': 1, 'field': 'gl_code', 'value': '500200'}],
        }, content_type='application/json')
        self.assertNotIn('payee_changed', r.json())

    def test_amend_on_a_locked_request_returns_409(self):
        self.pr.status = PaymentRequest.Status.PENDING_CFO
        self.pr.save(update_fields=['status'])
        r = self.client.post(self.amend_url, {
            'line_changes': [{'line': 1, 'field': 'amount', 'value': '1.00'}],
        }, content_type='application/json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-AMEND-LOCK')

    def test_cancel_a_line_through_the_endpoint(self):
        r = self.client.post(self.cancel_url,
                             {'line': 2, 'reason': 'Billed twice by mistake.'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['total'], '2000.00')
        self.assertFalse(r.json()['emptied'])
        self.assertEqual(len(PaymentRequest.objects.get().line_items), 2)

    def test_cancel_the_whole_request_through_the_endpoint(self):
        r = self.client.post(self.cancel_url,
                             {'reason': 'Raised against the wrong entity.'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'cancelled')
        self.assertEqual(len(PaymentRequest.objects.get().line_items), 2)

    def test_cancel_without_a_reason_is_refused(self):
        r = self.client.post(self.cancel_url, {}, content_type='application/json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-CANCEL-REASON')

    def test_a_stranger_gets_403_and_nothing_changes(self):
        stranger = User.objects.create_user('stranger', password='x')
        self.client.force_login(stranger)
        r = self.client.post(self.amend_url, {
            'line_changes': [{'line': 1, 'field': 'amount', 'value': '1.00'}],
        }, content_type='application/json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(PaymentRequest.objects.get().total, TOTAL)

    def test_the_detail_endpoint_reports_the_window_and_the_history(self):
        self.client.post(self.amend_url, {
            'line_changes': [{'line': 1, 'field': 'amount', 'value': '1500.00'}],
        }, content_type='application/json')
        d = self.client.get(reverse('v1-payment-request-detail',
                                    args=[self.pr.id])).json()
        self.assertTrue(d['is_amendable'])
        self.assertTrue(d['can_amend'])
        self.assertEqual(d['amend_lock_reason'], '')
        self.assertEqual(len(d['changes']), 1)

    def test_the_detail_endpoint_reports_the_lock_and_the_cancel_record(self):
        self.client.post(self.cancel_url,
                         {'reason': 'Raised against the wrong entity.'},
                         content_type='application/json')
        d = self.client.get(reverse('v1-payment-request-detail',
                                    args=[self.pr.id])).json()
        self.assertFalse(d['is_amendable'])
        self.assertFalse(d['can_amend'])
        self.assertIn('already cancelled', d['amend_lock_reason'])
        self.assertEqual(d['cancelled_reason'], 'Raised against the wrong entity.')
        self.assertEqual(d['cancelled_by'], 'Lorato Thebe')
        self.assertIsNotNone(d['cancelled_at'])
