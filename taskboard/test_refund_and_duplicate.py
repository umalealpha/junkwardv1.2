"""taskboard/test_refund_and_duplicate.py — B8 (refund payment type), the
CFO's build spec of 13 September 2026.

Omni moves no money anywhere in here. A payment request is a workflow record;
money leaves at FNB under a human's two-factor.

B8 — Refund is a third top-level payment type, and the two hand-raised refunds
must each name the payment they reverse:
  * a premium refund CANNOT be hand-raised (PAY-REFUND-01) — the overnight
    importer owns it, and a hand-keyed one next to the feed is how the same
    refund gets paid twice (CFO 2026-08-11);
  * the importer's own route still works, so the rule blocks people, not the feed;
  * an erroneous-payment refund with NO original payment reference is refused BY
    THE SERVER (PAY-REFUND-02), not merely by the form;
  * an excess refund likewise;
  * an excess refund must ALSO name the claim the excess was paid on
    (PAY-REFUND-03, CFO 2026-09-14) — an excess only exists because of a claim,
    so without the number the refund cannot be verified later. Excess refunds
    only: an erroneous payment has no claim behind it;
  * with the reference, both save and store it;
  * a non-refund category never keeps a reversal reference.

The B7 "copy a payment request" feature (and PAY-REFUND-03's copy-path tests)
live in taskboard/test_payment_request_duplicate.py, against the
taskboard.dropbox_views.duplicate_as_draft implementation that actually ships
(PR #983) — this file used to carry its own, earlier B7 module
(taskboard/payment_duplicate.py, since deleted as superseded) with different,
narrower carry-forward semantics; that whole class was removed here rather
than left testing code that no longer exists.

Run: manage.py test taskboard.test_refund_and_duplicate
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest
from taskboard.test_helpers import seed_adic


def _member(username, email):
    return User.objects.create_user(username, email=email, password='x')


class _Base(APITestCase):
    def setUp(self):
        seed_adic()
        self.raiser = _member('lmakwapa', 'lmakwapa@alphadirect.co.bw')
        # A finance approver must exist or the create path 500s before it gets
        # anywhere near the controls under test.
        self.pako = _member('pako', 'pkago@alphadirect.co.bw')
        self.kago = _member('kago', 'ktshutlhedi@alphadirect.co.bw')
        self.list_url = reverse('v1-payment-requests')

    def _post(self, **over):
        body = {
            'subject': 'Refund to client',
            'entity': 'Alpha Direct Insurance Company',
            'payee': 'A CLIENT',
            'account_name': 'A CLIENT',
            'bank_name': 'FNB Botswana',
            # Letters, not digits: no bank control has anything to judge, so
            # these suites test the refund and copy rules alone.
            'account_number': 'TEST-ACCT-NOT-REAL',
            'line_items': [{'description': 'Refund', 'amount': '1500.00'}],
        }
        body.update(over)
        self.client.force_authenticate(self.raiser)
        return self.client.post(self.list_url, body, format='json')


class RefundPaymentTypeTests(_Base):
    """B8."""

    # ── the three kinds exist, as ONE enum (no parallel list) ────────────────
    def test_the_three_refund_kinds_are_on_the_category_enum(self):
        codes = {c for c, _ in PaymentRequest.Category.choices}
        self.assertTrue({'premium_refund', 'erroneous_refund',
                         'excess_refund'} <= codes)
        self.assertEqual(
            set(PaymentRequest.REFUND_CATEGORIES),
            {'premium_refund', 'erroneous_refund', 'excess_refund'})
        # Premium refunds are importer-owned; the other two are hand-raised.
        self.assertIn('premium_refund', PaymentRequest.IMPORTER_ONLY_CATEGORIES)
        self.assertEqual(set(PaymentRequest.HAND_RAISED_REFUND_CATEGORIES),
                         {'erroneous_refund', 'excess_refund'})

    # ── PAY-REFUND-01: a premium refund cannot be hand-raised ────────────────
    def test_a_premium_refund_cannot_be_hand_raised(self):
        r = self._post(category='premium_refund',
                       original_payment_ref='RFND-000012')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-REFUND-01')
        self.assertFalse(PaymentRequest.objects
                         .filter(category='premium_refund').exists())

    def test_the_importer_route_for_premium_refunds_still_works(self):
        """The rule blocks PEOPLE, not the overnight feed. Without this the
        control could be 'fixed' by making premium refunds impossible."""
        pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/13/9001',
            category='premium_refund', graphite_ref='RFND-000012',
            subject='Premium refund', total=Decimal('100.00'),
            line_items=[{'description': 'Premium refund', 'amount': '100.00'}])
        self.assertEqual(pr.category, 'premium_refund')

    # ── PAY-REFUND-02: the original payment reference, on the SERVER ─────────
    def test_an_erroneous_refund_with_no_original_payment_is_refused(self):
        r = self._post(category='erroneous_refund')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-REFUND-02')
        self.assertIn('original payment reference', r.json()['detail'])
        self.assertFalse(PaymentRequest.objects.exists())

    def test_an_excess_refund_with_no_original_payment_is_refused(self):
        r = self._post(category='excess_refund', subject='Excess refund')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-REFUND-02')
        self.assertFalse(PaymentRequest.objects.exists())

    def test_blank_and_whitespace_are_not_a_reference(self):
        for bad in ('', '   ', '\t'):
            r = self._post(category='excess_refund', original_payment_ref=bad)
            self.assertEqual(r.status_code, 400, f'{bad!r} was accepted')
            self.assertEqual(r.json()['control'], 'PAY-REFUND-02')

    def test_a_refund_with_its_original_payment_is_accepted_and_stored(self):
        r = self._post(category='erroneous_refund',
                       original_payment_ref='PAY/ADIC/2026/08/07/0004')
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(pk=r.json()['id'])
        self.assertEqual(pr.category, 'erroneous_refund')
        self.assertEqual(pr.original_payment_ref, 'PAY/ADIC/2026/08/07/0004')

    def test_the_reference_is_not_kept_on_a_payment_that_is_not_a_refund(self):
        r = self._post(category='supplier', original_payment_ref='PAY/X/1',
                       subject='Rent',
                       line_items=[{'description': 'Rent', 'amount': '1500.00',
                                    'invoice_number': 'INV-1',
                                    'invoice_date': '2026-08-01',
                                    'terms_days': '30', 'due_date': '2026-09-30'}])
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(pk=r.json()['id'])
        self.assertEqual(pr.original_payment_ref, '')

    def test_the_approver_can_see_the_refund_and_what_it_reverses(self):
        r = self._post(category='excess_refund', subject='Excess refund',
                       original_payment_ref='RCPT-778',
                       line_items=[{'description': 'Excess refund',
                                    'amount': '1500.00',
                                    'claim_number': 'G2026004287'}])
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.json()['id']
        self.client.force_authenticate(self.raiser)
        d = self.client.get(reverse('v1-payment-request-detail', args=[pk]))
        self.assertEqual(d.status_code, 200, d.content)
        self.assertTrue(d.json()['is_refund'])
        self.assertEqual(d.json()['original_payment_ref'], 'RCPT-778')
        self.assertEqual(d.json()['category_label'], 'Excess refunds')


class ExcessRefundClaimNumberTests(_Base):
    """PAY-REFUND-03 (CFO 2026-09-14).

    An excess only exists because of a claim, so a refund of one must name that
    claim — otherwise nobody can verify the refund afterwards.
    """

    def _excess(self, *, claim_number=None, **over):
        line = {'description': 'Excess refund', 'amount': '1500.00'}
        if claim_number is not None:
            line['claim_number'] = claim_number
        return self._post(category='excess_refund', subject='Excess refund',
                          original_payment_ref='RCPT-778',
                          line_items=[line], **over)

    def test_an_excess_refund_with_no_claim_number_is_refused(self):
        r = self._excess()
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-REFUND-03')
        self.assertIn('which claim the excess was paid on', r.json()['detail'])
        self.assertFalse(PaymentRequest.objects.exists())

    def test_blank_and_whitespace_are_not_a_claim_number(self):
        for bad in ('', '   ', '\t'):
            r = self._excess(claim_number=bad)
            self.assertEqual(r.status_code, 400, f'{bad!r} was accepted')
            self.assertEqual(r.json()['control'], 'PAY-REFUND-03')
            self.assertFalse(PaymentRequest.objects.exists())

    def test_the_refused_line_is_named(self):
        """Which line is missing it, not just that something is."""
        r = self._post(
            category='excess_refund', subject='Excess refund',
            original_payment_ref='RCPT-778',
            line_items=[{'description': 'A', 'amount': '10.00',
                         'claim_number': 'G2026004287'},
                        {'description': 'B', 'amount': '20.00'}])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('line 2', r.json()['detail'])
        self.assertNotIn('line 1', r.json()['detail'])

    def test_an_excess_refund_with_its_claim_number_is_accepted_and_KEPT(self):
        """The gate insisted on it, so the stored pack must still carry it.

        The non-claim branch wipes claim numbers off every line; if excess
        refunds were not exempted, the fact the gate demanded would be thrown
        away one block later and the pack would be unverifiable anyway.
        """
        r = self._excess(claim_number='G2026004287')
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get(pk=r.json()['id'])
        self.assertEqual(pr.category, 'excess_refund')
        self.assertEqual(pr.original_payment_ref, 'RCPT-778')
        self.assertEqual(pr.line_items[0]['claim_number'], 'G2026004287')

    def test_an_erroneous_refund_needs_NO_claim_number(self):
        """Required for excess refunds specifically (CFO).

        An erroneous payment has no claim behind it — a gate on a document
        that cannot exist never opens.
        """
        r = self._post(category='erroneous_refund',
                       original_payment_ref='PAY/ADIC/2026/08/07/0004')
        self.assertEqual(r.status_code, 201, r.content)

    def test_the_original_payment_reference_is_still_required_too(self):
        """Both, not either — PAY-REFUND-02 fires first and on its own."""
        r = self._post(category='excess_refund', subject='Excess refund',
                       line_items=[{'description': 'Excess refund',
                                    'amount': '1500.00',
                                    'claim_number': 'G2026004287'}])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['control'], 'PAY-REFUND-02')
