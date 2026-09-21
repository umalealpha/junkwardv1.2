"""hris/tests/test_leave_encashment_year_bound.py — a SETTLED (paid) leave
encashment stops deducting once the leave year restarts.

Bug proven 2026-09-20: leave_balance loaded every LeaveEncashment in
OPEN_STATUSES — which includes 'paid' — with NO date filter, and subtracted
the total from a balance that restarts each January. A 10-day encashment PAID
in March 2024 still removed 10 of the 14 days accrued by Sep 2026, and would
have gone on doing so for ever.

The missing year filter is DELIBERATE for the states still IN FLIGHT (Fable 5
review 2026-07-21: an application straddling 1 Jan must keep its reservation,
or the days can be double-spent as booked leave). Only the SETTLED row rides
in wrongly — so that is the only thing bounded here, and the in-flight
behaviour is pinned below so it cannot be "tidied" away later.
"""
import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import Company
from hris.leave_balance import balances_for_profile
from hris.leave_encash_models import LeaveEncashment
from hris.models import HRISProfile
from payroll.models import Employee


def _annual_available(profile) -> float:
    for b in balances_for_profile(profile):
        if b['code'] == 'annual':
            return float(b['available'])
    raise AssertionError('no annual leave row returned')


class SettledEncashmentYearBoundTest(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='YRB', name='Year-bound test co')
        self.emp = Employee.objects.create(
            employee_number='YB1', full_name='Year Bound',
            company=self.co, status='active', hire_date=dt.date(2020, 1, 1))
        self.profile = HRISProfile.objects.create(employee=self.emp)
        # Baseline with NO encashment on file, read from the same engine.
        self.baseline = _annual_available(self.profile)
        # Sanity: the fixture must have enough accrued days for a 10-day
        # deduction to be visible rather than clamped at zero by max(0, ...).
        self.assertGreater(self.baseline, 10.0)

    def _encashment(self, status, raised_on: dt.date):
        e = LeaveEncashment.objects.create(
            employee=self.emp, profile=self.profile, company=self.co,
            leave_type_code='annual', days=Decimal('10'), status=status)
        # created_at is auto-set; move it with an UPDATE so the row genuinely
        # belongs to the year under test.
        when = timezone.now().replace(year=raised_on.year, month=raised_on.month,
                                      day=raised_on.day, hour=9, minute=0)
        LeaveEncashment.objects.filter(pk=e.pk).update(created_at=when)
        return e

    def test_paid_encashment_from_a_prior_year_stops_deducting(self):
        self._encashment(LeaveEncashment.Status.PAID, dt.date(2024, 3, 15))
        self.assertAlmostEqual(_annual_available(self.profile), self.baseline, places=2)

    def test_paid_encashment_raised_this_year_still_deducts(self):
        self._encashment(LeaveEncashment.Status.PAID, timezone.localdate())
        self.assertAlmostEqual(_annual_available(self.profile),
                               self.baseline - 10.0, places=2)

    def test_in_flight_application_from_a_prior_year_still_reserves(self):
        # DELIBERATE — do not "fix" this. A straddling, still-open application
        # holds its days whatever year it was raised in.
        self._encashment(LeaveEncashment.Status.PENDING_CFO, dt.date(2024, 3, 15))
        self.assertAlmostEqual(_annual_available(self.profile),
                               self.baseline - 10.0, places=2)

    def test_approved_but_unpaid_from_a_prior_year_still_reserves(self):
        self._encashment(LeaveEncashment.Status.APPROVED, dt.date(2024, 3, 15))
        self.assertAlmostEqual(_annual_available(self.profile),
                               self.baseline - 10.0, places=2)
