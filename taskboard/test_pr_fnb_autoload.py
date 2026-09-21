"""taskboard/test_pr_fnb_autoload.py — a signed-off request loads itself to FNB.

CFO 2026-08-20: "the team loads the payment request. and then they load the
payments again in FNB. So now create a link between payment request, which will
automatically translate into FNB loading."

The load moves no money. Same day, same person: "even in omni fnb area if I
approve, money doesn't leave — it goes to fnb actual banking system where I need
to approve again in the bank … I want to authorize all the payments through my
phone, which has dual factor authorization."

So what these tests pin is not a money control. It is that the work happens once,
that a failure is never silent, and that it cannot happen twice.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from banking.models import BankAccount
from core.models import Company, Currency
from ledger.models import Account
from taskboard.models import PaymentRequest

SOURCE_ACCT = '9' * 11


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='PRFNB', defaults={'name': 'PR Autoload Test Co',
                                    'base_currency': cls.bwp})
        cls.raiser = User.objects.create_user('pr_raiser', password='x')
        cls.approver = User.objects.create_user('pr_approver', password='x')
        cls.gl = Account.objects.create(
            code='PRFNB-BANK', name='PR autoload bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='PR test current',
            account_number=SOURCE_ACCT, currency_code_id='BWP')

    def _request(self, **over):
        kwargs = dict(
            ref='PR-TEST-0001', entity=self.company.name,
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='Invoice 5512 panel repair', payee='ABC Traders',
            line_items=[{'description': 'Panel repair', 'amount': 250.0,
                         'gl_code': '', 'ref': ''}],
            total=Decimal('250.00'),
            account_name='ABC Traders (Pty) Ltd', account_number='1234567',
            bank_name='FNB Botswana', branch_code='293567',
            account_type='CACC',
            status=PaymentRequest.Status.PENDING_CFO,
            created_by=self.raiser,
        )
        kwargs.update(over)
        return PaymentRequest.objects.create(**kwargs)


@override_settings(PAYMENT_REQUEST_AUTO_FNB=True,
                   PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={'default': SOURCE_ACCT})
class LoadRequestToFnbTests(_Base):

    def _load(self, pr, ref='R1G66R-pr-1'):
        from taskboard.fnb_autoload import load_request_to_fnb

        class _Resp:
            json = {'instructionId': ref}
            status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.return_value = _Resp()
            return load_request_to_fnb(pr, self.approver)

    def test_a_signed_off_request_reaches_the_bank_without_retyping(self):
        pr = self._request()
        out = self._load(pr)
        self.assertTrue(out['loaded'], msg=out['reason'])
        pr.refresh_from_db()
        self.assertIsNotNone(pr.fnb_batch_id)
        self.assertIsNotNone(pr.fnb_loaded_at)
        self.assertEqual(pr.fnb_load_error, '')
        self.assertIsNotNone(pr.payment_id)

    def test_the_bank_is_told_exactly_what_was_typed_on_the_request(self):
        """The whole point: the details are entered once, on the request."""
        pr = self._request(bank_narration='ALPHA DIRECT INV45678',
                           bank_our_reference='MOTOVAC INV45678')
        self._load(pr)
        pr.refresh_from_db()
        p = pr.payment
        self.assertEqual(p.payee_account_number, '1234567')
        self.assertEqual(p.payee_branch_code, '293567')
        self.assertEqual(p.bank_beneficiary_name, 'ABC Traders (Pty) Ltd')
        # The wording chosen on the request is what the bank is told.
        self.assertEqual(p.bank_our_reference, 'MOTOVAC INV45678')
        self.assertEqual(p.bank_narration, 'ALPHA DIRECT INV45678')
        self.assertEqual(p.amount, Decimal('250.00'))

    def test_with_no_wording_chosen_the_payment_number_is_the_floor(self):
        """FNB rejects an empty mandatory field, so something must go there.
        The request number is unique, which beats the same words on every
        payment in the batch — but it is a floor, never a default."""
        pr = self._request(bank_narration='', bank_our_reference='')
        self._load(pr, ref='R1G66R-pr-floor')
        pr.refresh_from_db()
        self.assertEqual(pr.payment.bank_our_reference, 'PR-TEST-0001')
        self.assertEqual(pr.payment.bank_narration, 'Invoice 5512 panel repair')

    def test_the_raiser_owns_the_payment_so_the_approver_can_release_it(self):
        """A request cannot be signed off by its own raiser, so making the
        raiser the payment's creator satisfies the two-person release rule
        through the workflow instead of an exemption."""
        pr = self._request()
        self._load(pr)
        pr.refresh_from_db()
        self.assertEqual(pr.payment.created_by_id, self.raiser.id)
        self.assertNotEqual(pr.payment.created_by_id, self.approver.id)

    def test_loading_twice_does_not_send_twice(self):
        pr = self._request()
        first = self._load(pr)
        pr.refresh_from_db()
        batch_id = pr.fnb_batch_id
        with mock.patch('fnb.payments.submit_eft_batch') as submit:
            again = self._load(pr, ref='R1G66R-pr-SECOND')
            submit.assert_not_called()
        pr.refresh_from_db()
        self.assertTrue(again['loaded'])
        self.assertEqual(pr.fnb_batch_id, batch_id)
        self.assertEqual(first['batch_id'], again['batch_id'])

    def test_no_account_number_is_reported_not_swallowed(self):
        pr = self._request(account_number='')
        out = self._load(pr)
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIn('account number', pr.fnb_load_error.lower())
        self.assertIsNone(pr.fnb_batch_id)

    def test_an_unknown_entity_refuses_rather_than_guessing_a_company(self):
        """`entity` is free text; 'Alpha Direct Insurance Company South Africa'
        would plausibly match two companies, and picking one would pay from the
        wrong account."""
        pr = self._request(entity='Some Entity That Does Not Exist')
        out = self._load(pr)
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIn('does not match a company', pr.fnb_load_error)

    def test_an_alias_maps_an_entity_whose_name_does_not_match(self):
        pr = self._request(entity='Alpha Direct Insurance Company South Africa')
        with override_settings(PAYMENT_REQUEST_ENTITY_ALIASES={
                'Alpha Direct Insurance Company South Africa': 'PRFNB'}):
            out = self._load(pr)
        self.assertTrue(out['loaded'], msg=out['reason'])

    def test_no_paying_account_configured_says_so(self):
        pr = self._request()
        with override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={}):
            out = self._load(pr)
        self.assertFalse(out['loaded'])
        self.assertIn('PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS', out['reason'])

    def test_the_switch_turns_it_off(self):
        pr = self._request()
        with override_settings(PAYMENT_REQUEST_AUTO_FNB=False):
            out = self._load(pr)
        self.assertFalse(out['loaded'])
        self.assertIn('switched off', out['reason'])

    def test_a_clean_reject_is_recorded_and_can_be_tried_again(self):
        """A 400 means FNB refused it and nothing moved."""
        from fnb.client import FNBAPIError
        from taskboard.fnb_autoload import load_request_to_fnb
        pr = self._request()
        with mock.patch('fnb.payments.submit_eft_batch',
                        side_effect=FNBAPIError(400, 'bad field')):
            out = load_request_to_fnb(pr, self.approver)   # must not raise
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIn('400', pr.fnb_load_error)
        self.assertIsNone(pr.fnb_loaded_at)          # claim released

    def test_an_indeterminate_failure_holds_the_claim(self):
        """A timeout or 5xx means the POST MAY have been accepted. Releasing
        the claim would mark it retry-eligible while the bank might hold an
        accepted batch — a double payment the day a retry button exists."""
        from fnb.client import FNBAPIError
        from taskboard.fnb_autoload import load_request_to_fnb
        for status in (502, 408, 0):
            pr = self._request(ref=f'PR-IND-{status}')
            with mock.patch('fnb.payments.submit_eft_batch',
                            side_effect=FNBAPIError(status, 'no clean answer')):
                out = load_request_to_fnb(pr, self.approver)
            self.assertFalse(out['loaded'], msg=str(status))
            pr.refresh_from_db()
            self.assertIsNotNone(pr.fnb_loaded_at, msg=f'{status} released the claim')
            self.assertIn('Check for this payment in FNB', pr.fnb_load_error)

    def test_an_unexpected_error_also_holds_the_claim(self):
        from taskboard.fnb_autoload import load_request_to_fnb
        pr = self._request(ref='PR-IND-ODD')
        with mock.patch('fnb.payments.submit_eft_batch',
                        side_effect=RuntimeError('something odd')):
            out = load_request_to_fnb(pr, self.approver)
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIsNotNone(pr.fnb_loaded_at)
        self.assertIn('Check FNB before', pr.fnb_load_error)

    def test_an_unexpected_error_names_what_happened(self):
        from taskboard.fnb_autoload import load_request_to_fnb
        pr = self._request(ref='PR-ODD-2')
        with mock.patch('fnb.payments.submit_eft_batch',
                        side_effect=RuntimeError('something odd')):
            out = load_request_to_fnb(pr, self.approver)   # must not raise
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIn('something odd', pr.fnb_load_error)


@override_settings(PAYMENT_REQUEST_AUTO_FNB=True,
                   PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={'default': SOURCE_ACCT})
class AutoLoadRefusalTests(_Base):
    """The four ways Fable found this could quietly do the wrong thing."""

    def _real_batch(self, ref):
        """fnb_batch is a real FK — a MagicMock cannot be assigned to it, so a
        stubbed submit must hand back an actual row."""
        from decimal import Decimal as D
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-TEST-PR-{ref}',
            source_account=self.source, payment_count=1,
            total_amount_bwp=D('250.00'), currency_code='BWP',
            status=FNBBatchSubmission.Status.SUBMITTED, fnb_reference=ref)

    def _load(self, pr):
        from taskboard.fnb_autoload import load_request_to_fnb

        class _Resp:
            json = {'instructionId': 'R1G66R-refusal'}
            status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.return_value = _Resp()
            return load_request_to_fnb(pr, self.approver)

    def test_it_will_not_debit_another_companys_account(self):
        """F1 was a CRITICAL: the paying account fell back to `.first()`, so a
        Veritas request could have been paid out of ADIC's FNB account."""
        from core.models import Company
        other, _ = Company.objects.get_or_create(
            code='PROTHER', defaults={'name': 'Other PR Co',
                                      'base_currency': self.bwp})
        pr = self._request(entity=other.name)
        out = self._load(pr)
        self.assertFalse(out['loaded'])
        self.assertIn('does not belong to', out['reason'])
        pr.refresh_from_db()
        self.assertIsNone(pr.fnb_batch_id)

    def test_a_second_concurrent_load_is_refused_by_the_claim(self):
        """F2: two sign-offs racing must not both load."""
        from taskboard.models import PaymentRequest as PR
        from taskboard.fnb_autoload import load_request_to_fnb
        pr = self._request()
        # Simulate the other caller having already claimed it.
        from django.utils import timezone as _tz
        PR.objects.filter(pk=pr.pk).update(fnb_loaded_at=_tz.now())
        with mock.patch('fnb.payments.submit_eft_batch') as submit:
            load_request_to_fnb(pr, self.approver)
            submit.assert_not_called()

    def test_a_pre_send_refusal_releases_its_claim(self):
        """Nothing reached the bank, so this one is safe to try again."""
        from taskboard.fnb_autoload import load_request_to_fnb
        pr = self._request(account_number='')
        out = load_request_to_fnb(pr, self.approver)
        self.assertFalse(out['loaded'])
        pr.refresh_from_db()
        self.assertIsNone(pr.fnb_loaded_at)
        self.assertTrue(pr.fnb_load_error)

    def test_a_foreign_currency_request_is_refused_not_sent(self):
        """F7: at rate 1 it would write a wrong amount_bwp, which the ceiling
        and the reports both read."""
        pr = self._request(currency='USD')
        out = self._load(pr)
        self.assertFalse(out['loaded'])
        self.assertIn('USD', out['reason'])

    def test_the_payment_date_is_sent_as_the_execution_date(self):
        """F6: a Friday-dated request must not load for execution today."""
        from datetime import timedelta
        from taskboard.fnb_autoload import load_request_to_fnb
        from django.utils import timezone as tz
        future = tz.localdate() + timedelta(days=3)
        pr = self._request(payment_date=future)
        with mock.patch('fnb.payments.submit_eft_batch') as submit:
            submit.return_value = self._real_batch('ref-exec-1')
            load_request_to_fnb(pr, self.approver)
        self.assertEqual(submit.call_args.kwargs['requested_execution_date'],
                         future)

    def test_a_past_payment_date_is_dropped(self):
        """FNB refuses a past execution date (DT01), so send none."""
        from datetime import timedelta
        from taskboard.fnb_autoload import load_request_to_fnb
        from django.utils import timezone as tz
        past = tz.localdate() - timedelta(days=5)
        pr = self._request(payment_date=past)
        with mock.patch('fnb.payments.submit_eft_batch') as submit:
            submit.return_value = self._real_batch('ref-exec-2')
            load_request_to_fnb(pr, self.approver)
        self.assertIsNone(submit.call_args.kwargs['requested_execution_date'])


@override_settings(PAYMENT_REQUEST_AUTO_FNB=True)
class ClaimsLeaveTheClaimsAccountTests(_Base):
    """CFO 2026-08-21: "claims payments will go through alpha Direct claims
    accounts… for alpha Direct any thing other than claims it goes though alpha
    Direct current account for example operational payments, refunds, rent,
    taxes etc."

    A single global paying account would have paid claims out of the operating
    account — wrong on the bank statement and wrong in the ledger.
    """

    CLAIMS_ACCT = '6' * 11
    CURRENT_ACCT = '5' * 11

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from banking.models import BankAccount
        from ledger.models import Account
        cls.claims_gl = Account.objects.create(
            code='PRFNB-CLAIMS', name='Claims bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.claims_bank = BankAccount.objects.create(
            gl_account=cls.claims_gl, bank_name='FNB',
            account_name='FNBB (Claims A/C)', account_number=cls.CLAIMS_ACCT,
            currency_code_id='BWP')
        cls.current_gl = Account.objects.create(
            code='PRFNB-CURRENT', name='Current bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True,
            owner_company=cls.company)
        cls.current_bank = BankAccount.objects.create(
            gl_account=cls.current_gl, bank_name='FNB',
            account_name='FNBB CHEQ A/C', account_number=cls.CURRENT_ACCT,
            currency_code_id='BWP')

    MAP = {'claim': CLAIMS_ACCT, 'default': CURRENT_ACCT}

    def _which_account(self, category):
        """Load and report which of our accounts was debited."""
        from taskboard.fnb_autoload import load_request_to_fnb
        from taskboard.models import PaymentRequest
        pr = self._request(ref=f'PR-ACCT-{category}', category=category)
        seen = {}

        def _capture(qs, *, source_account, user, **kw):
            seen['acct'] = source_account.account_number
            from fnb.models import FNBBatchSubmission
            return FNBBatchSubmission.objects.create(
                idempotency_key=f'ALPHA-EFT-TEST-ACCT-{category}',
                source_account=source_account, payment_count=1,
                total_amount_bwp=250, currency_code='BWP',
                status=FNBBatchSubmission.Status.SUBMITTED,
                fnb_reference=f'ref-{category}')

        with override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS=self.MAP), \
                mock.patch('fnb.payments.submit_eft_batch', side_effect=_capture):
            out = load_request_to_fnb(pr, self.approver)
        return out, seen.get('acct')

    def test_a_claim_is_paid_from_the_claims_account(self):
        from taskboard.models import PaymentRequest
        out, acct = self._which_account(PaymentRequest.Category.CLAIM)
        self.assertTrue(out['loaded'], msg=out['reason'])
        self.assertEqual(acct, self.CLAIMS_ACCT)

    def test_a_claim_is_never_paid_from_the_current_account(self):
        from taskboard.models import PaymentRequest
        _, acct = self._which_account(PaymentRequest.Category.CLAIM)
        self.assertNotEqual(acct, self.CURRENT_ACCT)

    def test_operational_refunds_rent_and_tax_use_the_current_account(self):
        """His examples: "operational payments, refunds, rent, taxes etc." """
        from taskboard.models import PaymentRequest
        for cat in (PaymentRequest.Category.SUPPLIER,
                    PaymentRequest.Category.VENDOR,
                    PaymentRequest.Category.PREMIUM_REFUND,
                    PaymentRequest.Category.PETTY_CASH):
            out, acct = self._which_account(cat)
            self.assertTrue(out['loaded'], msg=f'{cat}: {out["reason"]}')
            self.assertEqual(acct, self.CURRENT_ACCT, msg=cat)

    def test_a_kind_with_no_account_and_no_default_refuses(self):
        """Refusing beats guessing which account to debit."""
        from taskboard.fnb_autoload import load_request_to_fnb
        from taskboard.models import PaymentRequest
        pr = self._request(ref='PR-ACCT-NONE',
                           category=PaymentRequest.Category.CLAIM)
        with override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={'supplier': '1'}):
            out = load_request_to_fnb(pr, self.approver)
        self.assertFalse(out['loaded'])
        self.assertIn('No paying FNB account is set', out['reason'])

    def test_the_old_single_value_setting_is_honoured_as_the_default(self):
        """Someone may still have the earlier setting in .env; it must not be
        silently ignored."""
        from taskboard.fnb_autoload import source_account_number_for
        with override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS=self.CURRENT_ACCT):
            self.assertEqual(source_account_number_for('claim'), self.CURRENT_ACCT)
