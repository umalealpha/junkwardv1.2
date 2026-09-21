"""Which payroll period a regrade lands on — the /fabe gate's findings, 2026-09-12.

The two bugs here both hid behind the same weak fixture: the original suite
opened exactly ONE payroll period, and with one period every ordering and every
filter gives the same answer. A case that cannot tell two behaviours apart
proves nothing about either. These tests open THREE.

  1. An effective-TODAY regrade raised contract.basic straight away but put the
     amendment on `status=OPEN` ordered by '-start_date' — the LATEST open
     period. ensure_payroll_periods opens through today + 2 months, so the rise
     was recorded on the contract now and reached a payslip two months later.

  2. A future-dated regrade beyond the open horizon raised no amendment at all
     and was reported as success. Nothing downstream reads the new contract
     window — the roll-forward copies the baseline period's lines verbatim —
     so the rise simply never arrived and nobody was told.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Company
from payroll.contract_models import EmploymentContract
from payroll.models import Employee, PayrollAmendment, PayrollPeriod
from recruitment.models import AuthorityToRecruit


class RegradeLandsOnThePeriodOfTheEffectiveDate(TestCase):

    def setUp(self):
        self.company = Company.objects.create(code='RGP', name='Alpha Direct Insurance Company')
        self.emp = Employee.objects.create(
            employee_number='RGP-001', full_name='Regrade Period Person',
            company=self.company, hire_date=datetime.date(2023, 1, 1))
        EmploymentContract.objects.create(
            employee=self.emp, start_date=datetime.date(2023, 1, 1),
            basic=Decimal('9000.00'), status=EmploymentContract.Status.ACTIVE)

        # THREE open periods: this month and the two after it, exactly as
        # ensure_payroll_periods leaves them. With one period the bug is
        # invisible.
        today = timezone.localdate()
        self.this_month = self._period(today.replace(day=1), 0)
        self.next_month = self._period(self._add_month(today.replace(day=1), 1), 1)
        self.month_after = self._period(self._add_month(today.replace(day=1), 2), 2)

        self.cfo = User.objects.create_superuser('rgpcfo', 'rgpcfo@alphadirect.co.bw', 'x')
        self.client.force_login(self.cfo)

    @staticmethod
    def _add_month(d, n):
        m = d.month - 1 + n
        return datetime.date(d.year + m // 12, m % 12 + 1, 1)

    def _period(self, start, n):
        end = self._add_month(start, 1) - datetime.timedelta(days=1)
        return PayrollPeriod.objects.create(
            period_name=f'{start:%Y-%m}', start_date=start, end_date=end,
            status=PayrollPeriod.Status.OPEN)

    def _atr(self, effective_date):
        a = AuthorityToRecruit.objects.create(
            kind=AuthorityToRecruit.Kind.REGRADE,
            person_name='Regrade Period Person',
            position='Senior Officer',
            entity='Alpha Direct Insurance Company',
            employment_type='permanent',
            effective_date=effective_date,
            proposed_basic_salary=Decimal('11000.00'),
            salary_lines=[{'sn': 1, 'item': 'Basic Salary', 'monthly': '11000.00',
                           'annual': '132000.00'}])
        a.approvals = {slug: {'decision': 'approved', 'by': label,
                              'at': '2026-09-12T00:00:00Z'}
                       for slug, label, _e in AuthorityToRecruit.SIGNATORIES}
        a.status = AuthorityToRecruit.Status.APPROVED
        a.save()
        return a

    def _convert(self, a):
        return self.client.post(reverse('v1-recruitment-authority-convert', args=[a.id]))

    def test_an_effective_today_regrade_lands_on_THIS_month(self):
        """Not the period two months out. The pay rise is recorded on the
        contract today, so it has to reach the payslip for today's period."""
        r = self._convert(self._atr(timezone.localdate()))
        self.assertEqual(r.status_code, 200, r.content)
        amd = PayrollAmendment.objects.get(
            employee=self.emp, kind=PayrollAmendment.Kind.SALARY_CHANGE)
        self.assertEqual(
            amd.batch.target_period_id, self.this_month.id,
            'the rise was recorded on the contract now but scheduled on '
            f'{amd.batch.target_period.period_name} - it must land on this month')

    def test_a_regrade_effective_next_month_lands_on_NEXT_month(self):
        r = self._convert(self._atr(self.next_month.start_date))
        self.assertEqual(r.status_code, 200, r.content)
        amd = PayrollAmendment.objects.get(
            employee=self.emp, kind=PayrollAmendment.Kind.SALARY_CHANGE)
        self.assertEqual(amd.batch.target_period_id, self.next_month.id)
        # ...and the person is NOT paid it yet.
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.current_contract.basic, Decimal('9000.00'))

    def test_a_date_beyond_every_open_period_is_REFUSED_not_silently_dropped(self):
        """A rise recorded on a contract that no payslip ever reads is a rise
        that never happens, and the old code reported success."""
        r = self._convert(self._atr(datetime.date(2099, 12, 1)))
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('open payroll period', r.json()['detail'].lower())
        self.assertFalse(
            PayrollAmendment.objects.filter(employee=self.emp).exists(),
            'nothing should have been raised')
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.current_contract.basic, Decimal('9000.00'),
                         'a refused regrade must not have touched the contract')
