"""Tests for the no-login 'explain your day' page — the employee-only PLANNING
token (CFO 2026-07-31: the morning-brief button must open the explain page
directly, not an omni login).

Covers: the plan token renders a date-picker form with no login; a valid POST
stores a WorkdayJustification (EXPLAINED); an out-of-range date is rejected; and
the original fixed-date token still works unchanged (no regression).
"""
import datetime as dt

from django.test import TestCase, Client
from django.utils import timezone

from core.models import Company
from hris.leave_explain import make_token, make_plan_token
from hris.models import HRISProfile, WorkdayJustification
from payroll.models import Employee


class LeaveExplainPlanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        cls.emp = Employee.objects.create(
            employee_number='E900', full_name='Kefilwe Test',
            department='Ops', email='kefilwe@test.example',
            company=cls.company, status='active')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def setUp(self):
        self.c = Client()

    # --- planning token (employee-only, no date) ---
    def test_plan_get_renders_date_picker_no_login(self):
        url = f'/api/leave-explain/{make_plan_token(self.emp.id)}/'
        r = self.c.get(url)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Kefilwe', body)
        self.assertIn('name="work_date"', body)          # date picker present
        self.assertIn('golf day', body.lower())          # planning copy
        self.assertNotIn('recorded <strong>no hours', body)  # not the fixed-day copy
        self.assertNotIn('password', body.lower())       # no login wall

    def test_plan_post_valid_creates_justification(self):
        day = timezone.localdate() + dt.timedelta(days=2)
        url = f'/api/leave-explain/{make_plan_token(self.emp.id)}/'
        r = self.c.post(url, {'worked': 'leave', 'place': '',
                              'work_date': day.isoformat(),
                              'explanation': 'On a company golf day, out of office.'})
        self.assertEqual(r.status_code, 200)
        row = WorkdayJustification.objects.get(profile=self.profile, work_date=day)
        self.assertEqual(row.status, WorkdayJustification.Status.EXPLAINED)
        self.assertEqual(row.reason, WorkdayJustification.Reason.ON_LEAVE)
        self.assertTrue(row.justification.startswith('[On leave]'))

    def test_plan_post_out_of_range_date_rejected(self):
        day = timezone.localdate() + dt.timedelta(days=400)   # way out
        url = f'/api/leave-explain/{make_plan_token(self.emp.id)}/'
        r = self.c.post(url, {'worked': 'worked', 'place': 'office',
                              'work_date': day.isoformat(),
                              'explanation': 'Trying a far-off date to be safe.'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(WorkdayJustification.objects.filter(
            profile=self.profile, work_date=day).exists())

    # --- fixed-date token (unchanged behaviour) ---
    def test_fixed_date_token_still_works(self):
        day = dt.date(2026, 7, 31)
        url = f'/api/leave-explain/{make_token(self.emp.id, day.isoformat())}/'
        r = self.c.get(url)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('no hours', body)                 # fixed-day copy
        self.assertNotIn('name="work_date"', body)      # no date picker
