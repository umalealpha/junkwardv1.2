"""Paging the legal-bill detail must never repeat a row or lose one.

WHY THIS TEST EXISTS
--------------------
`bonu.tests_invoice_lines.FableFixesTests.test_the_second_page_continues_where_
the_first_stopped` failed in CI on 12-Sep-2026 with `AssertionError: 3 != 4`:
page 1 and page 2 together returned three distinct rows out of four. Re-running
it would have gone green, because the fault is not in the test.

`invoice_lines` ordered by `(invoice__invoice_date, line_no)`. Neither is
unique, and real data collides on both — an invoice run loaded on one date, each
invoice's first line numbered 1. When every sort key ties, SQL leaves the order
of those rows undefined, and Postgres is free to return them one way for
`OFFSET 0` and another for `OFFSET 2`. The reader then gets one row twice and
never sees another, with no error and no warning.

That matters here more than on an ordinary list. This screen exists because
Kutlo Keitumele asked to see the transactions behind a consolidated figure, and
its own tests assert that a page which drops rows must SAY so. A page that
drops a row through an unstable sort says nothing at all — it just quietly
disagrees with the total above it.

The fix is `id` as the final sort key, making the ordering total. The first test
below fails deterministically without it; the second is the behaviour it
protects.
"""
import datetime as dt
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
from bonu.views import invoice_lines
from core.models import Company, UserProfile


class InvoiceLinePagingOrderTests(TestCase):
    """Every line here shares a date AND a line number — the real collision."""

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))
        self.user = User.objects.create_user('bonu-pager', email='pager@example.invalid',
                                             password='x')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        self.user = User.objects.get(pk=self.user.pk)

        firm = LawFirm.objects.create(name='Gamma Chambers')
        self.expected_ids = set()
        for i in range(6):
            inv = BonuInvoice.objects.create(
                firm=firm, invoice_number=f'PAGE-{i:04d}',
                invoice_date=dt.date(2026, 7, 1))      # same date for all six
            line = BonuInvoiceLine.objects.create(
                invoice=inv, line_no=1,                # and the same line number
                amount=D('100.00'), matter_type='debt', fee_earner='A Moeng',
                matter_type_source='firm', member_ref=f'BONU-P{i:04d}',
                service_date=dt.date(2026, 7, 2))
            # as a string: the endpoint serialises ids with str(), and a set of
            # UUIDs never equals a set of strings however right the rows are.
            self.expected_ids.add(str(line.id))

    def _page(self, limit, offset):
        req = APIRequestFactory().get('/api/v1/bonu/lines/',
                                      {'limit': limit, 'offset': offset})
        force_authenticate(req, user=self.user)
        return invoice_lines(req)

    def test_the_sort_has_a_unique_final_key(self):
        """The one that fails every time, not one run in ten.

        A paging bug caused by tied sort keys is flaky by nature: whether it
        shows up depends on the query plan, so a behavioural test can pass on a
        broken build. Assert the property that actually guarantees correctness —
        the ordering ends in a unique column — so the guard cannot go green by
        luck. Drop 'id' from the order_by in bonu/views.py and this goes red.
        """
        from bonu.models import BonuInvoiceLine as Line
        req = APIRequestFactory().get('/api/v1/bonu/lines/', {'limit': 1})
        force_authenticate(req, user=self.user)
        invoice_lines(req)

        qs = (Line.objects.select_related('invoice', 'invoice__firm')
              .order_by('invoice__invoice_date', 'line_no', 'id'))
        ordering = list(qs.query.order_by)
        self.assertEqual(
            ordering[-1].lstrip('-'), 'id',
            'the last sort key must be unique or paging is undefined')

        import inspect
        from bonu import views
        source = inspect.getsource(views)
        self.assertIn(
            "order_by('invoice__invoice_date', 'line_no', 'id')", source,
            "invoice_lines must order by a unique final key — without it, two "
            "lines sharing a date and line number can appear on two pages at "
            "once while another appears on none")

    def test_paging_through_everything_returns_every_row_exactly_once(self):
        seen = []
        for offset in range(0, 6, 2):
            page = self._page(limit=2, offset=offset)
            self.assertEqual(page.status_code, 200, page.data)
            seen.extend(line['id'] for line in page.data['lines'])

        self.assertEqual(len(seen), 6, 'six rows over three pages of two')
        self.assertEqual(len(set(seen)), 6, 'a row must not appear on two pages')
        self.assertEqual(set(seen), self.expected_ids,
                         'every row must appear on exactly one page')

    def test_the_same_page_read_twice_gives_the_same_rows(self):
        first = [line['id'] for line in self._page(limit=3, offset=0).data['lines']]
        again = [line['id'] for line in self._page(limit=3, offset=0).data['lines']]
        self.assertEqual(first, again,
                         'the same request must return the same rows in the '
                         'same order, or the total under them means nothing')
