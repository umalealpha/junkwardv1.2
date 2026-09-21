"""fnb/test_express_pay.py — Express Pay (CFO 2026-09-04).

FNB is MOCKED throughout (`fnb.express_pay.submit_eft_batch` or `FNBClient`);
nothing here reaches the bank. Omni stages a payment; the money leaves only when
the CFO releases it in the FNB app. Each test fails without its fix.

Run: manage.py test fnb.test_express_pay
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from fnb.tests import _PaymentFixture

SOURCE_ACCOUNTS = {'default': '000000004', 'claim': '000000004'}


def _profile(user, title, *, active=True):
    from core.models import UserProfile
    prof, _ = UserProfile.objects.get_or_create(user=user, defaults={'title': title, 'is_active': active})
    prof.title = title
    prof.is_active = active
    prof.save()
    return prof


class _ExpressFixture(_PaymentFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from core.models import UserProfile
        from ledger.models import Account
        # The company the phone pays from. The fixture bank account must belong
        # to it: _resolve_source_account scopes on gl_account.owner_company.
        from core.models import Company
        cls.adic, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance', 'base_currency': cls.bwp})
        Account.objects.filter(pk=cls.gl_bank.pk).update(owner_company=cls.adic)
        cls.cfo = User.objects.create_user('pganesharajah', email='pganesharajah@alphadirect.co.bw', password='x')
        _profile(cls.cfo, UserProfile.Title.CFO)
        cls.ceo = User.objects.create_user('arun.iyer', email='aiyer@alphadirect.co.bw', password='x')
        _profile(cls.ceo, UserProfile.Title.CEO)
        # The shared EXCO mailbox also carries the cfo title in Omni — must be OUT.
        cls.excoboard = User.objects.create_user('excoboard', email='excoboard@alphadirect.co.bw', password='x')
        _profile(cls.excoboard, UserProfile.Title.CFO)
        cls.coo = User.objects.create_user('coo.person', email='coo@alphadirect.co.bw', password='x')
        _profile(cls.coo, UserProfile.Title.COO)
        cls.clerk = User.objects.create_user('clerk', email='clerk@alphadirect.co.bw', password='x')
        _profile(cls.clerk, UserProfile.Title.ACCOUNTANT)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    BODY = {'payee_name': 'Guard Test Supplier', 'account_number': '62001234567',
            'bank_name': 'FNB', 'amount': '150.00', 'reference': 'INV 77', 'company': 'ADIC'}


class GateTests(_ExpressFixture):
    def test_the_gate_itself(self):
        from core.permissions import is_cfo_or_ceo
        self.assertTrue(is_cfo_or_ceo(self.cfo))
        self.assertTrue(is_cfo_or_ceo(self.ceo))
        self.assertFalse(is_cfo_or_ceo(self.excoboard), 'shared mailbox with the cfo title must be OUT')
        self.assertFalse(is_cfo_or_ceo(self.coo))
        self.assertFalse(is_cfo_or_ceo(self.clerk))
        self.assertFalse(is_cfo_or_ceo(None))
        # Superuser is NOT an arm: the QC robots and the Super Admin are superusers
        # and none of them is the CFO or the CEO (independent review 2026-09-04).
        self.assertTrue(self.staff.is_superuser)
        self.assertFalse(is_cfo_or_ceo(self.staff), 'a superuser Financial Controller must be OUT')
        # The CEO arm is bound to the real account, like the CFO arm: an admin who
        # re-titles a mailbox to "ceo" gains nothing.
        from core.models import UserProfile
        impostor = User.objects.create_user('ceo.mailbox', email='ceo@alphadirect.co.bw', password='x')
        _profile(impostor, UserProfile.Title.CEO)
        self.assertFalse(is_cfo_or_ceo(impostor), 'a ceo-titled stranger must be OUT')

    def test_a_deactivated_cfo_profile_loses_express_pay(self):
        from core.permissions import is_cfo_or_ceo
        from core.models import UserProfile
        _profile(self.cfo, UserProfile.Title.CFO, active=False)
        self.assertFalse(is_cfo_or_ceo(User.objects.get(pk=self.cfo.pk)))

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS=SOURCE_ACCOUNTS)
    def test_endpoint_refuses_everyone_but_cfo_and_ceo(self):
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            for who in (self.clerk, self.excoboard, self.coo, self.staff):
                r = self._client(who).post('/api/v1/fnb/express-pay/', self.BODY, format='json')
                self.assertEqual(r.status_code, 403, who.username)
            self.assertEqual(sub.call_count, 0)
            for who in (self.clerk, self.staff):
                self.assertEqual(self._client(who).get('/api/v1/fnb/recent-payees/').status_code, 403)
            # ...and the two people it is FOR get through the same door.
            sub.return_value = mock.Mock(id='1', fnb_reference='R', idempotency_key='K', status='submitted')
            body = {**self.BODY, 'confirm': True}
            self.assertEqual(self._client(self.cfo).post('/api/v1/fnb/express-pay/', body, format='json').status_code, 200)
            self.assertEqual(self._client(self.ceo).post('/api/v1/fnb/express-pay/', body, format='json').status_code, 200)
            self.assertEqual(sub.call_count, 2)

    def test_can_endpoint_tells_the_phone_who_sees_the_tile(self):
        self.assertTrue(self._client(self.cfo).get('/api/v1/fnb/express-pay/can/').json()['allowed'])
        self.assertTrue(self._client(self.ceo).get('/api/v1/fnb/express-pay/can/').json()['allowed'])
        self.assertFalse(self._client(self.excoboard).get('/api/v1/fnb/express-pay/can/').json()['allowed'])
        self.assertFalse(self._client(self.clerk).get('/api/v1/fnb/express-pay/can/').json()['allowed'])


class _Resp:
    json = {'instructionId': 'R1G66R-express'}
    status_code = 200


@override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS=SOURCE_ACCOUNTS)
class LoadTests(_ExpressFixture):
    def _load(self, user, **over):
        body = {**self.BODY, **over}
        return self._client(user).post('/api/v1/fnb/express-pay/', body, format='json')

    def test_the_payment_handed_to_the_bank_has_no_creator_and_no_row(self):
        """THE safety invariant. FNB accepts a single-person release only for a
        payment with nobody recorded as its creator; a persisted payment stamped
        with the releaser is refused. Express Pay must never write a Payment."""
        from payments.models import Payment
        before = Payment.objects.count()
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            sub.return_value = mock.Mock(id='11111111-1111-1111-1111-111111111111',
                                         fnb_reference='R1', idempotency_key='K1',
                                         status='submitted')
            r = self._load(self.cfo, confirm=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['loaded'])
        payments = sub.call_args.args[0]
        self.assertEqual(len(payments), 1)
        p = payments[0]
        self.assertIsNone(getattr(p, 'created_by_id', None))
        self.assertIsNone(getattr(p, 'created_by', None))
        self.assertIsNone(p.pk)
        self.assertTrue(sub.call_args.kwargs.get('allow_single_person'))
        self.assertEqual(sub.call_args.kwargs.get('user'), self.cfo)
        self.assertEqual(sub.call_args.kwargs.get('source_account'), self.source_account)
        self.assertEqual(p.amount_bwp, Decimal('150.00'))
        self.assertEqual(p.vendor_bank_account.account_number, '62001234567')
        self.assertTrue(p.payment_number.startswith('EXP-'))
        self.assertLessEqual(len(p.payment_number), 35)
        self.assertEqual(Payment.objects.count(), before, 'Express Pay must write no Payment row')

    def test_the_release_rule_would_refuse_a_payment_that_names_its_creator(self):
        """Proves the invariant above is load-bearing, not decorative: the same
        stand-in WITH a created_by is refused by the choke point."""
        from django.core.exceptions import ValidationError
        from fnb.payments import _assert_release_is_a_second_person
        from fnb.express_pay import _Stand
        p = _Stand()
        p.payment_number = 'EXP-TEST'
        p.created_by_id = self.cfo.pk
        with self.assertRaises(ValidationError):
            _assert_release_is_a_second_person([p], self.cfo, allow_single_person=True)
        del p.created_by_id
        _assert_release_is_a_second_person([p], self.cfo, allow_single_person=True)   # no raise

    def test_a_new_payee_asks_first_then_loads_on_confirm(self):
        """PAY-BANK-03 as a self-confirmation: 409 with the warning, then 200."""
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            sub.return_value = mock.Mock(id='1', fnb_reference='R', idempotency_key='K', status='submitted')
            r = self._load(self.cfo, payee_name='Brand New Payee Ltd')
            self.assertEqual(r.status_code, 409, r.content)
            self.assertTrue(r.json()['needs_confirm'])
            self.assertEqual([w['control'] for w in r.json()['warnings']], ['PAY-BANK-03'])
            self.assertEqual(sub.call_count, 0, 'must not load before the payer confirms')
            r = self._load(self.cfo, payee_name='Brand New Payee Ltd', confirm=True)
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(sub.call_count, 1)

    def test_a_changed_account_for_a_known_payee_asks_first(self):
        """PAY-BANK-01: we paid this payee into another account before."""
        from taskboard.models import PaymentRequest
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/04/0001', entity='ADIC', category='supplier',
            subject='Prior', payee='Known Supplier', total=Decimal('10'),
            status=PaymentRequest.Status.PAID, account_number='62009999999',
            bank_name='FNB', created_by=self.staff)
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            r = self._load(self.cfo, payee_name='Known Supplier', account_number='62001111111')
            self.assertEqual(r.status_code, 409, r.content)
            self.assertIn('PAY-BANK-01', [w['control'] for w in r.json()['warnings']])
            self.assertEqual(sub.call_count, 0)

    def test_a_same_amount_same_account_repeat_within_six_hours_asks_first(self):
        from fnb.models import FNBBatchSubmission
        FNBBatchSubmission.objects.create(
            idempotency_key='Guard Test Supplier 000001 (O)', source_account=self.source_account,
            payment_count=1, total_amount_bwp=Decimal('150.00'), status=FNBBatchSubmission.Status.SUBMITTED,
            submitted_by=self.cfo,
            payload_snapshot={'paymentInformation': [{'creditTransferTransactionInformation': [
                {'creditorAccount': {'accountNumber': '62001234567'}}]}]})
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            sub.return_value = mock.Mock(id='1', fnb_reference='R', idempotency_key='K', status='submitted')
            r = self._load(self.cfo, confirm=False)
            self.assertEqual(r.status_code, 409, r.content)
            self.assertIn('EXP-DUP', [w['control'] for w in r.json()['warnings']])
            self.assertEqual(sub.call_count, 0)
            # The CEO loading the CFO's payment again is the same double load.
            r = self._load(self.ceo, confirm=False)
            self.assertEqual(r.status_code, 409, r.content)
            self.assertIn('EXP-DUP', [w['control'] for w in r.json()['warnings']])
            # A different amount is not a duplicate.
            r = self._load(self.cfo, amount='151.00', confirm=True)
            self.assertEqual(r.status_code, 200, r.content)

    def test_end_to_end_through_the_real_choke_point_with_the_bank_mocked(self):
        """The whole path with only FNBClient mocked: the batch row is written,
        the payload carries the payee's account, and the status view reads it."""
        from fnb.models import FNBBatchSubmission
        with mock.patch('fnb.payments.FNBClient') as client, \
             mock.patch('fnb.payments.refresh_batch_status', return_value={}):
            client.return_value.post.return_value = _Resp()
            r = self._load(self.cfo, confirm=True)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['loaded'])
        self.assertEqual(body['status'], 'loaded')
        batch = FNBBatchSubmission.objects.get(pk=body['batch_id'])
        self.assertEqual(batch.submitted_by, self.cfo)
        self.assertEqual(batch.payment_count, 1)
        self.assertEqual(batch.total_amount_bwp, Decimal('150.00'))
        self.assertEqual(batch.payments.count(), 0, 'no Payment row is ever linked')
        tx = batch.payload_snapshot['paymentInformation'][0]['creditTransferTransactionInformation'][0]
        self.assertEqual(tx['creditorAccount']['accountNumber'], '62001234567')
        self.assertIn('Guard Test Supplier', tx['creditor']['name'])
        self.assertIn('(O)', tx['endToEndId'])
        s = self._client(self.cfo).get(f'/api/v1/fnb/express-pay/{batch.id}/status/')
        self.assertEqual(s.status_code, 200)
        self.assertEqual(s.json()['status'], 'loaded')
        batch.status = FNBBatchSubmission.Status.SETTLED
        batch.save()
        self.assertEqual(self._client(self.cfo).get(f'/api/v1/fnb/express-pay/{batch.id}/status/').json()['status'], 'paid')
        self.assertEqual(self._client(self.clerk).get(f'/api/v1/fnb/express-pay/{batch.id}/status/').status_code, 403)
        # The CEO may use Express Pay but this is not his load — he cannot read it.
        self.assertEqual(self._client(self.ceo).get(f'/api/v1/fnb/express-pay/{batch.id}/status/').status_code, 404)

    def test_the_batch_ceiling_and_bank_errors_are_surfaced_not_swallowed(self):
        from fnb.client import FNBAPIError
        r = self._load(self.cfo, amount='2000000.01', confirm=True)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('per-batch limit', r.json()['detail'])
        with mock.patch('fnb.express_pay.submit_eft_batch', side_effect=FNBAPIError(400, 'AC08 bad branch')):
            r = self._load(self.cfo, confirm=True)
            self.assertEqual(r.status_code, 502)
            self.assertIn('AC08', r.json()['detail'])

    def test_a_bank_signin_failure_is_a_readable_503_not_a_crash(self):
        from fnb.client import FNBAuthError
        with mock.patch('fnb.express_pay.submit_eft_batch', side_effect=FNBAuthError('token expired')):
            r = self._load(self.cfo, confirm=True)
        self.assertEqual(r.status_code, 503, r.content)
        self.assertEqual(r.json()['reason'], 'bank_signin_failed')
        self.assertFalse(r.json()['loaded'])

    def test_a_lost_bank_answer_is_never_reported_as_not_loaded(self):
        """A timeout / 5xx may have reached FNB. submit_eft_batch marks the batch
        UNKNOWN and re-raises; Express Pay must hand that batch back and tell the
        CFO to check FNB first — never 'loaded: false', which invites a second tap."""
        from fnb.client import FNBAPIError
        from fnb.models import FNBBatchSubmission
        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.side_effect = FNBAPIError(599, 'Network error: connection reset')
            r = self._load(self.cfo, confirm=True)
        self.assertEqual(r.status_code, 502, r.content)
        body = r.json()
        self.assertEqual(body['reason'], 'unknown')
        self.assertIsNone(body['loaded'])
        self.assertIn('BEFORE you try again', body['detail'])
        batch = FNBBatchSubmission.objects.get(pk=body['batch_id'])
        self.assertEqual(batch.status, FNBBatchSubmission.Status.UNKNOWN)
        self.assertEqual(body['reference'], batch.fnb_reference or batch.idempotency_key)

    def test_an_unexpected_error_is_json_not_a_django_500_page(self):
        with mock.patch('fnb.express_pay.submit_eft_batch', side_effect=RuntimeError('boom')):
            r = self._load(self.cfo, confirm=True)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()['reason'], 'error')

    def test_a_non_fnb_bank_needs_a_branch_code(self):
        """A blank branch falls back to FNB's universal branch inside the payload
        builder — right for FNB, a misroute for anyone else."""
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            r = self._load(self.cfo, bank_name='Stanbic Bank', branch_code='', confirm=True)
            self.assertEqual(r.status_code, 400, r.content)
            self.assertIn('branch code', r.json()['detail'])
            self.assertEqual(sub.call_count, 0)
            sub.return_value = mock.Mock(id='1', fnb_reference='R', idempotency_key='K', status='submitted')
            self.assertEqual(self._load(self.cfo, bank_name='Stanbic Bank', branch_code='064967', confirm=True).status_code, 200)
            self.assertEqual(self._load(self.cfo, bank_name='First National Bank', branch_code='', confirm=True).status_code, 200)

    def test_confirming_past_a_control_leaves_an_audit_row(self):
        from core.models import AuditLog
        before = AuditLog.objects.filter(table_name='fnb.FNBBatchSubmission').count()
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            sub.return_value = mock.Mock(id='22222222-2222-2222-2222-222222222222', fnb_reference='R', idempotency_key='K', status='submitted')
            r = self._load(self.cfo, payee_name='Brand New Payee Ltd', confirm=True)   # PAY-BANK-03 overridden
            self.assertEqual(r.status_code, 200, r.content)
            row = AuditLog.objects.filter(table_name='fnb.FNBBatchSubmission').order_by('-created_at').first()
            self.assertEqual(AuditLog.objects.filter(table_name='fnb.FNBBatchSubmission').count(), before + 1)
            self.assertEqual(row.user, self.cfo)
            self.assertEqual(row.record_id, '22222222-2222-2222-2222-222222222222')
            self.assertEqual(row.new_values['controls_overridden'], ['PAY-BANK-03'])
            self.assertIn('PAY-BANK-03', row.description)
            # No control fired → no audit row: the trail only records overrides.
            # (A payee we HAVE paid into this account, so neither PAY-BANK fires.)
            from taskboard.models import PaymentRequest
            PaymentRequest.objects.create(
                ref='PAY/ADIC/2026/09/04/0900', entity='ADIC', category='supplier', subject='Prior',
                payee='Guard Test Supplier', total=Decimal('10'), status=PaymentRequest.Status.PAID,
                account_number='62001234567', bank_name='FNB', created_by=self.staff)
            self.assertEqual(self._load(self.cfo, confirm=True).status_code, 200)
            self.assertEqual(AuditLog.objects.filter(table_name='fnb.FNBBatchSubmission').count(), before + 1)

    def test_bad_input_is_refused_before_anything_else(self):
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            self.assertEqual(self._load(self.cfo, amount='0', confirm=True).status_code, 400)
            self.assertEqual(self._load(self.cfo, amount='abc', confirm=True).status_code, 400)
            self.assertEqual(self._load(self.cfo, account_number='none', confirm=True).status_code, 400)
            self.assertEqual(self._load(self.cfo, company='NOPE', confirm=True).status_code, 400)
            with override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS={}):
                r = self._load(self.cfo, confirm=True)
                self.assertEqual(r.status_code, 400)
                self.assertIn('No paying account', r.json()['detail'])
            self.assertEqual(sub.call_count, 0)


class HelperTests(_ExpressFixture):
    def _pr(self, payee, acct, **extra):
        from taskboard.models import PaymentRequest
        n = PaymentRequest.objects.count() + 1
        return PaymentRequest.objects.create(
            ref=f'PAY/ADIC/2026/09/04/{n:04d}', entity='ADIC',
            category=extra.pop('category', 'supplier'), subject=extra.pop('subject', 'x'),
            payee=payee, total=extra.pop('total', Decimal('10')),
            status=extra.pop('status', 'paid'), account_number=acct, bank_name='FNB',
            branch_code='287867', created_by=self.staff, **extra)

    def test_recent_payees_carry_the_bank_we_last_paid_them_into(self):
        self._pr('Carfil Service', '62005550001')
        self._pr('Korean Auto', '62005550002')
        self._pr('Carfil Service', '62005550001')                 # repeat → one row
        self._pr('Draft Only Co', '62005550003', status='draft')  # drafts are excluded
        r = self._client(self.cfo).get('/api/v1/fnb/recent-payees/')
        self.assertEqual(r.status_code, 200)
        rows = {p['payee']: p for p in r.json()['payees']}
        self.assertEqual(set(rows), {'Carfil Service', 'Korean Auto'})
        self.assertEqual(rows['Carfil Service']['account_number'], '62005550001')
        self.assertEqual(rows['Carfil Service']['bank_name'], 'FNB')
        self.assertEqual(rows['Carfil Service']['branch_code'], '287867')
        q = self._client(self.cfo).get('/api/v1/fnb/recent-payees/?q=korean').json()['payees']
        self.assertEqual([p['payee'] for p in q], ['Korean Auto'])

    def test_claim_payee_resolves_the_prior_claim_payment(self):
        self._pr('Motlatsi Repairs', '62007770001', category='claim',
                 subject='Claim CLM-2026-0042 repair', total=Decimal('4500'))
        r = self._client(self.cfo).get('/api/v1/fnb/claim-payee/?claim=CLM-2026-0042')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['found'])
        self.assertEqual(body['payee'], 'Motlatsi Repairs')
        self.assertEqual(body['account_number'], '62007770001')
        self.assertEqual(body['last_paid'], '4500.00')
        self.assertNotIn('amount', body, 'the phone must not be handed an amount to prefill')
        self.assertFalse(self._client(self.cfo).get('/api/v1/fnb/claim-payee/?claim=ZZZ-NOPE').json()['found'])
        self.assertFalse(self._client(self.cfo).get('/api/v1/fnb/claim-payee/?claim=CL').json()['found'])


class SavedPayeeTests(_ExpressFixture):
    URL = '/api/v1/fnb/express-pay/payees/'

    def test_only_cfo_and_ceo_can_see_or_save_payees(self):
        for who in (self.clerk, self.excoboard, self.coo, self.staff):
            self.assertEqual(self._client(who).get(self.URL).status_code, 403, who.username)
            self.assertEqual(self._client(who).post(self.URL, {'name': 'X', 'account_number': '62000000001'}, format='json').status_code, 403)

    def test_save_list_edit_remove(self):
        c = self._client(self.cfo)
        r = c.post(self.URL, {'name': 'Molapo Motors', 'bank_name': 'FNB', 'account_number': '6200 111 2222',
                              'default_amount': '1,250.00', 'reference': 'Service', 'category': 'operations'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()['id']
        self.assertEqual(r.json()['account_number'], '62001112222', 'digits only')
        self.assertEqual(r.json()['default_amount'], '1250.00')
        # The CEO sees the same shared list.
        rows = self._client(self.ceo).get(self.URL).json()['payees']
        self.assertEqual([x['name'] for x in rows], ['Molapo Motors'])
        # Re-saving the same account updates it — never a duplicate.
        r = c.post(self.URL, {'name': 'Molapo Motors (Pty) Ltd', 'account_number': '62001112222'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(c.get(self.URL).json()['payees']), 1)
        # Edit.
        r = c.patch(f'{self.URL}{pid}/', {'default_amount': '900'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['default_amount'], '900.00')
        # Remove: gone from the list, row kept (soft delete).
        self.assertEqual(c.delete(f'{self.URL}{pid}/').status_code, 204)
        self.assertEqual(c.get(self.URL).json()['payees'], [])
        from fnb.models import ExpressPayee
        self.assertFalse(ExpressPayee.objects.get(pk=pid).is_active)

    def test_bad_payee_details_are_refused(self):
        c = self._client(self.cfo)
        # Wrong JSON shapes are a 400, never a 500 (independent review).
        self.assertEqual(c.post(self.URL, {'name': ['x'], 'account_number': '62001112222'}, format='json').status_code, 400)
        self.assertEqual(c.post(self.URL, {'name': 'A', 'account_number': {'n': 1}}, format='json').status_code, 400)
        self.assertEqual(c.post(self.URL, ['not', 'an', 'object'], format='json').status_code, 400)
        # Two saved payees can never share an account: a PATCH onto another's account is refused.
        a = c.post(self.URL, {'name': 'A', 'account_number': '62001112222'}, format='json').json()
        c.post(self.URL, {'name': 'B', 'account_number': '62003334444'}, format='json')
        self.assertEqual(c.patch(f"{self.URL}{a['id']}/", {'account_number': '62003334444'}, format='json').status_code, 400)
        from django.db import IntegrityError, transaction
        from fnb.models import ExpressPayee
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExpressPayee.objects.create(name='dup', account_number='62001112222')
        self.assertEqual(c.post(self.URL, {'name': '', 'account_number': '62001112222'}, format='json').status_code, 400)
        self.assertEqual(c.post(self.URL, {'name': 'A', 'account_number': '123'}, format='json').status_code, 400)
        self.assertEqual(c.post(self.URL, {'name': 'A', 'account_number': '62001112222', 'bank_name': 'Stanbic'}, format='json').status_code, 400)
        self.assertEqual(c.post(self.URL, {'name': 'A', 'account_number': '62001112222', 'default_amount': 'lots'}, format='json').status_code, 400)

    @override_settings(PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS=SOURCE_ACCOUNTS)
    def test_a_load_stamps_the_saved_payee_as_paid(self):
        from fnb.models import ExpressPayee
        x = ExpressPayee.objects.create(name='Guard Test Supplier', account_number='62001234567', created_by=self.cfo)
        self.assertIsNone(x.last_paid_at)
        with mock.patch('fnb.express_pay.submit_eft_batch') as sub:
            sub.return_value = mock.Mock(id='1', fnb_reference='R', idempotency_key='K', status='submitted')
            r = self._client(self.cfo).post('/api/v1/fnb/express-pay/', {**self.BODY, 'confirm': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        x.refresh_from_db()
        self.assertIsNotNone(x.last_paid_at)


class PaymentHistoryTests(_ExpressFixture):
    URL = '/api/v1/fnb/express-pay/history/'

    def _pr(self, payee, acct, total, ref_tail, **extra):
        from taskboard.models import PaymentRequest
        return PaymentRequest.objects.create(
            ref=f'PAY/ADIC/2026/08/{ref_tail}', entity='ADIC', category=extra.pop('category', 'supplier'),
            subject=extra.pop('subject', f'{payee} invoice'), payee=payee, total=Decimal(total),
            status=extra.pop('status', 'paid'), account_number=acct, bank_name='FNB', branch_code='',
            created_by=self.staff, **extra)

    def test_gate(self):
        for who in (self.clerk, self.excoboard, self.coo, self.staff):
            self.assertEqual(self._client(who).get(self.URL).status_code, 403, who.username)

    def test_history_searches_by_name_amount_and_date_and_carries_the_bank(self):
        from django.utils import timezone as tz
        from datetime import timedelta
        a = self._pr('Carfil Service', '62005550001', '4,500.00'.replace(',', ''), '01/0001')
        self._pr('Korean Auto', '62005550002', '1200.00', '02/0002')
        self._pr('Draft Only', '62005550003', '99.00', '03/0003', status='draft')
        old = self._pr('Carfil Service', '62005550001', '800.00', '04/0004')
        # push one back in time: created_at is auto_now_add, so update the row directly
        type(old).objects.filter(pk=old.pk).update(created_at=tz.now() - timedelta(days=40))
        c = self._client(self.cfo)

        rows = c.get(self.URL).json()['payments']
        self.assertEqual([r['payee'] for r in rows], ['Korean Auto', 'Carfil Service', 'Carfil Service'], 'newest first, no drafts')
        top = rows[1]
        self.assertEqual(top['id'], str(a.id))
        self.assertEqual(top['amount'], '4500.00')
        self.assertEqual(top['account_number'], '62005550001')
        self.assertEqual(top['bank_name'], 'FNB')
        self.assertEqual(top['category'], 'operations')

        self.assertEqual([r['payee'] for r in c.get(self.URL + '?q=korean').json()['payments']], ['Korean Auto'])
        self.assertEqual([r['amount'] for r in c.get(self.URL + '?amount=4,500').json()['payments']], ['4500.00'])
        self.assertEqual([r['amount'] for r in c.get(self.URL + '?amount_min=1000&amount_max=2000').json()['payments']], ['1200.00'])
        today = tz.localdate().isoformat()
        self.assertEqual(len(c.get(self.URL + f'?from={today}&to={today}').json()['payments']), 2, 'the 40-day-old one is out')
        self.assertEqual(c.get(self.URL + '?from=yesterday').status_code, 400)

    def test_history_includes_earlier_express_pay_loads(self):
        from fnb.models import FNBBatchSubmission
        FNBBatchSubmission.objects.create(
            idempotency_key='Molapo Motors 000001 (O)', source_account=self.source_account, payment_count=1,
            total_amount_bwp=Decimal('1250.00'), status=FNBBatchSubmission.Status.SETTLED, submitted_by=self.cfo,
            payload_snapshot={'paymentInformation': [{'creditTransferTransactionInformation': [{
                'endToEndId': 'Molapo Motors EXP-1 (O)',
                'creditor': {'name': 'Molapo Motors'},
                'creditorAccount': {'accountNumber': '62009990001'},
                'creditorAgent': {'branchId': '287867'},
                'remittanceInformationUnstructured': 'Service Raptor'}]}]})
        rows = self._client(self.cfo).get(self.URL + '?q=molapo').json()['payments']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], 'express_pay')
        self.assertEqual(rows[0]['account_number'], '62009990001')
        self.assertEqual(rows[0]['amount'], '1250.00')
        self.assertEqual(rows[0]['reference'], 'Service Raptor')
        self.assertEqual(rows[0]['bank_name'], 'FNB')
