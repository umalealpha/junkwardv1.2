"""Controlled override of a system-locked maternity leave date (bug f4464440,
requirement 3).

A maternity end date is locked (see hris.feature_views.apply_leave). It may only
change through this record: new medical evidence, a reason code, and DUAL
approval — the second approver must not be the original applicant, the original
approver, or the person who proposed the change. Every override is retained with
the old and new dates, the reason, the document, and both people (requirement 5).
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel
from hris.models import LeaveRequest


def _override_cert_path(instance, filename):
    return f'maternity_overrides/{instance.leave_request_id}/{filename}'


class MaternityDateOverride(BaseModel):
    class ReasonCode(models.TextChoices):
        COMPLICATIONS = 'complications_extension', 'Complications extension'
        STILLBIRTH    = 'stillbirth_miscarriage',  'Stillbirth or miscarriage'
        DATA_ENTRY    = 'data_entry_error',        'Data entry error'

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending second approval'
        APPROVED = 'approved', 'Approved and applied'
        REJECTED = 'rejected', 'Rejected'

    leave_request = models.ForeignKey(
        LeaveRequest, on_delete=models.CASCADE, related_name='date_overrides')
    old_end_date  = models.DateField()
    new_end_date  = models.DateField()
    reason_code   = models.CharField(max_length=32, choices=ReasonCode.choices)
    certificate   = models.FileField(upload_to=_override_cert_path)
    proposed_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='maternity_overrides_proposed')
    approved_by   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='maternity_overrides_approved')
    decided_at    = models.DateTimeField(null=True, blank=True)
    status        = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    notes         = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Maternity Date Override'
        verbose_name_plural = 'Maternity Date Overrides'
