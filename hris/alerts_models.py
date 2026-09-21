"""
hris/alerts_models.py — HRISAlert register.

Per Unami's wishlist (Fw: Omni, 2026-06-02):
  * Contract 2 months before expiry
  * Leave balance exceeds the legal cap
  * Performance concerns / reviews due / quarterly reviews due

Alerts are append-only.  The `generate_hris_alerts` management command
re-runs nightly (or on demand) and is idempotent: an alert with the same
(kind, target_id, due_date) is updated, not duplicated.
"""
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class HRISAlert(models.Model):
    KIND_CONTRACT_EXPIRY    = 'contract_expiry'
    KIND_LEAVE_EXCESS       = 'leave_balance_excess'
    KIND_LEAVE_MANDATORY_TAKE = 'leave_mandatory_take'
    KIND_REVIEW_DUE         = 'review_due'
    KIND_QUARTERLY_REVIEW   = 'quarterly_review_due'
    KIND_PERFORMANCE_CONCERN= 'performance_concern'

    KIND_CHOICES = [
        (KIND_CONTRACT_EXPIRY,     'Contract 2 months before expiry'),
        (KIND_LEAVE_EXCESS,        'Leave balance exceeds legal cap'),
        (KIND_LEAVE_MANDATORY_TAKE,'Mandatory annual leave not taken (ELRA s.219)'),
        (KIND_REVIEW_DUE,          'Performance review due'),
        (KIND_QUARTERLY_REVIEW,    'Quarterly review due'),
        (KIND_PERFORMANCE_CONCERN, 'Performance concern flagged'),
    ]

    STATE_OPEN     = 'open'
    STATE_ACK      = 'acknowledged'
    STATE_DISMISSED= 'dismissed'
    STATE_RESOLVED = 'resolved'
    STATE_CHOICES  = [
        (STATE_OPEN,      'Open'),
        (STATE_ACK,       'Acknowledged'),
        (STATE_DISMISSED, 'Dismissed'),
        (STATE_RESOLVED,  'Resolved'),
    ]

    SEV_LOW    = 'low'
    SEV_MEDIUM = 'medium'
    SEV_HIGH   = 'high'
    SEV_CRIT   = 'critical'
    SEV_CHOICES = [
        (SEV_LOW, 'Low'), (SEV_MEDIUM, 'Medium'),
        (SEV_HIGH, 'High'), (SEV_CRIT, 'Critical'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=40, choices=KIND_CHOICES)
    severity = models.CharField(max_length=10, choices=SEV_CHOICES, default=SEV_MEDIUM)
    state = models.CharField(max_length=15, choices=STATE_CHOICES, default=STATE_OPEN)

    employee = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                 on_delete=models.CASCADE, related_name='hris_alerts')
    profile = models.ForeignKey('hris.HRISProfile', null=True, blank=True,
                                on_delete=models.CASCADE, related_name='alerts')
    target_kind = models.CharField(max_length=40, blank=True, default='',
                                   help_text='e.g. employment_contract / leave_request / review')
    target_id = models.CharField(max_length=40, blank=True, default='')

    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default='')
    due_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.CharField(max_length=80, blank=True, default='')
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['state', 'kind']),
            models.Index(fields=['employee', 'state']),
            models.Index(fields=['kind', 'target_id']),
        ]

    def __str__(self) -> str:
        return f'[{self.kind}] {self.title}'
