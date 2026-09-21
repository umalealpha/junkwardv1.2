"""The committee can now FIX a branch code, not just be told about it.

Until 18-Sep-2026 PAY-BANK-05 could find the fault that caused ten AC08 rejects
and BWP 677,284.93, and the only way out was to cancel the request and raise it
again — `branch_code` is not an amendable field and EXCEPTION is not an
amendable state. A control nobody can act on is half a control.

CFO, 18-Sep-2026: *"why dont we fix it"*.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from taskboard.branch_code_correction import correct_branch_code, may_correct
from taskboard.models import PaymentRequest, PaymentRequestChange
from taskboard.payment_amend import AmendError

User = get_user_model()
S = PaymentRequest.Status


def _url(pk):
    return reverse('v1-payment-request-correct-branch-code', args=[pk])


class _Fixture(TestCase):

    def setUp(self):
        def mk(u, e):
            return User.objects.create_user(username=u, email=e, password='x')

        self.cfo = mk('pganesharajah', 'pganesharajah@alphadirect.co.bw')
        self.committee = mk('omogomotsi', 'omogomotsi@alphadirect.co.bw')
        self.finance = mk('lntabeni', 'lntabeni@alphadirect.co.bw')
        self.raiser = mk('clerk9', 'clerk9@alphadirect.co.bw')
        self.stranger = mk('nobody', 'nobody@alphadirect.co.bw')

        self.pr = PaymentRequest.objects.create(
            ref='PAY/T/9001', subject='A supplier', payee='A Supplier',
            entity='ADIC', currency='BWP', total=Decimal('4370.00'),
            status=S.EXCEPTION, exception_control='PAY-BANK-05',
            bank_name='Stanbic Bank', account_number='9001234567',
            branch_code='6700',           # the live 4-digit reject
            created_by=self.raiser)
        self.c = APIClient()


class WhoMayCorrectTests(_Fixture):

    def test_the_committee_may(self):
        self.assertTrue(may_correct(self.committee, self.pr))

    def test_a_finance_approver_may(self):
        self.assertTrue(may_correct(self.finance, self.pr))

    def test_the_cfo_may(self):
        self.assertTrue(may_correct(self.cfo, self.pr))

    def test_the_person_who_raised_it_may_NOT(self):
        # The whole point of the exception is that somebody else checks the
        # bank details. Letting the raiser correct them quietly undoes that.
        self.assertFalse(may_correct(self.raiser, self.pr))

    def test_a_stranger_may_not(self):
        self.assertFalse(may_correct(self.stranger, self.pr))

    def test_a_SUPERUSER_who_raised_it_may_NOT(self):
        # _is_cfo() is true for ANY superuser, and Pramod keeps Super Admin, so
        # the old `and not _is_cfo(user)` let a superuser correct the bank
        # details on a request he raised himself — while the docstring promised
        # he could not (Fable 5.1, 18-Sep-2026). The raiser rule is absolute.
        su = User.objects.create_user('su_raiser', 'su@alphadirect.co.bw', 'x',
                                      is_superuser=True, is_staff=True)
        PaymentRequest.objects.filter(pk=self.pr.pk).update(created_by=su)
        self.pr.refresh_from_db()
        self.assertFalse(may_correct(su, self.pr))


class CorrectingItTests(_Fixture):

    def test_a_good_code_is_written_and_logged(self):
        out = correct_branch_code(self.pr, self.committee, branch_code='064967')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '064967')
        self.assertEqual(out['before'], '6700')
        self.assertEqual(out['after'], '064967')

        row = PaymentRequestChange.objects.get(request=self.pr, field='branch_code')
        self.assertEqual(row.value_before, '6700')
        self.assertEqual(row.value_after, '064967')
        self.assertEqual(row.actor_id, self.committee.id)
        self.assertTrue(row.actor_name, 'attribution is read, never typed')

    def test_a_blank_before_is_recorded_readably(self):
        PaymentRequest.objects.filter(pk=self.pr.pk).update(branch_code='')
        self.pr.refresh_from_db()
        correct_branch_code(self.pr, self.committee, branch_code='064967')
        row = PaymentRequestChange.objects.get(request=self.pr, field='branch_code')
        self.assertEqual(row.value_before, '(blank)')

    def test_a_still_bad_code_is_refused_with_the_reason(self):
        # Refused HERE, with the plain sentence — not accepted and discovered
        # again by the bank. Same shared rule as the capture screen.
        with self.assertRaises(AmendError) as e:
            correct_branch_code(self.pr, self.committee, branch_code='64967')
        self.assertIn('064967', e.exception.detail)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '6700', 'nothing was written')

    def test_an_account_number_in_the_branch_box_is_refused(self):
        with self.assertRaises(AmendError) as e:
            correct_branch_code(self.pr, self.committee, branch_code='60000000000')
        self.assertIn('account number', e.exception.detail)

    def test_the_same_value_is_not_a_change(self):
        with self.assertRaises(AmendError):
            correct_branch_code(self.pr, self.committee, branch_code='6700')

    def test_a_refusal_leaves_the_request_exactly_where_it_was(self):
        # 🔴 Nothing here may dead-end a payment.
        with self.assertRaises(AmendError):
            correct_branch_code(self.pr, self.committee, branch_code='nonsense')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, S.EXCEPTION)
        self.assertEqual(self.pr.exception_control, 'PAY-BANK-05')

    def test_correcting_does_not_decide_the_exception(self):
        correct_branch_code(self.pr, self.committee, branch_code='064967')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, S.EXCEPTION,
                         'it fixes the bank details, it does not approve anything')

    def test_the_account_number_is_never_touched(self):
        # The one field. If this ever fails, somebody widened the door.
        before = self.pr.account_number
        correct_branch_code(self.pr, self.committee, branch_code='064967')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.account_number, before)


class ABranchCodeCHOOSESTheBankTests(_Fixture):
    """🔴 THE ASSUMPTION THIS MODULE WAS BUILT ON WAS WRONG.

    I wrote that a branch code "cannot redirect money to a different person,
    because the ACCOUNT NUMBER identifies the payee", and told the CFO so when
    he asked whether to ship it. Omni's own payroll/bank_codes.py says the
    opposite in its first paragraph: the FIRST TWO DIGITS of a six-digit
    Botswana sort code identify the BANK (28 FNB, 06 Stanbic, 29 Absa,
    20 Bank Gaborone). Account numbers are per bank, so the same number sent to
    a different bank can be a different account holder.

    So an unchecked correction was a payment-redirection door — the exact fraud
    PAY-BANK-01 exists to stop. Caught by Fable 5.1, 18-Sep-2026.
    """

    def test_a_code_belonging_to_another_bank_is_refused(self):
        # The payee banks with Stanbic. 290167 is an ABSA code.
        with self.assertRaises(AmendError) as e:
            correct_branch_code(self.pr, self.committee, branch_code='290167')
        self.assertIn('Absa', e.exception.detail)
        self.assertIn('Stanbic', e.exception.detail)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '6700', 'nothing was written')

    def test_an_fnb_code_on_a_non_fnb_payee_is_refused(self):
        # 287867 is FNB's own branch — the exact substitution that caused the
        # AC08s in the first place, now impossible to enter by hand either.
        with self.assertRaises(AmendError) as e:
            correct_branch_code(self.pr, self.committee, branch_code='287867')
        self.assertIn('First National Bank', e.exception.detail)

    def test_the_right_banks_code_is_accepted(self):
        # 064967 is a Stanbic code and the payee banks with Stanbic.
        out = correct_branch_code(self.pr, self.committee, branch_code='064967')
        self.assertEqual(out['after'], '064967')
        self.assertIn('Stanbic', out['bank_for_code'])

    def test_an_fnb_payee_may_have_an_fnb_code(self):
        PaymentRequest.objects.filter(pk=self.pr.pk).update(bank_name='FNB Botswana')
        self.pr.refresh_from_db()
        correct_branch_code(self.pr, self.committee, branch_code='287867')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '287867')

    def test_an_unrecognised_prefix_is_ALLOWED_but_named(self):
        # A new bank must be payable — refusing what we cannot place would
        # dead-end a payee, and this module may never do that. It says so
        # instead, so a person can look.
        out = correct_branch_code(self.pr, self.committee, branch_code='777767')
        self.assertEqual(out['after'], '777767')
        self.assertIn('could not place', out['bank_for_code'])

    def test_EVERY_bank_the_code_table_names_is_recognised(self):
        """🔴 The second hole in the same guard.

        My first version compared banks using `_OTHER_BANK_TOKENS`, which is a
        list of words meaning "not FNB" — a different job. Bank Gaborone,
        First Capital, State Bank of India and Bank of Botswana have no word in
        it, so they fell through to "one side unrecognised, allow" and a Bank
        Gaborone payee happily accepted an Absa branch code. Four banks wide
        open in the guard written to close exactly that door.

        This is the table Fable ran against the broken version. Every row it
        reported as wrongly ALLOWED must now be refused.
        """
        from payroll.bank_codes import derive_bank_name
        from taskboard.branch_code_correction import _same_bank

        must_refuse = [
            ('Bank Gaborone', '290167'),                     # Absa code
            ('Bank Gaborone', '064967'),                     # Stanbic code
            ('Stanbic Bank', '200167'),                      # Bank Gaborone code
            ('Absa', '200167'),
            ('First Capital Bank', '064967'),
            ('Bank Gaborone First National Bank', '290167'),  # a LIVE spelling
            ('Bank Gaborone', '287867'),                     # FNB code
            ('State Bank of India', '290167'),
        ]
        for payee, code in must_refuse:
            with self.subTest(payee=payee, code=code):
                self.assertFalse(_same_bank(derive_bank_name(code) or '', payee),
                                 f'{code} must not be accepted for {payee}')

    def test_legitimate_corrections_are_still_allowed(self):
        # A guard that refuses good work gets switched off. These must pass.
        from payroll.bank_codes import derive_bank_name
        from taskboard.branch_code_correction import _same_bank

        must_allow = [
            ('Barclays Bank', '290167'),        # Absa Botswana WAS Barclays
            ('Bank Gaborone', '200167'),
            ('Stanbic Bank Botswana', '064967'),
            ('FNB Botswana', '287867'),
            ('FNB Gaborone', '287867'),         # an FNB BRANCH, not Bank Gaborone
            ('State Bank of India', '500167'),
            ('Bank of Botswana', '910167'),
            ('Botswana Savings Bank', '064967'),  # not in the table -> cannot say
            ('Absa Bank Botswana', '777767'),     # unknown prefix -> cannot say
        ]
        for payee, code in must_allow:
            with self.subTest(payee=payee, code=code):
                self.assertTrue(_same_bank(derive_bank_name(code) or '', payee),
                                f'{code} must be allowed for {payee}')

    def test_another_bank_in_the_string_beats_the_fnb_words(self):
        from taskboard.branch_code_correction import _canonical_bank
        self.assertEqual(_canonical_bank('Bank Gaborone First National Bank'),
                         'Bank Gaborone')
        self.assertEqual(_canonical_bank('FNB Gaborone'),
                         'First National Bank Botswana')

    def test_the_log_records_which_bank_the_code_belongs_to(self):
        # The immutable log is what an auditor reads — it must say "a Stanbic
        # code on an X payee", not just that something changed.
        PaymentRequest.objects.filter(pk=self.pr.pk).update(bank_name='Stanbic Bank')
        self.pr.refresh_from_db()
        correct_branch_code(self.pr, self.committee, branch_code='064967')
        row = PaymentRequestChange.objects.get(request=self.pr, field='branch_code')
        self.assertIn('Stanbic', row.reason)
        self.assertIn('064967', row.reason)

    def test_a_payee_with_no_bank_recorded_is_not_dead_ended(self):
        PaymentRequest.objects.filter(pk=self.pr.pk).update(bank_name='')
        self.pr.refresh_from_db()
        correct_branch_code(self.pr, self.committee, branch_code='064967')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '064967')


class WhenItIsTooLateTests(_Fixture):

    def test_a_paid_request_cannot_be_corrected_here(self):
        PaymentRequest.objects.filter(pk=self.pr.pk).update(status=S.PAID)
        self.pr.refresh_from_db()
        with self.assertRaises(AmendError) as e:
            correct_branch_code(self.pr, self.cfo, branch_code='064967')
        self.assertIn('by hand', e.exception.detail,
                      'it must say what the person can actually do instead')

    def test_pending_finance_is_still_correctable(self):
        PaymentRequest.objects.filter(pk=self.pr.pk).update(
            status=S.PENDING_FINANCE)
        self.pr.refresh_from_db()
        correct_branch_code(self.pr, self.finance, branch_code='064967')
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '064967')


class TheEndpointTests(_Fixture):

    def test_the_committee_can_correct_it_over_the_api(self):
        self.c.force_authenticate(self.committee)
        r = self.c.post(_url(self.pr.pk), {'branch_code': '064967'}, format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['after'], '064967')
        self.assertTrue(r.json()['changes'], 'the change log rides back')

    def test_a_bad_code_comes_back_as_a_readable_refusal(self):
        self.c.force_authenticate(self.committee)
        r = self.c.post(_url(self.pr.pk), {'branch_code': '64967'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['control'], 'PAY-BANK-05')
        self.assertIn('064967', r.json()['detail'])

    def test_the_raiser_is_refused_over_the_api_too(self):
        self.c.force_authenticate(self.raiser)
        r = self.c.post(_url(self.pr.pk), {'branch_code': '064967'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_a_signed_out_caller_gets_nowhere(self):
        r = self.c.post(_url(self.pr.pk), {'branch_code': '064967'}, format='json')
        self.assertIn(r.status_code, (401, 403))
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.branch_code, '6700')


class EveryInstructionIsSeenTests(TestCase):
    """Item 2: three screens read only the FIRST bank instruction of a request.

    A request paid line by line becomes one instruction per line. On
    16-Sep-2026 a supplier request became four rejected instructions and the
    request's single `fnb_batch` FK pointed at one of them — so a request whose
    line 1 settled and whose lines 2-4 were rejected showed nothing wrong on
    the register, in its headline count, or on the CFO's authorisation drawer.
    """

    def setUp(self):
        from banking.models import BankAccount
        from core.models import Currency
        from ledger.models import Account
        # Create the currency the GL account needs rather than leaning on seed
        # data — a test that depends on what happens to be in the database is a
        # test that passes on one machine and fails on another.
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.user = User.objects.create_user('clerk8', 'clerk8@alphadirect.co.bw', 'x')
        gl = Account.objects.create(code='280102', name='FNB Current',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345671',
            bank_name='FNB Botswana', gl_account=gl)
        self.pr = PaymentRequest.objects.create(
            ref='PAY/T/9100', subject='Split supplier', payee='A Supplier',
            entity='ADIC', currency='BWP', total=Decimal('13560.02'),
            status=S.PAID, processing_method='individual',
            created_by=self.user)

    def _batch(self, key, status, reason=''):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=key, source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal('3390.00'), status=status,
            failure_reason=reason, payment_request=self.pr)

    def test_a_later_rejected_instruction_is_flagged(self):
        from taskboard.payment_views import _any_batch_failed
        settled = self._batch('SPLIT-A', 'settled')
        self._batch('SPLIT-B', 'failed', 'AG01: TRANSACTION FORBIDDEN')
        # The request's own FK points at the one that SETTLED — the exact shape
        # that used to read as perfectly fine.
        PaymentRequest.objects.filter(pk=self.pr.pk).update(fnb_batch=settled)
        self.pr.refresh_from_db()
        self.assertTrue(_any_batch_failed(self.pr))

    def test_all_settled_is_not_flagged(self):
        from taskboard.payment_views import _any_batch_failed
        self._batch('SPLIT-C', 'settled')
        self._batch('SPLIT-D', 'settled')
        self.pr.refresh_from_db()
        self.assertFalse(_any_batch_failed(self.pr))

    def test_the_reason_names_what_the_bank_said_on_the_failed_one(self):
        from taskboard.payment_views import _reject_reason
        settled = self._batch('SPLIT-E', 'settled')
        self._batch('SPLIT-F', 'failed', 'AC08: BRANCH CODE IS INVALID')
        PaymentRequest.objects.filter(pk=self.pr.pk).update(fnb_batch=settled)
        self.pr.refresh_from_db()
        self.assertIn('AC08', _reject_reason(self.pr))

    def test_two_different_rejects_are_both_reported(self):
        # "AC08 on one line and AG01 on another" is two problems, not one.
        from taskboard.payment_views import _reject_reason
        self._batch('SPLIT-G', 'failed', 'AC08: BRANCH CODE IS INVALID')
        self._batch('SPLIT-H', 'failed', 'AG01: TRANSACTION FORBIDDEN')
        self.pr.refresh_from_db()
        reason = _reject_reason(self.pr)
        self.assertIn('AC08', reason)
        self.assertIn('AG01', reason)

    def test_an_unsplit_request_behaves_exactly_as_before(self):
        from taskboard.payment_views import _any_batch_failed, _reject_reason
        b = self._batch('SOLO-A', 'failed', 'AC08: BRANCH CODE IS INVALID')
        PaymentRequest.objects.filter(pk=self.pr.pk).update(fnb_batch=b)
        self.pr.refresh_from_db()
        self.assertTrue(_any_batch_failed(self.pr))
        self.assertEqual(_reject_reason(self.pr), 'AC08: BRANCH CODE IS INVALID')

    def test_a_request_with_no_batch_at_all_is_not_flagged(self):
        from taskboard.payment_views import _any_batch_failed
        self.assertFalse(_any_batch_failed(self.pr))


class TheScreensActuallyUseThemTests(TestCase):
    """Mentioned is not invoked.

    The tests above prove `_any_batch_failed` and `_reject_reason` are correct.
    They do NOT prove the screens call them — and a helper nothing calls is the
    exact failure the notebook records (a whole CEO leg unreachable while every
    test passed). Reverting the call sites left those tests green, which is how
    this gap was found.

    Parsed with `ast`, not grepped: a substring search is satisfied by the
    function's own definition and by this docstring.
    """

    @staticmethod
    def _calls_in(fn_name):
        import ast
        import pathlib
        from taskboard import payment_views
        tree = ast.parse(pathlib.Path(payment_views.__file__)
                         .read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                return {n.func.id for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        raise AssertionError(f'{fn_name} is not in taskboard/payment_views.py')

    def test_the_register_row_asks_every_instruction(self):
        self.assertIn('_any_batch_failed', self._calls_in('payment_requests'),
                      'the register row must flag a split request whose LATER '
                      'instruction was rejected')

    def test_the_cfo_authorisation_drawer_asks_every_instruction(self):
        calls = self._calls_in('payment_request_detail')
        self.assertIn('_any_batch_failed', calls)
        self.assertIn('_reject_reason', calls,
                      'and it must show what the bank actually said')

    def test_nothing_reads_the_single_fk_for_rejection_any_more(self):
        # The old expression, in the three places this change owns. If it comes
        # back, a part-rejected split request goes quiet again.
        import pathlib
        from taskboard import payment_views
        src = pathlib.Path(payment_views.__file__).read_text(encoding='utf-8')
        self.assertNotIn(
            "'bank_rejected': bool(p.fnb_batch_id", src,
            'a reader went back to the single-batch FK')


class TheHeadlineCountSeesEveryInstructionTests(TestCase):
    """The "you have N rejected payments" figure on the register.

    It used `qs.filter(fnb_batch__status='failed')` — one batch — so a request
    paid line by line whose LATER instruction was rejected was not counted.
    Reverting that line left every other test green, which is the same
    untested-call-site hole the helper tests had (Fable 5.1, 18-Sep-2026).
    """

    def setUp(self):
        from banking.models import BankAccount
        from core.models import Currency
        from ledger.models import Account
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.cfo = User.objects.create_user(
            'pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x')
        gl = Account.objects.create(code='280103', name='FNB Current',
                                    account_type='asset')
        self.acct = BankAccount.objects.create(
            account_name='ADIC Main', account_number='62812345672',
            bank_name='FNB Botswana', gl_account=gl)
        self.c = APIClient()

    def _request(self, ref):
        return PaymentRequest.objects.create(
            ref=ref, subject=ref, payee='A Supplier', entity='ADIC',
            currency='BWP', total=Decimal('100.00'), status=S.PAID,
            created_by=self.cfo)

    def _batch(self, key, status, request):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=key, source_account=self.acct, payment_count=1,
            total_amount_bwp=Decimal('100.00'), status=status,
            payment_request=request)

    def _count(self):
        self.c.force_authenticate(self.cfo)
        r = self.c.get('/api/v1/payment-requests/?register=1&all=1')
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()['bank_rejected_count']

    def test_a_split_request_whose_later_line_failed_is_counted(self):
        pr = self._request('PAY/T/9200')
        settled = self._batch('HC-A', 'settled', pr)
        self._batch('HC-B', 'failed', pr)
        # The FK points at the one that SETTLED — the shape that used to be
        # invisible to this count.
        PaymentRequest.objects.filter(pk=pr.pk).update(fnb_batch=settled)
        self.assertEqual(self._count(), 1)

    def test_a_request_with_two_failed_instructions_counts_ONCE(self):
        # Proves the .distinct() — the join would otherwise report 2.
        pr = self._request('PAY/T/9201')
        self._batch('HC-C', 'failed', pr)
        self._batch('HC-D', 'failed', pr)
        self.assertEqual(self._count(), 1)

    def test_a_fully_settled_request_is_not_counted(self):
        pr = self._request('PAY/T/9202')
        self._batch('HC-E', 'settled', pr)
        self.assertEqual(self._count(), 0)

    def test_a_request_with_no_batch_is_not_counted(self):
        self._request('PAY/T/9203')
        self.assertEqual(self._count(), 0)
