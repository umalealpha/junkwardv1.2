"""The RealPay money reads window on created_at, the real collection time
(CFO 17-Sep-2026: "use the actual RealPay info"), not InstalmentActionDate —
whose scheduled/future dates made the dashboard under-report. policy_statuses is
the deliberate exception: its 'no outcome received' label reasons about the
scheduled date, so it stays on InstalmentActionDate.
"""
from unittest.mock import patch
from django.test import SimpleTestCase

from realpay import graphite_feed


class MoneyReadsWindowOnCreatedAt(SimpleTestCase):
    def _sql(self, call):
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=True), \
             patch.object(graphite_feed.graphite_ro, 'query', return_value=[]) as q:
            call()
        return q.call_args[0][0]

    def test_collections_by_group_windows_on_created_at(self):
        import datetime as dt
        sql = self._sql(lambda: graphite_feed.collections_by_group(
            dt.date(2026, 9, 1), dt.date(2026, 9, 30)))
        self.assertIn('created_at >= %s', sql)
        self.assertIn('created_at <', sql)
        # It must NOT window on the scheduled column any more.
        self.assertNotIn('InstalmentActionDate >=', sql)

    def test_monthly_collections_windows_on_created_at(self):
        sql = self._sql(lambda: graphite_feed.monthly_collections(months=6))
        self.assertIn('created_at >=', sql)
        self.assertNotIn('InstalmentActionDate >=', sql)

    def test_collected_rows_windows_on_created_at_but_shows_tracking_date(self):
        sql = self._sql(lambda: graphite_feed.collected_rows(None, None))
        # Windowed on the real money date...
        self.assertIn('created_at <', sql)
        # ...but Finance still reconciles on the tracking date, so that is the
        # column shown.
        self.assertIn('InstalmentActionDate AS action_date', sql)

    def test_collected_in_window_uses_created_at(self):
        import datetime as dt
        sql = self._sql(lambda: graphite_feed.collected_in_window(
            ['COMG1234567890'], dt.date(2026, 9, 1), dt.date(2026, 9, 30)))
        self.assertIn('created_at', sql)
        self.assertNotIn('i.InstalmentActionDate >=', sql)
