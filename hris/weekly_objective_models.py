"""
hris/weekly_objective_models.py — the weekly manager objective (CFO 2026-09-09).

The problem this fixes, in the CFO's words: "managers are chilling and not being
accountable". Proven on prod before building — Kakale Botana (AML/CFT Officer,
5 reports) had TWO tasks in her entire Omni history, both auto-raised "do your
monthly feedback". She was never given a number to hit, so there was nothing to
be behind on.

The mechanism (CFO): a task is generated automatically at Sunday midnight and
is due Wednesday 4pm. Not typed by anyone, not negotiable, and it settles itself
against live data — so it can never become another form to fill in.

Two models:
  * WeeklyObjective    — the standing definition: whose it is, what is counted,
                         and how much must move in a week.
  * WeeklyObjectiveRun — one week of that objective: the baseline taken on
                         Sunday, the actual read on Wednesday, and whether it
                         was met. The baseline is FROZEN at raise time so a
                         later data change cannot rewrite what was asked.

Why a new model and not hris.PerformanceTarget: that one is monthly by design
(period_year + period_month, unique per month) and its actual is confirmed by a
human at month end. This is weekly and settles itself with no human in the loop.

DELIBERATE: the run is what closes the task. Omni's existing cycles raise tasks
and never close them — proven on prod 2026-09-09, where 14 managers including
the CFO were chased daily for monthly feedback they had already filed. A weekly
cadence would have turned that into five false nags a week, so settling is part
of this module, not a later improvement.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class Cadence(models.TextChoices):
    """How often the objective comes round, and therefore when it is due.

    WEEKLY is the CFO's default (raised Sunday midnight, due Wednesday 4pm).
    QUARTERLY exists for the Board/EXCO compliance report, which he set at
    "the 5th of every quarter ending" — a different clock, but deliberately the
    SAME task, the same self-closing and the same board. A second engine for
    quarterly work would drift out of step with the weekly one within a month.
    """
    WEEKLY    = 'weekly',    'Weekly — raised Sunday, due Wednesday'
    QUARTERLY = 'quarterly', 'Quarterly — raised at quarter end, due the 5th'
    ANNUAL    = 'annual',    'Annual — raised at the period start'


class Direction(models.TextChoices):
    """What 'good' looks like for the counter behind an objective."""
    REDUCE   = 'reduce',   'Backlog must come DOWN by the target'
    INCREASE = 'increase', 'Count must go UP by the target'
    NIL      = 'nil',      'Must be zero for the week (zero tolerance)'


class WeeklyObjective(AuditableMixin, BaseModel):
    """A standing weekly number one manager must move."""

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='weekly_objectives',
    )
    key = models.SlugField(
        max_length=60,
        help_text='Stable identifier, e.g. "kyc-failed-active". Used in the task source tag.')
    title = models.CharField(max_length=200)
    how_to = models.TextField(
        blank=True, default='',
        help_text='Plain, click-by-click instruction shown on the task — never a vague summary.')
    counter = models.CharField(
        max_length=60,
        help_text='Name registered in hris.objective_counters.COUNTERS.')
    cadence = models.CharField(max_length=10, choices=Cadence.choices,
                               default=Cadence.WEEKLY, db_index=True)
    due_days = models.PositiveSmallIntegerField(
        default=0,
        help_text='ANNUAL only: days after the period start that the task is due. '
                  'Weekly and quarterly due dates are policy, not per-objective — '
                  'see the cycle command.')
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                 default=Direction.REDUCE)
    target = models.PositiveIntegerField(
        default=0,
        help_text='How much must move in one week. Ignored for NIL objectives.')
    note = models.TextField(
        blank=True, default='',
        help_text='Why this target, and what the number was when it was set. '
                  'Without it, a target read a year later looks arbitrary and '
                  'nobody can tell whether it was ambitious or trivial.')
    active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='weekly_objectives_created',
    )

    class Meta(BaseModel.Meta):
        ordering = ['profile__employee__full_name', 'key']
        unique_together = [('profile', 'key')]
        verbose_name = 'Weekly Objective'
        verbose_name_plural = 'Weekly Objectives'
        indexes = [models.Index(fields=['active'], name='wkobj_active_idx')]

    def __str__(self):
        who = getattr(getattr(self.profile, 'employee', None), 'full_name', '') or 'employee'
        return f'{who} — {self.title} ({self.direction} {self.target}/week)'


class WeeklyObjectiveRun(AuditableMixin, BaseModel):
    """One week of one objective: what it was on Sunday, what it was on Wednesday."""

    objective = models.ForeignKey(
        WeeklyObjective, on_delete=models.CASCADE, related_name='runs')
    period_start = models.DateField(
        db_index=True,
        help_text='First day of the period. For a WEEKLY objective this is the '
                  'Monday and it matches OmniTask.week_of; for a quarterly one '
                  'it is the first day of the quarter being reported on.')
    cadence = models.CharField(max_length=10, choices=Cadence.choices,
                               default=Cadence.WEEKLY)
    baseline = models.IntegerField(
        help_text='Counter value read when the task was raised — frozen, never recomputed.')
    target = models.PositiveIntegerField(
        default=0, help_text='Snapshot of the target at raise time.')
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                 default=Direction.REDUCE)
    actual = models.IntegerField(
        null=True, blank=True, help_text='Counter value read at settle time. Null = not settled.')
    met = models.BooleanField(
        null=True, blank=True, help_text='Null until settled.')
    settled_at = models.DateTimeField(null=True, blank=True)
    settle_error = models.TextField(
        blank=True, default='',
        help_text='Why the counter could not be read. A run that could NOT be measured is '
                  'never marked failed — an unreachable database is not a manager missing '
                  'their number.')
    task = models.ForeignKey(
        'core.OmniTask', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='weekly_objective_runs',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-period_start', 'objective__key']
        unique_together = [('objective', 'period_start')]
        verbose_name = 'Weekly Objective Run'
        verbose_name_plural = 'Weekly Objective Runs'
        indexes = [
            models.Index(fields=['period_start', 'met'], name='wkobjrun_week_met_idx'),
        ]

    # ── the arithmetic, in one place ────────────────────────────────────────
    @property
    def moved(self) -> int | None:
        """How far the number travelled in the right direction. None until settled."""
        if self.actual is None:
            return None
        if self.direction == Direction.REDUCE:
            return self.baseline - self.actual
        if self.direction == Direction.INCREASE:
            return self.actual - self.baseline
        return None                      # NIL has no 'movement'

    def evaluate(self) -> bool:
        """Was the period's number hit? Call only once `actual` is set."""
        if self.actual is None:
            raise ValueError('evaluate() before the run was settled')
        if self.direction == Direction.NIL:
            return self.actual == 0
        if self.direction == Direction.REDUCE and self.actual <= 0:
            # An EMPTY backlog is the point of the objective, so it must count
            # as met even when there was nothing left to clear. Without this,
            # "clear 25 a week" turns into a permanent miss the moment the
            # manager finishes the job — which would punish exactly the person
            # who did the work, and is the fastest way to make the whole board
            # ignorable.
            return True
        return (self.moved or 0) >= self.target

    def __str__(self):
        return f'{self.objective.key} from {self.period_start}: {self.baseline}→{self.actual}'
