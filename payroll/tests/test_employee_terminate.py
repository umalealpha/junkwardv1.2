"""Terminate Employee — feature request from D. Ikgopoleng (Omni bug report
dfc0b768-8ded-4610-8109-c142fb374bde, 2026-09-10), CFO-authorised 2026-09-10.

HR had no way to close a leaver's profile. Covers: a reason and a valid date
are mandatory; the exit is recorded and the status flipped; history is
RETAINED (never deleted); the Asset Control gate blocks a leaver still holding
kit; one reason-carrying audit row is written; and the action is restricted to
HR / Finance Managers.
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditLog
from payroll.archive_service import terminate_employee
from payroll.models import Employee

YESTERDAY = timezone.localdate() - timedelta(days=1)


def _active(**kwargs):
    defaults = dict(full_name='Active Person', employee_number='TRM-001',
                    status=Employee.Status.ACTIVE, hire_date=date(2024, 1, 1))
    defaults.update(kwargs)
    return Employee.objects.create(**defaults)


class TerminateServiceTest(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr_term', 'hr_term@test.com', 'pw')

    def test_records_the_exit_and_flips_status(self):
        emp = _active()
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        emp.refresh_from_db()
        self.assertEqual(emp.status, Employee.Status.TERMINATED)
        self.assertEqual(emp.termination_date, YESTERDAY)

    def test_profile_is_retained_not_deleted(self):
        """The whole point of the request: archive the profile, keep the history."""
        emp = _active(employee_number='TRM-KEEP')
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='retirement')
        self.assertTrue(Employee.objects.filter(pk=emp.pk).exists())
        self.assertEqual(Employee.objects.get(pk=emp.pk).employee_number, 'TRM-KEEP')

    def test_reason_is_mandatory_and_validated(self):
        emp = _active()
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY, reason='')
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                               reason='because i said so')
        emp.refresh_from_db()
        self.assertEqual(emp.status, Employee.Status.ACTIVE, 'must not have changed')

    def test_date_is_mandatory_and_sane(self):
        emp = _active()
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, termination_date=None, reason='dismissal')
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, reason='dismissal',
                               termination_date=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, reason='dismissal',
                               termination_date=date(2023, 1, 1))  # before hire_date

    def test_cannot_terminate_twice(self):
        emp = _active()
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY, reason='redundancy')
        with self.assertRaises(ValidationError):
            terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY, reason='redundancy')

    def test_writes_one_audit_row_carrying_the_reason(self):
        emp = _active()
        before = AuditLog.objects.filter(record_id=str(emp.pk)).count()
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='end_of_contract', notes='contract lapsed')
        # order_by('created_at'), NEVER order_by('-id'): AuditLog's pk is a UUID,
        # so '-id' is effectively random order and this assertion picked up the
        # CREATE row instead of the termination one. That is exactly how this
        # test failed intermittently in CI.
        rows = AuditLog.objects.filter(record_id=str(emp.pk)).order_by('created_at')
        self.assertEqual(rows.count(), before + 1)
        term_rows = [r for r in rows if 'Terminated' in (r.description or '')]
        self.assertEqual(len(term_rows), 1, 'exactly one termination audit row')
        self.assertIn('end_of_contract', term_rows[0].description)
        self.assertEqual(term_rows[0].new_values.get('termination_reason'), 'end_of_contract')

    def test_archive_flag_also_hides_the_record(self):
        # A leaver from BEFORE the offboarding rule started (19-Sep-2026) still
        # archives in one step. This date is pinned, not relative: when it was
        # `today - 1` the test passed all evening and turned red at midnight,
        # the moment "yesterday" became the 19th.
        emp = _active()
        terminate_employee(emp, actor=self.hr, termination_date=date(2026, 9, 1),
                           reason='resignation', archive=True)
        emp.refresh_from_db()
        self.assertTrue(emp.is_archived)

    def test_a_leaver_under_the_new_rule_cannot_be_archived_without_offboarding(self):
        """CFO/Unami 19-Sep-2026: offboarding is finished BEFORE Omni archives anyone."""
        emp = _active(employee_number='TRM-002')

        with self.assertRaises(ValidationError) as caught:
            terminate_employee(emp, actor=self.hr, termination_date=date(2026, 9, 19),
                               reason='resignation', archive=True)

        self.assertIn('offboarding case', str(caught.exception).lower())
        emp.refresh_from_db()
        # The exit is still RECORDED — only the hiding of the record is blocked.
        self.assertEqual(emp.status, Employee.Status.TERMINATED)
        self.assertFalse(emp.is_archived)


class _FakeHeldAsset:
    """Module-level on purpose: a class defined inside a test method cannot be
    pickled, and Django's parallel runner pickles failures between processes —
    a local class turns any failure here into 'cannot pickle' noise instead of
    the real assertion message."""
    tag_number = 'ADI-IT-0042'


class TerminateAssetGateTest(TestCase):
    """Guard must fail in BOTH directions: block while kit is held, allow once
    it is returned."""

    def setUp(self):
        self.hr = User.objects.create_superuser('hr_gate', 'hr_gate@test.com', 'pw')
        self.emp = _active(employee_number='TRM-ASSET')

    def test_blocked_while_holding_an_asset(self):
        with patch('assets.control_services.assets_blocking_offboarding',
                   return_value=[_FakeHeldAsset()]):
            with self.assertRaises(ValidationError) as ctx:
                terminate_employee(self.emp, actor=self.hr, termination_date=YESTERDAY,
                                   reason='resignation')
        self.assertIn('ADI-IT-0042', str(ctx.exception))
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, Employee.Status.ACTIVE)

    def test_allowed_once_returned(self):
        with patch('assets.control_services.assets_blocking_offboarding', return_value=[]):
            terminate_employee(self.emp, actor=self.hr, termination_date=YESTERDAY,
                               reason='resignation')
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, Employee.Status.TERMINATED)


class TerminateApiPermissionTest(TestCase):
    def setUp(self):
        self.emp = _active(employee_number='TRM-API')
        self.client = APIClient()

    def test_ordinary_staff_cannot_terminate(self):
        plain = User.objects.create_user('plain_user', 'plain@test.com', 'pw')
        self.client.force_authenticate(user=plain)
        resp = self.client.post(f'/api/v1/employees/{self.emp.pk}/terminate/',
                                {'termination_date': str(YESTERDAY), 'reason': 'resignation'},
                                format='json')
        self.assertIn(resp.status_code, (401, 403))
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, Employee.Status.ACTIVE)

    def test_hr_can_terminate(self):
        hr = User.objects.create_superuser('hr_api', 'hr_api@test.com', 'pw')
        self.client.force_authenticate(user=hr)
        resp = self.client.post(f'/api/v1/employees/{self.emp.pk}/terminate/',
                                {'termination_date': str(YESTERDAY), 'reason': 'resignation'},
                                format='json')
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.status, Employee.Status.TERMINATED)
