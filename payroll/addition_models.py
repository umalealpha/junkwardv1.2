"""
payroll/addition_models.py — "Add Employee to Payroll" with Finance sign-off.

Feature request (Pako Kago, Financial Controller, 2026-08-12): the Payroll
Dashboard needs an "Add Employee" button that stages a new employee + their pay
components against a period, held in PENDING approval until a Finance signer
approves. Nothing touches headcount, the period totals, the register or All
Payslips until approval — maker-checker with segregation of duties (the
requester may not approve their own submission).

Kept as a SEPARATE per-addition record on purpose: PayrollSignOff is a
period x company AGGREGATE and cannot represent a single pending person. On
approval the service (addition_service.py) creates the real payroll.Employee —
reusing an existing match where one is found, never minting a duplicate
blank-shell row (the July-2026 duplicate-employee / invisible-payslip bug) —
and a DRAFT Payslip, then reuses Payslip.recompute_totals() + the BURS
brackets for PAYE so tax is never hard-coded here.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Company

ZERO = Decimal('0.00')


class PayrollAdditionRequest(AuditableMixin, BaseModel):
    """A single new employee staged for a payroll period, pending Finance sign-off."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending Finance sign-off'
        APPROVED = 'approved', 'Approved — live on the payroll'
        REJECTED = 'rejected', 'Rejected — returned to requester'

    period  = models.ForeignKey('payroll.PayrollPeriod', on_delete=models.PROTECT,
                                related_name='addition_requests')
    company = models.ForeignKey(Company, on_delete=models.PROTECT,
                                related_name='payroll_addition_requests')
    status  = models.CharField(max_length=10, choices=Status.choices,
                               default=Status.PENDING)

    # ── Entered details (the modal form) ────────────────────────────────────
    full_name       = models.CharField(max_length=200)
    employee_number = models.CharField(max_length=60, blank=True, default='')
    department      = models.CharField(max_length=150, blank=True, default='')

    basic      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    commission = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    incentive  = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # [{"code": "HOUSING_ALLOWANCE", "amount": "1000.00"}, ...] — each maps to an
    # active EARNING PayslipComponent on approval.
    allowances = models.JSONField(default=list, blank=True)

    # ── Maker-checker trail (Pako: log requester/approver/timestamps/values) ──
    requested_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='payroll_additions_requested')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='payroll_additions_decided')
    decided_at   = models.DateTimeField(null=True, blank=True)
    rejection_comment = models.TextField(blank=True, default='')

    # ── Set on approval ─────────────────────────────────────────────────────
    created_employee = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name='+')
    created_payslip  = models.ForeignKey('payroll.Payslip', null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name='+')
    linked_existing  = models.BooleanField(
        default=False,
        help_text='True if approval linked to an existing employee instead of creating one.')

    class Meta(BaseModel.Meta):
        ordering = ['-requested_at']
        verbose_name        = 'Payroll Addition Request'
        verbose_name_plural = 'Payroll Addition Requests'

    def __str__(self):
        return (f'{self.full_name} → {self.company.name} '
                f'{self.period.period_name} ({self.get_status_display()})')

    @property
    def allowance_total(self) -> Decimal:
        t = ZERO
        for a in (self.allowances or []):
            try:
                t += Decimal(str(a.get('amount') or 0))
            except Exception:  # noqa: BLE001
                pass
        return t

    @property
    def gross_estimate(self) -> Decimal:
        """Indicative gross for the pending card. Real gross/PAYE/net are
        computed from the payslip lines on approval, not from this."""
        return ((self.basic or ZERO) + (self.commission or ZERO)
                + (self.incentive or ZERO) + self.allowance_total)
