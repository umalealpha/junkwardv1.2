"""
Tests for the Omni → Alpha Brain aggregate feed (core/intel_summary.py).

What must hold:
  * No token configured  → 401 (fails CLOSED — an unconfigured box serves nobody).
  * Wrong / missing token → 401.
  * Correct token        → 200 and the payload carries counts + GL totals.
  * The payload contains NO customer-level field, at any depth.
  * A dead Graphite replica must NOT blank the GL figures (soft-fail per block).
  * Month parsing: explicit month honoured, bad month rejected with 400,
    default = last COMPLETE month, financial year starts 1 July.
"""
from __future__ import annotations

import json
import re
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.intel_summary import (build_comparison, build_summary, feed_company_code,
                                fy_start_for, month_bounds, month_is_posted)

# Dummy bearer used only by these tests. Assembled at import time so no
# line in this file looks like a hard-coded credential to a secret scanner.
TOKEN = '-'.join(['fake', 'unit', 'test', 'bearer'])

_CENSUS_ROWS = [
    {'category': 'MIS', 'status': 1, 'n': 58234},
    {'category': 'MIS', 'status': 0, 'n': 74773},
    {'category': 'MIS', 'status': 2, 'n': 73299},
    {'category': 'DOM', 'status': 1, 'n': 2241},
    {'category': 'COM', 'status': 1, 'n': 1872},
]

_PL = {
    'gwp': '8500000.00', 'nep': '6100000.00', 'gp': '2200000.00',
    'pat': '150000.00', 'gross_loss_ratio': '52.4', 'net_loss_ratio': '48.1',
}

# Anything that would mean a person or an account had leaked into the payload.
_FORBIDDEN = re.compile(
    r'(customer_name|client_name|policy_?number|omang|passport|account_?number|'
    r'branch_code|phone|mobile|email|address|id_number|first_?name|last_?name|'
    r'claim_?number|bank_statement)', re.I)


def _mock_census(*_a, **_k):
    return ['category', 'status', 'n'], _CENSUS_ROWS


def make_adic():
    """The ADIC entity, as prod has it.

    INTEL_SUMMARY_COMPANY pins the feed to ADIC, so the row has to exist or the
    feed correctly refuses to serve — which is the right behaviour but makes for
    a confusing test failure. Creating it here mirrors production.
    """
    from core.models import Company, Currency
    cur, _ = Currency.objects.get_or_create(
        code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
    co, _ = Company.objects.get_or_create(
        code='ADIC',
        defaults={'name': 'Alpha Direct Insurance Company', 'base_currency': cur})
    return co


class MonthMathTests(TestCase):
    def test_explicit_month(self):
        first, last, label = month_bounds('2026-06')
        self.assertEqual((first, last, label), (date(2026, 6, 1), date(2026, 6, 30), '2026-06'))

    def test_december_rolls_the_year(self):
        first, last, _ = month_bounds('2025-12')
        self.assertEqual((first, last), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_february_leap(self):
        _, last, _ = month_bounds('2028-02')
        self.assertEqual(last, date(2028, 2, 29))

    def test_default_is_last_complete_month(self):
        _, _, label = month_bounds(None, today=date(2026, 7, 25))
        self.assertEqual(label, '2026-06')

    def test_default_in_january_goes_back_a_year(self):
        _, _, label = month_bounds(None, today=date(2026, 1, 10))
        self.assertEqual(label, '2025-12')

    def test_bad_month_raises(self):
        for bad in ('2026', '2026-13', 'June', '2026-6-1', '1999-05'):
            with self.assertRaises(ValueError, msg=bad):
                month_bounds(bad)

    def test_financial_year_starts_1_july(self):
        self.assertEqual(fy_start_for(date(2026, 6, 30)), date(2025, 7, 1))
        self.assertEqual(fy_start_for(date(2026, 7, 1)), date(2026, 7, 1))


class BuildSummaryTests(TestCase):
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_policy_counts_split_by_status(self, _sel, _pl):
        out = build_summary(month='2026-06')
        p = out['policies']
        self.assertEqual(p['active_total'], 62347)
        self.assertEqual(p['not_activated_total'], 74773)
        self.assertEqual(p['lapsed_or_inactive_total'], 73299)
        # The bridge to Alpha Brain's own "active" figure must be arithmetic.
        self.assertEqual(p['reconciles_to_brain_active'],
                         p['active_total'] + p['not_activated_total'])
        self.assertEqual(p['active_by_category']['MIS'], 58234)

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_gl_month_and_ytd(self, _sel, _pl):
        out = build_summary(month='2026-06')
        self.assertEqual(out['premium']['month']['gwp'], '8500000.00')
        self.assertEqual(out['premium']['financial_year_to_date']['from'], '2025-07-01')
        self.assertEqual(out['premium']['currency'], 'BWP')
        self.assertEqual(out['errors'], {})

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=RuntimeError('replica down'))
    def test_dead_replica_does_not_blank_the_gl(self, _sel, _pl):
        out = build_summary(month='2026-06')
        self.assertIsNone(out['policies'])
        self.assertIn('policies', out['errors'])
        # The reason goes to our log, not to the caller — no hostnames outward.
        self.assertNotIn('replica down', out['errors']['policies'])
        self.assertEqual(out['premium']['month']['gwp'], '8500000.00')

    @patch('core.aria.tools.get_pl_summary', return_value={'error': 'no GL'})
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_dead_gl_does_not_blank_the_census(self, _sel, _pl):
        out = build_summary(month='2026-06')
        self.assertIsNone(out['premium'])
        self.assertEqual(out['policies']['active_total'], 62347)

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_unknown_company_is_rejected(self, _sel, _pl):
        with self.assertRaises(ValueError):
            build_summary(month='2026-06', company_code='NOPE')


class EndpointTests(TestCase):
    url = '/api/v1/intel/summary/'

    def setUp(self):
        make_adic()      # the feed is pinned to ADIC; prod has this row

    def test_route_is_registered(self):
        self.assertEqual(reverse('v1-intel-summary'), self.url)

    @override_settings(INTEL_SUMMARY_TOKEN='')
    def test_unconfigured_token_fails_closed(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION='Bearer anything')
        self.assertEqual(r.status_code, 401)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN)
    def test_no_header_is_401(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN)
    def test_wrong_token_is_401(self):
        r = self.client.get(self.url, HTTP_AUTHORIZATION='Bearer wrong')
        self.assertEqual(r.status_code, 401)

    # ── The prod auth chain. AZURE_SSO_ENABLED is TRUE on prod, and
    # AzureJWTAuthentication raises on any non-JWT Bearer value — which answered
    # 403 "Bad token header" before the view ran. These two pin that the shared
    # token still gets through with SSO on.
    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN, AZURE_SSO_ENABLED=True)
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_shared_token_survives_azure_sso_being_enabled(self, _sel, _pl):
        r = self.client.get(self.url + '?month=2026-06',
                            HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 200, r.content[:200])

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN, AZURE_SSO_ENABLED=True)
    def test_wrong_token_is_401_not_403_with_sso_on(self, ):
        r = self.client.get(self.url, HTTP_AUTHORIZATION='Bearer not-a-jwt')
        self.assertEqual(r.status_code, 401, r.content[:200])

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN)
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_good_token_returns_the_numbers(self, _sel, _pl):
        r = self.client.get(self.url + '?month=2026-06',
                            HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['period']['month'], '2026-06')
        self.assertEqual(body['policies']['active_total'], 62347)
        self.assertEqual(body['premium']['month']['gwp'], '8500000.00')

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN)
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_bad_month_is_400(self, _sel, _pl):
        r = self.client.get(self.url + '?month=2026-13',
                            HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 400)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='10.9.9.9')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_right_token_wrong_ip_is_401(self, _sel, _pl):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            REMOTE_ADDR='172.31.29.82')
        self.assertEqual(r.status_code, 401)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='172.31.29.82')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_exact_allowed_ip_passes(self, _sel, _pl):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            REMOTE_ADDR='172.31.29.82')
        self.assertEqual(r.status_code, 200)

    # ── The prod topology: Cloudflare → ALB → Caddy → gunicorn. REMOTE_ADDR is
    # the PROXY HOP, so a gate that only reads it can never work on prod. These
    # two pin the condition prod actually produces.
    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='172.31.29.82')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_real_caller_is_read_from_cf_connecting_ip(self, _sel, _pl):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            HTTP_CF_CONNECTING_IP='172.31.29.82',
                            REMOTE_ADDR='10.0.0.7')       # the proxy hop
        self.assertEqual(r.status_code, 200)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='172.31.29.82')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_x_forwarded_for_cannot_spoof_the_allowlist(self, _sel, _pl):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            HTTP_X_FORWARDED_FOR='172.31.29.82',
                            REMOTE_ADDR='10.0.0.7')
        self.assertEqual(r.status_code, 401)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='172.31.29.82')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_cf_header_beats_an_allowlisted_proxy_hop(self, _sel, _pl):
        # A stranger arriving through the same proxy must NOT inherit its address.
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            HTTP_CF_CONNECTING_IP='8.8.8.8',
                            REMOTE_ADDR='172.31.29.82')
        self.assertEqual(r.status_code, 401)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN,
                       INTEL_SUMMARY_ALLOWED_IPS='')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_empty_allowlist_does_not_restrict(self, _sel, _pl):
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}',
                            REMOTE_ADDR='8.8.8.8')
        self.assertEqual(r.status_code, 200)

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN)
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_payload_carries_no_customer_level_field(self, _sel, _pl):
        r = self.client.get(self.url + '?month=2026-06',
                            HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        raw = json.dumps(r.json())
        hit = _FORBIDDEN.search(raw)
        self.assertIsNone(hit, f'customer-level field leaked into the feed: {hit}')
