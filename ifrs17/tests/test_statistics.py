"""
ifrs17/tests/test_statistics.py — the analytics the CFO asked for.

"more data like loss ratios by products, etc etc all the statistics" — this
proves the six-year trend, the loss/combined ratio by product, the growth CAGRs,
the mix and the development factors are real and tie to the report, and that they
follow the levers rather than being a frozen snapshot.
"""
from decimal import Decimal as D

from django.test import SimpleTestCase

from ifrs17 import constants as K
from ifrs17.engine import Levers, compute
from ifrs17.statistics import (
    build_all, development_factors, loss_ratios_by_product, segment_growth,
    six_year_performance,
)


class StatisticsTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c = compute(Levers.base(), year='FY2026')

    def test_six_year_ends_on_the_signed_fy26_gwp(self):
        t = six_year_performance()
        last = t['rows'][-1]
        self.assertEqual(last[0], 'FY2026')
        self.assertEqual(last[1], D('133416295'))

    def test_six_year_starts_on_the_frozen_fy25(self):
        """FY2025 GWP in the trend must be the frozen 125,148,692."""
        t = six_year_performance()
        fy25 = next(r for r in t['rows'] if r[0] == 'FY2025')
        self.assertEqual(fy25[1], D('125148692'))

    def test_loss_ratios_by_product_puts_the_worst_first(self):
        t = loss_ratios_by_product(self.c)
        ratios = [r[3] for r in t['rows']]
        self.assertEqual(ratios, sorted(ratios, reverse=True))
        # Engineering (295%) and Guarantee (144%) are the loss-makers.
        worst = t['rows'][0]
        self.assertGreater(worst[4], D('1'))            # combined ratio > 100%
        self.assertEqual(worst[5], 'loss-making')

    def test_the_loss_ratios_follow_the_levers(self):
        """Switch Motor off — it must vanish from the product ratio table."""
        no_motor = compute(Levers.base(),
                           segments_on=set(K.SEGMENT_FY26) - {'motor'})
        names = [r[0] for r in loss_ratios_by_product(no_motor)['rows']]
        self.assertNotIn('Motor', names)

    def test_segment_growth_has_a_cagr_per_product(self):
        t = segment_growth()
        self.assertEqual(len(t['rows']), 8)
        # Liability grew ~9x over five years — a large positive CAGR.
        lib = next(r for r in t['rows'] if r[0] == 'Liability')
        self.assertGreater(lib[-1], D('0.5'))
        # Engineering contracted — a negative CAGR.
        eng = next(r for r in t['rows'] if r[0] == 'Engineering')
        self.assertLess(eng[-1], D('0'))

    def test_development_factors_show_the_tail_change(self):
        t = development_factors()
        fy26 = next(r for r in t['rows'] if 'FY2026 selected' in r[0])
        fy25 = next(r for r in t['rows'] if 'FY2025 signed' in r[0])
        # FY25 nil tail vs FY26 observed tail — the DQ the report highlights.
        self.assertEqual(fy25[-1], D('1.000000'))
        self.assertEqual(fy26[-1], D('1.003100'))

    def test_all_the_tables_build(self):
        tables = build_all(self.c)
        for key in ('six_year_performance', 'loss_ratios_by_product',
                    'segment_growth', 'premium_mix', 'key_ratios',
                    'development_factors'):
            self.assertIn(key, tables)
            self.assertTrue(tables[key]['rows'])
