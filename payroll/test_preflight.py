"""Payroll pre-flight dry-run (CFO 2026-08-28): what would change + what looks
wrong, before Apply, without writing. Run: manage.py test payroll.test_preflight
"""
import datetime
from decimal import Decimal
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.models import (Employee, Payslip, PayslipLine, PayslipComponent,
                            PayrollPeriod, PayrollAmendment, PayrollAmendmentBatch)


class PreflightTests(APITestCase):
    def setUp(self):
        self.fc = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.co = Company.objects.create(code='TEST', name='Test Co.')
        self.base = PayrollPeriod.objects.create(period_name='2026-05',
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 5, 31))
        self.target = PayrollPeriod.objects.create(period_name='2026-06',
            start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 6, 30))
        self.basic = PayslipComponent.objects.create(code='BASIC', name='Basic',
            kind=PayslipComponent.Kind.EARNING, is_taxable=True, sort_order=1)
        self.fuel = PayslipComponent.objects.create(code='FUEL', name='Fuel',
            kind=PayslipComponent.Kind.EARNING, is_taxable=True, sort_order=2)
        # Baseline: Alice has a payslip (gross 5000); Bob does NOT.
        self.alice = Employee.objects.create(employee_number='E1', full_name='Alice A', company=self.co)
        self.bob = Employee.objects.create(employee_number='E2', full_name='Bob B', company=self.co)
        ps = Payslip.objects.create(employee=self.alice, period=self.base, company=self.co,
                                    status=Payslip.Status.DRAFT, gross_amount=Decimal('5000'))
        PayslipLine.objects.create(payslip=ps, component=self.basic, amount=Decimal('5000'))
        self.batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base, company=self.co,
            uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)

    def _amd(self, **kw):
        return PayrollAmendment.objects.create(batch=self.batch, **kw)

    def _get(self):
        self.client.force_authenticate(self.fc)
        return self.client.get(reverse('v1-payroll-amendments-preflight', args=[self.batch.id]))

    def test_preflight_summary_and_exceptions(self):
        # Alice: fuel +500 (fine); duplicate fuel row; Bob: allowance but no baseline;
        # one unresolved row; one hire; one terminate.
        self._amd(employee=self.alice, kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=self.fuel, amount=Decimal('500'), employee_ref='E1')
        self._amd(employee=self.alice, kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=self.fuel, amount=Decimal('200'), employee_ref='E1')  # duplicate (alice,fuel)
        self._amd(employee=self.bob, kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=self.fuel, amount=Decimal('300'), employee_ref='E2')  # no baseline payslip
        self._amd(employee=None, kind=PayrollAmendment.Kind.BONUS, amount=Decimal('100'), employee_ref='Ghost', resolution_error='employee not found')
        self._amd(employee=self.bob, kind=PayrollAmendment.Kind.HIRE, amount=Decimal('4000'), employee_ref='E2')
        self._amd(employee=self.alice, kind=PayrollAmendment.Kind.TERMINATE, amount=Decimal('0'), employee_ref='E1')
        r = self._get()
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['rows'], 6)
        self.assertEqual(d['headcount']['joiners'], 1)
        self.assertEqual(d['headcount']['leavers'], 1)
        self.assertEqual(d['exceptions']['unmatched_count'], 1)
        self.assertEqual(d['exceptions']['duplicate_component_rows'], 1)   # (alice, fuel) twice
        self.assertIn('Bob B', d['exceptions']['no_baseline_payslip'])
        self.assertFalse(d['ready'])   # exceptions present
        # gross estimate: 500 + 200 + 300 = 1000 add (hire/terminate/unresolved excluded)
        self.assertEqual(Decimal(d['gross']['estimated_change']), Decimal('1000'))

    def test_clean_batch_is_ready(self):
        self._amd(employee=self.alice, kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=self.fuel, amount=Decimal('500'), employee_ref='E1')
        d = self._get().json()
        self.assertTrue(d['ready'])
        self.assertEqual(d['exceptions']['unmatched_count'], 0)

    def test_requires_payroll_authority(self):
        nobody = User.objects.create_user('nobody', email='n@x.com')
        self.client.force_authenticate(nobody)
        r = self.client.get(reverse('v1-payroll-amendments-preflight', args=[self.batch.id]))
        self.assertIn(r.status_code, (401, 403))
