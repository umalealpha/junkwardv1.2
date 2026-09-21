"""Prompt 03 (Shared Contract v1, 2026-09-12) — Authority to Recruit seeds
payroll on conversion instead of a bare name/department shell.

Before this fix, authority_convert() copied ONLY full_name, department,
company, employee_number, status — job_title, hire_date, the
EmploymentContract and recurring components were never seeded, so HR had to
re-key everything the ATR's five signatories had already approved (and the
monthly-pack ATR check had nothing to compare against).

Run: manage.py test recruitment.test_atr_payroll_seed
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

import datetime

from core.models import Company
from payroll.contract_models import EmploymentContract
from payroll.models import Employee, PayrollAmendment, PayrollPeriod
from recruitment.models import AuthorityToRecruit

CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'


def _month_end(d):
    nxt = datetime.date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def _approved_atr(**kw):
    base = dict(
        kind=AuthorityToRecruit.Kind.RECRUIT,
        person_name='Boitumelo Seatla',
        position='Underwriting Officer',
        department='Underwriting',
        entity='Alpha Direct Insurance Company',
        employment_type='permanent',
        effective_date=date(2026, 10, 1),
        proposed_basic_salary=Decimal('14500.00'),
        salary_lines=[
            {'sn': 1, 'item': 'Basic Salary', 'monthly': '14500.00', 'annual': '174000.00'},
            {'sn': 2, 'item': 'Housing Allowance', 'monthly': '2000.00', 'annual': '24000.00'},
            {'sn': 3, 'item': 'Total', 'monthly': '16500.00', 'annual': '198000.00'},
        ],
        quoted_ctc_monthly=Decimal('16500.00'),
    )
    base.update(kw)
    a = AuthorityToRecruit.objects.create(**base)
    a.approvals = {slug: {'decision': 'approved', 'by': label, 'at': '2026-09-12T00:00:00Z'}
                  for slug, label, _e in AuthorityToRecruit.SIGNATORIES}
    a.recompute_status()
    a.save(update_fields=['approvals', 'status', 'updated_at'])
    return a


class AtrSeedsPayrollTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user('cfo3', email=CFO_EMAIL, password='x')
        self.client.force_authenticate(self.cfo)
        self.company = Company.objects.create(code='ADI3', name='Alpha Direct Insurance Company')

    def _url(self, a):
        return reverse('v1-recruitment-authority-convert', args=[a.id])

    def test_convert_seeds_job_title_hire_date_and_contract(self):
        a = _approved_atr()
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 201, r.content)
        emp = Employee.objects.get(pk=r.json()['employee_id'])
        self.assertEqual(emp.job_title, 'Underwriting Officer')
        self.assertEqual(str(emp.hire_date), '2026-10-01')
        self.assertEqual(emp.recruit_authority_ref, a.reference)

        contract = EmploymentContract.objects.get(employee=emp)
        self.assertEqual(contract.basic, Decimal('14500.00'))
        self.assertEqual(contract.status, EmploymentContract.Status.ACTIVE)
        # Housing Allowance carried into allowance_template, Total excluded.
        self.assertIn('HOUSING_ALLOWANCE', contract.allowance_template)
        self.assertNotIn('TOTAL', contract.allowance_template)

    def test_reclick_convert_does_not_duplicate_employee_or_contract(self):
        a = _approved_atr()
        r1 = self.client.post(self._url(a))
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.client.post(self._url(a))
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertTrue(r2.json()['already'])
        self.assertEqual(Employee.objects.filter(recruit_authority_ref=a.reference).count(), 1)
        self.assertEqual(EmploymentContract.objects.filter(employee_id=r1.json()['employee_id']).count(), 1)

    def test_next_payroll_run_basic_equals_signed_amount(self):
        """The acceptance line: convert, then the monthly-pack ATR check
        (compares payslip BASIC to the signed base) must read OK — the
        contract's basic IS the signed proposed_basic_salary."""
        a = _approved_atr()
        r = self.client.post(self._url(a))
        emp = Employee.objects.get(pk=r.json()['employee_id'])
        contract = emp.current_contract or emp.contracts.order_by('-start_date').first()
        self.assertEqual(contract.basic, a.proposed_basic_salary)


class AtrRegradeTests(APITestCase):
    """Prompt 03, item 5: a REGRADE never creates a new employee — it updates
    the existing one's contract + BASIC and raises a PayrollAmendment from the
    effective date."""

    def setUp(self):
        self.cfo = User.objects.create_user('cfo4', email=CFO_EMAIL, password='x')
        self.client.force_authenticate(self.cfo)
        self.company = Company.objects.create(code='ADI4', name='Alpha Direct Insurance Company')
        self.emp = Employee.objects.create(
            employee_number='RG-001', full_name='Existing Regrade Person',
            company=self.company, hire_date=datetime.date(2022, 1, 1))
        EmploymentContract.objects.create(
            employee=self.emp, start_date=datetime.date(2022, 1, 1),
            basic=Decimal('9000.00'), status=EmploymentContract.Status.ACTIVE)
        # The amendment lands on the period that CONTAINS the effective date
        # (/fabe 2026-09-12) — so the fixture needs a period covering today for
        # the effective-today cases, and one covering the far-future date used
        # by the future-dated case. With only one period, every ordering and
        # every filter gives the same answer and the test proves nothing.
        today = timezone.localdate()
        self.period = PayrollPeriod.objects.create(
            period_name=f'{today:%Y-%m}', start_date=today.replace(day=1),
            end_date=_month_end(today))  # default status=OPEN
        self.future_period = PayrollPeriod.objects.create(
            period_name='2099-12', start_date=datetime.date(2099, 12, 1),
            end_date=datetime.date(2099, 12, 31))

    def _url(self, a):
        return reverse('v1-recruitment-authority-convert', args=[a.id])

    def _regrade_atr(self, **kw):
        base = dict(
            kind=AuthorityToRecruit.Kind.REGRADE,
            person_name='Existing Regrade Person',
            position='Senior Underwriting Officer',
            entity='Alpha Direct Insurance Company',
            employment_type='permanent',
            effective_date=timezone.localdate(),
            proposed_basic_salary=Decimal('11000.00'),
            salary_lines=[{'sn': 1, 'item': 'Basic Salary', 'monthly': '11000.00', 'annual': '132000.00'}],
        )
        base.update(kw)
        a = AuthorityToRecruit.objects.create(**base)
        a.approvals = {slug: {'decision': 'approved', 'by': label, 'at': '2026-09-12T00:00:00Z'}
                      for slug, label, _e in AuthorityToRecruit.SIGNATORIES}
        a.recompute_status()
        a.save(update_fields=['approvals', 'status', 'updated_at'])
        return a

    def test_regrade_updates_existing_employee_no_new_row(self):
        before_count = Employee.objects.count()
        a = self._regrade_atr()
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Employee.objects.count(), before_count,
                         'a regrade must never create a new employee')
        self.assertEqual(r.json()['employee_id'], str(self.emp.id))

        self.emp.refresh_from_db()
        contract = self.emp.current_contract
        self.assertEqual(contract.basic, Decimal('11000.00'))

        amd = PayrollAmendment.objects.get(employee=self.emp, kind=PayrollAmendment.Kind.SALARY_CHANGE)
        self.assertEqual(amd.amount, Decimal('11000.00'))
        self.assertEqual(amd.batch.target_period_id, self.period.id)

    def test_regrade_future_dated_does_not_raise_pay_yet(self):
        """CFO decision 2026-09-12: the authority carries the start date of
        employment on the new grade, so a regrade dated in the FUTURE must
        NOT raise the person's pay today. The contract they are paid on now
        keeps the old basic; a second window opens on the effective date."""
        far_future = datetime.date(2099, 12, 1)
        old_basic = self.emp.current_contract.basic
        a = self._regrade_atr(effective_date=far_future)
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 200, r.content)
        self.emp.refresh_from_db()

        now_contract = self.emp.current_contract
        self.assertIsNotNone(now_contract)
        self.assertEqual(now_contract.basic, old_basic,
                         'a future-dated regrade must not raise pay today')
        self.assertEqual(now_contract.end_date, far_future - datetime.timedelta(days=1),
                         'the old window must close the day before the new grade starts')

        future = self.emp.contracts.filter(start_date=far_future).first()
        self.assertIsNotNone(future, 'a contract window must open on the effective date')
        self.assertEqual(future.basic, Decimal('11000.00'))

        self.assertFalse(
            PayrollAmendment.objects.filter(
                employee=self.emp, kind=PayrollAmendment.Kind.SALARY_CHANGE,
                batch__target_period=self.period).exists(),
            'the rise must not be raised against the CURRENT open period')

    def test_reclick_regrade_does_not_duplicate(self):
        a = self._regrade_atr()
        r1 = self.client.post(self._url(a))
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = self.client.post(self._url(a))
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertTrue(r2.json()['already'])
        self.assertEqual(
            PayrollAmendment.objects.filter(employee=self.emp,
                                            kind=PayrollAmendment.Kind.SALARY_CHANGE).count(),
            1, 're-clicking convert must not raise a second amendment')

    def test_ambiguous_namesake_refuses_rather_than_guessing(self):
        """Coordinator review, 2026-09-12: two same-entity employees with the
        same name used to silently pick matches[0] — a regrade could raise
        the WRONG person's basic pay with no error. Must refuse instead."""
        namesake = Employee.objects.create(
            employee_number='RG-002', full_name='Existing Regrade Person',
            company=self.company, hire_date=datetime.date(2023, 1, 1))
        before_basic = self.emp.current_contract.basic
        a = self._regrade_atr()
        r = self.client.post(self._url(a))
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('more than one', r.json()['detail'].lower())
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.current_contract.basic, before_basic,
                         'neither namesake\'s pay may be touched when the match is ambiguous')
        self.assertFalse(PayrollAmendment.objects.filter(
            kind=PayrollAmendment.Kind.SALARY_CHANGE, employee__in=[self.emp, namesake]).exists())
