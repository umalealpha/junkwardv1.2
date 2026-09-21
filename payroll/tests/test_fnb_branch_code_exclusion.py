"""One bad branch code must cost ONE person's payment, never the whole run.

THE TRAP. `fnb.payments.build_batch_payload` raises when a creditor branch code
is unusable. For a payment request that is safe — each payment is its own
batch, so a bad line fails alone. Payroll is the opposite shape: it builds ONE
batch for EVERY employee (`load_period_to_fnb` -> a single `submit_eft_batch`),
so one mistyped branch code would have refused the entire salary run.

Found by Fable 5.1 on 17-Sep-2026 reviewing the FNB reject controls, and filed
as checklist class L66: *a per-line rule added to a shared BATCH builder refuses
the whole batch for every caller*.

WHY THIS FILE EXISTS RATHER THAN THE STRUCTURAL ONE. I first wrote only an
ast-parsed check and said an end-to-end test was impossible because payroll had
no period fixture. That was wrong — Fable pointed at
`payroll/tests/test_advance_mismatch.py`, which builds a PayrollPeriod, a
Company and an Employee in a few lines. These tests drive the real
`build_period_preview` against real rows, which is the only way to prove the
good employees still get paid.

'64967' is a real value off production: 064967 with the leading zero eaten by
Excel, the exact trap payroll/bank_codes already warns about.
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.fnb_disbursement import build_period_preview
from payroll.models import Employee, PayrollPeriod, Payslip


class OneBadBranchCodeDoesNotStopTheRun(TestCase):

    @classmethod
    def setUpTestData(cls):
        # The loader is hard-scoped to ADIC, so the company code must be real.
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-09', start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))

    def _employee(self, number, name, *, branch, bank='FNB Botswana',
                  account='62812345678'):
        return Employee.objects.create(
            employee_number=number, full_name=name, company=self.adic,
            hire_date=datetime.date(2024, 1, 1),
            bank_name=bank, bank_account_no=account, bank_branch_code=branch)

    def _payslip(self, emp, net='9000.00'):
        return Payslip.objects.create(
            employee=emp, period=self.period, company=self.adic,
            status=Payslip.Status.APPROVED, net_amount=Decimal(net),
            gross_amount=Decimal(net))

    def test_the_good_employees_are_still_paid(self):
        # 🔴 THE POINT. Before the fix, Thabo's '64967' would have raised inside
        # build_batch_payload and refused the batch that carries all three.
        ok1 = self._employee('E1', 'Alice A', branch='064967')
        bad = self._employee('E2', 'Thabo B', branch='64967')
        ok2 = self._employee('E3', 'Chris C', branch='287867')
        for e in (ok1, bad, ok2):
            self._payslip(e)

        pre = build_period_preview(self.period)
        included = {r['name'] for r in pre.included}
        self.assertEqual(included, {'Alice A', 'Chris C'},
                         'two good salaries must still load')
        self.assertEqual(pre.count, 2)
        self.assertEqual(pre.total, Decimal('18000.00'))

    def test_the_one_bad_employee_is_named_with_a_reason(self):
        # A silent drop is a person who quietly does not get paid.
        self._payslip(self._employee('E4', 'Thabo B', branch='64967'))
        pre = build_period_preview(self.period)
        self.assertEqual(pre.count, 0)
        reasons = {r['name']: r['reason'] for r in pre.excluded}
        self.assertIn('Thabo B', reasons)
        self.assertIn('branch code', reasons['Thabo B'])
        self.assertIn('064967', reasons['Thabo B'],
                      'it must say what the code probably should be')

    def test_a_blank_branch_on_a_non_fnb_bank_is_excluded(self):
        self._payslip(self._employee('E5', 'Dineo D', branch='',
                                     bank='Stanbic Bank'))
        pre = build_period_preview(self.period)
        self.assertEqual(pre.count, 0)
        self.assertTrue(any('branch' in r['reason'] for r in pre.excluded))

    def test_an_account_number_in_the_branch_box_is_excluded(self):
        self._payslip(self._employee('E6', 'Eric E', branch='60000000000'))
        pre = build_period_preview(self.period)
        self.assertEqual(pre.count, 0)

    def test_a_clean_run_excludes_nobody(self):
        # The regression that matters most: this must not start refusing
        # perfectly good salaries.
        for i, branch in enumerate(('064967', '287867', '060167', '291467'), 1):
            self._payslip(self._employee(f'F{i}', f'Person {i}', branch=branch))
        pre = build_period_preview(self.period)
        self.assertEqual(pre.count, 4)
        self.assertEqual(pre.excluded, [])

    def test_the_submit_path_filters_exactly_as_the_preview_did(self):
        # If these two ever disagree, a payee the CFO was shown as excluded
        # still reaches the bank — or a good one silently vanishes.
        from payroll.fnb_disbursement import _payees_from_preview
        self._payslip(self._employee('G1', 'Alice A', branch='064967'))
        self._payslip(self._employee('G2', 'Thabo B', branch='64967'))

        pre = build_period_preview(self.period)
        payees = _payees_from_preview(self.period, self.adic, None)
        self.assertEqual({p['name'] for p in pre.included},
                         {p.payee_name for p in payees})
        self.assertEqual(len(payees), 1)
