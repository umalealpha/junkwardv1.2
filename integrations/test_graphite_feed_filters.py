"""
Graphite feed viewer — Instant-Insurance exclusion + column totals.

CFO 2026-09-01, two asks on the Graphite Feeds screen:
  * "we dont need instant insurance policies here … instant insurance is
    automatically renewed" → the renewals feed must drop the auto-renewing
    Instant (MIS-prefixed) policies, everywhere it is shown.
  * "where is the total here" → every feed popup shows a total row, summing the
    additive money/count columns over ALL rows, not just the visible page.

These pin the behaviour, not the plumbing: a filter that quietly also dropped a
DOMG renewal, or a total that summed a day-count, would be a working-looking
screen that lies.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from integrations.graphite_feed_filters import (
    column_totals, visible_rows)
from integrations.graphite_feeds_views import (
    graphite_feed_detail, graphite_feeds_list)
from integrations.graphite_panels_views import renewals_due
from integrations.models import GraphiteSnapshot


def _snap(dataset, rows):
    return GraphiteSnapshot.objects.create(
        dataset=dataset, payload={'rows': rows}, row_count=len(rows))


# ── pure helpers ────────────────────────────────────────────────────────────
class VisibleRowsTests(TestCase):

    def test_renewals_drops_instant_mis_keeps_the_rest(self):
        rows = [
            {'policy_no': 'MIS2025186633', 'product_line': 'Third Party Car Insurance'},
            {'policy_no': 'DOMG2024103671', 'product_line': 'Domestic Insurance'},
            {'policy_no': 'COMG2025189299', 'product_line': 'Commercial Insurance'},
            {'policy_no': 'mis2025000001', 'product_line': 'Legal Insurance'},  # lower-case
        ]
        out = visible_rows('renewals_trigger', rows)
        self.assertEqual([r['policy_no'] for r in out],
                         ['DOMG2024103671', 'COMG2025189299'])

    def test_other_feeds_are_never_narrowed(self):
        rows = [{'policy_no': 'MIS2025000001', 'claim_type': 'Motor'}]
        # A MIS row in a NON-renewals feed must pass through untouched.
        self.assertEqual(len(visible_rows('major_claims', rows)), 1)
        self.assertEqual(len(visible_rows('claims_by_type', rows)), 1)


class ColumnTotalsTests(TestCase):

    def test_sums_money_and_counts_skips_days_dates_ids(self):
        rows = [
            {'payment': '29,398.09', 'reserve': 31415.03, 'claim_type': 'Glass', 'claim_count': 8},
            {'payment': 3000, 'reserve': 12157.79, 'claim_type': 'Fire', 'claim_count': 1},
        ]
        t = column_totals(rows)
        self.assertEqual(t['payment'], 32398.09)
        self.assertEqual(t['reserve'], 43572.82)
        self.assertEqual(t['claim_count'], 9)
        self.assertNotIn('claim_type', t)   # text, never summed

    def test_never_sums_a_day_count_date_or_policy_number(self):
        rows = [
            {'policy_no': 'DOMG1', 'expiry_date': '2026-09-01', 'days_to_expiry': 0},
            {'policy_no': 'DOMG2', 'expiry_date': '2026-09-02', 'days_to_expiry': 1},
        ]
        t = column_totals(rows)
        # Summing days_to_expiry (→1) or a policy number is meaningless — none
        # of these are additive, so there is nothing to total.
        self.assertEqual(t, {})

    def test_a_column_with_a_non_number_is_not_summed(self):
        rows = [{'amount': 100}, {'amount': 'n/a'}]
        self.assertNotIn('amount', column_totals(rows))


# ── through the views ───────────────────────────────────────────────────────
class FeedViewTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.user = get_user_model().objects.create_superuser(
            username='feed-tester', email='feed-tester@example.com', password='x' * 24)

    def call(self, view, path, *args, **params):
        req = self.rf.get(path, params, secure=True)
        force_authenticate(req, user=self.user)
        return view(req, *args)

    def test_detail_hides_instant_and_totals_over_all_rows(self):
        _snap('claims_by_type', [
            {'payment': 29398.09, 'reserve': 31415.03, 'claim_type': 'Glass', 'claim_count': 8},
            {'payment': 3000, 'reserve': 12157.79, 'claim_type': 'Fire', 'claim_count': 1},
        ])
        body = self.call(graphite_feed_detail,
                         '/api/v1/graphite-feeds/claims_by_type/', 'claims_by_type').data
        self.assertEqual(body['totals']['claim_count'], 9)
        self.assertEqual(body['totals']['payment'], 32398.09)

    def test_renewals_detail_drops_instant_rows_and_counts_real_ones(self):
        _snap('renewals_trigger', [
            {'policy_no': 'MIS1', 'days_to_expiry': 0},
            {'policy_no': 'MIS2', 'days_to_expiry': 1},
            {'policy_no': 'DOMG1', 'days_to_expiry': 0},
        ])
        body = self.call(graphite_feed_detail,
                         '/api/v1/graphite-feeds/renewals_trigger/', 'renewals_trigger').data
        self.assertEqual(body['total'], 1)                       # only the DOMG one
        self.assertEqual([r['policy_no'] for r in body['results']], ['DOMG1'])
        self.assertEqual(body['totals'], {})                    # nothing additive to sum

    def test_list_card_count_reflects_the_instant_exclusion(self):
        _snap('renewals_trigger', [
            {'policy_no': 'MIS1', 'days_to_expiry': 0},
            {'policy_no': 'DOMG1', 'days_to_expiry': 0},
        ])
        body = self.call(graphite_feeds_list, '/api/v1/graphite-feeds/').data
        card = [f for f in body['feeds'] if f['dataset'] == 'renewals_trigger'][0]
        self.assertEqual(card['row_count'], 1)                  # not the raw 2

    def test_panel_renewals_due_also_drops_instant(self):
        _snap('renewals_trigger', [
            {'policy_no': 'MIS1', 'product_line': 'Legal Insurance', 'days_to_expiry': 3},
            {'policy_no': 'DOMG1', 'product_line': 'Domestic Insurance', 'days_to_expiry': 2},
        ])
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/').data
        self.assertEqual(body['total_in_feed'], 1)   # the MIS row is gone before counting
        self.assertEqual(body['due_count'], 1)
        self.assertEqual(body['results'][0]['policy_no'], 'DOMG1')
