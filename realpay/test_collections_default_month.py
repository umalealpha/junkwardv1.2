"""The RealPay Collections Dashboard must default to the CURRENT MONTH when no
date range is given, so the headline figure is a clean single month that
reconciles to Finance's own month-by-month pull (Bokani, 2026-09-07) — never an
all-history sum that cannot match a monthly figure.

Without the default, a no-range request returned all history (P97.8M) and could
never tie to a month. These tests pin the default and prove explicit ranges are
still honoured. No customer data — the feed is not configured in tests, so the
view uses the (empty) uploaded-row fallback; only the echoed filters matter here.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient


class CollectionsDashboardDefaultsToCurrentMonth(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='fin_user', password='x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse('v1-realpay-collections-dashboard')

    def test_no_range_defaults_to_current_month(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        f = resp.json()['filters']
        today = datetime.date.today()
        self.assertEqual(f['start'], today.replace(day=1).isoformat())
        self.assertEqual(f['end'], today.isoformat())
        self.assertTrue(f['defaulted_to_current_month'])

    def test_explicit_range_is_honoured(self):
        resp = self.client.get(self.url, {'start': '2026-08-01', 'end': '2026-08-31'})
        self.assertEqual(resp.status_code, 200)
        f = resp.json()['filters']
        self.assertEqual(f['start'], '2026-08-01')
        self.assertEqual(f['end'], '2026-08-31')
        self.assertFalse(f['defaulted_to_current_month'])
