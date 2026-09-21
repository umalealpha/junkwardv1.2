"""
fnb/test_partial_reject_recovery.py

Manus nine-area retest P2 (2026-08-25) — the real gap in bank-rejection recovery.

A WHOLE-batch reject (groupStatus RJCT) already released every payment so it
could be re-sent (Fable H1). A PARTIAL one did not: PART / ACWC landed the batch
in ACKNOWLEDGED with the comment "needs per-txn reconcile" and nothing ever did
that reconcile — so a payment the bank refused inside an otherwise-accepted batch
kept `bank_submitted_at` set and could never go again. Claims payments reach FNB
through the same path (taskboard/fnb_autoload.py), so they were affected too.

Fixture shape follows the existing RR10 fixture in fnb/tests.py.
"""
from datetime import date, datetime, timedelta, timezone as _tz
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from banking.models import BankAccount
from billing.models import Contact
from core.models import Company, Currency, UserProfile
from fnb.models import FNBBatchSubmission
from fnb.payments import bank_view_of, refresh_batch_status
from ledger.models import Account
from payments.models import Payment

SUBMITTED_AT = datetime(2026, 8, 25, 6, 0, tzinfo=_tz.utc)


class _Resp:
    def __init__(self, j):
        self.json = j
        self.status_code = 200


class PartialRejectRecoveryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='PART', name='Partial Co',
                                             base_currency=cls.bwp)
        cls.user = User.objects.create_superuser('partclerk', 'part@x.co', 'x')
        UserProfile.objects.get_or_create(
            user=cls.user, defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER,
                                     'is_active': True})
        cls.gl = Account.objects.create(
            code='PART-BANK', name='Partial test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='Partial current',
            account_number='000000009', currency_code_id='BWP')
        cls.payee_a = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Supplier Alpha',
            currency_code=cls.bwp, company=cls.company)
        cls.payee_b = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Supplier Beta',
            currency_code=cls.bwp, company=cls.company)

    def _payment(self, payee, amount):
        return Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=payee, company=self.company, bank_account=self.gl,
            payment_date=date(2026, 8, 25), currency_code=self.bwp,
            amount=Decimal(amount),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.CONFIRMED,
            approval_status=Payment.ApprovalStatus.APPROVED,
            is_once_off=True, payee_name=payee.name,
            payee_account_number='1234567', payee_branch_code='293567',
            bank_submitted_at=SUBMITTED_AT, created_by=self.user,
        )

    def _batch(self, payments, ref='ref-part-1'):
        b = FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-TEST-PART-{ref}',
            source_account=self.source, payment_count=len(payments),
            total_amount_bwp=sum(p.amount for p in payments),
            currency_code='BWP', status=FNBBatchSubmission.Status.SUBMITTED,
            fnb_reference=ref,
        )
        b.payments.set(payments)
        return b

    def _refresh(self, batch, body):
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.get.return_value = _Resp(body)
            refresh_batch_status(batch)
        batch.refresh_from_db()
        return batch

    @staticmethod
    def _e2e(payment):
        """The endToEndId omni actually sent for this payment."""
        return bank_view_of(payment)['our_reference']

    def _part_body(self, rejected, accepted):
        rows = [{
            'originalEndToEndId': self._e2e(rejected),
            'transactionStatus': 'VALIDATION_FAILED',
            'statusReasonInformation': [
                {'reason': 'RR10', 'additionalInformation': 'INVALID CHARACTER SET'}],
        }, {
            'originalEndToEndId': self._e2e(accepted),
            'transactionStatus': 'ACSC',
            'statusReasonInformation': [],
        }]
        return {'groupStatus': 'PART',
                'originalPaymentInformation': [{
                    'statusReasonInformation': [],
                    'transactionInfoAndStatus': rows,
                }]}

    # ---- the fix -------------------------------------------------------
    def test_only_the_rejected_payment_is_released(self):
        bad = self._payment(self.payee_a, '100.00')
        good = self._payment(self.payee_b, '200.00')
        batch = self._batch([bad, good])

        self._refresh(batch, self._part_body(bad, good))

        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertIsNone(bad.bank_submitted_at,
                          'the rejected payment must be freed to re-send')
        self.assertEqual(good.bank_submitted_at, SUBMITTED_AT,
                         'the accepted payment moved money — do not touch it')

    def test_the_batch_records_which_payment_and_why(self):
        bad = self._payment(self.payee_a, '100.00')
        good = self._payment(self.payee_b, '200.00')
        batch = self._refresh(self._batch([bad, good]), self._part_body(bad, good))
        self.assertEqual(batch.status, FNBBatchSubmission.Status.ACKNOWLEDGED)
        self.assertIn(bad.payment_number, batch.failure_reason)
        self.assertIn('RR10', batch.failure_reason)
        self.assertIn('1 released', batch.failure_reason)

    def test_an_acwc_batch_with_no_rejects_releases_nothing(self):
        """ACWC is routine — the bank bumped the execution date. The money moved."""
        p = self._payment(self.payee_a, '100.00')
        batch = self._batch([p], ref='ref-acwc-clean')
        self._refresh(batch, {
            'groupStatus': 'ACWC',
            'originalPaymentInformation': [{
                'transactionInfoAndStatus': [{
                    'originalEndToEndId': self._e2e(p),
                    'transactionStatus': 'ACSC',
                }],
            }],
        })
        p.refresh_from_db()
        self.assertEqual(p.bank_submitted_at, SUBMITTED_AT)

    # ---- fail-closed ---------------------------------------------------
    def test_an_unmatchable_reject_releases_nothing_and_says_so(self):
        """Guessing which payment a reject belongs to is the RR10 mistake. An id
        that pins to no single payment must leave every payment alone."""
        p = self._payment(self.payee_a, '100.00')
        batch = self._batch([p], ref='ref-part-unmatched')
        self._refresh(batch, {
            'groupStatus': 'PART',
            'originalPaymentInformation': [{
                'transactionInfoAndStatus': [{
                    'originalEndToEndId': 'SOMETHING WE NEVER SENT',
                    'transactionStatus': 'RJCT',
                    'statusReasonInformation': [{'reason': 'AC01'}],
                }],
            }],
        })
        p.refresh_from_db()
        self.assertEqual(p.bank_submitted_at, SUBMITTED_AT)
        batch.refresh_from_db()
        self.assertIn('1 unmatched', batch.failure_reason)

    # ---- re-poll idempotency (Fable H90, 2026-08-25) -------------------
    def test_re_polling_the_same_batch_does_not_re_release(self):
        """This function runs on EVERY poll, and ACKNOWLEDGED is exactly the
        state a person comes back and refreshes."""
        bad = self._payment(self.payee_a, '100.00')
        good = self._payment(self.payee_b, '200.00')
        batch = self._batch([bad, good], ref='ref-part-repoll')
        body = self._part_body(bad, good)

        batch = self._refresh(batch, body)
        bad.refresh_from_db()
        self.assertIsNone(bad.bank_submitted_at)
        after_first = batch.failure_reason
        self.assertIn(bad.payment_number, after_first)
        self.assertIn('1 released', after_first)

        batch = self._refresh(batch, body)          # same report, second poll
        # Nothing is released a second time...
        self.assertIn('0 released', batch.failure_reason)
        # ...the note does NOT accumulate (it would otherwise grow on every
        # refresh until the 2000-char cap pushed the real reason out of the field
        # a person reads)...
        self.assertEqual(batch.failure_reason.count(bad.payment_number), 1)
        self.assertLessEqual(len(batch.failure_reason), len(after_first) + 2)
        # ...and the line naming WHICH payment the bank refused survives the
        # re-poll, instead of vanishing because nothing was released this time.
        self.assertIn(bad.payment_number, batch.failure_reason)
        self.assertIn('RR10', batch.failure_reason)

    def test_an_old_report_cannot_unlock_a_resubmitted_payment(self):
        """THE ONE THAT MATTERS. Rejected in batch 1 -> released -> cause fixed
        -> resubmitted in batch 2 -> somebody refreshes batch 1. Without the
        guard, bank_submitted_at is cleared on an IN-FLIGHT payment, and that
        flag is the only thing stopping a third send. This company has already
        paid P399,338.10 twice."""
        bad = self._payment(self.payee_a, '100.00')
        good = self._payment(self.payee_b, '200.00')
        batch1 = self._batch([bad, good], ref='ref-part-b1')
        body1 = self._part_body(bad, good)

        self._refresh(batch1, body1)
        bad.refresh_from_db()
        self.assertIsNone(bad.bank_submitted_at, 'precondition: it was released')

        # The cause is fixed and the payment goes out again in a NEW batch.
        batch2 = self._batch([bad], ref='ref-part-b2')
        resent_at = SUBMITTED_AT + timedelta(days=1)
        Payment.objects.filter(pk=bad.pk).update(bank_submitted_at=resent_at)

        # Somebody refreshes the OLD batch. Its report still carries the reject.
        self._refresh(batch1, body1)

        bad.refresh_from_db()
        self.assertEqual(
            bad.bank_submitted_at, resent_at,
            'an old status report must never unlock a payment that is now '
            'in flight in a newer batch — that is a duplicate EFT')
        self.assertGreater(batch2.payments.count(), 0)

    def test_a_whole_batch_reject_cannot_unlock_a_resubmitted_payment_either(self):
        """The same flaw pre-existed in the whole-batch RJCT branch (Fable H1's
        release, earlier ship). Fixed in the same pass."""
        p = self._payment(self.payee_a, '100.00')
        batch1 = self._batch([p], ref='ref-rjct-b1')
        self._refresh(batch1, {'groupStatus': 'RJCT'})
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)

        self._batch([p], ref='ref-rjct-b2')
        resent_at = SUBMITTED_AT + timedelta(days=1)
        Payment.objects.filter(pk=p.pk).update(bank_submitted_at=resent_at)

        self._refresh(batch1, {'groupStatus': 'RJCT'})
        p.refresh_from_db()
        self.assertEqual(p.bank_submitted_at, resent_at)

    def test_a_whole_batch_reject_still_releases_everything(self):
        """The pre-existing RJCT behaviour (Fable H1) must be unchanged."""
        a = self._payment(self.payee_a, '100.00')
        b = self._payment(self.payee_b, '200.00')
        batch = self._batch([a, b], ref='ref-rjct-all')
        self._refresh(batch, {'groupStatus': 'RJCT'})
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNone(a.bank_submitted_at)
        self.assertIsNone(b.bank_submitted_at)
        self.assertEqual(batch.status, FNBBatchSubmission.Status.FAILED)
