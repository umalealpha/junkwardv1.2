"""
claims/tests_loss_ratio.py

Loss-ratio engine + report tests. Engine math is pure (providers injected), so
most tests need no DB. One DB test proves the live premium provider reads the
Graphite payment feed correctly (net of refunds, success-only).

Run:  python manage.py test claims
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from claims.loss_ratio import (
    build_client_loss_ratio,
    build_large_loss_clients,
    loss_ratio_band,
    loss_ratio_pct,
    premium_by_policy,
)


class LossRatioMathTests(TestCase):
    def test_ratio_basic(self):
        self.assertEqual(loss_ratio_pct(1000, 700), 70.0)
        self.assertEqual(loss_ratio_pct(1000, 1250), 125.0)
        self.assertEqual(loss_ratio_pct(200, 0), 0.0)

    def test_ratio_no_premium_is_none(self):
        self.assertIsNone(loss_ratio_pct(0, 500))
        self.assertIsNone(loss_ratio_pct(None, 500))

    def test_bands(self):
        self.assertEqual(loss_ratio_band(None), 'no_premium')
        self.assertEqual(loss_ratio_band(10), 'low')
        self.assertEqual(loss_ratio_band(55), 'healthy')
        self.assertEqual(loss_ratio_band(85), 'high')
        self.assertEqual(loss_ratio_band(120), 'underwater')
        self.assertEqual(loss_ratio_band(70), 'high')      # boundary
        self.assertEqual(loss_ratio_band(40), 'healthy')   # boundary


class ClientLossRatioTests(TestCase):
    # Babusi split: Reserves and Payments are independent columns/ratios.
    PREM = {'POL1': Decimal('1000'), 'POL2': Decimal('500'), 'POL3': Decimal('0')}
    RES  = {'POL1': Decimal('700'),  'POL2': Decimal('900'), 'POL3': Decimal('200')}
    PAY  = {'POL1': Decimal('300'),  'POL2': Decimal('250'), 'POL3': Decimal('100')}
    CUST = {'POL1': 'Acme Ltd', 'POL2': 'Acme Ltd', 'POL3': 'Beta Co'}

    def _build(self, **kw):
        return build_client_loss_ratio(
            _premium_map=self.PREM, _reserves_map=self.RES, _payments_map=self.PAY,
            _claims_status='ok', _customer_map=self.CUST, **kw)

    def test_rows_and_overall(self):
        r = self._build()
        self.assertEqual(r['summary']['policy_count'], 3)
        self.assertEqual(r['summary']['total_premium'], 1500.0)
        self.assertEqual(r['summary']['total_reserves'], 1800.0)
        self.assertEqual(r['summary']['total_payments'], 650.0)
        # 1800/1500 = 120% reserves; 650/1500 = 43.33% payments
        self.assertEqual(r['summary']['overall_loss_ratio_on_reserves_pct'], 120.0)
        self.assertEqual(r['summary']['overall_loss_ratio_on_payments_pct'], 43.33)
        by = {x['policy_number']: x for x in r['rows']}
        self.assertEqual(by['POL1']['loss_ratio_on_reserves_pct'], 70.0)
        self.assertEqual(by['POL1']['loss_ratio_on_payments_pct'], 30.0)
        self.assertEqual(by['POL2']['loss_ratio_on_reserves_pct'], 180.0)
        self.assertEqual(by['POL2']['loss_ratio_on_payments_pct'], 50.0)
        self.assertIsNone(by['POL3']['loss_ratio_on_reserves_pct'])   # no premium
        self.assertIsNone(by['POL3']['loss_ratio_on_payments_pct'])

    def test_filter_by_policy(self):
        r = self._build(policy_number='POL1')
        self.assertEqual([x['policy_number'] for x in r['rows']], ['POL1'])

    def test_filter_by_customer(self):
        r = self._build(customer='acme')
        self.assertEqual({x['policy_number'] for x in r['rows']}, {'POL1', 'POL2'})

    def test_claims_pending_status_default(self):
        # No injected reserves/payments → real provider → pending, both 0.
        r = build_client_loss_ratio(_premium_map=self.PREM, _customer_map=self.CUST)
        self.assertEqual(r['claims_status'], 'claims_feed_pending')
        self.assertEqual(r['summary']['total_reserves'], 0.0)
        self.assertEqual(r['summary']['total_payments'], 0.0)


class LargeLossClientsTests(TestCase):
    PREM = {'P1': Decimal('1000'), 'P2': Decimal('1000'), 'P3': Decimal('100')}
    RES  = {'P1': Decimal('1200'), 'P2': Decimal('300'),  'P3': Decimal('90')}
    PAY  = {'P1': Decimal('400'),  'P2': Decimal('300'),  'P3': Decimal('95')}
    CUST = {'P1': 'Risky Ltd', 'P2': 'Good Co', 'P3': 'Tiny Co'}

    def _build(self, **kw):
        return build_large_loss_clients(
            _premium_map=self.PREM, _reserves_map=self.RES, _payments_map=self.PAY,
            _claims_status='ok', _customer_map=self.CUST, **kw)

    def test_threshold_filters_and_sorts(self):
        r = self._build(threshold_pct=70)
        names = [x['customer_name'] for x in r['rows']]
        # Risky Ltd worst=120% (reserves), Tiny Co worst=95% (payments) qualify;
        # Good Co worst=30% excluded. Sorted worst-first.
        self.assertEqual(names, ['Risky Ltd', 'Tiny Co'])

    def test_min_premium_excludes_small(self):
        r = self._build(threshold_pct=70, min_premium=500)
        self.assertEqual([x['customer_name'] for x in r['rows']], ['Risky Ltd'])

    def test_aggregates_per_customer(self):
        prem = {'A1': Decimal('500'), 'A2': Decimal('500')}
        res  = {'A1': Decimal('400'), 'A2': Decimal('600')}
        pay  = {'A1': Decimal('400'), 'A2': Decimal('600')}
        cust = {'A1': 'Acme', 'A2': 'Acme'}
        r = build_large_loss_clients(_premium_map=prem, _reserves_map=res, _payments_map=pay,
                                     _claims_status='ok', _customer_map=cust,
                                     threshold_pct=70)
        self.assertEqual(len(r['rows']), 1)
        self.assertEqual(r['rows'][0]['policy_count'], 2)
        self.assertEqual(r['rows'][0]['loss_ratio_on_reserves_pct'], 100.0)  # 1000/1000
        self.assertEqual(r['rows'][0]['loss_ratio_on_payments_pct'], 100.0)


class PremiumProviderTests(TestCase):
    """The live premium provider reads the Graphite payment feed: success-only,
    net of refunds/reverses."""

    def setUp(self):
        from integrations.models import GraphitePaymentTransaction as GPT
        mk = lambda **k: GPT.objects.create(**k)
        # POLX: 1000 success + 200 refund → net 800
        mk(graphite_id=1, policy_number='POLX', amount=Decimal('1000'),
           status_norm='success', is_refund=False)
        mk(graphite_id=2, policy_number='POLX', amount=Decimal('200'),
           status_norm='success', is_refund=True)
        # POLX: a failed payment must NOT count
        mk(graphite_id=3, policy_number='POLX', amount=Decimal('500'),
           status_norm='failed', is_refund=False)
        # POLY: 300 success
        mk(graphite_id=4, policy_number='POLY', amount=Decimal('300'),
           status_norm='success', is_refund=False)

    def test_premium_net_of_refunds_success_only(self):
        prem = premium_by_policy(None, None)
        self.assertEqual(prem['POLX'], Decimal('800'))
        self.assertEqual(prem['POLY'], Decimal('300'))
