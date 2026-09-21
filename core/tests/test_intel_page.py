"""
Tests for the Intelligence Summary page's backend (core/intel_summary.py's
comparison layer + core/intel_api.IntelSummaryStaffView), and for the ADIC
scoping of the machine feed.

CFO decisions this pins (2026-07-26):
  * "Brain sees ADIC" — the caller cannot widen the scope, not even by omitting
    the company parameter to get the all-company rollup.
  * "I want to see 62,347 only" — the headline policy number is OUR active book;
    Alpha Brain's inflated figure appears only in the reconciliation strip, on
    its own basis.
  * May/June 2026 are coming in as opening balances at 1 July 2026, so a month
    with no posted revenue must read "not posted", never zero.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.intel_summary import build_comparison, build_summary, feed_company_code, month_is_posted
from core.tests.test_intel_summary import TOKEN, _FORBIDDEN, _PL, _mock_census, make_adic

# What Alpha Brain reported to the CFO on 2026-07-24.
_BRAIN = {
    'activated': True, 'as_of': '2026-07-25', 'note': '',
    'ai_narrative': 'Nothing unusual.',
    'kyc': {'by_category': {'MIS': 133293, 'DOM': 3105, 'COM': 3116}},
}


class FeedScopeTests(TestCase):
    @override_settings(INTEL_SUMMARY_COMPANY='ADIC')
    def test_pin_overrides_whatever_the_caller_asks_for(self):
        self.assertEqual(feed_company_code('ADIH'), 'ADIC')
        self.assertEqual(feed_company_code(None), 'ADIC')   # no param must NOT mean rollup
        self.assertEqual(feed_company_code('ADIC'), 'ADIC')

    @override_settings(INTEL_SUMMARY_COMPANY='ALL')
    def test_all_deliberately_allows_the_rollup(self):
        self.assertIsNone(feed_company_code(None))
        self.assertEqual(feed_company_code('ADIC'), 'ADIC')

    @override_settings(INTEL_SUMMARY_COMPANY='')
    def test_unset_leaves_the_caller_in_charge(self):
        self.assertIsNone(feed_company_code(None))

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN, INTEL_SUMMARY_COMPANY='ADIC')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_asking_for_another_entity_serves_adic_anyway(self, _sel, _pl):
        make_adic()
        r = self.client.get('/api/v1/intel/summary/?company=ADIH',
                            HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 200, r.content[:200])
        # ADIH was never what got served — the pin held.
        self.assertEqual(r.json()['company'], 'ADIC')

    @override_settings(INTEL_SUMMARY_TOKEN=TOKEN, INTEL_SUMMARY_COMPANY='ADIC')
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_omitting_the_parameter_does_not_give_the_rollup(self, _sel, _pl):
        make_adic()
        r = self.client.get('/api/v1/intel/summary/', HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.json()['company'], 'ADIC')      # not 'ALL'


class MonthPostedTests(TestCase):
    def test_a_real_month_is_posted(self):
        self.assertTrue(month_is_posted({'month': {'gwp': '8500000.00'}}))

    def test_zero_and_rounding_artefacts_are_not_posted(self):
        # The actual ADIC prod values for May, June and July 2026.
        for g in ('0.00', '-1.00', '-249.00', '999.99'):
            self.assertFalse(month_is_posted({'month': {'gwp': g}}), g)

    def test_missing_block_is_not_posted(self):
        self.assertFalse(month_is_posted(None))
        self.assertFalse(month_is_posted({}))

    @patch('core.aria.tools.get_pl_summary', return_value={**_PL, 'gwp': '-1.00'})
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_summary_flags_an_unposted_month(self, _sel, _pl):
        self.assertFalse(build_summary(month='2026-06')['month_posted'])

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_summary_flags_a_posted_month(self, _sel, _pl):
        self.assertTrue(build_summary(month='2026-04')['month_posted'])


class ComparisonTests(TestCase):
    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_headline_stays_our_active_book(self, _s, _p, _b):
        out = build_comparison(month='2026-06')
        self.assertEqual(out['policies']['active_total'], 62347)

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_the_two_are_compared_like_for_like(self, _s, _p, _b):
        out = build_comparison(month='2026-06')
        self.assertEqual(out['brain']['our_same_basis_total'], 62347 + 74773)
        self.assertEqual(out['brain']['their_active_total'], 133293 + 3105 + 3116)
        self.assertEqual(out['brain']['agree'], 'amber')

    @patch('core.compliance_brain.latest_summary',
           return_value={**_BRAIN, 'kyc': {'by_category': {'MIS': 137120}}})
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_matching_totals_show_green(self, _s, _p, _b):
        self.assertEqual(build_comparison(month='2026-06')['brain']['agree'], 'green')

    @patch('core.compliance_brain.latest_summary',
           return_value={'activated': False, 'note': 'Awaiting activation', 'kyc': {}})
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_brain_silent_is_red_and_our_side_still_works(self, _s, _p, _b):
        out = build_comparison(month='2026-06')
        self.assertEqual(out['brain']['agree'], 'red')
        self.assertFalse(out['brain']['activated'])
        self.assertEqual(out['policies']['active_total'], 62347)

    @patch('core.compliance_brain.latest_summary', side_effect=RuntimeError('brain down'))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_brain_raising_does_not_break_the_page(self, _s, _p, _b):
        out = build_comparison(month='2026-06')
        self.assertEqual(out['brain']['agree'], 'red')
        self.assertEqual(out['premium']['month']['gwp'], '8500000.00')


class StaffEndpointTests(TestCase):
    url = '/api/v1/intel/staff-summary/'

    def setUp(self):
        make_adic()      # the page defaults to ADIC; prod has this row

    def _login(self, name):
        # No password argument — force_login does not need one, and a literal
        # here would read as a credential to the secret scanners.
        u = get_user_model().objects.create_user(
            username=name, email=f'{name}@alphadirect.co.bw')
        self.client.force_login(u)
        return u

    def test_route_is_registered(self):
        self.assertEqual(reverse('v1-intel-staff-summary'), self.url)

    def test_anonymous_is_refused(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_signed_in_staff_get_both_sides(self, _s, _p, _b):
        self._login('intelstaff')
        r = self.client.get(self.url + '?month=2026-06')
        self.assertEqual(r.status_code, 200, r.content[:200])
        body = r.json()
        self.assertEqual(body['policies']['active_total'], 62347)
        self.assertIn('brain', body)
        self.assertIn('month_posted', body)

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_no_shared_token_can_reach_the_staff_view(self, _s, _p, _b):
        # The staff surface is login-only; a machine token must not open it.
        r = self.client.get(self.url, HTTP_AUTHORIZATION=f'Bearer {TOKEN}')
        self.assertIn(r.status_code, (401, 403))

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_staff_payload_carries_no_customer_level_field(self, _s, _p, _b):
        self._login('intelstaff2')
        r = self.client.get(self.url + '?month=2026-06')
        hit = _FORBIDDEN.search(json.dumps(r.json()))
        self.assertIsNone(hit, f'customer-level field leaked onto the page: {hit}')

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_bad_month_is_400_for_staff_too(self, _s, _p, _b):
        self._login('intelstaff3')
        self.assertEqual(self.client.get(self.url + '?month=2026-13').status_code, 400)

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_company_uuid_resolves_like_the_code(self, _s, _p, _b):
        # The TopBar switcher / apiFetch auto-injects the selected company as its
        # UUID id (?company=<uuid>), NOT its code. Prod threw "Unknown company
        # code: <uuid>" because the endpoint only matched on code. It must accept
        # the id too and resolve to the same entity.
        from core.models import Company
        adic = Company.objects.get(code='ADIC')
        self._login('intelstaffuuid')
        r = self.client.get(f'{self.url}?company={adic.id}&month=2026-06')
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.json()['company'], 'ADIC')


@override_settings(INTEL_SUMMARY_COMPANY='ADIC')
class StaffEntityIsolationTests(TestCase):
    """DeepSeek 2026-07-26 raised this as CRITICAL and it was real: an ordinary
    employee could name any entity, or omit the parameter, and get that entity's
    profit or the group rollup. The CFO opened the PAGE to all staff; he did not
    open every subsidiary. Default entity for everyone, anything else needs the
    company access the rest of omni already checks."""

    url = '/api/v1/intel/staff-summary/'

    def setUp(self):
        make_adic()
        self.plain = get_user_model().objects.create_user(
            username='plainstaff', email='plainstaff@alphadirect.co.bw')

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_ordinary_employee_gets_the_default_entity(self, _s, _p, _b):
        self.client.force_login(self.plain)
        r = self.client.get(self.url + '?month=2026-06')
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.json()['company'], 'ADIC')

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_ordinary_employee_cannot_pull_another_entity(self, _s, _p, _b):
        from core.models import Company, Currency
        cur = Currency.objects.get(code='BWP')
        Company.objects.create(code='ADIH', name='Alpha Direct Holdings', base_currency=cur)
        self.client.force_login(self.plain)
        r = self.client.get(self.url + '?company=ADIH&month=2026-06')
        self.assertEqual(r.status_code, 403, r.content[:200])

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_granted_access_lets_them_through(self, _s, _p, _b):
        from core.models import Company, Currency, UserCompanyAccess
        cur = Currency.objects.get(code='BWP')
        adih = Company.objects.create(code='ADIH', name='Alpha Direct Holdings',
                                      base_currency=cur)
        UserCompanyAccess.objects.create(user=self.plain, company=adih, can_view=True)
        self.client.force_login(self.plain)
        r = self.client.get(self.url + '?company=ADIH&month=2026-06')
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.json()['company'], 'ADIH')

    @override_settings(INTEL_SUMMARY_COMPANY='ALL')
    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_group_rollup_is_never_an_implicit_default(self, _s, _p, _b):
        self.client.force_login(self.plain)
        r = self.client.get(self.url + '?month=2026-06')
        self.assertEqual(r.status_code, 403, r.content[:200])

    @patch('core.compliance_brain.latest_summary', return_value=dict(_BRAIN))
    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_superuser_is_unrestricted(self, _s, _p, _b):
        su = get_user_model().objects.create_superuser(
            username='intelsu', email='intelsu@alphadirect.co.bw')
        self.client.force_login(su)
        r = self.client.get(self.url + '?month=2026-06')
        self.assertEqual(r.status_code, 200, r.content[:200])


class BrainPayloadShapeTests(TestCase):
    """Fable 2026-07-26: the brain is Node and nothing in Omni reads inside its
    `kyc` block, so the exact key is not guaranteed. A naming difference must not
    park the traffic light on red for ever with no symptom."""

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_camel_case_breakdown_is_understood(self, _sel, _pl):
        camel = {**_BRAIN, 'kyc': {'byCategory': {'MIS': 133293, 'DOM': 3105, 'COM': 3116}}}
        with patch('core.compliance_brain.latest_summary', return_value=camel):
            out = build_comparison(month='2026-06')
        self.assertEqual(out['brain']['their_active_total'], 139514)

    @patch('core.aria.tools.get_pl_summary', return_value=dict(_PL))
    @patch('aware.engine.run_select', side_effect=_mock_census)
    def test_reported_but_uncountable_says_so_instead_of_silent_red(self, _sel, _pl):
        odd = {**_BRAIN, 'kyc': {'somethingElse': {'MIS': 1}}}
        with patch('core.compliance_brain.latest_summary', return_value=odd):
            out = build_comparison(month='2026-06')
        self.assertEqual(out['brain']['agree'], 'red')
        self.assertTrue(out['brain']['activated'])
        # The red must diagnose itself rather than look like "not reported yet".
        self.assertIn('no policy-count breakdown', out['brain']['note'])
