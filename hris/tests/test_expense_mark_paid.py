"""Closing out a refund that was actually PAID (Bharath Balasubramanian, 2026-08-17).

His mail: *"They are rejecting the reimbursement instead of approving this once
paid. If you can keep an option of paid reimbursement - that will keep a proper
track of these. Tomorrow, someone can scam us by saying we haven't paid."*

He is right. `ExpenseClaim.Status.PAID` existed from the start but nothing ever
set it, so a refund paid outside the accountant + CFO route had nowhere to go and
was closed with Reject + reason "PAID". Three of his own refunds (P1,422.19 +
P500.00 + P549.99 = P2,472.18, all 30-Jul-2026) are recorded on prod as
"Returned to requester" for money that was paid.

These tests pin the new close-out: who may use it, that a reference is required,
that it works from every open state INCLUDING rejected (so the mis-recorded rows
can be corrected), and that correcting one clears the misleading rejection.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import Group, User
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from hris.expense_claim_models import ExpenseClaim
from hris.models import HRISProfile
from payroll.models import Employee

URL = '/api/v1/expense-claims'


class ExpenseMarkPaidTest(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='REFT', name='Refund Test Co.')

        # The requester.
        cls.staff_user = User.objects.create_user('bharath', 'bb@test.example', 'x')
        cls.staff_profile = UserProfile.objects.create(
            user=cls.staff_user, is_active=True)

        # The senior accountant who handles it. _is_approver() is GROUP-based,
        # not title-based — read it off expense_api.APPROVER_GROUP rather than
        # guessing a title.
        from hris.expense_api import APPROVER_GROUP
        approvers, _ = Group.objects.get_or_create(name=APPROVER_GROUP)
        cls.accountant = User.objects.create_user('accountant', 'acc@test.example', 'x')
        cls.accountant.groups.add(approvers)
        UserProfile.objects.update_or_create(
            user=cls.accountant, defaults={'is_active': True})

        # _is_cfo() keys on the profile TITLE, not the email address.
        cls.cfo = User.objects.create_user(
            'prathap', 'pganesharajah@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.cfo,
            defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        cls.stranger = User.objects.create_user('stranger', 'no@test.example', 'x')
        cls.admin = User.objects.create_superuser('root', 'root@test.example', 'x')

    def _claim(self, status=ExpenseClaim.Status.SUBMITTED, reject_reason='',
               amount='1422.19'):
        return ExpenseClaim.objects.create(
            profile=self.staff_profile,
            expense_date=dt.date(2026, 7, 30),
            amount=Decimal(amount),
            description='Fuel Reimbursement',
            status=status,
            approver=self.accountant,
            reject_reason=reject_reason,
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _mark(self, claim, user=None, **body):
        payload = {'reference': 'FNB-REF-99881', **body}
        return self._client(user or self.accountant).post(
            f'{URL}/{claim.id}/mark-paid/', payload, format='json')

    # ── the happy path ──────────────────────────────────────────────────────
    def test_accountant_can_close_a_submitted_refund_as_paid(self):
        c = self._claim()
        r = self._mark(c)
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.PAID)
        self.assertEqual(c.paid_by_id, self.accountant.pk)
        self.assertEqual(c.paid_reference, 'FNB-REF-99881')
        self.assertIsNotNone(c.paid_at)

    def test_the_cfo_can_do_it_too(self):
        c = self._claim(status=ExpenseClaim.Status.PENDING_CFO)
        self.assertEqual(self._mark(c, self.cfo).status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.PAID)

    def test_it_works_from_every_open_state(self):
        for st in (ExpenseClaim.Status.SUBMITTED,
                   ExpenseClaim.Status.PENDING_CFO,
                   ExpenseClaim.Status.APPROVED,
                   ExpenseClaim.Status.REJECTED):
            with self.subTest(status=st):
                c = self._claim(status=st)
                self.assertEqual(self._mark(c).status_code, 200)
                c.refresh_from_db()
                self.assertEqual(c.status, ExpenseClaim.Status.PAID)

    def test_the_payload_reports_the_payment_details(self):
        c = self._claim()
        b = self._mark(c, note='paid by FNB transfer on 30 Jul').json()
        self.assertEqual(b['status'], 'paid')
        self.assertEqual(b['paid_reference'], 'FNB-REF-99881')
        self.assertEqual(b['paid_by'], 'accountant')
        self.assertIn('FNB transfer', b['paid_note'])

    # ── correcting the rows Bharath found ───────────────────────────────────
    def test_correcting_a_wrongly_rejected_refund_clears_the_rejection(self):
        """The exact prod case: rejected with reason "PAID". After correction the
        record must no longer read as returned-to-requester, and the old reason
        must be kept in the note as an audit breadcrumb."""
        c = self._claim(status=ExpenseClaim.Status.REJECTED, reject_reason='PAID')
        r = self._mark(c, reference='FNB-30JUL-001')
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.PAID)
        self.assertEqual(c.reject_reason, '', 'the misleading rejection must go')
        self.assertIn('was rejected with reason "PAID"', c.paid_note)
        self.assertEqual(c.paid_reference, 'FNB-30JUL-001')

    # ── the guards ──────────────────────────────────────────────────────────
    def test_a_reference_is_required(self):
        """Marking money paid with no evidence is the very hole this closes."""
        c = self._claim()
        r = self._client(self.accountant).post(
            f'{URL}/{c.id}/mark-paid/', {'reference': '   '}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('reference', r.json()['detail'].lower())
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.SUBMITTED)

    def test_a_stranger_cannot_mark_it_paid(self):
        c = self._claim()
        r = self._mark(c, self.stranger)
        self.assertEqual(r.status_code, 403)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.SUBMITTED)

    def test_the_requester_cannot_mark_their_own_refund_paid(self):
        """Otherwise anyone could close their own claim as paid."""
        c = self._claim()
        r = self._mark(c, self.staff_user)
        self.assertEqual(r.status_code, 403)

    def test_a_draft_cannot_be_marked_paid(self):
        c = self._claim(status=ExpenseClaim.Status.DRAFT)
        r = self._mark(c)
        self.assertEqual(r.status_code, 400)
        self.assertIn('submit it first', r.json()['detail'].lower())

    def test_marking_it_paid_twice_is_refused(self):
        c = self._claim()
        self.assertEqual(self._mark(c).status_code, 200)
        again = self._mark(c)
        self.assertEqual(again.status_code, 400)
        self.assertIn('already marked paid', again.json()['detail'])

    def test_an_admin_can_do_it(self):
        c = self._claim()
        self.assertEqual(self._mark(c, self.admin).status_code, 200)

    def test_signed_out_is_refused(self):
        c = self._claim()
        r = APIClient().post(f'{URL}/{c.id}/mark-paid/',
                             {'reference': 'X'}, format='json')
        self.assertIn(r.status_code, (401, 403))

    def test_reject_still_works_for_a_genuine_rejection(self):
        """This must not break the real reject path — a refund that genuinely
        needs changes should still be returnable."""
        c = self._claim()
        r = self._client(self.accountant).post(
            f'{URL}/{c.id}/reject/', {'reason': 'Receipt is unreadable'},
            format='json')
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.REJECTED)
        self.assertEqual(c.reject_reason, 'Receipt is unreadable')


class FixRefundsRejectedAsPaidTest(ExpenseMarkPaidTest):
    """The one-off correction command for the rows Bharath found.

    Must be NARROW: a genuine rejection ("Receipt is unreadable") must survive
    untouched, or the command would erase real rejections.
    """

    def _run(self, **opts):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('fix_refunds_rejected_as_paid', stdout=out, **opts)
        return out.getvalue()

    def test_it_corrects_a_reject_reason_of_PAID(self):
        c = self._claim(status=ExpenseClaim.Status.REJECTED, reject_reason='PAID')
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.PAID)
        self.assertEqual(c.reject_reason, '')
        self.assertIn('was rejected with reason "PAID"', c.paid_note)
        self.assertTrue(c.paid_reference)

    def test_it_is_case_and_padding_insensitive(self):
        for reason in ('paid', '  Paid  ', 'ALREADY PAID', 'was paid'):
            with self.subTest(reason=reason):
                c = self._claim(status=ExpenseClaim.Status.REJECTED,
                                reject_reason=reason)
                self._run()
                c.refresh_from_db()
                self.assertEqual(c.status, ExpenseClaim.Status.PAID)

    def test_a_GENUINE_rejection_is_never_touched(self):
        """The whole risk of this command. These must stay rejected."""
        for reason in ('Receipt is unreadable', 'Error', 'Keep it at 5k',
                       'not paid yet', 'Duck u'):
            with self.subTest(reason=reason):
                c = self._claim(status=ExpenseClaim.Status.REJECTED,
                                reject_reason=reason)
                self._run()
                c.refresh_from_db()
                self.assertEqual(c.status, ExpenseClaim.Status.REJECTED,
                                 f'{reason!r} is a real rejection, not a payment')
                self.assertEqual(c.reject_reason, reason)

    def test_dry_run_changes_nothing(self):
        c = self._claim(status=ExpenseClaim.Status.REJECTED, reject_reason='PAID')
        output = self._run(dry_run=True)
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.REJECTED)
        self.assertEqual(c.reject_reason, 'PAID')
        self.assertIn('DRY RUN', output)

    def test_it_leaves_non_rejected_refunds_alone(self):
        for st in (ExpenseClaim.Status.SUBMITTED, ExpenseClaim.Status.APPROVED,
                   ExpenseClaim.Status.PENDING_CFO):
            with self.subTest(status=st):
                c = self._claim(status=st)
                self._run()
                c.refresh_from_db()
                self.assertEqual(c.status, st)

    def test_running_it_twice_is_safe(self):
        c = self._claim(status=ExpenseClaim.Status.REJECTED, reject_reason='PAID')
        self._run()
        c.refresh_from_db()
        first_note = c.paid_note
        self._run()
        c.refresh_from_db()
        self.assertEqual(c.status, ExpenseClaim.Status.PAID)
        self.assertEqual(c.paid_note, first_note, 'must not stack notes on re-run')
