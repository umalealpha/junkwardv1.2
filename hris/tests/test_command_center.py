from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from core.models import OmniTask
from hris.command_center import compute_month
from hris.command_center_views import command_center as command_center_view
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Company, Employee, Payslip, PayrollPeriod
from hris.models import HRISProfile, WorkdayJustification


User = get_user_model()


class CommandCenterTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Alpha Direct WCC', code='WCCA')
        self.other_company = Company.objects.create(name='Other Co WCC', code='WCCO')

        self.user = User.objects.create_user(
            username='cfo',
            email='cfo@alpha.co.bw',
        )
        self.employee = Employee.objects.create(
            employee_number='WCC-1',
            full_name='Tselane Kgosi',
            email='tselane@alphadirect.co.bw',
            status=Employee.Status.ACTIVE,
            company=self.company,
            department='Finance',
            job_title='Accountant',
            user=self.user,
        )
        self.profile = HRISProfile.objects.create(employee=self.employee)

        self.qs = Employee.objects.filter(pk=self.employee.pk)

        self.period = PayrollPeriod.objects.create(
            period_name='2026-06',
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )

        self.flight_risk_patcher = patch(
            'hris.command_center.compute_flight_risk',
            return_value=[],
        )
        self.mock_flight_risk = self.flight_risk_patcher.start()
        self.addCleanup(self.flight_risk_patcher.stop)

    def test_snapshot_hours_summed(self):
        TimeDoctorDailySnapshot.objects.create(
            company_id=str(self.company.pk),
            as_of=date(2026, 6, 1),
            totals={'productive_pct': 80},
            payload=[{'email': 'tselane@alphadirect.co.bw', 'tracked_seconds': 3600}],
        )
        TimeDoctorDailySnapshot.objects.create(
            company_id=str(self.company.pk),
            as_of=date(2026, 6, 2),
            totals={'productive_pct': 90},
            payload=[{'email': 'tselane@alphadirect.co.bw', 'tracked_seconds': 1800}],
        )

        result = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
        )
        self.assertEqual(result['td']['total_tracked_hours'], 1.5)

    def test_stale_flag_when_latest_snapshot_before_month_end(self):
        TimeDoctorDailySnapshot.objects.create(
            company_id=str(self.company.pk),
            as_of=date(2026, 6, 28),
            totals={'productive_pct': 80},
            payload=[],
        )
        result = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
        )
        self.assertTrue(result['td']['stale'])

    def test_no_tracking_counts_only_mapped_active_employees(self):
        inactive = Employee.objects.create(
            employee_number='WCC-2',
            full_name='Inactive Person',
            email='inactive@alphadirect.co.bw',
            status=Employee.Status.TERMINATED,
            company=self.company,
        )
        snapshot = TimeDoctorDailySnapshot.objects.create(
            company_id=str(self.company.pk),
            as_of=date(2026, 6, 3),  # Wednesday
            totals={'productive_pct': 60},
            payload=[
                {'email': 'tselane@alphadirect.co.bw', 'tracked_seconds': 0},
                {'email': 'inactive@alphadirect.co.bw', 'tracked_seconds': 0},
                {'email': 'nobody@example.com', 'tracked_seconds': 0},
            ],
        )
        result = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
        )
        self.assertEqual(result['td']['no_tracking_person_days'], 1)

    def test_shortfall_hours_sum(self):
        WorkdayJustification.objects.create(
            profile=self.profile,
            work_date=date(2026, 6, 10),
            required_hours=8,
            tracked_hours=5,
            reason=WorkdayJustification.Reason.OTHER,
            status=WorkdayJustification.Status.UNJUSTIFIED,
        )
        WorkdayJustification.objects.create(
            profile=self.profile,
            work_date=date(2026, 6, 11),
            required_hours=8,
            tracked_hours=2,
            reason=WorkdayJustification.Reason.OTHER,
            status=WorkdayJustification.Status.JUSTIFIED,
        )
        result = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
        )
        self.assertEqual(Decimal(result['shortfall']['hours_short']), Decimal('9.00'))

    def test_salary_aggregate_only_unless_named(self):
        row = {
            'name': 'Tselane Kgosi',
            'profile_id': str(self.employee.pk),
            'department': 'Finance',
            'job_title': 'Accountant',
            'score': 80,
            'band': 'high',
            'reasons': [],
        }
        self.mock_flight_risk.return_value = [row]
        Payslip.objects.create(
            employee=self.employee,
            period=self.period,
            company=self.company,
            gross_amount=Decimal('10000'),
            status=Payslip.Status.APPROVED,
        )
        result_agg = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
            named=False,
        )
        # One person: the aggregate would be their pay, so it is hidden unless named.
        self.assertIsNone(result_agg['salary_at_risk']['bwp'])
        self.assertIn('fewer than', result_agg['salary_at_risk']['basis'])
        self.assertEqual(result_agg['salary_at_risk']['people'], 1)
        self.assertNotIn('breakdown', result_agg['salary_at_risk'])

        result_named = compute_month(
            2026, 6, employees_qs=self.qs,
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
            named=True,
        )
        self.assertIn('breakdown', result_named['salary_at_risk'])
        self.assertEqual(result_named['salary_at_risk']['breakdown'][0]['gross'], '10000')

    def test_non_whitelisted_user_denied(self):
        factory = APIRequestFactory()
        with patch(
            'hris.command_center_views._deny_if_not_whitelisted',
            return_value=Response(status=403),
        ) as mock_deny:
            request = factory.get('/api/v1/hris/command-center/')
            request.user = self.user
            response = command_center_view(request)
            self.assertEqual(response.status_code, 403)
            mock_deny.assert_called_once()

    def test_company_scope_respected(self):
        other_emp = Employee.objects.create(
            employee_number='WCC-3',
            full_name='Other Co Employee',
            email='other@otherco.co.bw',
            status=Employee.Status.ACTIVE,
            company=self.other_company,
        )
        snapshot = TimeDoctorDailySnapshot.objects.create(
            company_id=str(self.company.pk),
            as_of=date(2026, 6, 4),  # Thursday
            totals={'productive_pct': 70},
            payload=[
                {'email': 'tselane@alphadirect.co.bw', 'tracked_seconds': 0},
                {'email': 'other@otherco.co.bw', 'tracked_seconds': 0},
            ],
        )
        result = compute_month(
            2026, 6,
            employees_qs=Employee.objects.filter(company=self.company),
            now=datetime(2026, 7, 1, tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone')),
        )
        self.assertEqual(result['cross_check']['employees_in_scope'], 1)
        self.assertEqual(result['td']['no_tracking_person_days'], 1)

    def test_default_month_last_full_month(self):
        factory = APIRequestFactory()
        with patch(
            'hris.command_center_views._deny_if_not_whitelisted',
            return_value=None,
        ), patch(
            'hris.command_center_views.apply_company_scope',
            return_value=Employee.objects.none(),
        ), patch(
            'hris.command_center_views.compute_month',
            return_value={'month': '2026-06', 'named': False, 'placeholder': True},
        ) as mock_compute, patch(
            'django.utils.timezone.now',
            return_value=datetime(
                2026, 7, 5, 12, 0, 0,
                tzinfo=__import__('zoneinfo').ZoneInfo('Africa/Gaborone'),
            ),
        ):
            request = factory.get('/api/v1/hris/command-center/')
            request.user = self.user
            response = command_center_view(request)
            args, kwargs = mock_compute.call_args
            self.assertEqual(args[0], 2026)
            self.assertEqual(args[1], 6)
            self.assertEqual(response.data['month'], '2026-06')
            self.assertEqual(response.data['named'], False)


class CommandCenterLiveDataLessonsTests(TestCase):
    """Found by the read-only live run on 18-Sep-2026."""

    def test_an_impossible_snapshot_day_is_left_out(self):
        from hris.command_center import _usable_snapshots
        ok = TimeDoctorDailySnapshot(as_of=date(2026, 8, 3), payload=[{'tracked_seconds': 8 * 3600}])
        bulk = TimeDoctorDailySnapshot(as_of=date(2026, 6, 16), payload=[{'tracked_seconds': 300 * 3600}])
        usable, excluded = _usable_snapshots([ok, bulk])
        self.assertEqual(usable, [ok])
        self.assertEqual(excluded, ['2026-06-16'])


class CommandCenterJudgeFindingsTests(TestCase):
    def test_absurd_month_is_a_400_not_a_500(self):
        from rest_framework.test import APIClient
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_superuser('wccadmin', 'wccadmin@alphadirect.co.bw', 'x')
        c = APIClient(); c.force_authenticate(u)
        r = c.get('/api/v1/hris/command-center/?month=0-05')
        self.assertEqual(r.status_code, 400)
