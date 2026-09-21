"""FC enters staff-loan opening balances; the existing engine deducts them
(CFO 2026-08-28). Run: manage.py test payroll.test_staff_loan_opening
"""
import datetime
from decimal import Decimal
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.models import Employee, PayrollPeriod, PayslipLine
from payroll.contract_models import EmployeeLoan
from payroll.staff_loan_opening import create_opening_balances, _term_months
from payroll.loan_service import apply_loan_repayments


class OpeningBalanceTests(APITestCase):
    def setUp(self):
        self.fc = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.clerk = User.objects.create_user('clerk', email='clerk@alphadirect.co.bw')
        UserProfile.objects.create(user=self.clerk, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title='hr_manager', is_active=True)
        self.co = Company.objects.create(code='TEST', name='Test Co.')
        self.sep = PayrollPeriod.objects.create(period_name='2026-09',
            start_date=datetime.date(2026, 9, 1), end_date=datetime.date(2026, 9, 30))
        self.emp = Employee.objects.create(employee_number='E1', full_name='Alice A', company=self.co)

    def test_term_derivation(self):
        self.assertEqual(_term_months(Decimal('1000'), Decimal('300')), 4)   # ceil(3.33)
        self.assertEqual(_term_months(Decimal('900'), Decimal('300')), 3)

    def test_create_opening_balance_rate_zero(self):
        res = create_opening_balances(rows=[{'employee_id': str(self.emp.id),
            'net_balance': '1000.00', 'monthly_deduction': '300.00'}],
            start_period=self.sep, user=self.fc)
        self.assertEqual(res['created_count'], 1)
        loan = EmployeeLoan.objects.get(employee=self.emp)
        self.assertEqual(loan.annual_rate_pct, Decimal('0'))   # flat interest baked into balance
        self.assertEqual(loan.outstanding, Decimal('1000.00'))
        self.assertEqual(loan.term_months, 4)
        self.assertEqual(loan.status, EmployeeLoan.Status.ACTIVE)

    def test_duplicate_active_loan_skipped(self):
        create_opening_balances(rows=[{'employee_id': str(self.emp.id),
            'net_balance': '1000', 'monthly_deduction': '300'}], start_period=self.sep, user=self.fc)
        res = create_opening_balances(rows=[{'employee_id': str(self.emp.id),
            'net_balance': '500', 'monthly_deduction': '100'}], start_period=self.sep, user=self.fc)
        self.assertEqual(res['created_count'], 0)
        self.assertEqual(res['skipped_count'], 1)
        self.assertEqual(EmployeeLoan.objects.filter(employee=self.emp).count(), 1)

    def test_engine_deducts_the_opening_balance(self):
        create_opening_balances(rows=[{'employee_id': str(self.emp.id),
            'net_balance': '1000', 'monthly_deduction': '250'}], start_period=self.sep, user=self.fc)
        # the existing monthly engine deducts a LOAN_REPAYMENT line and reduces outstanding
        apply_loan_repayments(self.sep)
        loan = EmployeeLoan.objects.get(employee=self.emp)
        self.assertLess(loan.outstanding, Decimal('1000'))     # something was deducted
        self.assertTrue(PayslipLine.objects.filter(component__code='LOAN_REPAYMENT').exists())

    def test_endpoint_requires_finance_lead(self):
        self.client.force_authenticate(self.clerk)
        r = self.client.post(reverse('v1-payroll-staff-loans-opening'),
            {'start_period_id': str(self.sep.id),
             'rows': [{'employee_id': str(self.emp.id), 'net_balance': '1000', 'monthly_deduction': '300'}]},
            format='json')
        self.assertEqual(r.status_code, 403)

    def test_endpoint_creates_via_fc(self):
        self.client.force_authenticate(self.fc)
        r = self.client.post(reverse('v1-payroll-staff-loans-opening'),
            {'start_period_id': str(self.sep.id),
             'rows': [{'employee_id': str(self.emp.id), 'net_balance': '1000', 'monthly_deduction': '300'}]},
            format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['created_count'], 1)
        # list endpoint shows it
        g = self.client.get(reverse('v1-payroll-staff-loans'))
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()['count'], 1)
