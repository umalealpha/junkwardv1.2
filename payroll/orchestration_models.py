"""
payroll/orchestration_models.py — the run log of the monthly payroll
orchestration (Build Spec B13, 2026-09-13).

Two things live here, and only two.

``PayrollOrchestrationRun``
    One attempt at conducting one month for one entity (or for every entity,
    when nobody named one). It records what was asked for, what it refused,
    what it ran, and — critically — WHO WAS TOLD. A run that finished and
    told nobody is the failure this whole build exists to prevent: the weekly
    failed-debit report went live on 11 September 2026, exited quietly on
    every single run because its recipient list was empty, and nothing ever
    alerted anyone. Silence is not success.

``PayrollOrchestrationStep``
    One step of that run — its name, its counts, and its error if it had one.
    Steps are rows, not log lines, so "what ran, what was skipped and why" is
    a query rather than archaeology in a log file.

THE DATABASE-LEVEL GUARD HERE
    ``uniq_orchestration_running_per_period_company`` — a partial unique index
    that allows at most ONE run in flight for the same (period, entity). The
    conductor must not itself become the way a month gets fed twice: two crons
    that overlap, or a person who runs it by hand while the cron is mid-flight,
    are refused by Postgres, not by a Python ``if``.

    That is the SECOND database guard, not the only one. The no-double-pay
    guard proper is ``PayrollAmendment.feed_key`` (unique), which each feed
    writes — the orchestrator calls feeds that are already idempotent on their
    own and never becomes the only thing preventing a double payment.

Omni never moves money. Everything this conducts prepares payroll amendment
records; the dual HR + Finance sign-off is what actually pays, and LOCK and
POST stay human.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel, Company

ALL_COMPANIES_KEY = 'ALL'


class PayrollOrchestrationRun(AuditableMixin, BaseModel):
    """One conducted month. Append-only; a re-run is a new row."""

    class Status(models.TextChoices):
        RUNNING   = 'running',   'Running'
        REFUSED   = 'refused',   'Refused — nothing was touched'
        OK        = 'ok',        'Completed'
        EMPTY     = 'empty',     'Completed but did NOTHING'
        ATTENTION = 'attention', 'Completed with gaps to look at'
        FAILED    = 'failed',    'A step failed'

    class Trigger(models.TextChoices):
        CRON   = 'cron',   'Scheduled'
        MANUAL = 'manual', 'Run by hand'

    period_label = models.CharField(max_length=10, db_index=True,
                                    help_text='e.g. 2026-10')
    period  = models.ForeignKey('payroll.PayrollPeriod', null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name='orchestration_runs')
    company = models.ForeignKey(Company, null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name='payroll_orchestration_runs',
                                help_text='NULL = every entity.')
    # Non-null mirror of company, so the partial unique index below also holds
    # for the every-entity run. NULLs stay distinct under a unique index, which
    # would have left exactly that case with no guard at all.
    company_key = models.CharField(max_length=40, default=ALL_COMPANIES_KEY,
                                   editable=False)

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.RUNNING, db_index=True)
    trigger = models.CharField(max_length=8, choices=Trigger.choices,
                               default=Trigger.MANUAL)
    refusal_reason = models.TextField(blank=True, default='',
                                      help_text='Why nothing was touched.')

    started_at  = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(User, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='payroll_orchestration_runs')

    rows_touched = models.PositiveIntegerField(
        default=0, help_text='Total amendment / payslip rows the steps raised '
                             'or refreshed. Zero means NOTHING happened.')
    gaps = models.TextField(blank=True, default='',
                            help_text='Commission-population gaps found. '
                                      'Reported, never silently widened.')

    # Who was told. A finished run with an empty `notified_to` is the 12-Sep bug.
    notified_to  = models.CharField(max_length=500, blank=True, default='')
    notify_error = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-started_at']
        verbose_name = 'Payroll Orchestration Run'
        verbose_name_plural = 'Payroll Orchestration Runs'
        constraints = [
            # At most one in-flight run per (period, entity). Two overlapping
            # crons, or a hand-run during a cron, are refused by the database.
            # company_key (never NULL) is used, not company_id — NULLs stay
            # distinct under a unique index, so the every-entity run would
            # otherwise have had no guard at all.
            models.UniqueConstraint(
                fields=['period_label', 'company_key'],
                condition=models.Q(status='running'),
                name='uniq_orchestration_running_per_period_company'),
        ]

    def save(self, *args, **kwargs):
        self.company_key = str(self.company_id) if self.company_id else ALL_COMPANIES_KEY
        super().save(*args, **kwargs)

    def __str__(self):
        who = self.company.name if self.company_id else 'all entities'
        return (f'Payroll orchestration {self.period_label} · {who} '
                f'— {self.get_status_display()}')

    @property
    def did_nothing(self) -> bool:
        return self.rows_touched == 0


class PayrollOrchestrationStep(BaseModel):
    """One step of one run. `status` is what to read; `detail` is for a human."""

    class Status(models.TextChoices):
        OK      = 'ok',      'Ran'
        NOTHING = 'nothing', 'Ran, found nothing to do'
        SKIPPED = 'skipped', 'Skipped'
        FAILED  = 'failed',  'Failed'

    run = models.ForeignKey(PayrollOrchestrationRun, on_delete=models.CASCADE,
                            related_name='steps')
    sequence = models.PositiveIntegerField(default=0)
    name     = models.CharField(max_length=40)
    status   = models.CharField(max_length=8, choices=Status.choices,
                                default=Status.OK)
    rows     = models.PositiveIntegerField(default=0)
    skipped  = models.PositiveIntegerField(default=0)
    detail   = models.TextField(blank=True, default='')
    error    = models.TextField(blank=True, default='')
    started_at  = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['run', 'sequence']
        verbose_name = 'Payroll Orchestration Step'
        verbose_name_plural = 'Payroll Orchestration Steps'

    def __str__(self):
        return f'{self.name} — {self.get_status_display()} ({self.rows} rows)'
