"""Tests for the cost-per-productive-hour league (feature #9)."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from reporting.cost_per_hour import compute_cost_per_hour
from reporting.views import IsCostLeagueViewer


class ComputeTests(TestCase):
    def setUp(self):
        from core.models import Company
        from payroll.models import Employee, Payslip, PayrollPeriod
        from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap

        self.co = Company.objects.create(code='ADIC', name='ADIC Test')
        self.emp = Employee.objects.create(full_name='Alice A', department='Claims', company=self.co, employee_number='E-001')
        self.period = PayrollPeriod.objects.create(
            period_name='2025-11', start_date=date(2025, 11, 1), end_date=date(2025, 11, 30),
        )
        Payslip.objects.create(employee=self.emp, period=self.period, gross_amount=Decimal('10000'), company=self.co)
        TimeDoctorDailySnapshot.objects.create(
            company_id='c1', as_of=date(2025, 11, 15),
            totals={}, payload=[{'user_id': 'u1', 'name': 'Alice A', 'productive_hours': 60}],
        )
        TimeDoctorDailySnapshot.objects.create(
            company_id='c1', as_of=date(2025, 11, 16),
            totals={}, payload=[{'user_id': 'u1', 'name': 'Alice A', 'productive_hours': 40}],
        )
        TimeDoctorUserMap.objects.create(td_user_id='u1', td_name='Alice A', employee=self.emp)

    def test_cost_per_hour_math(self):
        r = compute_cost_per_hour('2025-11', company_id=self.co.id)
        self.assertEqual(len(r['departments']), 1)
        d = r['departments'][0]
        self.assertEqual(d['department'], 'Claims')
        self.assertEqual(d['total_cost_bwp'], '10000.00')
        self.assertEqual(d['productive_hours'], '100.00')   # 60 + 40 across the month
        self.assertEqual(d['cost_per_productive_hour'], '100.00')  # 10000 / 100
        self.assertEqual(r['people'][0]['full_name'], 'Alice A')

    def test_no_hours_gives_none_cph(self):
        from payroll.models import Employee, Payslip
        emp2 = Employee.objects.create(full_name='Bob B', department='Ops', company=self.co, employee_number='E-002')
        Payslip.objects.create(employee=emp2, period=self.period, gross_amount=Decimal('5000'), company=self.co)
        r = compute_cost_per_hour('2025-11', company_id=self.co.id)
        ops = [d for d in r['departments'] if d['department'] == 'Ops'][0]
        self.assertIsNone(ops['cost_per_productive_hour'])   # paid, no tracked hours

    def test_bad_month(self):
        r = compute_cost_per_hour('nonsense', company_id=self.co.id)
        self.assertIn('error', r)
        self.assertEqual(r['departments'], [])


class GateTests(TestCase):
    def _req(self, user):
        class R:
            pass
        r = R(); r.user = user
        return r

    def _user(self, title, superuser=False):
        from django.contrib.auth import get_user_model
        from core.models import UserProfile
        u = get_user_model().objects.create(username=f'u-{title}-{superuser}', is_superuser=superuser)
        UserProfile.objects.update_or_create(user=u, defaults={'title': title})
        return u

    def test_allowed_titles(self):
        perm = IsCostLeagueViewer()
        for t in ('cfo', 'executive', 'hr_manager'):
            self.assertTrue(perm.has_permission(self._req(self._user(t)), None), t)

    def test_denied_titles(self):
        perm = IsCostLeagueViewer()
        for t in ('accountant', 'finance_manager', 'operations', 'claims_manager'):
            self.assertFalse(perm.has_permission(self._req(self._user(t)), None), t)

    def test_superuser_allowed(self):
        perm = IsCostLeagueViewer()
        self.assertTrue(perm.has_permission(self._req(self._user('operations', superuser=True)), None))
