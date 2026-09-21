"""Tests for the Nexus Staff Portal self-service endpoints (CFO 2026-07-14):
my-payslips returns ONLY the caller's own rows; phonebook lists active staff."""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company
from payroll.models import Employee, Payslip, PayrollPeriod


class StaffMobileTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TST', name='Test Co')
        cls.me = User.objects.create_user('me', 'me@alphadirect.co.bw', 'x')
        cls.other = User.objects.create_user('other', 'o@alphadirect.co.bw', 'x')
        cls.emp_me = Employee.objects.create(
            employee_number='E1', full_name='Me Person', company=cls.co,
            user=cls.me, phone='71000001', job_title='Analyst', department='Finance')
        cls.emp_other = Employee.objects.create(
            employee_number='E2', full_name='Other Person', company=cls.co,
            user=cls.other, phone='71000002')
        period = PayrollPeriod.objects.create(
            period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30')
        Payslip.objects.create(employee=cls.emp_me, period=period, company=cls.co,
                               gross_amount='10000.00', paye_amount='1000.00',
                               net_amount='9000.00')
        Payslip.objects.create(employee=cls.emp_other, period=period, company=cls.co,
                               gross_amount='99999.00', paye_amount='9999.00',
                               net_amount='90000.00')

    def test_my_payslips_only_mine(self):
        self.client.force_authenticate(user=self.me)
        r = self.client.get(reverse('v1-my-payslips'))
        self.assertEqual(r.status_code, 200)
        slips = r.json()['payslips']
        self.assertEqual(len(slips), 1)
        self.assertEqual(slips[0]['net'], '9000.00')     # never the other person's

    def test_no_payroll_record_is_calm(self):
        loner = User.objects.create_user('loner', 'l@alphadirect.co.bw', 'x')
        self.client.force_authenticate(user=loner)
        r = self.client.get(reverse('v1-my-payslips'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['payslips'], [])

    def test_phonebook_lists_active_and_searches(self):
        self.client.force_authenticate(user=self.me)
        r = self.client.get(reverse('v1-staff-phonebook'))
        self.assertEqual(r.status_code, 200)
        names = [p['name'] for p in r.json()['people']]
        self.assertIn('Me Person', names)
        r2 = self.client.get(reverse('v1-staff-phonebook') + '?q=finance')
        self.assertEqual([p['name'] for p in r2.json()['people']], ['Me Person'])

    def test_phonebook_hides_terminated(self):
        Employee.objects.create(employee_number='E3', full_name='Gone Person',
                                company=self.co, status=Employee.Status.TERMINATED)
        self.client.force_authenticate(user=self.me)
        names = [p['name'] for p in self.client.get(reverse('v1-staff-phonebook')).json()['people']]
        self.assertNotIn('Gone Person', names)
