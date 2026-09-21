"""hris/leave_reversal_models.py — the employee worked through approved leave.

Ontlametse Mogomotsi, ref AD/HR/IA/2026/001 (bug 800fd702 item 3).
CFO 2026-08-10: *"when a person wants to reverse the leave, let it be manager's
problem — reversal of zero point five or one day or even five days."*

So it is deliberately simple: the employee says how many days they actually worked
and why, and their manager decides. Half a day, one day, five days — the number is
whatever they claim, up to what the leave holds. There is no eligibility window and
no date-picking, because judging whether the claim is true is the manager's job,
not the software's.

Two things the software does still hold:
  * ONLY THE EMPLOYEE STARTS IT. The claim is "I was working", and nobody can make
    that claim on somebody else's behalf.
  * THE ORIGINAL SURVIVES. Approving shortens the leave, so the reversal snapshots
    what the leave said beforehand and the FK is PROTECT. The audit trail is the
    pair, not the survivor.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models

from core.models import AuditableMixin, BaseModel


ZERO = Decimal('0.00')

#: A reason is mandatory and must say something a manager can act on.
MIN_REASON_CHARS = 10


def reversal_attachment_path(instance, filename: str) -> str:
    return f'leave-reversals/{instance.leave_request_id}/{filename}'


class LeaveReversal(AuditableMixin, BaseModel):
    """A claim that some of an approved leave was actually worked."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Awaiting your manager'
        APPROVED = 'approved', 'Approved — days credited back'
        DECLINED = 'declined', 'Declined'

    OPEN_STATUSES = (Status.PENDING,)

    leave_request = models.ForeignKey(
        'hris.LeaveRequest', on_delete=models.PROTECT, related_name='reversals',
        help_text='The approved leave being reversed. PROTECT: the original must '
                  'outlive the reversal.')
    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE, related_name='leave_reversals')

    days = models.DecimalField(
        max_digits=6, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.5'))],
        help_text='Days worked and claimed back. Half a day upwards.')
    reason = models.TextField(
        help_text='Mandatory. Why the employee worked days they had booked off.')
    attachment = models.FileField(
        upload_to=reversal_attachment_path, blank=True, null=True,
        help_text='Optional supporting document, e.g. a Time Doctor hours report.')

    # The leave as it stood when the claim was raised. Approving shortens the live
    # record, so without these the previous position is unrecoverable.
    original_days       = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)
    original_start_date = models.DateField(null=True, blank=True)
    original_end_date   = models.DateField(null=True, blank=True)
    original_status     = models.CharField(max_length=20, blank=True, default='')

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING, db_index=True)

    requested_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='leave_reversals_requested')
    approver = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='leave_reversals_decided')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(
        blank=True, default='',
        help_text="Mandatory when declining — the employee is shown the manager's "
                  'own words.')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Leave Reversal'
        verbose_name_plural = 'Leave Reversals'
        constraints = [
            models.CheckConstraint(
                check=models.Q(days__gte=Decimal('0.5')),
                name='leavereversal_at_least_half_a_day',
            ),
            # A decline must say why. The view enforces it; this stops a shell or a
            # data script writing a reasonless refusal the employee then receives.
            models.CheckConstraint(
                check=~models.Q(status='declined') | ~models.Q(decision_notes=''),
                name='leavereversal_declined_needs_a_reason',
            ),
        ]

    def __str__(self):
        return f'Reversal of {self.days}d on leave {self.leave_request_id} ({self.status})'
