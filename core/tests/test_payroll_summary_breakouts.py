"""
core/tests/test_payroll_summary_breakouts.py

/api/v1/payroll/summary/ feeds the Payroll Dashboard. It broke out only Basic,
so a run's Commission and Incentive were invisible on the very screen Pako Kago
named in his 2026-07-29 bug report — even after the register import stored the
component lines.

These assert the endpoint returns commission + incentive per period and in the
grand total, and that a negative-stored line cannot subtract from the total.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine)


D = Decimal


class PayrollSummaryBreakoutTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('setup_payroll_components', verbosity=0)
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.user = User.objects.create_superuser('cfo-sum', 'cfo@x.co.bw', 'x')
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')

    def _slip(self, code_amounts, emp_no):
        emp = Employee.objects.create(
            company=self.company, employee_number=emp_no, full_name=f'Person {emp_no}')
        ps = Payslip.objects.create(
            employee=emp, period=self.period, company=self.company)
        comps = {c.code: c for c in PayslipComponent.objects.all()}
        for code, amt in code_amounts:
            PayslipLine.objects.create(payslip=ps, component=comps[code], amount=amt)
        ps.recompute_totals(recompute_paye=False)
        ps.save()
        return ps

    def _summary(self):
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.get('/api/v1/payroll/summary/')
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        return resp.json()

    def test_commission_and_incentive_are_returned_per_period(self):
        self._slip([('BASIC', D('20000.00')), ('COMMISSION', D('1500.00')),
                    ('INCENTIVE', D('500.00')), ('PAYE', D('3000.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['commission'], '1500.00')
        self.assertEqual(row['incentive'], '500.00')
        self.assertEqual(row['basic'], '20000.00')

    def test_totals_sum_across_employees(self):
        self._slip([('BASIC', D('20000.00')), ('COMMISSION', D('1500.00'))], 'E1')
        self._slip([('BASIC', D('10000.00')), ('COMMISSION', D('2500.00')),
                    ('INCENTIVE', D('800.00'))], 'E2')
        data = self._summary()
        self.assertEqual(data['periods'][0]['commission'], '4000.00')
        self.assertEqual(data['grand_total']['commission'], '4000.00')
        self.assertEqual(data['grand_total']['incentive'], '800.00')

    def test_absent_component_reports_zero_not_missing(self):
        """A run with no commission must return 0.00 — the dashboard formats a
        missing key as an em dash, which reads as "broken"."""
        self._slip([('BASIC', D('20000.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['commission'], '0.00')
        self.assertEqual(row['incentive'], '0.00')

    def test_negative_stored_line_does_not_subtract(self):
        """Prod stores some register lines negative; an earning breakout must use
        the magnitude or one bad row silently shrinks the dashboard total."""
        self._slip([('BASIC', D('20000.00')), ('COMMISSION', D('-1500.00'))], 'E1')
        self.assertEqual(self._summary()['periods'][0]['commission'], '1500.00')

    def test_commission_is_inside_gross_not_additional(self):
        """The footnote claims this; assert it so the claim stays true."""
        self._slip([('BASIC', D('20000.00')), ('COMMISSION', D('1500.00')),
                    ('INCENTIVE', D('500.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['gross'], '22000.00')

    def test_grand_total_still_carries_the_original_keys(self):
        """Don't break the existing tiles while adding new ones."""
        self._slip([('BASIC', D('20000.00')), ('PAYE', D('3000.00'))], 'E1')
        gt = self._summary()['grand_total']
        for key in ('headcount', 'basic', 'gross', 'paye', 'net', 'ctc',
                    'commission', 'incentive', 'allowances'):
            self.assertIn(key, gt)

    # ── Allowances (Pako Kago 2026-07-29, second pass) ─────────────────────
    # Bokani Makosha's BWP 2,000 general Allowance was inside Gross but shown
    # nowhere on the dashboard — the same defect Commission and Incentive had.
    def test_allowance_is_broken_out_on_the_dashboard(self):
        """The exact live case: Basic 8,000 + Allowance 2,000 + Health Insurance
        280 = Gross 10,280."""
        self._slip([('BASIC', D('8000.00')), ('ALLOWANCE', D('2000.00')),
                    ('HEALTH_INS_ALLOWANCE', D('280.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['allowances'], '2280.00')   # 2,000 + 280
        self.assertEqual(row['basic'], '8000.00')
        self.assertEqual(row['gross'], '10280.00')

    def test_any_non_breakout_earning_code_rolls_into_allowances(self):
        """Derived, not a hand-written list: any earning component that is not
        Basic / Commission / Incentive must land in Allowances. A new component
        must never be silently invisible again (the L2 failure class)."""
        self._slip([('BASIC', D('10000.00')), ('VEHICLE_ALLOWANCE', D('1500.00')),
                    ('FUEL_ALLOWANCE', D('500.00')), ('LEAVE_PAY', D('250.00')),
                    ('PO_ALLOWANCE', D('15000.00')), ('BONUS', D('2500.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['allowances'], '19750.00')
        self.assertEqual(row['basic'], '10000.00')

    def test_breakouts_plus_allowances_equal_gross(self):
        """The footnote claims Basic + Commission + Incentive + Allowances is
        Gross. Assert it, so the claim on the screen stays true."""
        self._slip([('BASIC', D('20000.00')), ('COMMISSION', D('1500.00')),
                    ('INCENTIVE', D('500.00')), ('VEHICLE_ALLOWANCE', D('1000.00')),
                    ('PAYE', D('3000.00'))], 'E1')
        row = self._summary()['periods'][0]
        total = (D(row['basic']) + D(row['commission'])
                 + D(row['incentive']) + D(row['allowances']))
        self.assertEqual(total, D(row['gross']))

    def test_housing_salary_sacrifice_keeps_its_sign_in_allowances(self):
        """A negative EARNING is the BURS §32 housing salary sacrifice — it
        genuinely REDUCES gross. Unlike Commission (where a negative is a bad
        register row and we take the magnitude), Allowances must keep the sign
        or the column overstates pay and stops tying to Gross. Live data has
        HOUSING_ALLOWANCE summing to −19,500."""
        self._slip([('BASIC', D('30000.00')),
                    ('HOUSING_ALLOWANCE', D('-14000.00'))], 'E1')
        row = self._summary()['periods'][0]
        self.assertEqual(row['allowances'], '-14000.00')
        self.assertEqual(row['gross'], '16000.00')
        # and it still ties
        self.assertEqual(D(row['basic']) + D(row['allowances']), D(row['gross']))

    def test_run_with_no_allowances_reports_zero_not_missing(self):
        self._slip([('BASIC', D('20000.00'))], 'E1')
        self.assertEqual(self._summary()['periods'][0]['allowances'], '0.00')

    def test_computed_marker_lines_never_land_in_allowances(self):
        """GROSS / NET / CTC exist in the component catalogue as `computed_*`
        marker kinds. Allowances is a catch-all over EARNING kinds, so if a
        marker line were ever written with an earning kind — or the catch-all
        widened — Gross would be counted twice inside its own breakdown. Pin it
        here: the endpoint has no equivalent of payslip_breakdown's
        `test_every_component_kind_is_accounted_for`."""
        ps = self._slip([('BASIC', D('20000.00')), ('ALLOWANCE', D('1000.00'))], 'E1')
        comps = {c.code: c for c in PayslipComponent.objects.all()}
        PayslipLine.objects.create(payslip=ps, component=comps['GROSS'],
                                   amount=D('21000.00'))
        row = self._summary()['periods'][0]
        self.assertEqual(row['allowances'], '1000.00')   # NOT 22,000
        self.assertEqual(row['basic'], '20000.00')

    def test_foreign_currency_slip_is_excluded_from_the_line_columns(self):
        """ADRisk pays in INR: its LINES are INR while gross/paye/net_amount are
        the BWP reporting figures. Summing those lines into the Basic /
        Commission / Incentive / Allowances columns would put INR next to a BWP
        Gross. Same deliberate skip as payslip_breakdown._compare_stored."""
        emp = Employee.objects.create(company=self.company,
                                      employee_number='FX1', full_name='FX Person')
        ps = Payslip.objects.create(
            employee=emp, period=self.period, company=self.company,
            source_currency='INR', fx_rate_to_bwp=D('0.142857'),
            source_gross=D('60000.00'), source_net=D('55800.00'),
            gross_amount=D('8571.42'), net_amount=D('7971.43'))
        comps = {c.code: c for c in PayslipComponent.objects.all()}
        PayslipLine.objects.create(payslip=ps, component=comps['BASIC'],
                                   amount=D('50000.00'))       # INR
        PayslipLine.objects.create(payslip=ps, component=comps['ALLOWANCE'],
                                   amount=D('10000.00'))       # INR
        row = self._summary()['periods'][0]
        self.assertEqual(row['allowances'], '0.00')   # the 10,000 INR stays out
        self.assertEqual(row['basic'], '0.00')        # the 50,000 INR stays out
        # …but the slip still counts in headcount and the BWP totals.
        self.assertEqual(row['headcount'], 1)
        self.assertEqual(row['gross'], '8571.42')
