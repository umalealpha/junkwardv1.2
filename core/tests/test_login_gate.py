import core.signals_user_audit  # noqa: F401  # ensure user post_save audit signal is registered
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.login_gate import decide_new_login, record
from core.models import AuditLog, Company, Currency
from payroll.models import Employee


class LoginGateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP',
            defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TST1', name='Test Co One')

        cls.payroll_staff = Employee.objects.create(
            employee_number='E001',
            full_name='Payroll Staff',
            email='p@alphadirect.co.bw',
            status='active',
            company=cls.company,
        )
        cls.payroll_agent = Employee.objects.create(
            employee_number='E002',
            full_name='Payroll Agent',
            email='i@insurance.co.bw',
            status='active',
            company=cls.company,
        )

        User = get_user_model()
        cls.agent_user = User.objects.create_user(
            username='agent',
            email='agent@insurance.co.bw',
            password='p',
        )
        cls.payroll_agent_user = User.objects.create_user(
            username='payagent',
            email='i@insurance.co.bw',
            password='p',
        )
        cls.alpha_user = User.objects.create_user(
            username='alpha',
            email='staff@alphadirect.co.bw',
            password='p',
        )

    def test_decide_new_login_staff_on_payroll_allows(self):
        self.assertEqual(
            decide_new_login('p@alphadirect.co.bw'),
            ('allow', 'on payroll'),
        )

    def test_decide_new_login_agent_not_on_payroll_blocks(self):
        self.assertEqual(
            decide_new_login('agent@insurance.co.bw'),
            ('block', 'UniCoin agent (insurance.co.bw) not on payroll'),
        )

    def test_decide_new_login_staff_not_on_payroll_reports_by_default(self):
        self.assertEqual(
            decide_new_login('staff@alphadirect.co.bw'),
            ('report', 'not on payroll — watch-only until enforcement'),
        )

    @override_settings(SSO_NEW_LOGIN_POLICY='enforce')
    def test_decide_new_login_staff_not_on_payroll_blocks_when_enforce(self):
        self.assertEqual(
            decide_new_login('staff@alphadirect.co.bw'),
            ('block', 'not on payroll'),
        )

    def test_decide_new_login_agent_on_payroll_allows(self):
        self.assertEqual(
            decide_new_login('i@insurance.co.bw'),
            ('allow', 'on payroll'),
        )

    def test_record_emails_once_within_24_hours(self):
        email = 'new@alphadirect.co.bw'
        with patch('core.login_gate.send_html_with_cfo_cc') as mock_send:
            mock_send.return_value = 1

            record(email, 'report', 'not on payroll — watch-only until enforcement')
            record(email, 'report', 'not on payroll — watch-only until enforcement')

        self.assertEqual(mock_send.call_count, 1)
        self.assertTrue(
            AuditLog.objects.filter(
                record_id=email,
                description__startswith='SSO new login report',
            ).exists()
        )

    def test_close_agent_logins_commit_deactivates_only_agent_not_on_payroll(self):
        with patch(
            'core.management.commands.close_agent_logins.send_html_with_cfo_cc'
        ) as mock_send:
            mock_send.return_value = 1
            call_command('close_agent_logins', commit=True)

        self.agent_user.refresh_from_db()
        self.assertFalse(self.agent_user.is_active)

        self.payroll_agent_user.refresh_from_db()
        self.assertTrue(self.payroll_agent_user.is_active)

        self.alpha_user.refresh_from_db()
        self.assertTrue(self.alpha_user.is_active)

        self.assertTrue(
            AuditLog.objects.filter(
                table_name='auth_user',
                record_id=str(self.agent_user.pk),
                action='update',
                description__startswith='Closed: UniCoin agent not on payroll',
            ).exists()
        )
        self.assertEqual(mock_send.call_count, 1)

    def test_user_post_save_audit_row_is_created(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='auditme',
            email='auditme@alphadirect.co.bw',
            password='p',
        )

        self.assertTrue(
            AuditLog.objects.filter(
                table_name='auth_user',
                record_id=str(user.pk),
                action='create',
                description='Omni login created',
            ).exists()
        )


class CloseAgentLoginsKeepsStaffTests(TestCase):
    def test_agent_login_linked_to_active_staff_is_kept(self):
        from io import StringIO
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from core.models import Company, Currency
        from payroll.models import Employee
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        co = Company.objects.create(code='UNIK', name='Unicoin Keep')
        User = get_user_model()
        staff = User.objects.create_user(username='bm', email='bm@insurance.co.bw',
                                         first_name='Bee', last_name='Emm')
        Employee.objects.create(employee_number='K1', full_name='Bee Emm', company=co,
                                status='active', email='bm@alphadirect.co.bw', user=staff)
        agent = User.objects.create_user(username='ag', email='ag@insurance.co.bw',
                                         first_name='Real', last_name='Agent')
        with patch('core.management.commands.close_agent_logins.send_html_with_cfo_cc', return_value=1):
            call_command('close_agent_logins', '--commit', stdout=StringIO())
        staff.refresh_from_db(); agent.refresh_from_db()
        self.assertTrue(staff.is_active)
        self.assertFalse(agent.is_active)

