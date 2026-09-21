"""Onboarding captures annual leave entitlement and seeds accrual (bug c48f10b0).

Oprah Mogomotsi, 2026-09-03: a newly onboarded employee shows 0 entitlement and
0 accrued on the Leave Report until a manual CSV upload is done, because the
accrual engine only accrues a profile that has a LeaveOpeningBalance row. These
tests pin that onboarding now captures the annual entitlement and, on approval,
writes that row so the person appears on the Leave Report and accrues from their
start date.
"""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Company
from hris.leave_balance import balances_for_profile
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import Employee

User = get_user_model()


class OnboardingLeaveEntitlementTest(TestCase):
    def setUp(self):
        self.unlock = patch('hris.onboarding_views.is_hris_unlocked', return_value=True)
        self.unlock.start()
        self.addCleanup(self.unlock.stop)

        self.company = Company.objects.create(code='LEO', name='Leave Entitlement Co')
        self.maker = User.objects.create_superuser('leo_maker', 'leo_maker@example.com', 'x')
        self.approver = User.objects.create_superuser('leo_approver', 'leo_approver@example.com', 'x')
        # Start date three whole months ago so accrual has something to show.
        self.start = timezone.localdate().replace(day=1) - timedelta(days=95)
        self.payload = {
            'full_name': 'New Joiner Leave',
            'email': 'new.joiner.leave@example.com',
            'company_id': str(self.company.pk),
            'department': 'Operations',
            'job_title': 'Analyst',
            'hire_date': self.start.isoformat(),
            'annual_leave_entitlement': '25',
        }

    def _submit(self, payload=None):
        c = APIClient(); c.force_authenticate(self.maker)
        return c.post('/hris/api/onboard-employee/', payload or self.payload, format='json')

    def _approve(self, request_id):
        c = APIClient(); c.force_authenticate(self.approver)
        return c.post(f'/hris/api/onboarding/{request_id}/decide/',
                      {'action': 'approve', 'notes': 'Reviewed against offer letter'},
                      format='json')

    def _profile(self):
        emp = Employee.objects.get(email__iexact=self.payload['email'])
        return HRISProfile.objects.get(employee=emp)

    def test_approval_seeds_annual_opening_balance_from_captured_entitlement(self):
        submitted = self._submit()
        self.assertEqual(submitted.status_code, 202, submitted.content)
        approved = self._approve(submitted.data['id'])
        self.assertEqual(approved.status_code, 200, approved.content)

        ob = LeaveOpeningBalance.objects.get(
            profile=self._profile(), leave_type_code='annual')
        self.assertEqual(ob.entitlement_days, Decimal('25.000'))
        self.assertEqual(ob.opening_balance_days, Decimal('0.00'))
        self.assertEqual(ob.as_at_date, self.start)

    def test_new_hire_appears_on_leave_report_and_accrues_from_start(self):
        submitted = self._submit()
        self._approve(submitted.data['id'])
        rows = {r['code']: r for r in balances_for_profile(self._profile())}
        annual = rows['annual']
        self.assertEqual(annual['days'], 25.0)             # entitlement visible, not 0
        self.assertTrue(annual['accrues'])
        self.assertEqual(annual['source'], 'opening_balance')
        # Three whole months since start -> 25 * 3/12 = 6.25 (not zero, not full).
        self.assertGreater(annual['accrued'], 0.0)
        self.assertLess(annual['accrued'], 25.0)

    def test_blank_entitlement_falls_back_to_cos_default(self):
        payload = dict(self.payload); payload.pop('annual_leave_entitlement')
        submitted = self._submit(payload)
        self._approve(submitted.data['id'])
        ob = LeaveOpeningBalance.objects.get(
            profile=self._profile(), leave_type_code='annual')
        self.assertEqual(ob.entitlement_days, Decimal('21.000'))
