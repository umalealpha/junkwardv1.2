from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from core.models import AuditLog, Company, Currency
from hris.contract_module import (
    DEFAULT_MONTHS,
    category_for,
    months_before,
    run_reminders,
)
from hris.models import (
    ContractReminderLog,
    ContractReminderRule,
    ContractRenewalDecision,
    Grade,
    HRISProfile,
)
from payroll.models import Employee, EmploymentContract


class ContractModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TST1', name='Test Co One')
        cls.hr_user = get_user_model().objects.create_superuser(
            username='superhr',
            email='superhr@alphadirect.co.bw',
        )
        cls.non_hr_user = get_user_model().objects.create_user(
            username='nonhr',
            email='nonhr@alphadirect.co.bw',
        )
        cls.grade_ex = Grade.objects.create(code='EX1', name='Executive', level=1, midpoint=10000)
        cls.grade_manager = Grade.objects.create(code='MGR1', name='Claims Manager', level=2, midpoint=10000)
        cls.grade_staff = Grade.objects.create(code='S1', name='Staff', level=3, midpoint=10000)
        cls.manager = Employee.objects.create(
            employee_number='M001',
            full_name='Manager Person',
            email='manager@alphadirect.co.bw',
            company=cls.company,
            status='active',
        )

    def make_employee(self, number, name, grade=None, *, expatriate=False, controller=False, manager=None):
        employee = Employee.objects.create(
            employee_number=number,
            full_name=name,
            email=f'{number.lower()}@alphadirect.co.bw',
            company=self.company,
            department='Insurance',
            job_title='Officer',
            status='active',
        )
        if grade is not None:
            HRISProfile.objects.create(
                employee=employee,
                grade=grade,
                is_expatriate=expatriate,
                is_controller=controller,
                manager=manager or self.manager,
            )
        return employee

    def make_contract(self, employee, start_date, end_date, **kwargs):
        defaults = {
            'employee': employee,
            'status': 'active',
            'start_date': start_date,
            'end_date': end_date,
            'contract_type': 'permanent',
        }
        defaults.update(kwargs)
        return EmploymentContract.objects.create(**defaults)

    def test_category_for_order(self):
        expat = self.make_employee('E001', 'Expat Person', grade=self.grade_ex, expatriate=True, controller=True)
        self.assertEqual(category_for(expat), 'expatriate')

        controller = self.make_employee('E002', 'Controller Person', grade=self.grade_ex, controller=True)
        self.assertEqual(category_for(controller), 'controller')

        c_suite = self.make_employee('E003', 'C Suite Person', grade=self.grade_ex)
        self.assertEqual(category_for(c_suite), 'c_suite')

        senior_manager = self.make_employee('E004', 'Senior Manager Person', grade=self.grade_manager)
        self.assertEqual(category_for(senior_manager), 'senior_manager')

        staff = self.make_employee('E005', 'Staff Person', grade=self.grade_staff)
        self.assertEqual(category_for(staff), 'employee')

    def test_months_before_uses_db_rule_over_default(self):
        ContractReminderRule.objects.update_or_create(category='employee', defaults={'months_before': 9, 'is_active': True})
        self.assertEqual(months_before('employee'), 9)
        self.assertEqual(months_before('expatriate'), DEFAULT_MONTHS['expatriate'])

    @patch('hris.hr_settings.get_setting', return_value=['hr@example.com'])
    @patch('hris.contract_module.send_html_with_cfo_cc', return_value=1)
    def test_run_reminders_sends_for_contract_inside_window(self, mock_send, mock_get_setting):
        employee = self.make_employee('E101', 'Inside Window', grade=self.grade_staff)
        today = date(2026, 11, 1)
        end_date = date(2026, 12, 1)
        self.make_contract(employee, date(2026, 1, 1), end_date)

        counts = run_reminders(today, commit=False)

        self.assertEqual(counts['sent'], 1)
        mock_send.assert_not_called()

    @patch('hris.hr_settings.get_setting', return_value=['hr@example.com'])
    @patch('hris.contract_module.send_html_with_cfo_cc', return_value=1)
    def test_run_reminders_skips_contract_outside_window(self, mock_send, mock_get_setting):
        employee = self.make_employee('E102', 'Outside Window', grade=self.grade_staff)
        today = date(2026, 9, 29)
        end_date = date(2026, 12, 1)
        self.make_contract(employee, date(2026, 1, 1), end_date)

        counts = run_reminders(today, commit=False)

        self.assertEqual(counts['sent'], 0)
        self.assertEqual(counts['skipped'], 1)
        mock_send.assert_not_called()

    @patch('hris.hr_settings.get_setting', return_value=['hr@example.com'])
    @patch('hris.contract_module.send_html_with_cfo_cc', return_value=1)
    def test_run_reminders_skips_when_decision_exists(self, mock_send, mock_get_setting):
        employee = self.make_employee('E103', 'Decided Person', grade=self.grade_staff)
        today = date(2026, 11, 1)
        contract = self.make_contract(employee, date(2026, 1, 1), date(2026, 12, 1))
        ContractRenewalDecision.objects.create(
            contract=contract,
            decision='renew_same',
            note='Already decided',
            decided_by=self.hr_user,
        )

        counts = run_reminders(today, commit=False)

        self.assertEqual(counts['sent'], 0)
        self.assertEqual(counts['skipped'], 1)
        mock_send.assert_not_called()

    @patch('hris.hr_settings.get_setting', return_value=['hr@example.com'])
    @patch('hris.contract_module.send_html_with_cfo_cc', return_value=1)
    def test_run_reminders_does_not_send_twice_within_seven_days(self, mock_send, mock_get_setting):
        employee = self.make_employee('E104', 'Repeat Person', grade=self.grade_staff)
        today = date(2026, 11, 1)
        end_date = today + timedelta(days=40)
        contract = self.make_contract(employee, date(2026, 1, 1), end_date)
        ContractReminderLog.objects.create(
            contract=contract,
            stage='reminder',
            sent_on=today - timedelta(days=3),
            recipients=['hr@example.com'],
        )

        counts = run_reminders(today, commit=False)

        self.assertEqual(counts['sent'], 0)
        self.assertEqual(counts['skipped'], 1)
        mock_send.assert_not_called()

    @patch('hris.hr_settings.get_setting', return_value=['hr@example.com'])
    @patch('hris.contract_module.send_html_with_cfo_cc', return_value=1)
    def test_run_reminders_escalates_with_cfo_in_to_within_thirty_days(self, mock_send, mock_get_setting):
        employee = self.make_employee('E105', 'Escalate Person', grade=self.grade_staff)
        today = date(2026, 11, 1)
        end_date = today + timedelta(days=20)
        self.make_contract(employee, date(2026, 1, 1), end_date)

        counts = run_reminders(today, commit=True)

        self.assertEqual(counts['sent'], 1)
        self.assertTrue(mock_send.called)
        args, kwargs = mock_send.call_args
        self.assertIn('pganesharajah@alphadirect.co.bw', kwargs['to'])
        self.assertTrue(kwargs['allow_named_exec'])
        self.assertTrue(ContractReminderLog.objects.filter(contract__employee=employee, stage='escalation').exists())

    def test_register_due_filter_counts(self):
        client = APIClient()
        client.force_authenticate(self.hr_user)

        today = timezone.localdate()
        due_end_1 = today + timedelta(days=40)
        due_end_2 = today + timedelta(days=41)
        not_due_end = today + timedelta(days=100)

        e1 = self.make_employee('E201', 'Due One', grade=self.grade_staff)
        e2 = self.make_employee('E202', 'Due Two', grade=self.grade_staff)
        e3 = self.make_employee('E203', 'Not Due', grade=self.grade_staff)

        self.make_contract(e1, date(2026, 1, 1), due_end_1)
        self.make_contract(e2, date(2026, 1, 1), due_end_2)
        self.make_contract(e3, date(2026, 1, 1), not_due_end)

        response = client.get('/api/v1/hris/contracts/?filter=due')
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['counts']['due'], 2)
        self.assertEqual(len(data['rows']), 2)

    def test_decision_endpoint_403_for_non_hr_and_auditlog_for_hr(self):
        employee = self.make_employee('E301', 'Decision Person', grade=self.grade_staff)
        contract = self.make_contract(employee, date(2026, 1, 1), date(2026, 12, 1))

        non_hr_client = APIClient()
        non_hr_client.force_authenticate(self.non_hr_user)
        response = non_hr_client.post(
            f'/api/v1/hris/contracts/{contract.id}/decision/',
            {'decision': 'renew_same'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

        hr_client = APIClient()
        hr_client.force_authenticate(self.hr_user)
        response = hr_client.post(
            f'/api/v1/hris/contracts/{contract.id}/decision/',
            {'decision': 'renew_same', 'note': 'Looks good'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ContractRenewalDecision.objects.filter(contract=contract).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                table_name='payroll_employmentcontract',
                record_id=str(contract.id),
            ).exists()
        )

    def upload_file(self, row_values):
        wb = Workbook()
        ws = wb.active
        ws.append([
            'Employee number',
            'Full name',
            'Company',
            'Department',
            'Job title',
            'Grade code (current)',
            'Contract type (permanent/fixed_term/probation/internship)',
            'Start date',
            'End date',
            'Probation end date',
            'Pension or provident (pension/provident/none)',
            'Controller (Y/N)',
            'Expatriate (Y/N)',
        ])
        ws.append(row_values)
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer

    def api_post_upload(self, client, buffer, commit=False):
        from django.core.files.uploadedfile import SimpleUploadedFile

        file = SimpleUploadedFile(
            'contract-sheet.xlsx',
            buffer.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        url = '/api/v1/hris/contracts/upload/'
        if commit:
            url += '?commit=1'
        return client.post(url, {'file': file}, format='multipart')

    def test_upload_preview_writes_nothing_and_commit_writes_contract(self):
        employee = self.make_employee('E401', 'Upload Person', grade=self.grade_staff)

        row_values = [
            employee.employee_number,
            employee.full_name,
            self.company.name,
            employee.department,
            employee.job_title,
            self.grade_staff.code,
            'permanent',
            date(2026, 1, 1),
            date(2027, 1, 1),
            '',
            'pension',
            'N',
            'N',
        ]

        client = APIClient()
        client.force_authenticate(self.hr_user)

        buffer = self.upload_file(row_values)
        response = self.api_post_upload(client, buffer, commit=False)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(EmploymentContract.objects.filter(employee=employee, status='active').exists())

        buffer = self.upload_file(row_values)
        response = self.api_post_upload(client, buffer, commit=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('written'), 1)
        from core.models import AuditLog
        row = AuditLog.objects.filter(table_name='payroll_employmentcontract').order_by('-created_at').first()
        self.assertIsNotNone(row)
        self.assertTrue(row.new_values, 'contract upload audit row must record what changed')
        self.assertTrue(EmploymentContract.objects.filter(employee=employee, status='active').exists())

    def test_upload_rejects_fixed_term_over_twelve_months_with_elra_message(self):
        employee = self.make_employee('E402', 'Fixed Term Person', grade=self.grade_staff)

        row_values = [
            employee.employee_number,
            employee.full_name,
            self.company.name,
            employee.department,
            employee.job_title,
            self.grade_staff.code,
            'fixed_term',
            date(2026, 1, 1),
            date(2027, 2, 1),
            '',
            'provident',
            'N',
            'N',
        ]

        client = APIClient()
        client.force_authenticate(self.hr_user)

        buffer = self.upload_file(row_values)
        response = self.api_post_upload(client, buffer, commit=False)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['rows'][0]['status'], 'error')
        self.assertTrue(any('12 months' in message for message in data['rows'][0]['messages']))

    def test_register_rows_carry_name_company_and_readable_type(self):
        from hris.contract_module import contract_row
        from django.utils import timezone
        employee = self.make_employee('E901', 'Named Person', grade=self.grade_staff)
        contract = EmploymentContract.objects.create(employee=employee, start_date=date(2026, 1, 1),
                                                     end_date=date(2026, 12, 31), contract_type='fixed_term')
        row = contract_row(contract, timezone.localdate())
        self.assertEqual(row['employee'], 'Named Person')
        self.assertEqual(row['company'], employee.company.name)
        self.assertEqual(row['contract_type_label'], 'Fixed-term / duration')

