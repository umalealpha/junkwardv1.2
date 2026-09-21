"""The three-way check must find every disagreement that exists on production.

Built from the shapes actually measured on live on 13-Sep-2026, not invented:
  paid in Omni  / batch FAILED    : 10
  paid in Omni  / batch SUBMITTED : 11
  NOT paid      / batch SETTLED   :  6, two of them CANCELLED

The cancelled-against-settled pair is the one worth naming separately: a person
decided to cancel a payment the bank had already made. That is not the same
problem as a queue that is merely out of date, and an automatic "fix" that
quietly flipped it back to paid would erase the human decision. The check
reports; a person decides.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from banking.models import BankAccount
from ledger.models import Account
from fnb.models import FNBBatchSubmission
from fnb.three_way_check import contradictions, open_batches, run
from taskboard.models import PaymentRequest


class TheCheckFindsEveryDisagreement(TestCase):

    def setUp(self):
        gl = Account.objects.create(code='280100', name='FNB Current Account',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345678',
            bank_name='FNB Botswana', gl_account=gl)
        self.who = User.objects.create_user('finuser', 'fin@alphadirect.co.bw', 'x')

    def _batch(self, status, days_old=1, key=None):
        b = FNBBatchSubmission.objects.create(
            idempotency_key=key or f'K-{status}-{FNBBatchSubmission.objects.count()}',
            source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal('1000.00'), status=status,
            submitted_by=self.who)
        # created_at is auto_now_add, so age has to be written after the fact.
        FNBBatchSubmission.objects.filter(pk=b.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=days_old))
        b.refresh_from_db()
        return b

    def _request(self, status, batch, total='1000.00'):
        return PaymentRequest.objects.create(
            ref=f'PAY/TEST/{PaymentRequest.objects.count():04d}',
            entity='Alpha Direct Insurance', category='supplier',
            currency='BWP', subject='Test payment', payee='A Supplier',
            total=Decimal(total), status=status, fnb_batch=batch)

    def test_a_blank_payee_still_names_who_is_being_paid(self):
        """321 of the 359 live payment requests have an EMPTY payee - the name
        is in `subject`. Every fixture above sets payee='A Supplier', which is
        exactly why the suite was green while the live screen showed a column
        of 27 blanks. This case is the one that tells the two apart."""
        r = self._request('paid', self._batch('failed'))
        PaymentRequest.objects.filter(pk=r.pk).update(
            payee='', subject='G2026004923 BUILDERS MAPS HARDWARE(B467BRZ)')
        row = contradictions()['paid_but_batch_failed'][0]
        self.assertEqual(row['payee'], 'G2026004923 BUILDERS MAPS HARDWARE(B467BRZ)',
                         'a row with no payee must still say whose money it is')

    def test_a_real_payee_is_preferred_over_the_subject(self):
        self._request('paid', self._batch('failed'))
        row = contradictions()['paid_but_batch_failed'][0]
        self.assertEqual(row['payee'], 'A Supplier')

    def test_paid_in_omni_but_the_bank_rejected_it(self):
        self._request('paid', self._batch('failed'))
        found = contradictions()
        self.assertEqual(len(found['paid_but_batch_failed']), 1)
        self.assertEqual(found['paid_but_batch_failed'][0]['batch_status'], 'failed')

    def test_paid_in_omni_but_the_bank_never_confirmed(self):
        self._request('paid', self._batch('submitted'))
        self.assertEqual(len(contradictions()['paid_but_batch_unconfirmed']), 1)

    def test_the_bank_settled_it_but_omni_still_shows_it_open(self):
        self._request('pending_cfo', self._batch('settled'))
        found = contradictions()
        self.assertEqual(len(found['settled_but_not_paid']), 1)
        self.assertEqual(found['cancelled_but_settled'], [],
                         'a pending request is not a cancelled one')

    def test_cancelled_against_a_settled_batch_is_its_own_finding(self):
        """The worst case, and the reason this check does not auto-correct:
        somebody cancelled a payment after the money had already left."""
        self._request('cancelled', self._batch('settled'))
        found = contradictions()
        self.assertEqual(len(found['cancelled_but_settled']), 1)
        self.assertEqual(found['settled_but_not_paid'], [],
                         'a cancelled request must not be counted twice')

    def test_agreement_is_not_reported(self):
        self._request('paid', self._batch('settled'))
        self.assertEqual(run()['contradiction_count'], 0)
        self.assertTrue(run()['clean'])

    def test_a_request_with_no_batch_is_never_a_contradiction(self):
        """87 live requests are marked paid with no FNB batch at all - paid by
        another route. Calling those a disagreement would bury the real ones."""
        PaymentRequest.objects.create(
            ref='PAY/TEST/NOBATCH', entity='Alpha Direct Insurance',
            category='supplier', currency='BWP', subject='Paid by cheque',
            payee='A Supplier', total=Decimal('500.00'), status='paid')
        self.assertEqual(run()['contradiction_count'], 0)

    def test_open_batches_report_age_amount_and_owner(self):
        self._batch('submitted', days_old=9)
        self._batch('submitted', days_old=1)
        self._batch('failed', days_old=30)
        out = open_batches(stale_days=7)
        self.assertEqual(out['groups']['submitted']['count'], 2)
        self.assertEqual(out['groups']['submitted']['over_threshold'], 1)
        self.assertEqual(out['groups']['failed']['over_threshold'], 1)
        row = out['groups']['failed']['rows'][0]
        self.assertEqual(row['owner'], 'finuser')
        self.assertGreaterEqual(row['age_days'], 29)

    def test_an_unknown_batch_is_reported_because_the_money_may_have_moved(self):
        """`unknown` means the request left and no clean answer came back. Those
        are the ones nobody may quietly resubmit."""
        self._batch('unknown', days_old=3)
        self.assertIn('unknown', open_batches()['groups'])

    def test_the_check_writes_nothing(self):
        b = self._batch('failed')
        p = self._request('paid', b)
        run()
        p.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(p.status, 'paid', 'the check must not correct anything')
        self.assertEqual(b.status, 'failed')


class WhoCanOpenTheExceptionScreen(TestCase):
    """The daily report names Kago Tshutlhedi and Pako Kago. On the morning it
    went live both of them were REFUSED by the screen the email points at --
    the gate was CFO/administrator only. A queue nobody can open is the unowned
    queue the cockpit exists to fix."""

    def setUp(self):
        self.url = reverse('v1-fnb-exception-cockpit')
        self.recipient = User.objects.create_user(
            'kago', 'ktshutlhedi@alphadirect.co.bw', 'x')
        self.outsider = User.objects.create_user(
            'someone', 'someone@alphadirect.co.bw', 'x')

    def test_a_person_the_report_is_sent_to_can_open_it(self):
        self.client.force_login(self.recipient)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_everyone_else_is_still_refused(self):
        self.client.force_login(self.outsider)
        self.assertIn(self.client.get(self.url).status_code, (403, 401))
