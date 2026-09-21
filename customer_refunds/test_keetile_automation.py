"""Keetile Mokhendo's 2026-09-11 request: stop re-typing a refund three times.

Her ask, in her words: an approved Graphite refund should arrive in Omni under
Payment Request -> **Refund** (not Operational), and reach FNB already filled in
— debit account, references, notification email and all — so nobody keys it on
the bank screen. The CFO confirmed the same day: keep it seamless (no extra
Finance Manager step), and pay refunds from the Alpha Direct Current Account
she named.

Each test here fails if its fix is reverted — that is the point of them.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from taskboard.models import PaymentRequest

from .models import CustomerRefund
from .services import (REFUND_DEBIT_ACCOUNT_DEFAULT, REFUND_POP_EMAIL,
                       _resolve_source_bank_account, ensure_payment_request,
                       fnb_send_enabled)


class ArmingSwitchIsSplitTests(TestCase):
    """Refunds must arm WITHOUT opening the type-in-any-account screen.

    Both used to read FNB_QUICK_TRANSFER_ENABLED, so arming refunds armed the
    quick-transfer screen too (Pramod Bisen, 2026-09-02).
    """

    @override_settings(FNB_QUICK_TRANSFER_ENABLED=True, REFUND_FNB_SEND_ENABLED=False)
    def test_quick_transfer_on_does_not_arm_refunds(self):
        # Reverting the split makes this read True — the whole defect.
        self.assertFalse(fnb_send_enabled())

    @override_settings(FNB_QUICK_TRANSFER_ENABLED=False, REFUND_FNB_SEND_ENABLED=True)
    def test_refunds_arm_on_their_own(self):
        self.assertTrue(fnb_send_enabled())

    def test_unset_means_off(self):
        # A missing setting must never open the gate.
        with override_settings():
            from django.conf import settings
            if hasattr(settings, 'REFUND_FNB_SEND_ENABLED'):
                del settings.REFUND_FNB_SEND_ENABLED
            self.assertFalse(fnb_send_enabled())


class DebitAccountTests(TestCase):
    def test_default_is_the_current_account_keetile_named(self):
        self.assertEqual(REFUND_DEBIT_ACCOUNT_DEFAULT, '62403392335')

    @override_settings(FNB_DEBTOR_ACCOUNT_NUMBER='63001966639')
    def test_refunds_do_not_follow_the_general_debtor_account(self):
        """Supplier/payroll runs keep their own account; refunds have theirs.

        Reverting to the shared setting makes this resolve 63001966639.
        """
        from banking.models import BankAccount
        seen = {}

        class _QS:
            def filter(self, **kw):
                seen.update(kw)
                return self

            def first(self):
                return None

        with mock.patch.object(BankAccount, 'objects', _QS()):
            _resolve_source_bank_account()
        self.assertEqual(seen.get('account_number'), '62403392335')


class FnbFieldsAreFilledInTests(TestCase):
    """The bank screen Keetile fills by hand today must arrive pre-filled."""

    def setUp(self):
        self.refund = CustomerRefund.objects.create(
            graphite_ref='RFND-000100', policy_number='MIS2026000777',
            customer_name='Boitumelo Seleka', refund_amount=Decimal('412.50'),
            currency='BWP', bank_name='FNB', branch_code='250655',
            reason='Double debit July and August')
        self.refund.set_account_number('62000000111')
        self.refund.save()
        self.user = User.objects.create_user('maker', 'maker@alphadirect.co.bw', 'x')

        # The refund Payment needs a company and a bank GL account to exist.
        # Built here rather than skipped: this is the assertion that proves
        # Keetile never has to key the FNB screen again, so it must RUN.
        from core.models import Company, Currency
        from ledger.models import Account
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        self.company, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance Company',
                                   'base_currency_id': 'BWP'})
        Account.objects.get_or_create(
            code='1100', defaults={'name': 'FNB Current Account',
                                   'owner_company': self.company,
                                   'is_bank_account': True})

    def _payment(self):
        from .services import create_refund_payment
        return create_refund_payment(self.refund, self.user)

    def test_payment_carries_the_bank_strings_and_pop_address(self):
        p = self._payment()
        # Recipient name = the client being refunded.
        self.assertEqual(p.bank_beneficiary_name, 'Boitumelo Seleka')
        # Finance's 2026-08-20 wording, carrying the policy number.
        self.assertIn('MIS2026000777', p.bank_narration)
        self.assertIn('REFUND', p.bank_narration.upper())
        self.assertIn('MIS2026000777', p.bank_our_reference)
        # FNB emails the proof of payment to the shared refunds mailbox.
        self.assertEqual(p.remittance_email, REFUND_POP_EMAIL)
        self.assertEqual(REFUND_POP_EMAIL, 'refund@alphadirect.co.bw')


class LandsOnTheRefundsTabTests(TestCase):
    """Her point 1: under Payment Request -> Refund, never Operational."""

    def setUp(self):
        self.refund = CustomerRefund.objects.create(
            graphite_ref='RFND-000101', policy_number='MIS2026000888',
            customer_name='Kabo Tau', refund_amount=Decimal('250.00'),
            currency='BWP')

    def test_no_graphite_ref_raises_nothing(self):
        """A blank reference cannot be de-duplicated, so it must not raise one.

        This is the shape that produced a fresh request every midnight.
        """
        r = CustomerRefund.objects.create(
            graphite_ref='', policy_number='MIS2026000999',
            refund_amount=Decimal('10.00'))
        self.assertEqual(ensure_payment_request(r), '')
        self.assertEqual(PaymentRequest.objects.count(), 0)

    def test_the_direct_handoff_lands_on_the_refunds_tab(self):
        """END TO END: a Graphite hand-off becomes a PREMIUM_REFUND request.

        Category is what the tab keys off (frontend payment-requests page: the
        refund tab matches category === 'premium_refund'), so a request raised
        under anything else appears under Operational — exactly what Keetile
        asked us not to do. Before this change the hand-off raised NO payment
        request at all, so removing the ensure_payment_request call turns this
        red on the very first assertion.
        """
        from taskboard.premium_refund_requests import ENTERED_BY_EMAIL
        User.objects.create_user('keetile', ENTERED_BY_EMAIL, 'x',
                                 first_name='Keetile', last_name='Mokhendo')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')

        ref = ensure_payment_request(self.refund)
        self.assertTrue(ref, 'no payment request was raised for the refund')

        pr = PaymentRequest.objects.get(graphite_ref='RFND-000101')
        self.assertEqual(pr.category, PaymentRequest.Category.PREMIUM_REFUND)
        self.assertNotEqual(pr.category, PaymentRequest.Category.OTHER)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)
        self.assertEqual(pr.total, Decimal('250.00'))
        self.assertEqual(pr.payee, 'Kabo Tau')
        self.assertIsNotNone(pr.task, 'a request with no task can never be paid')

    def test_calling_it_twice_raises_only_one_request(self):
        """Both pipes may see the same refund — it must never become two."""
        from taskboard.premium_refund_requests import ENTERED_BY_EMAIL
        User.objects.create_user('keetile', ENTERED_BY_EMAIL, 'x')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')

        first = ensure_payment_request(self.refund)
        second = ensure_payment_request(self.refund)
        self.assertEqual(first, second)
        self.assertEqual(
            PaymentRequest.objects.filter(graphite_ref='RFND-000101').count(), 1)

    def test_a_builder_failure_never_undoes_the_refund(self):
        """The refund and its bank leg are already saved — best effort only."""
        with mock.patch('taskboard.premium_refund_requests.entered_by_user',
                        return_value=None):
            self.assertEqual(ensure_payment_request(self.refund), '')
        self.refund.refresh_from_db()   # still there, nothing rolled back
        self.assertEqual(self.refund.graphite_ref, 'RFND-000101')


class HandoffRaisesTheRequestTests(TestCase):
    """stage_handoff_refund must actually CALL it.

    A direct test of ensure_payment_request passes even if nothing invokes it —
    which is exactly how a refund could reach FNB while showing on no tab at
    all. Deleting the call in stage_handoff_refund turns this red.
    """

    def setUp(self):
        from taskboard.premium_refund_requests import ENTERED_BY_EMAIL
        User.objects.create_user('keetile', ENTERED_BY_EMAIL, 'x')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')
        self.refund = CustomerRefund.objects.create(
            graphite_ref='RFND-000102', policy_number='MIS2026001010',
            customer_name='Neo Phiri', refund_amount=Decimal('180.00'),
            currency='BWP')
        self.refund.set_account_number('62000000222')
        self.refund.save()

        from core.models import Company, Currency
        from ledger.models import Account
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        company, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance Company',
                                   'base_currency_id': 'BWP'})
        Account.objects.get_or_create(
            code='1100', defaults={'name': 'FNB Current Account',
                                   'owner_company': company,
                                   'is_bank_account': True})

    def test_a_graphite_handoff_appears_on_the_refunds_tab(self):
        """The real staging path — only the bank call and the email are stubbed."""
        from . import services

        with mock.patch.object(services, 'load_refund_to_fnb',
                               return_value={'mode': 'preview'}),              mock.patch.object(services, 'notify_cfo_to_authorise'):
            out = services.stage_handoff_refund(self.refund)

        self.assertTrue(out['staged'], out.get('reason'))
        pr = PaymentRequest.objects.get(graphite_ref='RFND-000102')
        self.assertEqual(pr.category, PaymentRequest.Category.PREMIUM_REFUND)
        self.assertEqual(out['payment_request'], pr.ref)


class BankFailureStillLeavesItVisibleTests(TestCase):
    """Fable, 11-Sep: the bank call can throw something nobody catches.

    load_refund_to_fnb does not catch the fnb.client exceptions. They escape
    stage_handoff_refund past its two except clauses, and the ingest view's
    blanket except eats them — so before this fix a refund could end APPROVED,
    with a payment raised, on no tab, with nobody told. Raising the request
    FIRST means the worst case is a refund sitting visible and waiting.

    Move ensure_payment_request back below the load and this goes red.
    """

    def setUp(self):
        from taskboard.premium_refund_requests import ENTERED_BY_EMAIL
        User.objects.create_user('keetile', ENTERED_BY_EMAIL, 'x')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')
        self.refund = CustomerRefund.objects.create(
            graphite_ref='RFND-000103', policy_number='MIS2026001111',
            customer_name='Thato Modise', refund_amount=Decimal('95.00'),
            currency='BWP')
        self.refund.set_account_number('62000000333')
        self.refund.save()

        from core.models import Company, Currency
        from ledger.models import Account
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        company, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance Company',
                                   'base_currency_id': 'BWP'})
        Account.objects.get_or_create(
            code='1100', defaults={'name': 'FNB Current Account',
                                   'owner_company': company,
                                   'is_bank_account': True})

    def test_an_uncaught_bank_error_still_leaves_it_on_the_refunds_tab(self):
        from . import services

        class _FnbExploded(Exception):
            """Stands in for FNBAuthError / FNBAPIError — not a caught type."""

        with mock.patch.object(services, 'load_refund_to_fnb',
                               side_effect=_FnbExploded('bank said no')),              mock.patch.object(services, 'notify_cfo_to_authorise'):
            with self.assertRaises(_FnbExploded):
                services.stage_handoff_refund(self.refund)

        # The refund is VISIBLE even though the bank leg blew up.
        pr = PaymentRequest.objects.get(graphite_ref='RFND-000103')
        self.assertEqual(pr.category, PaymentRequest.Category.PREMIUM_REFUND)


class LongReferenceSurvivesTests(TestCase):
    """The idempotency key must be the same width on both sides of the join.

    PaymentRequest.graphite_ref was 40 and CustomerRefund.graphite_ref 64, so a
    long reference raised a DataError that the best-effort catch swallowed: the
    refund could reach the bank while its request silently never existed.
    """

    def test_a_64_character_reference_still_raises_the_request(self):
        from taskboard.premium_refund_requests import ENTERED_BY_EMAIL
        User.objects.create_user('keetile', ENTERED_BY_EMAIL, 'x')
        User.objects.create_superuser('pganesharajah', 'cfo@alphadirect.co.bw', 'x')
        long_ref = 'RFND-' + ('9' * 59)          # exactly 64 characters
        self.assertEqual(len(long_ref), 64)
        r = CustomerRefund.objects.create(
            graphite_ref=long_ref, policy_number='MIS2026001212',
            customer_name='Lesego Rapoo', refund_amount=Decimal('60.00'),
            currency='BWP')

        ref = ensure_payment_request(r)
        self.assertTrue(ref, 'a 64-character reference was silently dropped')
        self.assertEqual(
            PaymentRequest.objects.get(graphite_ref=long_ref).category,
            PaymentRequest.Category.PREMIUM_REFUND)


class NoHandKeyTaskForAnAlreadyLoadedRefundTests(TestCase):
    """Fable, 11-Sep: the midnight job must not send Keetile to type a payment
    the direct route already put in FNB. That is the double payment."""

    def test_a_refund_with_an_fnb_batch_raises_no_hand_keying_task(self):
        from taskboard.management.commands.import_graphite_refunds import Command
        from banking.models import BankAccount
        from fnb.models import FNBBatchSubmission

        from core.models import Company, Currency
        from ledger.models import Account
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
        company, _ = Company.objects.get_or_create(
            code='ADIC', defaults={'name': 'Alpha Direct Insurance Company',
                                   'base_currency_id': 'BWP'})
        gl, _ = Account.objects.get_or_create(
            code='1100', defaults={'name': 'FNB Current Account',
                                   'owner_company': company,
                                   'is_bank_account': True})
        source = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB',
            account_name='Alpha Direct Current Account',
            account_number='62403392335', currency_code_id='BWP')
        batch = FNBBatchSubmission.objects.create(
            idempotency_key='TEST-BATCH-1', source_account=source)
        CustomerRefund.objects.create(
            graphite_ref='RFND-000104', policy_number='MIS2026001313',
            refund_amount=Decimal('70.00'), currency='BWP', fnb_batch=batch)

        cmd = Command()
        cmd.stdout = mock.Mock()
        self.assertTrue(cmd._already_loaded_to_fnb('RFND-000104'))
        # A refund the direct route has NOT loaded still gets its task.
        self.assertFalse(cmd._already_loaded_to_fnb('RFND-000105'))
        self.assertFalse(cmd._already_loaded_to_fnb(''))
