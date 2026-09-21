"""Prompt 02 (Shared Contract v1, 2026-09-12) — joiners and leavers: one
"active for this period" rule.

Before this fix, roll-forward exclusion read Employee.status ==
TERMINATED — a leaver whose status was never flipped (only termination_date
set) kept appearing every month after their final period. A brand-new hire
with no HIRE amendment row never appeared at all.

Run: manage.py test payroll.test_active_period_rule
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.eligibility import is_active_for_period, exclusion_reason
from payroll.models import (
    Employee, PayrollAmendmentBatch, PayrollPeriod, Payslip, PayslipComponent, PayslipLine,
)
from payroll.contract_models import EmploymentContract


class _Period:
    """Lightweight stand-in — is_active_for_period only reads start/end."""
    def __init__(self, start, end):
        self.start_date, self.end_date = start, end


class IsActiveForPeriodUnitTests(APITestCase):
    """Pure unit tests of the predicate — no DB roll-forward involved."""

    def _emp(self, hire, term=None, archived=False):
        e = Employee(hire_date=hire, termination_date=term, is_archived=archived)
        return e

    def test_hired_mid_month_is_active_from_that_period(self):
        sep = _Period(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))
        aug = _Period(datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        emp = self._emp(hire=datetime.date(2026, 9, 15))
        self.assertTrue(is_active_for_period(emp, sep))
        self.assertFalse(is_active_for_period(emp, aug))  # not before hire

    def test_terminated_on_day_one_of_period_is_not_active(self):
        """CFO decision 2026-09-12: a termination effective on the FIRST day
        of a period excludes that period entirely — no full month's pay by
        default, Finance raises a settlement amendment instead."""
        oct_ = _Period(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        emp = self._emp(hire=datetime.date(2020, 1, 1), term=datetime.date(2026, 10, 1))
        self.assertFalse(is_active_for_period(emp, oct_))

    def test_terminated_mid_month_is_active_for_final_period_only(self):
        oct_ = _Period(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        nov_ = _Period(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
        emp = self._emp(hire=datetime.date(2024, 1, 1), term=datetime.date(2026, 10, 20))
        self.assertTrue(is_active_for_period(emp, oct_), 'must appear on the final period')
        self.assertFalse(is_active_for_period(emp, nov_), 'must NOT linger the month after')

    def test_status_is_never_consulted_only_dates(self):
        """The exact bug: an employee whose `status` field was never flipped
        to TERMINATED (only termination_date set) must still be excluded once
        the date has passed — this predicate is date-driven, not status-driven."""
        nov_ = _Period(datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))
        emp = Employee(hire_date=datetime.date(2024, 1, 1),
                       termination_date=datetime.date(2026, 10, 20),
                       status=Employee.Status.ACTIVE,   # deliberately NOT flipped
                       is_archived=False)
        self.assertFalse(is_active_for_period(emp, nov_))

    def test_terminated_status_with_no_termination_date_is_excluded(self):
        """The mirror-image bug (coordinator review, 2026-09-12): status
        flipped to TERMINATED via the PATCH status=terminated API (or a
        legacy import) that set NO termination_date at all must never read
        as active — a positive termination signal is never ignored just
        because the date is missing."""
        oct_ = _Period(datetime.date(2026, 10, 1), datetime.date(2026, 10, 31))
        emp = Employee(hire_date=datetime.date(2020, 1, 1),
                       termination_date=None,
                       status=Employee.Status.TERMINATED,
                       is_archived=False)
        self.assertFalse(is_active_for_period(emp, oct_))
        self.assertTrue(exclusion_reason(emp, oct_))

    def test_no_hire_date_falls_back_to_status_not_guessed_active(self):
        """A legacy record with no hire_date on file must not be silently
        dropped from an untouched roll-forward — compatibility fallback to
        the pre-Prompt-02 status signal, never a guessed date."""
        sep = _Period(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))
        active_legacy = Employee(hire_date=None, status=Employee.Status.ACTIVE, is_archived=False)
        self.assertTrue(is_active_for_period(active_legacy, sep))
        terminated_legacy = Employee(hire_date=None, status=Employee.Status.TERMINATED, is_archived=False)
        self.assertFalse(is_active_for_period(terminated_legacy, sep))

    def test_archived_is_never_active(self):
        sep = _Period(datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))
        emp = self._emp(hire=datetime.date(2020, 1, 1), archived=True)
        self.assertFalse(is_active_for_period(emp, sep))


class RollForwardEligibilityTests(APITestCase):
    """End-to-end through the real apply-batch endpoint (amendment_views)."""

    def setUp(self):
        self.fc = User.objects.create_user('pkago2', email='pkago2@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.client.force_authenticate(self.fc)
        self.co = Company.objects.create(code='APR', name='Active Period Rule Co')
        self.basic = PayslipComponent.objects.get_or_create(
            code='BASIC', defaults={'name': 'Basic Salary',
                                    'kind': PayslipComponent.Kind.EARNING, 'is_taxable': True})[0]
        self.oct_ = PayrollPeriod.objects.create(period_name='2099-10',
            start_date=datetime.date(2099, 10, 1), end_date=datetime.date(2099, 10, 31))
        self.nov_ = PayrollPeriod.objects.create(period_name='2099-11',
            start_date=datetime.date(2099, 11, 1), end_date=datetime.date(2099, 11, 30))

    def _apply(self, batch):
        url = reverse('v1-payroll-amendments-apply', args=[batch.id])
        return self.client.post(url)

    def test_leaver_present_in_final_period_absent_the_month_after(self):
        leaver = Employee.objects.create(employee_number='LV1', full_name='Leaving Soon',
                                         company=self.co, hire_date=datetime.date(2020, 1, 1))
        oct_ps = Payslip.objects.create(employee=leaver, period=self.oct_, company=self.co)
        PayslipLine.objects.create(payslip=oct_ps, component=self.basic, amount=Decimal('4000'))

        # Terminate mid-October — status left ACTIVE on purpose (the real bug:
        # some offboarding paths only set the date), termination_date set.
        leaver.termination_date = datetime.date(2099, 10, 20)
        leaver.save(update_fields=['termination_date'])

        # October → November roll-forward: leaver has no amendment this batch,
        # must be excluded from November because their final period was October.
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.nov_, baseline_period=self.oct_, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        r = self._apply(batch)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Payslip.objects.filter(employee=leaver, period=self.nov_).exists(),
                         'leaver must NOT linger into November')

    def test_terminated_on_day_one_gets_no_full_baseline_copy(self):
        """CFO decision 2026-09-12: someone whose last day IS the first day
        of the target period must not receive a full month's pay as a plain
        baseline copy — Finance raises a settlement amendment for what is
        actually owed instead."""
        leaver = Employee.objects.create(employee_number='LV2', full_name='Day One Leaver',
                                         company=self.co, hire_date=datetime.date(2020, 1, 1))
        sep_ps = Payslip.objects.create(employee=leaver, period=PayrollPeriod.objects.create(
            period_name='2099-09c', start_date=datetime.date(2099, 9, 1), end_date=datetime.date(2099, 9, 30)),
            company=self.co)
        PayslipLine.objects.create(payslip=sep_ps, component=self.basic, amount=Decimal('4000'))
        leaver.termination_date = self.oct_.start_date   # 1 Oct — day 1 of the target period
        leaver.save(update_fields=['termination_date'])

        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.oct_, baseline_period=sep_ps.period, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        r = self._apply(batch)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Payslip.objects.filter(employee=leaver, period=self.oct_).exists(),
                         'day-1 termination must not produce a full baseline copy')

    def test_new_joiner_auto_included_from_their_hire_period(self):
        joiner = Employee.objects.create(employee_number='JN1', full_name='New Joiner',
                                         company=self.co, hire_date=datetime.date(2099, 10, 15))
        EmploymentContract.objects.create(employee=joiner, start_date=joiner.hire_date,
                                          basic=Decimal('6000.00'),
                                          status=EmploymentContract.Status.ACTIVE)
        # No baseline payslip (they didn't exist in October's predecessor) and
        # no HIRE amendment — this batch is a plain October roll-forward.
        sep = PayrollPeriod.objects.create(period_name='2099-09',
            start_date=datetime.date(2099, 9, 1), end_date=datetime.date(2099, 9, 30))
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.oct_, baseline_period=sep, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        r = self._apply(batch)
        self.assertEqual(r.status_code, 200, r.content)
        ps = Payslip.objects.filter(employee=joiner, period=self.oct_).first()
        self.assertIsNotNone(ps, 'a joiner hired mid-October must appear on the October run')
        line = ps.lines.filter(component=self.basic).first()
        self.assertIsNotNone(line)
        # Coordinator review 2026-09-12: the auto-created joiner payslip was
        # never recomputed, so it read gross=0.00/net=0.00 despite carrying a
        # real 6000.00 line — a draft that lies about a new hire's pay.
        self.assertEqual(ps.gross_amount, Decimal('6000.00'))
        self.assertEqual(ps.net_amount, Decimal('6000.00'))  # no PAYE brackets seeded in this test
        self.assertEqual(line.amount, Decimal('6000.00'), 'proration OFF → full BASIC')

    def test_cancelled_baseline_employee_gets_no_auto_joiner_payslip(self):
        """Coordinator review, 2026-09-12: step 1b used to select every active
        employee lacking a TARGET payslip, so someone whose baseline payslip
        existed but was CANCELLED (their September slip was reversed/voided)
        got a brand-new October draft — directly contradicting the rule
        elsewhere in this function that a cancelled slip never seeds the
        next period. Also covers the HR-only-record class (a hire_date on
        file but no payroll history) getting a payslip auto-created every
        single month."""
        # hire_date is deliberately AFTER the baseline period ends (so the
        # test isolates the baseline-payslip exclusion, not just the
        # hire_date__gt filter that alone would already exclude a pre-2099-10
        # hire) — a cancelled-baseline row can exist for other reasons (e.g.
        # a corrected data load) even when hire_date reads as a fresh joiner.
        person = Employee.objects.create(employee_number='CX1', full_name='Cancelled Baseline Person',
                                         company=self.co, hire_date=datetime.date(2099, 10, 5))
        sep = PayrollPeriod.objects.create(period_name='2099-09d',
            start_date=datetime.date(2099, 9, 1), end_date=datetime.date(2099, 9, 30))
        cancelled_ps = Payslip.objects.create(employee=person, period=sep, company=self.co,
                                              status=Payslip.Status.CANCELLED)
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.oct_, baseline_period=sep, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        r = self._apply(batch)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Payslip.objects.filter(employee=person, period=self.oct_).exists(),
                         'a CANCELLED baseline slip must not seed a fresh auto-joiner draft')

    def test_proration_on_gives_partial_basic(self):
        from payroll import config as payroll_config
        payroll_config.get_setting('payroll.prorate_partial_month')  # seed the row
        from payroll.models import PayrollSetting
        PayrollSetting.objects.filter(key='payroll.prorate_partial_month').update(value='true')

        joiner = Employee.objects.create(employee_number='JN2', full_name='Partial Month Joiner',
                                         company=self.co, hire_date=datetime.date(2099, 10, 16))
        EmploymentContract.objects.create(employee=joiner, start_date=joiner.hire_date,
                                          basic=Decimal('2400.00'),
                                          status=EmploymentContract.Status.ACTIVE)
        sep = PayrollPeriod.objects.create(period_name='2099-09b',
            start_date=datetime.date(2099, 9, 1), end_date=datetime.date(2099, 9, 30))
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.oct_, baseline_period=sep, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        try:
            r = self._apply(batch)
            self.assertEqual(r.status_code, 200, r.content)
            ps = Payslip.objects.get(employee=joiner, period=self.oct_)
            line = ps.lines.get(component=self.basic)
            self.assertLess(line.amount, Decimal('2400.00'), 'proration ON must give LESS than full BASIC')
            self.assertGreater(line.amount, Decimal('0'))
        finally:
            PayrollSetting.objects.filter(key='payroll.prorate_partial_month').update(value='false')
