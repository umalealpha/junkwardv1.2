"""
core/tests/test_payslip_email_body.py

The payslip email was plain text with newlines swapped for <br/> — unbranded,
and showing no figures at all, so staff had to open the PDF to see whether
their commission made it into the run. CFO ask 2026-07-29: design it properly.

These assert the house brand and, specifically, that Commission and Incentive
appear as named lines with their amounts.
"""

from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import Employee, PayrollPeriod, Payslip, PayslipComponent, PayslipLine
from payroll.payslip_email import _body_html


D = Decimal


class PayslipEmailBodyTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('setup_payroll_components', verbosity=0)
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.emp = Employee.objects.create(
            company=cls.company, employee_number='EMP_001', full_name='Lerato Modise',
        )
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31',
        )

    def setUp(self):
        self.ps = Payslip.objects.create(
            employee=self.emp, period=self.period, company=self.company,
        )
        comps = {c.code: c for c in PayslipComponent.objects.all()}
        for code, amount in (
            ('BASIC', D('20000.00')),
            ('COMMISSION', D('1500.00')),
            ('INCENTIVE', D('500.00')),
            ('PAYE', D('3000.00')),
            ('PENSION_EE', D('1000.00')),
            ('PENSION_ER', D('1000.00')),
        ):
            PayslipLine.objects.create(payslip=self.ps, component=comps[code], amount=amount)
        self.ps.recompute_totals(recompute_paye=False)
        self.ps.save()
        self.html = _body_html(self.ps, 'Lerato')

    # ── The specific ask ────────────────────────────────────────────────────
    def test_commission_and_incentive_are_named_with_amounts(self):
        self.assertIn('Commission', self.html)
        self.assertIn('1,500.00', self.html)
        self.assertIn('Incentive', self.html)
        self.assertIn('500.00', self.html)

    def test_every_earning_and_deduction_line_is_listed(self):
        for label in ('Basic Salary', 'Commission', 'Incentive', 'PAYE',
                      'Pension Employee Contribution'):
            with self.subTest(label=label):
                self.assertIn(label, self.html)

    def test_company_contribution_is_not_shown_as_the_employee_s_money(self):
        """Pension ER is cost to company — showing it in a pay summary invites
        "why was this deducted from me?"."""
        self.assertNotIn('Pension Company Contribution', self.html)

    def test_deductions_render_as_negative_not_raw_sign(self):
        self.assertIn('&minus;BWP 3,000.00', self.html)

    def test_net_pay_is_the_highlighted_total(self):
        self.assertIn('Net pay', self.html)
        self.assertIn('BWP 18,000.00', self.html)

    # ── House brand ─────────────────────────────────────────────────────────
    def test_uses_the_house_navy_and_orange(self):
        self.assertIn('#0D1B2A', self.html)
        self.assertIn('#F4A623', self.html)

    def test_title_uses_book_antiqua(self):
        self.assertIn('Book Antiqua', self.html)

    def test_period_is_in_the_header(self):
        self.assertIn('2026-07', self.html)

    def test_greets_the_employee_by_first_name(self):
        self.assertIn('Hi Lerato', self.html)

    def test_is_html_not_br_joined_plain_text(self):
        self.assertIn('<div', self.html)
        self.assertIn('<table', self.html)

    # ── Robustness ──────────────────────────────────────────────────────────
    def test_payslip_with_no_lines_still_renders_a_summary(self):
        """Headline-only slips (older summary imports) must not render blank."""
        bare_period = PayrollPeriod.objects.create(
            period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30',
        )
        bare = Payslip.objects.create(
            employee=self.emp, period=bare_period, company=self.company,
            gross_amount=D('10000.00'), paye_amount=D('900.00'),
            net_amount=D('9100.00'),
        )
        html = _body_html(bare, 'Lerato')
        self.assertIn('BWP 10,000.00', html)
        self.assertIn('BWP 9,100.00', html)
        self.assertIn('Net pay', html)

    def test_employee_name_is_escaped(self):
        html = _body_html(self.ps, '<script>x</script>')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_zero_amount_lines_are_omitted(self):
        comps = {c.code: c for c in PayslipComponent.objects.all()}
        PayslipLine.objects.create(
            payslip=self.ps, component=comps['FUEL_ALLOWANCE'], amount=D('0.00'))
        html = _body_html(self.ps, 'Lerato')
        self.assertNotIn('Fuel Allowance', html)
