"""
Every report must be reachable at the address the frontend actually calls.

HISTORY, because it explains the shape of these tests.

There used to be two hand-maintained lists of report addresses: reporting/urls.py
under /api/reports/, and the report block of alpha_finance/api_router.py under
/api/v1/reports/. The frontend only calls /api/v1 (api.ts sets
API_BASE = ${BASE_URL}/api/v1), so a report added to the first and forgotten in
the second was invisible to the whole product.

It shipped twice — cash-flow in August 2026, the market benchmark on 10 September
2026 — and both times users got a raw error on a page whose menu entry worked.

The first version of this file compared the two lists and failed on the
difference. That caught the symptom. Since 2026-09-11 reporting/urls.py MIRRORS
the router instead of authoring its own routes, so the difference cannot exist:
there is one list. These tests now pin that property, so anyone who reintroduces
a second hand-maintained list gets told immediately.
"""
from django.test import SimpleTestCase
from django.urls import get_resolver, reverse

V1_PREFIX = 'api/v1/'
LEGACY_PREFIX = 'api/reports/'
REPORTS = 'reports/'


def _paths_under(prefix: str) -> set[str]:
    """Every concrete URL registered below `prefix`, as its trailing path."""
    found = set()

    def walk(patterns, base=''):
        for p in patterns:
            route = base + str(getattr(p.pattern, '_route', p.pattern))
            if hasattr(p, 'url_patterns'):
                walk(p.url_patterns, route)
            elif route.startswith(prefix):
                found.add(route[len(prefix):])

    walk(get_resolver().url_patterns)
    return found


def _v1_reports() -> set[str]:
    return {p[len(REPORTS):] for p in _paths_under(V1_PREFIX) if p.startswith(REPORTS)}


class ReportRoutesMountedTests(SimpleTestCase):

    def test_the_legacy_mount_mirrors_the_router_exactly(self):
        """One authored list. If these ever differ, someone has started hand-
        maintaining reporting/urls.py again and the 404 bug is back on the table."""
        legacy, v1 = _paths_under(LEGACY_PREFIX), _v1_reports()
        self.assertEqual(
            sorted(v1 - legacy), [],
            'Reports reachable at /api/v1/reports/ but NOT at /api/reports/: '
            f'{sorted(v1 - legacy)}. reporting/urls.py should mirror the router.')
        self.assertEqual(
            sorted(legacy - v1), [],
            'Reports at /api/reports/ that the frontend cannot reach at '
            f'/api/v1/reports/: {sorted(legacy - v1)}. This is the exact bug that '
            'shipped twice — add them to alpha_finance/api_router.py.')

    def test_the_mirror_is_not_empty(self):
        """A mirror of nothing would pass the equality test above while every
        report was missing. Pin a floor and three specific reports."""
        v1 = _v1_reports()
        self.assertGreater(len(v1), 20, f'only {len(v1)} report routes found')
        for r in ('cash-flow/', 'peer-benchmark/', 'ma-profit-loss/'):
            self.assertIn(r, v1)

    def test_the_named_v1_routes_still_reverse_to_v1(self):
        """The mirrored routes are registered unnamed on purpose. If they ever
        carry the same names, reverse() resolves to whichever Django loaded
        last and code silently starts calling the legacy path."""
        for name, expected in (
            ('v1-cash-flow', '/api/v1/reports/cash-flow/'),
            ('v1-peer-benchmark', '/api/v1/reports/peer-benchmark/'),
            ('v1-ma-profit-loss', '/api/v1/reports/ma-profit-loss/'),
        ):
            self.assertEqual(reverse(name), expected)

    def test_the_market_benchmark_specifically_is_mounted(self):
        """The report this file was born for."""
        self.assertIn('reports/peer-benchmark/', _paths_under(V1_PREFIX))
