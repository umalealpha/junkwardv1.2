"""
hris/transfer_models.py — inter-entity employee transfer (feature c0d110b6).

Oprah Mogomotsi 2026-06-12: move an employee from one entity to another with
an effective date and a TWO-SIDED approval — a "Transfer Out" sign-off from
the source entity, then a "Transfer In" sign-off from the destination entity.
Only when BOTH are approved does payroll.Employee.company change. Modelled as
its own record (not an HRISAmendment) because the two sequential approvals and
the effective date don't fit the single-approver amendment shape.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Company
from payroll.models import Employee


class EmployeeTransfer(AuditableMixin, BaseModel):
    """Source → destination entity move, gated by Transfer-Out then Transfer-In."""

    class Mode(models.TextChoices):
        # Same employee record, only the entity changes. Leave, login, Time
        # Doctor link and payslip history all travel with the person.
        CARRY  = 'carry',  'Carry over (same record, leave travels with them)'
        # CFO 2026-09-05 (Naomi Pheko, redundancy at ADIC -> Unicoin): the
        # source record is TERMINATED the day before the effective date, a
        # leaver settlement is raised for CFO/HR/Finance to approve, and a NEW
        # employee record is created at the destination with leave from zero.
        REHIRE = 'rehire', 'Redundancy & re-hire (terminate at source, new record at destination)'

    class LeaveTreatment(models.TextChoices):
        PAYOUT = 'payout', 'Pay out the leave balance at the source entity'
        CARRY  = 'carry',  'Carry the leave balance to the new record'

    class Status(models.TextChoices):
        PENDING_OUT  = 'pending_out',  'Awaiting source (Transfer Out) approval'
        PENDING_IN   = 'pending_in',   'Out approved — awaiting destination (Transfer In)'
        SCHEDULED    = 'scheduled',    'Both approved — applies on the effective date'
        COMPLETED    = 'completed',    'Both approved — applied to employee'
        REJECTED     = 'rejected',     'Rejected'

    employee       = models.ForeignKey(Employee, on_delete=models.PROTECT,
                                       related_name='transfers')
    source_company = models.ForeignKey(Company, on_delete=models.PROTECT,
                                       related_name='transfers_out')
    dest_company   = models.ForeignKey(Company, on_delete=models.PROTECT,
                                       related_name='transfers_in')
    effective_date = models.DateField(help_text='Date the move takes effect.')
    reason         = models.TextField(blank=True, default='')

    mode            = models.CharField(max_length=8, choices=Mode.choices,
                                       default=Mode.CARRY, db_index=True)
    leave_treatment = models.CharField(max_length=8, choices=LeaveTreatment.choices,
                                       default=LeaveTreatment.PAYOUT,
                                       help_text='Rehire mode only: what happens to the '
                                                 'annual-leave balance at the source entity.')
    new_email       = models.EmailField(blank=True, default='',
                                        help_text='Rehire mode: the email address at the new '
                                                  'entity (blank = keep the current one).')
    new_employee    = models.ForeignKey(Employee, null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='rehired_from_transfers',
                                        help_text='Rehire mode: the record created at the destination.')
    settlement      = models.ForeignKey('hris.LeaveEncashment', null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='rehire_transfers',
                                        help_text='Rehire mode + payout: the leaver settlement raised.')

    status         = models.CharField(max_length=12, choices=Status.choices,
                                      default=Status.PENDING_OUT, db_index=True)

    submitter       = models.ForeignKey(User, null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='transfers_submitted')
    submitter_email = models.EmailField(blank=True)

    out_approver       = models.ForeignKey(User, null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='transfers_out_approved')
    out_approver_email = models.EmailField(blank=True)
    out_approved_at    = models.DateTimeField(null=True, blank=True)

    in_approver        = models.ForeignKey(User, null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='transfers_in_approved')
    in_approver_email  = models.EmailField(blank=True)
    in_approved_at     = models.DateTimeField(null=True, blank=True)

    decision_notes = models.TextField(blank=True, default='')
    applied_at     = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Employee Transfer'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['employee', '-created_at']),
        ]

    def __str__(self) -> str:
        return (f'{self.employee_id}: {self.source_company.code} → '
                f'{self.dest_company.code} ({self.status})')
