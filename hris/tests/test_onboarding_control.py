from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditLog, Company, UserCompanyAccess, UserProfile
from hris.models import HRISProfile, OnboardingRequest, OnboardingTask
from payroll.models import Employee

User = get_user_model()


class OnboardingControlApiTest(TestCase):
    def setUp(self):
        self.unlock = patch('hris.onboarding_views.is_hris_unlocked', return_value=True)
        self.unlock.start()
        self.addCleanup(self.unlock.stop)

        self.company = Company.objects.create(code='QCO', name='QC Onboarding Co')
        self.other_company = Company.objects.create(code='QCX', name='QC Other Co')
        self.maker = User.objects.create_superuser('onboard_maker', 'maker@example.com', 'x')
        self.approver = User.objects.create_superuser('onboard_approver', 'approver@example.com', 'x')
        self.plain = User.objects.create_user('plain_user', 'plain@example.com', 'x')

        self.manager_user = User.objects.create_user('line_manager', 'manager@example.com', 'x')
        self.manager = Employee.objects.create(
            employee_number='MGR-001', full_name='Line Manager', email='manager@example.com',
            company=self.company, user=self.manager_user,
        )
        self.payload = {
            'full_name': 'Synthetic New Joiner',
            'email': 'synthetic.joiner@example.com',
            'employee_number': 'QCO-001',
            'company_id': str(self.company.pk),
            'department': 'Operations',
            'job_title': 'Analyst',
            'hire_date': '2026-09-01',
            'phone': '+267 71 111 222',
            'manager_id': str(self.manager.pk),
            'leave_approver_id': str(self.manager_user.pk),
        }

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def submit(self, user=None, payload=None):
        return self.client_for(user or self.maker).post(
            '/hris/api/onboard-employee/', payload or self.payload, format='json')

    def approve(self, request_id, user=None, notes='Reviewed against offer letter'):
        return self.client_for(user or self.approver).post(
            f'/hris/api/onboarding/{request_id}/decide/',
            {'action': 'approve', 'notes': notes}, format='json')

    def test_submission_creates_pending_request_not_employee(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.content)
        self.assertTrue(response.data['pending_approval'])
        self.assertEqual(response.data['status'], OnboardingRequest.Status.PENDING)
        self.assertFalse(Employee.objects.filter(email__iexact=self.payload['email']).exists())
        self.assertEqual(OnboardingRequest.objects.filter(email=self.payload['email']).count(), 1)

    def test_independent_approval_activates_employee_assignments_and_checklist(self):
        submitted = self.submit()
        approved = self.approve(submitted.data['id'])
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.data['status'], OnboardingRequest.Status.APPROVED)

        employee = Employee.objects.get(email__iexact=self.payload['email'])
        profile = HRISProfile.objects.get(employee=employee)
        self.assertEqual(profile.manager_id, self.manager.pk)
        self.assertEqual(profile.default_leave_approver_id, self.manager_user.pk)
        self.assertEqual(OnboardingTask.objects.filter(employee=employee).count(), 5)
        request_row = OnboardingRequest.objects.get(pk=submitted.data['id'])
        self.assertEqual(request_row.employee_id, employee.pk)
        events = AuditLog.objects.filter(
            table_name='OnboardingRequest', record_id=str(request_row.pk))
        self.assertTrue(events.filter(new_values__event='submitted').exists())
        self.assertTrue(events.filter(new_values__event='approved').exists())

    def test_maker_cannot_approve_own_request(self):
        submitted = self.submit()
        denied = self.approve(submitted.data['id'], user=self.maker)
        self.assertEqual(denied.status_code, 400)
        self.assertIn('cannot approve', str(denied.data['detail']).lower())
        self.assertFalse(Employee.objects.filter(email__iexact=self.payload['email']).exists())

    def test_identical_retry_reuses_pending_request(self):
        first = self.submit()
        second = self.submit()
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.data['idempotent_retry'])
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(OnboardingRequest.objects.count(), 1)

    def test_changed_retry_is_rejected_as_conflict(self):
        first = self.submit()
        changed = dict(self.payload, job_title='Different Role')
        second = self.submit(payload=changed)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 400)
        self.assertIn('different onboarding request', str(second.data['detail']).lower())
        self.assertEqual(OnboardingRequest.objects.count(), 1)

    def test_repeated_approval_is_idempotent(self):
        submitted = self.submit()
        first = self.approve(submitted.data['id'])
        second = self.approve(submitted.data['id'])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['idempotent_replay'])
        self.assertEqual(Employee.objects.filter(email__iexact=self.payload['email']).count(), 1)
        employee = Employee.objects.get(email__iexact=self.payload['email'])
        self.assertEqual(OnboardingTask.objects.filter(employee=employee).count(), 5)

    def test_existing_payroll_only_employee_is_completed_without_duplicate(self):
        existing = Employee.objects.create(
            employee_number='OLD-001', full_name='Synthetic New Joiner',
            email=self.payload['email'], company=self.company,
        )
        submitted = self.submit(payload=dict(self.payload, employee_number='OLD-001'))
        approved = self.approve(submitted.data['id'])
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(Employee.objects.filter(email__iexact=self.payload['email']).count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.job_title, 'Analyst')
        profile = HRISProfile.objects.get(employee=existing)
        self.assertEqual(profile.manager_id, self.manager.pk)

    def test_duplicate_risk_warnings_are_saved_for_name_phone_and_similar_email(self):
        Employee.objects.create(
            employee_number='OLD-002', full_name=self.payload['full_name'],
            email='synthetic.joiner2@example.com', phone=self.payload['phone'],
            company=self.company,
        )
        submitted = self.submit()
        self.assertEqual(submitted.status_code, 202, submitted.content)
        codes = {warning['code'] for warning in submitted.data['risk_warnings']}
        self.assertIn('matching_name', codes)
        self.assertIn('matching_phone', codes)
        self.assertIn('similar_email', codes)

    def test_rejection_requires_reason_and_never_creates_employee(self):
        submitted = self.submit()
        no_reason = self.client_for(self.approver).post(
            f"/hris/api/onboarding/{submitted.data['id']}/decide/",
            {'action': 'reject', 'notes': ''}, format='json')
        self.assertEqual(no_reason.status_code, 400)
        rejected = self.client_for(self.approver).post(
            f"/hris/api/onboarding/{submitted.data['id']}/decide/",
            {'action': 'reject', 'notes': 'Offer not approved'}, format='json')
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.data['status'], OnboardingRequest.Status.REJECTED)
        self.assertFalse(Employee.objects.filter(email__iexact=self.payload['email']).exists())

    def test_non_hr_user_is_blocked(self):
        response = self.submit(user=self.plain)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(OnboardingRequest.objects.count(), 0)

    def test_required_fields_and_cross_entity_assignment_are_validated(self):
        missing = self.submit(payload=dict(self.payload, full_name=''))
        self.assertEqual(missing.status_code, 400)
        self.assertIn('full name', str(missing.data['detail']).lower())
        other_manager = Employee.objects.create(
            employee_number='MGR-X', full_name='Other Manager',
            email='other.manager@example.com', company=self.other_company,
        )
        cross = self.submit(payload=dict(self.payload, manager_id=str(other_manager.pk)))
        self.assertEqual(cross.status_code, 400)
        self.assertIn('chosen entity', str(cross.data['detail']).lower())

    def test_entity_scoped_hr_sees_and_writes_only_granted_company(self):
        scoped = User.objects.create_user('scoped_hr', 'scoped.hr@example.com', 'x')
        UserProfile.objects.create(
            user=scoped, role=UserProfile.Role.OPERATIONS_STAFF,
            title=UserProfile.Title.HR_MANAGER,
            hris_unlocked_until=timezone.now() + timezone.timedelta(hours=1),
        )
        Employee.objects.create(
            employee_number='HR-001', full_name='Scoped HR', email=scoped.email,
            company=self.company, user=scoped,
        )
        UserCompanyAccess.objects.create(
            user=scoped, company=self.company, can_view=True, can_write=True,
        )
        options = self.client_for(scoped).get('/hris/api/onboarding/options/')
        self.assertEqual(options.status_code, 200, options.content)
        self.assertEqual({row['id'] for row in options.data['companies']}, {str(self.company.pk)})
        denied = self.submit(user=scoped, payload=dict(
            self.payload, email='outside@example.com', company_id=str(self.other_company.pk),
            manager_id=None, leave_approver_id=None,
        ))
        self.assertEqual(denied.status_code, 400)
        self.assertIn('write access', str(denied.data['detail']).lower())
