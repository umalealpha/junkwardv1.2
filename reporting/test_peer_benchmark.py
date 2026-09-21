"""
Guards on the peer-benchmark dataset.

Two things can quietly go wrong here and both would put a wrong number in
front of the board: Alpha Direct's own figures drifting away from the CFO's
Management Accounts workbook, and a ratio silently reading zero because a peer
never disclosed the input. Each test below fails if either happens.
"""
from django.test import SimpleTestCase

from reporting.peer_benchmark import (
    ADIC_MA,
    PEERS,
    all_insurers,
    build_benchmark,
    market_stats,
)


class FrozenAlphaDirectNumbersTests(SimpleTestCase):
    """The MA workbook is the truth. These must never drift."""

    def test_fy26_headline_figures_match_the_ma_workbook(self):
        self.assertEqual(ADIC_MA['gross_written_premium'], 133.378)
        self.assertEqual(ADIC_MA['net_earned_premium'], 53.045)
        self.assertEqual(ADIC_MA['profit_after_tax'], 2.589)
        self.assertEqual(ADIC_MA['premiums_ceded'], 80.774)

    def test_history_carries_the_frozen_fy25_and_9m_figures(self):
        by_period = {h['period']: h for h in build_benchmark()['history']}
        fy25 = by_period['FY25 (12M to Jun-25)']
        self.assertEqual(fy25['gwp'], 125.149)   # never 99
        self.assertEqual(fy25['pat'], 0.292)
        nine = by_period['FY26 9M (to Mar-26)']
        self.assertEqual(nine['gwp'], 96.177)
        self.assertEqual(nine['pat'], 0.950)

    def test_the_ma_pl_still_adds_up(self):
        """Gross profit must reconcile from the lines above it, or the
        restatement onto the peer basis is built on a broken column."""
        m = ADIC_MA
        nep = m['gross_written_premium'] - m['premiums_ceded'] + m['change_in_upr']
        self.assertAlmostEqual(nep, m['net_earned_premium'], places=2)
        net_claims = m['gross_claims'] - m['claims_recovered'] - m['subrogations_salvages']
        self.assertAlmostEqual(net_claims, m['net_claims_incurred'], places=2)
        gross_profit = nep - net_claims + m['net_acquisition_income']
        self.assertAlmostEqual(gross_profit, m['gross_profit'], places=2)


class ComparableBasisTests(SimpleTestCase):
    """The restatement is the whole point of the module — pin its behaviour."""

    def setUp(self):
        self.us = all_insurers()[0]

    def test_we_are_first_and_flagged_as_us(self):
        self.assertTrue(self.us.is_us)
        self.assertEqual(self.us.short, 'Alpha Direct')

    def test_revenue_is_earned_not_written(self):
        """Using GWP here would flatter us against every IFRS 17 peer."""
        self.assertAlmostEqual(self.us.insurance_revenue, 133.819, places=2)
        self.assertNotEqual(self.us.insurance_revenue, ADIC_MA['gross_written_premium'])

    def test_provisions_and_depreciation_sit_in_operating_expenses(self):
        expected = (
            ADIC_MA['total_operating_expenses']
            + ADIC_MA['total_provisions']
            + ADIC_MA['depreciation']
        )
        self.assertAlmostEqual(self.us.operating_expenses, expected, places=2)

    def test_underwriting_profit_excludes_investment_and_other_income(self):
        """The headline finding. If this ever quietly turns into a healthy
        number because other income leaked in, the board gets told the
        insurance book is fine when it is not."""
        self.assertLess(self.us.underwriting_profit, 0.5)
        self.assertGreater(self.us.profit_after_tax, 2.0)

    def test_combined_ratio_is_at_break_even(self):
        self.assertAlmostEqual(self.us.combined_ratio, 100.0, delta=0.5)


class RatioSafetyTests(SimpleTestCase):
    """A missing disclosure must read as unknown, never as zero."""

    def test_missing_inputs_give_none_not_zero(self):
        sunshine = next(p for p in PEERS if p.short == 'Sunshine')
        self.assertIsNone(sunshine.staff_costs)
        self.assertIsNone(sunshine.staff_to_revenue)

    def test_negative_equity_suppresses_return_on_equity(self):
        """WestSure has negative equity. A naive division prints a large
        positive ROE off a negative base — worse than printing nothing."""
        westsure = next(p for p in PEERS if p.short == 'WestSure')
        self.assertLess(westsure.total_equity, 0)
        self.assertIsNone(westsure.return_on_equity)
        self.assertIsNone(westsure.operating_leverage)

    def test_loss_making_peer_has_no_underwriting_share_of_profit(self):
        westsure = next(p for p in PEERS if p.short == 'WestSure')
        self.assertIsNone(westsure.underwriting_share_of_profit)

    def test_distressed_peer_is_excluded_from_the_medians(self):
        """A company failing its capital test is not a benchmark. Asserting
        only that the median stays under 100 would pass either way — WestSure
        is one row of eight — so pin the exact values that change when it is
        wrongly left in: cession 30.6 not 38.4, combined 94.3 not 95.8."""
        median = market_stats()
        self.assertEqual(median['cession_ratio'], 30.6)
        self.assertEqual(median['combined_ratio'], 94.3)
        self.assertEqual(median['gross_loss_ratio'], 42.9)
        self.assertEqual(median['expense_to_revenue'], 12.0)


class SummaryStatementTests(SimpleTestCase):
    """The screen prints a summary income statement in the MA order. If the
    column stops footing, the CFO is handed a statement that does not add up —
    which is the exact criticism this page makes of everyone else."""

    def test_every_subtotal_foots(self):
        m = ADIC_MA
        nep = m['gross_written_premium'] - m['premiums_ceded'] + m['change_in_upr']
        self.assertAlmostEqual(nep, 53.045, places=2)
        net_claims = m['gross_claims'] - m['claims_recovered'] - m['subrogations_salvages']
        self.assertAlmostEqual(net_claims, 21.763, places=2)
        acquisition = (
            m['commissions_paid'] + m['insurance_in_a_box'] + m['bonu_acquisition']
            + m['broker_entertainment'] + m['health_insurance']
        )
        self.assertAlmostEqual(acquisition, 14.209, places=2)
        net_acquisition = m['commission_from_reinsurers'] - acquisition
        self.assertAlmostEqual(net_acquisition, m['net_acquisition_income'], places=2)
        gross_profit = nep - net_claims + net_acquisition
        self.assertAlmostEqual(gross_profit, m['gross_profit'], places=2)
        underwriting = (
            gross_profit - m['total_operating_expenses']
            - m['total_provisions'] - m['depreciation']
        )
        self.assertAlmostEqual(underwriting, 0.045, places=2)
        other_income = m['other_income'] - m['finance_cost']
        self.assertAlmostEqual(underwriting + other_income, m['profit_before_tax'], places=2)


class PayloadTests(SimpleTestCase):

    def test_every_peer_is_present_and_sorted_below_us(self):
        payload = build_benchmark()
        self.assertEqual(payload['peer_count'], 8)
        self.assertEqual(len(payload['insurers']), 9)
        peers = payload['insurers'][1:]
        revenues = [p['insurance_revenue'] for p in peers]
        self.assertEqual(revenues, sorted(revenues, reverse=True))

    def test_phoenix_is_our_size(self):
        """The comparison the whole pack turns on."""
        phoenix = next(p for p in PEERS if p.short == 'Phoenix')
        self.assertAlmostEqual(
            phoenix.gross_written_premium, ADIC_MA['gross_written_premium'], delta=1.0
        )
        self.assertGreater(phoenix.profit_after_tax, ADIC_MA['profit_after_tax'] * 3)

    def test_every_insurer_carries_a_source(self):
        for i in all_insurers():
            self.assertTrue(i.source, f'{i.short} has no source recorded')
