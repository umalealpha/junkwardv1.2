"""Importer guard: spreadsheet totals/summary rows must never become employees.

CFO directive 2026-06-25 — the Veritas (VCM) 2026-06 Smart Upload ingested the
spreadsheet's "Total" row as a person, creating a phantom payslip that would
DOUBLE the GL if the period were posted.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Company
from payroll.importer import (
    _is_total_row, commit_payroll_import, validate_payroll_rows,
)
from payroll.models import Employee, Payslip, PayrollImportBatch, PayrollPeriod


class TotalsRowGuardTest(TestCase):
    def test_is_total_row_matches_summary_labels(self):
        for n in ['Total', 'total', 'TOTAL', 'Totals', 'Grand Total',
                  'Subtotal', 'Sub-Total', 'Sub Total', 'Sum', 'Total for June']:
            self.assertTrue(_is_total_row(n), n)

    def test_is_total_row_keeps_real_names(self):
        for n in ['Tumiso Innocent Motseko', 'Tshephang Motswagae',
                  'Total Quality Mgmt', 'Totally Ltd', '', None]:
            self.assertFalse(_is_total_row(n), n)

    def test_validate_skips_totals_row(self):
        rows = [
            {'row_index': 1, 'Employee': 'Tumiso Motseko'},
            {'row_index': 2, 'Employee': 'Total'},
        ]
        # 'Total' must NOT be flagged as missing or duplicate — just skipped.
        self.assertEqual(validate_payroll_rows(rows), [])

    def test_commit_does_not_create_phantom_total(self):
        co = Company.objects.create(code='ZZV', name='Veritas (test)')
        per = PayrollPeriod.objects.create(
            period_name='2099-01', start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 31), pay_date=date(2099, 1, 25))
        user = User.objects.create_user('importer_t', 'imp@x.com', 'x')
        batch = PayrollImportBatch.objects.create(
            company=co, period=per, source='test', created_by=user,
            parsed_rows=[{'row_index': 1, 'Employee': 'Total',
                          'Gross': '50000', 'Net Salary': '40000'}])
        commit_payroll_import(batch, user)
        self.assertEqual(Employee.objects.filter(full_name='Total').count(), 0)
        self.assertEqual(Payslip.objects.filter(period=per).count(), 0)


class CtcAlwaysDerivedTest(TestCase):
    """CFO directive (ADIC Aug 2026): the sheet's CTC column can be stale, so
    cost-to-company must ALWAYS be derived (gross + employer contributions),
    never trusted from the file. Fails on the pre-fix line, which trusted a
    non-zero file CTC."""

    def test_stale_file_ctc_is_ignored_and_derived(self):
        from decimal import Decimal as D
        from payroll.models import PayslipComponent
        PayslipComponent.objects.get_or_create(
            code='PENSION_ER',
            defaults=dict(name='Pension Company Contribution',
                          kind=PayslipComponent.Kind.COMPANY_CONTRIBUTION))
        co = Company.objects.create(code='ZZC', name='CTC test co')
        per = PayrollPeriod.objects.create(
            period_name='2099-02', start_date=date(2099, 2, 1),
            end_date=date(2099, 2, 28), pay_date=date(2099, 2, 25))
        user = User.objects.create_user('ctc_t', 'ctc@x.com', 'x')
        batch = PayrollImportBatch.objects.create(
            company=co, period=per, source='test', created_by=user,
            parsed_rows=[{'row_index': 1, 'Employee': 'Ctc Tester',
                          'Gross': '22000', 'Net Salary': '20000', 'PAYE': '2000',
                          'Pension Company Contribution': '1000',
                          'CTC': '99999.00'}])   # deliberately STALE file CTC
        commit_payroll_import(batch, user)
        ps = Payslip.objects.get(period=per)
        # 22000 gross + 1000 employer contribution = 23000; the stale 99999 is ignored.
        self.assertEqual(ps.ctc_amount, D('23000.00'))


class NonCashBenefitColumnTest(TestCase):
    """'Non-Cash Benefits' is mapped in HEADER_TO_COMPONENT (and in
    payroll/register_backfill.py, which proves it is a real file header) but was
    missing from CFO_HEADERS. parse_payroll_file builds each row dict from
    CFO_HEADERS ONLY, so the value never reached the row: the earning silently
    became ZERO and no line was written, while the file's Gross — which INCLUDES
    the benefit — was still trusted verbatim. Header said 12,500; lines summed
    to 10,000 (proven 2026-09-20).
    """

    CSV = (b'Employee,Basic Salary,Non-Cash Benefits,Gross,PAYE,Net Salary\n'
           b'Kea Mmusi,10000,2500,12500,0,12500\n')

    def test_parser_carries_the_mapped_column(self):
        from payroll.importer import parse_payroll_file
        rows, errors = parse_payroll_file(self.CSV, 'noncash.csv')
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0].get('Non-Cash Benefits')), '2500')

    def test_lines_sum_to_the_trusted_gross(self):
        from decimal import Decimal as D
        from django.core.management import call_command
        from payroll.importer import parse_payroll_file
        from payroll.models import PayslipComponent, PayslipLine

        call_command('setup_payroll_components')
        co = Company.objects.create(code='ZZN', name='Non-cash test co')
        per = PayrollPeriod.objects.create(
            period_name='2099-03', start_date=date(2099, 3, 1),
            end_date=date(2099, 3, 31), pay_date=date(2099, 3, 25))
        user = User.objects.create_user('noncash_t', 'nc@x.com', 'x')
        rows, _ = parse_payroll_file(self.CSV, 'noncash.csv')
        batch = PayrollImportBatch.objects.create(
            company=co, period=per, source='test', created_by=user,
            parsed_rows=rows)
        commit_payroll_import(batch, user)

        ps = Payslip.objects.get(period=per)
        self.assertEqual(ps.gross_amount, D('12500.00'))
        earnings = sum(
            (ln.amount for ln in PayslipLine.objects.filter(payslip=ps)
             .select_related('component')
             if ln.component.kind == PayslipComponent.Kind.EARNING),
            D('0.00'))
        self.assertEqual(earnings, D('12500.00'))   # 10,000 + 2,500, NOT 10,000
