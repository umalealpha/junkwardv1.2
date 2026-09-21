"""Oprah Mogomotsi's leave-accrual spot-check, 2026-08-10.

She confirmed the month-end timing was right and then found three things wrong.
One was a real calculation fault, two were data problems with a real code gap
underneath them.

  1. THE RATE WAS FLAT. Every anchored row on the Team Leave Report accrued 1.8
     days a month — 21/12 — because that report used the CoS policy default while
     the uploaded anchor sitting right beside it carried the employee's real
     entitlement. On prod that is 18 days for 51 people (over-credited 0.3 a
     month) and 25 days for 8 (under-credited 0.3 a month).
     Her diagnosis was the un-corrected grade entitlements. The data says
     otherwise: the uploads are correct and varied, and this report ignored them.

  2. LEAVERS KEPT ACCRUING. The three she named still read as active with no
     leaving date, so that part is HR data — but nothing in the code stopped
     accrual at the last day of service, which does bite the eleven employees who
     ARE marked terminated. It was invisible before only because an uploaded
     balance never moved at all.

  3. TWO QA ACCOUNTS were accruing leave beside real staff.
"""
import datetime as _dt
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company
from hris.leave_balance import days_out
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import Employee


def _report(user, year):
    from hris.feature_views import leave_report
    req = APIRequestFactory().get('/hris/api/leave-report/', {
        'date_from': f'{year}-01-01', 'date_to': f'{year}-12-31',
        'leave_type': 'annual'})
    force_authenticate(req, user=user)
    return leave_report(req)


class SpotCheckTests(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC',
                                                  name='Alpha Direct Insurance'))
        User = get_user_model()
        self.hr = User.objects.create_superuser('hr-spotcheck', 'hr@example.invalid', 'x')
        from django.utils import timezone
        self.today = timezone.localdate()
        # An anchor at the end of a month five months back, so whole months have
        # certainly completed however far into the month the suite runs.
        m, y = self.today.month - 5, self.today.year
        while m < 1:
            m += 12
            y -= 1
        first = _dt.date(y, m, 1)
        self.as_at = (first.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) \
            - _dt.timedelta(days=1)

    _seq = 0

    def _staff(self, name, entitlement, *, opening='0.00', term=None, test=False):
        # employee_number is unique and defaults to '', so blank rows collide.
        SpotCheckTests._seq += 1
        emp = Employee.objects.create(
            full_name=name, company=self.company,
            employee_number=f'SPOTCHK{SpotCheckTests._seq:03d}',
            termination_date=term, is_test_record=test)
        prof = HRISProfile.objects.create(employee=emp)
        LeaveOpeningBalance.objects.create(
            profile=prof, leave_type_code='annual', as_at_date=self.as_at,
            entitlement_days=D(str(entitlement)),
            opening_balance_days=D(opening), accrued_days=D(opening))
        return prof

    def _row_for(self, name, year=None):
        resp = _report(self.hr, year or self.today.year)
        self.assertEqual(resp.status_code, 200, resp.data)
        return next((r for r in resp.data['rows'] if r['employee_name'] == name), None)

    # ── 1. the rate must follow the employee's own entitlement ───────────────
    def test_an_eighteen_day_employee_does_not_accrue_at_the_twentyone_day_rate(self):
        self._staff('Eighteen Day Person', 18)
        row = self._row_for('Eighteen Day Person')
        from hris.leave_balance import completed_months
        months = completed_months(self.today, since=self.as_at)
        self.assertEqual(row['accrued'], days_out(18.0 * months / 12.0))
        self.assertNotEqual(row['accrued'], days_out(21.0 * months / 12.0),
                            'this is the flat 21-day rate Oprah found')

    def test_a_twentyfive_day_employee_accrues_at_their_own_higher_rate(self):
        self._staff('Twentyfive Day Person', 25)
        row = self._row_for('Twentyfive Day Person')
        from hris.leave_balance import completed_months
        months = completed_months(self.today, since=self.as_at)
        self.assertEqual(row['accrued'], days_out(25.0 * months / 12.0))

    def test_the_two_rates_actually_differ(self):
        """Guards a 'fix' that reads the field but still lands on one number."""
        self._staff('Low Entitlement', 18)
        self._staff('High Entitlement', 25)
        resp = _report(self.hr, self.today.year)
        by = {r['employee_name']: r['accrued'] for r in resp.data['rows']}
        self.assertLess(by['Low Entitlement'], by['High Entitlement'])

    def test_a_blank_entitlement_still_falls_back_to_the_policy_default(self):
        self._staff('Blank Entitlement', 0)
        row = self._row_for('Blank Entitlement')
        from hris.feature_views import get_leave_rules
        from hris.leave_balance import completed_months
        default = float(get_leave_rules()['annual']['days'])
        months = completed_months(self.today, since=self.as_at)
        self.assertEqual(row['accrued'], days_out(default * months / 12.0))

    # ── 2. accrual stops at the last day of service ──────────────────────────
    def test_a_leaver_stops_accruing_at_their_termination_date(self):
        left = self.as_at + _dt.timedelta(days=1)      # left just after the anchor
        self._staff('Has Left', 18, term=left)
        self._staff('Still On Payroll', 18)
        resp = _report(self.hr, self.today.year)
        by = {r['employee_name']: r['accrued'] for r in resp.data['rows']}
        self.assertIn('Has Left', by, 'a leaver still belongs on the report')
        self.assertLess(by['Has Left'], by['Still On Payroll'],
                        'a leaver must accrue less than someone still employed')

    def test_the_balance_engine_also_stops_at_the_leaving_date(self):
        from hris.leave_balance import accrual_cutoff, balances_for_profile
        left = self.as_at + _dt.timedelta(days=1)
        prof = self._staff('Balance Leaver', 24, term=left)
        self.assertEqual(accrual_cutoff(prof), left)
        row = next(b for b in balances_for_profile(prof) if b['code'] == 'annual')
        self.assertEqual(row['accrued_since_upload'], 0.0,
                         'nothing accrues after the last day of service')

    def test_somebody_still_employed_is_unaffected_by_the_leaver_rule(self):
        from hris.leave_balance import accrual_cutoff
        prof = self._staff('Still Employed', 24)
        self.assertEqual(accrual_cutoff(prof), self.today)

    # ── 3. QA accounts are not staff ─────────────────────────────────────────
    def test_a_test_record_is_absent_from_the_leave_report(self):
        self._staff('Manus Reviewer (automated QA)', 21, test=True)
        self._staff('A Real Employee', 21)
        resp = _report(self.hr, self.today.year)
        names = {r['employee_name'] for r in resp.data['rows']}
        self.assertIn('A Real Employee', names)
        self.assertNotIn('Manus Reviewer (automated QA)', names)
