"""payroll/tests/test_import_discard.py — a not-yet-approved payroll import can
be discarded so a wrong upload can be replaced (Tshephang / Veritas 2026-07-24:
an entity processor's draft got stuck — approve is SoD-blocked and reject needs
an approver title, so there was no way to remove a wrong file).

Run in CI: manage.py test payroll.tests.test_import_discard
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company
from payroll.models import PayrollImportBatch, PayrollPeriod

U = get_user_model()


class DiscardImportTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='VCM', name='Veritas Capital')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')
        self.approver = U.objects.create_superuser('fm', 'fm@alphadirect.co.bw', 'x')
        self.other = U.objects.create_user('stranger', 'stranger@alphadirect.co.bw', 'x')

    def _batch(self, status=PayrollImportBatch.Status.DRAFT, created_by=None):
        return PayrollImportBatch.objects.create(
            source='odoo', file_name='veritas.xlsx', period=self.period, company=self.co,
            rows_total=6, rows_valid=6, rows_invalid=0, parsed_rows=[], validation_errors=[],
            status=status, created_by=created_by)

    def _post(self, user, pk):
        c = APIClient(); c.force_authenticate(user=user)
        return c.post(f'/api/v1/payroll-imports/{pk}/discard/')

    def test_approver_discards_draft(self):
        b = self._batch(created_by=self.other)
        r = self._post(self.approver, b.id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(PayrollImportBatch.objects.filter(pk=b.id).exists())

    def test_cannot_discard_approved(self):
        b = self._batch(status=PayrollImportBatch.Status.APPROVED, created_by=self.other)
        r = self._post(self.approver, b.id)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(PayrollImportBatch.objects.filter(pk=b.id).exists())

    def test_cannot_discard_committed(self):
        b = self._batch(status=PayrollImportBatch.Status.COMMITTED, created_by=self.approver)
        r = self._post(self.approver, b.id)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(PayrollImportBatch.objects.filter(pk=b.id).exists())
