"""Regression: applying a SECOND amendment batch in a month must NOT wipe the
first (CFO 2026-08-28 / Fable). Before the compose fix, apply re-copied the
baseline and applied only its own rows, so a partial batch (e.g. the
auto-incentive batch) silently reverted an already-applied manual batch.

Run in CI: manage.py test payroll.test_amendment_compose
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.models import (Employee, Payslip, PayslipLine, PayslipComponent,
                            PayrollPeriod, PayrollAmendment, PayrollAmendmentBatch)


class ComposeApplyTests(APITestCase):
    def setUp(self):
        self.fc = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.co = Company.objects.create(code='TEST', name='Test Co.')
        self.base = PayrollPeriod.objects.create(period_name='2026-05',
                        start_date='2026-05-01', end_date='2026-05-31')
        self.target = PayrollPeriod.objects.create(period_name='2026-06',
                        start_date='2026-06-01', end_date='2026-06-30')
        self.emp = Employee.objects.create(employee_number='E1', full_name='Alice A', company=self.co)
        self.basic = PayslipComponent.objects.create(code='BASIC', name='Basic',
                        kind=PayslipComponent.Kind.EARNING, is_taxable=True, sort_order=1)
        self.fuel = PayslipComponent.objects.create(code='FUEL', name='Fuel allowance',
                        kind=PayslipComponent.Kind.EARNING, is_taxable=True, sort_order=2)
        self.incentive = PayslipComponent.objects.create(code='INCENTIVE', name='Incentive',
                        kind=PayslipComponent.Kind.EARNING, is_taxable=True, sort_order=3)
        # Baseline payslip so the target copy has a row to amend.
        ps = Payslip.objects.create(employee=self.emp, period=self.base, company=self.co,
                                    status=Payslip.Status.DRAFT, gross_amount=Decimal('5000'))
        PayslipLine.objects.create(payslip=ps, component=self.basic, amount=Decimal('5000'))

    def _batch(self, component, amount, marker):
        b = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base, company=self.co,
            file_name=marker, uploaded_by=self.fc, status=PayrollAmendmentBatch.Status.PARSED)
        PayrollAmendment.objects.create(batch=b, employee=self.emp,
            kind=PayrollAmendment.Kind.ALLOWANCE_ADD, component=component,
            amount=Decimal(amount), employee_ref='E1')
        return b

    def _apply(self, batch):
        self.client.force_authenticate(self.fc)
        r = self.client.post(reverse('v1-payroll-amendments-apply', args=[batch.id]))
        self.assertEqual(r.status_code, 200, r.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.APPLIED)

    def _target_line(self, component):
        ps = Payslip.objects.get(employee=self.emp, period=self.target)
        ln = PayslipLine.objects.filter(payslip=ps, component=component).first()
        return ln.amount if ln else None

    def test_manual_then_incentive_both_survive(self):
        self._apply(self._batch(self.fuel, '500', 'MANUAL'))
        self._apply(self._batch(self.incentive, '300', 'AUTO-INCENTIVE'))
        self.assertEqual(self._target_line(self.fuel), Decimal('500'))      # NOT wiped
        self.assertEqual(self._target_line(self.incentive), Decimal('300'))

    def test_incentive_then_manual_both_survive(self):
        self._apply(self._batch(self.incentive, '300', 'AUTO-INCENTIVE'))
        self._apply(self._batch(self.fuel, '500', 'MANUAL'))
        self.assertEqual(self._target_line(self.fuel), Decimal('500'))
        self.assertEqual(self._target_line(self.incentive), Decimal('300'))  # NOT wiped

    def test_same_component_last_wins_no_double(self):
        # A full re-upload replaces (v2/v3/v5 semantics) — must NOT sum to 1200.
        self._apply(self._batch(self.fuel, '500', 'FUEL-v1'))
        self._apply(self._batch(self.fuel, '700', 'FUEL-v2'))
        self.assertEqual(self._target_line(self.fuel), Decimal('700'))
