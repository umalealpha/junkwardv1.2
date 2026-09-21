"""Regression tests for the URL-registration bugs found on 2026-08-24.

Both routes were registered AFTER a DRF router's urls had already been
appended to `urlpatterns`, so they were silently broken in production:

  * M365 Active Users list route  → 404 (registered on the shared router after
    router.urls was snapshot, so the register() was a no-op).
  * HRIS alerts "Generate Now"     → the literal 'generate' was captured as a
    viewset detail pk (POST 405 / GET 404) because the static path was appended
    after the router's catch-all detail route.

Each assertion FAILS on the pre-fix urlconf and passes after it. No DB needed —
these only resolve URLs, so they run without PostgreSQL.
"""
from django.test import SimpleTestCase
from django.urls import resolve


class RouteRegistrationTests(SimpleTestCase):
    def test_m365_active_users_list_route_exists(self):
        match = resolve('/api/v1/m365-active-users/')
        self.assertEqual(match.url_name, 'm365-active-users-list')

    def test_m365_last_run_still_resolves(self):
        # The static last-run/ path must still win over the router detail route.
        match = resolve('/api/v1/m365-active-users/last-run/')
        self.assertEqual(match.url_name, 'v1-m365-last-run')

    def test_hris_alerts_generate_not_shadowed_by_detail(self):
        match = resolve('/api/v1/hris/alerts/generate/')
        self.assertEqual(match.url_name, 'v1-hris-alert-generate')

    def test_hris_alert_detail_actions_still_resolve(self):
        # The uuid-scoped actions carry an extra path segment and must keep
        # resolving to their own views, not the router detail route.
        for verb, name in (('acknowledge', 'v1-hris-alert-ack'),
                           ('dismiss',     'v1-hris-alert-dismiss'),
                           ('resolve',     'v1-hris-alert-resolve')):
            path = f'/api/v1/hris/alerts/00000000-0000-0000-0000-000000000000/{verb}/'
            self.assertEqual(resolve(path).url_name, name)
