"""HRIS-006 regression — the employee directory must NEVER leak one entity's
staff to a user scoped to a different entity, even when that user is HR-tier.

Reproduces the 2026-06-18 leak (CFO / Lakshmi, lanand@theriskco.com): an
ADRG-scoped HR_MANAGER could list ADIC employees because the HRIS-004 HR-tier
branch in `employees()` bypassed company scoping. The fix routes EVERY caller
through `apply_company_scope`. This test fails the moment that branch returns.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import (
    Company, Role, UserCompanyAccess, UserProfile, UserRoleAssignment,
)
from hris.models import HRISProfile
from payroll.models import Employee

EMPLOYEES_URL = '/hris/api/employees/'


def _unlock(user):
    """Pass the HRIS password gate for the duration of the test (mirrors the
    pattern in test_leave_report.py)."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


class EmployeeDirectoryEntityScopeTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co_a = Company.objects.create(code='ADIA', name='Alpha Direct Insurance (test)')
        cls.co_b = Company.objects.create(code='ADRB', name='ADRisk (test)')

        cls.a1 = Employee.objects.create(employee_number='A1', full_name='Anna Adic', company=cls.co_a)
        cls.a2 = Employee.objects.create(employee_number='A2', full_name='Albert Adic', company=cls.co_a)
        cls.b1 = Employee.objects.create(employee_number='B1', full_name='Bonolo Adrisk', company=cls.co_b)
        for e in (cls.a1, cls.a2, cls.b1):
            HRISProfile.objects.create(employee=e)

        cls.hr_role, _ = Role.objects.get_or_create(
            code='HR_MANAGER', defaults={'name': 'HR Manager', 'level': 2})

        # Entity-restricted HR user (the Lakshmi scenario): HR-tier (HR_MANAGER →
        # manage_leave_admin) but granted access to ONLY co_b.
        cls.restricted_hr = User.objects.create_user(
            username='restricted_hr', email='hr_adrb@example.com', password='x')
        UserRoleAssignment.objects.create(user=cls.restricted_hr, role=cls.hr_role)
        UserCompanyAccess.objects.create(
            user=cls.restricted_hr, company=cls.co_b, can_view=True,
            granted_by=cls.restricted_hr)
        _unlock(cls.restricted_hr)

        # Genuinely-unrestricted user (whole-group view) — superuser bypass.
        cls.admin = User.objects.create_user(
            username='grp_admin', email='admin@example.com', password='x',
            is_superuser=True, is_staff=True)
        _unlock(cls.admin)

    def _rows(self, resp):
        return resp.json()['employees']

    def test_entity_restricted_hr_sees_only_its_entity(self):
        """HRIS-006: an ADRB-scoped HR_MANAGER sees ONLY co_b staff — never co_a."""
        self.client.force_authenticate(self.restricted_hr)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = self._rows(resp)
        names = {r['nm'] for r in rows}
        self.assertIn('Bonolo Adrisk', names)
        self.assertNotIn('Anna Adic', names)
        self.assertNotIn('Albert Adic', names)
        # Every returned row belongs to co_b — categorically no cross-entity leak.
        self.assertTrue(rows and all(r['company'] == 'ADRB' for r in rows), rows)

    def test_unrestricted_user_sees_all_entities(self):
        """The genuinely-unrestricted bucket keeps the consolidated group view."""
        self.client.force_authenticate(self.admin)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        names = {r['nm'] for r in self._rows(resp)}
        self.assertIn('Anna Adic', names)
        self.assertIn('Bonolo Adrisk', names)


class AmendmentPickerIdentityFieldsTest(APITestCase):
    """CFO file 2 Medium (18-Sep-2026): the Amendments employee picker searches
    by name, email and employee number. Those two identity fields ride on the
    SAME scoped rows — a restricted HR user must still never receive another
    entity's email or number."""

    @classmethod
    def setUpTestData(cls):
        cls.co_a = Company.objects.create(code='PKA', name='Picker A (test)')
        cls.co_b = Company.objects.create(code='PKB', name='Picker B (test)')
        for num, name, email, co in (
            ('PA1', 'Same Name', 'same.a@example.com', cls.co_a),
            ('PB1', 'Same Name', 'same.b@example.com', cls.co_b),
            ('PB2', 'Other Person', 'other.b@example.com', cls.co_b),
        ):
            HRISProfile.objects.create(employee=Employee.objects.create(
                employee_number=num, full_name=name, email=email, company=co))
        hr_role, _ = Role.objects.get_or_create(
            code='HR_MANAGER', defaults={'name': 'HR Manager', 'level': 2})
        cls.hr_b = User.objects.create_user('picker_hr_b', 'pickerhr@example.com', 'x')
        UserRoleAssignment.objects.create(user=cls.hr_b, role=hr_role)
        UserCompanyAccess.objects.create(user=cls.hr_b, company=cls.co_b, can_view=True,
                                         granted_by=cls.hr_b)
        _unlock(cls.hr_b)

    def test_rows_carry_email_and_employee_number(self):
        self.client.force_authenticate(self.hr_b)
        rows = self.client.get(EMPLOYEES_URL).json()['employees']
        by_en = {r['en']: r for r in rows}
        self.assertEqual(by_en['PB1']['email'], 'same.b@example.com')
        self.assertEqual(by_en['PB2']['email'], 'other.b@example.com')

    def test_other_entitys_same_name_twin_never_leaks(self):
        self.client.force_authenticate(self.hr_b)
        rows = self.client.get(EMPLOYEES_URL).json()['employees']
        self.assertNotIn('PA1', {r['en'] for r in rows})
        self.assertNotIn('same.a@example.com', {r['email'] for r in rows})
        self.assertEqual(sum(1 for r in rows if r['nm'] == 'Same Name'), 1)
