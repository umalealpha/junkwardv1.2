"""Recording that a payment was REJECTED inside the FNB app.

Leano Makwapa / Koketso Kgetse, 2026-09-11: FNB tells Omni nothing when a person
declines an authorisation in the app, so the batch stayed on 'submitted' and the
queue kept reading "Waiting for your authorisation in the FNB app" long after the
bank had said no. Finance had no way to make their records agree with FNB.

CFO decision the same day: the button goes to the ACCOUNTS TEAM who load the
payments, not to one controller. It is safe there because it moves no money and
closes nothing — it re-opens the payment as "Rejected by FNB" so the details can
be fixed and it can be loaded again.

What these tests pin:
  * The queue line corrects itself — the SAME wording FNB's own API rejection
    produces, from the same batch status. No second source of truth.
  * The payment request is left OPEN. Closing it would drop it out of the
    duplicate-payment control, which is how an unpaid invoice disappears.
  * A SETTLED batch can never be re-labelled a rejection — the money has left.
  * Only the accounts team, the finance approvers and the CFO may do it.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from banking.models import BankAccount
from core.models import AuditLog, Company, Currency
from fnb.email_reconcile import open_reason
from fnb.models import FNBBatchSubmission
from ledger.models import Account
from taskboard.models import PaymentRequest

ACCOUNTS = ['kkgetse@alphadirect.co.bw']


@override_settings(PAYMENT_FNB_REJECT_EMAILS=ACCOUNTS)
class MarkRejectedOnFnbTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='MRFNB', defaults={'name': 'Mark-rejected Test Co',
                                    'base_currency': cls.bwp})
        cls.gl = Account.objects.create(
            code='MRFNB-BANK', name='Mark-rejected bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='Test current',
            account_number='8' * 11, currency_code_id='BWP')
        cls.raiser = User.objects.create_user('mr_raiser', password='x')
        cls.koketso = User.objects.create_user(
            'kkgetse', 'kkgetse@alphadirect.co.bw', 'x')
        cls.outsider = User.objects.create_user(
            'mr_outsider', 'nobody@alphadirect.co.bw', 'x')

    def _batch(self, status=FNBBatchSubmission.Status.SUBMITTED, key='k1'):
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-MR-{key}', source_account=self.source,
            payment_count=1, total_amount_bwp=Decimal('250.00'),
            currency_code='BWP', status=status, fnb_reference=f'ref-{key}')

    def _request(self, batch=None, ref='PR-MR-0001'):
        return PaymentRequest.objects.create(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='Invoice 9001 panel repair', payee='ABC Traders',
            line_items=[{'description': 'Panel repair', 'amount': 250.0,
                         'gl_code': '', 'ref': ''}],
            total=Decimal('250.00'),
            account_name='ABC Traders (Pty) Ltd', account_number='1234567',
            bank_name='FNB Botswana', branch_code='293567', account_type='CACC',
            status=PaymentRequest.Status.PENDING_CFO,
            created_by=self.raiser, fnb_batch=batch)

    def _post(self, pr, reason='Declined in the app - wrong branch code',
              as_user=None):
        self.client.force_login(as_user or self.koketso)
        return self.client.post(
            f'/api/v1/payment-requests/{pr.id}/mark-rejected-fnb/',
            data={'reason': reason}, content_type='application/json')

    # ── it does what the bank's own rejection does ──────────────────────────

    def test_the_accounts_team_can_record_an_fnb_rejection(self):
        pr = self._request(self._batch())
        r = self._post(pr)
        self.assertEqual(r.status_code, 200, r.content)
        pr.fnb_batch.refresh_from_db()
        self.assertEqual(pr.fnb_batch.status, FNBBatchSubmission.Status.FAILED)
        self.assertIn('wrong branch code', pr.fnb_batch.failure_reason)
        self.assertIn('kkgetse', pr.fnb_batch.failure_reason)

    def test_it_never_says_this_person_rejected_it_in_the_fnb_app(self):
        """Reported 2026-09-11: the banner read "Rejected in the FNB app by
        <recorder>". That person did not reject it at the bank — they RECORDED in
        Omni that the bank had. Rejecting inside the FNB app is the authoriser's
        act, so the old wording put one person's decision against another's name
        on a financial record. Each person is now credited only with the half
        they actually did."""
        pr = self._request(self._batch())
        self._post(pr, 'Displayed as EFT PAYMENT so recon could not be done')
        pr.fnb_batch.refresh_from_db()
        text = pr.fnb_batch.failure_reason

        who = self.koketso.get_full_name() or self.koketso.username
        self.assertNotIn(f'in the FNB app by {who}', text)
        self.assertIn('rejected in the FNB app', text)
        self.assertIn(f'marked as rejected in Omni by {who}', text)
        # The reason the recorder typed still survives in full.
        self.assertIn('Displayed as EFT PAYMENT so recon could not be done', text)

    def test_the_queue_line_still_carries_what_to_fix_not_just_who_typed_it(self):
        """Caught by Fable, missed by every test above it.

        open_reason() renders this field as `fr[:70]` on the payment queue. The
        attribution alone is 55 characters, so leading with it pushed the typed
        reason clean out of that window: the line kept the name and lost the one
        thing it exists to carry, and a longer name was cut mid-name. This pins
        the PAYLOAD inside the shortest consumer's window, not the label — the
        label is what passed while the payload was being thrown away."""
        pr = self._request(self._batch())
        self._post(pr, 'Wrong branch code on the payee')
        pr.fnb_batch.refresh_from_db()
        line, tone = open_reason(batch_status=pr.fnb_batch.status,
                                 failure_reason=pr.fnb_batch.failure_reason,
                                 has_batch=True)
        self.assertEqual(tone, 'reject')
        self.assertIn('Wrong branch code', line)
        self.assertIn('Fix the bank/branch details and reload', line)

    def test_a_long_name_is_never_cut_in_half_on_the_queue_line(self):
        """A truncation that amputates a name is a truncation that misattributes,
        which is the very fault being fixed."""
        long_name = User.objects.create_user(
            'mr_long', 'kkgetse@alphadirect.co.bw', 'x')
        long_name.first_name, long_name.last_name = 'Legakwa', 'Ntabeni-Motshegwa'
        long_name.save()
        pr = self._request(self._batch())
        self._post(pr, 'Beneficiary account closed', as_user=long_name)
        pr.fnb_batch.refresh_from_db()
        line, _ = open_reason(batch_status=pr.fnb_batch.status,
                              failure_reason=pr.fnb_batch.failure_reason,
                              has_batch=True)
        self.assertIn('Beneficiary account closed', line)
        # Either the whole name is there or none of it — never a fragment.
        self.assertNotIn('Legakwa Ntabeni-Motsheg', line.replace(
            'Legakwa Ntabeni-Motshegwa', ''))

    def test_the_queue_line_stops_saying_waiting_and_says_rejected(self):
        """The whole point: the wording is the SAME one FNB's own rejection
        produces, because it comes from the same batch status."""
        batch = self._batch()
        pr = self._request(batch)
        before, tone_before = open_reason(
            batch_status=batch.status, has_batch=True)
        self.assertIn('Waiting for your authorisation', before)
        self.assertEqual(tone_before, 'wait')

        self._post(pr)
        batch.refresh_from_db()
        after, tone_after = open_reason(
            batch_status=batch.status, failure_reason=batch.failure_reason,
            has_batch=True)
        self.assertIn('Rejected by FNB', after)
        self.assertEqual(tone_after, 'reject')

    def test_the_payment_request_stays_open_for_reload(self):
        """A rejection is not a conclusion. Closing it here would drop the
        request out of the duplicate-payment control."""
        pr = self._request(self._batch())
        self._post(pr)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_it_writes_an_audit_row_naming_who_and_why(self):
        pr = self._request(self._batch())
        self._post(pr, 'Beneficiary account closed')
        row = AuditLog.objects.filter(
            table_name='fnb_fnbbatch', record_id=str(pr.fnb_batch_id)).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.user, self.koketso)
        self.assertEqual(row.old_values['status'],
                         FNBBatchSubmission.Status.SUBMITTED)
        self.assertEqual(row.new_values['status'],
                         FNBBatchSubmission.Status.FAILED)
        self.assertIn('Beneficiary account closed', row.description)

    # ── the refusals ────────────────────────────────────────────────────────

    def test_money_that_has_already_left_can_never_be_called_a_rejection(self):
        batch = self._batch(FNBBatchSubmission.Status.SETTLED, key='settled')
        pr = self._request(batch)
        r = self._post(pr)
        self.assertEqual(r.status_code, 409)
        batch.refresh_from_db()
        self.assertEqual(batch.status, FNBBatchSubmission.Status.SETTLED)

    def test_a_payment_never_loaded_to_fnb_is_refused(self):
        pr = self._request(None)
        r = self._post(pr)
        self.assertEqual(r.status_code, 409)
        self.assertIn('never loaded', r.json()['detail'])

    def test_it_cannot_be_recorded_twice(self):
        pr = self._request(self._batch())
        self.assertEqual(self._post(pr).status_code, 200)
        self.assertEqual(self._post(pr).status_code, 409)

    def test_a_reason_is_required(self):
        pr = self._request(self._batch())
        r = self._post(pr, reason='  ')
        self.assertEqual(r.status_code, 400)
        pr.fnb_batch.refresh_from_db()
        self.assertEqual(pr.fnb_batch.status, FNBBatchSubmission.Status.SUBMITTED)

    def test_someone_outside_the_accounts_team_cannot_do_it(self):
        pr = self._request(self._batch())
        r = self._post(pr, as_user=self.outsider)
        self.assertEqual(r.status_code, 403)
        pr.fnb_batch.refresh_from_db()
        self.assertEqual(pr.fnb_batch.status, FNBBatchSubmission.Status.SUBMITTED)

    # ── the button only shows where it makes sense ──────────────────────────

    def test_the_button_shows_while_it_waits_and_hides_once_recorded(self):
        pr = self._request(self._batch())
        self.client.force_login(self.koketso)
        d = self.client.get(f'/api/v1/payment-requests/{pr.id}/')
        self.assertEqual(d.status_code, 200, d.content)
        self.assertTrue(d.json()['can_mark_fnb_rejected'])

        self._post(pr)
        d2 = self.client.get(f'/api/v1/payment-requests/{pr.id}/')
        self.assertFalse(d2.json()['can_mark_fnb_rejected'])

    def test_someone_outside_the_team_cannot_even_open_the_pack(self):
        """The read widening is NARROW: it reaches the named accounts team and
        nobody else."""
        pr = self._request(self._batch())
        self.client.force_login(self.outsider)
        d = self.client.get(f'/api/v1/payment-requests/{pr.id}/')
        self.assertEqual(d.status_code, 403)

    def test_the_read_widening_grants_no_approval_power(self):
        """Seeing the pack must never become signing it off."""
        from taskboard.payment_views import _is_first_approver
        self.assertFalse(_is_first_approver(self.koketso))
