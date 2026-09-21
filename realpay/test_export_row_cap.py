"""The collections CSV export must not silently truncate.

``integrations.graphite_ro.query`` caps at 500 rows unless a limit is passed.
``collected_rows`` advertised a 200,000 cap in its own signature and SQL but did
not pass it through, so every export returned at most 500 lines while the CSV
footer still showed the full month total — a file that could never reconcile
(Bokani, bug d4e6d54a, 2026-09-08).
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from realpay import graphite_feed


class CollectedRowsPassesLimitTests(SimpleTestCase):
    def test_limit_is_passed_through_to_the_replica_read(self):
        """Without the fix `limit` is absent and graphite_ro caps at 500."""
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=True), \
             patch.object(graphite_feed.graphite_ro, 'query', return_value=[]) as q:
            graphite_feed.collected_rows(None, None, limit=200000)

        self.assertEqual(q.call_count, 1)
        _args, kwargs = q.call_args
        self.assertIn(
            'limit', kwargs,
            'collected_rows must pass limit= to graphite_ro.query; without it the '
            'read silently stops at 500 rows.')
        self.assertEqual(kwargs['limit'], 200000)

    def test_tracking_start_date_is_selected(self):
        """Finance reconciles on the tracking date, so it must be in the SELECT."""
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=True), \
             patch.object(graphite_feed.graphite_ro, 'query', return_value=[]) as q:
            graphite_feed.collected_rows(None, None)

        sql = q.call_args[0][0]
        self.assertIn('InstalmentActionDate AS action_date', sql)

    def test_client_names_lookup_is_chunked_and_limited(self):
        """The name join must pass an explicit limit too, for the same reason."""
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=True), \
             patch.object(graphite_feed.graphite_ro, 'query', return_value=[]) as q:
            graphite_feed.client_names([f'MIS{i:05d}' for i in range(1500)], chunk=1000)

        self.assertEqual(q.call_count, 2, 'must chunk 1500 client numbers into 2 reads')
        for _args, kwargs in q.call_args_list:
            self.assertEqual(kwargs.get('limit'), 1000)

    def test_client_names_prefers_business_name_and_skips_blanks(self):
        rows = [{'pn': 'COMG001', 'nm': 'Acme Holdings'},
                {'pn': 'DOMG002', 'nm': ''},
                {'pn': '', 'nm': 'orphan'}]
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=True), \
             patch.object(graphite_feed.graphite_ro, 'query', return_value=rows):
            out = graphite_feed.client_names(['COMG001', 'DOMG002'])

        self.assertEqual(out, {'COMG001': 'Acme Holdings'})

    def test_unconfigured_replica_yields_no_names_rather_than_raising(self):
        with patch.object(graphite_feed.graphite_ro, 'is_configured', return_value=False):
            self.assertEqual(graphite_feed.client_names(['MIS1']), {})
