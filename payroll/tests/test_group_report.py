from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency
from payroll.group_report import generate, notify
from payroll.models import (
    Employee,
    GroupPayrollSnapshot,
    Payslip,
    PayslipComponent,
    PayslipLine,
    PayrollPeriod,
    PayrollSignOff,
)
from payroll.monthly_pack import _totals_block


class GroupPayrollReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        Currency.objects.get_or_create(
            code='BWP',
            defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )

        cls.final_company = Company.objects.create(code='BWPFINAL', name='Alpha Final Co')
        cls.draft_company = Company.objects.create(code='BWPDRAFT', name='Beta Draft Co')
        cls.notrun_company = Company.objects.create(code='ZZNOTRUN', name='Zed Not Run Co')

        cls.period = PayrollPeriod.objects.create(
            period_name='2026-09',
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 30),
        )
        cls.previous_period = PayrollPeriod.objects.create(
            period_name='2026-08',
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )

        cls.basic = PayslipComponent.objects.create(code='BASIC', name='Basic Salary', kind='earning')
        cls.incentive = PayslipComponent.objects.create(code='INCENTIVE', name='Incentive', kind='earning')
        cls.commission = PayslipComponent.objects.create(code='COMMISSION', name='Commission', kind='earning')
        cls.pension_er = PayslipComponent.objects.create(
            code='PENSION_ER', name='Pension Employer', kind='company_contribution'
        )

        emp_final = Employee.objects.create(
            employee_number='E001',
            full_name='Final Employee',
            company=cls.final_company,
            status='active',
            department='Sales',
            email='final@example.com',
        )
        emp_draft = Employee.objects.create(
            employee_number='E002',
            full_name='Draft Employee',
            company=cls.draft_company,
            status='active',
            department='Operations',
            email='draft@example.com',
        )
        emp_notrun = Employee.objects.create(
            employee_number='E003',
            full_name='Not Run Employee',
            company=cls.notrun_company,
            status='active',
            department='Admin',
            email='notrun@example.com',
        )

        cls.final_slip = Payslip.objects.create(
            employee=emp_final,
            period=cls.period,
            company=cls.final_company,
            gross_amount=Decimal('5700.00'),
            paye_amount=Decimal('570.00'),
            net_amount=Decimal('5130.00'),
            ctc_amount=Decimal('5800.00'),
            source_currency='BWP',
            source_gross=Decimal('5700.00'),
            fx_rate_to_bwp=Decimal('1.0000'),
            status='paid',
        )
        PayslipLine.objects.create(payslip=cls.final_slip, component=cls.basic, amount=Decimal('5000.00'))
        PayslipLine.objects.create(
            payslip=cls.final_slip, component=cls.incentive, amount=Decimal('500.00')
        )
        PayslipLine.objects.create(
            payslip=cls.final_slip, component=cls.commission, amount=Decimal('200.00')
        )
        PayslipLine.objects.create(
            payslip=cls.final_slip, component=cls.pension_er, amount=Decimal('100.00')
        )

        cls.draft_slip = Payslip.objects.create(
            employee=emp_draft,
            period=cls.period,
            company=cls.draft_company,
            gross_amount=Decimal('3000.00'),
            paye_amount=Decimal('300.00'),
            net_amount=Decimal('2700.00'),
            ctc_amount=Decimal('3000.00'),
            source_currency='BWP',
            source_gross=Decimal('3000.00'),
            fx_rate_to_bwp=Decimal('1.0000'),
            status='approved',
        )
        PayslipLine.objects.create(payslip=cls.draft_slip, component=cls.basic, amount=Decimal('3000.00'))

        prev_slip = Payslip.objects.create(
            employee=emp_notrun,
            period=cls.previous_period,
            company=cls.notrun_company,
            gross_amount=Decimal('1000.00'),
            paye_amount=Decimal('100.00'),
            net_amount=Decimal('900.00'),
            ctc_amount=Decimal('1000.00'),
            source_currency='BWP',
            source_gross=Decimal('1000.00'),
            fx_rate_to_bwp=Decimal('1.0000'),
            status='paid',
        )
        PayslipLine.objects.create(payslip=prev_slip, component=cls.basic, amount=Decimal('1000.00'))

        final_slips = Payslip.objects.filter(period=cls.period, company=cls.final_company).prefetch_related(
            'lines__component'
        )
        final_totals = _totals_block(final_slips)
        cls.final_signoff = PayrollSignOff.objects.create(
            period=cls.period,
            company=cls.final_company,
            status='approved',
            headcount=final_totals['headcount'],
            gross_total=final_totals['gross'],
            paye_total=final_totals['paye'],
            net_total=final_totals['net'],
        )

        draft_slips = Payslip.objects.filter(period=cls.period, company=cls.draft_company).prefetch_related(
            'lines__component'
        )
        draft_totals = _totals_block(draft_slips)
        cls.draft_signoff = PayrollSignOff.objects.create(
            period=cls.period,
            company=cls.draft_company,
            status='submitted',
            headcount=draft_totals['headcount'],
            gross_total=draft_totals['gross'] + Decimal('100.00'),
            paye_total=draft_totals['paye'],
            net_total=draft_totals['net'],
        )

    def test_generate_has_three_lines_with_statuses(self):
        snap = generate(self.period)
        lines = {line.company: line for line in snap.lines.all()}

        self.assertEqual(snap.lines.count(), 3)
        self.assertEqual(lines[self.final_company].status, 'final')
        self.assertEqual(lines[self.draft_company].status, 'draft')
        self.assertEqual(lines[self.notrun_company].status, 'not_run')
        self.assertEqual(snap.excluded, [self.notrun_company.name])

    def test_group_totals_exclude_not_run_and_sum_included(self):
        snap = generate(self.period)

        final_line = snap.lines.get(company=self.final_company)
        draft_line = snap.lines.get(company=self.draft_company)
        totals = snap.totals

        self.assertEqual(totals['headcount'], str(final_line.headcount + draft_line.headcount))
        self.assertEqual(Decimal(totals['gross']), final_line.gross + draft_line.gross)
        self.assertEqual(Decimal(totals['basic']), final_line.basic + draft_line.basic)
        self.assertEqual(Decimal(totals['net']), final_line.net + draft_line.net)
        self.assertEqual(Decimal(totals['ctc']), final_line.ctc + draft_line.ctc)
        self.assertEqual(totals['final_companies'], 1)
        self.assertEqual(totals['draft_companies'], 1)

    def test_employer_contrib_summed(self):
        snap = generate(self.period)
        self.assertEqual(Decimal(snap.totals['employer_contrib']), Decimal('100.00'))

    def test_tieout_ok_for_matching_signoff_and_false_on_mismatch(self):
        snap = generate(self.period)

        final_line = snap.lines.get(company=self.final_company)
        draft_line = snap.lines.get(company=self.draft_company)

        self.assertTrue(final_line.tieout_ok)
        self.assertEqual(final_line.tieout_note, 'Matches sign-off')

        self.assertFalse(draft_line.tieout_ok)
        self.assertIn('Gross differs from sign-off by P100.00', draft_line.tieout_note)

    def test_regenerate_makes_version_2_and_version_1_unchanged(self):
        v1 = generate(self.period)
        v1_totals = v1.totals.copy()

        v2 = generate(self.period)

        self.assertEqual(v1.version, 1)
        self.assertEqual(v2.version, 2)
        v1.refresh_from_db()
        self.assertEqual(v1.totals, v1_totals)
        self.assertEqual(GroupPayrollSnapshot.objects.filter(period=self.period).count(), 2)

    def test_view_403_for_non_viewer_and_200_for_aiyer(self):
        generate(self.period)
        User = get_user_model()
        non_viewer = User.objects.create_user(
            username='nonviewer',
            email='nonviewer@alphadirect.co.bw',
        )
        aiyer = User.objects.create_user(
            username='aiyer',
            email='aiyer@alphadirect.co.bw',
        )

        client = APIClient()
        client.force_authenticate(non_viewer)
        resp = client.get('/api/v1/payroll/group-report/?period=2026-09')
        self.assertEqual(resp.status_code, 403)

        client.force_authenticate(aiyer)
        resp = client.get('/api/v1/payroll/group-report/?period=2026-09')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['period'], '2026-09')

    def test_regenerate_403_for_aiyer_and_200_for_superuser(self):
        generate(self.period)
        User = get_user_model()
        aiyer = User.objects.create_user(
            username='aiyer',
            email='aiyer@alphadirect.co.bw',
        )
        superuser = User.objects.create_superuser(
            username='superuser',
            email='superuser@alphadirect.co.bw',
        )

        client = APIClient()
        client.force_authenticate(aiyer)
        resp = client.post(
            '/api/v1/payroll/group-report/regenerate/',
            {'period': '2026-09'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

        client.force_authenticate(superuser)
        resp = client.post(
            '/api/v1/payroll/group-report/regenerate/',
            {'period': '2026-09'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['version'], 2)

    def test_notify_html_contains_no_company_name_or_employee_name(self):
        snap = generate(self.period)

        with patch('payroll.group_report.send_html_with_cfo_cc') as mocked:
            mocked.return_value = 5
            result = notify(snap)

        self.assertEqual(result, 5)
        self.assertTrue(mocked.called)

        args, kwargs = mocked.call_args
        html = kwargs['html']

        self.assertIn('Cost to company', html)
        self.assertNotIn(self.final_company.name, html)
        self.assertNotIn(self.draft_company.name, html)
        self.assertNotIn(self.notrun_company.name, html)
        self.assertNotIn('Final Employee', html)
        self.assertNotIn('Draft Employee', html)
        self.assertNotIn('Not Run Employee', html)

    def test_totals_only_company_is_flagged_not_silently_zero(self):
        from payroll.group_report import generate
        from payroll.models import Payslip
        Payslip.objects.filter(period=self.period).first().lines.all().delete()
        # a company whose slips carry no component lines at all
        target = Payslip.objects.filter(period=self.period).first().company
        from payroll.models import PayslipLine
        PayslipLine.objects.filter(payslip__company=target, payslip__period=self.period).delete()
        snap = generate(self.period, trigger='manual')
        line = snap.lines.get(company=target)
        self.assertIn('Totals only', line.tieout_note)

