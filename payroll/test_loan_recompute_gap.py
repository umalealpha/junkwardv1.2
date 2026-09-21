"""Prompt 01 (Shared Contract v1, 2026-09-12) — staff-loan repayment: close
the recompute + account-binding gaps.

Before this fix:
  * apply_loan_repayments() materialised the LOAN_REPAYMENT PayslipLine but
    never called payslip.recompute_totals(), so net_amount stayed stale.
  * An opening-balance loan (no prior disbursement) left the LOAN_REPAYMENT
    component's posting_account_code unbound.

Run: manage.py test payroll.test_loan_recompute_gap
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import Employee, PayrollPeriod, PayslipComponent, Payslip, PayslipLine
from payroll.contract_models import EmployeeLoan
from payroll.loan_service import apply_loan_repayments


class LoanRecomputeGapTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='TST2', name='Test Co Two')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-09', start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))   # default status=OPEN
        self.emp = Employee.objects.create(
            employee_number='LR1', full_name='Loan Recompute Test', company=self.co)
        self.basic = PayslipComponent.objects.get_or_create(
            code='BASIC', defaults={'name': 'Basic Salary',
                                    'kind': PayslipComponent.Kind.EARNING,
                                    'is_taxable': True})[0]
        self.payslip = Payslip.objects.create(employee=self.emp, period=self.period,
                                              company=self.co)
        PayslipLine.objects.create(payslip=self.payslip, component=self.basic,
                                   amount=Decimal('5000.00'))
        self.payslip.recompute_totals(recompute_paye=False)
        self.payslip.save(update_fields=['gross_amount', 'paye_amount', 'net_amount', 'ctc_amount'])
        self.assertEqual(self.payslip.net_amount, Decimal('5000.00'))  # sanity baseline

        self.loan = EmployeeLoan.objects.create(
            employee=self.emp, principal=Decimal('1200.00'),
            annual_rate_pct=Decimal('0'), term_months=12,
            status=EmployeeLoan.Status.ACTIVE, outstanding=Decimal('1200.00'))

    def test_net_amount_reflects_the_loan_deduction_after_apply(self):
        """THE bug: net_amount must fall by the instalment. Straight-line
        1200/12 = 100.00 instalment."""
        apply_loan_repayments(self.period)
        self.payslip.refresh_from_db()
        self.assertEqual(self.payslip.net_amount, Decimal('4900.00'),
                         'net_amount did not reflect the loan deduction — the '
                         'recompute-on-touch gap is back.')
        self.assertEqual(self.payslip.gross_amount, Decimal('5000.00'))  # gross unaffected (deduction, not earning)

    def test_loan_only_employee_still_gets_a_recomputed_net(self):
        """A loan-only employee (no other amendment this period) must end the
        run with net_amount that includes the deduction — the exact
        acceptance line from the prompt."""
        other_emp = Employee.objects.create(employee_number='LR2', full_name='Loan Only', company=self.co)
        other_ps = Payslip.objects.create(employee=other_emp, period=self.period, company=self.co)
        PayslipLine.objects.create(payslip=other_ps, component=self.basic, amount=Decimal('3000.00'))
        other_ps.recompute_totals(recompute_paye=False)
        other_ps.save()
        EmployeeLoan.objects.create(
            employee=other_emp, principal=Decimal('600.00'), annual_rate_pct=Decimal('0'),
            term_months=6, status=EmployeeLoan.Status.ACTIVE, outstanding=Decimal('600.00'))

        apply_loan_repayments(self.period)
        other_ps.refresh_from_db()
        self.assertEqual(other_ps.net_amount, Decimal('2900.00'))  # 3000 - 100

    def test_rerun_same_period_does_not_double_deduct(self):
        """Run-it-twice: no double deduction, outstanding drops once."""
        apply_loan_repayments(self.period)
        self.loan.refresh_from_db()
        after_first = self.loan.outstanding
        self.payslip.refresh_from_db()
        net_after_first = self.payslip.net_amount

        apply_loan_repayments(self.period)   # re-run — must be a no-op for this loan
        self.loan.refresh_from_db()
        self.payslip.refresh_from_db()

        self.assertEqual(self.loan.outstanding, after_first)
        self.assertEqual(self.payslip.net_amount, net_after_first)
        from payroll.contract_models import LoanRepayment
        self.assertEqual(LoanRepayment.objects.filter(loan=self.loan, period=self.period).count(), 1)

    def test_opening_balance_loan_posts_without_prior_disbursement(self):
        """An opening-balance loan (no disburse() ever called) must still end
        up with a bound posting_account_code once the engine touches it —
        previously blocked with 'missing posting_account_code'."""
        comp, _ = PayslipComponent.objects.get_or_create(
            code='LOAN_REPAYMENT',
            defaults={'name': 'Loan Repayment',
                     'kind': PayslipComponent.Kind.EMPLOYEE_DEDUCTION, 'is_taxable': False})
        # Force it unbound first, as an opening-balance-only company would be.
        comp.posting_account_code = ''
        comp.save(update_fields=['posting_account_code'])
        apply_loan_repayments(self.period)
        comp.refresh_from_db()
        self.assertTrue((comp.posting_account_code or '').strip(),
                        'LOAN_REPAYMENT component is still unbound after apply_loan_repayments()')
        self.assertEqual(comp.posting_account_code, '121010')

    def test_refuses_to_write_into_a_non_open_period(self):
        self.period.status = PayrollPeriod.Status.LOCKED
        self.period.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            apply_loan_repayments(self.period)


class LoanReceivableAccountSinglSourceTests(TestCase):
    """CFO decision 2026-09-12 (corrected the same day): the staff-loan
    receivable account is **121010**, the account actually named "Staff Loan"
    in Omni's chart.

    It was briefly set to 121000. In the live chart 121000 is "Provision for
    Bad Debts - ECL", and _ensure_account() resolves by CODE alone and returns
    whatever already holds it - so the fallback would have handed back the
    bad-debt provision and every payslip loan deduction would have posted
    there. The prod LOAN_REPAYMENT component was still unbound, so the first
    payroll run after that deploy would have bound it.

    The tests below pin both that the two paths read ONE value, and - the part
    that was missing and let the wrong number through - that the resolved
    account is the one NAMED for staff loans when a real chart exists.
    """

    def test_config_default_is_the_staff_loan_account(self):
        from payroll import config as payroll_config
        self.assertEqual(payroll_config.get_setting('staff_loan.receivable_account_code'), '121010')

    def test_staff_loans_services_reads_the_same_value(self):
        from staff_loans.services import _receivable_account_code
        self.assertEqual(_receivable_account_code(), '121010')

    def test_resolve_loan_accounts_creates_the_fallback(self):
        from staff_loans.services import resolve_loan_accounts
        co = Company.objects.create(code='RCV1', name='Receivable Account Test Co')
        receivable, _interest = resolve_loan_accounts(co)
        self.assertEqual(receivable.code, '121010')

    def test_the_loan_account_is_resolved_by_name_not_just_code(self):
        """THE GUARD THAT WAS MISSING. Seeds the chart as prod actually has it
        - 121000 "Provision for Bad Debts - ECL" and 121010 "Staff Loan" - and
        proves the resolver lands on the staff-loan account. The old tests all
        ran against an EMPTY chart, where any code at all looks correct."""
        from ledger.models import Account
        from staff_loans.services import resolve_loan_accounts
        co = Company.objects.create(code='RCV3', name='Real Chart Co')
        Account.objects.create(code='121000', name='Provision for Bad Debts - ECL',
                               account_type='asset', sub_type='current_asset', is_active=True)
        Account.objects.create(code='121010', name='Staff Loan',
                               account_type='asset', sub_type='current_asset', is_active=True)
        Account.objects.create(code='124004', name='Interest From Staff Loan',
                               account_type='income', sub_type='other_income', is_active=True)
        receivable, interest = resolve_loan_accounts(co)
        self.assertIn('staff loan', receivable.name.lower(),
                      f'staff loans resolved to {receivable.code} "{receivable.name}" - '
                      f'an account code is not a promise about what the account IS')
        self.assertNotIn('bad debt', receivable.name.lower())
        self.assertIn('interest', interest.name.lower())

    def test_apply_loan_repayments_binds_to_the_staff_loan_account(self):
        """End-to-end through the actual engine, not just the config read."""
        comp, _ = PayslipComponent.objects.get_or_create(
            code='LOAN_REPAYMENT',
            defaults={'name': 'Loan Repayment',
                     'kind': PayslipComponent.Kind.EMPLOYEE_DEDUCTION, 'is_taxable': False})
        comp.posting_account_code = ''
        comp.save(update_fields=['posting_account_code'])
        co = Company.objects.create(code='RCV2', name='Receivable Account Test Co 2')
        period = PayrollPeriod.objects.create(
            period_name='2026-12', start_date=datetime.date(2026, 12, 1),
            end_date=datetime.date(2026, 12, 31))
        emp = Employee.objects.create(employee_number='RCV-E1', full_name='Receivable Test', company=co)
        EmployeeLoan.objects.create(employee=emp, principal=Decimal('120.00'),
                                    annual_rate_pct=Decimal('0'), term_months=12,
                                    status=EmployeeLoan.Status.ACTIVE, outstanding=Decimal('120.00'))
        apply_loan_repayments(period)
        comp.refresh_from_db()
        self.assertEqual(comp.posting_account_code, '121010')
