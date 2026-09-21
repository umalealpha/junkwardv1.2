"""A date filter this viewset does not implement must be refused, not ignored.

Manus, 2026-08-09: `?entry_date__gte=2099-01-01` returned all 20,557 journal
entries. There is no filter backend here, so the parameter was accepted and
discarded — the caller believed it had filtered and got the whole ledger back.
A wrong answer delivered with a 200 is worse than an error.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class JournalEntryDateFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Company, Currency
        from ledger.models import JournalEntry
        cls.admin = User.objects.create_superuser('fjeadmin', 'je@example.invalid', 'x')
        cls.company = Company.objects.create(code='TSTJE', name='Test Entity')
        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        # Real rows, or the 2099 test passes on an empty table and certifies
        # nothing — which is exactly how the ignored filter survived a green
        # suite in the first place.
        for n, d in (('JE-T-1', date(2026, 8, 1)), ('JE-T-2', date(2026, 8, 5))):
            JournalEntry.objects.create(
                entry_number=n, entry_date=d, period_start=d,
                description='fixture', journal_type='general',
                status=JournalEntry.Status.POSTED, company=cls.company,
                created_by=cls.admin, currency_code=cls.bwp)

    def _c(self):
        c = APIClient()
        c.force_authenticate(self.admin)
        return c

    def _count(self, qs=''):
        res = self._c().get(f'/api/v1/journal-entries/{qs}')
        self.assertEqual(res.status_code, 200, res.content[:200])
        body = res.json()
        return body.get('count', len(body if isinstance(body, list) else body.get('results', [])))

    def test_the_table_is_not_empty_so_the_next_test_means_something(self):
        self.assertEqual(self._count(), 2)

    def test_a_future_floor_returns_nothing_not_everything(self):
        self.assertEqual(self._count('?entry_date__gte=2099-01-01'), 0,
                         'a year-2099 floor returning rows proves the filter is ignored')

    def test_a_real_window_selects_a_subset(self):
        self.assertEqual(self._count('?entry_date__gte=2026-08-03'), 1)

    def test_the_alias_matches_the_documented_parameter(self):
        self.assertEqual(self._count('?entry_date__gte=2099-01-01'),
                         self._count('?from_date=2099-01-01'))

    def test_an_unparseable_date_is_refused(self):
        res = self._c().get('/api/v1/journal-entries/?entry_date__gte=not-a-date')
        self.assertEqual(res.status_code, 400, res.content[:200])

    def test_an_unsupported_filter_is_refused_rather_than_dropped(self):
        res = self._c().get('/api/v1/journal-entries/?posted_date__gte=2026-08-01')
        self.assertEqual(res.status_code, 400,
                         'silently ignoring it returns unfiltered rows that look filtered')
        self.assertIn('posted_date__gte', str(res.json()))

    def test_ordinary_listing_still_works(self):
        self.assertEqual(self._count(), 2)

    def test_a_parameter_that_looks_like_nothing_is_still_refused(self):
        """Manus, 2026-08-09: `?bogus_param=1` returned all 20,557 because only
        names that LOOKED like filters were checked."""
        res = self._c().get('/api/v1/journal-entries/?bogus_param=1')
        self.assertEqual(res.status_code, 400, res.content[:200])

    def test_a_one_underscore_typo_does_not_silently_return_everything(self):
        """`entry_date_gte` — one underscore missing — quietly returned the lot."""
        res = self._c().get('/api/v1/journal-entries/?entry_date_gte=2099-01-01')
        self.assertEqual(res.status_code, 400,
                         'a near-miss filter name must never pass as unfiltered')
