"""
Guards for the payslip breakdown + PDF, written from the CFO audit of the live
July-2026 run (2026-07-25) where 68 of 102 printed payslips did not add up.

Each test below is one of the real defects. If any of these ever fails again,
staff are being handed a payslip they cannot reconcile.

Figures here are SYNTHETIC. The fixture mirrors the shape of the awkward real
case (a negative earning, post-tax and pre-tax deductions, employer
contributions, PAYE) without putting any employee's actual pay in the repo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            PayslipLine)
from payroll.payslip_breakdown import build_breakdown
from payroll.pdf import generate_payslip_pdf, _money, _bracket

D = Decimal


def _pdf_text(pdf: bytes) -> str:
    """Text actually visible on a rendered payslip.

    Asserting on the PDF itself (not on module source) is what makes a
    "this must not print" test real — the label could be removed from one code
    path and still reach the page from another. pdfplumber is already a
    dependency (core/smart_upload/extractors.py reads PDF uploads with it).
    """
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return '\n'.join((p.extract_text() or '') for p in doc.pages)


def _comp(code, name, kind, order):
    c, _ = PayslipComponent.objects.get_or_create(
        code=code, defaults={'name': name, 'kind': kind, 'sort_order': order})
    return c


class PayslipBreakdownTests(TestCase):
    """One slip carrying every awkward case: a negative earning (housing salary
    sacrifice), post-tax deductions, a pre-tax provident deduction, employer
    contributions, and PAYE.

    gross = 60,000 + 10,000 − 12,000 + 8,000 + 1,000        = 67,000
    total deductions = 1,000 + 500 + 1,500 + 3,000 + 13,000 = 19,000
    net   = 67,000 − 19,000                                 = 48,000
    ctc   = 67,000 + 1,500 + 3,500                           = 72,000
    """

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Test Entity', code='TSTE')
        cls.emp = Employee.objects.create(
            employee_number='TST_001', full_name='Test Employee One',
            job_title='Test Role', department='Testing', company=cls.company)
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31))

        K = PayslipComponent.Kind
        cls.ps = Payslip.objects.create(
            employee=cls.emp, period=cls.period, company=cls.company,
            gross_amount=D('67000.00'), paye_amount=D('13000.00'),
            net_amount=D('48000.00'), ctc_amount=D('72000.00'))
        rows = [
            ('BASIC', 'Basic Salary', K.EARNING, 10, D('60000.00')),
            ('ALLOWANCE', 'Allowance', K.EARNING, 20, D('10000.00')),
            # Negative earning: the BURS §32 housing salary sacrifice.
            ('HOUSING_ALLOWANCE', 'Housing Allowance', K.EARNING, 30, D('-12000.00')),
            ('VEHICLE_ALLOWANCE', 'Vehicle Allowance', K.EARNING, 40, D('8000.00')),
            ('FUEL_ALLOWANCE', 'Fuel Allowance', K.EARNING, 50, D('1000.00')),
            # Deductions are stored NEGATIVE on prod.
            ('LOANS_DEDUCTION', 'Loans Deduction', K.EMPLOYEE_DEDUCTION, 60, D('-1000.00')),
            ('HOUSING_TAX', 'Housing Tax Deduction', K.EMPLOYEE_DEDUCTION, 70, D('-500.00')),
            ('MEDICAL_AID_EE', 'Medical Aid Employee Contribution',
             K.EMPLOYEE_DEDUCTION, 80, D('-1500.00')),
            ('PROVIDENT_EE', 'Provident Fund Employee Contribution',
             K.EMPLOYEE_PRETAX, 90, D('-3000.00')),
            ('PAYE', 'PAYE', K.TAX, 100, D('13000.00')),
            ('MEDICAL_AID_ER', 'Medical Aid Company Contribution',
             K.COMPANY_CONTRIBUTION, 110, D('1500.00')),
            ('PROVIDENT_ER', 'Provident Fund Company Contribution',
             K.COMPANY_CONTRIBUTION, 120, D('3500.00')),
        ]
        for code, name, kind, order, amt in rows:
            PayslipLine.objects.create(
                payslip=cls.ps, component=_comp(code, name, kind, order),
                amount=amt)

    # ── the headline defect ────────────────────────────────────────────────
    def test_earnings_column_sums_to_gross(self):
        """Live bug: employer contributions were counted as employee earnings,
        so a slip listed ~20.7k more earnings than its printed Gross
        (66 of 102 slips affected)."""
        b = build_breakdown(self.ps)
        self.assertEqual(sum(a for _, a in b.earnings), D('67000.00'))
        self.assertEqual(b.gross, D('67000.00'))

    def test_gross_less_deductions_equals_net(self):
        b = build_breakdown(self.ps)
        self.assertEqual(b.gross - b.total_deductions, b.net)
        self.assertEqual(b.net, D('48000.00'))

    def test_both_columns_tie(self):
        self.assertTrue(build_breakdown(self.ps).ties)

    # ── employer money must not touch the employee's columns ───────────────
    def test_employer_contributions_are_their_own_block(self):
        b = build_breakdown(self.ps)
        labels = [l for l, _ in b.earnings] + [l for l, _ in b.deductions]
        self.assertNotIn('Medical Aid Company Contribution', labels)
        self.assertNotIn('Provident Fund Company Contribution', labels)
        self.assertEqual(b.employer_total, D('5000.00'))
        self.assertEqual(b.ctc, D('72000.00'))

    # ── the phantom deduction ──────────────────────────────────────────────
    def test_salary_sacrifice_stays_in_earnings_and_is_not_deducted_twice(self):
        """Live bug: a −14,000 housing sacrifice printed as a 14,000 Deduction
        even though it had already reduced Gross, so the slip showed money
        coming off that was never taken off."""
        b = build_breakdown(self.ps)
        self.assertIn(('Housing Allowance', D('-12000.00')), b.earnings)
        self.assertNotIn('Housing Allowance', [l for l, _ in b.deductions])
        self.assertEqual(b.total_deductions, D('19000.00'))

    # ── pre-tax deductions are still the employee's money ──────────────────
    def test_pretax_provident_appears_as_a_deduction(self):
        b = build_breakdown(self.ps)
        self.assertIn(('Provident Fund Employee Contribution', D('3000.00')),
                      b.deductions)
        self.assertEqual(b.taxable, D('64000.00'))

    def test_paye_appears_exactly_once(self):
        b = build_breakdown(self.ps)
        self.assertEqual([l for l, _ in b.deductions].count('PAYE'), 1)
        self.assertEqual(b.paye, D('13000.00'))

    def test_paye_on_both_line_and_header_is_not_double_counted(self):
        """Roll-forward slips carry PAYE on the header; imported ones on a line.
        A slip with both must show it once."""
        b = build_breakdown(self.ps)      # fixture has a TAX line AND paye_amount
        self.assertEqual(b.paye, D('13000.00'))
        self.assertEqual(b.total_deductions, D('19000.00'))

    # ── a positive-signed deduction must never become an earning ───────────
    def test_positive_signed_deduction_is_still_a_deduction(self):
        """The old classifier only caught deductions because their amounts were
        negative. Stored positive — which the importer does for some columns —
        the same line printed as an EARNING."""
        ps = Payslip.objects.create(
            employee=Employee.objects.create(employee_number='TST_002',
                                             full_name='Sign Test',
                                             company=self.company),
            period=self.period, company=self.company)
        K = PayslipComponent.Kind
        PayslipLine.objects.create(
            payslip=ps, component=_comp('BASIC', 'Basic Salary', K.EARNING, 10),
            amount=D('5000.00'))
        PayslipLine.objects.create(
            payslip=ps,
            component=_comp('UNION_DUES', 'Union Dues', K.EMPLOYEE_DEDUCTION, 65),
            amount=D('250.00'))          # positive on purpose
        b = build_breakdown(ps, check_stored=False)
        self.assertEqual([l for l, _ in b.earnings], ['Basic Salary'])
        self.assertIn(('Union Dues', D('250.00')), b.deductions)
        self.assertEqual(b.net, D('4750.00'))

    # ── L2: the enum must never grow past this module unnoticed ────────────
    def test_every_component_kind_is_accounted_for(self):
        """L2 failure class (CFO-approved checklist entry). If a new Kind is
        added that carries employee money and this module is not updated, the
        line is dropped by BOTH build_breakdown and Payslip.recompute_totals —
        so stored equals derived, no variance fires, and the money is invisible
        on the payslip. Pin the CONSTANT, not just the happy path."""
        from payroll.payslip_breakdown import _CLASSIFIED_KINDS
        missing = set(PayslipComponent.Kind.values) - set(_CLASSIFIED_KINDS)
        self.assertEqual(
            missing, set(),
            f'PayslipComponent.Kind gained {sorted(missing)} — classify it in '
            f'payroll/payslip_breakdown.py (earning / deduction / tax / '
            f'employer / deliberately-ignored) before shipping.')

    # ── never print two unrelated numbers in silence ───────────────────────
    def test_variance_is_reported_when_lines_disagree_with_stored_totals(self):
        """A live slip itemised a net ~19.5k above what payroll paid. The slip
        printed both figures and flagged neither."""
        self.ps.gross_amount = D('75000.00')      # deliberately wrong
        self.ps.save(update_fields=['gross_amount'])
        b = build_breakdown(self.ps)
        self.assertTrue(b.variances)
        self.assertIn('67,000.00', b.variances[0])
        self.assertIn('75,000.00', b.variances[0])

    # ── CTC removed from the slip, but still reconciled ────────────────────
    def test_ctc_is_not_shown_to_the_employee_but_is_still_reconciled(self):
        """CFO / Pako Kago 2026-07-29: cost-to-company no longer prints on the
        payslip, and the CFO asked for the reconciliation to be KEPT. So a CTC
        mismatch must NOT reach the employee-facing "under review" band (it
        would warn them about a figure their slip does not show) but must still
        be detected for finance."""
        self.ps.ctc_amount = D('99000.00')        # deliberately wrong
        self.ps.save(update_fields=['ctc_amount'])
        b = build_breakdown(self.ps)
        self.assertEqual(b.variances, [], 'CTC drift must not reach the employee')
        self.assertTrue(b.internal_variances, 'CTC drift must still be detected')
        self.assertIn('Cost to company', b.internal_variances[0])
        self.assertEqual(b.ctc, D('72000.00'))    # still computed

    def test_cost_to_company_does_not_print_on_the_payslip(self):
        """The employer-contributions block and the Cost-to-Company total were
        removed from the employee-facing PDF. Asserted against the REAL rendered
        page text, not the source: if either label comes back, a payslip is
        showing company cost as though it were the employee's own pay."""
        text = _pdf_text(generate_payslip_pdf(self.ps))
        self.assertNotIn('Cost to Company', text)
        self.assertNotIn('Employer Contributions', text)
        # The employee's own figures must still be there and still reconcile.
        self.assertIn('Gross Pay', text)
        self.assertIn('Net Pay', text)
        self.assertNotIn('Medical Aid Company Contribution', text)
        self.assertNotIn('Provident Fund Company Contribution', text)

    def test_pdf_renders_and_is_a_pdf(self):
        pdf = generate_payslip_pdf(self.ps)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 2000)

    def test_payslip_pdf_has_no_qr_link(self):
        """QR removed 2026-08: the old URL was /payroll/payslips/<id>, a
        login-only page — a dead link on any phone (Bharath, 2026-08-18). Red if
        anyone re-adds a qr_url without a PUBLIC verify route existing first."""
        from unittest.mock import patch
        with patch('payroll.pdf._render', return_value=b'%PDF-stub') as m:
            generate_payslip_pdf(self.ps)
        data = m.call_args[0][0]
        self.assertEqual(data['qr_url'], '')

    def test_pdf_is_a_single_page(self):
        """A payslip must never spill onto a second sheet — the first rebuild
        pushed Employer Contributions to page 2."""
        pdf = generate_payslip_pdf(self.ps)
        pages = pdf.count(b'/Type /Page') - pdf.count(b'/Type /Pages')
        self.assertEqual(pages, 1)

    def test_no_bare_minus_sign_on_the_slip(self):
        """A negative earning must print as (12,000.00), not -12,000.00 —
        otherwise it contradicts the explanatory note under the block."""
        from payroll.pdf import _section
        tbl = _section('Earnings', [('Housing Allowance', D('-12000.00'))],
                       total=('Gross Pay', D('48000.00')))
        rendered = ' '.join(
            c.text for row in tbl._cellvalues for c in row
            if hasattr(c, 'text'))
        self.assertIn('(12,000.00)', rendered)
        self.assertNotIn('-12,000.00', rendered)

    def test_net_pay_block_has_no_none_this_period_filler(self):
        """The Net Pay block carries only a total. The empty-block placeholder
        must not appear above it."""
        from payroll.pdf import _section
        tbl = _section('Net Pay', [], total=('Net Pay', D('48000.00')),
                       accent=True)
        rendered = ' '.join(
            c.text for row in tbl._cellvalues for c in row
            if hasattr(c, 'text'))
        self.assertNotIn('None this period', rendered)

    def test_footer_makes_no_scan_to_verify_promise(self):
        """There is no public payslip-verification route yet, so the slip must
        not tell a bank or embassy to scan the code to verify it (they would hit
        a login wall and a 404). Restore the sentence only when the route ships."""
        from payroll.pdf import FOOTER_LINE_2
        self.assertNotIn('scan', FOOTER_LINE_2.lower())


class HeadlineOnlyPayslipTests(TestCase):
    """A BWP slip with totals on the header but no component lines (2,158 on
    prod). It printed Gross 0.00 and a red "under review, contact HR before
    relying on it" band, so staff could not give it to a bank (CFO 2026-09-18)."""

    @classmethod
    def setUpTestData(cls):
        co = Company.objects.create(name='Headline Entity', code='HDLN')
        emp = Employee.objects.create(employee_number='HDL_001', full_name='Headline Staff',
                                      company=co)
        period = PayrollPeriod.objects.create(period_name='2026-05', start_date=date(2026, 5, 1),
                                              end_date=date(2026, 5, 31))
        cls.ps = Payslip.objects.create(employee=emp, period=period, company=co,
                                        gross_amount=D('20000.00'), paye_amount=D('2500.00'),
                                        net_amount=D('17500.00'))

    def test_headline_slip_ties_without_a_variance(self):
        b = build_breakdown(self.ps)
        self.assertEqual(b.variances, [])
        self.assertEqual(b.gross, D('20000.00'))
        self.assertEqual(b.net, D('17500.00'))
        self.assertTrue(b.ties)

    def test_headline_net_below_gross_less_paye_gets_one_balancing_line(self):
        """Historical slips: net already has medical/pension taken off that never
        arrived as lines. The gap shows once as a balancing deduction."""
        self.ps.net_amount = D('16000.00')        # 1,500 other deductions baked in
        self.ps.save(update_fields=['net_amount'])
        b = build_breakdown(self.ps)
        self.assertEqual(b.variances, [])
        self.assertIn(('Other deductions (per payroll)', D('1500.00')), b.deductions)
        self.assertEqual(b.net, D('16000.00'))

    def test_headline_net_above_gross_less_paye_is_still_flagged(self):
        self.ps.net_amount = D('19000.00')        # impossible: more than gross - PAYE
        self.ps.save(update_fields=['net_amount'])
        b = build_breakdown(self.ps)
        self.assertTrue(b.variances)
        self.assertNotIn('Other deductions (per payroll)', [l for l, _ in b.deductions])

    def test_headline_slip_pdf_has_no_under_review_band(self):
        text = _pdf_text(generate_payslip_pdf(self.ps))
        self.assertNotIn('under review', text)
        self.assertIn('20,000.00', text)
        self.assertIn('17,500.00', text)
        # The FNB strip is conditional: pin the BWP side too.
        self.assertIn('First National Bank', text)


class ForeignCurrencyPayslipTests(TestCase):
    """ADRisk Global pays in INR (CFO 2026-06-20). The slip must be issued in
    the source currency while the stored *_amount fields stay BWP for group
    reporting — so a BWP/INR difference is BY DESIGN and must NOT raise a
    variance warning on the employee's payslip."""

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Foreign Entity', code='FRGN')
        cls.emp = Employee.objects.create(
            employee_number='FRG_001', full_name='Foreign Test Employee',
            job_title='Analyst', department='Technology', company=cls.company)
        # period_name is max_length=10 AND globally unique (one row per month),
        # so a foreign-currency fixture must use a different month, not a suffix.
        cls.period = PayrollPeriod.objects.create(
            period_name='2026-08', start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31))

    def _slip(self, *, with_lines: bool, source_net=D('55800.00')):
        fx = D('0.142857')
        ps = Payslip.objects.create(
            employee=self.emp, period=self.period, company=self.company,
            source_currency='INR', fx_rate_to_bwp=fx,
            source_gross=D('60000.00'), source_paye=D('4200.00'),
            source_net=source_net,
            # BWP reporting figures — deliberately different from the INR ones.
            gross_amount=D('8571.42'), paye_amount=D('599.99'),
            net_amount=D('7971.43'), ctc_amount=D('8571.42'))
        if with_lines:
            K = PayslipComponent.Kind
            PayslipLine.objects.create(
                payslip=ps, component=_comp('BASIC', 'Basic Salary', K.EARNING, 10),
                amount=D('60000.00'))
            PayslipLine.objects.create(
                payslip=ps, component=_comp('TDS', 'Tax', K.TAX, 100),
                amount=D('4200.00'))
        return ps

    def test_bwp_vs_source_difference_is_not_flagged_as_a_variance(self):
        b = build_breakdown(self._slip(with_lines=True))
        self.assertEqual(b.variances, [])

    def test_headline_only_foreign_slip_renders_in_source_currency(self):
        """ADRisk slips imported as headline Gross/Tax/Net carry no component
        lines — the PDF must synthesise a readable slip rather than print blank."""
        pdf = generate_payslip_pdf(self._slip(with_lines=False))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 2000)

    def test_foreign_slip_carries_no_internal_or_wrong_bank_lines(self):
        """Staff give this slip to their own bank: no group-reporting FX line and
        no "paid from FNB Botswana" strip, which is false for an INR slip."""
        text = _pdf_text(generate_payslip_pdf(self._slip(with_lines=False)))
        self.assertNotIn('under review', text)
        self.assertIn('60,000.00', text)          # the INR gross, not 0.00
        self.assertNotIn('Group reporting', text)
        self.assertNotIn('First National Bank', text)
        self.assertIn('INR', text)

    def test_foreign_slip_with_lines_renders(self):
        pdf = generate_payslip_pdf(self._slip(with_lines=True))
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_headline_foreign_net_below_gross_less_tax_still_ties(self):
        """`source_net` comes verbatim from the source payroll's Net column, so
        an Indian payroll's net is already net of PF / professional tax that
        never arrived as components. Without a balancing line the slip would
        assert 'gross less tax equals net' — false on the face of the document,
        and `_compare_stored` is skipped for foreign slips so nothing catches it.

        Gross 60,000, tax 4,200, net 50,000 → total deductions 10,000, made up
        of the 4,200 tax plus a 5,800 balancing row. Asserts the actual data
        handed to the renderer, so deleting the balancing code fails this test.
        """
        from unittest import mock
        ps = self._slip(with_lines=False, source_net=D('50000.00'))
        with mock.patch('payroll.pdf._render', return_value=b'%PDF-stub') as r:
            generate_payslip_pdf(ps)
        data = r.call_args[0][0]
        self.assertEqual(data['total_deductions'], D('10000.00'))
        self.assertIn(('Other deductions (per source payroll)', D('5800.00')),
                      data['deductions'])
        self.assertIn(('Tax', D('4200.00')), data['deductions'])
        # The column must sum to its own printed total.
        self.assertEqual(sum(a for _, a in data['deductions']),
                         data['total_deductions'])
        # …and the reconciliation sentence the slip prints must be true.
        self.assertEqual(data['gross'] - data['total_deductions'],
                         data['net_salary'])
        self.assertEqual(data['variances'], [])

    def test_headline_foreign_net_above_gross_less_tax_is_flagged_not_faked(self):
        """Corrupt import: net ABOVE gross-less-tax. A negative balancing row
        would be rendered as a magnitude, so the column would silently stop
        summing. The slip must say the figures do not reconcile instead."""
        from unittest import mock
        ps = self._slip(with_lines=False, source_net=D('60000.00'))  # > 60,000-4,200
        with mock.patch('payroll.pdf._render', return_value=b'%PDF-stub') as r:
            generate_payslip_pdf(ps)
        data = r.call_args[0][0]
        self.assertEqual(data['total_deductions'], D('4200.00'))
        self.assertEqual(sum(a for _, a in data['deductions']),
                         data['total_deductions'])
        self.assertNotIn('Other deductions (per source payroll)',
                         [l for l, _ in data['deductions']])
        self.assertTrue(data['variances'])
        self.assertIn('do not reconcile', data['variances'][0])

    def test_foreign_paye_fallback_never_uses_the_bwp_figure(self):
        """A foreign slip with earning lines but no TAX line must fall back to
        `source_paye`, not the BWP `paye_amount` — mixing the two produces an
        INR-minus-BWP net that the (skipped) variance check cannot catch."""
        ps = self._slip(with_lines=False)
        K = PayslipComponent.Kind
        PayslipLine.objects.create(
            payslip=ps, component=_comp('BASIC', 'Basic Salary', K.EARNING, 10),
            amount=D('60000.00'))
        b = build_breakdown(ps)
        self.assertEqual(b.paye, D('4200.00'))        # source_paye, INR
        self.assertNotEqual(b.paye, D('599.99'))      # never paye_amount, BWP


class MoneyFormatTests(TestCase):
    """CFO 2026-07-25: no 'P' after every figure; deductions in brackets."""

    def test_no_currency_mark_on_figures(self):
        self.assertEqual(_money(D('78900')), '78,900.00')
        self.assertNotIn('P', _money(D('78900')))

    def test_deductions_print_in_brackets(self):
        self.assertEqual(_bracket(D('300')), '(300.00)')
        self.assertEqual(_bracket(D('3000')), '(3,000.00)')
        self.assertEqual(_bracket(D('-1234.5')), '(1,234.50)')

    def test_currency_is_passed_not_held_in_module_state(self):
        """Prod runs gunicorn --workers 4 --threads 4. The currency label must
        be a parameter, never module state, or a concurrent BWP render can flip
        an in-flight INR payslip's header."""
        import payroll.pdf as m
        self.assertFalse(hasattr(m, '_ACTIVE_CCY'))
        tbl = m._section('Earnings', [('Basic Salary', D('100.00'))], ccy='INR')
        rendered = ' '.join(
            c.text for row in tbl._cellvalues for c in row
            if hasattr(c, 'text'))
        self.assertIn('INR', rendered)
