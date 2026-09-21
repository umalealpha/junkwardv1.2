"""taskboard/test_pop_subject.py — the POP inherits the request SUBJECT.

Kelvin Kimani spec 2026-09-08, scoped off the live Payment Authorisation
Request output. The proof of payment used to take its own heading, typed or
defaulted separately from the request it came out of, so the POP for the
request carrying SUBJECT "MCS 1162 JULY" could go out titled something else
entirely. One source, no mismatch.

These are the rules that must never regress:
  - the POP heading is the request SUBJECT, verbatim;
  - a BLANK subject falls back to the request reference, so a POP is never
    left untitled;
  - the value is the subject AS AT SUBMISSION — editing the subject afterwards
    must not retitle a POP that has already gone out;
  - the internal REF never wins over a subject that is present, and neither
    does the Section A "Ref #" column, which carries roughly the same text but
    is typed per line;
  - under Individual, N POPs must not all carry the identical heading.

No real payee or staff names — fake suppliers only.

Run: manage.py test taskboard.test_pop_subject
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest
from taskboard.test_helpers import seed_adic

ACCT = '62055443311'


class PopSubjectStampedAtSubmissionTests(TestCase):
    """What the create endpoint writes into pop_subject, and when."""

    def setUp(self):
        self.me = User.objects.create_user('raiser', password='x')
        # A finance approver must exist or create fails before we get here.
        User.objects.create_user('approver', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Approver')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()

    def post(self, **over):
        body = {
            'subject': 'MCS 1162 JULY',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Courier run', 'amount': '1250.00',
                            'ref': 'MCS 1162 - JULY'}],
            'payee': 'Nonesuch Couriers',
            'account_name': 'Nonesuch Couriers',
            'bank_name': 'FNB',
            'account_number': ACCT,
            'new_payee_confirmed': True,
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    # ── the happy path ───────────────────────────────────────────────────────
    def test_pop_subject_inherits_the_request_subject(self):
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.pop_subject, 'MCS 1162 JULY')
        self.assertEqual(pr.pop_heading(), 'MCS 1162 JULY')

    def test_the_subject_wins_over_the_internal_ref(self):
        """The REF (PAY/ADIC/…) is a routing reference, not a heading."""
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertNotIn('PAY/', pr.pop_heading())

    def test_the_subject_wins_over_the_line_ref_column(self):
        """Section A's Ref # carries the same value with a dash. SUBJECT wins."""
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        self.assertEqual(pr.pop_heading(), 'MCS 1162 JULY')
        self.assertNotEqual(pr.pop_heading(), 'MCS 1162 - JULY')

    # ── the blank-subject guard ──────────────────────────────────────────────
    # This endpoint refuses a blank subject outright, so the stamp can never be
    # blank on a hand-raised request. The fallback still matters: rows created
    # elsewhere (the Graphite refund importer, fx_planning) never pass through
    # here, and the ~100 requests raised before the field existed hold ''.
    def test_the_endpoint_refuses_a_blank_subject(self):
        r = self.post(subject='')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('Subject', r.json()['detail'])

    def test_a_whitespace_only_subject_is_refused_too(self):
        self.assertEqual(self.post(subject='   ').status_code, 400)

    def test_a_stored_row_with_no_subject_falls_back_to_its_reference(self):
        """A POP is never left untitled — proven against a saved row, not just
        an unsaved instance."""
        pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/9001', subject='', pop_subject='',
            line_items=[], total='0.00')
        pr.refresh_from_db()
        self.assertEqual(pr.pop_heading(), 'PAY/ADIC/2026/09/08/9001')

    # ── as at submission, not read live ──────────────────────────────────────
    def test_the_heading_is_the_value_as_at_submission(self):
        """Editing the subject afterwards must not retitle a POP already out."""
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        pr.subject = 'RETYPED AFTER SUBMISSION'
        pr.save(update_fields=['subject'])
        pr.refresh_from_db()
        self.assertEqual(pr.pop_heading(), 'MCS 1162 JULY')

    # ── what the screen is told ──────────────────────────────────────────────
    def test_the_detail_endpoint_exposes_the_pop_heading(self):
        """The raiser and the approver should see the POP title before it goes
        out, not discover a mismatch afterwards."""
        self.assertEqual(self.post().status_code, 201)
        pr = PaymentRequest.objects.get()
        d = self.client.get(reverse('v1-payment-request-detail', args=[pr.id]))
        self.assertEqual(d.status_code, 200, d.content)
        self.assertEqual(d.json()['pop_subject'], 'MCS 1162 JULY')


class PopHeadingHelperTests(TestCase):
    """The helper's own edges, without going through the endpoint."""

    def _pr(self, **over):
        data = {'ref': 'PAY/ADIC/2026/09/07/0007', 'subject': 'MCS 1162 JULY',
                'pop_subject': 'MCS 1162 JULY', 'total': '1250.00'}
        data.update(over)
        return PaymentRequest(**data)

    def test_a_legacy_row_with_no_stamp_falls_back_to_its_subject(self):
        """The ~100 requests raised before the field existed hold '' — they must
        still read as titled."""
        self.assertEqual(self._pr(pop_subject='').pop_heading(), 'MCS 1162 JULY')

    def test_a_legacy_row_with_no_stamp_and_no_subject_falls_back_to_the_ref(self):
        self.assertEqual(self._pr(pop_subject='', subject='').pop_heading(),
                         'PAY/ADIC/2026/09/07/0007')

    # ── Bulk: one payment, one POP, one heading ──────────────────────────────
    def test_under_bulk_every_line_shares_the_request_heading(self):
        pr = self._pr()
        line = {'invoice_number': 'IN102985', 'amount': '500.00'}
        self.assertEqual(pr.pop_heading_for_line(line), 'MCS 1162 JULY')

    # ── Individual: N POPs must stay distinguishable ─────────────────────────
    def test_under_individual_the_invoice_number_is_appended(self):
        pr = self._pr()
        line = {'invoice_number': 'IN102985', 'amount': '500.00'}
        self.assertEqual(pr.pop_heading_for_line(line, individual=True),
                         'MCS 1162 JULY - IN102985')

    def test_the_separator_is_never_an_em_dash(self):
        """An em dash in a field that reaches FNB rejects the whole batch on
        RR10 'invalid character set' — proven in production 2026-08-19."""
        pr = self._pr()
        heading = pr.pop_heading_for_line({'invoice_number': 'IN102985'},
                                          individual=True)
        self.assertNotIn('—', heading)

    def test_individual_falls_back_to_the_line_ref_then_the_description(self):
        pr = self._pr()
        self.assertEqual(
            pr.pop_heading_for_line({'ref': 'IN777'}, individual=True),
            'MCS 1162 JULY - IN777')
        self.assertEqual(
            pr.pop_heading_for_line({'description': 'Courier run 12 Aug'},
                                    individual=True),
            'MCS 1162 JULY - Courier run 12 Aug')

    def test_individual_with_nothing_to_append_keeps_the_request_heading(self):
        pr = self._pr()
        self.assertEqual(pr.pop_heading_for_line({}, individual=True),
                         'MCS 1162 JULY')

    def test_a_tail_already_in_the_heading_is_not_repeated(self):
        pr = self._pr(subject='MCS 1162 JULY', pop_subject='MCS 1162 JULY')
        self.assertEqual(
            pr.pop_heading_for_line({'invoice_number': 'mcs 1162 july'},
                                    individual=True),
            'MCS 1162 JULY')

    def test_the_heading_never_exceeds_the_column(self):
        pr = self._pr(pop_subject='S' * 195)
        self.assertLessEqual(
            len(pr.pop_heading_for_line({'invoice_number': 'IN102985'},
                                        individual=True)), 200)
