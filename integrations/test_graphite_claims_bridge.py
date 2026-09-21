"""Same-timestamp claims bridge (CFO file 2 Medium, 18-Sep-2026).

Acceptance: the same extraction timestamp is visible for both sides; the
bridge ties when the two sides agree and SAYS SO when they do not; Graphite
being unreachable shows as unavailable, never as a zero or a fake tie.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from django.core.cache import cache

from integrations.graphite_claims_bridge import build_bridge, cached_bridge
from integrations.graphite_feeds_views import graphite_claims_bridge_view
from integrations.models import GraphiteClaim

RO = 'integrations.graphite_claims_bridge.'


class ClaimsBridgeTests(TestCase):
    def setUp(self):
        cache.clear()
        for i, pay in enumerate(('100.00', '250.50')):
            GraphiteClaim.objects.create(graphite_id=i + 1, claim_number=f'C{i}',
                                         total_payment=Decimal(pay))

    def _graphite(self, count, paid):
        return [patch(RO + 'is_configured', return_value=True),
                patch(RO + 'query', return_value=[{'claim_count': count, 'paid': paid}])]

    def _run(self, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return build_bridge()

    def test_ties_when_both_sides_agree(self):
        b = self._run(self._graphite(2, Decimal('350.50')))
        self.assertEqual(b['omni'], {'claim_count': 2, 'paid': '350.50'})
        self.assertEqual(b['ties'], {'claim_count': True, 'paid': True})
        self.assertEqual(b['note'], '')

    def test_a_gap_is_shown_not_hidden(self):
        b = self._run(self._graphite(2, Decimal('400.00')))
        self.assertEqual(b['difference'], {'claim_count': 0, 'paid': '49.50'})
        self.assertEqual(b['ties'], {'claim_count': True, 'paid': False})
        self.assertIn('does not tie', b['note'])

    def test_one_as_of_stamp_for_both_sides(self):
        b = self._run(self._graphite(2, Decimal('350.50')))
        self.assertTrue(b['as_of'])
        self.assertIn('omni_mirror_synced_at', b)

    def test_graphite_unreachable_is_unavailable_not_zero(self):
        b = self._run([patch(RO + 'is_configured', return_value=True),
                       patch(RO + 'query', side_effect=RuntimeError('replica down'))])
        self.assertIsNone(b['graphite'])
        self.assertIsNone(b['ties'])
        self.assertIn('could not be read', b['note'])

    def test_not_configured_says_so(self):
        b = self._run([patch(RO + 'is_configured', return_value=False)])
        self.assertIsNone(b['graphite'])
        self.assertIn('not configured', b['note'])

    def test_rounding_is_half_up(self):
        b = self._run(self._graphite(2, Decimal('350.505')))
        self.assertEqual(b['graphite']['paid'], '350.51')

    def test_a_failed_read_is_not_cached(self):
        for p in [patch(RO + 'is_configured', return_value=True),
                  patch(RO + 'query', side_effect=RuntimeError('down'))]:
            p.start(); self.addCleanup(p.stop)
        self.assertIsNone(cached_bridge()['graphite'])
        self.assertIsNone(cache.get('graphite_claims_bridge:v1'))

    def test_bridge_endpoint_carries_the_bridge(self):
        for p in self._graphite(2, Decimal('350.50')):
            p.start()
            self.addCleanup(p.stop)
        user = get_user_model().objects.create_user('bridge_viewer', 'bv@example.com', 'x')
        req = APIRequestFactory().get('/api/v1/graphite-feeds/claims-bridge/')
        force_authenticate(req, user=user)
        res = graphite_claims_bridge_view(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['claims_bridge']['ties']['paid'], True)

    def test_no_mirror_no_bridge(self):
        GraphiteClaim.objects.all().delete()
        user = get_user_model().objects.create_user('bridge_viewer2', 'bv2@example.com', 'x')
        req = APIRequestFactory().get('/api/v1/graphite-feeds/claims-bridge/')
        force_authenticate(req, user=user)
        self.assertIsNone(graphite_claims_bridge_view(req).data['claims_bridge'])
