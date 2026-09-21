"""payroll/tests/test_total_row.py — spreadsheet TOTAL/summary rows must never be
ingested as employees. Bug (ADIC final run 2026-07-24): the label
"TOTAL (72 payslips)" slipped past the exact-word pattern and doubled Basic.
"""
from django.test import SimpleTestCase
from payroll.importer import _is_total_row


class TotalRowDetectionTests(SimpleTestCase):
    def test_total_rows_are_detected(self):
        for label in ['TOTAL (72 payslips)', 'Total', 'totals', 'GRAND TOTAL',
                      'Sub-total', 'Subtotal (x)', 'Sum', 'total for June',
                      'TOTAL - 72 payslips', 'Totals:']:
            self.assertTrue(_is_total_row(label), f'{label!r} should be a total row')

    def test_real_names_are_not_total_rows(self):
        for label in ['Bernard Balikani', 'Arun Iyer', 'Aobakwe Angel Morris',
                      'Sumaya Khan', 'Summertime Ltd', 'Sunday Phiri', '', None]:
            self.assertFalse(_is_total_row(label), f'{label!r} must NOT be a total row')


class TotalRowRealisticLabelsTests(SimpleTestCase):
    """Bug proven 2026-09-20: 'TOTAL' and 'TOTAL (72 payslips)' were skipped,
    but four realistic summary labels were INGESTED AS AN EMPLOYEE — an
    Employee row minted, a payslip created and the period's Basic doubled.

    The pattern is narrow ON PURPOSE (Sumaya Khan / Summertime Ltd / Sunday
    Phiri are people, and so is 'Total Quality Mgmt'), so the widening is an
    ALLOW-LIST of payroll-summary tail words plus an upper-case entity code —
    never 'anything after the word Total'.
    """

    def test_realistic_total_labels_are_skipped(self):
        for label in ['Total Payroll', 'TOTAL STAFF', 'Totals ADIC',
                      'Grand Total Gross']:
            self.assertTrue(_is_total_row(label), f'{label!r} should be a total row')

    def test_the_widening_does_not_swallow_names(self):
        # Pinned elsewhere too (payroll/tests/test_importer.py) — repeated here
        # because THIS is the file that widens the pattern.
        for label in ['Total Quality Mgmt', 'Totally Ltd', 'Sumaya Khan',
                      'Summertime Ltd', 'Sunday Phiri']:
            self.assertFalse(_is_total_row(label), f'{label!r} must NOT be a total row')
