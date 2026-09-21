"""
Panels over the Graphite snapshots.

What matters here is not that a panel renders — it is that the shaping is
honest. A loss ratio flagged at the wrong threshold, a KYC roll-up that divides
by the wrong denominator, or a "renewals due" list that quietly drops the
already-expired policies are all failures that LOOK like working screens. So
each test pins the arithmetic, not the plumbing.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from integrations.graphite_panels_views import (
    BROKER_LR_FLAG, RENEWAL_WINDOW_DAYS, broker_loss_ratios, kyc_completeness,
    major_claims, renewals_due)
from integrations import graphite_live_broker_lr
from integrations.graphite_panels_views import _UNAVAILABLE_SOURCE
from integrations.models import GraphiteSnapshot


def _snap(dataset, rows):
    return GraphiteSnapshot.objects.create(
        dataset=dataset, payload={'rows': rows}, row_count=len(rows))


def _live_broker_rows(rows):
    """The broker panel is computed live from the replica now, not from the
    pushed snapshot, so these shaping tests feed the live builder instead. What
    they pin — the 70% rule, the incurred-vs-reserve distinction, the sort — is
    unchanged and still worth pinning."""
    return mock.patch.object(graphite_live_broker_lr, 'build',
                             return_value=list(rows))


class PanelTestCase(TestCase):
    """SERVER_NAME + secure=True mirror test_graphite_ingest: production settings
    force an HTTPS redirect, and a 301 would make every assertion below vacuous."""

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        # Broker premiums, large-loss amounts and per-agent KYC gaps are
        # financial data, so the panels sit behind the same gate as the other
        # financial screens rather than plain "signed in".
        self.user = get_user_model().objects.create_superuser(
            username='panel-tester', email='panel-tester@example.com',
            password='x' * 24)

    def call(self, view, path, **params):
        req = self.rf.get(path, params, secure=True)
        force_authenticate(req, user=self.user)
        return view(req)


class RenewalsDueTests(PanelTestCase):

    def test_window_filters_and_keeps_already_expired(self):
        _snap('renewals_trigger', [
            {'policy_no': 'P1', 'product_line': 'Motor', 'days_to_expiry': 5},
            {'policy_no': 'P2', 'product_line': 'Motor', 'days_to_expiry': 28},
            # Negative = already past expiry. The whole point of the panel is to
            # surface these, so a naive `0 <= d <= window` filter would hide the
            # rows that matter most.
            {'policy_no': 'P3', 'product_line': 'Property', 'days_to_expiry': -12},
        ])
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/', days=7).data

        self.assertEqual(body['due_count'], 2)
        self.assertEqual(body['expired_count'], 1)
        self.assertEqual(body['total_in_feed'], 3)
        self.assertEqual([r['policy_no'] for r in body['results']], ['P3', 'P1'])
        self.assertEqual(body['by_product_line'][0]['count'], 1)

    def test_window_cannot_exceed_what_the_feed_actually_carries(self):
        """Graphite's trigger list stops at 30 days. Accepting days=180 would
        return the same rows under a label promising six months of visibility."""
        _snap('renewals_trigger', [{'policy_no': 'P1', 'days_to_expiry': 10}])
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/', days=180).data
        self.assertEqual(body['window_days'], RENEWAL_WINDOW_DAYS)
        self.assertEqual(body['feed_window_days'], RENEWAL_WINDOW_DAYS)

    def test_a_blank_day_count_is_not_read_as_due_today(self):
        """Zero sorts to the top of the list, which is where it gets believed."""
        _snap('renewals_trigger', [
            {'policy_no': 'P1', 'days_to_expiry': 3},
            {'policy_no': 'P2', 'days_to_expiry': None},
        ])
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/').data
        self.assertEqual(body['due_count'], 1)
        self.assertEqual(body['undated_rows'], 1)
        self.assertEqual(body['results'][0]['policy_no'], 'P1')

    def test_only_allowlisted_columns_reach_the_browser(self):
        """The feed carries no personal data today. If Graphite added a client
        name to the export tomorrow, it must not flow straight through."""
        _snap('renewals_trigger', [
            {'policy_no': 'P1', 'days_to_expiry': 3, 'client_name': 'Should Not Appear'},
        ])
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/').data
        self.assertNotIn('client_name', body['results'][0])
        self.assertEqual(body['results'][0]['policy_no'], 'P1')

    def test_missing_feed_is_empty_not_an_error(self):
        body = self.call(renewals_due, '/api/v1/graphite-panels/renewals-due/').data
        self.assertEqual(body['due_count'], 0)
        self.assertIsNone(body['received_at'])


class BrokerLossRatioTests(PanelTestCase):

    def test_the_rule_is_applied_to_incurred_not_to_reserves_alone(self):
        """The feed's lr_on_reserve leaves PAID claims out. A broker who has
        already paid most of its claims scores near zero on that measure and
        passes the 70% rule in green while the business has incurred 85%."""
        rows = [
            {'broker': 'Mostly Paid Out', 'lr_on_reserve': 0.05, 'premium_fy': 1_000_000,
             'payment': 800_000, 'reserve': 50_000, 'claim_count': 40},
        ]
        with _live_broker_rows(rows):
            body = self.call(broker_loss_ratios,
                             '/api/v1/graphite-panels/broker-loss-ratios/').data
        r = body['results'][0]

        self.assertEqual(r['reserve_lr_pct'], 5.0)     # what the feed says
        self.assertEqual(r['incurred_lr_pct'], 85.0)   # what was actually incurred
        self.assertTrue(r['flagged'])
        self.assertEqual(body['flagged_count'], 1)

    def test_flags_on_the_cfo_threshold_and_sorts_worst_first(self):
        # Books are deliberately ABOVE ESCALATION_MIN_BOOK. The old figures here were
        # P100-200, which the credibility floor added later puts below escalation, so
        # nothing flagged and the test was asserting the floor rather than the 70%
        # rule it is named for. The ratios are unchanged; only the scale is.
        rows = [
            # lr_on_reserve is a ratio, not a percentage: 0.41 means 41%.
            {'broker': 'Safe Brokers', 'lr_on_reserve': 0.41, 'premium_fy': 100 * 1_000,
             'payment': 0, 'reserve': 41 * 1_000, 'claim_count': 3},
            {'broker': 'Hot Brokers', 'lr_on_reserve': 0.915, 'premium_fy': 200 * 1_000,
             'payment': 0, 'reserve': 183 * 1_000, 'claim_count': 11},
            # Exactly on the line is NOT flagged — the rule is "over 70%".
            # 50,000 is exactly ESCALATION_MIN_BOOK, so it is big enough to escalate
            # and is held back by the 70% rule alone — which is the case being tested.
            {'broker': 'Borderline', 'lr_on_reserve': BROKER_LR_FLAG / 100.0,
             'premium_fy': 50 * 1_000, 'payment': 0, 'reserve': 35 * 1_000,
             'claim_count': 2},
        ]
        with _live_broker_rows(rows):
            body = self.call(broker_loss_ratios,
                             '/api/v1/graphite-panels/broker-loss-ratios/').data

        self.assertEqual(body['broker_count'], 3)
        self.assertEqual(body['flagged_count'], 1)
        self.assertEqual(body['results'][0]['broker'], 'Hot Brokers')
        # 0.915 in, 91.5% out — the conversion the screens depend on.
        self.assertEqual(body['results'][0]['reserve_lr_pct'], 91.5)
        self.assertEqual(body['results'][0]['incurred_lr_pct'], 91.5)
        self.assertTrue(body['results'][0]['flagged'])
        self.assertFalse([r for r in body['results'] if r['broker'] == 'Borderline'][0]['flagged'])
        self.assertEqual(body['total_premium_fy'], 350_000.0)

    def test_blank_ratio_does_not_crash_the_panel(self):
        with _live_broker_rows([{'broker': 'No Data', 'lr_on_reserve': '',
                                 'premium_fy': None}]):
            body = self.call(broker_loss_ratios,
                             '/api/v1/graphite-panels/broker-loss-ratios/').data
        self.assertEqual(body['results'][0]['reserve_lr_pct'], 0.0)
        self.assertEqual(body['results'][0]['incurred_lr_pct'], 0.0)
        self.assertFalse(body['results'][0]['flagged'])

    def test_the_panel_fails_closed_when_the_replica_cannot_be_read(self):
        """No broker figures rather than the pushed ones.

        The pushed snapshot takes the broker from the selling agent and
        double-counts settled claims, so it escalates a different and wrong set
        of brokers. Reverting to it silently would put the old, understated flag
        list back in front of the CFO. A seeded snapshot must NOT resurface here.
        """
        _snap('broker_lr', [
            {'broker': 'From The Old Push', 'lr_on_reserve': 9.0, 'premium_fy': 100,
             'payment': 0, 'reserve': 900, 'claim_count': 1},
        ])
        with mock.patch.object(graphite_live_broker_lr, 'build', return_value=None):
            body = self.call(broker_loss_ratios,
                             '/api/v1/graphite-panels/broker-loss-ratios/').data

        self.assertEqual(body['results'], [])
        self.assertEqual(body['broker_count'], 0)
        self.assertEqual(body['flagged_count'], 0)
        self.assertFalse(body['computed_live'])
        self.assertEqual(body['source'], _UNAVAILABLE_SOURCE)


class KycCompletenessTests(PanelTestCase):

    def test_branch_rollup_divides_by_policies_not_by_agents(self):
        # Two agents in one branch: 190 of 200 policies complete = 95.0%.
        # Averaging the two agents' own percentages would give 75.0% — the
        # classic wrong answer, and the reason this test exists.
        _snap('kyc_completeness', [
            {'agent': 'A', 'branch': 'Gaborone', 'policies': 190, 'complete': 190, 'pct_complete': 1},
            {'agent': 'B', 'branch': 'Gaborone', 'policies': 10, 'complete': 0, 'pct_complete': 0},
        ])
        body = self.call(kyc_completeness, '/api/v1/graphite-panels/kyc-completeness/').data

        self.assertEqual(body['by_branch'][0]['pct_complete'], 95.0)
        self.assertEqual(body['pct_complete'], 95.0)
        self.assertEqual(body['agent_count'], 2)
        # Ranked by policies actually missing KYC, so B (10 missing) leads A (0).
        self.assertEqual(body['worst_agents'][0]['agent'], 'B')
        # The feed's own per-agent figure is a ratio (1 = 100%), same as the
        # loss ratios. Passing it through raw put "1%" next to a 100% bar.
        self.assertEqual(
            [a['pct_complete'] for a in body['worst_agents'] if a['agent'] == 'A'], [100.0])


class MajorClaimsTests(PanelTestCase):

    def test_exposure_adds_magnitudes_and_reads_repudiation(self):
        # Reserves arrive negative in some Graphite cuts. Summing them signed
        # would net a large reserve against a large payment and rank a serious
        # claim as small.
        _snap('major_claims', [
            {'claim_no': 'C1', 'paid': 100_000, 'reserve': -400_000,
             'claim_type': 'Motor', 'status': 'Open', 'repudiated': 'No'},
            {'claim_no': 'C2', 'paid': 250_000, 'reserve': 0,
             'claim_type': 'Fire', 'status': 'Closed', 'repudiated': 'Yes'},
        ])
        body = self.call(major_claims, '/api/v1/graphite-panels/major-claims/').data

        self.assertEqual(body['results'][0]['claim_no'], 'C1')
        self.assertEqual(body['results'][0]['exposure'], 500_000.0)
        self.assertEqual(body['total_exposure'], 750_000.0)
        self.assertEqual(body['repudiated_count'], 1)
        self.assertFalse(body['results'][0]['repudiated'])
