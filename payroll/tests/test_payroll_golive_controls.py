"""The two go-live controls the CFO decided on 13 September 2026, both about
not paying somebody twice.

1. LEAVE ENCASHMENT GO-LIVE CUT-OFF. Approved encashments are settled directly
   by Finance today — the money has already left the bank. Switching the
   AUTO-LEAVEPAY feed on without a cut-off puts every one of them onto a
   payslip and pays the person a second time. The test that matters is
   `test_encashment_approved_and_paid_before_golive_produces_no_payroll_line`:
   approved before the cut-off, already paid, and the feed must raise NOTHING
   and SAY it held one back.

2. ONE PAYROLL LINE PER ENCASHMENT, whoever raised it. Proved at the DATABASE —
   go round the feed entirely and insert a second claim row, and Postgres must
   refuse it. A Python check-then-write can be raced; a unique index cannot.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import Company
from hris.leave_encash_models import LeaveEncashment, LeaveEncashmentPayrollLine
from hris.leave_pay_feed import (AUTO_BATCH_MARKER, GOLIVE_SETTING_KEY,
                                 claim_payroll_line)
from hris.leave_pay_feed import feed_period as feed_leave_pay
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollPeriod, PayrollSetting)

PREV = '2026-09'
PERIOD = '2026-10'


def _aware(y, m, d):
    return timezone.make_aware(datetime.datetime(y, m, d, 10, 0),
                               timezone.get_current_timezone())


class LeavePayGoLiveCutoffTests(TestCase):
    """The cut-off is a named, editable setting — not a constant in the code."""

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.prev = PayrollPeriod.objects.create(
            period_name=PREV, start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))
        cls.target = PayrollPeriod.objects.create(
            period_name=PERIOD, start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 31))
        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A', company=cls.co,
            hire_date=datetime.date(2024, 1, 1))
        cls.hr = User.objects.create_user('hr2', 'ubutale@alphadirect.co.bw', 'x')

    def _cutoff(self, value):
        PayrollSetting.objects.update_or_create(key=GOLIVE_SETTING_KEY,
                                                defaults={'value': value})

    def _encashment(self, when, status=LeaveEncashment.Status.APPROVED,
                    amount='2000.00', paid=False):
        return LeaveEncashment.objects.create(
            employee=self.emp, company=self.co, days=Decimal('5'),
            basic_salary=Decimal('9000'), daily_rate=Decimal('375'),
            amount=Decimal(amount), tax_amount=Decimal('250'),
            net_amount=Decimal(amount) - Decimal('250'),
            status=status, finance_approved_at=when,
            payroll_processed=paid)

    # ── THE TEST THAT MATTERS ────────────────────────────────────────────────
    def test_encashment_approved_and_paid_before_golive_produces_no_payroll_line(self):
        """Approved BEFORE the cut-off and already settled directly by Finance:
        no payroll line at all, and the run says it held one back."""
        self._cutoff('2026-10-20')
        self._encashment(_aware(2026, 10, 15), status=LeaveEncashment.Status.PAID,
                         paid=True)

        res = feed_leave_pay(period_label=PERIOD, user=self.hr)

        # Nothing raised — not a zero row, not a pending row, nothing payable.
        self.assertFalse(
            PayrollAmendment.objects.filter(employee=self.emp,
                                            component__code='LEAVE_PAY').exists())
        self.assertEqual(res['pushed'], 0)
        # And never silent: the count, the reason and the cut-off come back.
        self.assertEqual(res['skipped_pre_golive'], 1)
        self.assertEqual(res['golive_cutoff'], '2026-10-20')
        self.assertIn('go-live cut-off',
                      res['pre_golive_detail'][0]['reason'])

    def test_an_encashment_settled_directly_after_the_cutoff_is_still_not_fed(self):
        """The cut-off widened the queryset so directly-settled encashments can
        be COUNTED. This proves widening it did not also start feeding them:
        marked paid, carried by no payroll line, approved after the cut-off —
        still nothing raised, and still named as a skip."""
        self._cutoff('2026-10-10')
        self._encashment(_aware(2026, 10, 25), status=LeaveEncashment.Status.PAID,
                         paid=True)
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 0)
        self.assertEqual(res['skipped'], 1)
        self.assertIn('settled outside payroll', res['skipped_detail'][0]['reason'])
        self.assertFalse(
            PayrollAmendment.objects.filter(employee=self.emp,
                                            component__code='LEAVE_PAY').exists())

    def test_the_same_encashment_approved_after_the_cutoff_IS_fed(self):
        """The control. Without this the first test would also pass on a feed
        that simply never raises anything."""
        self._cutoff('2026-10-20')
        self._encashment(_aware(2026, 10, 25))

        res = feed_leave_pay(period_label=PERIOD, user=self.hr)

        self.assertEqual(res['pushed'], 1)
        self.assertEqual(res['skipped_pre_golive'], 0)
        row = PayrollAmendment.objects.get(employee=self.emp,
                                           component__code='LEAVE_PAY')
        self.assertEqual(row.amount, Decimal('2000.00'))

    def test_an_encashment_ON_the_cutoff_date_is_held_back(self):
        """"On or before" — the cut-off day itself is go-live day, and anything
        approved that day was settled the old way."""
        self._cutoff('2026-10-15')
        self._encashment(_aware(2026, 10, 15))
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 0)
        self.assertEqual(res['skipped_pre_golive'], 1)

    def test_moving_the_setting_moves_the_cutoff(self):
        """It is a configurable setting, not a constant — Finance move the date
        on screen, no deploy."""
        self._encashment(_aware(2026, 10, 15))

        self._cutoff('2026-10-31')
        self.assertEqual(feed_leave_pay(period_label=PERIOD,
                                        user=self.hr)['pushed'], 0)

        self._cutoff('2026-10-01')
        self.assertEqual(feed_leave_pay(period_label=PERIOD,
                                        user=self.hr)['pushed'], 1)

    def test_a_blank_cutoff_feeds_everything_and_says_so(self):
        self._cutoff('')
        self._encashment(_aware(2026, 10, 15))
        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 1)
        self.assertIsNone(res['golive_cutoff'])

    def test_held_back_rows_are_named_on_the_batch_not_just_counted(self):
        """One held back, one fed: the batch that IS created carries a named
        zero-amount notice for the one that was not."""
        self._cutoff('2026-10-20')
        self._encashment(_aware(2026, 10, 15))          # held back
        other = Employee.objects.create(
            employee_number='E2', full_name='Bob B', company=self.co,
            hire_date=datetime.date(2024, 1, 1))
        LeaveEncashment.objects.create(
            employee=other, company=self.co, days=Decimal('2'),
            basic_salary=Decimal('9000'), daily_rate=Decimal('375'),
            amount=Decimal('750.00'), net_amount=Decimal('750.00'),
            status=LeaveEncashment.Status.APPROVED,
            finance_approved_at=_aware(2026, 10, 25))   # fed

        res = feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(res['pushed'], 1)
        self.assertEqual(res['skipped_pre_golive'], 1)

        notice = PayrollAmendment.objects.get(
            batch__file_name=AUTO_BATCH_MARKER, employee__isnull=True)
        self.assertEqual(notice.employee_ref, 'Alice A')
        self.assertEqual(notice.amount, Decimal('0.00'))
        self.assertIn('go-live cut-off', notice.reason)


class OnePayrollLinePerEncashmentTests(TestCase):
    """Belt and braces: a payroll line already raised for an encashment — by
    anyone — is never raised a second time, and the refusal is Postgres's."""

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TEST', name='Test Co.')
        cls.prev = PayrollPeriod.objects.create(
            period_name=PREV, start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))
        cls.target = PayrollPeriod.objects.create(
            period_name=PERIOD, start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 31))
        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A', company=cls.co,
            hire_date=datetime.date(2024, 1, 1))
        cls.hr = User.objects.create_user('hr3', 'ubutale@alphadirect.co.bw', 'x')

    def setUp(self):
        PayrollSetting.objects.update_or_create(
            key=GOLIVE_SETTING_KEY, defaults={'value': '2026-09-13'})
        self.app = LeaveEncashment.objects.create(
            employee=self.emp, company=self.co, days=Decimal('5'),
            basic_salary=Decimal('9000'), daily_rate=Decimal('375'),
            amount=Decimal('2000.00'), net_amount=Decimal('1750.00'),
            status=LeaveEncashment.Status.APPROVED,
            finance_approved_at=_aware(2026, 10, 15))

    def test_the_feed_records_a_claim_row_for_the_encashment(self):
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        claim = LeaveEncashmentPayrollLine.objects.get(encashment=self.app)
        self.assertEqual(claim.source, LeaveEncashmentPayrollLine.Source.FEED)
        self.assertEqual(claim.amendment.component.code, 'LEAVE_PAY')

    def test_a_second_line_for_the_same_encashment_is_refused_by_the_DATABASE(self):
        """Go round the feed entirely and insert a second claim. Postgres must
        refuse it — a Python check could be raced, a unique index cannot."""
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        first = LeaveEncashmentPayrollLine.objects.get(encashment=self.app)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LeaveEncashmentPayrollLine.objects.create(
                    encashment=self.app, amendment=first.amendment,
                    source=LeaveEncashmentPayrollLine.Source.HAND)

    def test_a_hand_keyed_line_stops_the_feed_raising_a_second_one(self):
        """Somebody keyed the encashment onto a spreadsheet amendment. The feed
        must leave it alone and say why."""
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.prev, company=self.co,
            file_name='amendments.xlsx',
            status=PayrollAmendmentBatch.Status.PARSED)
        hand = PayrollAmendment.objects.create(
            batch=batch, employee=self.emp,
            kind=PayrollAmendment.Kind.ALLOWANCE_ADD, amount=Decimal('2000.00'))
        claim_payroll_line(self.app, hand)

        res = feed_leave_pay(period_label=PERIOD, user=self.hr)

        self.assertEqual(res['pushed'], 0)
        self.assertEqual(res['skipped'], 1)
        self.assertIn('already exists', res['skipped_detail'][0]['reason'])
        # Still exactly one payroll line for that encashment: the hand-keyed one.
        self.assertEqual(
            LeaveEncashmentPayrollLine.objects.filter(encashment=self.app).count(), 1)
        self.assertFalse(
            PayrollAmendment.objects.filter(component__code='LEAVE_PAY').exists())

    def test_claiming_twice_through_the_helper_is_refused(self):
        batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.prev, company=self.co,
            file_name='amendments.xlsx',
            status=PayrollAmendmentBatch.Status.PARSED)
        hand = PayrollAmendment.objects.create(
            batch=batch, employee=self.emp,
            kind=PayrollAmendment.Kind.ALLOWANCE_ADD, amount=Decimal('2000.00'))
        claim_payroll_line(self.app, hand)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                claim_payroll_line(self.app, hand)

    def test_rerunning_the_feed_refreshes_its_own_claim_not_a_second_one(self):
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        feed_leave_pay(period_label=PERIOD, user=self.hr)
        self.assertEqual(
            LeaveEncashmentPayrollLine.objects.filter(encashment=self.app).count(), 1)
        self.assertEqual(
            PayrollAmendment.objects.filter(employee=self.emp,
                                            component__code='LEAVE_PAY').count(), 1)
