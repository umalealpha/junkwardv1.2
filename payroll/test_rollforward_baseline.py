"""payroll/test_rollforward_baseline.py — roll-forward must reproduce the
VALIDATED baseline for an unchanged employee, and a final settlement must
actually land (Legakwa, RSA July 2026).

Two defects fixed here (bugs 2026-07-23):

Defect 1 — an unchanged employee was silently re-taxed on roll-forward. A
salary-sacrifice HOUSING_DEDUCTION that the source import had already netted out
of gross got re-derived back INTO gross, inflating GROSS/PAYE/NET (Arjun:
86,700 → 99,700). abs()-normalisation did not fix it; the cause is a
gross/taxable-classification difference. Fix: untouched payslips keep the
baseline totals; only TOUCHED payslips recompute.

Defect 2 — a final settlement silently didn't apply: (a) a terminated employee
was excluded from resolution, (b) kind "other" logged a note instead of posting
the Severance / Leave Pay earning, (c) a rejected row returned no error line,
(d) a manual PAYE tax-override was clobbered by recompute.

Run in CI: manage.py test payroll.test_rollforward_baseline
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company
from payroll.amendment_views import apply_amendment_batch, _resolve_employee
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine, PayrollAmendment, PayrollAmendmentBatch)

D = Decimal


class RollForwardBaselineTests(TestCase):
    def setUp(self):
        K = PayslipComponent.Kind
        self.co = Company.objects.create(code='RSA', name='Risk Software Africa')
        self.basic = PayslipComponent.objects.create(code='BASIC', name='Basic', kind=K.EARNING)
        self.allow = PayslipComponent.objects.create(code='ALLOWANCE', name='Allowance', kind=K.EARNING)
        self.veh   = PayslipComponent.objects.create(code='VEHICLE_ALLOWANCE', name='Vehicle', kind=K.EARNING)
        self.house = PayslipComponent.objects.create(code='HOUSING_DEDUCTION', name='Housing Deduction', kind=K.EMPLOYEE_DEDUCTION)
        self.medee = PayslipComponent.objects.create(code='MEDICAL_AID_EE', name='Medical Aid EE', kind=K.EMPLOYEE_DEDUCTION)
        self.provee= PayslipComponent.objects.create(code='PROVIDENT_EE', name='Provident EE', kind=K.EMPLOYEE_PRETAX)
        self.paye  = PayslipComponent.objects.create(code='PAYE', name='PAYE', kind=K.TAX)
        self.sev   = PayslipComponent.objects.create(code='SEVERANCE', name='Severance Pay', kind=K.EARNING)
        self.leave = PayslipComponent.objects.create(code='LEAVE_PAY', name='Leave Pay', kind=K.EARNING)

        self.june = PayrollPeriod.objects.create(period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30')
        self.july = PayrollPeriod.objects.create(period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')

        # Arjun — validated June: gross is EARNINGS less the housing sacrifice.
        self.arjun = Employee.objects.create(employee_number='A1', full_name='Arjun Parameswaran',
                                             company=self.co, status='active')
        aj = Payslip.objects.create(employee=self.arjun, period=self.june, company=self.co,
                                    status=Payslip.Status.DRAFT,
                                    gross_amount=D('86700.00'), paye_amount=D('19512.50'),
                                    net_amount=D('61773.00'), ctc_amount=D('92736.50'))
        for c, a in [(self.basic, '62200'), (self.allow, '25000'), (self.veh, '12500'),
                     (self.house, '-13000'), (self.medee, '-1682.50'), (self.provee, '-3732')]:
            PayslipLine.objects.create(payslip=aj, component=c, amount=D(a))

        # Emily — leaver, final settlement in July.
        self.emily = Employee.objects.create(employee_number='E1', full_name='Emily Chilongo',
                                             company=self.co, status='terminated')
        em = Payslip.objects.create(employee=self.emily, period=self.june, company=self.co,
                                    status=Payslip.Status.DRAFT,
                                    gross_amount=D('5000.00'), paye_amount=D('50.00'),
                                    net_amount=D('4950.00'), ctc_amount=D('5000.00'))
        PayslipLine.objects.create(payslip=em, component=self.basic, amount=D('5000'))

        self.applier = get_user_model().objects.create_superuser(
            username='fc', email='fc@alphadirect.co.bw', password='x')

    def _apply(self, amendments):
        """Create a June→July batch with the given amendment rows and apply it.
        `amendments` = list of dicts {employee, kind, component, amount, reason}."""
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.july, baseline_period=self.june, company=self.co,
            file_name='test', status=PayrollAmendmentBatch.Status.PARSED, row_count=len(amendments))
        for r in amendments:
            PayrollAmendment.objects.create(
                batch=batch, employee=r['employee'], employee_ref=r['employee'].full_name,
                kind=r['kind'], component=r.get('component'), amount=D(str(r['amount'])),
                reason=r.get('reason', ''))
        req = APIRequestFactory().post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=self.applier)
        resp = apply_amendment_batch(req, batch_id=str(batch.id))
        assert resp.status_code == 200, resp.data
        return resp.data

    def test_untouched_employee_keeps_validated_baseline(self):
        # Emily has a settlement; Arjun has NO amendment → July must equal June.
        self._apply([
            {'employee': self.emily, 'kind': 'salary_change', 'component': self.basic, 'amount': '4062.50'},
            {'employee': self.emily, 'kind': 'other', 'component': self.sev,   'amount': '4375.00', 'reason': 'Severance Pay'},
            {'employee': self.emily, 'kind': 'other', 'component': self.leave, 'amount': '1145.83', 'reason': 'Leave Pay'},
            {'employee': self.emily, 'kind': 'tax_override', 'component': self.paye, 'amount': '199.48'},
            {'employee': self.emily, 'kind': 'terminate', 'amount': '0'},
        ])
        aj = Payslip.objects.get(employee=self.arjun, period=self.july)
        self.assertEqual(aj.gross_amount, D('86700.00'))   # NOT 99700
        self.assertEqual(aj.paye_amount,  D('19512.50'))   # NOT 21829.50
        self.assertEqual(aj.net_amount,   D('61773.00'))   # NOT 59456
        self.assertEqual(aj.ctc_amount,   D('92736.50'))

    def test_final_settlement_lands_with_override(self):
        self._apply([
            {'employee': self.emily, 'kind': 'salary_change', 'component': self.basic, 'amount': '4062.50'},
            {'employee': self.emily, 'kind': 'other', 'component': self.sev,   'amount': '4375.00', 'reason': 'Severance Pay'},
            {'employee': self.emily, 'kind': 'other', 'component': self.leave, 'amount': '1145.83', 'reason': 'Leave Pay'},
            {'employee': self.emily, 'kind': 'tax_override', 'component': self.paye, 'amount': '199.48'},
            {'employee': self.emily, 'kind': 'terminate', 'amount': '0'},
        ])
        em = Payslip.objects.get(employee=self.emily, period=self.july)
        self.assertEqual(em.gross_amount, D('9583.33'))    # 4062.50 + 4375 + 1145.83
        self.assertEqual(em.paye_amount,  D('199.48'))     # override survived recompute
        self.assertEqual(em.net_amount,   D('9383.85'))
        self.assertEqual(em.status, Payslip.Status.CANCELLED)

    def test_terminated_employee_resolves(self):
        emp, note = _resolve_employee('Emily Chilongo', self.co)
        self.assertIsNotNone(emp)
        self.assertEqual(note, '')

    def test_settled_leaver_does_not_roll_into_next_period(self):
        # July: Emily is settled (creates her July slip). She is terminated.
        self._apply([
            {'employee': self.emily, 'kind': 'salary_change', 'component': self.basic, 'amount': '4062.50'},
            {'employee': self.emily, 'kind': 'other', 'component': self.sev,   'amount': '4375.00'},
            {'employee': self.emily, 'kind': 'tax_override', 'component': self.paye, 'amount': '199.48'},
        ])
        # Mirror the live repair: her settlement slip stays DRAFT (payable) — the
        # exclusion must rest on the employee being a leaver, not on slip status.
        Payslip.objects.filter(employee=self.emily, period=self.july).update(status=Payslip.Status.DRAFT)
        # Roll July -> August with a batch that does NOT amend Emily.
        aug = PayrollPeriod.objects.create(period_name='2026-08', start_date='2026-08-01', end_date='2026-08-31')
        batch = PayrollAmendmentBatch.objects.create(
            target_period=aug, baseline_period=self.july, company=self.co,
            file_name='t', status=PayrollAmendmentBatch.Status.PARSED, row_count=0)
        req = APIRequestFactory().post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=self.applier)
        resp = apply_amendment_batch(req, batch_id=str(batch.id))
        self.assertEqual(resp.status_code, 200, resp.data)
        # The leaver must NOT reappear as a payable August draft; active Arjun does.
        self.assertFalse(Payslip.objects.filter(employee=self.emily, period=aug).exists())
        self.assertTrue(Payslip.objects.filter(employee=self.arjun, period=aug).exists())

    def test_rejected_row_surfaces_an_error(self):
        # deduction_remove with no component → cannot apply → must NOT be silent.
        data = self._apply([
            {'employee': self.arjun, 'kind': 'deduction_remove', 'component': None, 'amount': '0'},
        ])
        self.assertEqual(data['amendments_failed'], 1)
        self.assertTrue(any('Arjun' in e for e in data['errors']), data['errors'])

    def test_foreign_currency_survives_roll_forward(self):
        """ADRisk (INR) slips rolled June→July came out as BWP: the copy carried
        source_* figures and the fx rate but not source_currency, so July printed
        INR amounts as a broken pula slip (CFO 2026-09-18)."""
        inr = Employee.objects.create(employee_number='ADRG_1', full_name='India Staff',
                                      company=self.co, status='active')
        Payslip.objects.create(employee=inr, period=self.june, company=self.co,
                               status=Payslip.Status.DRAFT, source_currency='INR',
                               fx_rate_to_bwp=D('0.14285714'),
                               source_gross=D('70000.00'), source_paye=D('7000.00'),
                               source_net=D('63000.00'),
                               gross_amount=D('10000.00'), paye_amount=D('1000.00'),
                               net_amount=D('9000.00'), ctc_amount=D('10000.00'))
        self._apply([])
        july = Payslip.objects.get(employee=inr, period=self.july)
        self.assertEqual(july.source_currency, 'INR')
        self.assertEqual(july.source_gross, D('70000.00'))


class RollForwardOneOffTests(TestCase):
    """A ONE-OFF earning must never be re-paid the following month.

    Bug proven 2026-09-20: step 1 of apply_amendment_batch copied EVERY
    baseline payslip line into the target period and excluded exactly one
    component code, LOAN_REPAYMENT. An employee paid COMMISSION 7,250 +
    INCENTIVE 3,000 + LEAVE_PAY 9,500 + SEVERANCE 45,000 in August had all
    four copied into September — P64,750 of duplicate pay for ONE person,
    from an EMPTY amendment batch, HTTP 200, errors: []. The baseline
    gross was carried verbatim too, so the September register reconciled to
    the inflated figure and looked right.

    The components here are seeded by the REAL management command, not
    hand-built, so this asserts the SHIPPED classification of each code
    rather than a flag the test set for itself.
    """

    def setUp(self):
        from django.core.management import call_command
        call_command('setup_payroll_components')
        self.co = Company.objects.create(code='ONEOFF', name='One-off test co')
        self.aug = PayrollPeriod.objects.create(period_name='2026-08',
                                                start_date='2026-08-01', end_date='2026-08-31')
        self.sep = PayrollPeriod.objects.create(period_name='2026-09',
                                                start_date='2026-09-01', end_date='2026-09-30')
        self.emp = Employee.objects.create(employee_number='OO1', full_name='One Off Tester',
                                           company=self.co, status='active')
        comp = {c.code: c for c in PayslipComponent.objects.all()}
        aug_slip = Payslip.objects.create(
            employee=self.emp, period=self.aug, company=self.co,
            status=Payslip.Status.DRAFT,
            gross_amount=D('84750.00'), paye_amount=D('0.00'),
            net_amount=D('84750.00'), ctc_amount=D('84750.00'))
        for code, amt in [('BASIC', '20000'), ('VEHICLE_ALLOWANCE', '0'),
                          ('COMMISSION', '7250'), ('INCENTIVE', '3000'),
                          ('LEAVE_PAY', '9500'), ('SEVERANCE', '45000')]:
            if amt == '0':
                continue
            PayslipLine.objects.create(payslip=aug_slip, component=comp[code], amount=D(amt))
        self.applier = get_user_model().objects.create_superuser(
            username='fc_oneoff', email='fc.oneoff@alphadirect.co.bw', password='x')

    def _roll_empty_batch(self):
        """August -> September with an EMPTY amendment batch (the exact shape
        that produced the duplicate pay on prod)."""
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.sep, baseline_period=self.aug, company=self.co,
            file_name='empty', status=PayrollAmendmentBatch.Status.PARSED, row_count=0)
        req = APIRequestFactory().post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=self.applier)
        resp = apply_amendment_batch(req, batch_id=str(batch.id))
        self.assertEqual(resp.status_code, 200, resp.data)
        return Payslip.objects.get(employee=self.emp, period=self.sep)

    def test_one_off_earnings_are_not_re_paid_next_month(self):
        sep = self._roll_empty_batch()
        carried = {ln.component.code: ln.amount
                   for ln in sep.lines.select_related('component')}
        for code in ('COMMISSION', 'INCENTIVE', 'LEAVE_PAY', 'SEVERANCE'):
            self.assertNotIn(code, carried, f'{code} was re-paid in September')

    def test_recurring_pay_still_rolls_forward(self):
        # The guard must not throw the baby out with the bathwater.
        sep = self._roll_empty_batch()
        self.assertEqual(sep.lines.get(component__code='BASIC').amount, D('20000.00'))

    def test_gross_matches_the_lines_actually_carried(self):
        sep = self._roll_empty_batch()
        earnings = sum((ln.amount for ln in sep.lines.select_related('component')
                        if ln.component.kind == PayslipComponent.Kind.EARNING),
                       D('0.00'))
        self.assertEqual(earnings, D('20000.00'))
        # The register must reconcile to the lines that were ACTUALLY carried —
        # a verbatim 84,750 would make the duplicate pay look correct.
        self.assertEqual(sep.gross_amount, earnings)
