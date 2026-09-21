from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.models import Grade, HRISProfile, OffboardingCase
from payroll.models import Employee

from hris.offboarding_service import (
    REQUIRED_STEPS,
    archive_block_reason,
    can_do_step,
    open_case,
    record_step,
)


class OffboardingTests(TestCase):
    def setUp(self):
        User = get_user_model()

        Currency.objects.get_or_create(
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
        self.senior_user = User.objects.create_user(
            username='senior', email='senior@alphadirect.co.bw', password='p'
        )
        self.finance_user = User.objects.create_user(
            username='fin', email='fin@alphadirect.co.bw', password='p'
        )
        self.cfo_user = User.objects.create_user(
            username='cfo', email='pganesharajah@alphadirect.co.bw', password='p'
        )
        self.admin_user = User.objects.create_superuser(
            username='admin', email='admin@alphadirect.co.bw', password='p'
        )

        self.send_email_patcher = patch(
            'hris.offboarding_service.send_html_with_cfo_cc', return_value=1
        )
        self.mock_send_email = self.send_email_patcher.start()
        self.addCleanup(self.send_email_patcher.stop)

        self.revoke_patcher = patch('hris.offboarding_service.revoke_device_sessions')
        self.mock_revoke = self.revoke_patcher.start()
        self.addCleanup(self.revoke_patcher.stop)

        self.clear_patcher = patch('hris.offboarding_service.clear_browser_tokens')
        self.mock_clear = self.clear_patcher.start()
        self.addCleanup(self.clear_patcher.stop)

        self.assets_patcher = patch(
            'hris.offboarding_service.assets_blocking_offboarding', return_value=[]
        )
        self.mock_assets = self.assets_patcher.start()
        self.addCleanup(self.assets_patcher.stop)

    def _create_employee(self, full_name, emp_no, user=None, grade_name='Ordinary', manager=None):
        employee = Employee.objects.create(
            full_name=full_name,
            employee_number=emp_no,
            email=f'{emp_no.lower()}@alphadirect.co.bw',
            company=self.company,
            status='active',
            user=user,
        )
        grade = Grade.objects.create(code=f'G-{emp_no}', name=grade_name, level=1, midpoint=10000)
        HRISProfile.objects.create(
            employee=employee,
            grade=grade,
            manager=manager,
            is_controller=False,
            is_expatriate=False,
            nationality='Botswana',
        )
        return employee

    def test_open_case_creates_six_steps_and_sets_termination_date(self):
        employee = self._create_employee(
            'Emp One', 'E001', user=self.employee_user, grade_name='Ordinary'
        )

        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        employee.refresh_from_db()
        self.assertEqual(employee.termination_date, date(2026, 10, 1))
        self.assertEqual(case.steps.count(), 6)
        self.assertEqual(set(case.steps.values_list('kind', flat=True)), set(REQUIRED_STEPS))
        self.assertEqual(case.status, OffboardingCase.Status.OPEN)
        self.assertIsNone(case.access_removed_at)

    def test_senior_grade_closes_login_immediately_ordinary_does_not(self):
        ordinary = self._create_employee(
            'Ordinary Emp', 'E002', user=self.employee_user, grade_name='Ordinary'
        )
        case_ordinary = open_case(
            ordinary,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )
        self.employee_user.refresh_from_db()
        self.assertTrue(self.employee_user.is_active)
        self.assertIsNone(case_ordinary.access_removed_at)

        senior = self._create_employee(
            'Senior Emp', 'E003', user=self.senior_user, grade_name='Senior Associate'
        )
        case_senior = open_case(
            senior,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )
        self.senior_user.refresh_from_db()
        self.assertFalse(self.senior_user.is_active)
        self.assertIsNotNone(case_senior.access_removed_at)

    def test_superuser_and_cfo_never_closed(self):
        admin_emp = self._create_employee(
            'Admin Emp', 'E004', user=self.admin_user, grade_name='Senior Associate'
        )
        case_admin = open_case(
            admin_emp,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.is_active)
        self.assertIsNone(case_admin.access_removed_at)

        cfo_emp = self._create_employee(
            'CFO Emp', 'E005', user=self.cfo_user, grade_name='Senior Associate'
        )
        case_cfo = open_case(
            cfo_emp,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )
        self.cfo_user.refresh_from_db()
        self.assertTrue(self.cfo_user.is_active)
        self.assertIsNone(case_cfo.access_removed_at)

    def test_it_ticket_email_sent(self):
        employee = self._create_employee(
            'Ticket Emp', 'E006', user=self.employee_user, grade_name='Ordinary'
        )

        with self.captureOnCommitCallbacks(execute=True):
            case = open_case(
                employee,
                reason='resignation',
                last_working_day=date(2026, 10, 1),
                user=self.hr_user,
            )

        self.mock_send_email.assert_called_once()
        case.refresh_from_db()
        self.assertIsNotNone(case.it_ticket_sent_at)

    def test_upload_step_without_file_returns_400(self):
        employee = self._create_employee(
            'Upload Emp', 'E007', user=self.employee_user, grade_name='Ordinary'
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        client = APIClient()
        client.force_authenticate(self.hr_user)
        url = f'/api/v1/hris/offboarding/{case.id}/step/'

        with patch('hris.offboarding_views.user_can_access_hris', return_value=True), \
                patch('hris.offboarding_service.user_can_access_hris', return_value=True):
            response = client.post(url, {'kind': 'resignation_letter'}, format='multipart')

        self.assertEqual(response.status_code, 400)
        self.assertIn('Please attach the document.', response.data['detail'])

    def test_non_hr_cannot_sign_hc(self):
        employee = self._create_employee(
            'Non HR Emp', 'E008', user=self.employee_user, grade_name='Ordinary'
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        with patch('hris.offboarding_service.user_can_access_hris', return_value=False):
            self.assertFalse(can_do_step(self.employee_user, case, 'signoff_hc'))
        with patch('hris.offboarding_service.user_can_access_hris', return_value=True):
            self.assertTrue(can_do_step(self.hr_user, case, 'signoff_hc'))

    def test_manager_can_sign_manager_step_for_own_report(self):
        manager = self._create_employee(
            'Manager Emp', 'E009', user=self.manager_user, grade_name='Manager'
        )
        employee = self._create_employee(
            'Report Emp',
            'E010',
            user=self.employee_user,
            grade_name='Ordinary',
            manager=manager,
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        with patch('hris.offboarding_service.user_can_access_hris', return_value=False):
            self.assertTrue(can_do_step(self.manager_user, case, 'signoff_manager'))
            self.assertFalse(can_do_step(self.employee_user, case, 'signoff_manager'))

    def test_finance_role_can_sign_finance_step(self):
        employee = self._create_employee(
            'Finance Emp', 'E011', user=self.employee_user, grade_name='Ordinary'
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        with patch('hris.offboarding_service.user_can_access_hris', return_value=False):
            with patch('hris.offboarding_service._user_has_finance_role', return_value=True):
                self.assertTrue(can_do_step(self.finance_user, case, 'signoff_finance'))
            with patch('hris.offboarding_service._user_has_finance_role', return_value=False):
                self.assertFalse(can_do_step(self.finance_user, case, 'signoff_finance'))

    def test_after_all_six_steps_complete(self):
        employee = self._create_employee(
            'Complete Emp', 'E012', user=self.employee_user, grade_name='Ordinary'
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        upload_content = b'%PDF-1.4 test content'
        for kind in ('resignation_letter', 'it_document', 'hc_document'):
            upload = SimpleUploadedFile(
                f'{kind}.pdf', upload_content, content_type='application/pdf'
            )
            record_step(case, kind, self.admin_user, upload=upload, note=f'{kind} uploaded')

        for kind in ('signoff_hc', 'signoff_manager', 'signoff_finance'):
            record_step(case, kind, self.admin_user, note=f'{kind} signed')

        case.refresh_from_db()
        self.assertEqual(case.status, OffboardingCase.Status.COMPLETE)
        self.assertIsNotNone(case.completed_at)

    def test_archive_block_reason_while_open_and_complete(self):
        employee = self._create_employee(
            'Archive Emp', 'E013', user=self.employee_user, grade_name='Ordinary'
        )
        case = open_case(
            employee,
            reason='resignation',
            last_working_day=date(2026, 10, 1),
            user=self.hr_user,
        )

        reason = archive_block_reason(employee)
        self.assertIn('Offboarding is not finished for Archive Emp', reason)
        self.assertIn('6 step(s) outstanding', reason)

        upload_content = b'%PDF-1.4 test content'
        for kind in ('resignation_letter', 'it_document', 'hc_document'):
            upload = SimpleUploadedFile(
                f'{kind}.pdf', upload_content, content_type='application/pdf'
            )
            record_step(case, kind, self.admin_user, upload=upload, note=f'{kind} uploaded')

        for kind in ('signoff_hc', 'signoff_manager', 'signoff_finance'):
            record_step(case, kind, self.admin_user, note=f'{kind} signed')

        case.refresh_from_db()
        self.assertIsNone(archive_block_reason(employee))

    def test_archive_block_reason_legacy_terminated_no_case_none(self):
        employee = self._create_employee(
            'Legacy Emp', 'E014', user=self.employee_user, grade_name='Ordinary'
        )
        employee.termination_date = date(2026, 8, 1)
        employee.save(update_fields=['termination_date'])

        self.assertIsNone(archive_block_reason(employee))

    def test_archive_block_reason_new_leaver_no_case_text(self):
        employee = self._create_employee(
            'New Emp', 'E015', user=self.employee_user, grade_name='Ordinary'
        )
        employee.termination_date = date(2026, 10, 1)
        employee.save(update_fields=['termination_date'])

        reason = archive_block_reason(employee)
        self.assertIn('Open an offboarding case for New Emp first', reason)

    def test_finance_manager_by_title_can_sign_finance_step(self):
        from core.models import UserProfile
        from hris.offboarding_service import _user_has_finance_role
        profile, _ = UserProfile.objects.get_or_create(user=self.finance_user)
        profile.title = UserProfile.Title.FINANCE_MANAGER
        profile.is_active = True
        profile.save()
        self.assertTrue(_user_has_finance_role(self.finance_user))
        profile.title = UserProfile.Title.FINANCIAL_CONTROLLER
        profile.save()
        self.assertTrue(_user_has_finance_role(self.finance_user))

