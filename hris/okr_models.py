"""
hris/okr_models.py — Key Results and check-ins under an Objective.

Unami's request (email "Omni System | HR & Talent Management", 30 Jul 2026) with
her own OKR training deck attached:

  * "Allow to add sub objectives (target)"
  * "The objectives can they appear when the employee logs in with an update"
  * "The department should create sub objectives - information attached"
  * "Team should add their sub-objectives (how will they achieve)"

Her deck defines the shape, so the models follow it rather than inventing one:

    OBJECTIVE      → "Where do I need to go?"      (hris.models.OKR, exists)
    KEY RESULT     → "How do I know I'm getting there?"   (KeyResult, here)
    INITIATIVE     → "What will I do to get there?"       (KeyResult.kind=initiative)

A Key Result is measurable — the deck's examples are "Increase __ from X to Y" and
"Reduce __ by X%" — so it carries start / target / current values and a unit, and
computes its own attainment. That is deliberately NOT the same thing as the
existing `OKR.score_h1 / score_h2`, which are the 0-5 APPRAISAL scores HR records
at mid-year and year-end and which feed the 9-box grid. Self-reported progress
must never silently move an appraisal score, so the two live apart: check-ins
answer "are we on track", H1/H2 answer "what was this person rated".

Check-ins are the deck's step 5 ("through the quarter, employees measure and share
their progress … contributors assess how likely they are to fully achieve their
OKRs"). They are append-only: a progress history you can read back, not a single
number that quietly overwrites itself.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.models import AuditableMixin, BaseModel


class KeyResult(AuditableMixin, BaseModel):
    """A measurable Key Result — or an Initiative — under one Objective."""

    class Kind(models.TextChoices):
        KEY_RESULT = 'key_result', 'Key Result'      # how we measure the objective
        INITIATIVE = 'initiative', 'Initiative'      # what we will DO about it

    class Direction(models.TextChoices):
        INCREASE = 'increase', 'Increase to target'
        DECREASE = 'decrease', 'Reduce to target'
        DONE     = 'done',     'Done / not done'

    objective = models.ForeignKey(
        'hris.OKR', on_delete=models.CASCADE, related_name='key_results')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.KEY_RESULT)

    name = models.CharField(max_length=250)
    description = models.TextField(blank=True, default='')

    # Measurement. `unit` is free text ('%', 'BWP', 'policies', 'days') because
    # the deck allows "a 0-100% scale or any numerical unit".
    direction = models.CharField(max_length=10, choices=Direction.choices,
                                 default=Direction.INCREASE)
    unit = models.CharField(max_length=30, blank=True, default='')
    start_value   = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    target_value  = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    current_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)

    # Who owns delivering this. Null = owned by the objective itself (a department
    # or company Key Result nobody has picked up yet).
    owner = models.ForeignKey(
        'hris.HRISProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='key_results')

    due_date = models.DateField(null=True, blank=True)
    is_done = models.BooleanField(default=False)
    # Weight within the parent objective. 0 = weight it equally with its siblings.
    weight_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))

    class Meta(BaseModel.Meta):
        ordering = ['objective', 'kind', '-weight_pct', 'name']
        indexes = [
            models.Index(fields=['objective', 'kind'], name='hris_kr_obj_kind_idx'),
            models.Index(fields=['owner'], name='hris_kr_owner_idx'),
        ]
        verbose_name = 'Key Result'
        verbose_name_plural = 'Key Results'

    def __str__(self):
        return f'{self.name} ({self.get_kind_display()})'

    @property
    def attainment_pct(self):
        """0-100 attainment, or None when it cannot be computed yet.

        DONE-type results are binary. Otherwise progress runs from `start_value`
        toward `target_value`, which handles "reduce X to Y" without a special
        case: the denominator is the distance to travel, whichever way it points.
        Returns None (not 0) when there is nothing to measure — "not reported" and
        "reported as zero" must stay distinguishable.
        """
        if self.direction == self.Direction.DONE or self.target_value is None:
            return 100.0 if self.is_done else (0.0 if self.current_value is not None else None)
        if self.current_value is None:
            return None
        start = Decimal('0') if self.start_value is None else self.start_value
        span = self.target_value - start
        if span == 0:
            # Target equals the starting point — met iff we are at or past it.
            met = (self.current_value >= self.target_value
                   if self.direction == self.Direction.INCREASE
                   else self.current_value <= self.target_value)
            return 100.0 if met else 0.0
        pct = (self.current_value - start) / span * Decimal('100')
        return float(max(Decimal('0'), min(Decimal('100'), pct)).quantize(Decimal('0.1')))


class OKRCheckIn(AuditableMixin, BaseModel):
    """One progress update against a Key Result (deck step 5).

    Append-only history. `confidence` is the deck's "how likely they are to fully
    achieve" so a manager can see trouble coming before the score lands.
    """

    class Confidence(models.TextChoices):
        ON_TRACK = 'on_track', 'On track'
        AT_RISK  = 'at_risk',  'At risk'
        OFF      = 'off',      'Off track'

    key_result = models.ForeignKey(
        KeyResult, on_delete=models.CASCADE, related_name='check_ins')
    # Who filed it. Kept even if the profile is later removed, hence SET_NULL.
    author = models.ForeignKey(
        'hris.HRISProfile', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='okr_check_ins')
    author_email = models.CharField(max_length=200, blank=True, default='')

    value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True,
                                help_text='The measured value at this check-in.')
    confidence = models.CharField(max_length=10, choices=Confidence.choices,
                                  default=Confidence.ON_TRACK)
    note = models.TextField(blank=True, default='',
                            help_text='What moved, what is blocked, what changes next.')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['key_result', '-created_at'], name='hris_ci_kr_created_idx'),
        ]
        verbose_name = 'OKR check-in'
        verbose_name_plural = 'OKR check-ins'

    def __str__(self):
        return f'{self.key_result_id} @ {self.value} ({self.confidence})'
