"""payroll/test_report_reflects_adjustments.py — an applied adjustment MUST
flow through to the payroll report (Pako Kago, FC, 2026-07-23).

Bug: adjustments (commission, incentive) applied to a period did not update the
payroll report. Root cause was upstream — a commission uploaded as kind "other"
was logged as a note and never posted a payslip line, so the report (which reads
the payslip lines + stored gross) had nothing to show. Fixed in #463: kind
"other" with a resolved component now posts the earning line and the employee is
recomputed. This test pins Pako's end-to-end acceptance check so it can never
regress: apply a commission of a known amount, then confirm the report row shows
(a) the commission as its own component column, (b) gross up by that amount, and
(c) the grand total equals the sum of payslips.

Run in CI: manage.py test payroll.test_report_reflects_adjustments
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Company
from payroll.amendment_views import (apply_amendment_batch, _write_period_sheet,
                                      _scoped_payslips)
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine, PayrollAmendment, PayrollAmendmentBatch)

D = Decimal


class ReportReflectsAdjustmentsTests(TestCase):
    def setUp(self):
        K = PayslipComponent.Kind
        self.co = Company.objects.create(code='RSA', name='Risk Software Africa')
        self.basic = PayslipComponent.objects.create(code='BASIC', name='Basic', kind=K.EARNING, sort_order=10)
        self.comm  = PayslipComponent.objects.create(code='COMMISSION', name='Commission', kind=K.EARNING, sort_order=20)
        self.paye  = PayslipComponent.objects.create(code='PAYE', name='PAYE', kind=K.TAX, sort_order=900)
        self.june = PayrollPeriod.objects.create(period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30')
        self.july = PayrollPeriod.objects.create(period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')
        self.emp = Employee.objects.create(employee_number='C1', full_name='Commission Person',
                                           company=self.co, status='active')
        jn = Payslip.objects.create(employee=self.emp, period=self.june, company=self.co,
                                    status=Payslip.Status.DRAFT, gross_amount=D('10000.00'),
                                    paye_amount=D('0.00'), net_amount=D('10000.00'), ctc_amount=D('10000.00'))
        PayslipLine.objects.create(payslip=jn, component=self.basic, amount=D('10000.00'))
        self.applier = get_user_model().objects.create_superuser(
            username='fc', email='fc@alphadirect.co.bw', password='x')

    def _sheet_rows(self, period, user):
        """Render the export sheet and return list-of-dicts keyed by header."""
        wb = Workbook(); ws = wb.active
        _write_period_sheet(ws, period, _scoped_payslips(period, user))
        data = [[c.value for c in row] for row in ws.iter_rows()]
        header = data[0]
        out = []
        for r in data[1:]:
            if not any(v is not None for v in r):
                continue
            out.append(dict(zip(header, r)))
        return header, out

    def _apply_commission(self, amount):
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.july, baseline_period=self.june, company=self.co,
            file_name='t', status=PayrollAmendmentBatch.Status.PARSED, row_count=1)
        # The exact path Pako hit: a commission uploaded as kind "other".
        PayrollAmendment.objects.create(
            batch=batch, employee=self.emp, employee_ref=self.emp.full_name,
            kind='other', component=self.comm, amount=D(str(amount)), reason='Commission')
        req = APIRequestFactory().post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=self.applier)
        resp = apply_amendment_batch(req, batch_id=str(batch.id))
        assert resp.status_code == 200, resp.data
        return resp.data

    def test_commission_flows_into_report_and_totals_tie(self):
        # Apply a commission of a KNOWN amount (2,500) as kind "other" — the
        # exact path that used to be a no-op note. The apply also rolls the
        # June baseline (10,000) into July first.
        self._apply_commission('2500.00')

        ps = Payslip.objects.get(employee=self.emp, period=self.july)
        # (a) the commission actually landed on the payslip record, not just the batch
        self.assertTrue(ps.lines.filter(component__code='COMMISSION', amount=D('2500.00')).exists())
        # (b) gross/net rose by exactly the commission
        self.assertEqual(ps.gross_amount, D('12500.00'))
        self.assertEqual(ps.net_amount,   D('12500.00'))  # no PAYE bracket seeded → net = gross

        # (c) the REPORT row shows the commission column and the raised gross,
        #     and the grand total equals the sum of payslips.
        header, rows = self._sheet_rows(self.july, self.applier)
        self.assertIn('Commission', header)
        emp_row = next(r for r in rows if r.get('Employee') == 'Commission Person')
        self.assertEqual(emp_row['Commission'], 2500.0)
        self.assertEqual(emp_row['GROSS'], 12500.0)
        total_row = next(r for r in rows if str(r.get('Employee', '')).startswith('TOTAL'))
        self.assertEqual(total_row['GROSS'], 12500.0)   # one payslip → total == that gross

    def test_deactivated_component_with_a_line_is_not_dropped_from_report(self):
        # A commission lands, then the component is later deactivated. The report
        # must still show the column (never silently exclude an adjustment while
        # its amount stays in gross) — FC Pako's "components list" concern.
        self._apply_commission('2500.00')
        self.comm.is_active = False
        self.comm.save(update_fields=['is_active'])
        header, rows = self._sheet_rows(self.july, self.applier)
        self.assertIn('Commission', header)
        emp_row = next(r for r in rows if r.get('Employee') == 'Commission Person')
        self.assertEqual(emp_row['Commission'], 2500.0)
        self.assertEqual(emp_row['GROSS'], 12500.0)
