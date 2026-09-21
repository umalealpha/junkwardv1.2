"""Tests for the Omni-computed KPIs.

The central control: the base for ratios is REVENUE − VAT − COMMISSION, not any
other number. On Kutlo's live file a single wrong cell inflated that base by
6,499,670.08, and the loss ratio read 50.88% against a real 89.05%. These tests
pin the correct arithmetic, and pin that the author's own figure is returned
alongside so a screen can show the disagreement instead of picking a winner.
"""
import os
import tempfile
from decimal import Decimal

import openpyxl
from django.test import TestCase

from bonu import schedule
from bonu.schedule_kpis import compute


def _wb(path, base_totals=230):
    """A summary tab whose maths is: revenue 300, VAT −30, commission −40, so net
    is 230. Claims −80 (26.67% loss), admin −50 (16.67%), combined 43.33%. If
    base_totals != 230, the author has miscoded their own base like Kutlo did."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'SUMMARY'
    ws.append(['', 'Jan-25', 'Feb-25', 'Totals'])
    ws.append(['Total Revenue',                100,   200,  300])
    ws.append(['Vat @ 14%',                    -10,   -20,  -30])
    ws.append(['Commission @ 15%',             -15,   -25,  -40])
    ws.append(['Client Claims',                -30,   -50,  -80])
    ws.append(['Admin Expenses',               -20,   -30,  -50])
    ws.append(['Profit/Loss',                   25,    75,  100])
    ws.append(['Premiums(- VAT & Commission)',  75,   155,  base_totals])
    wb.save(path)


class KpiTests(TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(); self.path = os.path.join(d, 'k.xlsx')

    def test_omni_ratios_are_computed_on_the_correct_base(self):
        _wb(self.path, base_totals=230)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        k = compute()
        self.assertEqual(k['net_premium']['omni'], '230.00')
        self.assertEqual(k['loss_ratio']['omni'], '34.78')            # 80/230
        self.assertEqual(k['expense_ratio']['omni'], '21.74')         # 50/230
        self.assertEqual(k['combined_ratio']['omni'], '56.52')

    def test_authors_wrong_base_produces_low_ratios_but_omni_stays_true(self):
        """The exact defect on Kutlo's file — a single overtyped Totals cell
        makes every ratio in the workbook read low. Omni recomputes from the
        monthly cells, which cannot be over-typed."""
        _wb(self.path, base_totals=999)                # base overstated
        schedule.import_workbook(self.path, commit=True, wipe=True)
        k = compute()
        self.assertEqual(k['net_premium']['omni'], '230.00')     # from months
        self.assertEqual(k['net_premium']['author'], '999.00')   # from the cell
        self.assertTrue(k['net_premium']['differ'])
        # Author's ratios read lower, Omni's do not.
        self.assertNotEqual(k['loss_ratio']['omni'],
                            k['loss_ratio']['author'])
        self.assertTrue(k['loss_ratio']['differ'])

    def test_break_even_uplift_puts_combined_at_100_percent(self):
        _wb(self.path, base_totals=230)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        k = compute()
        # Cost to cover 130 (80 claims + 50 admin), take-rate 230/300 ≈ 0.7667.
        # Break-even gross = 130 / 0.7667 = 169.5652. Uplift = 169.57/300 − 1.
        self.assertEqual(k['break_even']['gross_needed'], '169.57')
        # Rounded uplift = -43.48%. Sign convention: uplift below zero when net
        # premium exceeds cost. Kutlo's file needs a POSITIVE uplift; this
        # synthetic is a healthier book by construction.
        self.assertEqual(k['break_even']['uplift_pct'], '-43.48')

    def test_scratch_cells_right_of_totals_never_pollute_the_ratio_base(self):
        """Fable 5, 13-Aug-2026: `_months_sum` filtered period columns by NAME
        instead of stopping at Totals. Kutlo's file works today only because the
        P&L rows' scratch cells (Column 21..31 on his SUMMARY) are empty. A
        future upload with a working note on Total Revenue would silently
        inflate the ratio base — the exact defect this feature exists to
        prevent."""
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'SUMMARY'
        ws.append(['', 'Jan-25', 'Feb-25', 'Totals', 'Column 5'])
        ws.append(['Total Revenue',                100,   200,  300, 9.22])
        ws.append(['Vat @ 14%',                    -10,   -20,  -30, 0])
        ws.append(['Commission @ 15%',             -15,   -25,  -40, 0])
        ws.append(['Client Claims',                -30,   -50,  -80, 0])
        ws.append(['Admin Expenses',               -20,   -30,  -50, 0])
        ws.append(['Profit/Loss',                   25,    75,  100, 0])
        ws.append(['Premiums(- VAT & Commission)',  75,   155,  230, 0])
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        k = compute()
        # Total Revenue's Column 5 scratch cell must NOT be added to Omni's
        # computed revenue. Was 309.22; must be 300.
        self.assertEqual(k['revenue']['omni'], '300.00')
        self.assertEqual(k['net_premium']['omni'], '230.00')

    def test_period_label_reflects_the_workbook_not_a_hardcoded_span(self):
        """A hardcoded 'Jan 2025 to Jun 2026' silently mislabels next month's
        upload. The label must derive from the period columns."""
        _wb(self.path, base_totals=230)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        k = compute()
        self.assertEqual(k['period'], '2 periods, Jan-25 to Feb-25')

    def test_no_summary_tab_means_no_answer_offered(self):
        from bonu.models import BonuScheduleSheet
        BonuScheduleSheet.objects.all().delete()
        self.assertFalse(compute()['available'])
