"""
Tests for the staff_loans app — policy maths, the full workflow, the GL
issuance entry, segregation-of-duties, blue-book control, and the monthly
payroll deduction reusing the live EmployeeLoan engine.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency, UserProfile
from ledger.models import Account, FiscalPeriod, JournalEntry
from payroll.contract_models import EmployeeLoan, EmploymentContract
from payroll.models import Employee, PayrollPeriod, PayslipLine

from unittest import mock

from staff_loans import policy
from staff_loans import rates
from staff_loans import services as svc
from staff_loans.models import StaffLoanApplication, StaffLoanRate


ZERO = Decimal('0.00')

# A valid ≥50-word motivation for reuse in tests.
MOTIVATION = (
    'I am requesting this staff loan to cover urgent family and household expenses that I '
    'cannot meet from my current monthly salary alone this month. The money will help me '
    'settle a pressing medical bill and some school costs for my children, and I will repay '
    'it responsibly through the agreed monthly salary deductions without fail.'
)


# ---------------------------------------------------------------------------
# Policy (pure) tests
# ---------------------------------------------------------------------------

class PolicyMathTest(TestCase):
    def test_flat_interest_and_total(self):
        # P5,000 at 15.5% for 4 months (flat): 5000 * 0.155 * 4/12 = 258.33
        self.assertEqual(policy.flat_interest(5000, Decimal('15.5'), 4), Decimal('258.33'))
        self.assertEqual(policy.total_repayable(5000, Decimal('15.5'), 4), Decimal('5258.33'))
        self.assertEqual(policy.monthly_instalment(5000, Decimal('15.5'), 4), Decimal('1314.58'))

    def test_zero_rate_is_straight_line(self):
        self.assertEqual(policy.flat_interest(4000, 0, 4), ZERO)
        self.assertEqual(policy.total_repayable(4000, 0, 4), Decimal('4000.00'))

    def test_staff_cap_one_month_salary(self):
        errs = policy.validate_staff_loan(Decimal('9000'), 3, Decimal('8000'))
        self.assertIn('amount_requested', errs)  # over one month salary
        self.assertFalse(policy.validate_staff_loan(Decimal('8000'), 3, Decimal('8000')))

    def test_staff_term_max_six_months(self):
        self.assertIn('term_months_requested', policy.validate_staff_loan(Decimal('1000'), 7, Decimal('8000')))
        self.assertFalse(policy.validate_staff_loan(Decimal('1000'), 6, Decimal('8000')))

    def test_motivation_min_50_words(self):
        self.assertIn('reason', policy.validate_motivation('too short a reason'))
        self.assertFalse(policy.validate_motivation(MOTIVATION))

    def test_declarations(self):
        self.assertIn('no_other_loans_declared',
                      policy.validate_declarations(no_other_loans=False, loan_type=policy.STAFF, purchased_via_veritas=False))
        self.assertIn('purchased_via_veritas',
                      policy.validate_declarations(no_other_loans=True, loan_type=policy.VEHICLE, purchased_via_veritas=False))
        self.assertFalse(
            policy.validate_declarations(no_other_loans=True, loan_type=policy.VEHICLE, purchased_via_veritas=True))
        self.assertFalse(
            policy.validate_declarations(no_other_loans=True, loan_type=policy.STAFF, purchased_via_veritas=False))

    def test_staff_needs_salary_on_file(self):
        self.assertIn('amount_requested', policy.validate_staff_loan(Decimal('1000'), 2, None))

    def test_vehicle_cap_40k(self):
        self.assertIn('amount_requested', policy.validate_vehicle_loan(Decimal('40001'), 12))
        self.assertFalse(policy.validate_vehicle_loan(Decimal('40000'), 12))

    def test_vehicle_term_max_24(self):
        self.assertIn('term_months_requested', policy.validate_vehicle_loan(Decimal('30000'), 25))


# ---------------------------------------------------------------------------
# Workflow + GL tests
# ---------------------------------------------------------------------------

class RateTest(TestCase):
    def test_fallback_when_no_rows(self):
        self.assertEqual(rates.current_annual_rate(), policy.DEFAULT_ANNUAL_RATE_PCT)
        self.assertEqual(rates.current_spread(), policy.DEFAULT_SPREAD_PCT)

    def test_latest_effective_row_wins(self):
        StaffLoanRate.objects.create(effective_from=date(2026, 6, 1), annual_rate_pct=Decimal('14.0'),
                                     spread_pct=Decimal('10'), source='auto')
        StaffLoanRate.objects.create(effective_from=date(2026, 7, 1), annual_rate_pct=Decimal('16.0'),
                                     spread_pct=Decimal('10'), source='auto')
        # a future row must NOT win yet
        StaffLoanRate.objects.create(effective_from=date(2027, 1, 1), annual_rate_pct=Decimal('99.0'),
                                     spread_pct=Decimal('10'), source='manual')
        self.assertEqual(rates.current_annual_rate(), Decimal('16.000'))

    def test_refresh_computes_mopr_plus_spread(self):
        with mock.patch('staff_loans.rates.fetch_reference_rate', return_value=Decimal('6.0')):
            res = rates.refresh()
        self.assertEqual(res['status'], 'created')
        self.assertEqual(Decimal(res['rate']), Decimal('16.00'))   # 6 + 10 spread fallback
        self.assertEqual(rates.current_annual_rate(), Decimal('16.00'))

    def test_refresh_fetch_failed_keeps_rate(self):
        StaffLoanRate.objects.create(effective_from=date(2026, 7, 1), annual_rate_pct=Decimal('15.5'),
                                     spread_pct=Decimal('10'), source='seed')
        with mock.patch('staff_loans.rates.fetch_reference_rate', return_value=None):
            res = rates.refresh()
        self.assertEqual(res['status'], 'fetch_failed')
        self.assertEqual(rates.current_annual_rate(), Decimal('15.5'))
        self.assertFalse(StaffLoanRate.objects.filter(source='auto').exists())


class StaffLoanWorkflowTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct')

        # Open fiscal period covering today (JE posts dated today).
        t = date.today()
        cls.fiscal = FiscalPeriod.objects.create(
            period_name=f'SL-{t:%Y-%m}', start_date=date(t.year, 1, 1),
            end_date=date(t.year, 12, 31), status=FiscalPeriod.Status.OPEN,
            company=cls.company,
        )

        # ADIC-style chart of accounts the resolver should find by name.
        Account.objects.create(code='121010', name='Staff Loan',
                               account_type='asset', sub_type='current_asset', is_active=True)
        Account.objects.create(code='124004', name='Interest From Staff Loan',
                               account_type='revenue', sub_type='other_revenue', is_active=True)
        Account.objects.create(code='1110', name='FNB BWP operating account',
                               account_type='asset', sub_type='bank', is_bank_account=True, is_active=True)

        # Applicant (employee + linked user + monthly salary contract)
        cls.emp_user = User.objects.create_user('alice', email='alice@ad.co.bw', password='x')
        cls.emp = Employee.objects.create(
            employee_number='E001', full_name='Alice Molefe',
            company=cls.company, user=cls.emp_user, email='alice@ad.co.bw',
        )
        EmploymentContract.objects.create(
            employee=cls.emp, start_date=date(2025, 1, 1), end_date=None,
            basic=Decimal('8000.00'), frequency=EmploymentContract.Frequency.MONTHLY,
            status=EmploymentContract.Status.ACTIVE,
        )

        # CFO + HR approvers (separate people)
        cls.cfo_user = User.objects.create_user('cfo', email='cfo@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.cfo_user, title=UserProfile.Title.CFO, is_active=True)
        cls.hr_user = User.objects.create_user('hr', email='hr@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.hr_user, title=UserProfile.Title.HR_MANAGER, is_active=True)
        # CFO 15-Sep-2026: Finance releases the loan, HR is only notified. These
        # tests used to disburse as hr_user; that is now a REFUSAL, pinned in
        # FinanceReleasesTheLoanTest below.
        cls.fin_user = User.objects.create_user('finmgr', email='ktshutlhedi@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.fin_user,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)

    # ---- happy path: staff loan end to end ----
    def _submit_staff(self, amount='5000', term=4):
        app = svc.create_application(
            employee=self.emp, loan_type=StaffLoanApplication.LoanType.STAFF,
            amount_requested=Decimal(amount), term_months_requested=term,
            reason=MOTIVATION, user=self.emp_user, no_other_loans=True)
        svc.submit_application(app, self.emp_user)
        return app

    def test_full_staff_loan_flow_and_gl(self):
        app = self._submit_staff('5000', 4)
        self.assertEqual(app.status, StaffLoanApplication.Status.PENDING_CFO)
        self.assertEqual(app.monthly_salary_snapshot, Decimal('8000.00'))

        svc.cfo_decide(app, self.cfo_user, approve=True)  # rate defaults to 15.5
        self.assertEqual(app.status, StaffLoanApplication.Status.APPROVED)
        self.assertEqual(app.annual_rate_pct, policy.DEFAULT_ANNUAL_RATE_PCT)

        svc.sign_application(app, self.emp_user,
                             signature_data_url='data:image/png;base64,AAAA',
                             signatory_full_name='Alice Molefe')
        self.assertEqual(app.status, StaffLoanApplication.Status.SIGNED)

        app = svc.disburse(app, self.fin_user, disbursement_bank_code='1110',
                           disbursement_ref='PACOTO-001')
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)

        # EmployeeLoan created with interest capitalised, rate 0
        loan = app.employee_loan
        self.assertIsNotNone(loan)
        total = policy.total_repayable(Decimal('5000'), policy.DEFAULT_ANNUAL_RATE_PCT, 4)
        self.assertEqual(loan.principal, total)
        self.assertEqual(loan.outstanding, total)
        self.assertEqual(loan.annual_rate_pct, ZERO)
        self.assertEqual(loan.term_months, 4)

        # Issuance JE: posted + balanced + right accounts
        je = app.issuance_journal_entry
        self.assertIsNotNone(je)
        self.assertEqual(je.status, JournalEntry.Status.POSTED)
        lines = {ln.account.code: ln for ln in je.lines.all()}
        interest = policy.flat_interest(Decimal('5000'), policy.DEFAULT_ANNUAL_RATE_PCT, 4)
        self.assertEqual(lines['121010'].debit_bwp, total)          # receivable Dr full repayable
        self.assertEqual(lines['1110'].credit_bwp, Decimal('5000.00'))  # bank Cr cash out
        self.assertEqual(lines['124004'].credit_bwp, interest)       # interest income Cr
        self.assertEqual(sum(l.debit_bwp for l in je.lines.all()),
                         sum(l.credit_bwp for l in je.lines.all()))

        # LOAN_REPAYMENT component now points at the receivable
        from payroll.models import PayslipComponent
        comp = PayslipComponent.objects.get(code='LOAN_REPAYMENT')
        self.assertEqual(comp.posting_account_code, '121010')

    # ---- monthly deduction reuses the live engine ----
    def test_monthly_deduction_reduces_balance_to_zero(self):
        app = self._submit_staff('4000', 4)
        svc.cfo_decide(app, self.cfo_user, approve=True, annual_rate_pct=Decimal('0'))
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        app = svc.disburse(app, self.fin_user, disbursement_bank_code='1110')
        loan = app.employee_loan
        self.assertEqual(loan.outstanding, Decimal('4000.00'))

        from payroll.loan_service import apply_loan_repayments
        for i in range(4):
            period = PayrollPeriod.objects.create(
                period_name=f'2026-{7 + i:02d}', start_date=date(2026, 7 + i, 1),
                end_date=date(2026, 7 + i, 28), status=PayrollPeriod.Status.OPEN)
            apply_loan_repayments(period)
            loan.refresh_from_db()

        self.assertEqual(loan.outstanding, ZERO)
        self.assertEqual(loan.status, EmployeeLoan.Status.PAID)
        # a LOAN_REPAYMENT deduction line exists on the payslip
        line = PayslipLine.objects.filter(component__code='LOAN_REPAYMENT').first()
        self.assertIsNotNone(line)
        self.assertEqual(line.component.posting_account_code, '121010')

    # ---- caps enforced through the service ----
    def test_staff_loan_over_one_month_salary_rejected(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.STAFF,
                amount_requested=Decimal('9000'), term_months_requested=3,
                reason=MOTIVATION, user=self.emp_user, no_other_loans=True)

    def test_vehicle_over_40k_rejected(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.VEHICLE,
                amount_requested=Decimal('45000'), term_months_requested=12,
                reason=MOTIVATION, user=self.emp_user, blue_book_holder=policy.BLUE_BOOK_ALPHA,
                no_other_loans=True, purchased_via_veritas=True)

    def test_vehicle_requires_blue_book_holder(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.VEHICLE,
                amount_requested=Decimal('30000'), term_months_requested=12,
                reason=MOTIVATION, user=self.emp_user,
                no_other_loans=True, purchased_via_veritas=True)  # no blue_book_holder

    def test_motivation_under_50_words_rejected(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.STAFF,
                amount_requested=Decimal('3000'), term_months_requested=3,
                reason='Need money for fees', user=self.emp_user, no_other_loans=True)

    def test_no_other_loans_declaration_required(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.STAFF,
                amount_requested=Decimal('3000'), term_months_requested=3,
                reason=MOTIVATION, user=self.emp_user, no_other_loans=False)

    def test_vehicle_requires_veritas_purchase(self):
        with self.assertRaises(ValidationError):
            svc.create_application(
                employee=self.emp, loan_type=StaffLoanApplication.LoanType.VEHICLE,
                amount_requested=Decimal('30000'), term_months_requested=12,
                reason=MOTIVATION, user=self.emp_user, blue_book_holder=policy.BLUE_BOOK_VERITAS,
                no_other_loans=True, purchased_via_veritas=False)

    def test_six_month_staff_loan_allowed(self):
        app = self._submit_staff('3000', 6)  # 6 months now allowed
        self.assertEqual(app.status, StaffLoanApplication.Status.PENDING_CFO)

    # ---- segregation of duties ----
    def test_applicant_cannot_approve_own_loan(self):
        app = self._submit_staff('3000', 3)
        # give the applicant a CFO title — SoD must still block self-approval
        UserProfile.objects.create(user=self.emp_user, title=UserProfile.Title.CFO, is_active=True)
        with self.assertRaises(ValidationError):
            svc.cfo_decide(app, self.emp_user, approve=True)

    def test_non_cfo_cannot_approve(self):
        app = self._submit_staff('3000', 3)
        with self.assertRaises(ValidationError):
            svc.cfo_decide(app, self.hr_user, approve=True)  # HR is not the approver

    def test_non_hr_cannot_disburse(self):
        app = self._submit_staff('3000', 3)
        svc.cfo_decide(app, self.cfo_user, approve=True)
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        with self.assertRaises(ValidationError):
            svc.disburse(app, self.cfo_user, disbursement_bank_code='1110')  # CFO is not Finance

    # ---- vehicle disburse needs blue book in hand ----
    def test_vehicle_disburse_requires_blue_book_received(self):
        app = svc.create_application(
            employee=self.emp, loan_type=StaffLoanApplication.LoanType.VEHICLE,
            amount_requested=Decimal('30000'), term_months_requested=12,
            reason=MOTIVATION, user=self.emp_user, blue_book_holder=policy.BLUE_BOOK_ALPHA,
            vehicle_reg='B123ABC', no_other_loans=True, purchased_via_veritas=True)
        svc.submit_application(app, self.emp_user)
        svc.cfo_decide(app, self.cfo_user, approve=True)
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        with self.assertRaises(ValidationError):
            svc.disburse(app, self.fin_user, disbursement_bank_code='1110',
                         blue_book_received=False)
        # with the blue book confirmed, it goes through
        svc.disburse(app, self.fin_user, disbursement_bank_code='1110', blue_book_received=True)
        app.refresh_from_db()
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)
        self.assertTrue(app.blue_book_received)

    # ---- rate ceiling (fat-finger guard) ----
    def test_rate_ceiling_rejects_typo(self):
        app = self._submit_staff('3000', 3)
        with self.assertRaises(ValidationError):
            svc.cfo_decide(app, self.cfo_user, approve=True, annual_rate_pct=Decimal('155'))  # meant 15.5

    # ---- affordability: monthly repayment can't exceed one month salary ----
    def test_instalment_over_salary_rejected(self):
        # amount = one month salary, term 1 -> instalment = salary + interest > salary
        app = self._submit_staff('8000', 1)
        with self.assertRaises(ValidationError):
            svc.cfo_decide(app, self.cfo_user, approve=True)

    # ---- the CFO approval pre-fills the current (cron-set) scheme rate ----
    def test_cfo_uses_current_scheme_rate(self):
        StaffLoanRate.objects.create(effective_from=date(2026, 7, 1), annual_rate_pct=Decimal('16.0'),
                                     spread_pct=Decimal('10'), source='auto')
        app = self._submit_staff('3000', 3)
        svc.cfo_decide(app, self.cfo_user, approve=True)  # no rate passed → uses current 16%
        self.assertEqual(app.annual_rate_pct, Decimal('16.0'))

    # ---- SoD: the approver may not also disburse (even a superuser) ----
    def test_approver_cannot_disburse(self):
        root = User.objects.create_superuser('root', 'root@ad.co.bw', 'x')
        app = self._submit_staff('3000', 3)
        svc.cfo_decide(app, root, approve=True)             # superuser approves
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        with self.assertRaises(ValidationError):
            svc.disburse(app, root, disbursement_bank_code='1110')  # same person can't disburse

    # ---- double-disburse is blocked (idempotency) ----
    def test_cannot_disburse_twice(self):
        app = self._submit_staff('3000', 3)
        svc.cfo_decide(app, self.cfo_user, approve=True)
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        svc.disburse(app, self.fin_user, disbursement_bank_code='1110')
        with self.assertRaises(ValidationError):
            svc.disburse(app, self.fin_user, disbursement_bank_code='1110')  # already disbursed
        self.assertEqual(EmployeeLoan.objects.filter(employee=self.emp).count(), 1)

    # ---- non-BWP salary: staff loan refused (cap can't be checked) ----
    def test_non_bwp_salary_refused(self):
        Currency.objects.get_or_create(code='INR', defaults={'name': 'Indian Rupee', 'symbol': '₹'})
        u2 = User.objects.create_user('ravi', email='ravi@ad.co.bw', password='x')
        emp2 = Employee.objects.create(employee_number='E002', full_name='Ravi K',
                                       company=self.company, user=u2, email='ravi@ad.co.bw')
        EmploymentContract.objects.create(
            employee=emp2, start_date=date(2025, 1, 1), end_date=None, basic=Decimal('200000'),
            frequency=EmploymentContract.Frequency.MONTHLY, status=EmploymentContract.Status.ACTIVE,
            currency_code_id='INR')
        self.assertIsNone(svc.monthly_salary_for(emp2))
        with self.assertRaises(ValidationError):
            svc.create_application(employee=emp2, loan_type=StaffLoanApplication.LoanType.STAFF,
                                   amount_requested=Decimal('50000'), term_months_requested=3,
                                   reason=MOTIVATION, user=u2, no_other_loans=True)

    # ---- monthly deduction is idempotent per period ----
    def test_deduction_idempotent_per_period(self):
        app = self._submit_staff('4000', 4)
        svc.cfo_decide(app, self.cfo_user, approve=True, annual_rate_pct=Decimal('0'))
        svc.sign_application(app, self.emp_user, signature_data_url='data:image/png;base64,AA',
                             signatory_full_name='Alice Molefe')
        app = svc.disburse(app, self.fin_user, disbursement_bank_code='1110')
        loan = app.employee_loan
        from payroll.loan_service import apply_loan_repayments
        period = PayrollPeriod.objects.create(period_name='2026-07', start_date=date(2026, 7, 1),
                                              end_date=date(2026, 7, 28), status=PayrollPeriod.Status.OPEN)
        apply_loan_repayments(period)
        apply_loan_repayments(period)  # re-run same period — must be a no-op
        loan.refresh_from_db()
        self.assertEqual(loan.outstanding, Decimal('3000.00'))  # dropped once, not twice
        self.assertEqual(PayslipLine.objects.filter(component__code='LOAN_REPAYMENT').count(), 1)
