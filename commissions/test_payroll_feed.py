"""Approved payroll-group commissions auto-feed a PENDING payroll batch
(CFO 2026-08-28, Phase 1b). Money-safety: payroll-group only, email-exact
employee match, sum per person, idempotent, never auto-applied.

Run in CI: manage.py test commissions.test_payroll_feed
"""
import datetime
from decimal import Decimal

from django.test import TestCase

from core.models import Company
from commissions.models import CommissionGroup, CommissionAgent, CommissionSubmission
from commissions.payroll_feed import feed_period
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollPeriod)
from payroll.period_guard import current_period_label


def _next_month_start(d: datetime.date) -> datetime.date:
    return (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


# The feed may only write into the CURRENT month (CFO 16-Sep-2026: "it only
# works for the month of september, no going backward"), so the month under
# test follows the clock rather than being pinned to one that is already past.
# payroll.period_guard owns the definition of "current", in Gaborone time.
# Back-dating is covered in payroll/tests/test_feed_period_guard.py.
PERIOD = current_period_label()
_THIS = datetime.date(int(PERIOD[:4]), int(PERIOD[5:]), 1)
_PREV_END = _THIS - datetime.timedelta(days=1)
_PREV = _PREV_END.replace(day=1)


class CommFeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        PayrollPeriod.objects.create(period_name=_PREV.strftime('%Y-%m'),
            start_date=_PREV, end_date=_PREV_END)
        cls.target = PayrollPeriod.objects.create(period_name=PERIOD,
            start_date=_THIS,
            end_date=_next_month_start(_THIS) - datetime.timedelta(days=1))
        cls.emp = Employee.objects.create(employee_number='E1', full_name='Alice A',
            email='alice@alphadirect.co.bw', company=cls.co)
        cls.gp, _ = CommissionGroup.objects.update_or_create(
            key=CommissionGroup.Key.IN_HOUSE,
            defaults=dict(name='Payroll agents', pays_via=CommissionGroup.PaysVia.PAYROLL))
        cls.gp2, _ = CommissionGroup.objects.update_or_create(
            key=CommissionGroup.Key.BDU,
            defaults=dict(name='BDU payroll', pays_via=CommissionGroup.PaysVia.PAYROLL))
        cls.gd, _ = CommissionGroup.objects.update_or_create(
            key=CommissionGroup.Key.INDEPENDENT,
            defaults=dict(name='Independent', pays_via=CommissionGroup.PaysVia.DIRECT_BANK,
                          withholding_rate=Decimal('0.10')))

    def _sub(self, group, agent, net, status=CommissionSubmission.Status.APPROVED, period=PERIOD):
        return CommissionSubmission.objects.create(agent=agent, group=group,
            period_label=period, status=status,
            gross_commission=Decimal(net), net_payable=Decimal(net))

    def _agent(self, name, email, group):
        return CommissionAgent.objects.create(name=name, email=email, group=group)

    def test_payroll_group_feeds_matched_employee(self):
        a = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a, '900.00')
        res = feed_period(period_label=PERIOD, user=None)
        self.assertEqual(res['status'], 'pushed')
        batch = PayrollAmendmentBatch.objects.get(target_period=self.target, company=self.co,
                                                   file_name='AUTO-COMMISSION')
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.PARSED)  # never applied
        amd = batch.amendments.get(employee=self.emp)
        self.assertEqual(amd.amount, Decimal('900.00'))
        self.assertEqual(amd.component.code, 'COMMISSION')
        self.assertTrue(amd.component.is_taxable)

    def test_independent_group_not_fed(self):
        a = self._agent('Ext Agent', 'alice@alphadirect.co.bw', self.gd)  # even w/ matching email
        self._sub(self.gd, a, '5000.00')
        res = feed_period(period_label=PERIOD, user=None)
        self.assertEqual(res['status'], 'nothing_to_push')
        self.assertFalse(PayrollAmendmentBatch.objects.filter(file_name='AUTO-COMMISSION').exists())

    def test_no_email_match_skipped_not_guessed(self):
        a = self._agent('Ghost', 'nobody@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a, '300.00')
        res = feed_period(period_label=PERIOD, user=None)
        self.assertEqual(res['status'], 'nothing_to_push')
        self.assertEqual(res['skipped'], 1)

    def test_two_payroll_subs_same_person_sum(self):
        a1 = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        a2 = self._agent('Alice Second Code', 'alice@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a1, '400.00')
        self._sub(self.gp, a2, '250.00')
        feed_period(period_label=PERIOD, user=None)
        amd = PayrollAmendment.objects.get(employee=self.emp, component__code='COMMISSION')
        self.assertEqual(amd.amount, Decimal('650.00'))

    def test_idempotent_rerun(self):
        a = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a, '900.00')
        feed_period(period_label=PERIOD, user=None)
        feed_period(period_label=PERIOD, user=None)
        self.assertEqual(PayrollAmendmentBatch.objects.filter(file_name='AUTO-COMMISSION').count(), 1)
        self.assertEqual(PayrollAmendment.objects.filter(
            employee=self.emp, component__code='COMMISSION').count(), 1)

    def test_manually_processed_excluded_but_fed_kept(self):
        a = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        s1 = self._sub(self.gp, a, '900.00')
        feed_period(period_label=PERIOD, user=None)
        s1.status = CommissionSubmission.Status.PAID   # Finance marked it processed
        s1.save(update_fields=['status'])
        a2 = self._agent('Alice C2', 'alice@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a2, '100.00')
        feed_period(period_label=PERIOD, user=None)
        amd = PayrollAmendment.objects.get(employee=self.emp, component__code='COMMISSION')
        self.assertEqual(amd.amount, Decimal('1000.00'))  # fed s1 stays in sum + new 100

    def test_only_approved_fed(self):
        a = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        self._sub(self.gp, a, '900.00', status=CommissionSubmission.Status.FINAL_REVIEW)
        res = feed_period(period_label=PERIOD, user=None)
        self.assertEqual(res['status'], 'nothing_to_push')

    def test_same_person_two_payroll_groups_sums(self):
        # A person earning in TWO payroll groups must SUM, not overwrite (Fable fix 1).
        a1 = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        a2 = self._agent('Alice BDU', 'alice@alphadirect.co.bw', self.gp2)
        s1 = self._sub(self.gp, a1, '500.00')
        self._sub(self.gp2, a2, '300.00')
        # simulate the approval hook feeding the whole month for the first group
        from commissions.payroll_feed import feed_submission
        feed_submission(s1, user=None)
        amd = PayrollAmendment.objects.get(employee=self.emp, component__code='COMMISSION')
        self.assertEqual(amd.amount, Decimal('800.00'))

    def test_rejected_auto_batch_not_re_fed(self):
        # Fed → close REJECTS the batch → Finance pays manually (sub PAID) →
        # a later feed must NOT re-insert the paid sub (Fable fix 2, double-pay).
        a = self._agent('Alice A', 'alice@alphadirect.co.bw', self.gp)
        s1 = self._sub(self.gp, a, '900.00')
        feed_period(period_label=PERIOD, user=None)
        b = PayrollAmendmentBatch.objects.get(file_name='AUTO-COMMISSION')
        b.status = PayrollAmendmentBatch.Status.REJECTED
        b.save(update_fields=['status'])
        s1.status = CommissionSubmission.Status.PAID   # manual fallback pay
        s1.save(update_fields=['status'])
        res = feed_period(period_label=PERIOD, user=None)
        # nothing new to push; the paid+rejected sub is excluded
        self.assertEqual(res['status'], 'nothing_to_push')
