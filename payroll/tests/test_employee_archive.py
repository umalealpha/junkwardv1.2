"""Terminated Employee Archive — feature request from Oprah Mogomotsi
(Omni bug report d0f05edc-06f5-4084-94ce-b613e54e7665, 2026-08-13).

Covers: archive/unarchive require a reason; archive requires a termination
date; archived staff are hidden from the default employee list; archived
payroll fields are read-only; every archive/unarchive writes an audit row;
retention-expiry flagging (no auto-purge). Also covers the pay-run entry
points a Fable review (2026-08-13) found were NOT excluding archived staff:
the amendment/final-settlement resolver, the roll-forward baseline-copy, the
Smart Upload importer, and hard delete via the API.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import AuditLog, Company
from payroll.amendment_views import _resolve_employee
from payroll.archive_service import (
    archive_employee, records_nearing_retention_expiry, unarchive_employee,
)
from payroll.importer import commit_payroll_import
from payroll.models import Employee, PayrollImportBatch, PayrollPeriod, Payslip


def _make_employee(**kwargs):
    defaults = dict(full_name='Test Leaver', employee_number='T-001',
                     status=Employee.Status.TERMINATED,
                     termination_date=date(2026, 1, 15))
    defaults.update(kwargs)
    return Employee.objects.create(**defaults)


class ArchiveServiceTest(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr_user', 'hr@test.com', 'pw')

    def test_archive_requires_reason(self):
        emp = _make_employee()
        with self.assertRaises(ValidationError):
            archive_employee(emp, actor=self.hr, reason='')

    def test_archive_requires_termination_date(self):
        emp = _make_employee(termination_date=None, status=Employee.Status.ACTIVE)
        with self.assertRaises(ValidationError):
            archive_employee(emp, actor=self.hr, reason='Left the company')

    def test_archive_sets_fields_and_logs_audit(self):
        emp = _make_employee()
        before = AuditLog.objects.count()
        archive_employee(emp, actor=self.hr, reason='Resigned — notice served')
        emp.refresh_from_db()
        self.assertTrue(emp.is_archived)
        self.assertEqual(emp.archive_reason, 'Resigned — notice served')
        self.assertIsNotNone(emp.archived_at)
        self.assertEqual(emp.archived_by, self.hr)
        self.assertEqual(AuditLog.objects.count(), before + 1)
        row = AuditLog.objects.latest('created_at')
        self.assertEqual(row.table_name, 'payroll.Employee')
        self.assertIn('Archived', row.description)

    def test_cannot_archive_twice(self):
        emp = _make_employee()
        archive_employee(emp, actor=self.hr, reason='Resigned')
        with self.assertRaises(ValidationError):
            archive_employee(emp, actor=self.hr, reason='Again')

    def test_unarchive_requires_reason(self):
        emp = _make_employee()
        archive_employee(emp, actor=self.hr, reason='Resigned')
        with self.assertRaises(ValidationError):
            unarchive_employee(emp, actor=self.hr, reason='')

    def test_unarchive_clears_flag_and_logs_audit(self):
        emp = _make_employee()
        archive_employee(emp, actor=self.hr, reason='Resigned')
        before = AuditLog.objects.count()
        unarchive_employee(emp, actor=self.hr, reason='Rehired')
        emp.refresh_from_db()
        self.assertFalse(emp.is_archived)
        self.assertEqual(AuditLog.objects.count(), before + 1)
        self.assertIn('Unarchived', AuditLog.objects.latest('created_at').description)

    def test_cannot_unarchive_when_not_archived(self):
        emp = _make_employee()
        with self.assertRaises(ValidationError):
            unarchive_employee(emp, actor=self.hr, reason='Rehired')

    def test_retention_expiry_default_seven_years(self):
        emp = _make_employee(termination_date=date(2026, 1, 15))
        self.assertEqual(emp.retention_expiry_date, date(2033, 1, 15))

    def test_retention_years_configurable_per_record(self):
        emp = _make_employee(termination_date=date(2026, 1, 15))
        archive_employee(emp, actor=self.hr, reason='Resigned', retention_years=10)
        emp.refresh_from_db()
        self.assertEqual(emp.retention_years, 10)
        self.assertEqual(emp.retention_expiry_date, date(2036, 1, 15))

    def test_archive_forces_status_terminated(self):
        """Fable review (2026-08-13): archiving only checked termination_date,
        so a record whose status was never flipped to TERMINATED could be
        archived yet still pass every pay-run check that keys off `status`
        alone. Archive must force status too."""
        emp = _make_employee(status=Employee.Status.ACTIVE, termination_date=date(2026, 1, 15))
        archive_employee(emp, actor=self.hr, reason='Resigned, status was never updated')
        emp.refresh_from_db()
        self.assertEqual(emp.status, Employee.Status.TERMINATED)

    def test_records_nearing_expiry_flagged_within_window(self):
        soon = date.today() - timedelta(days=7 * 365 - 30)  # ~1 month from 7yr expiry
        far = date.today() - timedelta(days=365)            # ~6 years from expiry
        emp_soon = _make_employee(employee_number='T-002', termination_date=soon)
        emp_far = _make_employee(employee_number='T-003', termination_date=far)
        archive_employee(emp_soon, actor=self.hr, reason='Resigned')
        archive_employee(emp_far, actor=self.hr, reason='Resigned')
        due = records_nearing_retention_expiry(within_days=90)
        self.assertIn(emp_soon, due)
        self.assertNotIn(emp_far, due)

    def test_no_auto_purge(self):
        """Archiving/expiry flagging never deletes the row — data retained."""
        emp = _make_employee()
        archive_employee(emp, actor=self.hr, reason='Resigned')
        records_nearing_retention_expiry(within_days=36500)
        self.assertTrue(Employee.objects.filter(pk=emp.pk).exists())


class ArchiveApiTest(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr_api', 'hrapi@test.com', 'pw')
        self.staff = User.objects.create_user('plain_staff', 'staff@test.com', 'pw')
        self.emp = _make_employee(employee_number='T-100')
        self.client = APIClient()

    def test_default_list_hides_archived(self):
        archive_employee(self.emp, actor=self.hr, reason='Resigned')
        self.client.force_authenticate(self.hr)
        resp = self.client.get('/api/v1/employees/')
        ids = [r['id'] for r in resp.data['results']]
        self.assertNotIn(str(self.emp.pk), ids)

    def test_non_hr_cannot_archive(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(f'/api/v1/employees/{self.emp.pk}/archive/',
                                 {'reason': 'Resigned'})
        self.assertEqual(resp.status_code, 403)

    def test_hr_can_archive_via_api(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.post(f'/api/v1/employees/{self.emp.pk}/archive/',
                                 {'reason': 'Resigned — notice served'})
        self.assertEqual(resp.status_code, 200)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.is_archived)

    def test_archived_employee_cannot_be_edited(self):
        archive_employee(self.emp, actor=self.hr, reason='Resigned')
        self.client.force_authenticate(self.hr)
        resp = self.client.patch(f'/api/v1/employees/{self.emp.pk}/?archived=1',
                                  {'job_title': 'New Title'})
        self.assertEqual(resp.status_code, 403)

    def test_unarchive_via_api_requires_reason(self):
        archive_employee(self.emp, actor=self.hr, reason='Resigned')
        self.client.force_authenticate(self.hr)
        resp = self.client.post(f'/api/v1/employees/{self.emp.pk}/unarchive/', {'reason': ''})
        self.assertEqual(resp.status_code, 400)

    def test_archived_employee_cannot_be_deleted(self):
        """Fable review (2026-08-13): archive means retain, never delete —
        the API had no destroy() guard at all."""
        archive_employee(self.emp, actor=self.hr, reason='Resigned')
        self.client.force_authenticate(self.hr)
        resp = self.client.delete(f'/api/v1/employees/{self.emp.pk}/?archived=1')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Employee.objects.filter(pk=self.emp.pk).exists())


class ArchivedExcludedFromPayRunsTest(TestCase):
    """Fable review (2026-08-13): archiving hid an employee from the employee
    LIST, but three real pay-run entry points never checked is_archived at
    all — an archived leaver could still be re-matched into a final
    settlement, seeded into next month's roll-forward, or given a fresh
    payslip via a Smart Upload import. All three are fixed here."""

    def setUp(self):
        self.hr = User.objects.create_superuser('hr_payrun', 'hrpayrun@test.com', 'pw')
        self.co = Company.objects.create(code='ZZA', name='Archive Test Co')
        self.emp = Employee.objects.create(
            full_name='Archived Leaver', employee_number='ARCH-001', company=self.co,
            status=Employee.Status.TERMINATED, termination_date=date(2026, 1, 15))
        archive_employee(self.emp, actor=self.hr, reason='Resigned')

    def test_amendment_resolver_will_not_match_archived_employee(self):
        emp, note = _resolve_employee('ARCH-001', self.co)
        self.assertIsNone(emp)
        emp, note = _resolve_employee('Archived Leaver', self.co)
        self.assertIsNone(emp)

    def test_importer_skips_archived_employee_no_new_payslip(self):
        per = PayrollPeriod.objects.create(
            period_name='2099-02', start_date=date(2099, 2, 1),
            end_date=date(2099, 2, 28), pay_date=date(2099, 2, 25))
        batch = PayrollImportBatch.objects.create(
            company=self.co, period=per, source='test', created_by=self.hr,
            parsed_rows=[{'row_index': 1, 'Employee': 'Archived Leaver',
                          'Gross': '10000', 'Net Salary': '8000'}])
        commit_payroll_import(batch, self.hr)
        self.assertEqual(Payslip.objects.filter(period=per, employee=self.emp).count(), 0)


class BulkArchiveApiTest(TestCase):
    """Bulk archive (Unami Butale feature request, 2026-08-22): archive several
    terminated leavers in one HR action, one shared reason — each still routed
    through the single-record service, so a leaver without a termination date is
    reported as skipped rather than silently dropped."""

    URL = '/api/v1/employees/bulk-archive/'

    def setUp(self):
        self.hr = User.objects.create_superuser('hr_bulk', 'hrbulk@test.com', 'pw')
        self.staff = User.objects.create_user('plain_bulk', 'plainbulk@test.com', 'pw')
        self.a = _make_employee(employee_number='B-001')
        self.b = _make_employee(employee_number='B-002')
        self.client = APIClient()

    def test_hr_can_bulk_archive(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.post(self.URL, {'ids': [str(self.a.pk), str(self.b.pk)],
                                           'reason': 'Year-end offboarding batch'},
                                format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['archived'], 2)
        self.assertEqual(resp.data['skipped'], 0)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertTrue(self.a.is_archived and self.b.is_archived)

    def test_bulk_archive_skips_leaver_without_termination_date(self):
        no_date = _make_employee(employee_number='B-003', termination_date=None,
                                 status=Employee.Status.ACTIVE)
        self.client.force_authenticate(self.hr)
        resp = self.client.post(self.URL, {'ids': [str(self.a.pk), str(no_date.pk)],
                                           'reason': 'Batch archive'},
                                format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['archived'], 1)
        self.assertEqual(resp.data['skipped'], 1)
        bad = next(r for r in resp.data['results'] if r['id'] == str(no_date.pk))
        self.assertFalse(bad['ok'])
        self.assertIn('termination date', bad['detail'].lower())
        no_date.refresh_from_db()
        self.assertFalse(no_date.is_archived)

    def test_non_hr_cannot_bulk_archive(self):
        self.client.force_authenticate(self.staff)
        resp = self.client.post(self.URL, {'ids': [str(self.a.pk)], 'reason': 'x'},
                                format='json')
        self.assertEqual(resp.status_code, 403)

    def test_bulk_archive_requires_reason(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.post(self.URL, {'ids': [str(self.a.pk)], 'reason': '  '},
                                format='json')
        self.assertEqual(resp.status_code, 400)

    def test_bulk_archive_requires_ids(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.post(self.URL, {'ids': [], 'reason': 'x'}, format='json')
        self.assertEqual(resp.status_code, 400)


class RegisterHidesArchivedTest(TestCase):
    """Oprah Mogomotsi, 2026-08-24: an archived leaver still showed on the
    Payroll Register (PayslipViewSet listing) — a draft created before the
    person was archived lingered, because the register never filtered
    is_archived. Roll-forward already skips archived staff (see
    ArchivedExcludedFromPayRunsTest), so this is purely the *listing*.

    A TERMINATED-but-not-yet-archived leaver is deliberately NOT hidden here —
    a final-settlement payslip must stay visible/payable until HR archives the
    person after paying it (mirrors _resolve_employee's exact-match rule)."""

    LIST = '/api/v1/payslips/'

    def setUp(self):
        from core.models import Currency
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        self.hr = User.objects.create_superuser('hr_reg', 'hrreg@test.com', 'pw')
        self.staff = User.objects.create_user('plain_reg', 'plainreg@test.com', 'pw')
        self.co = Company.objects.create(code='ZZR', name='Register Test Co')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-08', start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31), pay_date=date(2026, 8, 25))

        self.active = Employee.objects.create(
            full_name='Active Worker', employee_number='REG-ACT', company=self.co,
            status=Employee.Status.ACTIVE)
        self.terminated = Employee.objects.create(
            full_name='Terminated Not Archived', employee_number='REG-TERM', company=self.co,
            status=Employee.Status.TERMINATED, termination_date=date(2026, 8, 1))
        self.archived = Employee.objects.create(
            full_name='Archived Leaver', employee_number='REG-ARCH', company=self.co,
            status=Employee.Status.TERMINATED, termination_date=date(2026, 7, 15))

        for emp in (self.active, self.terminated, self.archived):
            Payslip.objects.create(employee=emp, period=self.period, company=self.co)
        # Archive AFTER the draft exists — reproduces the lingering-draft bug.
        archive_employee(self.archived, actor=self.hr, reason='Resigned — paid out')

        self.client = APIClient()

    def _ids(self, resp):
        rows = resp.data['results'] if isinstance(resp.data, dict) and 'results' in resp.data else resp.data
        return {str(r['employee']) for r in rows}

    def test_register_hides_archived_but_keeps_active_and_terminated(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.get(self.LIST, {'period_name': '2026-08'})
        self.assertEqual(resp.status_code, 200, resp.content)
        emp_ids = self._ids(resp)
        self.assertIn(str(self.active.pk), emp_ids)
        # A terminated-but-not-archived final settlement stays on the register.
        self.assertIn(str(self.terminated.pk), emp_ids)
        # The archived leaver's lingering draft is gone.
        self.assertNotIn(str(self.archived.pk), emp_ids)

    def test_hr_can_reveal_archived_with_query_param(self):
        self.client.force_authenticate(self.hr)
        resp = self.client.get(self.LIST, {'period_name': '2026-08', 'archived': '1'})
        self.assertEqual(resp.status_code, 200, resp.content)
        # ?archived=1 shows ONLY archived (mirrors EmployeeViewSet).
        self.assertEqual(self._ids(resp), {str(self.archived.pk)})

    def test_non_hr_viewer_cannot_reveal_archived(self):
        """A payroll VIEWER who is not HR/Finance-ops (e.g. a junior HR-dept
        staffer — can read the register via department, but hris_role='ess')
        must NOT be able to surface archived leavers with ?archived=1. Mirrors
        the EmployeeViewSet reveal gate on a different, more sensitive dataset."""
        from core.models import UserProfile
        from core.hris_access import hris_role
        from payroll.amendment_views import user_can_view_payroll
        UserProfile.objects.update_or_create(
            user=self.staff, defaults={'department': 'Human Resources'})
        # Precondition: can view the register, but is not in the reveal role set.
        self.assertTrue(user_can_view_payroll(self.staff))
        self.assertNotIn(hris_role(self.staff), {'hr', 'hris', 'admin', 'superadmin'})

        self.client.force_authenticate(self.staff)
        resp = self.client.get(self.LIST, {'period_name': '2026-08', 'archived': '1'})
        self.assertEqual(resp.status_code, 200, resp.content)
        # The param is ignored for a non-HR caller — archived never leaks.
        self.assertNotIn(str(self.archived.pk), self._ids(resp))
