"""Internal Audit — data model (Phase 1: Engagement stub, Findings, Follow-up).

Structure IS the control (spec Module 5). The five-element findings rule, the
forced root-cause taxonomy, and the auto-computed rating are enforced here in
`clean()` so they cannot be bypassed, not left to auditor discipline.

Models subclass core (AuditableMixin, BaseModel) so every create/edit writes an
immutable AuditLog row with the acting user — the tamper-evident trail that
backs the function's independence.
"""
from __future__ import annotations

import datetime as dt

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel
from .rating import compute_rating, retest_interval_days


# ---------------------------------------------------------------------------
# Engagement — Phase 1 stub. Full planning (memo, scope, work programme) lands
# in Phase 2; for now it is the container findings hang off.
# ---------------------------------------------------------------------------
class Engagement(AuditableMixin, BaseModel):
    class Type(models.TextChoices):
        ASSURANCE = 'assurance', 'Assurance'
        ADVISORY = 'advisory', 'Advisory'
        INVESTIGATION = 'investigation', 'Investigation'

    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not started'
        PLANNING = 'planning', 'Planning'
        FIELDWORK = 'fieldwork', 'Fieldwork'
        REPORTING = 'reporting', 'Reporting'
        FOLLOW_UP = 'follow_up', 'Follow-up'
        CLOSED = 'closed', 'Closed'

    reference = models.CharField(max_length=40, unique=True)
    title = models.CharField(max_length=200)
    engagement_type = models.CharField(max_length=20, choices=Type.choices, default=Type.ASSURANCE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    lead_auditor = models.CharField(max_length=120, blank=True, default='')
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reference} — {self.title}'


# ---------------------------------------------------------------------------
# Finding — the register. Five mandatory elements + forced root cause + rating.
# ---------------------------------------------------------------------------
class Finding(AuditableMixin, BaseModel):
    class RootCause(models.TextChoices):
        # Forced taxonomy. 'Human error' and 'oversight' are deliberately NOT
        # selectable terminal values — the auditor must name a deeper category.
        CONTROL_DESIGN = 'control_design', 'Control design failure'
        MISSING_CONTROL = 'missing_control', 'Missing control'
        TRAINING_GAP = 'training_gap', 'Training gap'
        SYSTEM_LIMITATION = 'system_limitation', 'System limitation'
        TONE_AT_THE_TOP = 'tone_at_the_top', 'Tone-at-the-top'
        RESOURCE_CONSTRAINT = 'resource_constraint', 'Resource constraint'
        OTHER = 'other', 'Other (justify)'

    class EffectCategory(models.TextChoices):
        FINANCIAL = 'financial', 'Financial'
        REGULATORY = 'regulatory', 'Regulatory'
        REPUTATIONAL = 'reputational', 'Reputational'
        OPERATIONAL = 'operational', 'Operational'
        FRAUD = 'fraud', 'Fraud exposure'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        AGREED = 'agreed', 'Agreed with management'
        ISSUED = 'issued', 'Issued'
        CLOSED = 'closed', 'Closed'

    engagement = models.ForeignKey(Engagement, on_delete=models.CASCADE, related_name='findings')
    reference = models.CharField(max_length=40, blank=True, default='')
    title = models.CharField(max_length=200)

    # The five mandatory elements ------------------------------------------
    criteria = models.TextField(help_text='The standard / policy / expectation.')
    condition = models.TextField(help_text='What was found. Must cite supporting evidence.')
    evidence_reference = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Workpaper / evidence reference backing the condition.')
    root_cause = models.CharField(max_length=30, choices=RootCause.choices)
    root_cause_justification = models.TextField(
        blank=True, default='',
        help_text="Required when root cause is 'Other'.")
    effect_category = models.CharField(max_length=20, choices=EffectCategory.choices)
    effect_detail = models.TextField(
        help_text='Stated in specific terms — BWP amount/range, regulatory, etc.')

    # Rating — auto-computed, never typed ----------------------------------
    likelihood = models.PositiveSmallIntegerField(default=1)  # 1..5
    impact = models.PositiveSmallIntegerField(default=1)      # 1..5
    rating = models.CharField(max_length=10, blank=True, default='')
    rating_justification = models.TextField()

    fraud_flag = models.BooleanField(default=False)

    # Regulatory overlay (cheap now; feeds the NBFIRA dashboard tile) -------
    regulatory_tag = models.BooleanField(default=False)
    nbfira_reference = models.CharField(
        max_length=120, blank=True, default='',
        help_text='NBFIRA return line item affected, if regulatory.')

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['rating']),
            models.Index(fields=['fraud_flag']),
        ]

    def __str__(self):
        return f'{self.reference or "FND"} — {self.title}'

    # -- enforcement --------------------------------------------------------
    def clean(self):
        errors = {}

        for field in ('criteria', 'condition', 'effect_detail', 'rating_justification'):
            if not (getattr(self, field) or '').strip():
                errors[field] = 'Mandatory — a finding cannot be saved without this element.'

        if not self.root_cause:
            errors['root_cause'] = 'Root cause is mandatory (forced taxonomy).'
        if self.root_cause == self.RootCause.OTHER and not self.root_cause_justification.strip():
            errors['root_cause_justification'] = "Justification is required when root cause is 'Other'."

        for field in ('likelihood', 'impact'):
            val = getattr(self, field)
            if val is None or not (1 <= int(val) <= 5):
                errors[field] = 'Must be between 1 and 5.'

        # Condition must cite evidence — it cannot stand alone (spec Module 5).
        if not errors.get('condition') and not self.evidence_reference.strip():
            errors['evidence_reference'] = 'The condition must reference supporting evidence.'

        # A finding cannot move to 'agreed' without an agreed management response.
        if self.status in (self.Status.AGREED, self.Status.ISSUED, self.Status.CLOSED):
            resp = getattr(self, 'response', None)
            if resp is None or not resp.agreed:
                errors['status'] = 'An agreed management response is required before this status.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Rating is always derived, never trusted from input.
        self.rating = compute_rating(self.likelihood, self.impact)
        # A fraud-category effect implies the fraud flag.
        if self.effect_category == self.EffectCategory.FRAUD:
            self.fraud_flag = True
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# ManagementResponse — 1:1 with a finding. Mandatory before 'agreed'.
# ---------------------------------------------------------------------------
class ManagementResponse(AuditableMixin, BaseModel):
    finding = models.OneToOneField(Finding, on_delete=models.CASCADE, related_name='response')
    response_text = models.TextField()
    action = models.TextField(help_text='The remediation action management commits to.')
    owner = models.CharField(max_length=120, help_text='Responsible manager.')
    target_date = models.DateField()
    agreed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Response to {self.finding_id}'


# ---------------------------------------------------------------------------
# FollowUp — remediation tracking. Overdue is auto-derived, not manually set.
# ---------------------------------------------------------------------------
class FollowUp(AuditableMixin, BaseModel):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not started'
        IN_PROGRESS = 'in_progress', 'In progress'
        IMPLEMENTED = 'implemented', 'Implemented'
        RISK_ACCEPTED = 'risk_accepted', 'Risk accepted'

    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name='followups')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    evidence_reference = models.TextField(
        blank=True, default='',
        help_text='Evidence of implementation — required to close as Implemented.')
    next_retest_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Follow-up {self.finding_id}: {self.status}'

    @property
    def is_overdue(self) -> bool:
        """Overdue = the management target date has passed and the action is not
        yet implemented (or risk-accepted). Computed — never a stored flag that
        someone forgot to update."""
        if self.status in (self.Status.IMPLEMENTED, self.Status.RISK_ACCEPTED):
            return False
        resp = getattr(self.finding, 'response', None)
        if resp is None or resp.target_date is None:
            return False
        return resp.target_date < timezone.localdate()

    def clean(self):
        if self.status == self.Status.IMPLEMENTED and not self.evidence_reference.strip():
            raise ValidationError(
                {'evidence_reference': 'Evidence is required before closing an action as Implemented.'})

    def save(self, *args, **kwargs):
        # Default the re-test date from the finding rating if not set.
        if self.next_retest_date is None and self.finding_id:
            base = timezone.localdate()
            self.next_retest_date = base + dt.timedelta(days=retest_interval_days(self.finding.rating))
        super().save(*args, **kwargs)
