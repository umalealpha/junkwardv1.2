"""payroll/test_recompute_signs.py — recompute_totals must be sign-robust.

Bug (Kago / Legakwa 2026-07-23): the payroll import stores employee deductions
as NEGATIVE amounts, but recompute_totals did `gross -= amt`, so
`gross - (-993.50)` ADDED the contribution to gross — inflating GROSS/PAYE/NET
and driving TOTAL DEDUCTIONS negative. This pins the fix:
  - GROSS is earnings only,
  - every deduction is taken by magnitude (abs) regardless of stored sign,
  - a post-tax deduction reduces NET only.

Run in CI: manage.py test payroll.test_recompute_signs
"""
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine)


class RecomputeSignTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='QIH', name='Quantum IH')
        self.emp = Employee.objects.create(employee_number='E1', full_name='Test Person',
                                           company=self.co, status='active')
        self.period = PayrollPeriod.objects.create(period_name='2026-07',
                                                   start_date='2026-07-01', end_date='2026-07-31')
        self.ps = Payslip.objects.create(employee=self.emp, period=self.period,
                                         company=self.co, status=Payslip.Status.DRAFT)

        def comp(code, kind):
            return PayslipComponent.objects.create(code=code, name=code, kind=kind)
        K = PayslipComponent.Kind
        basic  = comp('BASIC', K.EARNING)
        allow  = comp('INTERNET_ALLOWANCE', K.EARNING)
        med    = comp('MEDICAL_AID_EE', K.EMPLOYEE_DEDUCTION)   # post-tax (after fix)
        loan   = comp('LOAN_DEDUCTION', K.EMPLOYEE_DEDUCTION)
        paye   = comp('PAYE', K.TAX)
        # Deductions stored NEGATIVE — the exact convention that broke gross.
        PayslipLine.objects.create(payslip=self.ps, component=basic, amount=Decimal('35000.00'))
        PayslipLine.objects.create(payslip=self.ps, component=allow, amount=Decimal('500.00'))
        PayslipLine.objects.create(payslip=self.ps, component=med,  amount=Decimal('-993.50'))
        PayslipLine.objects.create(payslip=self.ps, component=loan, amount=Decimal('-3433.33'))
        PayslipLine.objects.create(payslip=self.ps, component=paye, amount=Decimal('6712.50'))

    def test_gross_is_earnings_only_and_net_correct(self):
        # recompute_paye=False → PAYE is taken from the TAX line as-is (abs).
        self.ps.recompute_totals(recompute_paye=False)
        self.assertEqual(self.ps.gross_amount, Decimal('35500.00'))   # NOT 36493.50
        self.assertEqual(self.ps.paye_amount,  Decimal('6712.50'))
        # net = 35500 - 6712.50 - (993.50 + 3433.33) = 24360.67  (Legakwa's expected)
        self.assertEqual(self.ps.net_amount, Decimal('24360.67'))

    def test_negative_stored_deduction_never_inflates_gross(self):
        # The regression: a negative-stored deduction must never be added to gross.
        self.ps.recompute_totals(recompute_paye=False)
        self.assertLess(self.ps.gross_amount, Decimal('36000.00'))
