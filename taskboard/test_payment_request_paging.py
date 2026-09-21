"""taskboard/test_payment_request_paging.py — the payment list must never
silently show you less than there is.

WHY (CFO 2026-09-15): the action list ran `ordered[:200]` with no page, no
total and nothing on screen saying it had stopped. With 378 requests on the
live system, ticking "include settled" returned 200 and dropped 178 — and the
screen looked like a complete list. Same class as the UniCoin audit finding
where "All Statuses" silently hid 38 rows.

What these tests pin:
  * the response always carries the TRUE total, not the number of rows returned;
  * paging through the whole list yields every request exactly once — nothing
    lost between pages, nothing repeated;
  * `has_more` is true while rows remain and false on the last page;
  * a bad limit/offset is refused rather than silently ignored.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, Currency
from taskboard.models import PaymentRequest

#: The single timestamp every fixture row is forced onto — see setUpTestData.
TIED_AT = datetime(2026, 9, 15, 6, 0, 0, tzinfo=timezone.utc)

#: Deliberately more than one default page (200) so a cap cannot hide inside
#: the fixture — the empty-book trap: a fixture that fits in one page proves
#: nothing about paging.
TOTAL = 205


class PaymentRequestPagingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # A superuser is all this view needs to take the CFO branch (`_is_cfo`
        # is `is_superuser or the configured CFO`), so the fixture carries no
        # real person's name or address.
        cls.cfo = User.objects.create_user(
            'paging-test-admin', 'paging-test-admin@example.invalid', 'x',
            is_superuser=True)
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='PRPAG', defaults={'name': 'Payment-Paging Test Co',
                                    'base_currency': cls.bwp})
        # Every fixture row shares ONE created_at. That is deliberate: paging sorts
        # on created_at, and a sort key with ties is where offset paging actually
        # breaks — a tied pair can land either side of a page edge and one row is
        # skipped or repeated. With distinct timestamps this suite would only
        # prove the arithmetic, never the ordering (Fable 5, H88, 2026-09-15).
        PaymentRequest.objects.bulk_create([
            PaymentRequest(
                ref=f'PAY/PRPAG/2026/09/15/{i:04d}',
                entity=cls.company.name,
                category=PaymentRequest.Category.OTHER,
                currency='BWP', subject=f'Paging fixture {i}',
                payee='Paging Test Payee',
                total=Decimal('10.00'),
                status=PaymentRequest.Status.PAID,
            ) for i in range(1, TOTAL + 1)
        ])
        PaymentRequest.objects.filter(ref__startswith='PAY/PRPAG/').update(
            created_at=TIED_AT)

    def setUp(self):
        self.client.force_authenticate(user=self.cfo)

    def _get(self, **params):
        params.setdefault('all', '1')       # include settled — the failing case
        res = self.client.get(reverse('v1-payment-requests'), params)
        self.assertEqual(res.status_code, 200, res.content[:400])
        return res.json()

    def test_the_total_is_the_real_total_not_the_rows_returned(self):
        body = self._get()
        page = body.get('page')
        self.assertIsNotNone(page, 'the response carries no page block at all')
        self.assertEqual(page['total'], TOTAL)
        self.assertLess(page['returned'], page['total'],
                        'fixture too small to prove anything about paging')
        self.assertTrue(page['has_more'])

    def test_paging_through_loses_nothing_and_repeats_nothing(self):
        seen: list[str] = []
        offset, guard = 0, 0
        while True:
            guard += 1
            self.assertLess(guard, 20, 'paging did not terminate')
            body = self._get(offset=offset, limit=50)
            seen.extend(r['ref'] for r in body['requests'])
            page = body['page']
            self.assertEqual(page['offset'], offset)
            if not page['has_more']:
                break
            offset += page['limit']

        self.assertEqual(len(seen), TOTAL, 'rows were lost or repeated across pages')
        self.assertEqual(len(set(seen)), TOTAL, 'the same row came back on two pages')

    def test_has_more_is_false_on_the_last_page(self):
        body = self._get(offset=TOTAL - 5, limit=50)
        self.assertFalse(body['page']['has_more'])
        self.assertEqual(body['page']['returned'], 5)

    def test_a_nonsense_limit_is_refused_not_ignored(self):
        res = self.client.get(reverse('v1-payment-requests'),
                              {'all': '1', 'limit': 'lots'})
        self.assertEqual(res.status_code, 400)
