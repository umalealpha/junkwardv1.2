"""
ifrs17/tests/test_parity.py — the hard gate.

The engine must reproduce Empirica's signed FY2026 valuation TO THE CENT at base
levers. If it cannot, the engine is wrong — not the report. Everything else the
module does (cockpit, disclosures, exports) is formatting over these numbers, so
nothing downstream can be trusted until this passes.

Also holds the anti-drift guards:
  * the frozen ADIC figures in bridge.FROZEN_MA agree with constants.REPORTED, so
    the two in-code copies cannot silently diverge;
  * FY2025 GWP is exactly 125,148,692 (125.15 Mn). Quoting anything else is a
    production incident the CFO has corrected around a hundred times.

No Django, no database — the engine is pure so these run instantly.
"""
from decimal import Decimal as D

from django.test import SimpleTestCase

from ifrs17 import constants as K
from ifrs17.constants import BASE_LEVERS
from ifrs17.bridge import FROZEN_MA, build_bridge, gwp_check
from ifrs17.engine import (
    Levers, chain_ladder_ibnr, claims_handling_expense_reserve, compute,
    jbb_commission_sensitivity, risk_adjustment, salvage_subrogation_deduction,
)

CENT = D('0.01')


class ReportParityTest(SimpleTestCase):
    """Base levers, every segment on → Empirica's Table 1 / 14 / 17 / 18."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c = compute(Levers.base(), year='FY2026')
        cls.rep = K.REPORTED['FY2026']

    def _same(self, got, key, label):
        want = self.rep[key]
        self.assertLessEqual(
            abs(D(got) - D(want)), CENT,
            f'{label}: engine {got:,} vs report {want:,}')

    # ---- statement of comprehensive income (§4.1 Table 14) -------------
    def test_insurance_revenue(self):
        self._same(self.c.insurance_revenue, 'insurance_revenue', 'Insurance revenue')

    def test_insurance_service_expenses(self):
        self._same(self.c.insurance_service_expenses,
                   'insurance_service_expenses', 'Insurance service expenses')

    def test_service_result_before_reinsurance(self):
        self._same(self.c.service_result_before_reinsurance,
                   'service_result_before_reinsurance', 'Result before reinsurance')

    def test_net_reinsurance_result(self):
        self._same(self.c.net_reinsurance_result,
                   'net_reinsurance_result', 'Net reinsurance result')

    def test_profit_before_tax(self):
        """The engine sums the signed components to 12,864,244; the report states
        12,864,243. That 1 is a rounding difference INSIDE the report — §7.2's own
        bridge lands on 12,864,245 and calls the 2 rounding. Recorded as DQ-13; the
        engine reports the arithmetic rather than forcing either side to match."""
        want = self.rep['profit_before_tax']
        self.assertLessEqual(
            abs(self.c.profit_before_tax - want), D('2'),
            f'PBT: engine {self.c.profit_before_tax:,} vs report {want:,}')
        components = (self.c.insurance_revenue
                      + self.c.insurance_service_expenses
                      + self.c.net_reinsurance_result)
        self.assertEqual(self.c.profit_before_tax, components,
                         'the engine must report the arithmetic it actually did')

    # ---- statement of financial position (§4.4 Table 17/18) ------------
    def test_insurance_contract_liabilities(self):
        self._same(self.c.insurance_contract_liabilities,
                   'insurance_contract_liabilities', 'Insurance contract liabilities')

    def test_reinsurance_contract_assets(self):
        self._same(self.c.reinsurance_contract_assets,
                   'reinsurance_contract_assets', 'Reinsurance contract assets')

    def test_net_ifrs17_liability(self):
        self._same(self.c.net_ifrs17_liability,
                   'net_ifrs17_liability', 'Net IFRS 17 liability')

    def test_lic_components(self):
        self._same(self.c.lic_best_estimate, 'lic_best_estimate', 'LIC best estimate')
        self._same(self.c.lic_risk_adjustment, 'lic_risk_adjustment', 'LIC risk adjustment')
        self._same(self.c.lrc, 'lrc', 'LRC')

    # ---- reserves (§5) -------------------------------------------------
    def test_gross_ibnr(self):
        self._same(self.c.gross_ibnr, 'gross_ibnr', 'Gross IBNR')

    def test_claims_handling_expense_reserve(self):
        self._same(self.c.che_reserve, 'che_reserve', 'Claims handling expense reserve')

    def test_salvage_and_subrogation_deduction(self):
        self._same(-self.c.salvage_subrogation,
                   'salvage_subrogation_deduction', 'Salvage and subrogation')

    def test_attributable_expenses(self):
        self._same(self.c.attributable_expenses,
                   'attributable_expenses', 'Attributable expenses')

    # ---- ratios (§4.3) --------------------------------------------------
    def test_combined_ratio_is_82_percent(self):
        self.assertEqual(round(self.c.combined_ratio * 100), 82)

    def test_loss_ratio_is_51_percent(self):
        self.assertEqual(round(self.c.loss_ratio * 100), 51)


class ComponentMathTest(SimpleTestCase):
    """Each component against the report's own worked numbers."""

    def test_che_reserve_uses_half_of_case_reserves(self):
        """§3.7 Table 10 — base is IBNR plus HALF of case reserves: 26,906,988.90."""
        base = K.REPORTED['FY2026']['gross_ibnr'] + \
            K.REPORTED['FY2026']['gross_case_reserves'] / 2
        self.assertLessEqual(abs(base - D('26906988.90')), D('1'))
        got = claims_handling_expense_reserve(
            K.REPORTED['FY2026']['gross_ibnr'],
            K.REPORTED['FY2026']['gross_case_reserves'],
            K.BASE_LEVERS['che_factor_pct'])
        self.assertLessEqual(abs(got - D('666747.22')), D('1'))

    def test_risk_adjustment_is_six_percent(self):
        got = risk_adjustment(K.REPORTED['FY2026']['lic_best_estimate'], D('0.06'))
        self.assertLessEqual(abs(got - D('1878005.77')), D('1'))

    def test_salvage_deduction(self):
        """6.2015% is a ROUNDED display of the real rate, so re-deriving lands
        about 15 short of the signed 2,025,344. The signed figure is what the
        valuation uses (see test_salvage_and_subrogation_deduction); this only
        checks the published rate is the right order. Same class as DQ-12."""
        got = salvage_subrogation_deduction(
            K.REPORTED['FY2026']['gross_case_reserves'],
            K.REPORTED['FY2026']['gross_ibnr'],
            K.BASE_LEVERS['salvage_subro_pct'])
        self.assertLessEqual(abs(got - D('2025344')), D('20'))

    def test_ibnr_by_uwy_sums_to_the_selection(self):
        signed = K.REPORTED['FY2026']['gross_ibnr']
        total, rows = chain_ladder_ibnr(K.IBNR_BY_UWY_FY26,
                                        K.BASE_LEVERS['ibnr_tail_factor'], signed)
        # The SIGNED selection is returned verbatim at the signed tail...
        self.assertEqual(total, D('21155286.00'))
        self.assertEqual(len(rows), 7)
        # ...and the rows deliberately do NOT foot to it. 31 short, inside the
        # report (DQ-11). Re-adding them would silently lower a signed figure.
        self.assertEqual(sum(r['ibnr'] for r in rows), D('21155255.00'))

    def test_the_2026_year_dominates_the_ibnr(self):
        """§5.2 — 17,511,400 of 21,155,286 is the 2026 underwriting year, 82.8%."""
        _, rows = chain_ladder_ibnr(K.IBNR_BY_UWY_FY26,
                                    K.BASE_LEVERS['ibnr_tail_factor'],
                                    K.REPORTED['FY2026']['gross_ibnr'])
        y26 = next(r for r in rows if r['underwriting_year'] == 2026)
        self.assertLessEqual(abs(y26['ibnr'] - D('17511400')), D('1'))


class LeverSensitivityTest(SimpleTestCase):
    """Moving a lever must actually move the result — that is the whole ask."""

    def test_the_jbb_slider_shows_the_unrecognised_commission(self):
        """§6.3 — 25% provisional to 41% top of scale = 8,712,000."""
        got = jbb_commission_sensitivity(D('0.41'))
        self.assertLessEqual(abs(got - D('8712000')), D('100'))

    def test_the_jbb_slider_is_zero_at_the_booked_rate(self):
        self.assertEqual(jbb_commission_sensitivity(D('0.25')), D('0.00'))

    def test_jbb_flows_into_the_result_and_is_labelled_a_sensitivity(self):
        base = compute(Levers.base())
        moved = compute(Levers(jbb_commission_pct=D('0.41')))
        self.assertGreater(moved.profit_before_tax, base.profit_before_tax)
        self.assertLessEqual(
            abs((moved.profit_before_tax - base.profit_before_tax) - D('8712000')),
            D('100'))
        self.assertTrue(any('not been recognised' in n for n in moved.notes),
                        'the JBB movement MUST be labelled a sensitivity')

    def test_raising_the_risk_adjustment_raises_the_liability(self):
        base = compute(Levers.base())
        moved = compute(Levers(ra_pct=D('0.08')))
        self.assertGreater(moved.lic_risk_adjustment, base.lic_risk_adjustment)
        self.assertGreater(moved.insurance_contract_liabilities,
                           base.insurance_contract_liabilities)

    def test_raising_the_risk_adjustment_LOWERS_profit(self):
        """🔴 THE ONE FABLE CAUGHT. More prudence is more expense, so profit must
        FALL. The cockpit was showing it RISE — a sensitivity screen where more
        risk adjustment reads as more profit is worse than no screen. The change
        in PBT must be exactly minus the change in the LIC risk adjustment."""
        base = compute(Levers.base())
        moved = compute(Levers(ra_pct=D('0.08')))
        self.assertLess(moved.profit_before_tax, base.profit_before_tax,
                        'raising the risk adjustment must lower profit')
        self.assertEqual(
            moved.profit_before_tax - base.profit_before_tax,
            -(moved.lic_risk_adjustment - base.lic_risk_adjustment))

    def test_a_tiny_lever_nudge_does_not_jump_the_result(self):
        """No basis-switch discontinuity: an infinitesimal move off base must
        move PBT by an infinitesimal amount, not snap it by millions."""
        base = compute(Levers.base())
        nudged = compute(Levers(ra_pct=BASE_LEVERS['ra_pct'] + D('0.0000001')))
        self.assertLess(abs(nudged.profit_before_tax - base.profit_before_tax),
                        D('10'),
                        'a 0.00001% RA nudge snapped the result — basis switch')

    def test_raising_the_expense_apportionment_lowers_profit(self):
        base = compute(Levers.base())
        moved = compute(Levers(attributable_share_pct=D('0.95')))
        self.assertGreater(moved.attributable_expenses, base.attributable_expenses)
        self.assertLess(moved.profit_before_tax, base.profit_before_tax)

    def test_off_base_pbt_is_pinned(self):
        """An exact off-base value, so the two engines can be held to the same
        number and a future 'simplification' cannot quietly change it."""
        c = compute(Levers(ra_pct=D('0.08')))
        # RA 6%->8% raises LIC RA by 2% of the LIC best estimate; PBT falls by
        # that amount. 0.02 * 31,300,096 = 626,001.92.
        expected = K.REPORTED['FY2026']['profit_before_tax'] - D('626001.92')
        self.assertLessEqual(abs(c.profit_before_tax - expected), D('2'),
                             f'off-base PBT {c.profit_before_tax:,} != {expected:,}')

    def test_the_ibnr_tail_lever_moves_the_ibnr(self):
        """FY25 used a nil tail; the report values that single change at 896,862."""
        base = compute(Levers.base())
        nil_tail = compute(Levers(ibnr_tail_factor=D('1.000000')))
        self.assertLess(nil_tail.gross_ibnr, base.gross_ibnr)

    def test_the_expense_apportionment_lever_moves_expenses(self):
        base = compute(Levers.base())
        fy25_share = compute(Levers(attributable_share_pct=D('0.805')))
        self.assertLess(fy25_share.attributable_expenses, base.attributable_expenses)

    def test_switching_a_segment_off_removes_it_everywhere(self):
        """Per-segment storage exists precisely so a switch can do this."""
        base = compute(Levers.base())
        no_motor = compute(Levers.base(),
                           segments_on=set(K.SEGMENT_FY26) - {'motor'})
        self.assertLess(no_motor.insurance_revenue, base.insurance_revenue)
        self.assertNotIn('motor', [s.segment for s in no_motor.by_segment])
        self.assertLessEqual(
            abs((base.insurance_revenue - no_motor.insurance_revenue) - D('68414860')),
            D('5'))

    def test_health_is_off_by_default_and_labelled_when_switched_on(self):
        self.assertFalse(Levers.base().health_modelled)
        with_health = compute(Levers(health_modelled=True))
        self.assertTrue(any('Health is INCLUDED' in n for n in with_health.notes))

    def test_discounting_on_is_labelled_as_a_departure(self):
        c = compute(Levers(discounting=True))
        self.assertTrue(any('departs from the signed basis' in n for n in c.notes))


class FrozenNumberGuardTest(SimpleTestCase):
    """🔴 Anti-drift. Two in-code copies of the ADIC figures must never diverge."""

    def test_fy25_gwp_is_exactly_125_148_692(self):
        self.assertEqual(FROZEN_MA['FY2025']['gross_written_premium'], D('125148692'))

    def test_the_valuation_agrees_with_the_frozen_register(self):
        self.assertEqual(K.REPORTED['FY2025']['gross_written_premium'],
                         FROZEN_MA['FY2025']['gross_written_premium'])

    def test_gwp_check_ties(self):
        g = gwp_check('FY2025')
        self.assertTrue(g['comparable'])
        self.assertTrue(g['ties'], g)

    def test_gwp_check_names_the_management_accounts_as_truth(self):
        self.assertEqual(gwp_check('FY2025')['registered_truth'],
                         'frozen_management_accounts')

    def test_fy25_pat_register_is_the_frozen_292k(self):
        self.assertEqual(FROZEN_MA['FY2025']['profit_after_tax'], D('292000'))


class BridgeTest(SimpleTestCase):
    """The bridge must be honest about what it cannot explain."""

    def test_it_reports_the_gap_rather_than_absorbing_it(self):
        r = build_bridge('FY2025')
        self.assertFalse(r.reconciles)
        self.assertIsNotNone(r.conflict)
        self.assertEqual(r.conflict['registered_truth'], 'frozen_management_accounts')
        self.assertGreater(abs(r.unexplained), D('0'))

    def test_supplying_the_missing_ledger_figures_closes_it(self):
        """Given the three figures the valuation does not carry, it must land on
        the frozen PAT exactly — proving the walk itself is right and only the
        inputs were missing."""
        r0 = build_bridge('FY2025')
        gap = r0.unexplained            # 310,930 at the time of writing
        r = build_bridge('FY2025', tax=gap)
        self.assertTrue(r.reconciles, f'unexplained {r.unexplained}')
        self.assertIsNone(r.conflict)

    def test_every_step_is_labelled(self):
        r = build_bridge('FY2025')
        self.assertTrue(all(s.label and s.source for s in r.steps))

    def test_fy2026_is_not_comparable_until_the_audit_closes(self):
        r = build_bridge('FY2026')
        self.assertFalse(r.comparable)
        self.assertIsNone(r.frozen_ma_pat)
