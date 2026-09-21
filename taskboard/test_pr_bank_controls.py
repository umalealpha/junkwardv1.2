"""taskboard/test_pr_bank_controls.py — the bank details, and who may change them.

CFO 2026-08-20, three asks in one message:
  "make the bank account details mandatory going forward"
  "the AI will automatically fill in the bank account details of the previous
   payment when the name of the supplier is loaded"
  "if a person is changing the bank account details it rejects, saying 'Why are
   you doing this because you paid this person with another bank account?'"

The middle one is built as a LOOKUP, not a model. Which account we paid someone
into is a fact we hold, and this is the field that decides who receives money —
the worst possible place for a confident invention.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from taskboard.models import PaymentRequest
from taskboard.payee_bank_history import (
    bank_change_warning, last_known_bank, normalise_payee,
)


class NormalisePayeeTests(TestCase):
    def test_the_same_supplier_typed_differently_is_one_key(self):
        for a, b in (
            ('ABC Traders (Pty) Ltd', 'abc traders pty ltd'),
            ('A.B.C. Traders', 'ABC Traders'),
            ('Kalahari Motors Ltd.', 'kalahari motors limited'),
        ):
            self.assertEqual(normalise_payee(a), normalise_payee(b),
                             msg=f'{a!r} vs {b!r}')

    def test_different_suppliers_stay_different(self):
        self.assertNotEqual(normalise_payee('ABC Traders'),
                            normalise_payee('ABD Traders'))

    def test_blank_is_no_key(self):
        self.assertEqual(normalise_payee(''), '')
        self.assertEqual(normalise_payee('   '), '')


class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('pr_bank_tester', password='x')

    def _request(self, ref, payee, acct, **over):
        kwargs = dict(
            ref=ref, entity='Alpha Direct Insurance',
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='Invoice', payee=payee, line_items=[],
            total=Decimal('100.00'), account_number=acct,
            bank_name='FNB Botswana', account_name=payee,
            status=PaymentRequest.Status.PAID, created_by=self.user,
        )
        kwargs.update(over)
        return PaymentRequest.objects.create(**kwargs)


class LastKnownBankTests(_Fixture):
    def test_it_finds_the_previous_request_for_the_same_payee(self):
        self._request('PR-H-1', 'ABC Traders', '1234567')
        found = last_known_bank('abc traders pty ltd')
        self.assertIsNotNone(found)
        self.assertEqual(found['account_number'], '1234567')
        self.assertEqual(found['source'], 'previous_request')

    def test_a_payee_never_paid_has_no_history(self):
        self.assertIsNone(last_known_bank('Nobody We Have Ever Paid'))

    def test_the_most_recent_request_wins(self):
        """Pinned with distinct timestamps. The first version asserted the
        answer was one OR the other, which passes whichever row wins and
        therefore proves nothing."""
        from datetime import timedelta
        from django.utils import timezone
        older = self._request('PR-H-2', 'ABC Traders', '1111111')
        newer = self._request('PR-H-3', 'ABC Traders', '2222222')
        now = timezone.now()
        PaymentRequest.objects.filter(pk=older.pk).update(
            created_at=now - timedelta(days=30))
        PaymentRequest.objects.filter(pk=newer.pk).update(created_at=now)
        found = last_known_bank('ABC Traders')
        self.assertEqual(found['account_number'], '2222222')

    def test_a_request_with_no_account_is_not_history(self):
        self._request('PR-H-4', 'Blank Bank Ltd', '')
        self.assertIsNone(last_known_bank('Blank Bank Ltd'))


class BankChangeWarningTests(_Fixture):
    def test_the_same_account_is_not_a_change(self):
        self._request('PR-C-1', 'ABC Traders', '1234567')
        self.assertIsNone(bank_change_warning('ABC Traders', '1234567'))

    def test_spacing_and_dashes_are_not_a_change(self):
        self._request('PR-C-2', 'ABC Traders', '1234567')
        self.assertIsNone(bank_change_warning('ABC Traders', '123-45 67'))

    def test_a_different_account_asks_the_cfos_question(self):
        self._request('PR-C-3', 'ABC Traders', '1234567')
        warn = bank_change_warning('ABC Traders', '7654321')
        self.assertIsNotNone(warn)
        self.assertEqual(warn['control'], 'PAY-BANK-01')
        self.assertIn('Why are you doing this', warn['detail'])
        # names the two accounts by their last four, never in full
        self.assertEqual(warn['known_account_tail'], '4567')
        self.assertEqual(warn['new_account_tail'], '4321')
        self.assertNotIn('1234567', warn['detail'])
        self.assertNotIn('7654321', warn['detail'])

    def test_it_tells_them_to_ring_a_number_they_already_had(self):
        """The advice matters as much as the block: fraudsters put their own
        number on the invoice."""
        self._request('PR-C-4', 'ABC Traders', '1234567')
        warn = bank_change_warning('ABC Traders', '9999999')
        self.assertIn('number you already had', warn['detail'])

    def test_a_first_ever_payment_is_not_challenged(self):
        self.assertIsNone(bank_change_warning('Brand New Supplier', '5555555'))

    def test_a_blank_account_is_not_challenged_here(self):
        """Blank is the mandatory-fields control's job, not this one."""
        self._request('PR-C-5', 'ABC Traders', '1234567')
        self.assertIsNone(bank_change_warning('ABC Traders', ''))
