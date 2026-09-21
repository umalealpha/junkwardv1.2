"""
hris/manager_return_models.py — the Monthly Manager Return (CFO 2026-07-26).

The SHORT monthly accountability return a people-manager files about THEIR OWN
team and their own management of it. Deliberately small: the Development
Dialogue (hris/talent_cockpit_models.DevelopmentDialogue) is the big annual
instrument; this is the light monthly one.

Distinct from MonthlyCheckIn (hris/performance_feedback_models): that is the
manager writing about EACH team member. This is the manager answering for
themselves and the team as a unit, upward to their own manager.

The clock (CFO): task appears on the 1st -> manager submits by the 5th ->
their manager clears by the 10th.

The facts half of the form is NOT typed. It is captured from omni's own stores
(Time Doctor hours via WorkdayJustification, leave, tasks) and FROZEN onto the
row at submit, so the return stays a faithful record of what was true when it
was signed — a later data correction must never silently rewrite history.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel

# Filing clock — POLICY, not statute. Tune here, never in the views.
SUBMIT_BY_DAY = 5    # manager files by the 5th
CLEAR_BY_DAY = 10    # their manager clears by the 10th


def _curated_keys() -> frozenset[str]:
    """Every curated department question key. Imported lazily so the models
    module stays import-safe (questions imports nothing from here)."""
    from hris.manager_return_questions import DEPARTMENT_BLOCKS
    return frozenset(q['key'] for qs in DEPARTMENT_BLOCKS.values() for q in qs)


class ReturnStatus(models.TextChoices):
    DRAFT     = 'draft',     'Draft'
    SUBMITTED = 'submitted', 'Submitted — awaiting manager'
    RETURNED  = 'returned',  'Sent back for more detail'
    CLEARED   = 'cleared',   'Cleared'


class ReviewVerdict(models.TextChoices):
    ON_TRACK  = 'on_track',  'On track'
    WATCH     = 'watch',     'Watch'
    INTERVENE = 'intervene', 'Intervene'


class ManagerMonthlyReturn(AuditableMixin, BaseModel):
    """One people-manager x one month."""

    manager = models.ForeignKey(
        'payroll.Employee', on_delete=models.CASCADE,
        related_name='monthly_returns',
        help_text='The people-manager filing the return.')
    period_month = models.PositiveSmallIntegerField()
    period_year  = models.PositiveSmallIntegerField()

    status = models.CharField(max_length=10, choices=ReturnStatus.choices,
                              default=ReturnStatus.DRAFT)

    # ── the facts half: frozen at submit, never typed ────────────────────────
    team_snapshot = models.JSONField(
        default=list, blank=True,
        help_text='[{name, required_hours, tracked_hours, shortfall, unexplained_days, '
                  'leave_days, tasks_assigned, tasks_completed, tasks_on_time_pct}] '
                  '— frozen at submit.')
    own_snapshot = models.JSONField(
        default=dict, blank=True,
        help_text="The manager's own hours/tasks for the month — frozen at submit.")
    headcount_equivalent = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text='Team actual hours / required hours per head — the overstaffing signal. '
                  'e.g. 6 people delivering 4.2 people of hours.')

    # ── the answers half ────────────────────────────────────────────────────
    # Hours & leave discipline
    leave_action = models.TextField(
        blank=True, default='',
        help_text='What the manager DID about people who lost hours without leave '
                  'or an explanation.')
    # SLA — a declaration (CFO 2026-07-26: no SLA store to read yet, so ask).
    sla_breaches = models.PositiveIntegerField(null=True, blank=True)
    sla_explanation = models.TextField(blank=True, default='')
    # Sales — a declaration only (CFO 2026-07-26: "just ask the question, they
    # will say yes or no and put a place for new sales amount").
    sales_target_met = models.BooleanField(null=True, blank=True)
    new_sales_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='New sales written in the month, in BWP. Declaration.')
    # Delivery
    work_finished_on_time = models.BooleanField(null=True, blank=True)
    work_on_time_comment = models.TextField(blank=True, default='')
    dashboard_cleared_on_time = models.BooleanField(null=True, blank=True)
    tasks_comment = models.TextField(blank=True, default='')
    # Capacity — pre-answered by headcount_equivalent, then challenged.
    overstaffed = models.BooleanField(null=True, blank=True)
    overstaffed_comment = models.TextField(blank=True, default='')
    # Strategy
    fy27_aligned = models.BooleanField(null=True, blank=True)
    fy27_actions = models.TextField(blank=True, default='')
    # Innovation
    innovation = models.TextField(blank=True, default='')
    # ── role-specific half (CFO 2026-07-26: "we can't ask IT team and driver
    # for sales targets"). Department + Aria-generated questions live in JSON so
    # a new department never needs a migration. The spec that was actually shown
    # is frozen alongside the answers at submit, so a later change to the
    # question set cannot make a signed return unreadable.
    dept_answers = models.JSONField(
        default=dict, blank=True,
        help_text='{question_key: answer} for the department / Aria questions.')
    question_spec = models.JSONField(
        default=dict, blank=True,
        help_text='The question set shown to this manager — frozen at submit.')

    # Accountability loop — last month's promises, replayed.
    prior_commitments = models.TextField(
        blank=True, default='',
        help_text="Copied from last month's fy27_actions at draft time.")
    prior_outcome = models.TextField(
        blank=True, default='',
        help_text='What actually happened to last month\'s commitments.')
    # This month's promise, which becomes next month's prior_commitments.
    next_month_commitment = models.TextField(blank=True, default='')

    # ── workflow ────────────────────────────────────────────────────────────
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_to = models.ForeignKey(
        'payroll.Employee', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='monthly_returns_to_review',
        help_text="The filer's own manager, resolved at submit.")
    reviewer_verdict = models.CharField(
        max_length=10, choices=ReviewVerdict.choices, blank=True, default='')
    reviewer_notes = models.TextField(blank=True, default='')
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='monthly_returns_cleared')
    # Immutable once cleared — this is an employment record (mirrors the leave
    # encashment lock). A cleared return must never be editable afterwards.
    is_locked = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_month', 'manager__full_name']
        unique_together = [('manager', 'period_month', 'period_year')]
        indexes = [
            models.Index(fields=['manager', 'period_year', 'period_month'],
                         name='hris_mgrret_mgr_prd_idx'),
            models.Index(fields=['status'], name='hris_mgrret_status_idx'),
        ]
        verbose_name = 'Monthly Manager Return'
        verbose_name_plural = 'Monthly Manager Returns'

    def __str__(self):
        return (f'{self.manager.full_name} — '
                f'{self.period_year}-{self.period_month:02d} ({self.status})')

    # ── dates ───────────────────────────────────────────────────────────────
    @property
    def submit_due(self) -> dt.date:
        """Due the 5th of the month AFTER the period being reported on."""
        y, m = self.period_year, self.period_month + 1
        if m == 13:
            y, m = y + 1, 1
        return dt.date(y, m, SUBMIT_BY_DAY)

    @property
    def clear_due(self) -> dt.date:
        y, m = self.period_year, self.period_month + 1
        if m == 13:
            y, m = y + 1, 1
        return dt.date(y, m, CLEAR_BY_DAY)

    # ── completeness gate ───────────────────────────────────────────────────
    # Short and sweet, but not blank. These are the questions the CFO named as
    # the point of the exercise; a return with them empty says nothing.
    REQUIRED_ON_SUBMIT = (
        ('leave_action', 'What you did about lost hours / missing leave'),
        ('innovation', 'What you improved or automated'),
        ('fy27_actions', 'What you are doing to hit the FY27 targets'),
    )
    # NOTE: sales is NOT here. It is only required when the manager's role is
    # revenue-facing (CFO 2026-07-26 — never ask IT or a driver about sales).
    REQUIRED_BOOLS_ON_SUBMIT = (
        ('work_finished_on_time', 'Was the work provided finished on time?'),
        ('dashboard_cleared_on_time', 'Was your omni dashboard cleared on time?'),
        ('overstaffed', 'Are you overstaffed?'),
        ('fy27_aligned', 'Is your team aligned to the FY27 targets?'),
    )

    def missing_for_submit(self) -> list[str]:
        """What still blocks submission, in plain English the filer can act on."""
        from hris.manager_return_questions import wants_sales

        gaps = [label for field, label in self.REQUIRED_ON_SUBMIT
                if not (getattr(self, field) or '').strip()]
        gaps += [label for field, label in self.REQUIRED_BOOLS_ON_SUBMIT
                 if getattr(self, field) is None]
        # Sales only applies to revenue-facing roles.
        emp = self.manager
        if wants_sales(getattr(emp, 'department', '') or '',
                      getattr(emp, 'job_title', '') or '') and self.sales_target_met is None:
            gaps.append('Did your team meet its sales target?')
        # A "yes, overstaffed" with no plan is not an answer.
        if self.overstaffed is True and not (self.overstaffed_comment or '').strip():
            gaps.append('You said you are overstaffed — say what you propose to do')
        # Declared SLA breaches must be explained (the CFO's claims-team concern).
        if (self.sla_breaches or 0) > 0 and not (self.sla_explanation or '').strip():
            gaps.append('You declared SLA breaches — explain why they happened')
        # Every department / Aria question that was SHOWN must be answered. The
        # spec is read from question_spec once frozen, else rebuilt live.
        for key, label in self._shown_extra_questions():
            val = (self.dept_answers or {}).get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                gaps.append(label)
        return gaps

    def _shown_extra_questions(self) -> list[tuple[str, str]]:
        """(key, label) for the department/Aria questions this return shows."""
        spec = self.question_spec or {}
        if not spec:
            return []
        out = []
        for block in spec.get('blocks', []):
            for q in block.get('questions', []):
                key = q.get('key') or ''
                # Only the JSON-backed extras; the base fields are real columns.
                if key.startswith('ai_') or key in _curated_keys():
                    out.append((key, q.get('label') or key))
        return out

    def clean(self):
        super().clean()
        if not (1 <= int(self.period_month or 0) <= 12):
            raise ValidationError({'period_month': 'Month must be 1-12.'})
        # Defence in depth: the view already clamps the period, but the model must
        # not be able to store a nonsense year via any other path.
        if not (2020 <= int(self.period_year or 0) <= 2100):
            raise ValidationError({'period_year': 'Year must be between 2020 and 2100.'})
        if self.new_sales_amount is not None and self.new_sales_amount < 0:
            raise ValidationError({'new_sales_amount': 'New sales cannot be negative.'})
        if self.sla_breaches is not None and self.sla_breaches > 10000:
            raise ValidationError({'sla_breaches': 'That is not a plausible breach count.'})
