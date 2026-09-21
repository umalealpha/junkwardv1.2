"""CFO 19-Sep-2026: "improve it, filter…" on the Development Dialogue (All Employees).

Company, grade, manager and the canonical department are not in the stored
payload, so the cockpit's filter bar cannot offer them. These pin that the API
DERIVES them on read, that an unlinked dialogue still answers, and — the part
that protects live appraisals — that they are stripped again on save instead of
accreting into the payload the Excel roll-up reads.
"""
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import Company, Currency
from hris.models import Grade, HRISProfile
from hris.talent_cockpit_models import DevelopmentDialogue
from payroll.models import Employee

URL = '/hris/api/talent/cockpit/'


class CockpitFilterKeys(APITestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.boss = Employee.objects.create(
            employee_number='E100', full_name='Kago Boss', department='Finance',
            job_title='Manager', email='boss@example.com', status='active', company=cls.company)
        cls.alice = Employee.objects.create(
            employee_number='E101', full_name='Alice Motswana', department='Finance & Planning',
            job_title='Accountant', email='alice@example.com', status='active', company=cls.company)
        grade = Grade.objects.create(code='C-3', name='Senior Associate', level=3, midpoint=20000)
        HRISProfile.objects.create(employee=cls.alice, grade=grade, manager=cls.boss)
        cls.exec_user = User.objects.create_user('pganesharajah', 'pganesharajah@example.com', 'x')

    def _dialogue(self, employee, ref, name, **kw):
        return DevelopmentDialogue.objects.create(
            ref=ref, name=name, employee=employee, is_current=True,
            email=(employee.email if employee else ''),
            period='FY27', department='typed by hand',
            payload={'name': name, 'dept': 'typed by hand', 'period': 'FY27'}, **kw)

    def _get(self):
        c = APIClient()
        c.force_authenticate(self.exec_user)
        return c.get(URL)

    def test_linked_dialogue_carries_company_grade_manager_and_department(self):
        self._dialogue(self.alice, 'alice@example.com::1', 'Alice Motswana')

        person = self._get().data['people'][0]

        self.assertEqual(person['company'], 'ADIC')
        self.assertEqual(person['grade'], 'C-3')
        self.assertEqual(person['manager'], 'Kago Boss')
        # The canonical department beats the free text typed into the cockpit.
        self.assertEqual(person['deptCanonical'], 'Finance & Planning')
        self.assertEqual(person['dept'], 'typed by hand')

    def test_a_dialogue_with_no_payroll_link_still_answers(self):
        self._dialogue(None, 'external@example.com::1', 'External Person')

        person = self._get().data['people'][0]

        self.assertEqual(
            {person['company'], person['grade'], person['manager'], person['deptCanonical']}, {''})

    def test_the_filter_keys_never_reach_the_stored_payload(self):
        row = self._dialogue(self.alice, 'alice@example.com::1', 'Alice Motswana')
        person = self._get().data['people'][0]

        c = APIClient()
        c.force_authenticate(self.exec_user)
        saved = c.put(URL, {'people': [person]}, format='json')

        self.assertEqual(saved.status_code, 200)
        row.refresh_from_db()
        for key in ('company', 'grade', 'manager', 'deptCanonical'):
            self.assertNotIn(key, row.payload)
