"""Oprah Mogomotsi, 2026-08-24: the People Directory and the HRIS home
"Humans" headcount both read /hris/api/employees/, which listed EVERY
HRISProfile with no archival/status filter — so a terminated or archived
leaver still showed in the directory and inflated the headcount tile.

A directory + a live headcount are a "who works here now" view, so a leaver
belongs in neither. The endpoint now excludes both archived staff and anyone
whose status is TERMINATED (archived-or-not). Group-wide by construction:
is_archived / status are row-level flags on the shared payroll.Employee, so
every entity (RSA…VCM) is covered at once.

Each test below fails if the exclusion is removed from hris.api_views.employees.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, Currency, UserProfile
from hris.models import HRISProfile
from payroll.models import Employee

EMPLOYEES_URL = '/hris/api/employees/'


def _unlock(user):
    """Pass the HRIS password gate for the test (mirrors test_entity_scope.py)."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    profile.save(update_fields=['hris_unlocked_until'])
    return profile


class DirectoryExcludesLeaversTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.co = Company.objects.create(code='ZZD', name='Directory Test Co')

        cls.active = Employee.objects.create(
            employee_number='D-ACT', full_name='Active Worker', company=cls.co,
            status=Employee.Status.ACTIVE)
        cls.terminated = Employee.objects.create(
            employee_number='D-TERM', full_name='Terminated Not Archived', company=cls.co,
            status=Employee.Status.TERMINATED, termination_date=dt.date(2026, 8, 1))
        cls.archived = Employee.objects.create(
            employee_number='D-ARCH', full_name='Archived Leaver', company=cls.co,
            status=Employee.Status.TERMINATED, termination_date=dt.date(2026, 7, 15),
            is_archived=True)
        for e in (cls.active, cls.terminated, cls.archived):
            HRISProfile.objects.create(employee=e)

        cls.admin = User.objects.create_user(
            username='dir_admin', email='diradmin@example.com', password='x',
            is_superuser=True, is_staff=True)
        _unlock(cls.admin)

    def _names(self, resp):
        return {r['nm'] for r in resp.json()['employees']}

    def test_directory_lists_only_current_staff(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        names = self._names(resp)
        self.assertIn('Active Worker', names)
        self.assertNotIn('Archived Leaver', names)
        self.assertNotIn('Terminated Not Archived', names)

    def test_humans_headcount_counts_only_current_staff(self):
        """The 'Humans' tile is employees.length — one active head, not three."""
        self.client.force_authenticate(self.admin)
        resp = self.client.get(EMPLOYEES_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['count'], 1)
