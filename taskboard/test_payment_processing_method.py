"""taskboard/test_payment_processing_method.py — Bulk / Individual.

Kelvin Kimani spec 2026-09-08. A request can hold several invoice lines for one
supplier — the E.G Couriers example carries 8 lines under one payee, BWP
16,556.09 — and those lines were always presented as a single lump with no
statement of how the money should actually leave. The inputter now records ONE
decision and the request carries it, so the approver and whoever keys the bank
both know the intent.

These are the rules that must never regress:
  - the default is BULK, including when nothing is sent and when a nonsense
    value is sent (a grouping preference must never refuse a payment);
  - with a single line the choice is meaningless and is NOT a live decision —
    it silently reads Bulk;
  - the request states the method plainly on the pack, with the count of
    payments it implies (1 vs N), next to the liquidity check;
  - amounts NEVER change: the liquidity total is identical under both methods;
  - THE RECONCILIATION GUARD — bulk total and the sum of the individual
    payments must reconcile EXACTLY to TOTAL PAYABLE or the request does not
    finalise. Exact, not near: one thebe out is out;
  - a cancelled line is not a payment, so it does not swell the count, and it
    is not counted in the reconciliation either.

Omni moves no money anywhere in here — Bulk/Individual is a recorded intent on
a workflow record. No real payee or staff names.

Run: manage.py test taskboard.test_payment_processing_method
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest, money_dec
from taskboard.test_helpers import seed_adic

ACCT = '62011223344'

# The E.G Couriers shape from the spec: several invoices, one payee, one total.
EIGHT_LINES = [
    {'description': f'Courier run {n}', 'amount': amt, 'ref': f'IN10298{n}',
     'invoice_number': f'IN10298{n}'}
    for n, amt in enumerate(
        ['2000.00', '2000.00', '2000.00', '2000.00',
         '2000.00', '2000.00', '2000.00', '2556.09'], start=1)
]
EIGHT_TOTAL = Decimal('16556.09')


class ProcessingMethodCreateTests(TestCase):
    """What the create endpoint records, and what it defaults to."""

    def setUp(self):
        self.me = User.objects.create_user('raiser', password='x')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'E.G Nonesuch Couriers — July',
            'category': PaymentRequest.Category.OTHER,
            'line_items': EIGHT_LINES,
            'payee': 'Nonesuch Couriers',
            'account_name': 'Nonesuch Couriers',
            'bank_name': 'FNB',
            'account_number': ACCT,
            'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    # ── the default ──────────────────────────────────────────────────────────
    def test_the_default_is_bulk(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(PaymentRequest.objects.get().processing_method, 'bulk')

    def test_individual_is_recorded_when_chosen(self):
        self.assertEqual(self.post(processing_method='individual').status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertTrue(pr.is_individual)
        self.assertEqual(pr.payment_count(), 8)

    def test_a_nonsense_value_falls_back_to_bulk_rather_than_refusing(self):
        """A grouping preference must never stand between a real invoice and
        the approver."""
        r = self.post(processing_method='whenever-you-like')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PaymentRequest.objects.get().processing_method, 'bulk')

    # ── one line: the choice is meaningless ──────────────────────────────────
    def test_a_single_line_is_forced_to_bulk_even_if_individual_is_sent(self):
        """With one line, bulk and individual ARE the same one payment."""
        r = self.post(line_items=[{'description': 'MCS 1162 July',
                                   'amount': '1250.00', 'ref': 'MCS 1162'}],
                      processing_method='individual')
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.processing_method, 'bulk')
        self.assertEqual(pr.payment_count(), 1)

    def test_the_choice_is_only_live_with_more_than_one_line(self):
        one = [{'description': 'One', 'amount': '10.00'}]
        self.assertFalse(PaymentRequest.show_processing_choice(one))
        self.assertTrue(PaymentRequest.show_processing_choice(EIGHT_LINES))

    # ── amounts never change ─────────────────────────────────────────────────
    def test_the_total_is_identical_under_both_methods(self):
        self.assertEqual(self.post().status_code, 201)
        bulk_total = PaymentRequest.objects.get().total
        PaymentRequest.objects.all().delete()
        self.assertEqual(self.post(processing_method='individual').status_code, 201)
        self.assertEqual(PaymentRequest.objects.get().total, bulk_total)
        self.assertEqual(bulk_total, EIGHT_TOTAL)

    # ── what the request shows ───────────────────────────────────────────────
    def test_the_pack_states_the_method_and_the_payment_count(self):
        self.assertEqual(self.post(processing_method='individual',
                                   opening_balance='500000.00').status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertIn('INDIVIDUAL', pr.formatted_html)
        self.assertIn('8 payments', pr.formatted_html)
        # Next to the liquidity check in Section B, as the spec asks.
        self.assertIn('Transactions to key', pr.formatted_html)

    def test_the_bulk_pack_says_one_payment(self):
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertIn('BULK (1 payment)', pr.formatted_html)

    def test_the_emailed_plaintext_states_it_too(self):
        """A method shown on one surface and not the other is worse than none."""
        self.assertEqual(self.post(processing_method='individual').status_code, 201)
        self.assertIn('PROCESSING METHOD: INDIVIDUAL (8 payments)',
                      PaymentRequest.objects.get().task.body)

    def test_the_detail_endpoint_exposes_it(self):
        self.assertEqual(self.post(processing_method='individual').status_code, 201)
        pr = PaymentRequest.objects.get()
        d = self.client.get(reverse('v1-payment-request-detail', args=[pr.id])).json()
        self.assertEqual(d['processing_method'], 'individual')
        self.assertEqual(d['payment_count'], 8)
        self.assertEqual(d['processing_method_label'], 'INDIVIDUAL (8 payments)')
        self.assertTrue(d['processing_choice_shown'])


class ReconciliationGuardTests(TestCase):
    """The build-time assertion, as a real guard.

    "Bulk total and the sum of the individual payments must reconcile exactly
    to TOTAL PAYABLE, or the request does not finalise."
    """

    def test_lines_that_sum_to_the_total_reconcile(self):
        self.assertIsNone(PaymentRequest.reconciliation_error(
            EIGHT_LINES, EIGHT_TOTAL))

    def test_a_total_one_thebe_out_does_not_finalise(self):
        """Exact, not near. One thebe out is out."""
        err = PaymentRequest.reconciliation_error(
            EIGHT_LINES, EIGHT_TOTAL + Decimal('0.01'), currency='BWP')
        self.assertIsNotNone(err)
        self.assertIn('0.01', err)
        self.assertIn('16,556.09', err)

    def test_a_total_one_thebe_under_does_not_finalise_either(self):
        self.assertIsNotNone(PaymentRequest.reconciliation_error(
            EIGHT_LINES, EIGHT_TOTAL - Decimal('0.01')))

    def test_a_dropped_line_does_not_finalise(self):
        self.assertIsNotNone(PaymentRequest.reconciliation_error(
            EIGHT_LINES[:-1], EIGHT_TOTAL))

    def test_the_message_names_both_figures_so_it_can_be_acted_on(self):
        err = PaymentRequest.reconciliation_error(
            [{'amount': '100.00'}], Decimal('250.00'), currency='ZAR')
        self.assertIn('ZAR 100.00', err)
        self.assertIn('ZAR 250.00', err)
        self.assertIn('ZAR 150.00', err)

    def test_a_cancelled_line_is_excluded_from_the_reconciliation(self):
        """A pulled line is retained on the record but is not a payment, so the
        remaining lines must reconcile to the recalculated total."""
        lines = [{'amount': '600.00'},
                 {'amount': '400.00', 'cancelled': True}]
        self.assertIsNone(PaymentRequest.reconciliation_error(
            lines, Decimal('600.00')))
        self.assertIsNotNone(PaymentRequest.reconciliation_error(
            lines, Decimal('1000.00')))

    def test_a_cancelled_line_is_not_counted_as_a_payment(self):
        pr = PaymentRequest(processing_method='individual', line_items=[
            {'amount': '600.00'}, {'amount': '300.00'},
            {'amount': '400.00', 'cancelled': True}])
        self.assertEqual(pr.payment_count(), 2)

    def test_nothing_payable_implies_no_payments(self):
        pr = PaymentRequest(processing_method='bulk', line_items=[
            {'amount': '400.00', 'cancelled': True}])
        self.assertEqual(pr.payment_count(), 0)

    def test_the_guard_reads_money_the_same_way_the_total_does(self):
        """Two money parsers is how the P90k cap was bypassed. The guard and
        the total must be the same arithmetic, HALF UP, in Decimal."""
        from taskboard.payment_views import _dec
        self.assertEqual(_dec('10.005'), money_dec('10.005'))
        self.assertEqual(money_dec('10.005'), Decimal('10.01'))     # HALF UP
        lines = [{'amount': '10.005'}]
        self.assertIsNone(PaymentRequest.reconciliation_error(
            lines, _dec('10.005')))

    def test_a_non_numeric_amount_reads_as_zero_and_is_caught(self):
        """Garbage in a line must fail the guard, not silently vanish."""
        self.assertIsNotNone(PaymentRequest.reconciliation_error(
            [{'amount': 'about five hundred'}], Decimal('500.00')))


class ReconciliationGuardAtCreateTests(TestCase):
    """The guard is wired into the create path, not just available."""

    def setUp(self):
        self.me = User.objects.create_user('raiser', password='x')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def test_a_pack_whose_lines_do_not_sum_is_refused(self):
        """Patched at the ONE place the total is derived, because a request
        raised through this endpoint derives its total from its own lines — the
        guard must still be there for every other path that sets a total."""
        from unittest import mock
        with mock.patch('taskboard.payment_views.PaymentRequest.reconciliation_error',
                        return_value='the lines do not add up'):
            r = self.client.post(self.url, {
                'subject': 'Deliberately inconsistent',
                'category': PaymentRequest.Category.OTHER,
                'line_items': EIGHT_LINES,
                'payee': 'Nonesuch Couriers', 'account_name': 'Nonesuch Couriers',
                'bank_name': 'FNB', 'account_number': ACCT,
                'new_payee_confirmed': True,
            }, content_type='application/json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-RECON-01')
        self.assertFalse(PaymentRequest.objects.exists())
