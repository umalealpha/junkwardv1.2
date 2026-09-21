"""
hris/co_review_models.py — joint (50/50) monthly rating (CFO 2026-07-26).

Written after Kago Tshutlhedi tested the Monthly Manager Return and asked, fairly,
why the senior accountants were not on his team. They report to the CFO — but the
Finance Manager works with them every day, so the CFO's decision is that BOTH of
them rate those people and the score is SPLIT 50/50.

Two reasons it matters, in the CFO's words: the Finance Manager should have a say
because he actually works with them, and the CFO needs a live read on the bench so
that if the Finance Manager leaves, someone can be promoted quickly.

Design notes:
  * `HRISProfile.co_manager` is the org fact — who the second rater is. The real
    `manager` is untouched, so leave, approvals and the org chart do not move.
  * ONE row per (employee, month, rater). Each rater sees their own box; neither
    can edit the other's. The combined score is COMPUTED, never stored as an
    editable field, so it can't drift from its parts.
  * Scores are 0-100 so a weighted split is honest arithmetic. Letter grades would
    have to be mapped in and out and would lose the half-marks.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel


class RaterRole(models.TextChoices):
    LINE       = 'line',  'Line manager'
    CO         = 'co',    'Co-reviewer'
    # An OPERATIONS reviewer who oversees this person across departments (CFO
    # 2026-08-12, after Bharath listed cross-department people he must review).
    # Unlike CO, an additional reviewer's score is RECORDED and VISIBLE but is
    # deliberately kept OUT of the line/co weighted blend, so it never disturbs an
    # existing split (e.g. the CFO/Finance-Manager 50/50 on the senior accountants).
    ADDITIONAL = 'extra', 'Additional reviewer'


class CoRating(AuditableMixin, BaseModel):
    """One rater's monthly score for one employee."""

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE, related_name='co_ratings')
    period_month = models.PositiveSmallIntegerField()
    period_year  = models.PositiveSmallIntegerField()

    rater = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='co_ratings_given')
    rater_role = models.CharField(max_length=8, choices=RaterRole.choices)

    score = models.PositiveSmallIntegerField(
        help_text='0-100. Weighted with the other rater into the combined score.')
    comment = models.TextField(blank=True, default='')

    # Locked once both sides are in and the CFO signs the month off, mirroring the
    # monthly return. Until then a rater may revise their own score.
    is_locked = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ['-period_year', '-period_month', 'profile__employee__full_name']
        unique_together = [('profile', 'period_month', 'period_year', 'rater')]
        indexes = [
            models.Index(fields=['profile', 'period_year', 'period_month'],
                         name='hris_corate_prof_prd_idx'),
        ]
        verbose_name = 'Joint monthly rating'
        verbose_name_plural = 'Joint monthly ratings'

    def __str__(self):
        return (f'{self.profile.employee.full_name} '
                f'{self.period_year}-{self.period_month:02d} '
                f'{self.rater_role}={self.score}')

    def clean(self):
        super().clean()
        if self.score is None or not (0 <= int(self.score) <= 100):
            raise ValidationError({'score': 'Score must be between 0 and 100.'})
        if not (1 <= int(self.period_month or 0) <= 12):
            raise ValidationError({'period_month': 'Month must be 1-12.'})
        if not (2020 <= int(self.period_year or 0) <= 2100):
            raise ValidationError({'period_year': 'Year must be between 2020 and 2100.'})


class AdditionalReviewer(AuditableMixin, BaseModel):
    """An extra person who may review + give feedback on an employee, on top of the
    line manager and any co-reviewer (CFO 2026-08-12).

    The org fact only: who the operations reviewer is. It does NOT approve leave, is
    not the line manager, and — unlike `HRISProfile.co_manager` — carries no weight,
    so it never enters the line/co blend. Its purpose is a manager who oversees
    operations across departments (Bharath) being able to see and rate people who
    line-report elsewhere, without moving their org chart or disturbing an existing
    co-review split.
    """

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE, related_name='additional_reviewers')
    reviewer = models.ForeignKey(
        'payroll.Employee', on_delete=models.CASCADE, related_name='additional_reviews')
    reason = models.CharField(
        max_length=120, blank=True, default='',
        help_text='Why this person reviews them, e.g. "Operations oversight".')

    class Meta(BaseModel.Meta):
        ordering = ['profile__employee__full_name', 'reviewer__full_name']
        unique_together = [('profile', 'reviewer')]
        indexes = [
            models.Index(fields=['reviewer'], name='hris_addrev_reviewer_idx'),
        ]
        verbose_name = 'Additional reviewer'
        verbose_name_plural = 'Additional reviewers'

    def __str__(self):
        who = self.reviewer.full_name if self.reviewer_id else '?'
        whom = self.profile.employee.full_name if self.profile_id else '?'
        return f'{who} additionally reviews {whom}'

    def clean(self):
        super().clean()
        # Never let the additional reviewer be the person themselves.
        if self.profile_id and self.reviewer_id and self.profile.employee_id == self.reviewer_id:
            raise ValidationError('Someone cannot be their own additional reviewer.')


def combined_score(profile, year: int, month: int) -> dict:
    """The 50/50 (or configured) blend for one employee-month.

    Returns both parts and the blend, plus what is still outstanding. Deliberately
    does NOT invent a number from one side: with only one rater in, `combined` is
    that rater's score and `complete` is False, so the UI can say "waiting on the
    other person" instead of quietly presenting a half-formed score as final.
    """
    co_weight = int(getattr(profile, 'co_manager_weight', 50) or 0)
    co_weight = max(0, min(100, co_weight))
    line_weight = 100 - co_weight

    # Partition explicitly: there is at most one LINE and one CO row, but there may
    # be MANY ADDITIONAL rows (one per operations reviewer). A dict keyed on role
    # would silently drop all but the last additional row — so keep them as a list.
    all_rows = list(CoRating.objects.filter(
        profile=profile, period_year=year, period_month=month).select_related('rater'))
    line = next((r for r in all_rows if r.rater_role == RaterRole.LINE), None)
    co = next((r for r in all_rows if r.rater_role == RaterRole.CO), None)
    extras = [r for r in all_rows if r.rater_role == RaterRole.ADDITIONAL]

    out = {
        'line_score': line.score if line else None,
        'line_comment': line.comment if line else '',
        'co_score': co.score if co else None,
        'co_comment': co.comment if co else '',
        'line_weight': line_weight,
        'co_weight': co_weight,
        'complete': bool(line and co),
        'combined': None,
        'waiting_on': [],
        # Additional (operations) reviewers sit OUTSIDE the blend — advisory only.
        'additional': [
            {'rater_id': r.rater_id, 'score': r.score, 'comment': r.comment}
            for r in extras
        ],
    }
    if line and co:
        out['combined'] = round((line.score * line_weight + co.score * co_weight) / 100, 1)
    elif line:
        out['combined'] = float(line.score)
        out['waiting_on'] = ['co-reviewer']
    elif co:
        out['combined'] = float(co.score)
        out['waiting_on'] = ['line manager']
    else:
        out['waiting_on'] = ['line manager', 'co-reviewer']
    # A big gap between two raters is the interesting signal — it means they
    # genuinely disagree about the person and someone should talk.
    if line and co:
        out['spread'] = abs(line.score - co.score)
        out['disputed'] = out['spread'] >= 20
    else:
        out['spread'] = None
        out['disputed'] = False
    return out
