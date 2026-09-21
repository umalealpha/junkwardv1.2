"""Tests for premium lapse early-warning (feature #6)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from reporting.premium_lapse import compute_lapse_risk


class PremiumLapseTests(TestCase):
    def setUp(self):
        from integrations.models import GraphitePaymentTransaction as G
        self.G = G
        self._gid = 1
        now = timezone.now()
        self.t = lambda days_ago: now - timedelta(days=days_ago)

    def _pay(self, policy, status, when, amount='500.00', reverse=False, refund=False, product='Motor'):
        gid = self._gid
        self._gid += 1
        return self.G.objects.create(
            graphite_id=gid, policy_number=policy, amount=Decimal(amount),
            status_norm=status, is_reverse=reverse, is_refund=refund,
            product_name=product, source_recorded_at=when,
        )

    def test_tiers_and_at_risk_total(self):
        # P1: success(old) then two recent failures -> tier2, est = last success 500
        self._pay('P1', 'success', self.t(90), '500.00')
        self._pay('P1', 'failed', self.t(40))
        self._pay('P1', 'failed', self.t(10))
        # P2: failure then a recent success -> NOT at risk
        self._pay('P2', 'failed', self.t(40))
        self._pay('P2', 'success', self.t(10), '800.00')
        # P3: one recent failure after a success -> tier1
        self._pay('P3', 'success', self.t(60), '300.00')
        self._pay('P3', 'failed', self.t(5))

        r = compute_lapse_risk(months=6)
        self.assertEqual(r['counts'], {'tier1': 1, 'tier2': 1, 'tier3': 0})
        self.assertEqual(r['at_risk_policies'], 2)          # P1 + P3 (P2 excluded)
        policies = {row['policy_number'] for row in r['rows']}
        self.assertEqual(policies, {'P1', 'P3'})
        self.assertNotIn('P2', policies)
        # at-risk headline = tier2/3 only = P1's last successful amount
        self.assertEqual(r['at_risk_monthly_bwp'], '500.00')

    def test_refunds_ignored_and_blank_policy_skipped(self):
        self._pay('', 'failed', self.t(5))                  # blank policy — skipped
        self._pay('P9', 'failed', self.t(5), refund=True)   # refund — excluded
        r = compute_lapse_risk(months=6)
        self.assertEqual(r['at_risk_policies'], 0)
        self.assertEqual(r['rows'], [])

    def test_outside_window_ignored(self):
        self._pay('POLD', 'success', self.t(400), '900.00')
        self._pay('POLD', 'failed', self.t(300))
        r = compute_lapse_risk(months=6)                    # 6mo window excludes 300d-old
        self.assertEqual(r['at_risk_policies'], 0)
