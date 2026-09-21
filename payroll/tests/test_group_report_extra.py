from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from agent_portal.models import Agent, CommissionCycle, CommissionLine
from core.models import AuditLog, Company, Currency
from payroll.models import (
    Employee,
    GroupPayrollLine,
    GroupPayrollSnapshot,
    Payslip,
    PayslipComponent,
    PayslipLine,
    PayrollPeriod,
)

User = get_user_model()


class GroupReportExtraViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP',
            defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )

        cls.company1 = Company.objects.create(code='TST1', name='Test Co One')
        cls.company2 = Company.objects.create(code='TST2', name='Test Co Two')

        cls.period1 = PayrollPeriod.objects.create(
            period_name='2026-08',
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        cls.period2 = PayrollPeriod.objects.create(
            period_name='2026-09',
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
        )

        cls.user = User.objects.create_user(
            username='viewer',
            email='viewer@alphadirect.co.bw',
        )
        cls.api = APIClient()
        cls.api.force_authenticate(cls.user)

    def _line(self, snap, company, status, ctc, by_department):
        return GroupPayrollLine.objects.create(
            snapshot=snap,
            company=company,
            status=status,
            headcount=sum(v['headcount'] for v in by_department.values()) if by_department else 0,
            basic=Decimal('0.00'),
            incentive=Decimal('0.00'),
            commission=Decimal('0.00'),
            employer_contrib=Decimal('0.00'),
            gross=Decimal('0.00'),
            paye=Decimal('0.00'),
            net=Decimal('0.00'),
            ctc=ctc,
            by_department=by_department,
        )

    def _component(self, code, name, sort_order):
        return PayslipComponent.objects.create(
            code=code,
            name=name,
            kind='earning',
            sort_order=sort_order,
            is_active=True,
            is_taxable=True,
            posting_account_code='',
            computation_kind='flat',
        )

    def test_trend_returns_two_points_oldest_first(self):
        snap1 = GroupPayrollSnapshot.objects.create(
            period=self.period1,
            version=1,
            totals={'ctc': '1000.00', 'headcount': 2},
        )
        snap2 = GroupPayrollSnapshot.objects.create(
            period=self.period2,
            version=1,
            totals={'ctc': '2000.00', 'headcount': 3},
        )

        self._line(snap1, self.company1, 'final', Decimal('1000.00'), {})
        self._line(snap2, self.company1, 'final', Decimal('2000.00'), {})

        with patch('payroll.group_report_extra_views.can_view_group_report', return_value=True):
            response = self.api.get('/api/v1/payroll/group-report/trend/')

        self.assertEqual(response.status_code, 200)
        points = response.data['points']
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]['period'], '2026-08')
        self.assertEqual(points[1]['period'], '2026-09')
        self.assertEqual(points[0]['ctc'], '1000.00')
        self.assertEqual(points[1]['ctc'], '2000.00')
        self.assertEqual(points[0]['by_company']['Test Co One'], '1000.00')
        self.assertEqual(points[1]['by_company']['Test Co One'], '2000.00')

    def test_departments_sums_across_companies_and_excludes_not_run(self):
        snap = GroupPayrollSnapshot.objects.create(
            period=self.period1,
            version=1,
            totals={'ctc': '1500.00', 'headcount': 3},
        )

        self._line(
            snap,
            self.company1,
            'final',
            Decimal('1000.00'),
            {'Sales': {'headcount': 2, 'ctc': '1000.00'}},
        )
        self._line(
            snap,
            self.company2,
            'final',
            Decimal('500.00'),
            {
                'Sales': {'headcount': 1, 'ctc': '500.00'},
                'IT': {'headcount': 0, 'ctc': '0.00'},
            },
        )
        self._line(
            snap,
            self.company1,
            'not_run',
            Decimal('999.00'),
            {'Sales': {'headcount': 5, 'ctc': '999.00'}},
        )

        with patch('payroll.group_report_extra_views.can_view_group_report', return_value=True):
            response = self.api.get(
                '/api/v1/payroll/group-report/departments/?period=2026-08'
            )

        self.assertEqual(response.status_code, 200)
        rows = response.data['rows']
        self.assertEqual(len(rows), 2)

        sales = next(row for row in rows if row['department'] == 'Sales')
        it = next(row for row in rows if row['department'] == 'IT')

        self.assertEqual(sales['headcount'], 3)
        self.assertEqual(sales['ctc'], '1500.00')
        self.assertEqual(it['headcount'], 0)
        self.assertEqual(it['ctc'], '0.00')

    def test_people_returns_person_rows_and_writes_audit_log(self):
        snap = GroupPayrollSnapshot.objects.create(
            period=self.period1,
            version=1,
            totals={'ctc': '1500.00', 'headcount': 1},
        )

        employee = Employee.objects.create(
            employee_number='E001',
            full_name='Alice Molefe',
            email='alice@alphadirect.co.bw',
            company=self.company1,
            department='Sales',
            job_title='Sales Agent',
            status='active',
        )

        basic_comp = self._component('BASIC', 'Basic', 1)
        incentive_comp = self._component('INCENTIVE', 'Incentive', 2)
        commission_comp = self._component('COMMISSION', 'Commission', 3)

        payslip = Payslip.objects.create(
            period=self.period1,
            company=self.company1,
            employee=employee,
            status='final',
            gross_amount=Decimal('1300.00'),
            ctc_amount=Decimal('1500.00'),
        )
        PayslipLine.objects.create(
            payslip=payslip,
            component=basic_comp,
            amount=Decimal('1000.00'),
        )
        PayslipLine.objects.create(
            payslip=payslip,
            component=incentive_comp,
            amount=Decimal('200.00'),
        )
        PayslipLine.objects.create(
            payslip=payslip,
            component=commission_comp,
            amount=Decimal('100.00'),
        )

        with patch('payroll.group_report_extra_views.can_view_group_report', return_value=True):
            response = self.api.get(
                '/api/v1/payroll/group-report/people/'
                f'?period=2026-08&company={self.company1.pk}'
            )

        self.assertEqual(response.status_code, 200)
        rows = response.data['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Alice Molefe')
        self.assertEqual(rows[0]['department'], 'Sales')
        self.assertEqual(rows[0]['job_title'], 'Sales Agent')
        self.assertEqual(rows[0]['basic'], '1000.00')
        self.assertEqual(rows[0]['incentive'], '200.00')
        self.assertEqual(rows[0]['commission'], '100.00')
        self.assertEqual(rows[0]['gross'], '1300.00')
        self.assertEqual(rows[0]['ctc'], '1500.00')

        self.assertTrue(
            AuditLog.objects.filter(
                table_name='payroll_payslip',
                action='read',
                record_id=f'2026-08:{self.company1.pk}',
                user=self.user,
                description='Group payroll person-level view',
            ).exists()
        )

    def test_non_viewer_gets_403(self):
        no_view_user = User.objects.create_user(
            username='noview',
            email='noview@alphadirect.co.bw',
        )
        client = APIClient()
        client.force_authenticate(no_view_user)

        with patch('payroll.group_report_extra_views.can_view_group_report', return_value=False):
            response = client.get('/api/v1/payroll/group-report/trend/')

        self.assertEqual(response.status_code, 403)

    def test_agent_commissions_sums_only_payable_lines_of_overlapping_cycle(self):
        agent = Agent.objects.create(name='Agent A')
        cycle = CommissionCycle.objects.create(
            label='Cycle 1',
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            status='approved',
        )

        CommissionLine.objects.create(
            cycle=cycle,
            agent=agent,
            stream='policy',
            basis=Decimal('1000.00'),
            commission=Decimal('100.00'),
            payable=True,
        )
        CommissionLine.objects.create(
            cycle=cycle,
            agent=agent,
            stream='policy',
            basis=Decimal('1000.00'),
            commission=Decimal('50.00'),
            payable=False,
        )

        with patch('payroll.group_report_extra_views.can_view_group_report', return_value=True):
            response = self.api.get(
                '/api/v1/payroll/group-report/agent-commissions/?period=2026-08'
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], '100.00')
        self.assertEqual(len(response.data['cycles']), 1)
        self.assertEqual(response.data['cycles'][0]['label'], 'Cycle 1')
        self.assertEqual(response.data['cycles'][0]['status'], 'approved')
        self.assertEqual(response.data['cycles'][0]['total'], '100.00')
        self.assertEqual(
            response.data['note'],
            'UniCoin sales agents — paid outside payroll',
        )

    def test_prior_skips_a_period_with_no_saved_report(self):
        # Prod 19-Sep: an off-cycle period (UniCoin 23-Jul..24-Aug) sat between July and
        # August, had no group report, and the tile said "no earlier month" despite July.
        from payroll.group_report import snapshot_dict
        july = PayrollPeriod.objects.create(
            period_name='2026-07', start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))
        PayrollPeriod.objects.create(
            period_name='UC 2026-08', start_date=date(2026, 7, 23), end_date=date(2026, 8, 24))
        GroupPayrollSnapshot.objects.create(period=july, version=1, totals={'ctc': '900.00'})
        aug = GroupPayrollSnapshot.objects.create(
            period=self.period1, version=1, totals={'ctc': '1000.00'})

        self.assertEqual(snapshot_dict(aug, detail=False)['prior'],
                         {'period': '2026-07', 'totals': {'ctc': '900.00'}})

    def test_trend_keeps_the_newest_months_when_over_the_limit(self):
        from payroll.group_report_extra import trend
        july = PayrollPeriod.objects.create(
            period_name='2026-07', start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))
        for period in (july, self.period1, self.period2):
            snap = GroupPayrollSnapshot.objects.create(period=period, version=1, totals={'ctc': '1.00'})
            self._line(snap, self.company1, 'final', Decimal('1.00'), {})

        self.assertEqual([p['period'] for p in trend(limit=2)], ['2026-08', '2026-09'])
