"""
The CLAIMS feed cards are computed from omni's live claims mirror, not the
stale/broken Alpha-Brain push (CFO 2026-09-01: feed showed 53 claims vs ~4,500
real). These pin that the live numbers are right AND that they override a stale
pushed snapshot for the same dataset.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from integrations import graphite_live_claims as glc
from integrations.graphite_feeds_views import graphite_feed_detail
from integrations.models import GraphiteClaim, GraphiteSnapshot


def _claim(gid, ctype, policy, pay, res, status='open'):
    return GraphiteClaim.objects.create(
        graphite_id=gid, claim_number=f'G{gid}', claim_type=ctype,
        status=status, policy_number=policy, customer_name=f'C{gid}',
        total_payment=Decimal(str(pay)), total_reserve=Decimal(str(res)))


class LiveClaimsBuildTests(TestCase):
    def setUp(self):
        _claim(1, 'Motor', 'COMG2025001', 100, 400)
        _claim(2, 'Motor', 'DOMG2025002', 50, 0)
        _claim(3, 'Glass', 'MIS2025003', 10, 5)

    def test_by_type_counts_and_sums(self):
        rows = glc.build('claims_by_type')
        motor = [r for r in rows if r['claim_type'] == 'Motor'][0]
        self.assertEqual(motor['claim_count'], 2)
        self.assertEqual(motor['payment'], 150.0)
        self.assertEqual(motor['reserve'], 400.0)

    def test_by_group_buckets_on_policy_prefix(self):
        rows = {r['group']: r for r in glc.build('claims_by_group')}
        self.assertEqual(rows['COMG']['claim_count'], 1)
        self.assertEqual(rows['DOMG']['claim_count'], 1)
        self.assertEqual(rows['MIS']['claim_count'], 1)
        self.assertEqual(rows['COMG']['payment'], 100.0)

    def test_major_claims_ranked_by_exposure(self):
        rows = glc.build('major_claims')
        # claim 1 exposure 500 is the largest.
        self.assertEqual(rows[0]['claim_no'], 'G1')
        self.assertEqual(rows[0]['paid'], 100.0)
        self.assertEqual(rows[0]['reserve'], 400.0)

    def test_non_claims_dataset_returns_none(self):
        self.assertIsNone(glc.build('premium_by_group'))

    def test_empty_mirror_returns_none(self):
        GraphiteClaim.objects.all().delete()
        self.assertIsNone(glc.build('claims_by_type'))


class LiveOverridesStaleSnapshotTests(TestCase):
    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.user = get_user_model().objects.create_superuser(
            'live-feed', 'live-feed@example.com', 'x' * 16)
        # A STALE pushed snapshot claiming just 1 claim...
        GraphiteSnapshot.objects.create(
            dataset='claims_by_type',
            payload={'rows': [{'claim_type': 'Glass', 'payment': 8, 'reserve': 8, 'claim_count': 1}]},
            row_count=1)
        # ...while the live mirror holds real claims.
        _claim(1, 'Motor', 'COMG1', 100, 400)
        _claim(2, 'Glass', 'DOMG2', 10, 5)
        _claim(3, 'Motor', 'COMG3', 20, 0)

    def test_detail_uses_live_not_the_stale_snapshot(self):
        req = self.rf.get('/api/v1/graphite-feeds/claims_by_type/', secure=True)
        force_authenticate(req, user=self.user)
        body = graphite_feed_detail(req, 'claims_by_type').data
        # Live has 2 types (Motor, Glass) and 3 claims — not the stale single row.
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['totals']['claim_count'], 3)
        self.assertIn('live', body['source'].lower())

    def test_major_claims_panel_uses_live_not_the_stale_snapshot(self):
        # A stale major_claims push claiming 1 tiny claim...
        GraphiteSnapshot.objects.create(
            dataset='major_claims',
            payload={'rows': [{'claim_no': 'OLD', 'paid': 1, 'reserve': 1,
                               'claim_type': 'Glass', 'status': 'open'}]},
            row_count=1)
        from integrations.graphite_panels_views import major_claims
        req = self.rf.get('/api/v1/graphite-panels/major-claims/', secure=True)
        force_authenticate(req, user=self.user)
        body = major_claims(req).data
        # Live mirror has 3 claims; the biggest exposure (claim 1 = 500) leads —
        # not the stale 'OLD' row.
        self.assertEqual(body['claim_count'], 3)
        self.assertEqual(body['results'][0]['claim_no'], 'G1')
