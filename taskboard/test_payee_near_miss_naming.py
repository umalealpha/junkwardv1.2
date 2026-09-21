"""taskboard/test_payee_near_miss_naming.py — whose account is Omni citing?

Unopa Male, Omni bug cf6c042d (2026-09-17): "fix false 'Wrong Account Number'
errors mismatching suppliers".

The change-challenge (PAY-BANK-01) matches the typed payee against history with
`_same_payee`, which is deliberately GENEROUS — a near miss counts as the same
supplier, because being too strict is how invoice fraud gets paid (Fable audit
2026-09-02, H1). That stays exactly as it is.

What was wrong is what the raiser was then TOLD. The message was built from the
name they typed, so a near miss against a different real supplier read as
"you paid <this supplier> into an account ending 1234 before" — a statement
about a supplier Omni had never paid, phrased as a fraud challenge. Finance
cannot tell that apart from a real bank-change attempt.

So: the warning still fires on a near miss, and now says which record it
matched and that the spelling is not the same.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from taskboard.models import PaymentRequest
from taskboard.payee_bank_history import (
    bank_change_warning, bank_details_changed, last_known_bank,
)


class NearMissNamingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('near_miss_tester', password='x')
        # A real, paid supplier. "Gaborone Motors" is the standing example in
        # last_known_bank's own docstring for a name that a different supplier
        # can shadow.
        PaymentRequest.objects.create(
            ref='PR-NEAR-1', entity='Alpha Direct Insurance',
            category=PaymentRequest.Category.SUPPLIER, currency='BWP',
            subject='Invoice', payee='Gaborone Motors', line_items=[],
            total=Decimal('100.00'), account_number='62001234567',
            bank_name='FNB Botswana', account_name='Gaborone Motors',
            status=PaymentRequest.Status.PAID, created_by=cls.user,
        )

    # ── the lookup now reports WHICH record it matched ────────────────────
    def test_exact_match_is_flagged_exact(self):
        known = last_known_bank('Gaborone Motors')
        self.assertIsNotNone(known)
        self.assertTrue(known['matched_exact'])
        self.assertEqual(known['matched_name'], 'Gaborone Motors')

    def test_near_miss_is_flagged_not_exact_and_names_the_record(self):
        known = last_known_bank('Gaborone Motels')
        self.assertIsNotNone(known, 'the generous match must still hit')
        self.assertFalse(known['matched_exact'])
        self.assertEqual(known['matched_name'], 'Gaborone Motors')

    # ── the challenge still fires — that is the control, untouched ────────
    def test_near_miss_with_a_different_account_still_warns(self):
        warn = bank_change_warning('Gaborone Motels', '62009999999')
        self.assertIsNotNone(warn, 'the fraud control must not be weakened')
        self.assertEqual(warn['control'], 'PAY-BANK-01')

    # ── but it no longer claims we paid the supplier who was typed ────────
    def test_near_miss_message_names_the_matched_supplier(self):
        warn = bank_change_warning('Gaborone Motels', '62009999999')
        self.assertFalse(warn['matched_exact'])
        self.assertEqual(warn['matched_name'], 'Gaborone Motors')
        self.assertIn('Gaborone Motors', warn['detail'])
        self.assertIn('close match', warn['detail'])
        self.assertNotIn('You paid Gaborone Motels', warn['detail'])

    def test_exact_match_message_is_unchanged_in_substance(self):
        warn = bank_change_warning('Gaborone Motors', '62009999999')
        self.assertTrue(warn['matched_exact'])
        self.assertIn('You paid Gaborone Motors into an account ending',
                      warn['detail'])
        self.assertNotIn('close match', warn['detail'])

    def test_same_account_still_returns_nothing(self):
        self.assertIsNone(bank_change_warning('Gaborone Motors', '62001234567'))
        self.assertIsNone(bank_change_warning('Gaborone Motels', '62001234567'))

    # ── PAY-BANK-04 (re-verification) carries the same naming ─────────────
    def test_details_changed_names_the_matched_supplier_on_a_near_miss(self):
        out = bank_details_changed('Gaborone Motels',
                                   account_number='62009999999')
        self.assertIsNotNone(out)
        self.assertFalse(out['matched_exact'])
        self.assertIn('Gaborone Motors', out['detail'])
        self.assertIn('close match', out['detail'])

    def test_details_changed_on_an_exact_match_reads_as_before(self):
        out = bank_details_changed('Gaborone Motors',
                                   account_number='62009999999')
        self.assertIsNotNone(out)
        self.assertTrue(out['matched_exact'])
        self.assertIn('for Gaborone Motors (from', out['detail'])
