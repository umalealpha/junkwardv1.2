"""Maker-checker record for one-step employee onboarding."""
from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.models import BaseModel


class OnboardingRequest(BaseModel):
    """A proposed employee onboarding that is applied only after approval."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending approval'
        APPROVED = 'approved', 'Approved and applied'
        REJECTED = 'rejected', 'Rejected'

    full_name = models.CharField(max_length=200)
    email = models.EmailField(db_index=True)
    employee_number = models.CharField(max_length=30, blank=True, default='')
    department = models.CharField(max_length=100, blank=True, default='')
    job_title = models.CharField(max_length=100, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    hire_date = models.DateField()
    # Annual leave days/year captured at onboarding (bug c48f10b0, Oprah 2026-09-03).
    # On approval this seeds the employee's 'annual' LeaveOpeningBalance so they
    # accrue from day one instead of showing zero until a manual CSV upload; blank
    # falls back to the CoS annual default (21).
    annual_leave_entitlement = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True)
    company = models.ForeignKey(
        'core.Company', on_delete=models.PROTECT, related_name='onboarding_requests')
    manager = models.ForeignKey(
        'payroll.Employee', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='onboarding_requests_as_manager')
    default_leave_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='onboarding_requests_as_leave_approver')

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    risk_warnings = models.JSONField(default=list, blank=True)
    payload_hash = models.CharField(max_length=64, db_index=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    maker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='onboarding_requests_made')
    maker_email = models.EmailField(blank=True, default='')
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='onboarding_requests_decided')
    approver_email = models.EmailField(blank=True, default='')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default='')
    employee = models.ForeignKey(
        'payroll.Employee', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_onboarding_requests')

    class Meta(BaseModel.Meta):
        ordering: ClassVar[list[str]] = ['-created_at']
        indexes: ClassVar[list] = [
            models.Index(fields=['status', '-created_at'], name='hris_onbrd_status_created_idx'),
            models.Index(fields=['email', 'status'], name='hris_onbrd_email_status_idx'),
        ]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                Lower('email'),
                condition=Q(status='pending'),
                name='uniq_pending_onboarding_email_ci',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.full_name} <{self.email}> ({self.status})'
