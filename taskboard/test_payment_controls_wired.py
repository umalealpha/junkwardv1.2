"""The four preventive controls Manus asked for, 2026-08-10.

*"Do not chase the BWP 399,338.10 — wire the controls."* The eleven historical
cases are Finance's to reconcile. What matters here is that the same thing cannot
happen again.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from taskboard.models import PaymentRequest
from taskboard.payment_duplicates import compare_lines, find_duplicates

User = get_user_model()


class SamePayeeSameAmountNoReferenceTests(TestCase):
    """Six of the eleven groups carried no claim number, so the token rule skipped
    them and they were never compared against the register at all."""

    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user('kctrl', 'kc@example.invalid', 'x')
        # 'FAC Premium - May' with claim 'N/A' carries NO token at all: no claim
        # pattern, no long number, and 'N/A' normalises to two characters. An
        # earlier version of this test used 'CFAO MOBILITY 10030837', whose
        # 8-digit number IS tokenised — so it passed with the fix reverted and
        # certified nothing.
        PaymentRequest.objects.create(
            ref='PR-CTRL-PAID', entity='ADIC', status=PaymentRequest.Status.PAID,
            payee='FAC', currency='BWP', total='86470.94', created_by=cls.u,
            line_items=[{'description': 'FAC Premium - May',
                         'claim_number': 'N/A', 'amount': '86470.94'}])

    def test_the_same_payee_and_amount_is_caught_with_no_reference_at_all(self):
        from taskboard.payment_duplicates import line_tokens
        line = {'description': 'FAC Premium - May', 'claim_number': 'N/A',
                'amount': '86470.94'}
        self.assertEqual(line_tokens(line), set(),
                         'the point of this test is that the line has NO tokens')
        hard = find_duplicates([line], currency='BWP')['hard']
        self.assertTrue(hard, 'a payee+amount repeat with no reference must block')
        self.assertEqual(hard[0]['clash_ref'], 'PR-CTRL-PAID')

    def test_a_different_amount_to_the_same_payee_is_fine(self):
        self.assertEqual(find_duplicates(
            [{'description': 'FAC Premium - May', 'claim_number': 'N/A',
              'amount': '999.00'}], currency='BWP')['hard'], [])

    def test_next_months_recurring_payment_is_not_blocked(self):
        """'Rent July 26' and 'Rent August 26' are different text, so a genuine
        monthly payment of the same amount still goes through."""
        PaymentRequest.objects.create(
            ref='PR-CTRL-RENT', entity='UNI', status=PaymentRequest.Status.PAID,
            payee='Unicoin', currency='BWP', total='24504.94', created_by=self.u,
            line_items=[{'description': 'Unicoin - Rent July 26', 'amount': '24504.94'}])
        self.assertEqual(find_duplicates(
            [{'description': 'Unicoin - Rent August 26', 'amount': '24504.94'}],
            currency='BWP')['hard'], [])


class DraftAndClearedStillCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user('lctrl', 'lc@example.invalid', 'x')

    def test_a_rejected_twin_does_not_block(self):
        PaymentRequest.objects.create(
            ref='PR-CTRL-REJ', entity='ADIC', status=PaymentRequest.Status.REJECTED,
            payee='X', currency='BWP', total='500.00', created_by=self.u,
            line_items=[{'claim_number': 'G2026009111', 'amount': '500.00'}])
        self.assertEqual(find_duplicates(
            [{'claim_number': 'G2026009111', 'amount': '500.00'}],
            currency='BWP')['hard'], [],
            'a rejected request is dead money and must not block a fresh one')


class SameRequestDuplicationIsPermanentTests(TestCase):
    """Control 2 — made explicit and permanent, as asked."""

    def test_a_pack_that_repeats_its_own_line_blocks(self):
        lines = [{'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '86470.94'},
                 {'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '86470.94'}]
        hard = compare_lines(lines, [], currency='BWP')['hard']
        self.assertTrue(hard)
        self.assertEqual(hard[0]['clash_kind'], 'same_request')


class TheRealFacPairTests(TestCase):
    """Manus, 2026-08-10: confirm WHICH control stops the FAC pair.

    The honest answer is NEITHER, and that is deliberate. The live pair reads
    'FAC Premium – May' against '– June'. A rule matching on the payee root would
    catch it — and would also block 'FAC Premium Cessions', a pack of real data
    where FMRE-MP MINING May and June are both 43,805.74 and Continental RE
    APR-MAY and JUNE-JUL are both 25,573.00. Different periods, genuine payments.

    Same payee, same amount, different period is NORMAL in facultative cessions.
    No rule can separate the good from the bad here, so this one goes to Finance
    rather than being decided by a matcher. My earlier test used identical text on
    both sides, which production does not have, and so credited the fix to the
    wrong rule — that is what these tests exist to stop.
    """

    MAY = {'description': 'FAC Premium - May', 'claim_number': 'N/A',
           'invoice_number': 'N/A', 'amount': '86470.94'}
    JUNE = {'description': 'FAC Premium - June', 'claim_number': 'N/A',
            'invoice_number': 'N/A', 'amount': '86470.94'}

    def test_neither_line_carries_a_token(self):
        from taskboard.payment_duplicates import line_tokens
        self.assertEqual(line_tokens(self.MAY), set())
        self.assertEqual(line_tokens(self.JUNE), set())

    def test_two_periods_of_the_same_premium_are_NOT_blocked(self):
        """Deliberate. Blocking these would block real facultative cessions."""
        self.assertEqual(
            compare_lines([self.MAY, self.JUNE], [], currency='BWP')['hard'], [])

    def test_the_identical_line_twice_IS_blocked_by_control_2(self):
        hard = compare_lines([self.MAY, dict(self.MAY)], [], currency='BWP')['hard']
        self.assertTrue(hard)
        self.assertEqual(hard[0]['clash_kind'], 'same_request')


class SameLoaderInsideFortyEightHoursTests(TestCase):
    """One loader appears in 13 of the 15 cases; six pairs are consecutive-day and
    three same-day. This rule alone would have caught nine of the fifteen."""

    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('qloader48', 'q48@example.invalid', 'x')
        cls.other = User.objects.create_user('rloader48', 'r48@example.invalid', 'x')
        PaymentRequest.objects.create(
            ref='PR-48H-YDAY', entity='ADIC', status=PaymentRequest.Status.PAID,
            payee='BARBARA ENTERPRISE', currency='BWP', total='14197.74',
            created_by=cls.loader,
            line_items=[{'description': 'BARBARA ENTERPRISE', 'amount': '14197.74'}])

    def _check(self, user, amount='14197.74'):
        from taskboard.payment_views import _same_loader_recent_same_amount
        return _same_loader_recent_same_amount(
            [{'description': 'Something else entirely', 'amount': amount}],
            currency='BWP', user=user)

    def test_the_same_loader_reloading_the_same_amount_is_flagged(self):
        hits = self._check(self.loader)
        self.assertTrue(hits, 'same person, same amount, inside 48 hours')
        self.assertEqual(hits[0]['clash_kind'], 'same_loader_48h')
        self.assertEqual(hits[0]['clash_ref'], 'PR-48H-YDAY')

    def test_a_different_person_is_not_flagged_by_this_rule(self):
        self.assertEqual(self._check(self.other), [],
                         'this rule is about one hand repeating itself')

    def test_a_different_amount_is_not_flagged(self):
        self.assertEqual(self._check(self.loader, amount='999.00'), [])
