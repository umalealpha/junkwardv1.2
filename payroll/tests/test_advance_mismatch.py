"""The salary-advance mismatch warning (CFO decision 13 September 2026).

Omni does NOT raise the payout — Finance raise the payment by hand — so Omni's
records and what actually happened can disagree three ways. This proves each
one is found, that a clean book produces NOTHING, and that the scheduled job
refuses to email a cheerful empty report.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from payroll.advance_mismatch import find_mismatches
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollPeriod)
from payroll.salary_advance_models import SalaryAdvance, SalaryAdvancePayout
from reporting.models import ReportRecipient

SEND_PATH = ('payroll.management.commands.send_advance_mismatch_report'
             '.send_html_with_cfo_cc')


class AdvanceMismatchBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.prev = PayrollPeriod.objects.create(
            period_name='2026-09', start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))
        cls.target = PayrollPeriod.objects.create(
            period_name='2026-10', start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 31))
        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A', company=cls.co,
            hire_date=datetime.date(2024, 1, 1))
        cls.cfo = User.objects.create_user('cfo_adv',
                                           'pganesharajah@alphadirect.co.bw', 'x')

    def _advance(self, status=SalaryAdvance.Status.APPROVED, amount='1500.00',
                 approved_days_ago=10, period='2026-10', employee=None):
        approved = None
        if approved_days_ago is not None:
            approved = timezone.now() - datetime.timedelta(days=approved_days_ago)
        return SalaryAdvance.objects.create(
            employee=employee or self.emp, company=self.co,
            recovery_period=period, amount=Decimal(amount),
            basic_salary=Decimal('9000'), max_allowed=Decimal('3000'),
            status=status, cfo_approver=self.cfo, cfo_approved_at=approved)

    def _payout(self, advance=None, amount='1500.00', days_ago=8, employee=None):
        return SalaryAdvancePayout.objects.create(
            employee=employee or self.emp, advance=advance,
            amount=Decimal(amount),
            paid_on=timezone.localdate() - datetime.timedelta(days=days_ago),
            reference='FNB-TEST-1')

    def _recovery_amendment(self, advance):
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.prev, company=self.co,
            file_name='AUTO-ADVANCE',
            status=PayrollAmendmentBatch.Status.PARSED)
        amd = PayrollAmendment.objects.create(
            batch=batch, employee=advance.employee,
            kind=PayrollAmendment.Kind.DEDUCTION_ADD, amount=advance.amount)
        advance.payroll_amendment = amd
        advance.save(update_fields=['payroll_amendment', 'updated_at'])
        return amd


class MismatchDetectionTests(AdvanceMismatchBase):

    def test_a_clean_book_reports_nothing(self):
        adv = self._advance()
        self._payout(advance=adv)
        res = find_mismatches()
        self.assertEqual(res['count'], 0)
        self.assertEqual(res['value'], Decimal('0.00'))

    def test_approved_but_never_paid_out_is_found(self):
        self._advance(amount='1500.00')
        res = find_mismatches()
        self.assertEqual(len(res['approved_not_paid']), 1)
        row = res['approved_not_paid'][0]
        self.assertEqual(row['employee'], 'Alice A')
        self.assertEqual(row['amount'], Decimal('1500.00'))

    def test_an_advance_approved_today_is_not_a_mismatch_yet(self):
        """Approved this morning and being paid this afternoon is normal. A
        report that flags it teaches the reader to ignore the rest."""
        self._advance(approved_days_ago=0)
        self.assertEqual(find_mismatches()['count'], 0)

    def test_a_payout_with_no_advance_record_is_found(self):
        self._payout(advance=None, amount='900.00')
        res = find_mismatches()
        self.assertEqual(len(res['payout_without_advance']), 1)
        self.assertIn('No advance record',
                      res['payout_without_advance'][0]['what_to_do'])

    def test_a_payout_behind_a_declined_advance_is_found(self):
        adv = self._advance(status=SalaryAdvance.Status.DECLINED)
        self._payout(advance=adv)
        res = find_mismatches()
        self.assertEqual(len(res['payout_without_advance']), 1)
        self.assertIn('declined',
                      res['payout_without_advance'][0]['what_to_do'].lower())

    def test_a_recovery_raised_with_no_payout_recorded_is_found(self):
        """The one that costs an employee: the deduction is already on a batch
        and nothing says they ever received the advance."""
        adv = self._advance()
        self._recovery_amendment(adv)
        res = find_mismatches()
        self.assertEqual(len(res['recovery_without_payout']), 1)
        self.assertEqual(res['recovery_without_payout'][0]['applied'], 'Not yet')

    def test_a_recovery_with_its_payout_recorded_is_clean(self):
        adv = self._advance()
        self._recovery_amendment(adv)
        self._payout(advance=adv)
        self.assertEqual(find_mismatches()['count'], 0)

    def test_the_value_adds_up_the_rows_it_lists(self):
        self._advance(amount='1500.00')
        other = Employee.objects.create(employee_number='E2', full_name='Bob B',
                                        company=self.co,
                                        hire_date=datetime.date(2024, 1, 1))
        self._payout(advance=None, amount='900.00', employee=other)
        res = find_mismatches()
        self.assertEqual(res['count'], 2)
        self.assertEqual(res['value'], Decimal('2400.00'))

    def test_one_advance_is_never_counted_in_two_lists(self):
        """A recovery raised with no payout is ALSO an advance approved and not
        paid. It is one problem, not two — count it twice and the headline
        'value in question' is double what is in question."""
        adv = self._advance(amount='1500.00')
        self._recovery_amendment(adv)
        res = find_mismatches()
        self.assertEqual(len(res['recovery_without_payout']), 1)
        self.assertEqual(len(res['approved_not_paid']), 0)
        self.assertEqual(res['count'], 1)
        self.assertEqual(res['value'], Decimal('1500.00'))

    def test_it_only_reads(self):
        """No row is created, changed or deleted by looking."""
        adv = self._advance()
        self._recovery_amendment(adv)
        before = (SalaryAdvance.objects.count(),
                  SalaryAdvancePayout.objects.count(),
                  PayrollAmendment.objects.count())
        find_mismatches()
        self.assertEqual(before, (SalaryAdvance.objects.count(),
                                  SalaryAdvancePayout.objects.count(),
                                  PayrollAmendment.objects.count()))


class MismatchReportCommandTests(AdvanceMismatchBase):

    def _recipients(self):
        ReportRecipient.objects.create(report_slug='salary-advance-mismatch',
                                       email='finance@alphadirect.co.bw',
                                       kind=ReportRecipient.Kind.TO)

    def test_it_refuses_to_send_when_it_has_nothing_to_say(self):
        """A weekly all-clear is read for a month and then never read again."""
        self._recipients()
        out = StringIO()
        with mock.patch(SEND_PATH) as send:
            call_command('send_advance_mismatch_report', stdout=out)
        send.assert_not_called()
        self.assertIn('Nothing to report', out.getvalue())

    def test_it_sends_when_something_disagrees(self):
        self._recipients()
        self._advance(amount='1500.00')
        out = StringIO()
        with mock.patch(SEND_PATH, return_value=True) as send:
            call_command('send_advance_mismatch_report', stdout=out)
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs['to'], ['finance@alphadirect.co.bw'])
        self.assertIn('Alice A', kwargs['html'])
        self.assertEqual(len(kwargs['attachments']), 1)
        self.assertTrue(kwargs['attachments'][0][0].endswith('.xlsx'))

    def test_it_stops_rather_than_send_to_nobody(self):
        self._advance(amount='1500.00')
        with mock.patch(SEND_PATH) as send:
            with self.assertRaises(SystemExit):
                call_command('send_advance_mismatch_report', stderr=StringIO(),
                             stdout=StringIO())
        send.assert_not_called()

    def test_dry_run_sends_nothing(self):
        self._recipients()
        self._advance(amount='1500.00')
        out = StringIO()
        with mock.patch(SEND_PATH) as send:
            call_command('send_advance_mismatch_report', '--dry-run', stdout=out)
        send.assert_not_called()
        self.assertIn('DRY RUN', out.getvalue())
