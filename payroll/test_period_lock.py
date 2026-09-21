"""Payroll period lock (CFO 2026-08-28): a closed month is frozen — no amendment
batch can be created or applied to it until Finance re-opens it.

Run in CI: manage.py test payroll.test_period_lock
"""
import datetime
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.models import (Employee, PayrollPeriod, PayrollAmendmentBatch,
                            PayrollAmendment, PayslipComponent)


class PeriodLockTests(APITestCase):
    def setUp(self):
        self.fc = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.clerk = User.objects.create_user('clerk', email='clerk@alphadirect.co.bw')
        UserProfile.objects.create(user=self.clerk, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title='hr_manager', is_active=True)
        self.co = Company.objects.create(code='TEST', name='Test Co.')
        self.base = PayrollPeriod.objects.create(period_name='2026-05',
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 5, 31))
        self.target = PayrollPeriod.objects.create(period_name='2026-06',
            start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 6, 30))

    def _lock(self, user, pk):
        self.client.force_authenticate(user)
        return self.client.post(reverse('payroll-period-lock', args=[pk]))

    def test_finance_lead_locks_and_unlocks(self):
        r = self._lock(self.fc, self.target.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, PayrollPeriod.Status.LOCKED)
        self.client.force_authenticate(self.fc)
        r2 = self.client.post(reverse('payroll-period-unlock', args=[self.target.id]))
        self.assertEqual(r2.status_code, 200, r2.content)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, PayrollPeriod.Status.OPEN)

    def test_non_finance_cannot_lock(self):
        r = self._lock(self.clerk, self.target.id)
        self.assertEqual(r.status_code, 403)

    def test_apply_refused_on_locked_period(self):
        self.target.status = PayrollPeriod.Status.LOCKED
        self.target.save(update_fields=['status'])
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        self.client.force_authenticate(self.fc)
        r = self.client.post(reverse('v1-payroll-amendments-apply', args=[batch.id]))
        self.assertEqual(r.status_code, 409)
        self.assertIn('locked', r.json()['detail'].lower())

    def test_status_patch_on_locked_period_refused(self):
        self.target.status = PayrollPeriod.Status.LOCKED
        self.target.save(update_fields=['status'])
        self.client.force_authenticate(self.fc)
        r = self.client.patch(reverse('payroll-period-detail', args=[self.target.id]),
                              {'status': 'open'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, PayrollPeriod.Status.LOCKED)  # still locked
