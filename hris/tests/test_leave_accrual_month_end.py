"""Annual leave is credited at the END of a month, never on its first day.

HR reported that OMNI credited August's accrual on 01 August (Ontlametse
Mogomotsi, ref AD/HR/IA/2026/001). CoS §7.5.1 accrues annual leave monthly and
ELRA s.219 says leave accrues progressively, so the month must be worked before
it is credited: an employee resigning on 15 August has earned July, not August.

Two faults sat behind the report:
  * the engine used `timezone.now().month`, i.e. the CURRENT month number, so
    the running month was credited on day one;
  * for anyone with an uploaded LeaveOpeningBalance — nearly everyone, since HR
    loaded opening balances — `accrued` was the uploaded number verbatim and
    never moved again. That is the "no live monthly accrual engine" complaint.
"""
import datetime as _dt
from decimal import Decimal

from django.test import TestCase

from hris.leave_balance import accrued_to_date, completed_months


def _is_month_end(d):
    return (d + _dt.timedelta(days=1)).month != d.month


class CompletedMonthsTests(TestCase):
    def test_nothing_is_accrued_on_the_first_day_of_the_year(self):
        self.assertEqual(completed_months(_dt.date(2026, 1, 1)), 0)

    def test_the_running_month_is_not_credited_on_its_first_day(self):
        """The reported defect: 01 August credited August."""
        self.assertEqual(completed_months(_dt.date(2026, 8, 1)), 7)

    def test_mid_month_credits_only_finished_months(self):
        """An employee resigning on 15 August earns July, not August."""
        self.assertEqual(completed_months(_dt.date(2026, 8, 15)), 7)

    def test_the_month_is_credited_on_its_last_day(self):
        self.assertEqual(completed_months(_dt.date(2026, 8, 31)), 8)

    def test_a_full_year_is_credited_on_31_december(self):
        self.assertEqual(completed_months(_dt.date(2026, 12, 31)), 12)

    def test_february_month_end_is_recognised_in_a_leap_year(self):
        self.assertEqual(completed_months(_dt.date(2024, 2, 29)), 2)
        self.assertEqual(completed_months(_dt.date(2024, 2, 28)), 1)

    def test_thirty_day_month_end_is_recognised(self):
        self.assertEqual(completed_months(_dt.date(2026, 4, 30)), 4)
        self.assertEqual(completed_months(_dt.date(2026, 4, 29)), 3)

    def test_counting_from_a_mid_month_upload_date(self):
        """An opening balance as at 31 March accrues April at 30 April."""
        since = _dt.date(2026, 3, 31)
        self.assertEqual(completed_months(_dt.date(2026, 4, 15), since=since), 0)
        self.assertEqual(completed_months(_dt.date(2026, 4, 30), since=since), 1)
        self.assertEqual(completed_months(_dt.date(2026, 7, 31), since=since), 4)

    def test_a_date_before_the_start_accrues_nothing(self):
        self.assertEqual(
            completed_months(_dt.date(2026, 2, 1), since=_dt.date(2026, 6, 1)), 0)

    def test_one_twelfth_per_completed_month(self):
        # 25 days entitlement, 7 months complete → 14.58 (25 × 7/12 = 14.5833).
        # Was asserted as 14.6 until the EXCO no-round-up change of 2026-08-11:
        # a leave figure is now truncated, never rounded up, because 14.6 claims
        # a slice of a day the seven completed months had not earned.
        self.assertEqual(accrued_to_date(25.0, _dt.date(2026, 8, 1)), 14.58)
        # month end adds the eighth twelfth → 16.66 (25 × 8/12 = 16.6667)
        self.assertEqual(accrued_to_date(25.0, _dt.date(2026, 8, 31)), 16.66)


class UploadedOpeningBalanceAccruesForwardTests(TestCase):
    """The balance engine must move an uploaded opening balance forward."""

    def setUp(self):
        from core.models import Company
        from hris.models import HRISProfile
        from payroll.models import Employee
        company = (Company.objects.filter(code='ADIC').first()
                   or Company.objects.create(code='ADIC', name='Alpha Direct Insurance'))
        emp = Employee.objects.create(full_name='Accrual Tester', company=company)
        self.profile = HRISProfile.objects.create(employee=emp)

    def _annual(self):
        from hris.leave_balance import balances_for_profile
        return next(b for b in balances_for_profile(self.profile)
                    if b['code'] == 'annual')

    def test_an_uploaded_balance_gains_the_months_since_its_as_at_date(self):
        from django.utils import timezone
        from hris.models import LeaveOpeningBalance
        today = timezone.now().date()
        # As at the end of the month four months back, so four whole months have
        # since completed regardless of when this test runs inside a month.
        month = today.month - 5
        year = today.year
        while month < 1:
            month += 12
            year -= 1
        as_at = _dt.date(year, month, 1)
        # last day of that month
        as_at = (as_at.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)
        LeaveOpeningBalance.objects.create(
            profile=self.profile, leave_type_code='annual', as_at_date=as_at,
            entitlement_days=Decimal('24.00'),
            opening_balance_days=Decimal('6.00'),
            accrued_days=Decimal('6.00'),
        )
        expected_since = accrued_to_date(24.0, today, since=as_at)
        row = self._annual()
        self.assertGreater(expected_since, 0,
                           'the fixture must span at least one completed month')
        self.assertEqual(row['accrued_since_upload'], expected_since)
        self.assertEqual(row['accrued'], round(6.0 + expected_since, 1),
                         'uploaded accrued must move forward, not sit frozen')
        self.assertEqual(row['available'], round(6.0 + expected_since, 1))


class TeamLeaveReportCountsCompletedMonthsTests(TestCase):
    """The Team Leave Report is a THIRD derivation of the same accrual.

    The balance engine and the apply-time gate were both corrected for bug
    60fcbd4a while this report — the page HR reads and uploads from — still did
    `min(date_to.month, today.month)`, crediting the running month on its first
    day. On 10 August it reported 8 accrual months where the other two said 7.
    Fixing one surface and leaving a sibling on the old formula is how the same
    bug gets reported a second time.
    """

    def test_the_report_counts_only_months_that_have_finished(self):
        from django.utils import timezone

        from hris.feature_views import leave_report
        from rest_framework.test import APIRequestFactory, force_authenticate

        from core.models import Company, UserProfile
        from django.contrib.auth import get_user_model
        from hris.models import HRISProfile
        from payroll.models import Employee

        company = (Company.objects.filter(code='ADIC').first()
                   or Company.objects.create(code='ADIC', name='Alpha Direct Insurance'))
        emp = Employee.objects.create(full_name='Report Reader', company=company)
        HRISProfile.objects.create(employee=emp)
        User = get_user_model()
        hr = User.objects.create_superuser('hr-report-accrual', 'hr@example.invalid', 'x')

        today = timezone.now().date()
        req = APIRequestFactory().get('/hris/api/leave-report/', {
            'date_from': f'{today.year}-01-01',
            'date_to': f'{today.year}-12-31',
            'leave_type': 'annual',
        })
        force_authenticate(req, user=hr)
        resp = leave_report(req)
        self.assertEqual(resp.status_code, 200, resp.data)

        rows = [r for r in resp.data.get('rows', [])
                if r.get('employee_name') == 'Report Reader']
        self.assertTrue(rows, f'the employee must appear in the report: {resp.data}')
        row = rows[0]

        from hris.feature_views import get_leave_rules
        entitlement = float(get_leave_rules()['annual']['days'])
        expected = accrued_to_date(entitlement, today)
        self.assertEqual(row['accrued'], expected,
                         'the report must credit only COMPLETED months, the same '
                         'as the balance engine and the apply gate')
        # And prove that is genuinely fewer than the old formula on any day that
        # is not a month end — otherwise this test would pass on the old code.
        if not _is_month_end(today):
            old_formula = round(entitlement * today.month / 12.0, 1)
            self.assertLess(row['accrued'], old_formula,
                            'mid-month, the running month must NOT be credited')
