"""Tests for the BONU schedule pre-flight validator.

Every finding pins a specific defect that Kutlo's live workbook surfaced on
13-Aug-2026. The synthetic fixtures reproduce the shape of the real file so a
regression on any one check goes red without needing member data.
"""
import os
import tempfile

import openpyxl
from django.test import TestCase

from bonu import schedule, schedule_validate as sv


def _base_wb(path):
    """A miniature workbook with the exact shape of Kutlo's file: SUMMARY as a
    matrix with a Totals column, CLAIMS/UNPAID/ADMIN as tables, all healthy."""
    wb = openpyxl.Workbook()
    # SUMMARY
    ws = wb.active
    ws.title = 'SUMMARY'
    ws.append(['', 'Jan-25', 'Feb-25', 'Totals'])
    ws.append(['Total Revenue',                100,   200,  300])
    ws.append(['Vat @ 14%',                    -10,   -20,  -30])
    ws.append(['Commission @ 15%',             -15,   -25,  -40])
    ws.append(['Client Claims',                -30,   -50,  -80])
    ws.append(['Admin Expenses',               -20,   -30,  -50])
    ws.append(['Profit/Loss',                   25,    75,  100])
    ws.append(['Premiums(- VAT & Commission)',  75,   155,  230])
    # CLAIMS: two firms, one date, all with amounts, one status flag with a
    # trailing space so STATUS_SPACING can bite when we want it to.
    cl = wb.create_sheet('CLAIMS')
    cl.append(['Law Firm Name', 'Inv Date', 'Invoice/Referance No:',
               'Client Names', 'Case Matter', 'Invoice Month',
               'Inv Amount (P)', 'Status'])
    cl.append(['Firm A', '01/03/2025', 'A-1', 'X', 'Divorce', 'Mar', 50, 'Paid'])
    cl.append(['Firm B', '02/03/2025', 'B-1', 'Y', 'Custody', 'Mar', 30, 'Paid'])
    # UNPAID FEES: same firms so FIRM_MISSING_FROM_UNPAID is silent by default.
    up = wb.create_sheet('UNPAID FEES')
    up.append(['Claim Expenses', 'Bank', 'Totals'])
    up.append(['Firm A', 'FNB', -100])
    up.append(['Firm B', 'FNB', -50])
    # ADMIN EXPENSES
    ae = wb.create_sheet('ADMIN EXPENSES')
    ae.append(['Date', 'Category', 'Description', 'Invoice Issued',
               'Payment Method', 'Amount (BWP)'])
    ae.append(['01/03/2025', 'Ops', 'X', 'Y', 'EFT', 50])
    wb.save(path)


class BaseFixture(TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(); self.path = os.path.join(d, 'v.xlsx')
        _base_wb(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)


class RowTotalsCheck(BaseFixture):
    def test_a_healthy_file_produces_no_finding(self):
        codes = {f['code'] for f in sv.run_all()['findings']}
        self.assertNotIn('ROW_TOTAL_MISMATCH', codes)

    def test_flags_a_row_whose_total_disagrees_with_its_months(self):
        # The specific defect on Kutlo's file: the Totals cell inflated by 6.5M.
        wb = openpyxl.load_workbook(self.path)
        ws = wb['SUMMARY']
        for cell in ws['1']:
            pass
        # Overwrite the 'Premiums(- VAT & Commission)' Totals cell (col D).
        ws['D8'] = 999            # months add to 230, Totals now reads 999
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        finds = sv.run_all()['findings']
        self.assertTrue(any(f['code'] == 'ROW_TOTAL_MISMATCH'
                            and 'Premiums' in f['title'] for f in finds))

    def test_scratch_columns_to_the_right_of_totals_are_ignored(self):
        """Kutlo's live SUMMARY has junk cells in the columns to the right of
        Totals — working notes, ratio remainders. Including them made every
        firm-by-firm row read as a mismatch, on a screen showing 57 critical
        findings that were all noise. Only cells LEFT of Totals are periods."""
        wb = openpyxl.load_workbook(self.path)
        ws = wb['SUMMARY']
        # Chikati-shaped row: label + 2 monthly cells + Totals + scratch cells.
        ws.append(['Firm X', -50, -50, -100, 9.22, 420000, 681450])
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        finds = [f for f in sv.run_all()['findings']
                 if f['code'] == 'ROW_TOTAL_MISMATCH' and 'Firm X' in f['title']]
        self.assertEqual(finds, [])

    def test_a_ratio_row_is_ignored(self):
        # Adding a ratio row that sums to 0.66 across months but shows 0.03 as the
        # Totals (a real 'average of monthly ratios' behaviour). It must not fire.
        wb = openpyxl.load_workbook(self.path)
        ws = wb['SUMMARY']
        ws.append(['Loss Ratios (%)', 0.5, 0.5, 0.03])
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        codes_hit = {f['title'] for f in sv.run_all()['findings']
                     if f['code'] == 'ROW_TOTAL_MISMATCH'}
        self.assertFalse(any('ratio' in t.lower() for t in codes_hit))


class SummaryVsDetail(BaseFixture):
    def test_flags_when_summary_line_does_not_equal_detail_tab(self):
        # CLAIMS tab totals 80; force the P&L Client Claims line to -100.
        wb = openpyxl.load_workbook(self.path)
        ws = wb['SUMMARY']
        ws['D5'] = -100                                        # was -80
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        finds = sv.run_all()['findings']
        self.assertTrue(any(f['code'] == 'SUMMARY_MISMATCH'
                            and f['where']['detail_key'] == 'claims' for f in finds))


class RatioBase(BaseFixture):
    def test_flags_a_ratio_base_that_is_not_rev_minus_vat_minus_commission(self):
        # Expected base = 300 + (-30) + (-40) = 230. Overwrite it to 999 —
        # exactly what Kutlo's live file did.
        wb = openpyxl.load_workbook(self.path)
        wb['SUMMARY']['D8'] = 999
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        codes = {f['code'] for f in sv.run_all()['findings']}
        self.assertIn('RATIO_BASE_WRONG', codes)


class StrayBelowTotals(BaseFixture):
    def test_flags_a_labelless_row_below_the_totals_line(self):
        # Add a Totals row, then a row with amount but no label.
        wb = openpyxl.load_workbook(self.path)
        wb['UNPAID FEES'].append(['Totals', '', -150])
        wb['UNPAID FEES'].append(['',       '',  -20])           # the stray
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        codes = {f['code'] for f in sv.run_all()['findings']}
        self.assertIn('STRAY_BELOW_TOTAL', codes)


class FirmsMatch(BaseFixture):
    def test_flags_a_firm_on_claims_but_missing_from_unpaid(self):
        # Add Kenosi to CLAIMS only — the real defect that hid P5,000.
        wb = openpyxl.load_workbook(self.path)
        wb['CLAIMS'].append(['Kenosi Junior Lewis', '02/04/2025', 'K-1',
                             'Z', 'Debt', 'Apr', 5000, 'Paid'])
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        finds = [f for f in sv.run_all()['findings']
                 if f['code'] == 'FIRM_MISSING_FROM_UNPAID']
        self.assertTrue(any('kenosi' in f['title'].lower() for f in finds))


class StatusSpacing(BaseFixture):
    def test_paid_and_lowercase_paid_are_flagged_as_the_same_thing(self):
        # norm_value() strips on import, so trailing-space duplicates never enter
        # a fresh upload. Case difference is a real live pattern this catches:
        # 'Paid' and 'paid' are the same thing but split every 'group by status'
        # aggregate.
        wb = openpyxl.load_workbook(self.path)
        wb['CLAIMS'].append(['Firm A', '02/04/2025', 'A-2', 'X', 'Divorce',
                             'Apr', 40, 'paid'])
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        codes = {f['code'] for f in sv.run_all()['findings']}
        self.assertIn('STATUS_SPACING', codes)


class Severities(BaseFixture):
    def test_severity_is_stable_across_findings(self):
        # A blank-file variant so every check has something to fire on.
        wb = openpyxl.load_workbook(self.path)
        wb['SUMMARY']['D8'] = 999                    # ratio base wrong (critical)
        wb['SUMMARY']['D5'] = -999                   # summary mismatch (high)
        wb['CLAIMS'].append(['Kenosi', '02/04/2025', 'K-1', 'Z', 'Debt',
                             'Apr', 5000, 'Paid '])   # firm missing + spacing
        wb.save(self.path)
        schedule.import_workbook(self.path, commit=True, wipe=True)
        out = sv.run_all()
        self.assertGreaterEqual(out['critical_count'], 1)
        self.assertGreaterEqual(out['high_count'], 1)
        # Most-severe first.
        for a, b in zip(out['findings'], out['findings'][1:]):
            order = {'critical': 0, 'high': 1, 'low': 2}
            self.assertLessEqual(order[a['severity']], order[b['severity']])
