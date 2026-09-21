"""The three payroll feeds — B10 Leave Pay, B11 Severance Connect, B12 Early
Salary Advance — and the ONE proof that matters for all three: running a feed
twice cannot pay anybody twice, and the guard is the DATABASE's, not Python's.

Each feed has a `test_..._double_run_*` pair:
  * run the feed twice → still one batch, one row, the original amount;
  * then reach past the feed entirely and try to INSERT a second amendment
    carrying the same feed_key → the database refuses with IntegrityError.

The second half is the real proof. A Python check-then-write can be raced; a
unique index cannot.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from hris.leave_encash_models import LeaveEncashment
from hris.leave_pay_feed import AUTO_BATCH_MARKER as LEAVEPAY_MARKER
from hris.leave_pay_feed import feed_period as feed_leave_pay
from payroll import severance_feed
from payroll import salary_advance_service as advances
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollPeriod, Payslip, PayslipComponent, PayslipLine)
from payroll.salary_advance_models import SalaryAdvance

PREV = '2026-09'
PERIOD = '2026-10'
NEXT = '2026-11'


class FeedBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.prev = PayrollPeriod.objects.create(
            period_name=PREV, start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))
        cls.target = PayrollPeriod.objects.create(
            period_name=PERIOD, start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 31))
        cls.nxt = PayrollPeriod.objects.create(
            period_name=NEXT, start_date=datetime.date(2026, 11, 1),
            end_date=datetime.date(2026, 11, 30))
        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A', company=cls.co,
            hire_date=datetime.date(2024, 1, 1))
        cls.cfo = User.objects.create_user('cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.hr = User.objects.create_user('hr', 'ubutale@alphadirect.co.bw', 'x')

    # A timestamp inside the target period, in Botswana time.
    def _in_period(self, day=15):
        naive = datetime.datetime(2026, 10, day, 10, 0)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def _basic(self, employee, amount='9000.00'):
        """Give the employee a payslip carrying a BASIC line — the one-third
        cap and the leave valuation both read this."""
        comp, _ = PayslipComponent.objects.get_or_create(
            code='BASIC', defaults={'name': 'Basic', 'kind': PayslipComponent.Kind.EARNING,
                                    'is_taxable': True})
        slip = Payslip.objects.create(employee=employee, period=self.prev,
                                      company=self.co)
        PayslipLine.objects.create(payslip=slip, component=comp,
                                   amount=Decimal(amount))
        return slip


# ---------------------------------------------------------------------------
# B10 — Leave Pay
# ---------------------------------------------------------------------------

class LeavePayFeedTests(FeedBase):

    def _encashment(self, employee=None, amount='2000.00', when=None,
                    status=LeaveEncashment.Status.APPROVED):
        return LeaveEncashment.objects.create(
            employee=employee or self.emp, company=self.co, days=Decimal('5'),
            basic_salary=Decimal('9000'), daily_rate=Decimal('375'),
            amount=Decimal(amount), tax_amount=Decimal('250'),
            net_amount=Decimal(amount) - Decimal('250'),
            status=status, finance_approved_at=when or self._in_period())

    def test_approved_leave_pay_becomes_one_pending_batch_row(self):
        self._encashment()
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['status'], 'pushed')
        batch = PayrollAmendmentBatch.objects.get(target_period=self.target,
                                                  company=self.co,
                                                  file_name=LEAVEPAY_MARKER)
        # PARSED — never applied. The close applies; dual sign-off pays.
        self.assertEqual(batch.status, PayrollAmendmentBatch.Status.PARSED)
        row = batch.amendments.get(employee=self.emp)
        self.assertEqual(row.amount, Decimal('2000.00'))      # GROSS, not net
        self.assertEqual(row.component.code, 'LEAVE_PAY')
        self.assertTrue(row.component.is_taxable)
        self.assertFalse(row.applied)
        self.assertTrue(row.feed_key)

    def test_two_encashments_same_month_sum(self):
        self._encashment(amount='2000.00')
        self._encashment(amount='500.00')
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        row = PayrollAmendment.objects.get(employee=self.emp,
                                           component__code='LEAVE_PAY')
        self.assertEqual(row.amount, Decimal('2500.00'))

    def test_leaver_gone_before_the_period_is_skipped_and_named(self):
        emp = Employee.objects.create(employee_number='E9', full_name='Gone G',
                                      company=self.co,
                                      hire_date=datetime.date(2024, 1, 1),
                                      termination_date=datetime.date(2026, 10, 1))
        self._encashment(employee=emp)
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['status'], 'nothing_to_push')
        self.assertEqual(res['skipped'], 1)

    def test_approval_in_another_month_is_not_in_this_period(self):
        self._encashment(when=timezone.make_aware(
            datetime.datetime(2026, 9, 15, 10, 0), timezone.get_current_timezone()))
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['status'], 'nothing_to_push')

    def test_double_run_pays_once(self):
        self._encashment()
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(PayrollAmendmentBatch.objects.filter(
            target_period=self.target, company=self.co,
            file_name=LEAVEPAY_MARKER).count(), 1)
        rows = PayrollAmendment.objects.filter(employee=self.emp,
                                               component__code='LEAVE_PAY')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, Decimal('2000.00'))

    def test_double_run_is_stopped_by_the_DATABASE_not_python(self):
        """Go round the feed entirely: insert a second amendment with the same
        feed_key. Postgres must refuse it."""
        self._encashment()
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        first = PayrollAmendment.objects.get(employee=self.emp,
                                             component__code='LEAVE_PAY')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollAmendment.objects.create(
                    batch=first.batch, employee=self.emp, kind=first.kind,
                    component=first.component, amount=first.amount,
                    feed_key=first.feed_key)          # the same fingerprint

    def test_hand_keyed_rows_are_unaffected(self):
        """feed_key is NULL for hand-keyed amendments, and NULLs stay distinct —
        two spreadsheet rows for the same person must still both be allowed."""
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.prev, company=self.co,
            file_name='amendments.xlsx',
            status=PayrollAmendmentBatch.Status.PARSED)
        for _ in range(2):
            PayrollAmendment.objects.create(
                batch=batch, employee=self.emp,
                kind=PayrollAmendment.Kind.BONUS, amount=Decimal('10.00'))
        self.assertEqual(batch.amendments.count(), 2)


# ---------------------------------------------------------------------------
# B11 — Severance Connect
# ---------------------------------------------------------------------------

class SeveranceFeedTests(FeedBase):

    def _leaver(self, name, day):
        return Employee.objects.create(
            employee_number=f'T{day}', full_name=name, company=self.co,
            hire_date=datetime.date(2024, 1, 1),
            termination_date=datetime.date(2026, 10, day),
            status=Employee.Status.TERMINATED)

    def test_day_one_leaver_gets_NO_automatic_full_month(self):
        emp = self._leaver('Dayone D', 1)
        res = severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['day_one_excluded'], ['Dayone D'])
        # Not one amendment that could pay them.
        self.assertFalse(PayrollAmendment.objects.filter(employee=emp).exists())
        # But never silent — a named notice sits on the batch.
        notice = PayrollAmendment.objects.get(
            batch__file_name=severance_feed.AUTO_BATCH_MARKER, employee__isnull=True)
        self.assertEqual(notice.employee_ref, 'Dayone D')
        self.assertIn('NO automatic full month', notice.reason)
        self.assertEqual(notice.amount, Decimal('0.00'))

    def test_mid_period_leaver_gets_a_note_only_row(self):
        emp = self._leaver('Midmonth M', 15)
        res = severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 1)
        row = PayrollAmendment.objects.get(employee=emp)
        # OTHER with no component = a note on the batch, no figure changed.
        self.assertEqual(row.kind, PayrollAmendment.Kind.OTHER)
        self.assertIsNone(row.component)
        self.assertEqual(row.amount, Decimal('0.00'))   # B11 connects, never prices
        self.assertIn('2026-10-15', row.reason)

    def test_applying_the_batch_does_NOT_cancel_the_leavers_final_payslip(self):
        """The proof that matters. The kind LABEL is not the control — what the
        apply engine DOES with it is. A TERMINATE row makes the apply engine set
        the target payslip to CANCELLED, so the leaver this feed exists to put
        in front of the close would have their final pay cancelled. Apply the
        batch for real and look at the payslip. (Fable, 13-Sep-2026.)"""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from payroll.amendment_views import apply_amendment_batch

        emp = self._leaver('Midmonth M', 15)
        self._basic(emp, '9000.00')            # a baseline payslip in 2026-09
        severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        batch = PayrollAmendmentBatch.objects.get(
            file_name=severance_feed.AUTO_BATCH_MARKER)

        applier = User.objects.create_superuser('fc2', 'fc2@alphadirect.co.bw', 'x')
        req = APIRequestFactory().post(
            f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=applier)
        resp = apply_amendment_batch(req, batch_id=str(batch.id))
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))

        slip = Payslip.objects.get(employee=emp, period=self.target)
        self.assertNotEqual(
            slip.status, Payslip.Status.CANCELLED,
            "the severance feed cancelled the leaver's final payslip")

    def test_double_run_raises_one_row(self):
        emp = self._leaver('Midmonth M', 15)
        severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        self.assertEqual(PayrollAmendment.objects.filter(employee=emp).count(), 1)
        self.assertEqual(PayrollAmendmentBatch.objects.filter(
            file_name=severance_feed.AUTO_BATCH_MARKER).count(), 1)

    def test_double_run_is_stopped_by_the_DATABASE_not_python(self):
        emp = self._leaver('Midmonth M', 15)
        severance_feed.feed_period(period_label=PERIOD, user=self.hr)
        first = PayrollAmendment.objects.get(employee=emp)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollAmendment.objects.create(
                    batch=first.batch, employee=emp, kind=first.kind,
                    amount=first.amount, feed_key=first.feed_key)


# ---------------------------------------------------------------------------
# B12 — Early Salary Advance
# ---------------------------------------------------------------------------

class SalaryAdvanceTests(FeedBase):

    def setUp(self):
        self._basic(self.emp, '9000.00')       # cap = 3,000.00

    def test_the_cap_is_one_third_of_salary(self):
        with self.assertRaises(ValidationError) as ctx:
            advances.request_advance(employee=self.emp, amount='3000.01',
                                     user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)
        self.assertIn('3000.00', str(ctx.exception))
        adv = advances.request_advance(employee=self.emp, amount='3000.00',
                                       user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)
        self.assertEqual(adv.max_allowed, Decimal('3000.00'))
        self.assertEqual(adv.basic_salary, Decimal('9000.00'))

    def test_only_the_cfo_approves(self):
        adv = advances.request_advance(employee=self.emp, amount='1000',
                                       user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)
        with self.assertRaises(ValidationError):
            advances.approve(adv, self.hr)
        advances.approve(adv, self.cfo)
        adv.refresh_from_db()
        self.assertEqual(adv.status, SalaryAdvance.Status.APPROVED)
        self.assertEqual(adv.cfo_approver, self.cfo)

    def test_next_period_after(self):
        self.assertEqual(advances.next_period_after(PERIOD), NEXT)

    def test_requesting_twice_in_one_period_cannot_pay_twice(self):
        advances.request_advance(employee=self.emp, amount='1000',
                                 user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)
        with self.assertRaises(ValidationError):
            advances.request_advance(employee=self.emp, amount='500',
                                     user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)

    def test_the_second_advance_is_refused_by_the_DATABASE(self):
        """Past the service entirely — the unique index is the control."""
        advances.request_advance(employee=self.emp, amount='1000',
                                 user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PERIOD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SalaryAdvance.objects.create(
                    employee=self.emp, company=self.co, recovery_period=NEXT,
                    amount=Decimal('500'), status=SalaryAdvance.Status.PENDING_CFO)

    def _approved(self, amount='1000', period=PERIOD, taken=PREV):
        adv = advances.request_advance(employee=self.emp, amount=amount,
                                       user=self.hr, recovery_period=period,
                                       taken_in_period=taken)
        return advances.approve(adv, self.cfo)

    def test_a_recovery_month_that_is_NOT_the_next_payslip_is_refused(self):
        """CFO control 3 — "recovered in full on the very next payslip".
        `next_period_after` was written and tested and NEVER CALLED, so any
        month the caller typed was accepted: an advance taken in September
        could be set to recover in November and nothing objected. The rule is
        only a rule when the service refuses. (Fable, 13-Sep-2026.)"""
        with self.assertRaises(ValidationError) as ctx:
            advances.request_advance(employee=self.emp, amount='1000',
                                     user=self.hr, recovery_period=NEXT,
                                     taken_in_period=PREV)
        self.assertIn(PERIOD, str(ctx.exception))     # the one it should be
        self.assertEqual(SalaryAdvance.objects.count(), 0)

    def test_the_very_next_payslip_is_accepted(self):
        adv = advances.request_advance(employee=self.emp, amount='1000',
                                       user=self.hr, recovery_period=PERIOD,
                                       taken_in_period=PREV)
        self.assertEqual(adv.recovery_period, PERIOD)

    def test_a_recovery_month_before_the_advance_is_refused(self):
        with self.assertRaises(ValidationError):
            advances.request_advance(employee=self.emp, amount='1000',
                                     user=self.hr, recovery_period=PREV,
                                     taken_in_period=PERIOD)

    def test_recovered_in_full_on_the_next_payslip(self):
        self._approved(amount='1000', period=PERIOD)
        res = advances.feed_period(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 1)
        row = PayrollAmendment.objects.get(employee=self.emp,
                                           component__code='LOAN_REPAYMENT')
        self.assertEqual(row.kind, PayrollAmendment.Kind.DEDUCTION_ADD)
        self.assertEqual(row.amount, Decimal('1000.00'))    # in FULL, one month
        self.assertEqual(row.batch.file_name, advances.AUTO_BATCH_MARKER)
        self.assertEqual(row.batch.status, PayrollAmendmentBatch.Status.PARSED)

    def test_the_receivable_is_the_account_NAMED_staff_loan(self):
        """Assert on the account NAME, not just the code — 121000 is the
        bad-debt provision and resolving purely by code would hide that."""
        from ledger.models import Account
        Account.objects.create(code='121000', name='Provision for Bad Debts - ECL',
                               account_type='asset', owner_company=self.co)
        Account.objects.create(code='121010', name='Staff Loan',
                               account_type='asset', owner_company=self.co)
        self._approved(amount='1000', period=PERIOD)
        advances.feed_period(period_label=PERIOD, user=self.hr)
        comp = PayslipComponent.objects.get(code='LOAN_REPAYMENT')
        acct = Account.objects.get(code=comp.posting_account_code)
        self.assertEqual(acct.name, 'Staff Loan')
        self.assertNotIn('bad debt', acct.name.lower())

    def test_double_run_recovers_once(self):
        self._approved(amount='1000', period=PERIOD)
        advances.feed_period(period_label=PERIOD, user=self.hr)
        advances.feed_period(period_label=PERIOD, user=self.hr)
        rows = PayrollAmendment.objects.filter(employee=self.emp,
                                               component__code='LOAN_REPAYMENT')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, Decimal('1000.00'))
        self.assertEqual(PayrollAmendmentBatch.objects.filter(
            file_name=advances.AUTO_BATCH_MARKER).count(), 1)

    def test_double_run_is_stopped_by_the_DATABASE_not_python(self):
        self._approved(amount='1000', period=PERIOD)
        advances.feed_period(period_label=PERIOD, user=self.hr)
        first = PayrollAmendment.objects.get(employee=self.emp,
                                             component__code='LOAN_REPAYMENT')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollAmendment.objects.create(
                    batch=first.batch, employee=self.emp, kind=first.kind,
                    component=first.component, amount=first.amount,
                    feed_key=first.feed_key)
