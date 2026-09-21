"""payroll/test_name_matching.py — amendment employee-name resolution.

Bug (Pako 2026-07-23, batch 0fa712de): "Randy Taukobong" was matched only on
EXACT full name, so a first name ("Randy") or surname ("Taukobong") returned
"employee not found" and both amendments failed. Fix: fuzzy match on
first/surname/full name; ask to confirm when ambiguous instead of silently
failing. Also: an applied allowance (INCENTIVE, an earning) must flow into gross.

Run in CI: manage.py test payroll.test_name_matching
"""
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine)
from payroll.amendment_views import _resolve_employee


class NameMatchTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.randy = Employee.objects.create(employee_number='E100',
                                             full_name='Randy Taukobong',
                                             company=self.co, status='active')

    def test_first_name_resolves(self):
        emp, note = _resolve_employee('Randy', self.co)
        self.assertEqual(emp, self.randy); self.assertEqual(note, '')

    def test_surname_resolves(self):
        emp, note = _resolve_employee('Taukobong', self.co)
        self.assertEqual(emp, self.randy)

    def test_exact_and_reordered_full_name(self):
        self.assertEqual(_resolve_employee('randy taukobong', self.co)[0], self.randy)
        self.assertEqual(_resolve_employee('Taukobong Randy', self.co)[0], self.randy)

    def test_employee_number(self):
        self.assertEqual(_resolve_employee('E100', self.co)[0], self.randy)

    def test_ambiguous_asks_to_confirm(self):
        Employee.objects.create(employee_number='E101', full_name='Randy Mokoena',
                                company=self.co, status='active')
        emp, note = _resolve_employee('Randy', self.co)
        self.assertIsNone(emp)                 # never picks silently
        self.assertIn('ambiguous', note.lower())

    def test_not_found(self):
        emp, note = _resolve_employee('Nobody', self.co)
        self.assertIsNone(emp)
        self.assertIn('not found', note.lower())

    def test_terminated_excluded(self):
        Employee.objects.filter(id=self.randy.id).update(status=Employee.Status.TERMINATED)
        self.assertIsNone(_resolve_employee('Randy', self.co)[0])


class IncentiveFlowsToGrossTests(TestCase):
    """Bug #2: an applied allowance (INCENTIVE = an earning) must be reflected
    in gross / totals — once it lands on the payslip, recompute folds it in."""
    def test_incentive_earning_lands_in_gross(self):
        co = Company.objects.create(code='ADIC', name='Alpha Direct')
        emp = Employee.objects.create(employee_number='E200', full_name='Test P',
                                      company=co, status='active')
        period = PayrollPeriod.objects.create(period_name='2026-07',
                                              start_date='2026-07-01', end_date='2026-07-31')
        ps = Payslip.objects.create(employee=emp, period=period, company=co,
                                    status=Payslip.Status.DRAFT)
        basic = PayslipComponent.objects.create(code='BASIC', name='Basic',
                                                kind=PayslipComponent.Kind.EARNING)
        inc = PayslipComponent.objects.create(code='INCENTIVE', name='Incentive',
                                              kind=PayslipComponent.Kind.EARNING)
        PayslipLine.objects.create(payslip=ps, component=basic, amount=Decimal('10000.00'))
        PayslipLine.objects.create(payslip=ps, component=inc, amount=Decimal('1200.00'))
        ps.recompute_totals(recompute_paye=False)
        self.assertEqual(ps.gross_amount, Decimal('11200.00'))   # incentive reflected
