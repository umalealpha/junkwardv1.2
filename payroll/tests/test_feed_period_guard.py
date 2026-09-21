"""The two controls the CFO asked for on 16-Sep-2026 (payroll.docx §2 and §3).

§2 FEED IDEMPOTENCY — *"two simultaneous feed attempts for the same approved
incentive or commission must create one payroll amendment only; retries must
return/reuse the existing amendment and never duplicate payment."*

§3 LOCK-PERIOD PROTECTION — *"all incentive, commission, loan and direct
approval/feed hooks must refuse writes when the target payroll period is
LOCKED, SIGNED, POSTED or otherwise closed"* — plus his clarification the same
afternoon, which is the stricter half: *"it only works for the month of
september, no going backward."*

WHY THESE ARE NOT ALREADY COVERED. `PayrollAmendment.feed_key` has been UNIQUE
at the database level since 13-Sep, and `payroll/feed_common.py` has written it
since then — but the incentive and commission feeds never called any of it.
Both did a bare `update_or_create` on the field tuple, so the race-safety their
own docstrings promised was not in place, and neither looked at
`PayrollPeriod.status` at all. A Python check-then-write is not a control: two
approvals landing together, or a retry inside a race, both pass it.

RED-PROOF (run before trusting any of this):
  * Revert `hris/incentive_payroll_feed.py` and `commissions/payroll_feed.py` to
    their bare `PayrollAmendment.objects.update_or_create(...)` and every test
    in `FeedKeyTests` goes red on a NULL feed_key.
  * Drop the `period_guard.ensure_writable(...)` call from
    `payroll.feed_common.canonical_batch` and every test in `BackDatingTests`
    and `ClosedPeriodTests` goes red — the amendment is written into a month it
    must not touch.
"""
from __future__ import annotations

import datetime
import threading
from unittest import mock
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, connection, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from commissions.models import (CommissionAgent, CommissionGroup,
                                CommissionSubmission)
from commissions.payroll_feed import feed_period as commission_feed_period
from core.models import AuditLog, Company, Currency
from hris.incentive_models import IncentiveLine, IncentiveRequest
from hris.incentive_payroll_feed import feed_period_company as incentive_feed
from payroll import feed_common, period_guard
from payroll.models import (Employee, PayrollAmendment, PayrollPeriod)
from payroll.period_guard import current_period_label

THIS_MONTH = current_period_label()
_THIS = datetime.date(int(THIS_MONTH[:4]), int(THIS_MONTH[5:]), 1)
_LAST_END = _THIS - datetime.timedelta(days=1)
_LAST = _LAST_END.replace(day=1)
LAST_MONTH = _LAST.strftime('%Y-%m')


def _end_of(first: datetime.date) -> datetime.date:
    return (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) \
        - datetime.timedelta(days=1)


class GuardFixture:
    """Two consecutive real months and one employee — the smallest world in
    which "this month" and "an earlier month" are both real."""

    @classmethod
    def build(cls, target):
        # Company.base_currency defaults to 'BWP', which arrives from a data
        # migration. A TransactionTestCase truncates the tables it touches, so
        # the fixture seeds it rather than assuming the migration's row is still
        # there — otherwise the concurrency class leaves every later test in the
        # same database failing on a foreign key, which is a false red.
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        target.co = Company.objects.create(code='TEST', name='Test Co.')
        target.last = PayrollPeriod.objects.create(
            period_name=LAST_MONTH, start_date=_LAST, end_date=_LAST_END)
        target.this = PayrollPeriod.objects.create(
            period_name=THIS_MONTH, start_date=_THIS, end_date=_end_of(_THIS))
        target.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A',
            email='alice@alphadirect.co.bw', company=target.co)
        target.maker = User.objects.create_user('mgr', 'mgr@alphadirect.co.bw', 'x')

    @staticmethod
    def approve_incentive(*, company, maker, employee, period, amount='100.00'):
        req = IncentiveRequest.objects.create(
            title='Incentives', period=period, company=company, maker=maker,
            status=IncentiveRequest.Status.APPROVED)
        IncentiveLine.objects.create(request=req, employee=employee,
                                     name=employee.full_name,
                                     amount=Decimal(amount))
        return req

    @staticmethod
    def approve_commission(*, employee, period, net='500.00'):
        group, _ = CommissionGroup.objects.update_or_create(
            key=CommissionGroup.Key.IN_HOUSE,
            defaults={'name': 'In house', 'pays_via': CommissionGroup.PaysVia.PAYROLL})
        agent, _ = CommissionAgent.objects.get_or_create(
            name=employee.full_name, group=group,
            defaults={'email': employee.email})
        return CommissionSubmission.objects.create(
            group=group, agent=agent, period_label=period,
            gross_commission=Decimal(net), net_payable=Decimal(net),
            status=CommissionSubmission.Status.APPROVED)


class BackDatingTests(TestCase):
    """"No going backward." An approval made today never reaches last month."""

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_incentive_approved_for_an_earlier_month_is_refused(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        res = incentive_feed(period_label=LAST_MONTH, company=self.co,
                             user=self.maker)
        self.assertEqual(res['status'], 'period_closed')
        self.assertIn(THIS_MONTH, res['detail'])
        self.assertEqual(
            PayrollAmendment.objects.filter(batch__target_period=self.last).count(), 0,
            'a back-dated incentive reached an earlier payroll month')

    def test_commission_approved_for_an_earlier_month_is_refused(self):
        GuardFixture.approve_commission(employee=self.emp, period=LAST_MONTH)
        res = commission_feed_period(period_label=LAST_MONTH, user=self.maker)
        self.assertEqual(res['status'], 'period_closed')
        self.assertEqual(
            PayrollAmendment.objects.filter(batch__target_period=self.last).count(), 0,
            'a back-dated commission reached an earlier payroll month')

    def test_the_current_month_still_feeds(self):
        # The control must stop the backlog WITHOUT stopping this month's work —
        # a guard that blocks everything would read as "payroll is broken".
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        res = incentive_feed(period_label=THIS_MONTH, company=self.co,
                             user=self.maker)
        self.assertEqual(res['status'], 'pushed')
        self.assertEqual(res['pushed'], 1)

    def test_the_refusal_is_audited_not_silent(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=LAST_MONTH)
        incentive_feed(period_label=LAST_MONTH, company=self.co, user=self.maker)
        row = (AuditLog.objects
               .filter(table_name='payroll_payrollperiod',
                       record_id=str(self.last.pk))
               .order_by('-created_at').first())
        self.assertIsNotNone(row, 'a refused feed left no audit trail')
        self.assertTrue(row.description.startswith('REFUSED:'))
        self.assertEqual(row.new_values['source'], 'AUTO-INCENTIVE')
        self.assertEqual(row.new_values['target_period'], LAST_MONTH)
        self.assertEqual(row.new_values['current_month'], THIS_MONTH)


class RefusalAuditSurvivesTests(TestCase):
    """The audit row must survive the rollback the refusal itself causes.

    DeepSeek caught this in the 16-Sep-2026 ship-gate, voting FAIL against an
    otherwise-passing panel: `ensure_writable` used to write the AuditLog
    itself, and it runs INSIDE the feed's `transaction.atomic()`. The moment
    `PeriodClosed` left that block Django rolled the transaction back and took
    the audit row with it — so a refused feed recorded absolutely nothing.

    `BackDatingTests.test_the_refusal_is_audited_not_silent` stayed green
    throughout, because it only ever exercised the EARLY check, which runs
    before the transaction opens. It proved the symptom, not the mechanism.
    This test drives the BACKSTOP: the month is closed after the early check
    has already passed, which is exactly the race the backstop exists for.

    RED-PROOF: move `record_refusal` back inside `ensure_writable` and this
    goes red while every other test in this file stays green.
    """

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_the_backstop_refusal_is_still_audited_after_the_rollback(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        before = AuditLog.objects.filter(
            table_name='payroll_payrollperiod').count()

        # The month closes between the early check and the batch write — the
        # only thing the backstop is there to catch.
        real_refusal = period_guard.refusal
        state = {'first': True}

        def closes_underneath(period, *, source, current_month_only=False):
            if state['first']:
                state['first'] = False
                return ''                      # early check passes
            return real_refusal(period, source=source,
                                current_month_only=current_month_only) or (
                f'{source} cannot write into {period.period_name}: the month is '
                'Locked, not Open.')

        with mock.patch.object(period_guard, 'refusal', closes_underneath):
            res = incentive_feed(period_label=THIS_MONTH, company=self.co,
                                 user=self.maker)

        self.assertEqual(res['status'], 'period_closed')
        self.assertEqual(PayrollAmendment.objects.count(), 0,
                         'the batch was rolled back, as it must be')
        self.assertEqual(
            AuditLog.objects.filter(table_name='payroll_payrollperiod').count(),
            before + 1,
            'the refusal rolled back with the batch and left NO audit row — a '
            'refused feed that records nothing is a silent control')


class ClosedPeriodTests(TestCase):
    """Once Finance closes the month, nothing automatic slips in behind them."""

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def _feed_with_status(self, status):
        PayrollPeriod.objects.filter(pk=self.this.pk).update(status=status)
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        return incentive_feed(period_label=THIS_MONTH, company=self.co,
                              user=self.maker)

    def test_every_non_open_status_refuses_the_write(self):
        # LOCKED / APPROVED / POSTED / PAID. There is no 'SIGNED' status in this
        # model — the CFO's word for the state after Finance has calculated and
        # signed off is LOCKED, and APPROVED is the one after it.
        for status in (PayrollPeriod.Status.LOCKED, PayrollPeriod.Status.APPROVED,
                       PayrollPeriod.Status.POSTED, PayrollPeriod.Status.PAID):
            with self.subTest(status=status):
                PayrollAmendment.objects.all().delete()
                IncentiveRequest.objects.all().delete()
                res = self._feed_with_status(status)
                self.assertEqual(res['status'], 'period_closed')
                self.assertEqual(PayrollAmendment.objects.count(), 0)
                self.assertIn('not Open', res['detail'])

    def test_an_open_month_is_untouched_by_the_guard(self):
        res = self._feed_with_status(PayrollPeriod.Status.OPEN)
        self.assertEqual(res['status'], 'pushed')


class FeedKeyTests(TestCase):
    """The fingerprint the database holds unique — written, and unrepeatable."""

    @classmethod
    def setUpTestData(cls):
        GuardFixture.build(cls)

    def test_incentive_row_carries_the_deterministic_fingerprint(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        amd = PayrollAmendment.objects.get(employee=self.emp)
        self.assertTrue(amd.feed_key,
                        'the incentive feed wrote no feed_key — the unique index '
                        'it relies on is over a NULL and guards nothing')
        self.assertEqual(
            amd.feed_key,
            feed_common.make_feed_key('AUTO-INCENTIVE', THIS_MONTH,
                                      self.co.pk, self.emp.pk, 'INCENTIVE', ''))

    def test_commission_row_carries_the_deterministic_fingerprint(self):
        GuardFixture.approve_commission(employee=self.emp, period=THIS_MONTH)
        commission_feed_period(period_label=THIS_MONTH, user=self.maker)
        amd = PayrollAmendment.objects.get(employee=self.emp)
        self.assertTrue(amd.feed_key, 'the commission feed wrote no feed_key')
        self.assertEqual(
            amd.feed_key,
            feed_common.make_feed_key('AUTO-COMMISSION', THIS_MONTH,
                                      self.co.pk, self.emp.pk, 'COMMISSION', ''))

    def test_a_second_row_with_the_same_fingerprint_is_refused_by_the_database(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        existing = PayrollAmendment.objects.get(employee=self.emp)
        with self.assertRaises(IntegrityError):
            PayrollAmendment.objects.create(
                batch=existing.batch, employee=self.emp, kind=existing.kind,
                component=existing.component, amount=Decimal('999.00'),
                feed_key=existing.feed_key)

    def test_a_retry_reuses_the_amendment_instead_of_adding_one(self):
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)
        first = incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        second = incentive_feed(period_label=THIS_MONTH, company=self.co, user=self.maker)
        self.assertEqual(first['batch_id'], second['batch_id'])
        self.assertEqual(
            PayrollAmendment.objects.filter(employee=self.emp).count(), 1,
            'a retry created a second amendment — that is a double payment')


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentFeedTests(TransactionTestCase):
    """The half a single-threaded test cannot prove.

    SQLite serialises writers, so a green test there says nothing about two
    Gunicorn workers approving the same incentive in the same instant. This runs
    the two feeds in real threads against the real database and asserts ONE
    amendment survives — which is the CFO's §2 in one sentence.
    """

    reset_sequences = True

    def setUp(self):
        GuardFixture.build(self)
        GuardFixture.approve_incentive(company=self.co, maker=self.maker,
                                       employee=self.emp, period=THIS_MONTH)

    def test_two_simultaneous_feeds_create_one_amendment(self):
        start = threading.Barrier(2)
        results: list = []

        def run():
            try:
                start.wait(timeout=10)
                results.append(incentive_feed(period_label=THIS_MONTH,
                                              company=self.co, user=self.maker))
            except Exception as exc:                      # recorded, not swallowed
                results.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        for r in results:
            self.assertNotIsInstance(
                r, Exception,
                f'a concurrent feed raised instead of de-duplicating: {r!r}')

        rows = PayrollAmendment.objects.filter(employee=self.emp,
                                               feed_key__isnull=False)
        self.assertEqual(
            rows.count(), 1,
            'two simultaneous feeds produced more than one amendment for the '
            'same person, month and payslip line — that is a double payment')
        self.assertEqual(rows.first().amount, Decimal('100.00'))

    def tearDown(self):
        connection.close()
