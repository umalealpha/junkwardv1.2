"""Attendance gate for staff loans / leave encashment (CFO 2026-07-28):
no application while the last 20 days have unexplained Time Doctor shortfalls."""
import datetime as _dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import Company, Currency
from hris.attendance_gate import attendance_gate
from hris.models import HRISProfile, WorkdayJustification
from payroll.models import Employee


class AttendanceGateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.emp = Employee.objects.create(employee_number='E1', full_name='Alice M', company=cls.co)
        cls.hp = HRISProfile.objects.create(employee=cls.emp)

    def _wj(self, days_ago, status, tracked=0, required=8):
        d = timezone.now().date() - _dt.timedelta(days=days_ago)
        return WorkdayJustification.objects.create(
            profile=self.hp, work_date=d, status=status,
            tracked_hours=Decimal(tracked), required_hours=Decimal(required))

    def test_clear_when_no_rows(self):
        ok, bad, _ = attendance_gate(self.hp)
        self.assertTrue(ok)
        self.assertEqual(bad, [])

    def test_unjustified_in_window_blocks(self):
        self._wj(3, WorkdayJustification.Status.UNJUSTIFIED)
        ok, bad, msg = attendance_gate(self.hp)
        self.assertFalse(ok)
        self.assertEqual(len(bad), 1)
        self.assertIn('cannot apply', msg.lower())

    def test_pending_shortfall_blocks(self):
        self._wj(2, WorkdayJustification.Status.PENDING)
        self.assertFalse(attendance_gate(self.hp)[0])

    def test_short_but_present_day_does_not_block(self):
        # worked 5h of 8 — present, just short — is NOT an absence, must not block
        self._wj(3, WorkdayJustification.Status.UNJUSTIFIED, tracked=5, required=8)
        self.assertTrue(attendance_gate(self.hp)[0])

    def test_off_day_zero_required_does_not_block(self):
        # weekend / no-hours-required day (required=0) must not block even at 0 tracked
        self._wj(3, WorkdayJustification.Status.UNJUSTIFIED, tracked=0, required=0)
        self.assertTrue(attendance_gate(self.hp)[0])

    def test_justified_explained_met_do_not_block(self):
        self._wj(3, WorkdayJustification.Status.JUSTIFIED)
        self._wj(4, WorkdayJustification.Status.EXPLAINED)   # they added a comment
        self._wj(5, WorkdayJustification.Status.MET)
        self._wj(6, WorkdayJustification.Status.NOT_REQUIRED)
        self.assertTrue(attendance_gate(self.hp)[0])

    def test_old_unjustified_outside_20d_ignored(self):
        self._wj(40, WorkdayJustification.Status.UNJUSTIFIED)
        self.assertTrue(attendance_gate(self.hp)[0])

    def test_no_profile_allowed(self):
        self.assertTrue(attendance_gate(None)[0])
