"""Annual leave must accrue from the day a person JOINED, never from 1 January.

CFO, 2026-09-07. Amantle Adelaide Thake — a Client Onboarding Intern created on
the payroll on 20 Aug 2026 — showed 14.00 annual days available, and a leave
encashment for all 14 (BWP 1,652.84) was raised and valued against that figure.
14.00 is exactly 21 / 12 x 8: a full year-to-date accrual counted from
1 January, for somebody who had been employed for weeks.

`balances_for_profile` called `accrued_to_date` with no `since`, so it defaulted
to the start of the calendar year and never consulted `hire_date` at all. That
hit everyone, not only the 73 active employees whose hire date is blank: on the
day this was found, a developer hired on 3 September was likewise shown 14.00
days on his fourth day of service.

Each test below FAILS on the unfixed engine — the balance comes back as the full
year-to-date figure — which is the point of writing them.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from hris.leave_balance import balances_for_profile
from hris.models import HRISProfile, LeaveOpeningBalance
from hris.leave_encash_service import apply_encashment
from payroll.models import Employee


def _annual(profile) -> Decimal:
    for b in balances_for_profile(profile):
        if b['code'] == 'annual':
            return Decimal(str(b['available']))
    return Decimal('0')


class AccrualStartsAtHireDateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='ACCR', name='Accrual Co.')

    def _employee(self, name, hire_date, number):
        emp = Employee.objects.create(
            employee_number=number, full_name=name, department='Health Insurance',
            email=f'{number.lower()}@test.example', company=self.company,
            status='active', hire_date=hire_date)
        return emp, HRISProfile.objects.create(employee=emp)

    def test_joiner_this_year_does_not_get_the_whole_year(self):
        """The Amantle case: hired late in the year, must not read 21/12 x 8.

        Unfixed, this returns the full year-to-date accrual (14.00 on 7 Sep) and
        the assertion fails.
        """
        today = timezone.localdate()
        # Hired on the 1st of LAST month: exactly one completed month.
        first_of_this_month = today.replace(day=1)
        hire = (first_of_this_month - dt.timedelta(days=1)).replace(day=1)
        emp, profile = self._employee('Late Joiner', hire, 'ACC1')

        available = _annual(profile)
        full_year_to_date = Decimal(str(round(21 * (today.month - 1) / 12.0, 2)))

        self.assertLess(
            available, full_year_to_date,
            'a mid-year joiner was credited the full year-to-date accrual')
        # 21 days a year, one completed month = 1.75.
        self.assertEqual(available, Decimal('1.75'))

    def test_brand_new_joiner_has_nothing_to_encash(self):
        """Hired days ago = 0 accrued. Unfixed this reads the full year-to-date."""
        today = timezone.localdate()
        emp, profile = self._employee(
            'Brand New', today - dt.timedelta(days=4), 'ACC2')
        self.assertEqual(_annual(profile), Decimal('0'))

    def test_long_serving_employee_is_unchanged(self):
        """Hired before this leave year — behaviour must not move."""
        today = timezone.localdate()
        hire = dt.date(today.year - 3, 4, 1)
        emp, profile = self._employee('Long Server', hire, 'ACC3')

        from hris.leave_balance import accrued_to_date, accrual_cutoff
        expected = accrued_to_date(21.0, accrual_cutoff(profile),
                                   since=dt.date(today.year, 1, 1))
        self.assertEqual(_annual(profile), Decimal(str(expected)))

    def test_uploaded_opening_balance_never_accrues_from_before_the_hire_date(self):
        """An HR upload dated before someone joined must not accrue for them."""
        today = timezone.localdate()
        hire = today.replace(day=1)          # joined this month
        emp, profile = self._employee('Uploaded Joiner', hire, 'ACC4')
        LeaveOpeningBalance.objects.create(
            profile=profile, leave_type_code='annual',
            entitlement_days=Decimal('21'), opening_balance_days=Decimal('0'),
            accrued_days=Decimal('0'),
            as_at_date=dt.date(today.year, 1, 1))
        self.assertEqual(_annual(profile), Decimal('0'))

    def test_accrual_start_falls_back_to_year_start_when_hire_date_is_blank(self):
        """No hire date = unchanged behaviour; the guard in apply_encashment is
        what stops a blank date being turned into money."""
        today = timezone.localdate()
        year_start = dt.date(today.year, 1, 1)
        emp, profile = self._employee('No Date', None, 'ACC5')
        from hris.leave_balance import accrual_start
        self.assertEqual(accrual_start(profile, year_start), year_start)


class EncashmentNeedsAHireDateTest(TestCase):
    """No start date, no encashment (CFO 2026-09-07)."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='ENCG', name='Encash Guard Co.')
        cls.user = User.objects.create_user('nodate', 'nodate@test.example', 'x')
        cls.emp = Employee.objects.create(
            employee_number='ENC1', full_name='No Start Date',
            department='Health Insurance', email='nodate@test.example',
            company=cls.company, status='active', hire_date=None,
            user=cls.user)
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        # A generous uploaded opening, so the ONLY thing that can stop the
        # application is the missing hire date.
        LeaveOpeningBalance.objects.create(
            profile=cls.profile, leave_type_code='annual',
            entitlement_days=Decimal('21'), opening_balance_days=Decimal('20'),
            accrued_days=Decimal('20'), as_at_date=timezone.localdate())

    def test_apply_is_refused_and_says_why(self):
        with self.assertRaises(ValidationError) as ctx:
            apply_encashment(
                applicant=self.user, days='5',
                # A reason long enough to clear the 50-word minimum, so the
                # ONLY thing that can refuse this application is the missing
                # start date and not the reason validator.
                reason=(
                    'I would like to request the encashment of five days of my '
                    'accrued annual leave this month. I have a family '
                    'commitment coming up that I need to prepare for, and the '
                    'additional funds would help me manage it without taking '
                    'time away from my work during a busy period for the team. '
                    'I would appreciate your consideration of this request and '
                    'am happy to provide anything further that is needed.'))
        self.assertIn('start date', str(ctx.exception).lower())
