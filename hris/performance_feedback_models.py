"""
hris/performance_feedback_models.py

ELRA-2025 monthly performance feedback + PIP (CFO directive 2026-06-25).

Botswana's Employment and Labour Relations Act 2025 (in force 1 Jul 2026)
requires a contemporaneous written trail before any performance-based exit:
known standards, evidence, feedback, SUPPORT to improve, opportunity to improve,
employee response. This module captures twelve light-touch monthly records that
aggregate into a defensible trail, with auto-escalation to a formal warning /
PIP after consecutive low ratings.

Reuses omni's spine — BaseModel (UUID + timestamps), AuditableMixin (immutable
audit via core.AuditLog), HRISProfile (the HR record), entity scoping
(profile.employee.company). No separate audit table; no duplicated access layer.

PERSONAL DATA (DPA 2024): every field here is employee personal data. The whole
feature stays DORMANT behind settings.ELRA_PERF_ENABLED until a DPIA + counsel
sign-off; retention_until enforces storage limitation. Escalation thresholds are
CONFIGURABLE POLICY (mirror the disciplinary code), NOT statute.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel
from django.utils import timezone

# Escalation thresholds — POLICY, set to mirror the ELRA-aligned disciplinary
# code (the brief flags 2/3 as choices, not statute). Tune here, not in logic.
CONSEC_LOW_FOR_WARNING = 2
CONSEC_LOW_FOR_PIP = 3
# DPA-2024 storage limitation — placeholder horizon; the DPO sets the real figure.
RETENTION_YEARS = 6


class PerformanceCheckRating(models.TextChoices):
    EXCEEDS   = 'EX', 'Exceeds expectations'
    MEETS     = 'ME', 'Meets expectations'
    PARTIAL   = 'PA', 'Partially meets'
    BELOW     = 'BE', 'Below expectations'
    SIG_BELOW = 'SB', 'Significantly below'
    # CFO 2026-08-26. Used ONLY by the auto-post on the 7th, when the manager
    # never responded. Omni records the month's facts and explicitly does NOT
    # rate the person: a machine-assigned rating would feed PIPs, warnings and
    # flight-risk off data errors, and two wrong escalations have already come
    # out of that (#523). A manager can open it later and rate it properly.
    NOT_RATED = 'NR', 'Not rated — manager did not respond'


# Deliberately excludes NOT_RATED: it must never trigger a PIP, a warning or a
# consecutive-low count. It is the absence of a judgement, not a bad one.
LOW_RATINGS = frozenset({PerformanceCheckRating.BELOW, PerformanceCheckRating.SIG_BELOW})


class EmploymentStatusKind(models.TextChoices):
    PERMANENT = 'PERM',  'Permanent'
    FIXED     = 'FIXED', 'Fixed-term'
    PROBATION = 'PROB',  'Probation'


class MonthlyCheckIn(AuditableMixin, BaseModel):
    """One employee x one month light-touch performance check-in (ELRA trail)."""

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='monthly_checkins',
    )
    reviewer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='hris_checkins_given',
        help_text='Line manager who held the check-in.',
    )
    period_month = models.PositiveSmallIntegerField()   # 1-12
    period_year  = models.PositiveSmallIntegerField()
    conversation_date = models.DateField()
    employment_status = models.CharField(
        max_length=5, choices=EmploymentStatusKind.choices,
        default=EmploymentStatusKind.PERMANENT)

    overall_rating = models.CharField(max_length=2, choices=PerformanceCheckRating.choices)

    # Objectives + corrective actions as JSON (matches omni's competency/okr JSON
    # convention) — no extra child tables.
    objectives = models.JSONField(
        default=list, blank=True,
        help_text='[{description, target, result, score}] — monthly KPI snapshot.')
    improvement_actions = models.JSONField(
        default=list, blank=True,
        help_text='[{action, owner, due_date, status}] — agreed corrective actions.')

    evidence          = models.TextField(blank=True, default='')   # required if low (clean())
    strengths         = models.TextField(blank=True, default='')
    concerns          = models.TextField(blank=True, default='')   # required if low (clean())
    support_provided  = models.TextField(blank=True, default='',
        help_text='ELRA "support / opportunity to improve" evidence.')
    manager_comments  = models.TextField(blank=True, default='')
    employee_response = models.TextField(blank=True, default='')

    # The employee's own verdict on the manager's feedback (CFO 2026-08-18):
    # they may Accept, Partially accept, or Decline. On partial/decline they must
    # reply to the manager with a substantive reason (>= 50 words) — enforced in
    # performance_views (the self-tier PATCH). Blank until they respond.
    class EmployeeDecision(models.TextChoices):
        ACCEPT  = 'accept',  'Accept'
        PARTIAL = 'partial', 'Partially accept'
        DECLINE = 'decline', 'Do not accept'

    employee_decision = models.CharField(
        max_length=8, choices=EmployeeDecision.choices, blank=True, default='')

    employee_ack    = models.BooleanField(default=False)
    employee_ack_at = models.DateTimeField(null=True, blank=True)

    recurring_issue       = models.BooleanField(default=False)
    consecutive_low_count = models.PositiveSmallIntegerField(default=0)   # computed
    warning_recommended   = models.BooleanField(default=False)           # computed
    pip_triggered         = models.BooleanField(default=False)           # computed
    referred_to_hr        = models.BooleanField(default=False)

    # --- posted by Omni because the manager stayed silent (CFO 2026-08-26) --- #
    auto_posted    = models.BooleanField(
                         default=False, db_index=True,
                         help_text='Written by Omni on the 7th because the manager '
                                   'did not confirm or edit the draft.')
    auto_posted_at = models.DateTimeField(null=True, blank=True)

    # --- the employee's right of reply (CFO 2026-08-26: "so it's going to be
    #     fair"). Declining is already possible; this is the part that makes the
    #     manager answer it. Cleared when the manager responds. --- #
    employee_requested_comments = models.BooleanField(
                                      default=False, db_index=True,
                                      help_text='Employee asked the manager to add '
                                                'comments before they accept.')
    employee_requested_at = models.DateTimeField(null=True, blank=True)
    manager_followup      = models.TextField(
                                blank=True, default='',
                                help_text="The manager's answer to the employee's "
                                          'request for more comment.')
    manager_followup_at   = models.DateTimeField(null=True, blank=True)
    manager_followup_by   = models.ForeignKey(
                                User, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+')

    follow_up_date     = models.DateField(null=True, blank=True)
    manager_signed_at  = models.DateTimeField(null=True, blank=True)
    employee_signed_at = models.DateTimeField(null=True, blank=True)
    is_locked          = models.BooleanField(default=False)       # immutable after both sign-offs
    retention_until    = models.DateField(null=True, blank=True)  # DPA-2024 storage limit

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_month', 'profile__employee__full_name']
        unique_together = [('profile', 'period_month', 'period_year')]
        indexes = [
            models.Index(fields=['profile', 'period_year', 'period_month'],
                         name='hris_checkin_prof_prd_idx'),
        ]
        verbose_name = 'Monthly Performance Check-in'
        verbose_name_plural = 'Monthly Performance Check-ins'

    def __str__(self):
        return (f'{self.profile.employee.full_name} — '
                f'{self.period_year}-{self.period_month:02d} ({self.overall_rating})')

    # -- evidentiary guard (ELRA): no undocumented low rating -----------------
    def clean(self):
        super().clean()
        if self.overall_rating in LOW_RATINGS:
            if not (self.evidence or '').strip():
                raise ValidationError(
                    {'evidence': 'Evidence is required for a Below / Significantly-below rating.'})
            if not (self.concerns or '').strip():
                raise ValidationError(
                    {'concerns': 'Areas of concern are required for a Below / Significantly-below rating.'})

    # -- escalation: consecutive low calendar months --------------------------
    def _recompute_escalation(self):
        if self.overall_rating not in LOW_RATINGS:
            self.consecutive_low_count = 0
            self.warning_recommended = False
            self.pip_triggered = False
            return
        priors = {
            (r.period_year, r.period_month): r.overall_rating
            for r in MonthlyCheckIn.objects.filter(profile_id=self.profile_id).exclude(pk=self.pk)
        }
        count, y, m = 1, self.period_year, self.period_month
        for _ in range(120):   # safety bound
            m -= 1
            if m == 0:
                m, y = 12, y - 1
            if priors.get((y, m)) in LOW_RATINGS:
                count += 1
            else:
                break
        self.consecutive_low_count = count
        self.warning_recommended = count >= CONSEC_LOW_FOR_WARNING
        self.pip_triggered = count >= CONSEC_LOW_FOR_PIP

    def save(self, *args, **kwargs):
        self._recompute_escalation()
        if self.consecutive_low_count >= CONSEC_LOW_FOR_WARNING:
            self.recurring_issue = True
        if self.manager_signed_at and self.employee_signed_at:
            self.is_locked = True
        if not self.retention_until and self.conversation_date:
            self.retention_until = self.conversation_date + dt.timedelta(days=365 * RETENTION_YEARS)
        super().save(*args, **kwargs)
        if self.pip_triggered:
            self._ensure_pip()

    def _ensure_pip(self):
        open_states = (PerformanceImprovementPlan.Status.OPEN,
                       PerformanceImprovementPlan.Status.IN_PROGRESS)
        if PerformanceImprovementPlan.objects.filter(
                profile_id=self.profile_id, status__in=open_states).exists():
            return
        PerformanceImprovementPlan.objects.create(
            profile_id=self.profile_id,
            opened_from_checkin=self,
            reason=f'Auto-opened after {self.consecutive_low_count} consecutive months below standard.',
            start_date=self.conversation_date,
        )


class PerformanceImprovementPlan(AuditableMixin, BaseModel):
    """Formal PIP — auto-opened after CONSEC_LOW_FOR_PIP consecutive low months, or
    opened manually by HR. The ELRA 'opportunity to improve' container."""

    class Status(models.TextChoices):
        OPEN        = 'open',        'Open'
        IN_PROGRESS = 'in_progress', 'In progress'
        MET         = 'met',         'Objectives met'
        NOT_MET     = 'not_met',     'Objectives not met'
        CLOSED      = 'closed',      'Closed'

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE, related_name='pips')
    opened_from_checkin = models.ForeignKey(
        MonthlyCheckIn, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='pips_opened')
    reason       = models.TextField(blank=True, default='')
    objectives   = models.TextField(blank=True, default='')
    support_plan = models.TextField(blank=True, default='',
        help_text='Training / mentoring / resources offered — ELRA support evidence.')
    start_date   = models.DateField(null=True, blank=True)
    review_date  = models.DateField(null=True, blank=True)
    end_date     = models.DateField(null=True, blank=True)
    status       = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    outcome      = models.TextField(blank=True, default='')
    opened_by    = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='hris_pips_opened')
    hr_acknowledged = models.BooleanField(default=False)

    # ── CFO-directed PIP (directive 2026-07-15) ──────────────────────────────
    # A PIP raised directly by management for a specific documented issue, shown
    # to a NAMED audience on the employee's dashboard, independent of the dormant
    # ELRA monthly-check-in module (ELRA_PERF_ENABLED). The auto-escalation path
    # above is untouched; `directed` just marks the manually-raised ones and opens
    # the tight per-record visibility used by hris/pip_views.py.
    directed     = models.BooleanField(default=False, db_index=True)
    title        = models.CharField(max_length=200, blank=True, default='')
    # The employee's own account of the issue — the "opportunity to respond"
    # (ELRA) and the box the CFO asked for. Only the subject may fill it.
    employee_explanation    = models.TextField(blank=True, default='')
    employee_explanation_at = models.DateTimeField(null=True, blank=True)
    # Named people (besides the subject + HR) who may VIEW this directed PIP —
    # lowercased email addresses. Read-only access; they cannot edit or explain.
    viewer_emails = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-start_date', 'profile__employee__full_name']
        verbose_name = 'Performance Improvement Plan'
        verbose_name_plural = 'Performance Improvement Plans'

    def __str__(self):
        return f'PIP — {self.profile.employee.full_name} ({self.status})'


class MonthlyFeedbackNotice(BaseModel):
    """One row per (manager, period, kind) notice actually sent.

    The idempotency record for monthly_feedback_notice — a double cron run, or a
    manual re-run, must not re-mail a manager. It is also the evidence trail for
    the thing the CFO actually cares about (2026-08-26: "since it's not
    compulsory people are not really bothered"): who was told, when, and whether
    they ever responded. `silent_months_for()` reads it.
    """

    class Kind(models.TextChoices):
        # CFO 2026-09-09 moved the whole cycle: facts to managers on the 2nd,
        # a reminder to BOTH the manager and the staff member on the 4th and
        # again on the 7th, auto-post on the 10th. The old 5th/6th/7th labels
        # are kept as the same stored values so existing rows still read.
        NOTICE    = 'notice',    'Facts sent to the manager (2nd)'
        REMINDER  = 'reminder',  'First reminder (4th)'
        REMINDER2 = 'reminder2', 'Second reminder (7th)'
        AUTOPOST  = 'autopost',  'Auto-posted (10th)'

    manager      = models.ForeignKey('payroll.Employee', on_delete=models.CASCADE,
                                     related_name='feedback_notices')
    period_year  = models.PositiveSmallIntegerField()
    period_month = models.PositiveSmallIntegerField()
    kind         = models.CharField(max_length=10, choices=Kind.choices)
    outstanding  = models.PositiveSmallIntegerField(default=0)
    team_size    = models.PositiveSmallIntegerField(default=0)
    sent_to      = models.CharField(max_length=200, blank=True, default='')
    sent_at      = models.DateTimeField(auto_now_add=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=['manager', 'period_year', 'period_month', 'kind'],
                name='uniq_feedback_notice_mgr_period_kind'),
        ]
        indexes = [models.Index(fields=['period_year', 'period_month', 'kind'])]

    def __str__(self):
        return (f'{self.manager} {self.period_year}-{self.period_month:02d} '
                f'{self.kind}')


def silent_months_for(manager_employee, months: int = 6) -> int:
    """How many of the last `months` periods this manager let auto-post.

    The CFO's root cause is that feedback is not compulsory, so nobody bothers.
    A manager who never responds is a manager-performance fact, and this is the
    number that says so — it belongs on THEIR record, not the employee's.

    This is a COUNT, not a run. It deliberately makes no claim about consecutive
    months: January + June is 2, and any copy built on it must say "2 of the last
    six months", never "2 months in a row" (Fable, 2026-08-26).
    """
    # A real window. `[:months].count()` counted the most recent AUTOPOST rows
    # across ALL history capped at 6, so two autoposts from a year ago rendered as
    # "in 2 of the last six months" in red bold — false (Fable round 2).
    today = timezone.localdate()
    y, m = today.year, today.month
    m -= (months - 1)
    while m <= 0:
        m += 12
        y -= 1
    floor = y * 12 + m
    rows = MonthlyFeedbackNotice.objects.filter(
        manager=manager_employee, kind=MonthlyFeedbackNotice.Kind.AUTOPOST,
    ).values_list('period_year', 'period_month')
    return sum(1 for (py, pm) in rows if (py * 12 + pm) >= floor)
