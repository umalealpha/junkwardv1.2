"""B13 — Monthly Orchestration and Commission Verify. The conductor.

Three proofs carry this file, in the order the spec cares about them:

1.  RUN IT TWICE.  The whole orchestration, same period, same entity, back to
    back — and nobody is paid twice.  The guard proved here is the DATABASE's:
    `PayrollAmendment.feed_key` is UNIQUE, so after the two clean runs we reach
    past the orchestrator and past the feeds entirely and try to INSERT a
    second amendment carrying a key that already exists.  Postgres refuses.  A
    Python check-then-write can be raced; a unique index cannot.

    The conductor gets a second database guard of its own —
    `uniq_orchestration_running_per_period_company` — so two overlapping crons
    cannot conduct the same month at once.  The orchestrator must not become
    the only thing preventing a double payment, and it does not: each feed is
    idempotent on its own, and this index only stops the conductor racing
    itself.

2.  REFUSE A SIGNED-OFF PERIOD.  Not warn.  Nothing written, `PeriodRefused`
    raised, and the refusal itself recorded and e-mailed so it is not silent.

3.  SILENCE IS NOT SUCCESS.  A run that does nothing finishes EMPTY, shouts
    NOTHING RAN in a subject line to a real person, and the management command
    exits non-zero.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from commissions.models import (CommissionAgent, CommissionGroup,
                                CommissionSubmission)
from core.models import Company
from hris.leave_encash_models import LeaveEncashment
from payroll import monthly_orchestration as orch
from payroll.models import (Employee, PayrollAmendment, PayrollAmendmentBatch,
                            PayrollOrchestrationRun, PayrollOrchestrationStep,
                            PayrollPeriod, PayrollSetting, PayrollSignOff,
                            Payslip, PayslipComponent, PayslipLine)
from payroll.salary_advance_models import SalaryAdvance

PREV = '2026-09'
PERIOD = '2026-10'


class OrchestrationBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TESTCO', name='Test Co.')
        cls.prev = PayrollPeriod.objects.create(
            period_name=PREV, start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 9, 30))
        cls.period = PayrollPeriod.objects.create(
            period_name=PERIOD, start_date=datetime.date(2026, 10, 1),
            end_date=datetime.date(2026, 10, 31))
        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Alice A', company=cls.co,
            hire_date=datetime.date(2024, 1, 1))
        cls.cfo = User.objects.create_user('cfo13', 'cfo13@alphadirect.co.bw', 'x')
        cls.cfo.is_superuser = True
        cls.cfo.is_staff = True
        cls.cfo.save()

    def setUp(self):
        super().setUp()
        self._set('payroll.monthly_orchestration', 'true')

    # -- helpers ----------------------------------------------------------
    def _set(self, key, value):
        PayrollSetting.objects.update_or_create(key=key, defaults={'value': value})

    def _at(self, day=15):
        from django.utils import timezone
        return timezone.make_aware(datetime.datetime(2026, 10, day, 10, 0),
                                   timezone.get_current_timezone())

    def _encashment(self, amount='2000.00'):
        """One approved leave payout — real, feedable work for the month."""
        return LeaveEncashment.objects.create(
            employee=self.emp, company=self.co, days=Decimal('5'),
            basic_salary=Decimal('9000'), daily_rate=Decimal('375'),
            amount=Decimal(amount), tax_amount=Decimal('250'),
            net_amount=Decimal(amount) - Decimal('250'),
            status=LeaveEncashment.Status.APPROVED,
            finance_approved_at=self._at())

    def _run(self, **kw):
        kw.setdefault('period_label', PERIOD)
        kw.setdefault('company', self.co)
        kw.setdefault('user', self.cfo)
        return orch.run_month(**kw)


# ---------------------------------------------------------------------------
# 1. THE PROOF THAT MATTERS MOST — run it twice, nobody is paid twice
# ---------------------------------------------------------------------------

class DoubleRunTests(OrchestrationBase):

    def test_running_the_whole_orchestration_twice_pays_nobody_twice(self):
        self._encashment(amount='2000.00')

        first = self._run()
        rows_after_first = list(
            PayrollAmendment.objects.filter(feed_key__isnull=False)
            .values_list('feed_key', 'amount'))
        self.assertTrue(rows_after_first, 'the first run raised nothing to re-run')

        second = self._run()
        rows_after_second = list(
            PayrollAmendment.objects.filter(feed_key__isnull=False)
            .values_list('feed_key', 'amount'))

        self.assertEqual(sorted(rows_after_first), sorted(rows_after_second),
                         'the second orchestration changed the amendment rows')
        # One leave-pay row, still the original gross — not 2 rows, not 4,000.
        leave = PayrollAmendment.objects.filter(component__code='LEAVE_PAY')
        self.assertEqual(leave.count(), 1)
        self.assertEqual(leave.get().amount, Decimal('2000.00'))
        # One canonical PARSED batch per (period, entity, marker) — not two.
        self.assertEqual(
            PayrollAmendmentBatch.objects.filter(
                target_period=self.period, company=self.co,
                file_name='AUTO-LEAVEPAY').count(), 1)
        self.assertNotEqual(first.pk, second.pk)   # two runs, one result

    def test_the_no_double_pay_guard_is_the_DATABASE_not_python(self):
        """Reach past the orchestrator and past the feeds. Insert a duplicate.

        This is the half that matters. The feeds' own `update_or_create` is a
        Python check-then-write; two workers can both pass it. The unique index
        on `PayrollAmendment.feed_key` cannot be raced.
        """
        self._encashment()
        self._run()
        existing = PayrollAmendment.objects.filter(
            component__code='LEAVE_PAY').get()
        self.assertTrue(existing.feed_key)

        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                PayrollAmendment.objects.create(
                    batch=existing.batch, employee=existing.employee,
                    kind=existing.kind, component=existing.component,
                    amount=existing.amount, employee_ref='duplicate attempt',
                    feed_key=existing.feed_key)      # <- the same fingerprint
        self.assertIn('feed_key', str(caught.exception).lower())

    def test_two_conductors_cannot_run_the_same_month_at_once(self):
        """The orchestrator's own guard, also at database level.

        `uniq_orchestration_running_per_period_company` — a partial unique
        index over (period_label, company_key) where status='running'. Two
        overlapping crons, or a hand-run during a cron, are refused by Postgres.
        """
        PayrollOrchestrationRun.objects.create(
            period_label=PERIOD, period=self.period, company=self.co,
            status=PayrollOrchestrationRun.Status.RUNNING)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollOrchestrationRun.objects.create(
                    period_label=PERIOD, period=self.period, company=self.co,
                    status=PayrollOrchestrationRun.Status.RUNNING)

    def test_the_every_entity_run_is_guarded_too(self):
        """company is NULL for an all-entities run, and NULLs stay distinct
        under a unique index — which would have left the group-wide run with no
        guard at all. `company_key` is the non-null mirror that closes it."""
        PayrollOrchestrationRun.objects.create(
            period_label=PERIOD, period=self.period, company=None,
            status=PayrollOrchestrationRun.Status.RUNNING)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollOrchestrationRun.objects.create(
                    period_label=PERIOD, period=self.period, company=None,
                    status=PayrollOrchestrationRun.Status.RUNNING)

    def test_a_finished_run_does_not_block_the_next_one(self):
        self._encashment()
        first = self._run()
        self.assertNotEqual(first.status, PayrollOrchestrationRun.Status.RUNNING)
        self._run()          # must not raise


# ---------------------------------------------------------------------------
# 2. IT REFUSES A SIGNED-OFF PERIOD — refuses, not warns
# ---------------------------------------------------------------------------

class RefusalTests(OrchestrationBase):

    def _assert_touched_nothing(self):
        self.assertEqual(PayrollAmendment.objects.count(), 0)
        self.assertEqual(PayrollAmendmentBatch.objects.count(), 0)

    def test_refuses_a_company_month_both_signatures_are_on(self):
        self._encashment()
        hr = User.objects.create_user('hr13', 'hr13@alphadirect.co.bw', 'x')
        fin = User.objects.create_user('fin13', 'fin13@alphadirect.co.bw', 'x')
        PayrollSignOff.objects.create(
            period=self.period, company=self.co,
            status=PayrollSignOff.Status.APPROVED,
            hr_signed_by=hr, hr_signed_at=self._at(),
            fin_signed_by=fin, fin_signed_at=self._at())

        with self.assertRaises(orch.PeriodRefused) as caught:
            self._run()
        self.assertIn('already signed off', str(caught.exception))
        self._assert_touched_nothing()

        # The refusal is recorded and e-mailed — a refusal nobody sees is its
        # own kind of silence.
        run = PayrollOrchestrationRun.objects.get()
        self.assertEqual(run.status, PayrollOrchestrationRun.Status.REFUSED)
        self.assertTrue(run.refusal_reason)
        self.assertEqual(run.steps.count(), 0)
        self.assertTrue(run.notified_to)
        self.assertIn('REFUSED', mail.outbox[-1].subject)

    def test_refuses_a_period_that_has_moved_past_open(self):
        self._encashment()
        self.period.status = PayrollPeriod.Status.LOCKED
        self.period.save(update_fields=['status'])
        with self.assertRaises(orch.PeriodRefused) as caught:
            self._run()
        self.assertIn('not OPEN', str(caught.exception))
        self._assert_touched_nothing()

    def test_a_group_wide_run_refuses_for_one_signed_entity(self):
        """The CFO signs "ADIC October", not "October". One signed entity stops
        the group-wide run rather than half-feeding the month."""
        other = Company.objects.create(code='OTHER', name='Other Co.')
        hr = User.objects.create_user('hr14', 'hr14@alphadirect.co.bw', 'x')
        fin = User.objects.create_user('fin14', 'fin14@alphadirect.co.bw', 'x')
        PayrollSignOff.objects.create(
            period=self.period, company=other,
            status=PayrollSignOff.Status.APPROVED,
            hr_signed_by=hr, hr_signed_at=self._at(),
            fin_signed_by=fin, fin_signed_at=self._at())
        with self.assertRaises(orch.PeriodRefused):
            self._run(company=None)
        self._assert_touched_nothing()

    def test_refuses_while_the_feature_flag_is_off(self):
        self._set('payroll.monthly_orchestration', 'false')
        with self.assertRaises(orch.PeriodRefused) as caught:
            self._run()
        self.assertIn('feature flag', str(caught.exception))
        self._assert_touched_nothing()

    def test_refuses_rather_than_pretend_auto_lock_works(self):
        """LOCK and POST stay human under dual sign-off. If somebody switches
        the lever on, say so — never quietly ignore it."""
        self._set('payroll.auto_lock', 'true')
        with self.assertRaises(orch.PeriodRefused) as caught:
            self._run()
        self.assertIn('LOCK is a human step', str(caught.exception))
        self._assert_touched_nothing()

    def test_refuses_an_unknown_period(self):
        with self.assertRaises(orch.PeriodRefused):
            self._run(period_label='2099-01')

    def test_the_orchestrator_never_locks_or_posts(self):
        self._encashment()
        self._run()
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, PayrollPeriod.Status.OPEN)
        # And every batch it prepared is PARSED — prepared, never applied.
        for batch in PayrollAmendmentBatch.objects.all():
            self.assertEqual(batch.status, PayrollAmendmentBatch.Status.PARSED)


# ---------------------------------------------------------------------------
# 3. SILENCE IS NOT SUCCESS
# ---------------------------------------------------------------------------

class SilenceIsNotSuccessTests(OrchestrationBase):

    def test_a_run_that_did_nothing_is_EMPTY_and_says_so_to_a_person(self):
        run = self._run()          # nothing approved anywhere this month
        self.assertEqual(run.rows_touched, 0)
        self.assertEqual(run.status, PayrollOrchestrationRun.Status.EMPTY)
        self.assertTrue(run.steps.exists(), 'the steps must still be recorded')

        self.assertTrue(mail.outbox, 'a run that did nothing told nobody')
        msg = mail.outbox[-1]
        self.assertIn('NOTHING RAN', msg.subject)
        self.assertIn('did nothing at all', msg.body + str(msg.alternatives))
        self.assertTrue(run.notified_to)

    def test_a_run_that_did_nothing_exits_non_zero(self):
        with self.assertRaises(CommandError) as caught:
            call_command('run_monthly_payroll_orchestration',
                         period=PERIOD, company='TESTCO',
                         user_email=self.cfo.email, verbosity=0)
        self.assertIn('did NOTHING', str(caught.exception))

    def test_a_real_run_is_not_EMPTY(self):
        self._encashment()
        run = self._run()
        self.assertGreater(run.rows_touched, 0)
        self.assertNotEqual(run.status, PayrollOrchestrationRun.Status.EMPTY)
        self.assertNotIn('NOTHING RAN', mail.outbox[-1].subject)

    def test_with_no_recipient_list_it_still_reaches_a_person_and_says_so(self):
        """The 12-September lesson. An empty recipient list is the thing to
        shout about, never a reason to exit quietly."""
        self._encashment()
        run = self._run()
        self.assertTrue(mail.outbox)
        self.assertIn('NO RECIPIENTS CONFIGURED', mail.outbox[-1].subject)
        self.assertTrue(run.notified_to)
        self.assertFalse(run.notify_error)
        # It still went somewhere real — the CFO/EXCO inbox.
        recipients = mail.outbox[-1].to + mail.outbox[-1].cc
        self.assertTrue(any('excoboard' in r for r in recipients), recipients)

    def test_it_goes_to_the_list_finance_keep_when_there_is_one(self):
        from reporting.models import ReportRecipient
        ReportRecipient.objects.create(report_slug=orch.REPORT_SLUG,
                                       email='payroll@alphadirect.co.bw')
        self._encashment()
        run = self._run()
        self.assertIn('payroll@alphadirect.co.bw', mail.outbox[-1].to)
        self.assertNotIn('NO RECIPIENTS', mail.outbox[-1].subject)
        self.assertIn('payroll@alphadirect.co.bw', run.notified_to)

    def test_a_failing_step_is_reported_not_carried_past_quietly(self):
        self._encashment()
        boom = lambda ctx: (_ for _ in ()).throw(RuntimeError('feed exploded'))
        original = orch.STEPS['feeds']
        orch.STEPS['feeds'] = boom
        try:
            run = self._run()
        finally:
            orch.STEPS['feeds'] = original
        self.assertEqual(run.status, PayrollOrchestrationRun.Status.FAILED)
        step = run.steps.get(name='feeds')
        self.assertEqual(step.status, PayrollOrchestrationStep.Status.FAILED)
        self.assertIn('feed exploded', step.error)
        self.assertIn('FAILED', mail.outbox[-1].subject)
        # The steps after it still ran — one broken feed does not lose the month.
        self.assertTrue(run.steps.filter(name='recompute').exists())

    def test_a_typo_in_the_configured_sequence_is_a_failure_not_a_dropped_step(self):
        self._set('payroll.monthly_sequence', 'feeds,recomptue')
        self._encashment()
        run = self._run()
        self.assertEqual(run.status, PayrollOrchestrationRun.Status.FAILED)
        step = run.steps.get(name='recomptue')
        self.assertIn('not a step this code knows how to run', step.error)

    def test_every_step_lands_in_the_run_log(self):
        self._encashment()
        run = self._run()
        self.assertEqual([s.name for s in run.steps.all()],
                         orch.configured_sequence())


# ---------------------------------------------------------------------------
# Commission verify — report the gap, never widen the scope
# ---------------------------------------------------------------------------

class CommissionVerifyTests(OrchestrationBase):

    def _agent(self, name, pays_via, email=''):
        group, _ = CommissionGroup.objects.get_or_create(
            key=(CommissionGroup.Key.IN_HOUSE
                 if pays_via == CommissionGroup.PaysVia.PAYROLL
                 else CommissionGroup.Key.INDEPENDENT),
            defaults={'name': f'{pays_via} group', 'pays_via': pays_via})
        group.pays_via = pays_via
        group.save(update_fields=['pays_via'])
        return CommissionAgent.objects.create(name=name, group=group, email=email)

    def test_an_agent_who_can_never_reach_a_payslip_is_named(self):
        from commissions.verify import verify_population
        self._agent('Nameless N', CommissionGroup.PaysVia.PAYROLL, email='')
        out = verify_population(period_label=PERIOD)
        self.assertEqual(out['agents'], 1)
        self.assertEqual(out['agents_resolvable'], 0)
        self.assertTrue(any('Nameless N' in g for g in out['gaps']))

    def test_an_approved_payroll_commission_that_is_not_fed_is_reported(self):
        from commissions.verify import verify_population
        agent = self._agent('Alice A', CommissionGroup.PaysVia.PAYROLL,
                            email=self.emp.email or 'alice@alphadirect.co.bw')
        CommissionSubmission.objects.create(
            agent=agent, group=agent.group, period_label=PERIOD,
            status=CommissionSubmission.Status.APPROVED,
            net_payable=Decimal('500.00'))
        out = verify_population(period_label=PERIOD)
        self.assertEqual(out['unfed'], 1)
        self.assertTrue(any('not on a payroll batch' in g for g in out['gaps']))

    def test_a_config_that_disagrees_with_the_code_is_a_gap_not_a_silent_widening(self):
        from commissions.verify import verify_population
        self._set('commission.payroll_group_filter', 'everybody')
        out = verify_population(period_label=PERIOD)
        self.assertTrue(any('was NOT widened' in g for g in out['gaps']))
        # And the implemented filter is unchanged — nothing was broadened.
        self.assertEqual(out['implemented'], 'pays_via == PAYROLL')

    def test_gaps_make_the_run_ATTENTION_and_reach_the_report(self):
        self._encashment()
        self._agent('Nameless N', CommissionGroup.PaysVia.PAYROLL, email='')
        run = self._run()
        self.assertEqual(run.status, PayrollOrchestrationRun.Status.ATTENTION)
        self.assertIn('Nameless N', run.gaps)
        self.assertIn('Check needed', mail.outbox[-1].subject)


# ---------------------------------------------------------------------------
# The steps do what the sequence says
# ---------------------------------------------------------------------------

class SequenceTests(OrchestrationBase):

    def test_the_recompute_step_clears_a_stale_stored_total(self):
        """The staff-loan defect, in one test: a payslip line written without a
        recompute leaves the stored net stale, and posting reads the stored net.
        """
        comp, _ = PayslipComponent.objects.get_or_create(
            code='BASIC', defaults={'name': 'Basic',
                                    'kind': PayslipComponent.Kind.EARNING,
                                    'is_taxable': True})
        slip = Payslip.objects.create(employee=self.emp, period=self.period,
                                      company=self.co)
        PayslipLine.objects.create(payslip=slip, component=comp,
                                   amount=Decimal('9000.00'))
        Payslip.objects.filter(pk=slip.pk).update(gross_amount=Decimal('0.00'),
                                                  net_amount=Decimal('0.00'))
        self._run()
        slip.refresh_from_db()
        self.assertEqual(slip.gross_amount, Decimal('9000.00'))

    def test_an_advance_is_recovered_once_across_two_runs(self):
        adv = SalaryAdvance.objects.create(
            employee=self.emp, company=self.co, recovery_period=PERIOD,
            amount=Decimal('1500.00'), basic_salary=Decimal('9000.00'),
            max_allowed=Decimal('3000.00'),
            status=SalaryAdvance.Status.APPROVED)
        self._run()
        self._run()
        rows = PayrollAmendment.objects.filter(component__code='LOAN_REPAYMENT',
                                               employee=self.emp)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().amount, Decimal('1500.00'))
        adv.refresh_from_db()
        self.assertEqual(adv.status, SalaryAdvance.Status.RECOVERED)
