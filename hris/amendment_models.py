"""
hris/amendment_models.py — Maker-checker workflow for HRIS data edits.

CFO directive 2026-06-07: the HR team (Unami, Dorothy, Thapelo) get full
HRIS editing power ("HRIS" role), but EVERY amendment must be dual-approved
before it touches the live record. A maker submits a proposed change; it parks
here as PENDING; a second authorised person (the approver) approves, at which
point the change is applied to the target record. The maker can never approve
their own amendment (segregation of duties).

See hris/amendment_service.py for the submit / approve / reject logic and the
email + DeepSeek notifications.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import BaseModel


class HRISAmendment(BaseModel):
    """A proposed, not-yet-applied change to an HRIS record."""

    class Target(models.TextChoices):
        EMPLOYEE = 'employee', 'Employee record'
        PROFILE  = 'profile',  'HRIS profile'
        GRADE    = 'grade',    'Grade / salary band'

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending approval'
        APPROVED = 'approved', 'Approved & applied'
        REJECTED = 'rejected', 'Rejected'

    target_kind  = models.CharField(max_length=12, choices=Target.choices)
    target_id    = models.CharField(
                       max_length=64,
                       help_text='Primary key (UUID/str) of the target record.',
                   )
    target_label = models.CharField(
                       max_length=200, blank=True, default='',
                       help_text='Cached human label of the target (e.g. employee '
                                 'name) so notifications need no re-query.',
                   )

    # {field_name: {"old": <str>, "new": <str>, "label": <str>}}
    changes      = models.JSONField(default=dict)
    reason       = models.TextField(blank=True, default='')

    status       = models.CharField(
                       max_length=10, choices=Status.choices,
                       default=Status.PENDING, db_index=True,
                   )

    maker        = models.ForeignKey(
                       settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                       related_name='hris_amendments_made',
                   )
    maker_email  = models.EmailField(blank=True, default='')

    approver     = models.ForeignKey(
                       settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                       null=True, blank=True,
                       related_name='hris_amendments_decided',
                   )
    approver_email   = models.EmailField(blank=True, default='')

    # Compensating amendment (2026-08-25, Manus nine-area retest P2): an applied
    # amendment had no supported way back — the only options were a fresh
    # hand-typed amendment with no link to the original, or a direct DB edit.
    # A reversal is a NORMAL amendment carrying the old/new pair swapped, so it
    # goes through the same maker-checker path; this FK only records which
    # amendment it undoes. Deliberately NOT an undo-in-place: that would let one
    # person unwind a dual-approved change on their own.
    reversal_of  = models.ForeignKey(
                       'self', null=True, blank=True,
                       on_delete=models.SET_NULL,
                       related_name='reversals',
                       help_text='The applied amendment this one reverses.',
                   )
    decided_at       = models.DateTimeField(null=True, blank=True)
    decision_notes   = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='hris_amend_status_idx'),
            models.Index(fields=['target_kind', 'target_id'], name='hris_amend_target_idx'),
        ]

    def __str__(self) -> str:
        return f"{self.get_target_kind_display()} {self.target_label} ({self.status})"
