from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.hris_access import user_can_access_hris
from core.models import AuditLog, Company, Currency, Role, UserRoleAssignment
from hris.models import ContractReminderRule, HRSetting
from payroll.models import Employee, PayrollPeriod

User = get_user_model()


class HRSettingViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TST1', name='Test Co One')
        PayrollPeriod.objects.create(
            period_name='2026-08',
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )

        cls.head = User.objects.create_user(username='head', email='head@alphadirect.co.bw', password='p')
        cls.hr_viewer_user = User.objects.create_user(
            username='hrviewer',
            email='hrviewer@alphadirect.co.bw',
            password='p',
        )

        cls.hr_viewer_role = Role.objects.create(code='HR_VIEWER', name='HR Viewer', level=5)
        cls.hr_manager_role = Role.objects.create(code='HR_MANAGER', name='HR Manager', level=5)

        UserRoleAssignment.objects.create(user=cls.hr_viewer_user, role=cls.hr_viewer_role)
        cls.head_assignment = UserRoleAssignment.objects.create(user=cls.head, role=cls.hr_manager_role)

        HRSetting.objects.update_or_create(key='hr_heads', defaults={'value': [cls.head.email], 'locked': False})

    def setUp(self):
        self.client = APIClient()

    def test_non_head_hr_user_can_get_but_cannot_post(self):
        self.client.force_authenticate(self.hr_viewer_user)

        response = self.client.get('/api/v1/hris/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_edit'])

        post_data = [
            ('/api/v1/hris/settings/update/', {'key': 'contract_reminder_recipients', 'value': ['new@alphadirect.co.bw']}),
            ('/api/v1/hris/settings/lock/', {'key': 'contract_reminder_recipients', 'locked': True}),
            ('/api/v1/hris/settings/rule/', {'category': 'employee', 'months_before': 2, 'is_active': True}),
            ('/api/v1/hris/settings/team/add/', {'email': 'new@alphadirect.co.bw', 'role_code': 'HR_VIEWER'}),
            ('/api/v1/hris/settings/team/remove/', {'assignment_id': 1}),
            ('/api/v1/hris/settings/person-flags/', {'employee_id': 1}),
        ]
        for path, payload in post_data:
            response = self.client.post(path, payload, format='json')
            self.assertEqual(response.status_code, 403, f'{path} should return 403')
            self.assertEqual(response.data['detail'], 'Only HR heads can change HR settings.')

    def test_head_can_update_recipients(self):
        self.client.force_authenticate(self.head)

        with patch('hris.hr_settings.send_html_with_cfo_cc') as mock_email:
            response = self.client.post(
                '/api/v1/hris/settings/update/',
                {
                    'key': 'contract_reminder_recipients',
                    'value': ['Unami@AlphaDirect.co.bw', ' unami@alphadirect.co.bw '],
                },
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['value'], ['unami@alphadirect.co.bw'])

        row = HRSetting.objects.get(key='contract_reminder_recipients')
        self.assertEqual(row.value, ['unami@alphadirect.co.bw'])
        self.assertEqual(
            AuditLog.objects.filter(
                table_name='hris_hrsetting',
                record_id='contract_reminder_recipients',
            ).count(),
            1,
        )
        mock_email.assert_called_once()

    def test_locked_setting_returns_409(self):
        HRSetting.objects.update_or_create(
            key='contract_reminder_recipients',
            defaults={'value': ['a@alphadirect.co.bw'], 'locked': True},
        )
        self.client.force_authenticate(self.head)

        response = self.client.post(
            '/api/v1/hris/settings/update/',
            {'key': 'contract_reminder_recipients', 'value': ['b@alphadirect.co.bw']},
            format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['detail'], 'This setting is locked. Unlock it first.')

    def test_empty_hr_heads_rejected(self):
        self.client.force_authenticate(self.head)

        response = self.client.post(
            '/api/v1/hris/settings/update/',
            {'key': 'hr_heads', 'value': []},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'At least one HR head is needed.')

    def test_team_add_creates_assignment_and_grants_access(self):
        self.client.force_authenticate(self.head)
        new_user = User.objects.create_user(
            username='newuser',
            email='newuser@alphadirect.co.bw',
            password='p',
        )

        with patch('hris.hr_settings.send_html_with_cfo_cc'):
            response = self.client.post(
                '/api/v1/hris/settings/team/add/',
                {'email': 'NEWUSER@alphadirect.co.bw', 'role_code': 'HR_VIEWER'},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            UserRoleAssignment.objects.filter(user=new_user, role=self.hr_viewer_role).exists()
        )
        self.assertTrue(user_can_access_hris(new_user))

    def test_team_remove_deletes_assignment_and_revokes_access(self):
        self.client.force_authenticate(self.head)
        member = User.objects.create_user(
            username='member',
            email='member@alphadirect.co.bw',
            password='p',
        )
        assignment = UserRoleAssignment.objects.create(user=member, role=self.hr_viewer_role)
        self.assertTrue(user_can_access_hris(member))

        with patch('hris.hr_settings.send_html_with_cfo_cc'):
            response = self.client.post(
                '/api/v1/hris/settings/team/remove/',
                {'assignment_id': assignment.id},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserRoleAssignment.objects.filter(id=assignment.id).exists())
        self.assertFalse(user_can_access_hris(member))

    def test_cannot_remove_own_access(self):
        self.client.force_authenticate(self.head)

        response = self.client.post(
            '/api/v1/hris/settings/team/remove/',
            {'assignment_id': self.head_assignment.id},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['detail'], 'You cannot remove your own access.')
        self.assertTrue(UserRoleAssignment.objects.filter(id=self.head_assignment.id).exists())

    def test_rule_update_changes_months_before(self):
        self.client.force_authenticate(self.head)
        ContractReminderRule.objects.update_or_create(category='employee', defaults={'months_before': 1, 'is_active': False})

        with patch('hris.hr_settings.send_html_with_cfo_cc'):
            response = self.client.post(
                '/api/v1/hris/settings/rule/',
                {'category': 'employee', 'months_before': 5, 'is_active': True},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        rule = ContractReminderRule.objects.get(category='employee')
        self.assertEqual(rule.months_before, 5)
        self.assertTrue(rule.is_active)

    def test_invalid_email_rejected(self):
        self.client.force_authenticate(self.head)

        response = self.client.post(
            '/api/v1/hris/settings/update/',
            {'key': 'contract_reminder_recipients', 'value': ['not-an-email']},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_head_can_tick_controller_and_expatriate(self):
        from payroll.models import Employee
        from hris.models import HRISProfile
        emp = Employee.objects.create(employee_number='FLAG1', full_name='Flag Person',
                                      company=self.company, status='active') \
            if hasattr(self, 'company') else None
        if emp is None:
            from core.models import Company
            emp = Employee.objects.create(employee_number='FLAG1', full_name='Flag Person',
                                          company=Company.objects.create(code='FLG', name='Flag Co'),
                                          status='active')
        self.client.force_authenticate(self.head)
        response = self.client.post('/api/v1/hris/settings/person-flags/',
                                    {'employee_id': str(emp.id), 'is_controller': True,
                                     'is_expatriate': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        profile = HRISProfile.objects.get(employee=emp)
        self.assertTrue(profile.is_controller)
        self.assertTrue(profile.is_expatriate)

