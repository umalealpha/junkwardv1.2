"""Onboarding department control (CFO file 2 Medium, 18-Sep-2026).

Acceptance: Finance, Claims and Underwriting requests go through; a blank or
invalid department is refused server-side; no silent "Operations" default;
the department shows on the approval queue; exact-email reuse and independent
approval still hold; an existing employee's department is never overwritten.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from core.models import Company
from hris.departments import DEPARTMENTS, canonical_department, fold_legacy
from hris.models import OnboardingRequest
from payroll.models import Employee

User = get_user_model()


class DepartmentListTest(SimpleTestCase):
    def test_list_is_the_fifteen_the_cfo_approved(self):
        self.assertEqual(len(DEPARTMENTS), 15)
        self.assertEqual(len(set(DEPARTMENTS)), 15)

    def test_positive_match_only(self):
        self.assertEqual(canonical_department('  claims '), 'Claims')
        self.assertEqual(canonical_department('FINANCE & PLANNING'), 'Finance & Planning')
        for bad in ('', None, 'Finance', 'Claim', 'Operations Dept', 'IT'):
            self.assertIsNone(canonical_department(bad), bad)

    def test_legacy_spellings_fold_for_reporting_only(self):
        self.assertEqual(fold_legacy('Uni Coin'), 'UniCoin')
        self.assertEqual(fold_legacy('hr'), 'Human Capital')
        self.assertEqual(fold_legacy('C-Suite'), 'Executive')
        self.assertIsNone(fold_legacy('Marketing Wizards'))


class OnboardingDepartmentApiTest(TestCase):
    def setUp(self):
        unlock = patch('hris.onboarding_views.is_hris_unlocked', return_value=True)
        unlock.start()
        self.addCleanup(unlock.stop)
        self.company = Company.objects.create(code='QDP', name='QC Department Co')
        self.maker = User.objects.create_superuser('dept_maker', 'dmaker@example.com', 'x')
        self.approver = User.objects.create_superuser('dept_approver', 'dapprover@example.com', 'x')
        self.base = {
            'full_name': 'Synthetic Dept Joiner', 'email': 'dept.joiner@example.com',
            'company_id': str(self.company.pk), 'job_title': 'Analyst',
            'hire_date': '2026-09-01',
        }

    def post(self, user, url, data):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.post(url, data, format='json')

    def submit(self, **extra):
        return self.post(self.maker, '/hris/api/onboard-employee/', dict(self.base, **extra))

    def test_finance_claims_underwriting_are_accepted(self):
        for i, dept in enumerate(('Finance & Planning', 'Claims', 'Underwriting')):
            r = self.submit(email=f'dept{i}@example.com', department=dept)
            self.assertEqual(r.status_code, 202, r.content)
            self.assertEqual(OnboardingRequest.objects.get(pk=r.data['id']).department, dept)

    def test_case_is_normalised_to_the_approved_spelling(self):
        r = self.submit(department='claims')
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.data['department'], 'Claims')

    def test_blank_department_is_refused(self):
        for blank in ('', '   ', None):
            r = self.submit(department=blank)
            self.assertEqual(r.status_code, 400, r.content)
        r = self.post(self.maker, '/hris/api/onboard-employee/', self.base)  # key missing
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(OnboardingRequest.objects.exists())

    def test_invalid_department_is_refused_not_defaulted(self):
        for bad in ('Finance', 'Marketing Wizards', 'Operations Dept'):
            r = self.submit(department=bad)
            self.assertEqual(r.status_code, 400, r.content)
            self.assertIn('not an approved department', str(r.data['detail']))
        self.assertFalse(OnboardingRequest.objects.exists())

    def test_options_endpoint_returns_the_list(self):
        client = APIClient()
        client.force_authenticate(user=self.maker)
        r = client.get('/hris/api/onboarding/options/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['departments'], list(DEPARTMENTS))

    def test_queue_carries_department_for_the_approver(self):
        self.submit(department='Underwriting')
        client = APIClient()
        client.force_authenticate(user=self.approver)
        r = client.get('/hris/api/onboarding/queue/?status=pending')
        self.assertEqual(r.data['requests'][0]['department'], 'Underwriting')

    def test_independent_approval_applies_department_to_new_employee(self):
        sub = self.submit(department='Claims')
        own = self.post(self.maker, f"/hris/api/onboarding/{sub.data['id']}/decide/",
                        {'action': 'approve', 'notes': 'self'})
        self.assertNotEqual(own.status_code, 200)
        ok = self.post(self.approver, f"/hris/api/onboarding/{sub.data['id']}/decide/",
                       {'action': 'approve', 'notes': 'Checked offer letter'})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(Employee.objects.get(email__iexact=self.base['email']).department, 'Claims')

    def test_existing_employee_department_is_never_overwritten_and_says_so(self):
        existing = Employee.objects.create(
            employee_number='DPT-001', full_name='Synthetic Dept Joiner',
            email=self.base['email'], company=self.company, department='Veritas')
        sub = self.submit(department='Claims')
        self.assertEqual(sub.status_code, 202, sub.content)
        codes = {w['code'] for w in sub.data['risk_warnings']}
        self.assertIn('department_differs', codes)
        ok = self.post(self.approver, f"/hris/api/onboarding/{sub.data['id']}/decide/",
                       {'action': 'approve', 'notes': 'Checked'})
        self.assertEqual(ok.status_code, 200, ok.content)
        existing.refresh_from_db()
        self.assertEqual(existing.department, 'Veritas')
        self.assertEqual(Employee.objects.filter(email__iexact=self.base['email']).count(), 1)
