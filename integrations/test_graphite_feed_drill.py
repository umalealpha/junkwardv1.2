"""
Graphite Feeds claim drill-down — the claims behind a feed row, with client
name + subject, from the read-only claims mirror (CFO 2026-09-01).

Two things must hold: (1) it actually returns the client name and subject a
feed row hides, filtered to the row clicked; (2) it is locked to finance/exec —
this is the one claims view on the otherwise name-free feeds page that shows
client PII, so a non-finance user must be refused (403), not shown names.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from integrations.claims_views import graphite_feed_claim_drill
from integrations.models import GraphiteClaim


def _claim(gid, **kw):
    d = dict(claim_number=f'G{gid}', claim_type='Motor', status='Open',
             customer_name=f'Client {gid}', policy_number=f'COMG2025{gid}',
             product_name='Motor Comprehensive', damage_cause='Collision',
             total_payment=Decimal('1000'), total_reserve=Decimal('500'))
    d.update(kw)
    return GraphiteClaim.objects.create(graphite_id=gid, **d)


class ClaimDrillTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.cfo = get_user_model().objects.create_superuser(
            username='drill-cfo', email='drill-cfo@example.com', password='x' * 24)
        self.clerk = get_user_model().objects.create_user(
            username='drill-clerk', email='drill-clerk@example.com', password='x' * 24)
        _claim(1, claim_type='Motor', customer_name='Alpha Traders',
               damage_cause='Rear-end collision', total_payment=Decimal('12000'))
        _claim(2, claim_type='Motor', customer_name='Beta Holdings',
               damage_cause='Hail damage', total_payment=Decimal('8000'))
        _claim(3, claim_type='Glass', customer_name='Gamma Ltd',
               damage_cause='Windscreen', policy_number='DOMG2025003')

    def _get(self, user, **params):
        req = self.rf.get('/api/v1/graphite-feeds/claims/', params, secure=True)
        force_authenticate(req, user=user)
        return graphite_feed_claim_drill(req)

    def test_drill_by_type_returns_names_and_subject(self):
        r = self._get(self.cfo, claim_type='Motor')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total'], 2)
        names = {c['customer_name'] for c in r.data['results']}
        self.assertEqual(names, {'Alpha Traders', 'Beta Holdings'})
        # Subject (damage_cause) is present — the thing the feed hides.
        self.assertTrue(all(c['damage_cause'] for c in r.data['results']))
        # Worst first (largest payment leads).
        self.assertEqual(r.data['results'][0]['customer_name'], 'Alpha Traders')
        self.assertEqual(r.data['total_payment'], '20000.00')

    def test_drill_by_group_via_policy_prefix(self):
        r = self._get(self.cfo, search='COMG')
        self.assertEqual(r.data['total'], 2)   # the two COMG motor claims

    def test_non_finance_user_is_refused(self):
        r = self._get(self.clerk, claim_type='Motor')
        self.assertEqual(r.status_code, 403)

    def test_route_is_registered(self):
        self.assertEqual(reverse('v1-graphite-feed-claim-drill'),
                         '/api/v1/graphite-feeds/claims/')
