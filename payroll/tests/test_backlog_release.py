"""Finance releases an earlier month on purpose — and cannot pay it twice.

CFO, payroll.docx §5 (16-Sep-2026): *"Reconcile these against amounts already
paid before any backfill. Never automatically feed the backlog… Provide a
Finance-controlled reconciliation/backfill process with preview, duplicate
detection, period selection, approval and audit trail."*

The "never automatically" half is proved in test_feed_period_guard.py. This
file proves the other half: that there IS a deliberate way through, that it
lands in the month that can still be paid, that it refuses a line which has
already been through payroll, and that only Finance can work it.

RED-PROOF: delete the `held` refusal in `backfill_service.release()` and
`test_a_duplicate_is_refused_even_when_asked_for` goes red — the line is
released a second time and the person is paid twice.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import AuditLog
from hris.incentive_models import IncentiveLine, IncentiveRequest
from hris.incentive_payroll_feed import feed_period_company as incentive_feed
from payroll import backfill_service
from payroll.models import PayrollAmendment, PayrollPeriod
from payroll.tests.test_feed_period_guard import (GuardFixture, LAST_MONTH,
                                                  THIS_MONTH, _LAST)


class BacklogPreviewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_preview_lists_last_months_approved_incentives(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        shot = backfill_service.preview(source_period=LAST_MONTH, kind='incentive')
        self.assertEqual(shot['source_period'], LAST_MONTH)
        self.assertEqual(shot['target_period'], THIS_MONTH,
                         'a release must land in the month that can still be '
                         'paid, not the one already posted')
        self.assertEqual(len(shot['rows']), 1)
        row = shot['rows'][0]
        self.assertEqual(row['employee_name'], 'Alice A')
        self.assertEqual(row['amount'], '250.00')
        self.assertTrue(row['releasable'])
        self.assertEqual(shot['releasable_total'], '250.00')

    def test_preview_writes_nothing(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        backfill_service.preview(source_period=LAST_MONTH, kind='incentive')
        backfill_service.preview(source_period=LAST_MONTH, kind='incentive')
        self.assertEqual(PayrollAmendment.objects.count(), 0)

    def test_a_line_already_through_payroll_is_held(self):
        req = GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                             employee=self.emp, period=LAST_MONTH)
        # Simulate the line having been fed once already, the way the live rows
        # from June–August look: the source line points at an amendment.
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        amd = PayrollAmendment.objects.get(employee=self.emp)
        IncentiveLine.objects.filter(request=req).update(payroll_amendment=amd)

        shot = backfill_service.preview(source_period=LAST_MONTH, kind='incentive')
        row = shot['rows'][0]
        self.assertFalse(row['releasable'])
        self.assertIn('pay it twice', row['duplicate_reason'])
        self.assertEqual(shot['held_count'], 1)
        self.assertEqual(shot['releasable_count'], 0)

    def test_a_line_finance_settled_by_hand_is_held(self):
        req = GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                             employee=self.emp, period=LAST_MONTH)
        IncentiveRequest.objects.filter(pk=req.pk).update(payroll_processed=True)
        row = backfill_service.preview(source_period=LAST_MONTH,
                                       kind='incentive')['rows'][0]
        self.assertFalse(row['releasable'])
        self.assertIn('settled this by hand', row['duplicate_reason'])


class BacklogReleaseTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_release_raises_the_amendment_in_the_current_month(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        out = backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                       employee_ids=[str(self.emp.pk)],
                                       user=self.maker)
        self.assertEqual(out['status'], 'released')
        self.assertEqual(out['total'], '250.00')

        amd = PayrollAmendment.objects.get(employee=self.emp)
        self.assertEqual(amd.batch.target_period, self.this)
        self.assertEqual(amd.amount, Decimal('250.00'))
        self.assertIn(LAST_MONTH, amd.reason)
        self.assertIn(THIS_MONTH, amd.reason)
        self.assertTrue(amd.feed_key, 'a released line needs the same no-double '
                                      'fingerprint the automatic feed writes')
        self.assertEqual(amd.batch.file_name, 'BACKLOG-RELEASE:INCENTIVE',
                         'a released line must be visible as a backlog line at '
                         'the close, not mixed into this month ordinary work')

    def test_release_is_audited(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)
        row = AuditLog.objects.filter(
            table_name='payroll_payrollamendment').order_by('-created_at').first()
        self.assertIsNotNone(row)
        self.assertEqual(row.new_values['source_period'], LAST_MONTH)
        self.assertEqual(row.new_values['target_period'], THIS_MONTH)
        self.assertEqual(row.user, self.maker)

    def test_a_duplicate_is_refused_even_when_asked_for(self):
        req = GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                             employee=self.emp, period=LAST_MONTH)
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        amd = PayrollAmendment.objects.get(employee=self.emp)
        IncentiveLine.objects.filter(request=req).update(payroll_amendment=amd)

        with self.assertRaises(backfill_service.ReleaseRefused) as caught:
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[str(self.emp.pk)],
                                     user=self.maker)
        self.assertIn('pay twice', str(caught.exception))

    def test_releasing_the_same_month_twice_is_refused(self):
        # This used to assert the second release was a harmless no-op. It is
        # now REFUSED outright, which is stronger: once F1 writes the "fed" flag
        # back on the source rows, the second attempt is a duplicate and the
        # preview says so. A silent no-op and an explicit refusal look the same
        # on the amendment table and completely different to the person.
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)
        with self.assertRaises(backfill_service.ReleaseRefused) as caught:
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[str(self.emp.pk)],
                                     user=self.maker)
        self.assertIn('pay twice', str(caught.exception))
        rows = PayrollAmendment.objects.filter(employee=self.emp)
        self.assertEqual(rows.count(), 1, 'a second release added a second row')
        self.assertEqual(rows.first().amount, Decimal('250.00'))

    def test_release_is_refused_once_this_month_is_closed(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        PayrollPeriod.objects.filter(pk=self.this.pk).update(
            status=PayrollPeriod.Status.POSTED)
        with self.assertRaises(backfill_service.ReleaseRefused) as caught:
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[str(self.emp.pk)],
                                     user=self.maker)
        self.assertIn('not Open', str(caught.exception))
        self.assertEqual(PayrollAmendment.objects.count(), 0)

    def test_nobody_selected_is_refused_rather_than_releasing_everything(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        with self.assertRaises(backfill_service.ReleaseRefused):
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[], user=self.maker)
        self.assertEqual(PayrollAmendment.objects.count(), 0)


class ReleaseCannotPayTwiceTests(TestCase):
    """The four ways a release could still pay somebody twice.

    All four were found by the Fable ship-gate on 16-Sep-2026, after the first
    version of this file passed 13 tests. Every one of them is silent — the
    screen would have looked right in each case.
    """

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_a_released_line_is_marked_as_fed_on_the_source_record(self):
        # F1. release() used to raise the amendment and stop, never writing
        # `payroll_amendment` back — the very flag the preview reads. June would
        # still have shown "still owed" straight afterwards, and next month it
        # would release again under a new fingerprint. Paid twice.
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)
        again = backfill_service.preview(source_period=LAST_MONTH, kind='incentive')
        self.assertFalse(again['rows'][0]['releasable'],
                         'a month just released still reads as still owed')
        self.assertEqual(again['releasable_count'], 0)
        with self.assertRaises(backfill_service.ReleaseRefused):
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[str(self.emp.pk)],
                                     user=self.maker)

    def test_a_release_does_not_collide_with_this_months_own_incentive(self):
        # F2. Applying a batch is update_or_create per (payslip, component) —
        # last wins. A release on the SAME component as the ordinary feed would
        # have replaced it, so Alice got 100 or 250, never both. Its own
        # component is what keeps them apart.
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH,
                                       amount='100.00')
        incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)

        by_component = {a.component.code: a.amount
                        for a in PayrollAmendment.objects.filter(employee=self.emp)}
        self.assertEqual(by_component.get('INCENTIVE'), Decimal('100.00'))
        self.assertEqual(by_component.get('INCENTIVE_ARREARS'), Decimal('250.00'))
        self.assertEqual(
            len(by_component), 2,
            'the released line shares a payslip component with this month, so '
            'applying the two batches would silently overwrite one of them')

    def test_two_released_months_sum_instead_of_overwriting(self):
        # Also F2: one amendment per person per target month, so the second
        # release must ADD to the first, not replace it.
        two_back_end = _LAST - datetime.timedelta(days=1)
        two_back = two_back_end.replace(day=1)
        label = two_back.strftime('%Y-%m')
        PayrollPeriod.objects.create(period_name=label,
                                     start_date=two_back, end_date=two_back_end)
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=label,
                                       amount='75.00')
        backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)
        backfill_service.release(source_period=label, kind='incentive',
                                 employee_ids=[str(self.emp.pk)], user=self.maker)
        rows = PayrollAmendment.objects.filter(
            employee=self.emp, component__code='INCENTIVE_ARREARS')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, Decimal('325.00'),
                         'the second release overwrote the first')

    def test_a_line_the_source_month_already_paid_is_held(self):
        # F6, and his own words: "Reconcile these against amounts already paid
        # before any backfill." A line Finance keyed by hand carries no link and
        # no processed flag — only the payslip knows it was paid.
        from payroll.models import Payslip, PayslipComponent, PayslipLine
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        comp, _ = PayslipComponent.objects.get_or_create(
            code='INCENTIVE', defaults={'name': 'Incentive',
                                        'kind': PayslipComponent.Kind.EARNING})
        slip = Payslip.objects.create(employee=self.emp, period=self.last)
        PayslipLine.objects.create(payslip=slip, component=comp,
                                   amount=Decimal('250.00'))

        row = backfill_service.preview(source_period=LAST_MONTH,
                                       kind='incentive')['rows'][0]
        self.assertFalse(row['releasable'])
        self.assertIn('already paid', row['duplicate_reason'])
        with self.assertRaises(backfill_service.ReleaseRefused):
            backfill_service.release(source_period=LAST_MONTH, kind='incentive',
                                     employee_ids=[str(self.emp.pk)],
                                     user=self.maker)

    def test_a_commission_nobody_approved_is_not_offered(self):
        # F3. `.exclude(REJECTED)` kept DRAFT / SUBMITTED / SECOND_REVIEW /
        # FINAL_REVIEW, so the release was LOOSER than the automatic feed it
        # stands in for and offered unapproved money as "still owed".
        from commissions.models import CommissionSubmission
        sub = GuardFixture.approve_commission(employee=self.emp, period=LAST_MONTH)
        CommissionSubmission.objects.filter(pk=sub.pk).update(
            status=CommissionSubmission.Status.SUBMITTED)
        shot = backfill_service.preview(source_period=LAST_MONTH, kind='commission')
        self.assertEqual(shot['rows'], [],
                         'a commission still in review was offered for release')


class BacklogPermissionTests(TestCase):
    """Only Finance. An ordinary signed-in user cannot even look."""

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)
        cls.outsider = User.objects.create_user(
            'nobody', 'nobody@alphadirect.co.bw', 'x')
        cls.finance = User.objects.create_user(
            'pkago', 'pkago@alphadirect.co.bw', 'x')

    def setUp(self):
        self.api = APIClient()

    def test_an_ordinary_user_cannot_preview_or_release(self):
        self.api.force_authenticate(self.outsider)
        self.assertEqual(
            self.api.get('/api/v1/payroll/backlog/',
                         {'period': LAST_MONTH, 'kind': 'incentive'}).status_code, 403)
        self.assertEqual(
            self.api.post('/api/v1/payroll/backlog/release/',
                          {'period': LAST_MONTH, 'kind': 'incentive',
                           'employee_ids': [str(self.emp.pk)]},
                          format='json').status_code, 403)
        self.assertEqual(PayrollAmendment.objects.count(), 0)

    def test_finance_can_preview_and_release(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH,
                                       amount='250.00')
        self.api.force_authenticate(self.finance)
        shot = self.api.get('/api/v1/payroll/backlog/',
                            {'period': LAST_MONTH, 'kind': 'incentive'})
        self.assertEqual(shot.status_code, 200)
        self.assertEqual(shot.data['releasable_count'], 1)

        done = self.api.post('/api/v1/payroll/backlog/release/',
                             {'period': LAST_MONTH, 'kind': 'incentive',
                              'employee_ids': [str(self.emp.pk)]}, format='json')
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.data['count'], 1)
        self.assertEqual(PayrollAmendment.objects.count(), 1)

    def test_an_unknown_month_is_an_empty_preview_not_an_error(self):
        self.api.force_authenticate(self.finance)
        res = self.api.get('/api/v1/payroll/backlog/',
                           {'period': '2019-01', 'kind': 'commission'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['rows'], [])
