"""
hris/pulse_models.py — weekly anonymous staff mood / "pulse" check.

CFO-directed HRIS feature: a quick weekly "how are you feeling this week?"
for every member of staff (1-5 mood + optional comment). One row per
(employee, week). Deliberately plain `BaseModel` — NOT `AuditableMixin` —
so a save never writes a profile+score pair into AuditLog; HR must only
ever be able to read this back as aggregates (see hris/pulse_views.py,
which additionally refuses to surface any group smaller than 3 people).
"""
from __future__ import annotations

import datetime

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import BaseModel


class PulseResponse(BaseModel):
    """One employee's mood check-in for one Monday-anchored week."""

    profile    = models.ForeignKey(
                     'hris.HRISProfile', on_delete=models.CASCADE,
                     related_name='pulse_responses',
                 )
    week_start = models.DateField(help_text='Monday of the week this check-in covers.')
    score      = models.PositiveSmallIntegerField(
                     validators=[MinValueValidator(1), MaxValueValidator(5)],
                     help_text='1 (struggling) – 5 (great).',
                 )
    comment    = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['-week_start']
        unique_together     = [('profile', 'week_start')]
        verbose_name        = 'Pulse Response'
        verbose_name_plural = 'Pulse Responses'

    def __str__(self):
        return f'{self.profile.employee.full_name} — {self.week_start} ({self.score}/5)'

    @staticmethod
    def current_week_start() -> datetime.date:
        """Monday of the current week."""
        today = timezone.localdate()
        return today - datetime.timedelta(days=today.weekday())
