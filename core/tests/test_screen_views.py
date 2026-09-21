"""Screen-usage telemetry (CFO 2026-09-03): "who opens which screen, per
minute, staff only, no content".

Covers the path normaliser (ids → :id, query string dropped), the one-row-per
(user, screen, minute) key, the auth gates on both endpoints, the aggregated
report and the 180-day prune.
"""
from datetime import datetime, timedelta, timezone as dt_tz
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.screen_view_models import ScreenView
from core.screen_view_views import normalise_screen

BEACON = '/api/v1/adoption/screen-view/'
REPORT = '/api/v1/adoption/screens/'


class NormaliseScreenTests(TestCase):
    def test_uuid_segment_becomes_id(self):
        self.assertEqual(
            normalise_screen('/payment-requests/3f2a9c1e-7b4d-4e2a-9f1b-0c8d7e6f5a4b/edit'),
            '/payment-requests/:id/edit')

    def test_integer_segment_becomes_id(self):
        self.assertEqual(normalise_screen('/tasks/123'), '/tasks/:id')

    def test_hex_id_becomes_id_but_words_survive(self):
        self.assertEqual(normalise_screen('/claims/deadbeef01/notes'), '/claims/:id/notes')
        self.assertEqual(normalise_screen('/payroll/sign-off'), '/payroll/sign-off')

    def test_query_string_and_trailing_slash_dropped(self):
        self.assertEqual(normalise_screen('/adoption/?days=30&company=x#top'), '/adoption')

    def test_empty_path_is_root(self):
        self.assertEqual(normalise_screen(''), '/')


class ScreenViewBeaconTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user('beacon_staff', password='x')

    def setUp(self):
        self.client = APIClient()

    def test_anonymous_is_401(self):
        r = self.client.post(BEACON, {'screen': '/tasks', 'surface': 'desktop'}, format='json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(ScreenView.objects.count(), 0)

    def test_bad_surface_is_400(self):
        self.client.force_authenticate(self.staff)
        r = self.client.post(BEACON, {'screen': '/tasks', 'surface': 'tv'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_double_post_in_same_minute_is_one_row(self):
        self.client.force_authenticate(self.staff)
        fixed = datetime(2026, 9, 3, 8, 15, tzinfo=dt_tz.utc)
        with patch('core.screen_view_views._minute_now', return_value=fixed):
            r1 = self.client.post(BEACON, {'screen': '/tasks/123?x=1', 'surface': 'desktop'},
                                  format='json')
            r2 = self.client.post(BEACON, {'screen': '/tasks/456', 'surface': 'desktop'},
                                  format='json')
        self.assertEqual((r1.status_code, r2.status_code), (201, 200))
        self.assertTrue(r1.json()['ok'] and r2.json()['ok'])
        rows = list(ScreenView.objects.filter(user=self.staff))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].screen, '/tasks/:id')
        self.assertEqual(rows[0].minute, fixed)
        self.assertEqual(rows[0].surface, 'desktop')


class ScreenViewReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('report_admin', password='x', is_superuser=True)
        cls.a = User.objects.create_user('viewer_a', password='x')
        cls.b = User.objects.create_user('viewer_b', password='x')
        now = timezone.now().replace(second=0, microsecond=0)
        rows = [
            (cls.a, '/tasks', 'desktop', now),
            (cls.a, '/tasks', 'desktop', now - timedelta(minutes=1)),
            (cls.b, '/tasks', 'desktop', now),
            (cls.a, '/app/approve', 'app', now),
            # Outside a 7-day window — must not count.
            (cls.b, '/adoption', 'desktop', now - timedelta(days=10)),
        ]
        ScreenView.objects.bulk_create(
            ScreenView(user=u, screen=s, surface=sf, minute=m) for u, s, sf, m in rows)

    def setUp(self):
        self.client = APIClient()

    def test_non_admin_is_403(self):
        self.client.force_authenticate(self.a)
        self.assertEqual(self.client.get(REPORT).status_code, 403)

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get(REPORT).status_code, 401)

    def test_admin_gets_aggregated_users_and_hits(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(REPORT + '?days=7')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['days'], 7)
        by_screen = {(row['screen'], row['surface']): row for row in body['rows']}
        self.assertEqual(by_screen[('/tasks', 'desktop')]['users'], 2)
        self.assertEqual(by_screen[('/tasks', 'desktop')]['hits'], 3)
        self.assertEqual(by_screen[('/app/approve', 'app')]['users'], 1)
        self.assertNotIn(('/adoption', 'desktop'), by_screen)
        # Ordered by distinct users first.
        self.assertEqual(body['rows'][0]['screen'], '/tasks')

    def test_days_is_clamped(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(REPORT + '?days=1').json()['days'], 7)
        self.assertEqual(self.client.get(REPORT + '?days=9999').json()['days'], 180)
        self.assertEqual(self.client.get(REPORT + '?days=abc').json()['days'], 30)


class PruneScreenViewsTests(TestCase):
    def test_prune_deletes_only_old_rows(self):
        u = User.objects.create_user('prune_user', password='x')
        now = timezone.now().replace(second=0, microsecond=0)
        ScreenView.objects.create(user=u, screen='/old', surface='desktop',
                                  minute=now - timedelta(days=181))
        ScreenView.objects.create(user=u, screen='/fresh', surface='desktop',
                                  minute=now - timedelta(days=179))
        out = StringIO()
        call_command('prune_screen_views', stdout=out)
        self.assertIn('Pruned 1', out.getvalue())
        self.assertEqual(list(ScreenView.objects.values_list('screen', flat=True)), ['/fresh'])
