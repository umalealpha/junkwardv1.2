"""HR_VIEWER (CFO 2026-08-07) — a read-only, see-EVERYONE HR viewer that must
NEVER see another employee's pay.

Proves:
  * the HR_VIEWER role resolves to the 'hr_viewer' tier and holds HRIS module
    access + view_all, but NOT any compensation/payroll capability;
  * every real pay gate (user_can_view_payroll, can_view_compensation) REFUSES it
    — so the payslip / provision / payroll surfaces are blocked;
  * the two module-access screens that used to leak pay (employee directory
    grade-salary, pay-grade midpoints) now blank the pay figure for the viewer;
  * a genuine HR_MANAGER is UNAFFECTED — no regression to existing HR access.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, Role, UserProfile, UserRoleAssignment
from core.hris_access import (
    ROLE_CAPABILITIES, can_view_compensation, hris_role, user_can_access_hris,
)
from payroll.amendment_views import user_can_view_payroll
from hris.models import Grade, HRISProfile
from payroll.models import Employee

EMPLOYEES_URL = '/hris/api/employees/'
GRADES_URL = '/hris/api/grades/'


def _unlock(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


class HRViewerAccessTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct (test)')
        cls.grade = Grade.objects.create(
            code='M4', name='Managers', level=5, spread=40,
            midpoint=Decimal('50000.00'), is_active=True)

        # Someone else, WITH a pay grade — this is the person whose salary the
        # viewer must not see.
        cls.other = Employee.objects.create(
            employee_number='E1', full_name='Other Person', company=cls.co)
        HRISProfile.objects.create(employee=cls.other, grade=cls.grade)

        # The read-only HR viewer.
        cls.viewer = User.objects.create_user(
            username='hr_viewer_u', email='mtlagae@example.com', password='x')
        cls.viewer_emp = Employee.objects.create(
            employee_number='V1', full_name='View Only', company=cls.co)
        HRISProfile.objects.create(employee=cls.viewer_emp)
        cls.hr_viewer_role, _ = Role.objects.get_or_create(
            code='HR_VIEWER', defaults={'name': 'HR Viewer', 'level': 5})
        UserRoleAssignment.objects.create(user=cls.viewer, role=cls.hr_viewer_role)
        _unlock(cls.viewer)

        # A genuine HR manager (regression guard — must keep seeing pay).
        cls.hr = User.objects.create_user(
            username='hr_mgr_u', email='hrmgr@example.com', password='x')
        cls.hr_role, _ = Role.objects.get_or_create(
            code='HR_MANAGER', defaults={'name': 'HR Manager', 'level': 3})
        UserRoleAssignment.objects.create(user=cls.hr, role=cls.hr_role)
        _unlock(cls.hr)

    # ---- role + gates -------------------------------------------------------
    def test_role_resolves_to_hr_viewer(self):
        self.assertEqual(hris_role(self.viewer), 'hr_viewer')

    def test_viewer_has_module_access_but_no_pay_gate(self):
        self.assertTrue(user_can_access_hris(self.viewer))       # sees HR section
        self.assertFalse(can_view_compensation(self.viewer))     # not others' pay
        self.assertFalse(user_can_view_payroll(self.viewer))     # payslip viewset 403

    def test_capability_set_is_read_only_no_pay(self):
        caps = ROLE_CAPABILITIES['hr_viewer']
        self.assertIn('view_all', caps)            # sees everyone's HR record
        self.assertIn('view_own_payslip', caps)    # her OWN payslip
        for forbidden in ('view_compensation', 'view_bonus_pool',
                          'view_others_payslip', 'manage_payroll',
                          'manage_employees', 'approve_team_leave', 'amend_hris'):
            self.assertNotIn(forbidden, caps, forbidden)

    # ---- the two screens that used to leak pay ------------------------------
    def test_directory_blanks_others_salary_for_viewer(self):
        self.client.force_authenticate(self.viewer)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['employees']
        self.assertTrue(rows)                                   # she DOES see people
        self.assertTrue(all(r['salary'] is None for r in rows),
                        [r.get('salary') for r in rows])       # but no pay figure

    def test_directory_still_shows_salary_to_hr_manager(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = resp.json()['employees']
        other = [r for r in rows if r['nm'] == 'Other Person']
        self.assertTrue(other and other[0]['salary'] == 50000.0, rows)

    def test_grade_midpoints_hidden_from_viewer_shown_to_hr(self):
        self.client.force_authenticate(self.viewer)
        v = self.client.get(GRADES_URL)
        self.assertEqual(v.status_code, 200, v.content)
        self.assertTrue(all(g['midpoint'] is None for g in v.json()['grades']))

        self.client.force_authenticate(self.hr)
        h = self.client.get(GRADES_URL)
        self.assertEqual(h.status_code, 200, h.content)
        self.assertTrue(any(g['midpoint'] == 50000.0 for g in h.json()['grades']))
