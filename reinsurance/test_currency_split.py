"""Reinsurance money is never added across currencies.

Reinsurance control QC, 16-Sep-2026, P0: "Wrong financial reporting risk: BWP
and USD are added together and displayed as one BWP total." FacRiskExposure
carries currency_code per row, and both the control centre and the FAC risk
register were running a single Sum() over the lot — so pula and dollars were
added and the screen wrote BWP in front of the answer.

The fix reports every amount per currency and emits a single headline figure
ONLY when there is genuinely one currency to emit; with two it is null, so a
client that has not been taught about by_currency renders a dash instead of a
wrong number.

Each test covers something that would actually hurt if it broke:
  * with one currency the figures read exactly as they did before, so this is
    not a regression dressed up as a fix;
  * with two currencies NO returned value equals the naive cross-currency
    sum — asserted against the sum explicitly, because "the key is null" alone
    would still pass if some other key carried the bad total;
  * the per-currency amounts are right, not merely separated;
  * a cancelled risk is still excluded, which is what stops a dead placement
    inflating exposure.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from reinsurance.models import (
    FacReinsurerAllocation, FacRiskExposure, Reinsurer,
)

# Route names v1-reinsurance-control-centre / v1-reinsurance-fac-risk, read off
# alpha_finance/api_router.py. The control centre answers on .../controls/,
# NOT .../control-centre/ — a guessed path would 404 and the test would pass
# for the wrong reason.
CONTROL_CENTRE = '/api/v1/reinsurance/controls/'
FAC_REGISTER   = '/api/v1/reinsurance/fac-risk/'
SECURITY_PANEL = '/api/v1/reinsurance/security-panel/'


class CurrencyIsNeverSummedTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        # A superuser clears _require(user, 're.view'); the point of these
        # tests is the arithmetic, not the gate.
        cls.user = User.objects.create_superuser('re', 're@alphadirect.co.bw', 'x')
        # APPROVED on purpose: FacReinsurerAllocation.save() calls
        # _assert_placeable(), which refuses an allocation to a DRAFT
        # counterparty. That gate is correct and is not what these tests are
        # about, so the fixture satisfies it instead of working round it.
        # The KYC evidence register (16-Sep-2026) added a second precondition
        # to the same gate: approved is no longer enough on its own. Same
        # reasoning as the comment above — satisfy it, do not work round it.
        from reinsurance.test_kyc_documents import complete_kyc
        cls.munich = complete_kyc(Reinsurer.objects.create(
            name='Munich Re', short_code='MUN',
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            effective_date=timezone.localdate() - timedelta(days=365),
            expiry_date=timezone.localdate() + timedelta(days=365),
        ))

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.today = timezone.localdate()

    def exposure(self, reference, currency, placed, sum_insured, retained,
                 *, active=True, status=None):
        """One FAC risk. Active rows sit inside their cover period; expired
        rows ended yesterday.

        status defaults to PLACED, not the model default: is_active() returns
        False for a DRAFT row whatever its dates say, so a fixture that leaves
        status alone counts as nothing and every amount assertion below would
        read 0.00 and prove nothing.
        """
        if active:
            eff, exp = self.today - timedelta(days=30), self.today + timedelta(days=30)
        else:
            eff, exp = self.today - timedelta(days=60), self.today - timedelta(days=1)
        return FacRiskExposure.objects.create(
            reference=reference,
            currency_code=currency,
            gross_sum_insured=Decimal(sum_insured),
            fac_placed_amount=Decimal(placed),
            unplaced_retained_amount=Decimal(retained),
            effective_date=eff,
            expiry_date=exp,
            status=status or FacRiskExposure.Status.PLACED,
        )

    def centre(self):
        r = self.client.get(CONTROL_CENTRE)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data['fac_exposure']

    def register(self):
        r = self.client.get(FAC_REGISTER)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    # ── one currency: unchanged behaviour ─────────────────────────────────
    def test_a_single_currency_still_reports_one_plain_total(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        self.exposure('FAC-BWP-2', 'BWP', '200000.00', '900000.00', '15000.00')

        f = self.centre()
        self.assertFalse(f['mixed_currency'])
        self.assertEqual(f['currencies'], ['BWP'])
        self.assertEqual(Decimal(f['active_placed']), Decimal('300000.00'))
        self.assertEqual(Decimal(f['active_sum_insured']), Decimal('1400000.00'))
        self.assertEqual(Decimal(f['retained_unplaced']), Decimal('40000.00'))
        self.assertEqual(f['active_count'], 2)

    # ── two currencies: never a cross-currency sum ────────────────────────
    def test_two_currencies_never_produce_a_single_added_up_figure(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        self.exposure('FAC-USD-1', 'USD',  '40000.00', '200000.00', '10000.00')

        f = self.centre()
        self.assertTrue(f['mixed_currency'])
        self.assertEqual(f['currencies'], ['BWP', 'USD'])
        for key in ('active_placed', 'active_sum_insured',
                    'retained_unplaced', 'expired_placed'):
            self.assertIsNone(f[key], f'{key} must be null when currencies are mixed')

        # The real guard: the naive sum must appear NOWHERE in the payload.
        # Asserting only "the key is null" would still pass if some other key
        # quietly carried 140000 — which is the exact defect being fixed.
        naive = {'140000.00', '140000', '700000.00', '700000',
                 '35000.00', '35000'}
        flat = str(f)
        for bad in naive:
            self.assertNotIn(bad, flat,
                             f'a cross-currency total ({bad}) reached the payload')

    def test_each_currency_carries_its_own_correct_amounts(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        self.exposure('FAC-BWP-2', 'BWP', '200000.00', '900000.00', '15000.00')
        self.exposure('FAC-USD-1', 'USD',  '40000.00', '200000.00', '10000.00')

        by = {row['currency']: row for row in self.centre()['by_currency']}
        self.assertEqual(set(by), {'BWP', 'USD'})
        self.assertEqual(Decimal(by['BWP']['active_placed']), Decimal('300000.00'))
        self.assertEqual(Decimal(by['BWP']['active_sum_insured']), Decimal('1400000.00'))
        self.assertEqual(Decimal(by['BWP']['retained_unplaced']), Decimal('40000.00'))
        self.assertEqual(by['BWP']['active_count'], 2)
        self.assertEqual(Decimal(by['USD']['active_placed']), Decimal('40000.00'))
        self.assertEqual(by['USD']['active_count'], 1)

    def test_an_expired_risk_is_counted_separately_and_per_currency(self):
        self.exposure('FAC-BWP-OLD', 'BWP', '70000.00', '300000.00', '0.00',
                      active=False)
        self.exposure('FAC-USD-OLD', 'USD', '20000.00', '100000.00', '0.00',
                      active=False)

        f = self.centre()
        self.assertEqual(f['expired_count'], 2)
        self.assertIsNone(f['expired_placed'])
        by = {row['currency']: row for row in f['by_currency']}
        self.assertEqual(Decimal(by['BWP']['expired_placed']), Decimal('70000.00'))
        self.assertEqual(Decimal(by['USD']['expired_placed']), Decimal('20000.00'))

    def test_a_cancelled_risk_is_left_out_of_the_figures_entirely(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        self.exposure('FAC-BWP-DEAD', 'BWP', '999999.00', '999999.00', '999999.00',
                      status=FacRiskExposure.Status.CANCELLED)

        f = self.centre()
        self.assertEqual(f['active_count'], 1)
        self.assertEqual(Decimal(f['active_placed']), Decimal('100000.00'))
        self.assertNotIn('999999', str(f))

    # ── the register, and the by-reinsurer table ──────────────────────────
    def test_a_reinsurer_on_two_currencies_shows_both_and_totals_neither(self):
        bwp = self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        usd = self.exposure('FAC-USD-1', 'USD',  '40000.00', '200000.00', '10000.00')
        FacReinsurerAllocation.objects.create(
            exposure=bwp, reinsurer=self.munich,
            share_percent=Decimal('50.00'), allocated_amount=Decimal('50000.00'))
        FacReinsurerAllocation.objects.create(
            exposure=usd, reinsurer=self.munich,
            share_percent=Decimal('25.00'), allocated_amount=Decimal('10000.00'))

        row = self.register()['by_reinsurer'][0]
        self.assertEqual(row['short_code'], 'MUN')
        self.assertTrue(row['mixed_currency'])
        self.assertEqual(row['currencies'], ['BWP', 'USD'])
        self.assertIsNone(row['active_amount'])
        self.assertIsNone(row['expired_amount'])
        by = {c['currency']: c for c in row['by_currency']}
        self.assertEqual(Decimal(by['BWP']['active_amount']), Decimal('50000.00'))
        self.assertEqual(Decimal(by['USD']['active_amount']), Decimal('10000.00'))
        # 50000 + 10000 = 60000 must not appear anywhere on the row.
        self.assertNotIn('60000', str(row))

    def test_a_reinsurer_on_one_currency_keeps_its_plain_amount(self):
        bwp = self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        FacReinsurerAllocation.objects.create(
            exposure=bwp, reinsurer=self.munich,
            share_percent=Decimal('50.00'), allocated_amount=Decimal('50000.00'))

        row = self.register()['by_reinsurer'][0]
        self.assertFalse(row['mixed_currency'])
        self.assertEqual(Decimal(row['active_amount']), Decimal('50000.00'))

    def test_retained_unplaced_is_split_per_currency_and_never_added(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        self.exposure('FAC-USD-1', 'USD',  '40000.00', '200000.00', '10000.00')

        data = self.register()
        rows = {r['currency']: r['amount'] for r in data['retained_unplaced_by_currency']}
        self.assertEqual(set(rows), {'BWP', 'USD'})
        self.assertEqual(Decimal(rows['BWP']), Decimal('25000.00'))
        self.assertEqual(Decimal(rows['USD']), Decimal('10000.00'))
        self.assertIsNone(data['retained_unplaced'])
        self.assertNotIn('35000', str(data['retained_unplaced_by_currency']))

    def test_retained_unplaced_stays_a_plain_figure_in_one_currency(self):
        self.exposure('FAC-BWP-1', 'BWP', '100000.00', '500000.00', '25000.00')
        data = self.register()
        self.assertEqual(Decimal(data['retained_unplaced']), Decimal('25000.00'))

    # ── paging (the 500-row display cap had no way past it) ───────────────
    def test_the_register_pages_and_reports_the_whole_matched_count(self):
        for n in range(5):
            self.exposure(f'FAC-P-{n}', 'BWP', '1000.00', '5000.00', '0.00')

        r = self.client.get(f'{FAC_REGISTER}?page_size=2')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 5)          # the whole matched set
        self.assertEqual(r.data['shown'], 2)          # this page
        self.assertEqual(r.data['page'], 1)
        self.assertTrue(r.data['has_next'])

        last = self.client.get(f'{FAC_REGISTER}?page_size=2&page=3')
        self.assertEqual(last.data['shown'], 1)
        self.assertFalse(last.data['has_next'])

    def test_page_size_cannot_be_pushed_past_the_cap(self):
        self.exposure('FAC-BWP-1', 'BWP', '1000.00', '5000.00', '0.00')
        r = self.client.get(f'{FAC_REGISTER}?page_size=999999')
        self.assertEqual(r.data['page_size'], 500)

    # ── the security panel — missed by the 16-Sep currency fix ────────────
    # controls_api.security_panel() summed FacReinsurerAllocation.allocated_amount
    # per reinsurer with one Sum() over every allocation the reinsurer holds.
    # The allocation has no currency of its own — it belongs to its exposure's
    # currency_code — so a reinsurer on both a BWP and a USD slip had pula and
    # dollars added and the panel wrote one figure for both.
    def test_a_reinsurer_on_two_currencies_is_never_summed_on_the_security_panel(self):
        bwp = self.exposure('SPX-BWP', 'BWP', '0', '0', '0')
        usd = self.exposure('SPX-USD', 'USD', '0', '0', '0')
        FacReinsurerAllocation.objects.create(
            exposure=bwp, reinsurer=self.munich,
            share_percent=Decimal('50.00'), allocated_amount=Decimal('50000.00'))
        FacReinsurerAllocation.objects.create(
            exposure=usd, reinsurer=self.munich,
            share_percent=Decimal('25.00'), allocated_amount=Decimal('10000.00'))

        r = self.client.get(SECURITY_PANEL)
        self.assertEqual(r.status_code, 200, r.data)
        row = next(x for x in r.data['panel'] if x['short_code'] == 'MUN')
        self.assertTrue(row.get('mixed_currency'))
        self.assertIsNone(row.get('fac_amount'))
        by = {c['currency']: c for c in row['by_currency']}
        self.assertEqual(Decimal(by['BWP']['amount']), Decimal('50000.00'))
        self.assertEqual(Decimal(by['USD']['amount']), Decimal('10000.00'))
        # 50000 + 10000 = 60000 must not appear anywhere on the row.
        self.assertNotIn('60000', str(row))

    def test_a_reinsurer_on_one_currency_keeps_its_plain_amount_on_the_panel(self):
        bwp = self.exposure('SPX-BWP2', 'BWP', '0', '0', '0')
        FacReinsurerAllocation.objects.create(
            exposure=bwp, reinsurer=self.munich,
            share_percent=Decimal('50.00'), allocated_amount=Decimal('50000.00'))

        r = self.client.get(SECURITY_PANEL)
        row = next(x for x in r.data['panel'] if x['short_code'] == 'MUN')
        self.assertFalse(row.get('mixed_currency'))
        self.assertEqual(Decimal(row['fac_amount']), Decimal('50000.00'))

    def test_the_security_panel_total_never_sums_across_currencies_either(self):
        """Two DIFFERENT reinsurers, each in a single currency, must not be
        added into one 'total_fac_amount' either — that is still a
        cross-currency sum, just spread across two rows instead of one."""
        from reinsurance.test_kyc_documents import complete_kyc
        other = complete_kyc(Reinsurer.objects.create(
            name='GIC Re', short_code='GIC',
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=timezone.localdate() + timedelta(days=365)))
        bwp = self.exposure('SPX-BWP3', 'BWP', '0', '0', '0')
        usd = self.exposure('SPX-USD3', 'USD', '0', '0', '0')
        FacReinsurerAllocation.objects.create(
            exposure=bwp, reinsurer=self.munich,
            allocated_amount=Decimal('50000.00'))
        FacReinsurerAllocation.objects.create(
            exposure=usd, reinsurer=other,
            allocated_amount=Decimal('10000.00'))

        r = self.client.get(SECURITY_PANEL)
        self.assertTrue(r.data.get('mixed_currency'))
        self.assertIsNone(r.data.get('total_fac_amount'))
        self.assertNotIn('60000', str(r.data.get('total_by_currency')))
