"""
payroll/tests/test_payslip_send.py

CFO directive 2026-07-28: a button for HR (Unami, Dorothy) and Finance to send
an individual employee their payslip. These tests pin the safety rules of the
send path (payroll/payslip_email.send_one_payslip), which the send-email /
send-batch API actions call:

  * an unsigned 2026-07+ payroll is NOT pushed to staff (reuses the same
    release gate as the employee's own self-service copy)
  * once the CFO signs the month off, the payslip sends
  * a payslip from before the sign-off cutover sends freely (history is never
    retroactively withheld)
  * an employee with no email address is reported, not crashed on
  * a successful send is recorded in the immutable audit trail as a DOWNLOAD
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from core.models import AuditLog, Company, UserProfile
from payroll.models import Employee, PayrollPeriod, Payslip
from payroll.payslip_email import send_one_payslip
from payroll.signoff_service import sign_payroll


def _profile(user, title):
    prof, _ = UserProfile.objects.get_or_create(user=user)
    prof.title = title
    prof.is_active = True
    prof.save()
    return prof


class PayslipSendTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Alpha Direct Insurance', code='ADIC')
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31))
        cls.old_period = PayrollPeriod.objects.create(
            period_name='2026-05', start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 31))

        cls.fm = User.objects.create_user('ktshutlhedi', 'ktshutlhedi@alphadirect.co.bw', 'x')
        _profile(cls.fm, UserProfile.Title.FINANCE_MANAGER)
        cls.hr = User.objects.create_user('ubutale', 'ubutale@alphadirect.co.bw', 'x')
        _profile(cls.hr, UserProfile.Title.HR_MANAGER)
        cls.cfo = User.objects.create_user('pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x')
        _profile(cls.cfo, UserProfile.Title.CFO)

        cls.employee = Employee.objects.create(
            full_name='Tebogo Kgosi', company=cls.company,
            employee_number='E001', email='tkgosi@alphadirect.co.bw')

    def _payslip(self, period=None, *, employee=None):
        return Payslip.objects.create(
            employee=employee or self.employee,
            period=period or self.period, company=self.company,
            gross_amount=Decimal('10000.00'), paye_amount=Decimal('2000.00'),
            net_amount=Decimal('8000.00'))

    def test_blocked_when_not_signed_off(self):
        ps = self._payslip()
        res = send_one_payslip(ps, user=self.fm)
        self.assertFalse(res['ok'])
        self.assertEqual(res['reason'], 'blocked')
        self.assertEqual(len(mail.outbox), 0)

    def test_no_email_reported_not_crashed(self):
        empty = Employee.objects.create(
            full_name='No Email', company=self.company,
            employee_number='E002', email='')
        ps = self._payslip(employee=empty)
        res = send_one_payslip(ps, user=self.fm)
        self.assertFalse(res['ok'])
        self.assertEqual(res['reason'], 'no_email')
        self.assertEqual(len(mail.outbox), 0)

    def test_old_period_sends_without_signoff(self):
        ps = self._payslip(period=self.old_period)
        res = send_one_payslip(ps, user=self.fm)
        self.assertTrue(res['ok'], res)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.employee.email, mail.outbox[0].to)

    def test_sends_after_signoff_and_is_audited(self):
        ps = self._payslip()
        # Dual sign-off: HR (Unami) + Finance (Kago), two different people.
        sign_payroll(self.period, self.company, self.hr)
        sign_payroll(self.period, self.company, self.fm)

        res = send_one_payslip(ps, user=self.cfo)
        self.assertTrue(res['ok'], res)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                table_name='payroll.Payslip', record_id=str(ps.pk),
                action=AuditLog.Action.DOWNLOAD).exists())
