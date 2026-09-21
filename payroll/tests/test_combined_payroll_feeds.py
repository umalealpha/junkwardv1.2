"""Applying an amendment batch must not silently delete this month's loan
deduction (Omni bug 59c9fc5b-9b3d-4a5a-a546-5acab10613a6, 16-Sep-2026).

Why 89/89 passed while the payroll was still wrong: every suite tested its own
step. Nothing anywhere put a LOAN_REPAYMENT line and an amendment batch on the
SAME payslip, which is the only place the two steps meet.

The defect, in order:
  1. the loan step writes the LOAN_REPAYMENT line AND a LoanRepayment row, and
     reduces the loan's outstanding balance;
  2. applying an AUTO-INCENTIVE / AUTO-COMMISSION batch rebuilds the payslip
     from the baseline, which deletes every line and deliberately does not copy
     LOAN_REPAYMENT back;
  3. a rerun of the loan step sees the LoanRepayment ROW, decides the month is
     already done, and skips - so the deduction is never restored.

Net pay is then overstated by the instalment while the loan balance has already
come down: the employee is overpaid AND their loan reads as part-repaid on cash
that never left.

Run: manage.py test payroll.tests.test_combined_payroll_feeds
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from payroll.contract_models import EmployeeLoan, LoanRepayment
from payroll.loan_service import apply_loan_repayments
from payroll.models import (Employee, Payslip, PayslipComponent, PayslipLine,
                            PayrollAmendment, PayrollAmendmentBatch, PayrollPeriod)

BASIC = Decimal("5000.00")
INCENTIVE = Decimal("300.00")
COMMISSION = Decimal("400.00")
INSTALMENT = Decimal("100.00")


class CombinedFeedLoanPreservationTests(APITestCase):
    """One employee, the exact figures from the bug report."""

    def setUp(self):
        self.fc = User.objects.create_user("pkago2", email="pkago@alphadirect.co.bw")
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER,
                                   is_active=True)
        self.co = Company.objects.create(code="CMB", name="Combined Feed Co")
        self.base = PayrollPeriod.objects.create(
            period_name="2026-05", start_date=datetime.date(2026, 5, 1),
            end_date=datetime.date(2026, 5, 31))
        self.target = PayrollPeriod.objects.create(
            period_name="2026-06", start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 6, 30))

        self.basic = PayslipComponent.objects.get_or_create(
            code="BASIC", defaults={"name": "Basic Salary",
                                    "kind": PayslipComponent.Kind.EARNING,
                                    "is_taxable": True, "sort_order": 1})[0]
        self.incentive = PayslipComponent.objects.get_or_create(
            code="INCENTIVE", defaults={"name": "Incentive",
                                        "kind": PayslipComponent.Kind.EARNING,
                                        "is_taxable": True, "sort_order": 3})[0]
        self.commission = PayslipComponent.objects.get_or_create(
            code="COMMISSION", defaults={"name": "Commission",
                                         "kind": PayslipComponent.Kind.EARNING,
                                         "is_taxable": True, "sort_order": 4})[0]

        self.emp = self._employee("CMB1", "Borrower With Incentive")
        self.loan = self._loan(self.emp)

    # ---------------------------------------------------------------- helpers
    def _employee(self, number, name):
        emp = Employee.objects.create(employee_number=number, full_name=name,
                                      company=self.co)
        ps = Payslip.objects.create(employee=emp, period=self.base, company=self.co,
                                    status=Payslip.Status.DRAFT)
        PayslipLine.objects.create(payslip=ps, component=self.basic, amount=BASIC)
        ps.recompute_totals(recompute_paye=False)
        ps.save(update_fields=["gross_amount", "paye_amount", "net_amount",
                               "ctc_amount"])
        return emp

    def _loan(self, emp):
        """Principal 1200 over 12 months -> a 100.00 instalment."""
        return EmployeeLoan.objects.create(
            employee=emp, principal=Decimal("1200.00"),
            annual_rate_pct=Decimal("0"), term_months=12,
            status=EmployeeLoan.Status.ACTIVE, outstanding=Decimal("1200.00"))

    def _batch(self, employee, component, amount, marker):
        b = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base, company=self.co,
            file_name=marker, uploaded_by=self.fc,
            status=PayrollAmendmentBatch.Status.PARSED)
        PayrollAmendment.objects.create(
            batch=b, employee=employee, kind=PayrollAmendment.Kind.ALLOWANCE_ADD,
            component=component, amount=amount,
            employee_ref=employee.employee_number)
        return b

    def _apply(self, batch):
        self.client.force_authenticate(self.fc)
        r = self.client.post(reverse("v1-payroll-amendments-apply", args=[batch.id]))
        self.assertEqual(r.status_code, 200, r.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.APPLIED)

    def _line(self, emp, component):
        ps = Payslip.objects.filter(employee=emp, period=self.target).first()
        if ps is None:
            return None
        ln = PayslipLine.objects.filter(payslip=ps, component=component).first()
        return ln.amount if ln else None

    def _loan_line(self, emp):
        ps = Payslip.objects.filter(employee=emp, period=self.target).first()
        if ps is None:
            return None
        ln = PayslipLine.objects.filter(
            payslip=ps, component__code="LOAN_REPAYMENT").first()
        return ln.amount if ln else None

    def _assert_all_four_survive(self, emp):
        self.assertEqual(self._line(emp, self.basic), BASIC, "BASIC lost")
        self.assertEqual(self._line(emp, self.incentive), INCENTIVE, "INCENTIVE lost")
        self.assertEqual(self._line(emp, self.commission), COMMISSION, "COMMISSION lost")
        self.assertEqual(
            self._loan_line(emp), INSTALMENT,
            "THE BUG: this month's loan deduction was deleted by the rebuild "
            "and never restored. Net pay is overstated by the instalment while "
            "the loan balance has already been reduced.")

    # ------------------------------------------------------------------ tests
    def test_incentive_then_loan_then_commission_keeps_all_four(self):
        self._apply(self._batch(self.emp, self.incentive, INCENTIVE, "AUTO-INCENTIVE"))
        apply_loan_repayments(self.target)
        self.assertEqual(self._loan_line(self.emp), INSTALMENT,
                         "the loan step itself did not write the line")
        self._apply(self._batch(self.emp, self.commission, COMMISSION, "AUTO-COMMISSION"))
        self._assert_all_four_survive(self.emp)

    def test_commission_then_loan_then_incentive_keeps_all_four(self):
        """The reverse order must behave identically."""
        self._apply(self._batch(self.emp, self.commission, COMMISSION, "AUTO-COMMISSION"))
        apply_loan_repayments(self.target)
        self._apply(self._batch(self.emp, self.incentive, INCENTIVE, "AUTO-INCENTIVE"))
        self._assert_all_four_survive(self.emp)

    def test_net_pay_is_not_overstated_after_the_second_batch(self):
        """The money statement of the same bug, asserted on the stored total."""
        self._apply(self._batch(self.emp, self.incentive, INCENTIVE, "AUTO-INCENTIVE"))
        apply_loan_repayments(self.target)
        self._apply(self._batch(self.emp, self.commission, COMMISSION, "AUTO-COMMISSION"))
        ps = Payslip.objects.get(employee=self.emp, period=self.target)
        self.assertEqual(ps.gross_amount, BASIC + INCENTIVE + COMMISSION)
        self.assertEqual(
            ps.net_amount,
            BASIC + INCENTIVE + COMMISSION - INSTALMENT - ps.paye_amount,
            "stored net does not carry the loan deduction")

    def test_the_balance_is_reduced_once_not_twice(self):
        self._apply(self._batch(self.emp, self.incentive, INCENTIVE, "AUTO-INCENTIVE"))
        apply_loan_repayments(self.target)
        self._apply(self._batch(self.emp, self.commission, COMMISSION, "AUTO-COMMISSION"))
        apply_loan_repayments(self.target)          # the orchestrator reruns
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.outstanding, Decimal("1100.00"),
                         "the instalment was taken off the balance twice")
        self.assertEqual(
            LoanRepayment.objects.filter(loan=self.loan, period=self.target).count(), 1,
            "a second LoanRepayment row was written for the same period")
        self.assertEqual(self._loan_line(self.emp), INSTALMENT)

    def test_a_missing_line_is_repaired_without_charging_again(self):
        """Row kept, line deleted by hand - the rerun must REPAIR, not skip."""
        apply_loan_repayments(self.target)
        ps = Payslip.objects.get(employee=self.emp, period=self.target)
        PayslipLine.objects.filter(
            payslip=ps, component__code="LOAN_REPAYMENT").delete()
        self.assertIsNone(self._loan_line(self.emp))

        apply_loan_repayments(self.target)
        self.assertEqual(self._loan_line(self.emp), INSTALMENT,
                         "the rerun skipped on the LoanRepayment row instead of "
                         "repairing the missing payslip line")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.outstanding, Decimal("1100.00"),
                         "repairing the line charged the balance a second time")

    def _deduction_batch(self, employee, component, amount, marker):
        """A DEDUCTION_ADD batch - the shape salary-advance recovery uses."""
        b = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base, company=self.co,
            file_name=marker, uploaded_by=self.fc,
            status=PayrollAmendmentBatch.Status.PARSED)
        PayrollAmendment.objects.create(
            batch=b, employee=employee,
            kind=PayrollAmendment.Kind.DEDUCTION_ADD,
            component=component, amount=amount,
            employee_ref=employee.employee_number)
        return b

    def test_a_salary_advance_recovery_is_never_overwritten(self):
        """Advance recovery posts to the SAME LOAN_REPAYMENT line as the loan.

        A reconciler that forces that line to the loan ledger makes the two
        engines flip-flop - whichever ran last wins, silently, every month.
        Finance would apply a P500 advance recovery and the next loan run would
        quietly put it back to P100. A reconciler may CREATE what is missing; it
        may never REWRITE what another writer set.
        """
        apply_loan_repayments(self.target)
        self.assertEqual(self._loan_line(self.emp), INSTALMENT)

        loan_comp = PayslipComponent.objects.get(code="LOAN_REPAYMENT")
        self._apply(self._deduction_batch(
            self.emp, loan_comp, Decimal("500.00"), "ADVANCE-RECOVERY"))
        self.assertEqual(self._loan_line(self.emp), Decimal("500.00"),
                         "the advance recovery did not land")

        summary = apply_loan_repayments(self.target)
        self.assertEqual(
            self._loan_line(self.emp), Decimal("500.00"),
            "the reconciler overwrote the advance recovery with the loan figure")
        self.assertEqual(summary.get("mismatched"), 1,
                         "the mismatch must be reported, not silently corrected")
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.outstanding, Decimal("1100.00"),
                         "the balance was charged again")

    def test_a_manual_finance_figure_is_never_overwritten(self):
        """Finance keys a bigger deduction because the employee asked to pay
        extra. The reconciler must report it, not undo it."""
        apply_loan_repayments(self.target)
        ps = Payslip.objects.get(employee=self.emp, period=self.target)
        line = PayslipLine.objects.get(payslip=ps, component__code="LOAN_REPAYMENT")
        line.amount = Decimal("250.00")
        line.save(update_fields=["amount"])

        summary = apply_loan_repayments(self.target)
        self.assertEqual(self._loan_line(self.emp), Decimal("250.00"),
                         "a deliberate human figure was overwritten")
        self.assertEqual(summary.get("mismatched"), 1)

    def test_a_later_unrelated_batch_does_not_take_the_loan_line(self):
        apply_loan_repayments(self.target)
        fuel = PayslipComponent.objects.get_or_create(
            code="FUEL", defaults={"name": "Fuel allowance",
                                   "kind": PayslipComponent.Kind.EARNING,
                                   "is_taxable": True, "sort_order": 2})[0]
        self._apply(self._batch(self.emp, fuel, Decimal("500.00"), "MANUAL"))
        self.assertEqual(self._loan_line(self.emp), INSTALMENT)


class BystanderLoanTests(CombinedFeedLoanPreservationTests):
    """The quiet half of the defect, which the bug report does not mention.

    The rebuild deletes the lines of EVERY payslip in the target period, not
    only the employees named in the batch, and the recompute step then skips
    anyone who had no amendment. So a colleague who gets no incentive at all
    loses this month's loan deduction to somebody else's batch - and because
    their stored totals are copied straight from the baseline, nothing on the
    payslip shows that anything is missing.
    """

    def setUp(self):
        super().setUp()
        self.bystander = self._employee("CMB2", "Borrower With No Incentive")
        self.bystander_loan = self._loan(self.bystander)

    def test_a_colleague_batch_does_not_take_an_untouched_loan_line(self):
        apply_loan_repayments(self.target)
        self.assertEqual(self._loan_line(self.bystander), INSTALMENT)
        self._apply(self._batch(self.emp, self.incentive, INCENTIVE, "AUTO-INCENTIVE"))
        self.assertEqual(
            self._loan_line(self.bystander), INSTALMENT,
            "an employee with no amendment lost their loan deduction to "
            "another employee's batch")
