"""staff_rewards/models.py — Alpha Staff Rewards (employee programme).

A STAFF rewards module, deliberately SEPARATE from the customer-facing
``rewards`` app (RewardMember / PointsTransaction). Staff and customer
balances and tier ladders NEVER mix:

  * Identity   → payroll.Employee (the staff register), not RewardMember.
  * Ledger     → StaffPointsTransaction, not rewards.PointsTransaction.
  * Tiers      → STAFF_TIERS (Bronze→Diamond), not rewards' Bronze→Platinum.

Three pillars (Alpha Rewards concept): Innovation, Business Impact,
Health & Wellness. Every submission maps to exactly one pillar; the
pillar's running total feeds both the per-pillar tier and the overall
points ledger.

DPA (Botswana Data Protection Act No. 18 of 2024): payloads carry a
Member ID and references ONLY — never a customer name, never a staff
name (resolved via the Employee FK), never raw meal images, never
location/sensor data. See staff_rewards/service.py for the enforcement.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel
from payroll.models import Employee

from .tiers import progress_to_next, tier_for


# ---------------------------------------------------------------------------
# Pillars + feature codes (shared by models, rules and service)
# ---------------------------------------------------------------------------

class Pillar(models.TextChoices):
    INNOVATION       = 'innovation',       'Innovation'
    BUSINESS_IMPACT  = 'business_impact',  'Business Impact'
    HEALTH_WELLNESS  = 'health_wellness',  'Health & Wellness'


class FeatureCode(models.TextChoices):
    PROFDEV    = 'profdev',    'Professional Development & Upskilling'
    BIZDEV     = 'bizdev',     'Business Development & Partnerships'
    FITNESS    = 'fitness',    'Running Clubs & Fitness Activities'
    COMPLIMENT = 'compliment', 'Customer Compliments & Positive Feedback'
    MEAL       = 'meal',       'Healthy Eating & Nutrition'
    STEPS      = 'steps',      'Daily Step Goals'


# Which pillar each feature feeds. Single source of truth for service.py.
FEATURE_PILLAR = {
    FeatureCode.PROFDEV:    Pillar.INNOVATION,
    FeatureCode.BIZDEV:     Pillar.BUSINESS_IMPACT,
    FeatureCode.FITNESS:    Pillar.HEALTH_WELLNESS,
    FeatureCode.COMPLIMENT: Pillar.BUSINESS_IMPACT,
    FeatureCode.MEAL:       Pillar.HEALTH_WELLNESS,
    FeatureCode.STEPS:      Pillar.HEALTH_WELLNESS,
}

# Account field that holds each pillar's running total.
PILLAR_FIELD = {
    Pillar.INNOVATION:      'innovation_points',
    Pillar.BUSINESS_IMPACT: 'business_impact_points',
    Pillar.HEALTH_WELLNESS: 'health_wellness_points',
}


# ---------------------------------------------------------------------------
# Account — one per employee, holds the staff balance + pillar totals
# ---------------------------------------------------------------------------

class StaffPointsAccount(BaseModel):
    """An employee's staff-rewards balance. One per Employee (OneToOne).

    Separate from rewards.RewardMember by design — this is the STAFF
    programme ledger and never touches customer balances.
    """

    employee               = models.OneToOneField(
                                 Employee, on_delete=models.CASCADE,
                                 related_name='staff_points_account',
                             )
    points_balance         = models.IntegerField(default=0,
                                 help_text='Overall staff points (sum of all pillars).')
    innovation_points      = models.IntegerField(default=0)
    business_impact_points = models.IntegerField(default=0)
    health_wellness_points = models.IntegerField(default=0)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Staff Points Account'
        verbose_name_plural = 'Staff Points Accounts'
        ordering            = ['-points_balance']

    def __str__(self):
        return f'{self.employee.full_name} — {self.points_balance} pts ({self.tier})'

    @property
    def tier(self) -> str:
        return tier_for(self.points_balance)

    def pillar_total(self, pillar: str) -> int:
        return int(getattr(self, PILLAR_FIELD[pillar], 0) or 0)

    def progress(self) -> dict:
        return progress_to_next(self.points_balance)


# ---------------------------------------------------------------------------
# Submission — a maker entry awaiting approver decision
# ---------------------------------------------------------------------------

class StaffSubmission(BaseModel):
    """One staff reward submission: a maker logs an activity; an approver
    decides; on approval points post to the employee's account + pillar.

    DPA: ``payload`` stores references and numeric inputs ONLY — Member ID,
    case reference, course reference, organisation name. No customer or
    staff personal name, no raw image, no location data.
    """

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    employee       = models.ForeignKey(
                         Employee, on_delete=models.CASCADE,
                         related_name='staff_submissions',
                     )
    pillar         = models.CharField(max_length=20, choices=Pillar.choices)
    feature_code   = models.CharField(max_length=20, choices=FeatureCode.choices)
    payload        = models.JSONField(
                         default=dict, blank=True,
                         help_text='Activity inputs/references ONLY — Member ID, '
                                   'case ref, course ref. No personal names, no '
                                   'raw images, no location data (DPA).',
                     )
    status         = models.CharField(
                         max_length=10, choices=Status.choices,
                         default=Status.PENDING, db_index=True,
                     )
    points_awarded = models.IntegerField(default=0,
                         help_text='Computed on approval from points_rules.')
    maker_email    = models.EmailField(blank=True, default='',
                         help_text='Who submitted (snapshot at submit time).')
    approver       = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='staff_submissions_decided',
                     )
    approver_email = models.EmailField(blank=True, default='')
    decided_at     = models.DateTimeField(null=True, blank=True)
    reason         = models.TextField(blank=True, default='',
                         help_text='Decision note (mandatory context on reject).')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Staff Submission'
        verbose_name_plural = 'Staff Submissions'
        ordering            = ['-created_at']
        indexes             = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['employee', 'feature_code']),
        ]

    def __str__(self):
        return f'{self.employee.full_name} · {self.feature_code} [{self.status}]'


# ---------------------------------------------------------------------------
# Transaction — append-only points ledger row
# ---------------------------------------------------------------------------

class StaffPointsTransaction(BaseModel):
    """Append-only staff points ledger row. Separate from the customer
    rewards.PointsTransaction ledger — never mixed."""

    class Kind(models.TextChoices):
        EARN   = 'earn',   'Earn'
        ADJUST = 'adjust', 'Adjustment'

    account     = models.ForeignKey(
                      StaffPointsAccount, on_delete=models.CASCADE,
                      related_name='transactions',
                  )
    submission  = models.ForeignKey(
                      StaffSubmission, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='transactions',
                  )
    points      = models.IntegerField(help_text='+ on earn / adjust.')
    kind        = models.CharField(max_length=10, choices=Kind.choices,
                                   default=Kind.EARN)
    # Pillar this row feeds. Normally derived from submission.feature_code, but
    # submission-less rows (e.g. task-performance adjustments, CFO 2026-07-13)
    # carry it directly so the dashboard's per-pillar "recent" list can show
    # them. Blank on legacy rows — those still attribute via their submission.
    pillar      = models.CharField(max_length=20, blank=True, default='')
    detail      = models.CharField(max_length=300, blank=True, default='')
    occurred_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        verbose_name        = 'Staff Points Transaction'
        verbose_name_plural = 'Staff Points Transactions'
        ordering            = ['-occurred_at']

    def __str__(self):
        return f'{self.account.employee.full_name} {self.kind} {self.points}'
