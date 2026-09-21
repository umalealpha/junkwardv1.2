"""Smart daily HRIS data-tasks command — CFO directives 2026-06-25.

Verifies: payroll-only scoping, leaver detection (two-payroll comparison +
terminated), resignation/exit-interview reminders that stop once filed, spread,
follow-up, and persistence.
"""
import datetime as dt
import re
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Company, UserProfile
from payroll.models import Employee, PayrollPeriod, Payslip
from hris.models import HRDocument, HRISDailyTaskRun, HRISProfile
from hris.management.commands.hris_daily_data_tasks import EDITORS, MAX_PER_EMP


def _task_employees(body):
    return re.findall(r"^\s*\d+\.\s+Load (.+?)'s ", body, re.M)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DailyTasksPayrollLeaverTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.unami = User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw', password='x')
        cls.dorothy = User.objects.create_user('dikgopoleng', email='dikgopoleng@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.unami, is_administrator=True)
        UserProfile.objects.create(user=cls.dorothy, is_administrator=True)
        cls.prev = PayrollPeriod.objects.create(
            period_name='2099-01', start_date=dt.date(2099, 1, 1),
            end_date=dt.date(2099, 1, 31), pay_date=dt.date(2099, 1, 25))
        cls.latest = PayrollPeriod.objects.create(
            period_name='2099-02', start_date=dt.date(2099, 2, 1),
            end_date=dt.date(2099, 2, 28), pay_date=dt.date(2099, 2, 25))

    def _emp(self, n, status=Employee.Status.ACTIVE):
        e = Employee.objects.create(employee_number=n, full_name=n, company=self.co, status=status)
        HRISProfile.objects.create(employee=e)
        return e

    def _pay(self, e, period):
        Payslip.objects.create(employee=e, period=period, company=self.co)

    def test_only_payroll_members_get_field_tasks(self):
        member = self._emp('Pay Member')      # on the latest payroll
        self._pay(member, self.prev); self._pay(member, self.latest)
        offpay = self._emp('Off Payroll')     # has a profile but NO payslip
        call_command('hris_daily_data_tasks')
        body = mail.outbox[0].body
        self.assertIn("Pay Member", body)
        self.assertNotIn("Off Payroll", body)   # not on payroll -> never nagged

    def test_leaver_detected_and_reminded(self):
        stay = self._emp('Stay Person'); self._pay(stay, self.prev); self._pay(stay, self.latest)
        leaver = self._emp('Gone Person'); self._pay(leaver, self.prev)   # prev only -> left
        call_command('hris_daily_data_tasks')
        body = mail.outbox[0].body
        self.assertIn('LEAVERS', body)
        self.assertIn("Gone Person: load resignation", body)
        self.assertIn('exit interview', body)
        self.assertNotIn("Stay Person: load resignation", body)   # still on payroll, not a leaver

    def test_leaver_reminder_stops_when_both_docs_filed(self):
        anchor = self._emp('Stay Anchor'); self._pay(anchor, self.prev); self._pay(anchor, self.latest)
        leaver = self._emp('Filed Person'); self._pay(leaver, self.prev)   # prev only -> leaver
        HRDocument.objects.create(title='Resignation', category=HRDocument.Category.RESIGNATION,
                                  employee_name='Filed Person', file='hr_documents/r.pdf')
        HRDocument.objects.create(title='Exit', category=HRDocument.Category.EXIT,
                                  employee_name='Filed Person', file='hr_documents/e.pdf')
        call_command('hris_daily_data_tasks')
        body = mail.outbox[0].body
        self.assertNotIn('Filed Person', body)   # both docs on file -> no reminder

    def test_terminated_recent_payslip_is_a_leaver(self):
        t = self._emp('Term Person', status=Employee.Status.TERMINATED)
        self._pay(t, self.latest)                 # terminated but recently paid -> leaver
        call_command('hris_daily_data_tasks')
        body = mail.outbox[0].body
        self.assertIn("Term Person: load resignation", body)
        # terminated person must NOT also get field-data nagging
        self.assertNotIn("Load Term Person's National ID", body)

    def test_field_tasks_spread_across_people(self):
        for i in range(8):
            e = self._emp(f'Emp{i:02d}')
            self._pay(e, self.prev); self._pay(e, self.latest)
        call_command('hris_daily_data_tasks')
        emps = _task_employees(mail.outbox[0].body)
        self.assertGreaterEqual(len(set(emps)), 5, emps)
        self.assertLessEqual(max((emps.count(e) for e in set(emps)), default=0), MAX_PER_EMP)

    def test_persists_run_and_recipients(self):
        """HR only. The CFO was deliberately dropped from this daily email on
        2026-08-12 (his email-reduction plan) — asserting he is NOT on it, so a
        future change cannot quietly put him back."""
        m = self._emp('M One'); self._pay(m, self.latest)
        call_command('hris_daily_data_tasks')
        self.assertEqual(set(mail.outbox[0].to), {e for _, e in EDITORS})
        self.assertEqual(set(mail.outbox[0].to),
                         {'ubutale@alphadirect.co.bw', 'dikgopoleng@alphadirect.co.bw'})
        self.assertNotIn('pganesharajah@alphadirect.co.bw', mail.outbox[0].to)
        # localdate, not now().date(): hris_daily_data_tasks stamps the run with
        # timezone.localdate() (Africa/Gaborone), so a UTC-based lookup misses it
        # between 22:00 UTC and midnight and turned CI red every night.
        self.assertTrue(HRISDailyTaskRun.objects.filter(run_date=timezone.localdate()).exists())

    def test_dry_run_no_email_no_persist(self):
        m = self._emp('M One'); self._pay(m, self.latest)
        out = StringIO()
        call_command('hris_daily_data_tasks', '--dry-run', stdout=out)
        self.assertEqual(len(mail.outbox), 0)
        # Same clock as the sibling above. This one is an assertFalse, so in the
        # 22:00-midnight UTC window it passed for the WRONG reason — it looked up
        # a date the command never writes, so it could not have caught a dry run
        # that persisted.
        self.assertFalse(HRISDailyTaskRun.objects.filter(run_date=timezone.localdate()).exists())
        self.assertIn('[dry-run]', out.getvalue())
