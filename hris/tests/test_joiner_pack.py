from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.joiner_pack import (
    documents_status,
    joiner_row,
    start_joiner_pack,
    start_monthly_reviews,
    systems_for,
)
from hris.models import (
    EmployeeAcknowledgement,
    Grade,
    HRDocument,
    HRISProfile,
    HRSetting,
    OnboardingTask,
    RoleSystemRequirement,
)
from payroll.models import Employee

User = get_user_model()


class JoinerPackTests(TestCase):
    def setUp(self):
        self.currency, _ = Currency.objects.get_or_create(
            code='BWP',
            defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        self.company = Company.objects.create(code='TST1', name='Test Co One')
        self.hr_user = User.objects.create_user(
            username='hr', email='hr@alphadirect.co.bw', password='p'
        )
        self.manager_user = User.objects.create_user(
            username='mgr', email='mgr@alphadirect.co.bw', password='p'
        )
        self.employee_user = User.objects.create_user(
            username='emp', email='emp@alphadirect.co.bw', password='p'
        )
        self.other_user = User.objects.create_user(
            username='other', email='other@alphadirect.co.bw', password='p'
        )
        HRSetting.objects.get_or_create(
            key='contract_reminder_recipients',
            defaults={'value': ['hr@alphadirect.co.bw']},
        )
        self.grade = Grade.objects.create(code='G1', name='Grade 1', level=1, midpoint=10000)

    def make_employee(self, number, full_name, **kwargs):
        defaults = {
            'employee_number': number,
            'full_name': full_name,
            'company': self.company,
            'status': 'active',
        }
        defaults.update(kwargs)
        return Employee.objects.create(**defaults)

    def make_profile(self, employee, manager=None, is_controller=False):
        return HRISProfile.objects.create(
            employee=employee,
            grade=self.grade,
            manager=manager,
            is_controller=is_controller,
        )

    def test_systems_for_merges_star_and_department(self):
        RoleSystemRequirement.objects.update_or_create(department='*', defaults={'systems': ['email', 'vpn']})
        RoleSystemRequirement.objects.update_or_create(
            department='Underwriting', defaults={'systems': ['graphite', 'email']}
        )
        employee = self.make_employee('E001', 'Underwriter One', department='underwriting')

        self.assertEqual(systems_for(employee), ['email', 'vpn', 'graphite'])

    def test_documents_status_flags_missing_and_controller_extras(self):
        employee = self.make_employee('E001', 'Normal Employee')
        profile = self.make_profile(employee, is_controller=False)

        statuses = documents_status(employee)
        self.assertEqual(
            [s['category'] for s in statuses],
            ['id_document', 'bank_letter', 'police_clearance', 'onboarding'],
        )
        self.assertTrue(all(s['present'] is False for s in statuses))

        HRDocument.objects.create(
            title='ID document',
            category='id_document',
            file=ContentFile(b'id', name='id.pdf'),
            employee=profile,
            employee_name=employee.full_name,
            is_personal=True,
            uploaded_by=self.hr_user,
        )

        statuses = documents_status(employee)
        self.assertTrue(next(s['present'] for s in statuses if s['category'] == 'id_document'))

        profile.is_controller = True
        profile.save()
        statuses = documents_status(employee)
        self.assertIn('nbfira_letter', [s['category'] for s in statuses])
        self.assertIn('controller_docs', [s['category'] for s in statuses])

    @patch('hris.joiner_pack.send_html_with_cfo_cc')
    def test_start_joiner_pack_creates_tasks_acks_policies_is_idempotent_and_emails(self, mock_send):
        mock_send.return_value = 1

        manager = self.make_employee(
            'M001',
            'Manager One',
            email='mgr@alphadirect.co.bw',
            user=self.manager_user,
        )
        employee = self.make_employee(
            'E001',
            'New Joiner',
            email='new@alphadirect.co.bw',
            user=self.employee_user,
            department='Underwriting',
            job_title='Underwriter',
            hire_date=date(2026, 9, 1),
        )
        profile = self.make_profile(employee, manager=manager)

        HRDocument.objects.create(
            title='Underwriter Job Description',
            category='job_description',
            file=ContentFile(b'jd', name='jd.pdf'),
            employee=profile,
            employee_name=employee.full_name,
            is_personal=True,
            uploaded_by=self.hr_user,
        )
        HRDocument.objects.create(
            title='Code of Conduct',
            category='policy',
            file=ContentFile(b'policy1', name='code.pdf'),
            is_personal=False,
            uploaded_by=self.hr_user,
        )
        HRDocument.objects.create(
            title='IT Policy',
            category='policy',
            file=ContentFile(b'policy2', name='it.pdf'),
            is_personal=False,
            uploaded_by=self.hr_user,
        )

        start_joiner_pack(employee, self.hr_user)

        task_titles = set(
            OnboardingTask.objects.filter(employee=employee).values_list('title', flat=True)
        )
        expected_tasks = {
            'Collect ID, bank letter and police clearance',
            'File signed contract and onboarding documents',
            'Welcome meeting and introduce the team',
            'Agree 30-day objectives',
            'Read and sign all company policies',
            'Sign your job description',
        }
        self.assertEqual(task_titles, expected_tasks)

        self.assertEqual(EmployeeAcknowledgement.objects.filter(employee=employee).count(), 3)
        self.assertTrue(
            EmployeeAcknowledgement.objects.filter(
                employee=employee,
                kind='job_description',
                title=f'Job description — {employee.job_title}',
            ).exists()
        )
        self.assertEqual(
            EmployeeAcknowledgement.objects.filter(employee=employee, kind='policy').count(), 2
        )

        self.assertEqual(mock_send.call_count, 2)
        for call in mock_send.call_args_list:
            self.assertFalse(call.kwargs['cc_cfo'])

        # Second call must not create duplicate rows.
        start_joiner_pack(employee, self.hr_user)
        self.assertEqual(OnboardingTask.objects.filter(employee=employee).count(), 6)
        self.assertEqual(EmployeeAcknowledgement.objects.filter(employee=employee).count(), 3)

    def test_start_monthly_reviews_creates_once_per_period(self):
        today = timezone.localdate()
        manager = self.make_employee(
            'M001',
            'Manager One',
            email='mgr@alphadirect.co.bw',
            user=self.manager_user,
        )
        employee = self.make_employee(
            'E001',
            'Employee One',
            email='emp@alphadirect.co.bw',
            user=self.employee_user,
            hire_date=today - timedelta(days=60),
        )
        self.make_profile(employee, manager=manager)

        first_count = start_monthly_reviews(today)
        self.assertEqual(first_count, 1)
        self.assertTrue(
            EmployeeAcknowledgement.objects.filter(
                employee=employee,
                kind='monthly_review',
                period=today.strftime('%Y-%m'),
            ).exists()
        )

        second_count = start_monthly_reviews(today)
        self.assertEqual(second_count, 0)
        self.assertEqual(
            EmployeeAcknowledgement.objects.filter(
                employee=employee,
                kind='monthly_review',
                period=today.strftime('%Y-%m'),
            ).count(),
            1,
        )

    def test_sign_employee_own_and_double_sign(self):
        manager = self.make_employee(
            'M001',
            'Manager One',
            email='mgr@alphadirect.co.bw',
            user=self.manager_user,
        )
        employee = self.make_employee(
            'E001',
            'Employee One',
            email='emp@alphadirect.co.bw',
            user=self.employee_user,
        )
        self.make_profile(employee, manager=manager)
        ack = EmployeeAcknowledgement.objects.create(
            employee=employee,
            kind='job_description',
            title='Job description',
            due_date=timezone.localdate() + timedelta(days=5),
            needs_manager=True,
        )

        client = APIClient()
        client.force_authenticate(self.employee_user)
        url = f'/api/v1/hris/acknowledgements/{ack.pk}/sign/'

        response = client.post(url)
        self.assertEqual(response.status_code, 200, response.content)
        ack.refresh_from_db()
        self.assertIsNotNone(ack.employee_signed_at)

        response = client.post(url)
        self.assertEqual(response.status_code, 400)

    def test_sign_manager_side(self):
        manager = self.make_employee(
            'M001',
            'Manager One',
            email='mgr@alphadirect.co.bw',
            user=self.manager_user,
        )
        employee = self.make_employee(
            'E001',
            'Employee One',
            email='emp@alphadirect.co.bw',
            user=self.employee_user,
        )
        self.make_profile(employee, manager=manager)
        ack = EmployeeAcknowledgement.objects.create(
            employee=employee,
            kind='job_description',
            title='Job description',
            due_date=timezone.localdate() + timedelta(days=5),
            needs_manager=True,
        )

        client = APIClient()
        client.force_authenticate(self.manager_user)
        url = f'/api/v1/hris/acknowledgements/{ack.pk}/sign/'

        response = client.post(url)
        self.assertEqual(response.status_code, 200, response.content)
        ack.refresh_from_db()
        self.assertIsNotNone(ack.manager_signed_at)
        self.assertEqual(ack.manager_signed_by, self.manager_user)

    def test_sign_stranger_denied(self):
        manager = self.make_employee(
            'M001',
            'Manager One',
            email='mgr@alphadirect.co.bw',
            user=self.manager_user,
        )
        employee = self.make_employee(
            'E001',
            'Employee One',
            email='emp@alphadirect.co.bw',
            user=self.employee_user,
        )
        self.make_profile(employee, manager=manager)
        ack = EmployeeAcknowledgement.objects.create(
            employee=employee,
            kind='job_description',
            title='Job description',
            due_date=timezone.localdate() + timedelta(days=5),
            needs_manager=True,
        )

        client = APIClient()
        client.force_authenticate(self.other_user)
        url = f'/api/v1/hris/acknowledgements/{ack.pk}/sign/'

        response = client.post(url)
        self.assertEqual(response.status_code, 403)

    def test_joiners_list_requires_hris_access(self):
        client = APIClient()
        client.force_authenticate(self.employee_user)

        response = client.get('/api/v1/hris/joiners/')
        self.assertEqual(response.status_code, 403)
