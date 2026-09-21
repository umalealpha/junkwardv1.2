"""Omni-vs-Graphite reconciliation on the claims register (Bokani 31883c46).

Shaped on her own workbook: matched claims, two restated reserves, a payment
difference, Omni-only and Graphite-only claims — both bridges must tie to 0.00,
and a Graphite outage must read as unavailable, never as zero."""
import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .claims_register_reconciliation import build_reconciliation
from .models import GraphiteClaim

D1, D2 = dt.date(2026, 9, 1), dt.date(2026, 9, 16)
MOD = 'integrations.claims_register_reconciliation'


def _claim(n, reserve, paid, day=D1, gid=[0]):
    gid[0] += 1
    return GraphiteClaim.objects.create(claim_number=n, graphite_id=gid[0],
                                        total_reserve=Decimal(reserve),
                                        total_payment=Decimal(paid), registered_date=day)


GRAPHITE = [
    {'claim_number': 'G1', 'reserve': '1000.00', 'paid': '100.00'},
    {'claim_number': 'G5502', 'reserve': '438981.00', 'paid': '0'},       # restated
    {'claim_number': 'G5434', 'reserve': '177447.13', 'paid': '0'},       # restated
    {'claim_number': 'G2', 'reserve': '50.00', 'paid': '30.00'},          # paid differs
    {'claim_number': 'G5546', 'reserve': '338280.00', 'paid': '1500.00'}, # Graphite only
]


class Reconciliation(TestCase):
    def setUp(self):
        _claim('G1', '1000.00', '100.00')
        _claim('G5502', '332918.78', '0')
        _claim('G5434', '142757.20', '0')
        _claim('G2', '50.00', '10.00')
        _claim('G5564', '214000.00', '0', day=D2)        # Omni only
        _claim('OUTSIDE', '999.00', '9.00', day=dt.date(2026, 8, 31))

    def _run(self, rows=GRAPHITE):
        with patch(f'{MOD}.is_configured', return_value=True), \
             patch(f'{MOD}.query', return_value=rows) as q:
            return build_reconciliation(D1, D2), q

    def test_counts_follow_the_register_date_range(self):
        r, q = self._run()
        self.assertEqual(r['counts'], {'omni': 5, 'graphite': 5, 'matched': 4, 'omni_only': 1,
                                       'graphite_only': 1, 'matched_reserve_difference': 2,
                                       'matched_payment_difference': 1})
        self.assertEqual(q.call_args[0][1], ['2026-09-01', '2026-09-16'])

    def test_reserve_bridge_ties_to_nil(self):
        b = self._run()[0]['reserve_bridge']
        self.assertEqual(b['less_omni_only'], '-214000.00')
        self.assertEqual(b['add_graphite_only'], '338280.00')
        self.assertEqual(b['add_differences_on_matched'], '140752.15')   # her figure
        self.assertEqual(b['unexplained_variance'], '0.00')
        self.assertTrue(b['ties'])

    def test_payment_bridge_carries_matched_payment_differences(self):
        b = self._run()[0]['payment_bridge']
        self.assertEqual(b['add_differences_on_matched'], '20.00')
        self.assertEqual(b['add_graphite_only'], '1500.00')
        self.assertEqual(b['unexplained_variance'], '0.00')

    def test_detail_lists(self):
        r = self._run()[0]
        self.assertEqual({x['claim_number'] for x in r['restated']}, {'G5502', 'G5434'})
        self.assertEqual(r['omni_only'][0], {'claim_number': 'G5564', 'reserve': '214000.00',
                                             'reported_date': '2026-09-16'})
        self.assertEqual(r['graphite_only'][0]['claim_number'], 'G5546')

    def test_graphite_down_is_unavailable_not_zero(self):
        with patch(f'{MOD}.is_configured', return_value=True), \
             patch(f'{MOD}.query', side_effect=OSError('down')):
            r = build_reconciliation(D1, D2)
        self.assertFalse(r['graphite_available'])
        self.assertIsNone(r['reserve_bridge'])
        self.assertNotIn('graphite', r['counts'])

    def test_endpoint_reads_the_date_filter(self):
        c = APIClient(); c.force_authenticate(User.objects.create_user('u', 'u@x.co', 'x'))
        with patch(f'{MOD}.is_configured', return_value=True), \
             patch(f'{MOD}.query', return_value=GRAPHITE):
            r = c.get('/api/v1/claims-register/reconciliation/?from=2026-09-01&to=2026-09-16')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['counts']['omni'], 5)
        self.assertEqual(c.get('/api/v1/claims-register/reconciliation/?from=2026-09-20&to=2026-09-01').status_code, 400)

    def test_signed_out_is_refused(self):
        self.assertIn(APIClient().get('/api/v1/claims-register/reconciliation/').status_code, (401, 403))
