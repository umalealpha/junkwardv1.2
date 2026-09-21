"""hris/feature_adoption_models.py — one-click "I am happy with this feature"
staff declarations.

CFO directive 2026-07-21: force HR-team adoption of the new HRIS features. Each
person must open every feature and press a single button to declare they are
happy with it; their dashboard tracks what is still outstanding as their tasks.
Deliberately minimal — one row per (person, feature). No workflow, no approval.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class FeatureAcceptance(BaseModel):
    """A single staff member's 'I am happy with this feature' declaration."""
    user        = models.ForeignKey(
                      User, on_delete=models.CASCADE,
                      related_name='hris_feature_acceptances')
    feature_key = models.CharField(max_length=50)
    note        = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        # BaseModel.Meta is abstract; child sets its own constraints.
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'feature_key'],
                name='uniq_feature_acceptance_user_key'),
        ]
        indexes = [models.Index(fields=['user', 'feature_key'])]

    def __str__(self):
        return f"{self.user_id} accepted {self.feature_key}"
