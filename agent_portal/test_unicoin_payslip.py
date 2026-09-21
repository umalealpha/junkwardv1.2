"""
Tests for the UniCoin Instant Insurance payslip layer:
  • tax.agent_tax ties to Bharath's Tax-Compiler numbers to the cent
  • payslip_service builds one net-of-tax payslip per payable agent
  • the PDF renders and the earnings column sums to gross
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from agent_portal.models import Agent, AgentPayslip, CommissionCycle, CommissionLine
from agent_portal.tax import agent_tax
from agent_portal.payslip_service import build_payslips_for_cycle
from agent_portal.payslip_pdf import render_agent_payslip


class TaxTests(TestCase):
    def test_denford_high_earner_ties_to_sheet(self):
        t = agent_tax(Decimal('31843.25'))
        self.assertEqual(t.annual, Decimal('382119.00'))
        self.assertEqual(t.tax, Decimal('5798.29'))     # Bharath's monthly tax
        self.assertEqual(t.net, Decimal('26044.96'))    # Amount Payable

    def test_lone_lefa_top_band_of_the_run(self):
        # P4,500/month -> annual 54,000 -> (54,000-48,000)*5% = 300 -> /12 = 25.00
        t = agent_tax(Decimal('4500'))
        self.assertEqual(t.annual, Decimal('54000.00'))
        self.assertEqual(t.tax, Decimal('25.00'))
        self.assertEqual(t.net, Decimal('4475.00'))

    def test_below_threshold_is_zero_tax(self):
        # Most Instant Insurance agents: annual < 48,000 -> no tax, net = gross
        t = agent_tax(Decimal('2774'))
        self.assertEqual(t.tax, Decimal('0.00'))
        self.assertEqual(t.net, Decimal('2774.00'))

    def test_vat_is_memo_only_not_deducted(self):
        t = agent_tax(Decimal('1140.00'))
        self.assertEqual(t.ex_vat, Decimal('1000.00'))   # 1140 / 1.14
        self.assertEqual(t.net, t.gross - t.tax)          # VAT never leaves net


class PayslipBuildTests(TestCase):
    def setUp(self):
        self.cycle = CommissionCycle.objects.create(
            label='UniCoin July 2026', start_date=date(2026, 6, 23), end_date=date(2026, 7, 24))
        self.a1 = Agent.objects.create(name='Lone Lefa Morolong')
        self.a2 = Agent.objects.create(name='Jessica Nkala')
        # a1: single incentive line 4,500 (top of the run -> small tax)
        CommissionLine.objects.create(cycle=self.cycle, agent=self.a1, stream='incentives',
                                      basis=Decimal('4500'), commission=Decimal('4500'), payable=True)
        # a2: two streams summing 2,774 (below threshold -> no tax)
        CommissionLine.objects.create(cycle=self.cycle, agent=self.a2, stream='new_sales_mis',
                                      basis=Decimal('98'), commission=Decimal('98'), payable=True)
        CommissionLine.objects.create(cycle=self.cycle, agent=self.a2, stream='conversion',
                                      basis=Decimal('2676'), commission=Decimal('2676'), payable=True)
        # a2 also has a rejected line (transparency, must not affect gross)
        CommissionLine.objects.create(cycle=self.cycle, agent=self.a2, stream='conversion',
                                      basis=Decimal('79'), commission=Decimal('0'), payable=False,
                                      reason='Policy not activated', policy_ref='MIS2026210635')

    def test_build_one_payslip_per_payable_agent(self):
        summary = build_payslips_for_cycle(self.cycle)
        self.assertEqual(summary['payslips'], 2)
        self.assertEqual(Decimal(summary['gross_total_bwp']), Decimal('7274.00'))

        p1 = AgentPayslip.objects.get(cycle=self.cycle, agent=self.a1)
        self.assertEqual(p1.gross, Decimal('4500.00'))
        self.assertEqual(p1.tax, Decimal('25.00'))
        self.assertEqual(p1.net, Decimal('4475.00'))
        self.assertTrue(p1.number.startswith('UNI-2607-'))

        p2 = AgentPayslip.objects.get(cycle=self.cycle, agent=self.a2)
        self.assertEqual(p2.gross, Decimal('2774.00'))
        self.assertEqual(p2.tax, Decimal('0.00'))
        self.assertEqual(p2.net, Decimal('2774.00'))
        # earnings itemised by agent-facing label; the two conversion labels fold
        labels = {row[0] for row in p2.breakdown}
        self.assertEqual(labels, {'New Sales', 'RealPay Conversion'})
        # the rejected line shows under not_paid, not in gross
        self.assertTrue(any('activated' in r[2].lower() for r in p2.not_paid))

    def test_rebuild_is_idempotent_and_keeps_numbers(self):
        build_payslips_for_cycle(self.cycle)
        n_before = AgentPayslip.objects.get(cycle=self.cycle, agent=self.a1).number
        s2 = build_payslips_for_cycle(self.cycle)
        self.assertEqual(s2['payslips'], 2)
        self.assertEqual(AgentPayslip.objects.count(), 2)
        self.assertEqual(AgentPayslip.objects.get(cycle=self.cycle, agent=self.a1).number, n_before)

    def test_corrected_reimport_drops_stale_payslip(self):
        # a1 + a2 both get slips; then a1's commission is removed (corrected pack)
        build_payslips_for_cycle(self.cycle)
        self.assertEqual(AgentPayslip.objects.filter(cycle=self.cycle).count(), 2)
        CommissionLine.objects.filter(cycle=self.cycle, agent=self.a1).delete()
        s = build_payslips_for_cycle(self.cycle)
        self.assertEqual(s['removed'], 1)
        self.assertFalse(AgentPayslip.objects.filter(cycle=self.cycle, agent=self.a1).exists())
        self.assertTrue(AgentPayslip.objects.filter(cycle=self.cycle, agent=self.a2).exists())

    def test_rebuild_blocked_once_approved(self):
        build_payslips_for_cycle(self.cycle)
        self.cycle.status = CommissionCycle.Status.APPROVED
        self.cycle.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            build_payslips_for_cycle(self.cycle)

    def test_reopen_refuses_paid_cycle(self):
        from agent_portal.service import reopen_cycle
        self.cycle.status = CommissionCycle.Status.PAID
        self.cycle.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            reopen_cycle(self.cycle)

    def test_agent_review_workbook_ties(self):
        build_payslips_for_cycle(self.cycle)
        from agent_portal.review_xlsx import agent_review_workbook
        import io, openpyxl
        data = agent_review_workbook(self.cycle)
        self.assertEqual(data[:2], b'PK')  # xlsx is a zip
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        self.assertEqual(ws.cell(row=4, column=1).value, 'Payslip')  # header row
        # 2 agents -> rows 5,6 -> TOTAL on row 7; Net (col 13) = 4475 + 2774
        self.assertEqual(ws.cell(row=7, column=2).value, 'TOTAL')
        self.assertEqual(round(ws.cell(row=7, column=13).value, 2), 7249.00)

    def test_pdf_renders(self):
        build_payslips_for_cycle(self.cycle)
        from agent_portal.payslip_pdf import generate_agent_payslip_pdf
        p1 = AgentPayslip.objects.get(cycle=self.cycle, agent=self.a1)
        pdf = generate_agent_payslip_pdf(p1)
        self.assertTrue(pdf[:5] == b'%PDF-')
        self.assertGreater(len(pdf), 2000)


class PdfSampleTests(TestCase):
    def test_standalone_render_sums(self):
        pdf = render_agent_payslip({
            'agent_name': 'Test Agent', 'agent_ref': '900001', 'agency': 'UniCoin',
            'cycle_label': 'UniCoin July 2026', 'period': '23 Jun – 24 Jul 2026',
            'number': 'UNI-2607-0001', 'computed_on': '27 Jul 2026',
            'earnings': [('New Sales', Decimal('343.08')), ('Collection', Decimal('556.04'))],
            'gross': Decimal('899.12'), 'vat_memo': Decimal('788.70'),
            'tax': Decimal('0.00'), 'net': Decimal('899.12'), 'annual': Decimal('10789.44'),
            'qr_url': 'https://omni.alphadirect.co.bw/unicoin/verify/UNI-2607-0001',
        })
        self.assertTrue(pdf[:5] == b'%PDF-')
