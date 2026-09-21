"""
payroll/tests/test_payroll_signoff.py

DUAL payroll sign-off — CFO directive 2026-07-28. A company's month is released
to staff only when an HR signer (Unami/Dorothy) AND a Finance signer (Kago/Pako)
have both signed the current figures. The CFO is out of the routine loop.

These tests pin the rules that make that safe:
  * HR-only or Finance-only is NOT released; both legs release it (either order)
  * only HR / Finance signers may sign; ordinary staff cannot
  * the two legs must be two different people (SoD), even for a CFO back-stop
  * if the figures move after one signs, that signature drops (TOCTOU)
  * payslips are withheld until both sign — but only from the cutover forward
  * a send-back drops both legs
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, UserProfile
from payroll.models import Employee, PayrollPeriod, Payslip, PayrollSignOff
from payroll.signoff_service import (
    can_sign_finance, can_sign_hr, is_signed_off, reject_signoff,
    release_blocked_reason, sign_payroll, signoff_side,
)


def _profile(user, title):
    prof, _ = UserProfile.objects.get_or_create(user=user)
    prof.title = title
    prof.is_active = True
    prof.save()
    return prof


class DualSignOffBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Alpha Direct Insurance', code='ADIC')
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))
        cls.old_period = PayrollPeriod.objects.create(
            period_name='2026-05', start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))

        # HR signer (Unami), Finance signer (Kago), CFO back-stop, ordinary staff.
        cls.hr = User.objects.create_user('ubutale', 'ubutale@alphadirect.co.bw', 'x')
        _profile(cls.hr, UserProfile.Title.HR_MANAGER)
        cls.fin = User.objects.create_user('ktshutlhedi', 'ktshutlhedi@alphadirect.co.bw', 'x')
        _profile(cls.fin, UserProfile.Title.FINANCE_MANAGER)
        cls.cfo = User.objects.create_user('pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x')
        _profile(cls.cfo, UserProfile.Title.CFO)
        cls.staff = User.objects.create_user('tkgosi', 'tkgosi@alphadirect.co.bw', 'x')

        cls.employee = Employee.objects.create(
            full_name='Tebogo Kgosi', company=cls.company, user=cls.staff,
            employee_number='E001', email='tkgosi@alphadirect.co.bw')

    def _payslip(self, period=None, *, gross='10000.00', net='8000.00', paye='2000.00'):
        return Payslip.objects.create(
            employee=self.employee, period=period or self.period, company=self.company,
            gross_amount=Decimal(gross), paye_amount=Decimal(paye), net_amount=Decimal(net))


class SideResolutionTest(DualSignOffBase):
    def test_sides_resolve(self):
        self.assertEqual(signoff_side(self.hr), 'hr')
        self.assertEqual(signoff_side(self.fin), 'finance')
        self.assertEqual(signoff_side(self.cfo), 'both')
        self.assertIsNone(signoff_side(self.staff))
        self.assertTrue(can_sign_hr(self.hr) and not can_sign_finance(self.hr))
        self.assertTrue(can_sign_finance(self.fin) and not can_sign_hr(self.fin))


class DualReleaseTest(DualSignOffBase):
    def test_hr_only_is_not_released(self):
        self._payslip()
        sign_payroll(self.period, self.company, self.hr)
        self.assertFalse(is_signed_off(self.period, self.company))

    def test_finance_only_is_not_released(self):
        self._payslip()
        sign_payroll(self.period, self.company, self.fin)
        self.assertFalse(is_signed_off(self.period, self.company))

    def test_both_release_hr_then_finance(self):
        self._payslip()
        sign_payroll(self.period, self.company, self.hr)
        sign_payroll(self.period, self.company, self.fin)
        self.assertTrue(is_signed_off(self.period, self.company))

    def test_both_release_finance_then_hr(self):
        self._payslip()
        sign_payroll(self.period, self.company, self.fin)
        sign_payroll(self.period, self.company, self.hr)
        self.assertTrue(is_signed_off(self.period, self.company))

    def test_ordinary_staff_cannot_sign(self):
        self._payslip()
        with self.assertRaises(ValidationError):
            sign_payroll(self.period, self.company, self.staff)

    def test_cannot_sign_a_payroll_with_no_payslips(self):
        with self.assertRaises(ValidationError):
            sign_payroll(self.period, self.company, self.hr)


class SoDAndDriftTest(DualSignOffBase):
    def test_cfo_backstop_cannot_fill_both_legs(self):
        self._payslip()
        # CFO fills the HR leg, then tries the Finance leg — same person → blocked.
        sign_payroll(self.period, self.company, self.cfo, side='hr')
        with self.assertRaises(ValidationError):
            sign_payroll(self.period, self.company, self.cfo, side='finance')
        self.assertFalse(is_signed_off(self.period, self.company))

    def test_cfo_backstop_must_name_side(self):
        self._payslip()
        with self.assertRaises(ValidationError):
            sign_payroll(self.period, self.company, self.cfo)   # no side

    def test_figures_moving_after_one_sign_drops_it(self):
        ps = self._payslip(gross='10000.00', net='8000.00')
        sign_payroll(self.period, self.company, self.hr)        # HR signs 8,000 net
        # payroll changes
        ps.net_amount = Decimal('8500.00'); ps.save()
        # Finance signs — the drift drops HR's leg; only Finance is on now.
        sign_payroll(self.period, self.company, self.fin)
        self.assertFalse(is_signed_off(self.period, self.company))
        row = PayrollSignOff.objects.get(period=self.period, company=self.company)
        self.assertIsNone(row.hr_signed_by_id)
        self.assertIsNotNone(row.fin_signed_by_id)

    def test_release_then_change_unreleases(self):
        ps = self._payslip()
        sign_payroll(self.period, self.company, self.hr)
        sign_payroll(self.period, self.company, self.fin)
        self.assertTrue(is_signed_off(self.period, self.company))
        ps.net_amount = Decimal('7999.00'); ps.save()          # someone edits after close
        self.assertFalse(is_signed_off(self.period, self.company))

    def test_reject_drops_both_legs(self):
        self._payslip()
        sign_payroll(self.period, self.company, self.hr)
        row = PayrollSignOff.objects.get(period=self.period, company=self.company)
        reject_signoff(row, self.fin, 'overtime wrong')
        row.refresh_from_db()
        self.assertEqual(row.status, PayrollSignOff.Status.REJECTED)
        self.assertIsNone(row.hr_signed_by_id)
        self.assertFalse(is_signed_off(self.period, self.company))


class ReleaseGateTest(DualSignOffBase):
    def test_july_blocked_until_both_sign(self):
        ps = self._payslip()
        self.assertIsNotNone(release_blocked_reason(ps))        # unsigned
        sign_payroll(self.period, self.company, self.hr)
        self.assertIsNotNone(release_blocked_reason(ps))        # only HR
        sign_payroll(self.period, self.company, self.fin)
        self.assertIsNone(release_blocked_reason(ps))           # both → released

    def test_pre_cutover_never_blocked(self):
        ps = self._payslip(period=self.old_period)              # 2026-05
        self.assertIsNone(release_blocked_reason(ps))
