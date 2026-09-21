"""
core/tests/test_smart_upload_payroll_commit.py

Problem 2 of Pako Kago's 2026-07-29 bug report: after a Smart Upload commit,
Commission (BWP 87,774.22) and Incentive (BWP 30,300.00) did not appear on the
Payroll dashboard.

The dashboard reads PayslipLine rows, and the old commit_payroll wrote none —
only the gross/paye/net headline. These tests assert the committed payslip now
carries a real line per component, that totals are recomputed from those lines,
and that the register's own totals are cross-checked rather than trusted.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Company
from core.smart_upload.committers import commit_payroll
from payroll.models import Employee, PayslipComponent, Payslip


D = Decimal


def _row(**over):
    """One clean register row: Basic 20000 + Commission 1500 + Incentive 500."""
    row = {
        'employee_name': 'Lerato Modise',
        'employee_code': 'EMP_001',
        'department':    'Finance',
        'period_end':    '2026-07-31',
        'period_label':  'FY27',          # deliberately the reported bad label
        'basic':         '20000.00',
        'commission':    '1500.00',
        'incentive':     '500.00',
        'paye':          '3000.00',
        'pension_ee':    '1000.00',
        'pension_er':    '1000.00',
        'gross':         '22000.00',
        'net':           '18000.00',
        'status':        'Active',
    }
    row.update(over)
    return row


class PayrollCommitTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('setup_payroll_components', verbosity=0)
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.user = User.objects.create_user('cfo-test', password='x')
        cls.emp = Employee.objects.create(
            company=cls.company, employee_number='EMP_001', full_name='Lerato Modise',
        )

    def _commit(self, rows, **kw):
        return commit_payroll(rows, self.company, self.user, **kw)

    def _slip(self):
        return Payslip.objects.get(employee=self.emp)

    def _line(self, code):
        return self._slip().lines.filter(component__code=code).first()

    # ── Problem 2 ───────────────────────────────────────────────────────────
    def test_commission_and_incentive_land_as_payslip_lines(self):
        """The exact defect: these must exist as lines, or the dashboard is blank."""
        rep = self._commit([_row()])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.created, 1)
        self.assertIsNotNone(self._line('COMMISSION'))
        self.assertIsNotNone(self._line('INCENTIVE'))
        self.assertEqual(self._line('COMMISSION').amount, D('1500.00'))
        self.assertEqual(self._line('INCENTIVE').amount, D('500.00'))

    def test_every_supplied_component_becomes_a_line(self):
        self._commit([_row()])
        codes = set(self._slip().lines.values_list('component__code', flat=True))
        self.assertEqual(
            codes,
            {'BASIC', 'COMMISSION', 'INCENTIVE', 'PAYE', 'PENSION_EE', 'PENSION_ER'},
        )

    def test_totals_are_recomputed_from_the_lines(self):
        """Gross = earnings only; net = gross − PAYE − employee deductions.
        20000 + 1500 + 500 = 22000; 22000 − 3000 − 1000 = 18000."""
        self._commit([_row()])
        ps = self._slip()
        self.assertEqual(ps.gross_amount, D('22000.00'))
        self.assertEqual(ps.paye_amount,  D('3000.00'))
        self.assertEqual(ps.net_amount,   D('18000.00'))
        self.assertEqual(ps.ctc_amount,   D('23000.00'))   # + company pension

    def test_register_paye_is_kept_not_re_derived(self):
        """The register is the payroll already paid — re-deriving PAYE from the
        BURS brackets would disagree with what staff actually received."""
        self._commit([_row()])
        self.assertEqual(self._line('PAYE').amount, D('3000.00'))
        self.assertEqual(self._slip().paye_amount, D('3000.00'))

    def test_file_totals_are_cross_checked_and_variance_reported(self):
        """A register whose GROSS doesn't equal its own components is flagged,
        not silently accepted."""
        rep = self._commit([_row(gross='99999.00')])
        self.assertEqual(rep.extra.get('variance_count'), 1)
        v = rep.extra['variances'][0]
        self.assertEqual(v['file_gross'], '99999.00')
        self.assertEqual(v['computed_gross'], '22000.00')

    def test_clean_register_reports_no_variance(self):
        rep = self._commit([_row()])
        self.assertNotIn('variances', rep.extra)

    # ── Period handling ─────────────────────────────────────────────────────
    def test_fy_label_never_becomes_the_payroll_period(self):
        """"FY27" names a year, not a month. If it became the period name, all
        twelve months would collide on Payslip's unique (employee, period)."""
        self._commit([_row()])
        self.assertEqual(self._slip().period.period_name, '2026-07')

    def test_period_start_is_the_first_of_the_month_not_the_last(self):
        """Reported defect: Period Start and Period End both 31/07/2026."""
        self._commit([_row()], period_start='2026-07-31', period_end='2026-07-31')
        period = self._slip().period
        self.assertEqual(period.start_date.isoformat(), '2026-07-01')
        self.assertEqual(period.end_date.isoformat(), '2026-07-31')

    def test_form_period_overrides_the_file(self):
        self._commit([_row()], period_start='2026-06-01', period_end='2026-06-30',
                     period_label='2026-06')
        self.assertEqual(self._slip().period.period_name, '2026-06')

    # ── Row hygiene ─────────────────────────────────────────────────────────
    def test_nill_employee_code_does_not_reject_the_row(self):
        """HR backfills staff numbers later; match on the name instead."""
        rep = self._commit([_row(employee_code='nill')])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.created, 1)
        self.assertEqual(self._line('COMMISSION').amount, D('1500.00'))

    def test_blank_employee_code_does_not_reject_the_row(self):
        rep = self._commit([_row(employee_code='')])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.created, 1)

    def test_name_matching_ignores_case_and_extra_spaces(self):
        rep = self._commit([_row(employee_code='', employee_name='  lerato   MODISE ')])
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.created, 1)

    def test_total_row_is_skipped_not_imported_as_a_person(self):
        """"TOTAL (72 payslips)" as an employee DOUBLES the register."""
        rep = self._commit([_row(), _row(employee_name='TOTAL (72 payslips)',
                                        employee_code='')])
        self.assertEqual(rep.created, 1)
        self.assertEqual(rep.skipped, 1)
        self.assertEqual(Payslip.objects.count(), 1)

    def test_unknown_employee_is_reported_in_plain_words(self):
        rep = self._commit([_row(employee_code='', employee_name='Nobody Here')])
        self.assertEqual(rep.created, 0)
        self.assertEqual(rep.extra.get('unmatched_employees'), ['Nobody Here'])
        self.assertIn('not an employee of ADIC', rep.errors[0])

    def test_zero_amount_does_not_park_an_empty_line(self):
        self._commit([_row(incentive='0.00')])
        self.assertIsNone(self._line('INCENTIVE'))
        self.assertIsNotNone(self._line('COMMISSION'))

    # ── Re-upload behaviour ─────────────────────────────────────────────────
    def test_reupload_updates_in_place_without_duplicating(self):
        self._commit([_row()])
        rep = self._commit([_row(commission='2500.00')])
        self.assertEqual(rep.updated, 1)
        self.assertEqual(Payslip.objects.count(), 1)
        self.assertEqual(self._line('COMMISSION').amount, D('2500.00'))
        self.assertEqual(self._slip().gross_amount, D('23000.00'))

    def test_correction_file_does_not_wipe_components_it_omits(self):
        """A file carrying only Commission must not delete Basic."""
        self._commit([_row()])
        self._commit([{
            'employee_name': 'Lerato Modise', 'period_end': '2026-07-31',
            'commission': '3000.00',
        }])
        self.assertEqual(self._line('BASIC').amount, D('20000.00'))
        self.assertEqual(self._line('COMMISSION').amount, D('3000.00'))

    def test_replace_mode_clears_the_period_first(self):
        self._commit([_row()])
        rep = self._commit([_row()], mode='replace')
        self.assertEqual(rep.extra.get('replaced_count'), 1)
        self.assertEqual(Payslip.objects.count(), 1)

    def test_missing_components_fail_loudly_rather_than_silently(self):
        PayslipComponent.objects.filter(code='COMMISSION').delete()
        rep = self._commit([_row()])
        self.assertEqual(rep.created, 0)
        self.assertIn('COMMISSION', rep.errors[0])
