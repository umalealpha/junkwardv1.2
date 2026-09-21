"""A commission workbook split into Domestic / Commercial / New Business.

Bokani Makosha, bug db6c82e9 (19-Sep-2026): the upload kept only the tab with
the most rows (New Business), so an agent verified at P9,429.09 was loaded at
P6,177.69. The layout below copies the real template (made-up policies): a
Details tab with the COMMISSION SUMMARY and the Domestic policy list, a
Commercial tab with commission in the TOTAL column, and a New Business tab.
"""
import os
import tempfile
from decimal import Decimal

from django.test import SimpleTestCase

from . import importer


def _workbook(path):
    import openpyxl
    wb = openpyxl.Workbook()
    d = wb.active
    d.title = 'Details'
    d.append(['AGENT COMMISSION SUMMARY REPORT'])
    d.append([])
    d.append(['COMMISSION SUMMARY'])
    d.append(['', 'DOMESTIC', 'COMMERCIAL', 'NEW BUSINESS', 'TOTAL'])
    d.append(['Commission Payable:', 1200, 60, 25, 1285])
    d.append([])
    d.append(['POLICY DETAILS'])
    d.append(['Policy Number', 'Client Name', 'Amount Collected (P)', 'Plan',
              'New Business/renewal/prior', 'KYC Status'])
    d.append(['DOMG1', 'Client A', 600, 'MONTHLY', 'Renewal', 'Compliant'])
    d.append(['DOMG2', 'Client B', 900, 'MONTHLY', 'Renewal', 'Compliant'])

    c = wb.create_sheet('Commercial')
    c.append(['COMMERCIAL POLICIES - MONTHLY'])
    c.append(['', '', '', '', '', 'PREMIUM', '', 'COMMISSION', 'TOTAL'])
    c.append(['Client Name', 'Policy No.', 'KYC', 'Status', 'Total Premium', 'Type',
              'Motor (3.5%)', 'Non-Motor (5%)', 'TOTAL'])
    c.append(['Firm A', 'COMG1', 'Compliant', 'SUCCESSFUL', 1000, 'CURRENT', 20, 15, 35])
    c.append(['Firm B', 'COMG2', 'Compliant', 'SUCCESSFUL', 700, 'PRIOR', 25, 0, 25])
    c.append(['TOTAL', None, None, None, None, None, None, None, 60])

    n = wb.create_sheet('New Business')
    n.append(['Policy Number', 'Client Name', 'Previous Month / New Business', 'Amount (P)',
              'Commission (P)'])
    for i in range(3):   # the most rows — the tab the old reader kept alone
        n.append([f'NB{i}', f'Client N{i}', 'New Business', 100, Decimal('8.3333')])
    n.append(['NB3', 'Client N3', 'New Business', 100, 0.0001])
    wb.save(path)


class ThreeFigureWorkbookTests(SimpleTestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        _workbook(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_all_three_figures_are_read(self):
        lines = importer.parse_workbook(self.path)
        gross = sum((ln['commission_amount'] for ln in lines), Decimal('0'))
        self.assertEqual(round(gross, 2), Decimal('1285.00'),
                         'Domestic 1200 + Commercial 60 + New Business 25 = the summary TOTAL')

    def test_every_policy_on_each_tab_is_kept(self):
        pols = {ln['policy_number'] for ln in importer.parse_workbook(self.path)}
        self.assertTrue({'DOMG1', 'DOMG2', 'COMG1', 'COMG2', 'NB0', 'NB3'} <= pols)

    def test_domestic_is_one_summary_line_not_invented_per_policy(self):
        lines = importer.parse_workbook(self.path)
        dom = [ln for ln in lines if ln['policy_number'].startswith('DOMG')]
        self.assertTrue(all(ln['commission_amount'] == 0 for ln in dom))
        self.assertIn(Decimal('1200'), [ln['commission_amount'] for ln in lines
                                        if not ln['policy_number']])

    def test_commercial_commission_comes_from_the_total_column(self):
        com = {ln['policy_number']: ln['commission_amount']
               for ln in importer.parse_workbook(self.path)
               if ln['policy_number'].startswith('COMG')}
        self.assertEqual(com, {'COMG1': Decimal('35'), 'COMG2': Decimal('25')})

    def test_summary_figures_typed_as_text_are_still_read(self):
        rows = [['', 'DOMESTIC', 'COMMERCIAL', 'NEW BUSINESS', 'TOTAL'],
                ['Commission Payable:', '1,200.00', '60.00', '25.00', '1,285.00']]
        self.assertEqual(importer._three_figure_summary(rows)['total'], Decimal('1285.00'))
