"""Approved incentives auto-flow into a PENDING payroll batch (CFO 2026-08-28).

Money-safety focus: real employee only (no name guessing), amounts SUM per
person (never overwrite), idempotent (no double-count), batch never auto-applied.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Company
from hris.incentive_models import IncentiveLine, IncentiveRequest
from hris.incentive_payroll_feed import feed_period_company, feed_for_request
from hris import incentive_service
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollPeriod, PayslipComponent)
from payroll.period_guard import current_period_label


def _next_month_start(d: datetime.date) -> datetime.date:
    return (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)

# The feed may only write into the CURRENT month (CFO 16-Sep-2026: "it only
# works for the month of september, no going backward"), so the month under
# test moves with the clock instead of being pinned to one that will be in the
# past by the time anyone reads this. payroll.period_guard owns the definition
# of "current", in Gaborone time — asking it is what keeps the test and the
# rule from drifting apart. Back-dating is covered in
# payroll/tests/test_feed_period_guard.py.
PERIOD = current_period_label()
_THIS = datetime.date(int(PERIOD[:4]), int(PERIOD[5:]), 1)
_PREV_END = _THIS - datetime.timedelta(days=1)
_PREV = _PREV_END.replace(day=1)


class FeedTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.other = Company.objects.create(code='OTHER', name='Other Co.')
        PayrollPeriod.objects.create(period_name=_PREV.strftime('%Y-%m'),
            start_date=_PREV, end_date=_PREV_END)
        cls.target = PayrollPeriod.objects.create(period_name=PERIOD,
            start_date=_THIS, end_date=_next_month_start(_THIS) - datetime.timedelta(days=1))
        cls.empA = Employee.objects.create(employee_number='E1', full_name='Alice A', company=cls.co)
        cls.empB = Employee.objects.create(employee_number='E2', full_name='Bob B', company=cls.co)
        cls.empC = Employee.objects.create(employee_number='E3', full_name='Carol C', company=cls.other)
        cls.maker = User.objects.create_user('mgr', 'mgr@alphadirect.co.bw', 'x')

    def _approved_request(self, company=None, period=PERIOD, lines=None):
        req = IncentiveRequest.objects.create(
            title='Incentives', period=period, company=company or self.co,
            maker=self.maker, status=IncentiveRequest.Status.APPROVED)
        for emp, amt in (lines or []):
            IncentiveLine.objects.create(request=req, employee=emp,
                name=(emp.full_name if emp else 'category'), amount=Decimal(amt))
        return req


class FeedLogicTests(FeedTestBase):
    def test_creates_pending_batch_summed_per_employee(self):
        # Alice gets two lines (100 + 50) → must SUM to 150; Bob gets 200.
        self._approved_request(lines=[(self.empA, '100.00'), (self.empA, '50.00'),
                                      (self.empB, '200.00')])
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'pushed')
        self.assertEqual(res['pushed'], 2)                       # 2 employees, not 3 lines
        batch = PayrollAmendmentBatch.objects.get(pk=res['batch_id'])
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.PARSED)  # NEVER applied
        self.assertEqual(batch.company, self.co)
        rows = {a.employee_id: a for a in batch.amendments.all()}
        self.assertEqual(rows[self.empA.pk].amount, Decimal('150.00'))  # summed, not overwritten
        self.assertEqual(rows[self.empB.pk].amount, Decimal('200.00'))
        a = rows[self.empA.pk]
        self.assertEqual(a.kind, PayrollAmendment.Kind.ALLOWANCE_ADD)
        self.assertEqual(a.component.code, 'INCENTIVE')
        self.assertTrue(a.component.is_taxable)
        self.assertFalse(a.applied)
        self.assertEqual(a.resolution_error, '')

    def test_idempotent_rerun_no_double(self):
        self._approved_request(lines=[(self.empA, '100.00')])
        r1 = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        r2 = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(PayrollAmendmentBatch.objects.filter(
            target_period=self.target, company=self.co, file_name='AUTO-INCENTIVE').count(), 1)
        batch = PayrollAmendmentBatch.objects.get(pk=r2['batch_id'])
        self.assertEqual(batch.amendments.count(), 1)
        self.assertEqual(batch.amendments.first().amount, Decimal('100.00'))
        self.assertEqual(r1['batch_id'], r2['batch_id'])

    def test_second_request_same_month_adds_to_same_batch_and_sums(self):
        self._approved_request(lines=[(self.empA, '100.00')])
        feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self._approved_request(lines=[(self.empA, '25.00')])   # 2nd approved request, same person
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        batch = PayrollAmendmentBatch.objects.get(pk=res['batch_id'])
        self.assertEqual(batch.amendments.count(), 1)
        self.assertEqual(batch.amendments.first().amount, Decimal('125.00'))  # 100 + 25

    def test_line_without_employee_skipped_and_reported(self):
        req = IncentiveRequest.objects.create(title='x', period=PERIOD, company=self.co,
            maker=self.maker, status=IncentiveRequest.Status.APPROVED)
        IncentiveLine.objects.create(request=req, employee=None, name='a category', amount=Decimal('99'))
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'nothing_to_push')
        self.assertEqual(res['skipped'], 1)
        self.assertFalse(PayrollAmendmentBatch.objects.filter(
            target_period=self.target, company=self.co, file_name='AUTO-INCENTIVE').exists())

    def test_cross_entity_employee_skipped(self):
        # Carol (OTHER company) on a TEST-company request → never pushed to TEST payroll.
        self._approved_request(company=self.co, lines=[(self.empC, '500.00')])
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'nothing_to_push')
        self.assertEqual(res['skipped'], 1)

    def test_no_payroll_period_yet(self):
        # A month Finance has not opened yet. Far future, so it can never
        # collide with the current month the rest of this file uses.
        unopened = '2099-01'
        self._approved_request(period=unopened, lines=[(self.empA, '100.00')])
        res = feed_period_company(period_label=unopened, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'no_period')
        self.assertEqual(res['pushed'], 0)

    def test_only_approved_lines_pushed(self):
        pending = IncentiveRequest.objects.create(title='p', period=PERIOD, company=self.co,
            maker=self.maker, status=IncentiveRequest.Status.PENDING)
        IncentiveLine.objects.create(request=pending, employee=self.empA, name='Alice A', amount=Decimal('999'))
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'nothing_to_push')   # pending is not pushed


    def test_manually_processed_request_not_re_fed(self):
        # A request Finance already keyed into payroll (payroll_processed) must
        # NOT be auto-fed again (double-pay guard).
        req = self._approved_request(lines=[(self.empA, '100.00')])
        req.payroll_processed = True
        req.save(update_fields=['payroll_processed'])
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'nothing_to_push')


    def test_fed_then_processed_stays_in_sum(self):
        # Feed A(100); Finance ticks A processed (it IS in payroll now); approve
        # B(25) same person/month; re-feed → sum must stay 125, not drop to 25.
        reqA = self._approved_request(lines=[(self.empA, '100.00')])
        feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        reqA.payroll_processed = True
        reqA.save(update_fields=['payroll_processed'])
        self._approved_request(lines=[(self.empA, '25.00')])
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        batch = PayrollAmendmentBatch.objects.get(pk=res['batch_id'])
        self.assertEqual(batch.amendments.get(employee=self.empA).amount, Decimal('125.00'))

    def test_manually_keyed_request_still_excluded(self):
        # A request Finance keyed manually (processed, NEVER auto-fed) stays out.
        req = self._approved_request(lines=[(self.empA, '77.00')])
        req.payroll_processed = True
        req.save(update_fields=['payroll_processed'])
        res = feed_period_company(period_label=PERIOD, company=self.co, user=self.maker)
        self.assertEqual(res['status'], 'nothing_to_push')


class ApproveHookTests(FeedTestBase):
    def test_full_approval_triggers_autofeed(self):
        cfo = User.objects.create_user('cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        hr = User.objects.create_user('hr', 'ubutale@alphadirect.co.bw', 'x')
        req = IncentiveRequest.objects.create(title='Motor', period=PERIOD, company=self.co,
            maker=self.maker, status=IncentiveRequest.Status.PENDING)
        IncentiveLine.objects.create(request=req, employee=self.empA, name='Alice A', amount=Decimal('300'))
        incentive_service.approve_request(req, cfo)
        req.refresh_from_db()
        # not yet fully signed → no batch
        self.assertFalse(PayrollAmendmentBatch.objects.filter(
            target_period=self.target, file_name='AUTO-INCENTIVE').exists())
        incentive_service.approve_request(req, hr)
        req.refresh_from_db()
        self.assertEqual(req.status, IncentiveRequest.Status.APPROVED)
        batch = PayrollAmendmentBatch.objects.get(target_period=self.target,
            company=self.co, file_name='AUTO-INCENTIVE')
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.PARSED)
        amd = batch.amendments.get(employee=self.empA)
        self.assertEqual(amd.amount, Decimal('300'))
        self.assertEqual(IncentiveLine.objects.get(request=req).payroll_amendment_id, amd.pk)
