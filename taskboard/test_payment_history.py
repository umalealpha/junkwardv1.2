"""taskboard/test_payment_history.py — the per-line Payment History screen.

Finance spec 2026-09-08 (Bontle Tendani / Leano Makwapa): "Add a Payment History
screen using the same data already captured per line in the payment request
flow — claim number, invoice number, amount, supplier/payee … Filter by custom
date range plus Month/Year quick-select. Columns: date, claim number, payee,
invoice number, amount, status, with a totals row."

The register that already existed reads request-by-request. A claim is queried
at LINE grain, which is why this exists — and why it reads the same line records
the register, the duplicate sweep and the authorisation pack all read, rather
than a second data path.

These are the rules that must never regress:
  - the custom date range bounds the rows, both ends;
  - the month/year quick-select bounds them to that month, and wins over a
    range sent alongside it;
  - a month without a year is refused, never guessed;
  - the totals row adds up the lines shown, per currency, in Decimal;
  - a CANCELLED line is SHOWN as cancelled and left OUT of the payable total —
    never silently dropped. Nothing in this module is ever hard-deleted;
  - an ordinary raiser sees only their own requests; a finance approver sees
    the estate;
  - drafts are not history.

No real payee or staff names — fake suppliers only.

Run: manage.py test taskboard.test_payment_history
"""
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest


def _lines(*specs):
    """[(description, amount, claim, invoice)] -> stored line_items shape."""
    return [{'description': d, 'amount': a, 'claim_number': c,
             'invoice_number': i, 'gl_code': '', 'ref': ''}
            for d, a, c, i in specs]


class PaymentHistoryTests(TestCase):
    """The screen itself: filters, columns, totals, cancelled rows, scope."""

    def setUp(self):
        self.url = reverse('v1-payment-history')
        self.raiser = User.objects.create_user('raiser', password='x')
        # A real finance approver email — that is what _is_first_approver reads.
        self.approver = User.objects.create_user(
            'kago', email='ktshutlhedi@alphadirect.co.bw', password='x')
        self.other = User.objects.create_user('someone_else', password='x')

        # August: two lines, one claim payment and one supplier invoice.
        self.aug = self._request(
            ref='PAY/ADIC/2026/08/10/0001', payment_date=date(2026, 8, 10),
            total='16556.09', created_by=self.raiser,
            line_items=_lines(
                ('G2026004287 NONESUCH PANEL BEATERS', '10000.09',
                 'G2026004287', ''),
                ('Courier run July', '6556.00', '', 'IN102985'),
            ))
        # September, a different month and a different currency.
        self.sep = self._request(
            ref='PAY/ADIC/2026/09/07/0007', payment_date=date(2026, 9, 7),
            total='400.00', currency='ZAR', created_by=self.raiser,
            line_items=_lines(('Nonesuch Software licence', '400.00', '', 'IN777')))
        # Somebody else's request — must not show for an ordinary raiser.
        self.theirs = self._request(
            ref='PAY/ADIC/2026/08/12/0002', payment_date=date(2026, 8, 12),
            total='999.00', created_by=self.other,
            line_items=_lines(('Not yours', '999.00', '', 'IN999')))

    def _request(self, **over):
        data = {'ref': 'PAY/ADIC/2026/08/01/0000', 'subject': 'History fixture',
                'entity': 'Alpha Direct Insurance Company', 'currency': 'BWP',
                'category': PaymentRequest.Category.OTHER,
                'payee': 'Nonesuch Trading', 'total': '0.00',
                'status': PaymentRequest.Status.PAID, 'line_items': []}
        data.update(over)
        return PaymentRequest.objects.create(**data)

    def _get(self, who=None, **params):
        self.client.force_login(who or self.approver)
        r = self.client.get(self.url, params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    # ── the columns Finance named ────────────────────────────────────────────
    def test_every_column_the_spec_names_is_present(self):
        d = self._get()
        row = next(r for r in d['rows'] if r['invoice_number'] == 'IN102985')
        for key in ('date', 'claim_number', 'payee', 'invoice_number',
                    'amount', 'status', 'status_label'):
            self.assertIn(key, row)
        self.assertEqual(row['date'], '2026-08-10')
        self.assertEqual(row['amount'], '6556.00')

    def test_the_claim_number_comes_through_on_a_claim_line(self):
        d = self._get()
        row = next(r for r in d['rows'] if r['claim_number'] == 'G2026004287')
        # The payee reading strips the leading claim token off the description,
        # the way the duplicate sweep already does.
        self.assertEqual(row['payee'], 'NONESUCH PANEL BEATERS')

    def test_one_row_per_line_not_per_request(self):
        """The whole point of the screen — the August request has two lines."""
        d = self._get()
        aug_rows = [r for r in d['rows'] if r['ref'] == self.aug.ref]
        self.assertEqual(len(aug_rows), 2)

    # ── the custom date range ────────────────────────────────────────────────
    def test_the_from_date_bounds_the_rows(self):
        d = self._get(**{'from': '2026-09-01'})
        self.assertTrue(all(r['date'] >= '2026-09-01' for r in d['rows']))
        self.assertEqual({r['ref'] for r in d['rows']}, {self.sep.ref})

    def test_the_to_date_bounds_the_rows(self):
        d = self._get(to='2026-08-31')
        self.assertNotIn(self.sep.ref, {r['ref'] for r in d['rows']})

    def test_both_ends_together(self):
        d = self._get(**{'from': '2026-08-11', 'to': '2026-08-31'})
        self.assertEqual({r['ref'] for r in d['rows']}, {self.theirs.ref})

    def test_a_from_after_the_to_is_refused(self):
        self.client.force_login(self.approver)
        r = self.client.get(self.url, {'from': '2026-09-01', 'to': '2026-08-01'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('after', r.json()['detail'])

    def test_a_date_that_is_not_a_date_is_refused(self):
        self.client.force_login(self.approver)
        r = self.client.get(self.url, {'from': 'last Tuesday'})
        self.assertEqual(r.status_code, 400)

    # ── the month / year quick-select ────────────────────────────────────────
    def test_the_month_year_quick_select_bounds_the_rows(self):
        d = self._get(year='2026', month='9')
        self.assertEqual({r['ref'] for r in d['rows']}, {self.sep.ref})
        self.assertEqual(d['window'], 'September 2026')

    def test_a_year_on_its_own_takes_the_whole_year(self):
        d = self._get(year='2026')
        self.assertEqual(d['window'], '2026')
        self.assertEqual(len(d['rows']), 4)

    def test_the_quick_select_wins_over_a_custom_range(self):
        """It is the control the user just clicked."""
        d = self._get(year='2026', month='9', **{'from': '2026-08-01',
                                                 'to': '2026-08-31'})
        self.assertEqual({r['ref'] for r in d['rows']}, {self.sep.ref})

    def test_a_month_without_a_year_is_refused_not_guessed(self):
        """Silently assuming this year is how a total goes wrong unnoticed."""
        self.client.force_login(self.approver)
        r = self.client.get(self.url, {'month': '9'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('year', r.json()['detail'])

    def test_a_month_outside_1_to_12_is_refused(self):
        self.client.force_login(self.approver)
        self.assertEqual(
            self.client.get(self.url, {'year': '2026', 'month': '13'}).status_code, 400)

    # ── the totals row ───────────────────────────────────────────────────────
    def test_the_totals_row_adds_up_the_lines_shown(self):
        d = self._get(year='2026', month='8')
        bwp = next(t for t in d['totals'] if t['currency'] == 'BWP')
        # 10,000.09 + 6,556.00 + 999.00
        self.assertEqual(bwp['amount'], '17555.09')
        self.assertEqual(bwp['count'], 3)

    def test_totals_are_kept_apart_by_currency(self):
        """A single figure spanning BWP and ZAR would be a meaningless number."""
        d = self._get(year='2026')
        self.assertEqual({t['currency'] for t in d['totals']}, {'BWP', 'ZAR'})
        zar = next(t for t in d['totals'] if t['currency'] == 'ZAR')
        self.assertEqual(zar['amount'], '400.00')

    def test_the_total_is_exact_to_the_thebe(self):
        """Decimal, never float: 10000.09 + 6556.00 must not drift."""
        d = self._get(year='2026', month='8')
        bwp = next(t for t in d['totals'] if t['currency'] == 'BWP')
        self.assertEqual(Decimal(bwp['amount']),
                         Decimal('10000.09') + Decimal('6556.00') + Decimal('999.00'))

    # ── a cancelled item is shown, never dropped ─────────────────────────────
    def test_a_cancelled_request_is_shown_as_cancelled(self):
        self.sep.status = PaymentRequest.Status.CANCELLED
        self.sep.save(update_fields=['status'])
        d = self._get(year='2026', month='9')
        self.assertEqual(len(d['rows']), 1)
        self.assertTrue(d['rows'][0]['cancelled'])
        self.assertEqual(d['rows'][0]['status_label'], 'Cancelled')

    def test_a_cancelled_line_is_shown_as_cancelled(self):
        """A single pulled line inside a live request."""
        items = list(self.aug.line_items)
        items[1] = {**items[1], 'cancelled': True}
        self.aug.line_items = items
        self.aug.save(update_fields=['line_items'])
        d = self._get(year='2026', month='8')
        pulled = next(r for r in d['rows'] if r['invoice_number'] == 'IN102985')
        self.assertTrue(pulled['cancelled'])
        self.assertEqual(pulled['status_label'], 'Cancelled')

    def test_a_cancelled_line_is_left_out_of_the_payable_total(self):
        """Shown, but not payable — and reported separately so the figure is
        readable rather than just missing."""
        items = list(self.aug.line_items)
        items[1] = {**items[1], 'cancelled': True}
        self.aug.line_items = items
        self.aug.save(update_fields=['line_items'])
        d = self._get(year='2026', month='8')
        bwp = next(t for t in d['totals'] if t['currency'] == 'BWP')
        self.assertEqual(bwp['amount'], '10999.09')      # 17,555.09 - 6,556.00
        self.assertEqual(bwp['cancelled_amount'], '6556.00')
        self.assertEqual(bwp['cancelled_count'], 1)

    def test_a_held_line_says_so(self):
        """The CFO's per-line authorisation — 'approve 9, hold 1'."""
        items = list(self.aug.line_items)
        items[0] = {**items[0], 'line_status': 'held'}
        self.aug.line_items = items
        self.aug.save(update_fields=['line_items'])
        d = self._get(year='2026', month='8')
        held = next(r for r in d['rows'] if r['claim_number'] == 'G2026004287')
        self.assertEqual(held['status'], 'held')

    # ── who sees what ────────────────────────────────────────────────────────
    def test_an_ordinary_raiser_sees_only_their_own_requests(self):
        d = self._get(self.raiser, year='2026')
        self.assertEqual(d['scope'], 'mine')
        self.assertNotIn(self.theirs.ref, {r['ref'] for r in d['rows']})

    def test_a_finance_approver_sees_the_estate(self):
        d = self._get(self.approver, year='2026')
        self.assertEqual(d['scope'], 'all')
        self.assertIn(self.theirs.ref, {r['ref'] for r in d['rows']})

    def test_it_needs_a_login(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    # ── drafts are not history ───────────────────────────────────────────────
    def test_a_draft_never_appears(self):
        """A draft is a form somebody is still filling in."""
        self._request(ref='PAY/ADIC/2026/08/20/0003',
                      status=PaymentRequest.Status.DRAFT,
                      payment_date=date(2026, 8, 20), created_by=self.raiser,
                      line_items=_lines(('Half typed', '50.00', '', '')))
        d = self._get(year='2026', month='8')
        self.assertNotIn('PAY/ADIC/2026/08/20/0003', {r['ref'] for r in d['rows']})

    # ── the date a row is filed under ────────────────────────────────────────
    def test_a_request_with_no_payment_date_falls_back_to_the_date_raised(self):
        p = self._request(ref='PAY/ADIC/2026/07/04/0009', payment_date=None,
                          created_by=self.raiser,
                          line_items=_lines(('No payment date', '25.00', '', '')))
        PaymentRequest.objects.filter(pk=p.pk).update(
            created_at=datetime(2026, 7, 4, 9, 0, tzinfo=dt_timezone.utc))
        d = self._get(year='2026', month='7')
        self.assertEqual([r['ref'] for r in d['rows']], ['PAY/ADIC/2026/07/04/0009'])
        self.assertEqual(d['rows'][0]['date'], '2026-07-04')
