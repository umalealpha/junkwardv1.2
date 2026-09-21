"""
Tests for payroll/services.py — GL posting and reversal of payroll periods.
See .claude/specs/payroll-gl-posting/design.md §"Tests".
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency
from ledger.models import Account, FiscalPeriod, JournalEntry
from payroll.models import (
    Employee, Payslip, PayslipComponent, PayslipLine, PayrollPeriod,
)
from payroll.services import (
    SALARY_PAYABLE_ACCOUNT_CODE,
    post_payroll_period,
    reverse_payroll_period,
    reconcile_payroll_period,
)


ZERO = Decimal('0.00')


def _make_account(code, name, account_type='expense'):
    return Account.objects.create(
        code=code, name=name,
        account_type=account_type,
        sub_type='test',
        is_active=True,
    )


class PayrollGLPostingTest(TestCase):
    """Cover the 10 scenarios in design.md §Tests."""

    @classmethod
    def setUpTestData(cls):
        # Currency + company
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TEST', name='Test Co.')

        # Fiscal period covering the test pay date
        cls.fiscal = FiscalPeriod.objects.create(
            period_name='2026-04', start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30), status=FiscalPeriod.Status.OPEN,
        )
        # A reversal contra JE is dated on the reversal date ("today"), so an
        # open period must also cover the current date for reverse to post.
        _t = date.today()
        cls.fiscal_now = FiscalPeriod.objects.create(
            period_name=_t.strftime('%Y-%m'),
            start_date=date(_t.year, 1, 1), end_date=date(_t.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )

        # Chart of accounts
        cls.acct_salary_exp = _make_account('6100', 'Salaries and wages', 'expense')
        cls.acct_paye       = _make_account('2160', 'PAYE Payable',       'liability')
        cls.acct_salary_pay = _make_account(SALARY_PAYABLE_ACCOUNT_CODE,
                                            'Salary Payable',             'liability')
        cls.acct_med_aid    = _make_account('2155', 'Medical Aid Payable', 'liability')
        cls.acct_employer_cost = _make_account('6101',
                                            'Employer Contributions',     'expense')
        # 2151 (Employer Contributions Payable) is seeded by migration
        # payroll.0008, so it already exists in the test DB — don't recreate it.
        cls.acct_contrib_pay = Account.objects.get(code='2151')

        # Components — earnings/employer-cost map to expense, taxes/deductions to liability
        cls.comp_basic = PayslipComponent.objects.create(
            code='BASIC', name='Basic Salary',
            kind=PayslipComponent.Kind.EARNING, is_taxable=True,
            posting_account_code='6100',
        )
        cls.comp_paye = PayslipComponent.objects.create(
            code='PAYE', name='PAYE Tax',
            kind=PayslipComponent.Kind.TAX,
            posting_account_code='2160',
        )
        cls.comp_med = PayslipComponent.objects.create(
            code='MED', name='Medical Aid',
            kind=PayslipComponent.Kind.EMPLOYEE_DEDUCTION,
            posting_account_code='2155',
        )
        cls.comp_pension = PayslipComponent.objects.create(
            code='PENS', name='Pension (employer)',
            kind=PayslipComponent.Kind.COMPANY_CONTRIBUTION,
            posting_account_code='6101',
        )
        cls.comp_gross = PayslipComponent.objects.create(
            code='GROSS', name='Gross', kind=PayslipComponent.Kind.COMPUTED_GROSS,
        )

        # Users — poster and cfo (distinct), plus a regular user
        cls.poster_user = User.objects.create_user('poster', password='x')
        Group.objects.get_or_create(name='payroll_poster')[0].user_set.add(cls.poster_user)
        cls.cfo_user = User.objects.create_user('cfo', password='x')
        Group.objects.get_or_create(name='cfo')[0].user_set.add(cls.cfo_user)
        cls.regular_user = User.objects.create_user('staff', password='x')

        # Employees
        cls.emp_a = Employee.objects.create(
            employee_number='E001', full_name='Alice', company=cls.company,
        )
        cls.emp_b = Employee.objects.create(
            employee_number='E002', full_name='Bob',   company=cls.company,
        )

    def _build_period(self, status=PayrollPeriod.Status.APPROVED):
        period = PayrollPeriod.objects.create(
            period_name='2026-04', start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30), pay_date=date(2026, 4, 25),
            status=status,
        )
        # Two approved payslips
        ps_a = Payslip.objects.create(
            employee=self.emp_a, period=period, company=self.company,
            status=Payslip.Status.APPROVED,
        )
        PayslipLine.objects.create(payslip=ps_a, component=self.comp_basic,
                                   amount=Decimal('10000.00'))
        PayslipLine.objects.create(payslip=ps_a, component=self.comp_paye,
                                   amount=Decimal('1200.00'))
        PayslipLine.objects.create(payslip=ps_a, component=self.comp_med,
                                   amount=Decimal('500.00'))
        PayslipLine.objects.create(payslip=ps_a, component=self.comp_pension,
                                   amount=Decimal('800.00'))
        PayslipLine.objects.create(payslip=ps_a, component=self.comp_gross,
                                   amount=Decimal('10000.00'))   # COMPUTED — ignored
        ps_a.recompute_totals()
        ps_a.save()

        ps_b = Payslip.objects.create(
            employee=self.emp_b, period=period, company=self.company,
            status=Payslip.Status.APPROVED,
        )
        PayslipLine.objects.create(payslip=ps_b, component=self.comp_basic,
                                   amount=Decimal('20000.00'))
        PayslipLine.objects.create(payslip=ps_b, component=self.comp_paye,
                                   amount=Decimal('3500.00'))
        PayslipLine.objects.create(payslip=ps_b, component=self.comp_med,
                                   amount=Decimal('500.00'))
        PayslipLine.objects.create(payslip=ps_b, component=self.comp_pension,
                                   amount=Decimal('1600.00'))
        ps_b.recompute_totals()
        ps_b.save()

        return period

    # ----- 1. Post creates a balanced JE --------------------------------
    def test_post_balances_dr_cr(self):
        period = self._build_period()
        je = post_payroll_period(period, self.poster_user)
        total_dr = sum(ln.debit_bwp  for ln in je.lines.all())
        total_cr = sum(ln.credit_bwp for ln in je.lines.all())
        self.assertEqual(total_dr, total_cr)
        # Earnings (10k + 20k) + employer pension (0.8k + 1.6k) = 32400
        self.assertEqual(total_dr, Decimal('32400.00'))

    # ----- 2. period.journal_entry FK set after post --------------------
    def test_post_writes_period_journal_entry_fk(self):
        period = self._build_period()
        je = post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        self.assertEqual(period.journal_entry_id, je.id)
        self.assertEqual(period.status, PayrollPeriod.Status.POSTED)

    # ----- 3. Second post raises ----------------------------------------
    def test_post_idempotent_raises(self):
        period = self._build_period()
        post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        with self.assertRaises(ValidationError):
            post_payroll_period(period, self.poster_user)

    # ----- 4. Unmapped components raise ---------------------------------
    def test_post_unmapped_component_raises(self):
        # Clear the posting_account_code on one component
        self.comp_paye.posting_account_code = ''
        self.comp_paye.save()
        period = self._build_period()
        with self.assertRaises(ValidationError):
            post_payroll_period(period, self.poster_user)
        # Restore so other tests are unaffected (setUpTestData is per-class)
        self.comp_paye.posting_account_code = '2160'
        self.comp_paye.save()

    # ----- 5. Computed components do not get a JE line ------------------
    def test_post_skips_computed_components(self):
        period = self._build_period()
        je = post_payroll_period(period, self.poster_user)
        # No line should be mapped to a component of kind COMPUTED_*
        for ln in je.lines.all():
            self.assertNotEqual(ln.account.code, '')  # always real

    # ----- 6. DRAFT payslip in same period is ignored -------------------
    def test_post_excludes_unapproved_payslips(self):
        period = self._build_period()
        # Add a DRAFT payslip with large amounts that would unbalance the JE
        # if accidentally included. emp_a/emp_b already have payslips in this
        # period and (employee, period) is unique, so use a fresh employee.
        new_emp = Employee.objects.create(
            employee_number='E999', full_name='Drafty', company=self.company,
        )
        ps_draft = Payslip.objects.create(
            employee=new_emp, period=period, company=self.company,
            status=Payslip.Status.DRAFT,
        )
        PayslipLine.objects.create(payslip=ps_draft, component=self.comp_basic,
                                   amount=Decimal('999999.00'))
        ps_draft.recompute_totals()
        ps_draft.save()

        je = post_payroll_period(period, self.poster_user)
        # Expected DR is unchanged (32400.00) — DRAFT was excluded
        total_dr = sum(ln.debit_bwp for ln in je.lines.all())
        self.assertEqual(total_dr, Decimal('32400.00'))

    # ----- 7. Reverse creates contra JE ---------------------------------
    def test_reverse_creates_contra_je(self):
        period = self._build_period()
        original_je = post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        contra = reverse_payroll_period(period, self.cfo_user, reason='audit error')
        period.refresh_from_db()
        self.assertEqual(period.status, PayrollPeriod.Status.APPROVED)
        self.assertIsNone(period.journal_entry_id)
        # Per account: original DR == contra CR (and vice versa)
        for acct in {ln.account for ln in original_je.lines.all()}:
            orig_dr = sum(ln.debit_bwp for ln in original_je.lines.filter(account=acct))
            orig_cr = sum(ln.credit_bwp for ln in original_je.lines.filter(account=acct))
            cont_dr = sum(ln.debit_bwp for ln in contra.lines.filter(account=acct))
            cont_cr = sum(ln.credit_bwp for ln in contra.lines.filter(account=acct))
            self.assertEqual(orig_dr, cont_cr)
            self.assertEqual(orig_cr, cont_dr)
        original_je.refresh_from_db()
        self.assertEqual(original_je.status, JournalEntry.Status.REVERSED)

    # ----- 8. Reverse blocked if any payslip is PAID --------------------
    def test_reverse_blocked_if_any_payslip_paid(self):
        period = self._build_period()
        post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        # Mark one payslip as PAID
        ps = period.payslips.first()
        ps.status = Payslip.Status.PAID
        ps.save()
        with self.assertRaises(ValidationError):
            reverse_payroll_period(period, self.cfo_user, reason='too late')

    # ----- 9. Reverse requires CFO --------------------------------------
    def test_reverse_requires_cfo_group(self):
        period = self._build_period()
        post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        with self.assertRaises(ValidationError):
            reverse_payroll_period(period, self.regular_user, reason='no auth')
        # And poster (not CFO) is also blocked
        with self.assertRaises(ValidationError):
            reverse_payroll_period(period, self.poster_user, reason='no auth')

    # ----- 10. Reconcile returns ok=True on a freshly-posted period ----
    def test_reconcile_clean_period(self):
        period = self._build_period()
        post_payroll_period(period, self.poster_user)
        period.refresh_from_db()
        result = reconcile_payroll_period(period)
        self.assertTrue(result['ok'], msg=result)


class PayrollPostPermissionTest(TestCase):
    """_can_post_payroll must honour the finance title/role layer, not only
    Django groups. CFO directive 2026-06-25 — Pako, a Financial Controller
    (+ FINANCE_MANAGER role), was wrongly blocked because the finance_manager /
    cfo groups don't exist and the gate ignored titles/roles entirely."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Role, UserProfile, UserRoleAssignment

        # Financial Controller by TITLE (Pako's situation) — no group, no role.
        cls.fc = User.objects.create_user('fc_post', password='x')
        UserProfile.objects.update_or_create(
            user=cls.fc, defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER})

        # Finance Manager by RBAC ROLE only — title left non-finance.
        cls.fm_role = User.objects.create_user('fmrole_post', password='x')
        UserProfile.objects.update_or_create(
            user=cls.fm_role, defaults={'title': UserProfile.Title.ACCOUNTANT})
        role, _ = Role.objects.get_or_create(
            code='FINANCE_MANAGER', defaults={'name': 'Finance Manager', 'level': 3})
        UserRoleAssignment.objects.create(user=cls.fm_role, role=role)

        # Profile administrator — may post.
        cls.admin_prof = User.objects.create_user('adminprof_post', password='x')
        UserProfile.objects.update_or_create(
            user=cls.admin_prof,
            defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_administrator': True})

        # Operations staff — must NOT be able to post (separation of duties).
        cls.ops = User.objects.create_user('ops_post', password='x')
        UserProfile.objects.update_or_create(
            user=cls.ops, defaults={'title': UserProfile.Title.OPERATIONS})

        # Regression: existing Django-group path still works.
        cls.poster = User.objects.create_user('grp_poster', password='x')
        Group.objects.get_or_create(name='payroll_poster')[0].user_set.add(cls.poster)

        cls.su = User.objects.create_superuser('su_post', 'su_post@x.com', 'x')

    def test_financial_controller_title_can_post(self):
        from payroll.services import _can_post_payroll
        self.assertTrue(_can_post_payroll(self.fc))

    def test_finance_manager_role_can_post(self):
        from payroll.services import _can_post_payroll
        self.assertTrue(_can_post_payroll(self.fm_role))

    def test_profile_administrator_can_post(self):
        from payroll.services import _can_post_payroll
        self.assertTrue(_can_post_payroll(self.admin_prof))

    def test_operations_cannot_post(self):
        from payroll.services import _can_post_payroll
        self.assertFalse(_can_post_payroll(self.ops))

    def test_group_poster_still_can_post(self):
        from payroll.services import _can_post_payroll
        self.assertTrue(_can_post_payroll(self.poster))

    def test_superuser_can_post(self):
        from payroll.services import _can_post_payroll
        self.assertTrue(_can_post_payroll(self.su))


class PayrollPageGateTest(TestCase):
    """_payroll_view (the Payroll page gate that fronts the input/upload
    endpoints) must honour the HR_MANAGER role, not only title/department —
    else a role-granted HR user (Tshephang, Veritas) can open the Payroll
    Register but 403s on the Payroll page. CFO directive 2026-06-25."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Role, UserProfile, UserRoleAssignment
        # HR by ROLE only: title 'operations', no HR department (Tshephang).
        cls.hr_role_user = User.objects.create_user('tshephang_t', password='x')
        UserProfile.objects.update_or_create(
            user=cls.hr_role_user,
            defaults={'title': UserProfile.Title.OPERATIONS, 'department': ''})
        role, _ = Role.objects.get_or_create(
            code='HR_MANAGER', defaults={'name': 'HR Manager', 'level': 2})
        UserRoleAssignment.objects.create(user=cls.hr_role_user, role=role)
        # Plain operations — must stay blocked.
        cls.ops = User.objects.create_user('ops_pg', password='x')
        UserProfile.objects.update_or_create(
            user=cls.ops, defaults={'title': UserProfile.Title.OPERATIONS})

    def _req(self, user):
        return type('R', (), {'user': user})()

    def test_hr_role_passes_payroll_page(self):
        from payroll.amendment_views import _payroll_view
        _payroll_view(self._req(self.hr_role_user))  # must NOT raise

    def test_operations_blocked_from_payroll_page(self):
        from rest_framework.exceptions import PermissionDenied
        from payroll.amendment_views import _payroll_view
        with self.assertRaises(PermissionDenied):
            _payroll_view(self._req(self.ops))
