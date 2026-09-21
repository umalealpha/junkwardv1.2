"""
hris/exec_signoff_models.py — the CEO/CFO countersignature on an application
made by someone carrying long-overdue tasks (CFO 2026-08-07).

One record per gated application. It is deliberately generic (module + the
application's UUID) rather than three near-identical tables, because the rule
is one rule: *you have work more than 2 days late, so an executive signs before
this goes through*. See hris.overdue_gate for the "late" definition and
hris.exec_signoff_service for the create / block / resolve helpers.

The record also freezes WHICH tasks were late at the moment of applying
(`overdue_snapshot`), so the executive judges on the facts as they stood and
the audit trail survives the tasks later being completed.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class ExecSignoff(AuditableMixin, BaseModel):
    """A pending CEO/CFO countersignature blocking one application."""

    class Module(models.TextChoices):
        LEAVE     = 'leave',     'Leave request'
        LOAN      = 'loan',      'Staff loan'
        INCENTIVE = 'incentive', 'Incentive request'

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Awaiting CEO / CFO signature'
        APPROVED = 'approved', 'Signed — may proceed'
        DECLINED = 'declined', 'Declined by CEO / CFO'

    # Why the signature is being asked for. The original (and default) reason is
    # long-overdue work. LEAVE_POLICY was added 2026-09-10: discretionary leave
    # (compassionate / study / special) always needs the CFO, whether or not the
    # applicant has a single task outstanding.
    class Kind(models.TextChoices):
        OVERDUE      = 'overdue',      'Long-overdue work'
        LEAVE_POLICY = 'leave_policy', 'Discretionary leave'

    kind      = models.CharField(max_length=14, choices=Kind.choices,
                                 default=Kind.OVERDUE, db_index=True)
    # True = the CEO may NOT sign this one, only the CFO (CFO 2026-09-10:
    # "that gets approved by me as well" — he chose himself alone). Kept as a
    # field rather than inferred from `kind` so a future rule can differ.
    cfo_only  = models.BooleanField(default=False)

    module    = models.CharField(max_length=12, choices=Module.choices, db_index=True)
    # The gated application's primary key. Not a FK — one table serves three
    # unrelated models, and a dangling id is harmless (the row is audit anyway).
    object_id = models.UUIDField(db_index=True)

    applicant      = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='exec_signoffs_raised')
    applicant_name = models.CharField(max_length=160, blank=True, default='')

    # Why the countersignature was demanded, frozen at apply time.
    reason           = models.CharField(max_length=200, blank=True, default='')
    overdue_snapshot = models.JSONField(default=dict, blank=True)

    status         = models.CharField(
        max_length=10, choices=Status.choices,
        default=Status.PENDING, db_index=True)
    decided_by     = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='exec_signoffs_decided')
    decided_at     = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['module', 'object_id', 'status'])]
        verbose_name = 'Executive countersignature'
        verbose_name_plural = 'Executive countersignatures'

    def __str__(self):
        return f'{self.get_module_display()} · {self.applicant_name} · {self.status}'

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def overdue_count(self) -> int:
        return int((self.overdue_snapshot or {}).get('count') or 0)
