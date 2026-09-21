"""
hris/incentive_models.py — Staff Incentive Approval workflow (CFO 2026-07-13).

Replaces the email round (manager emails an incentive list → CFO + Unami
approve → Finance picks it up for payroll) with an omni record:

  1. A manager submits an IncentiveRequest for a period, with one line per
     person/category and an amount (mirrors the real requests, e.g.
     "Motor claims incentives — May 2026", "BW instant insurance incentive").
  2. TWO signatures are required — the CFO slot and the HR slot (Unami) —
     like EmployeeTransfer's two-approver shape. Either may reject.
  3. Once BOTH have signed, the request lands in the payroll queue where
     Finance (Pako / Kago) mark it processed into payroll.

Segregation of duties: a maker can never sign their own request.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class IncentiveRequest(AuditableMixin, BaseModel):
    """One incentive-approval request (a period's list of incentive lines)."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    title       = models.CharField(max_length=160)
    period      = models.CharField(
                      max_length=7,
                      help_text='Incentive month, format YYYY-MM.')
    department  = models.CharField(max_length=80, blank=True, default='')
    notes       = models.TextField(blank=True, default='')

    # Manager declaration (CFO 2026-08-18): the submitting manager must confirm
    # they have checked THIS employee's attendance and leave and would be
    # comfortable if all staff saw why they are paid. Puts the accountability on
    # the manager, not the CFO. Required to submit; recorded for the audit trail.
    manager_attested = models.BooleanField(default=False)

    maker       = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='incentive_requests')
    maker_email = models.CharField(max_length=254, blank=True, default='')
    company     = models.ForeignKey(
                      'core.Company', null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='incentive_requests')

    status      = models.CharField(
                      max_length=12, choices=Status.choices,
                      default=Status.PENDING, db_index=True)

    # Dual signatures — CFO slot + HR slot. Both required for approval.
    cfo_approver    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='+')
    cfo_approved_at = models.DateTimeField(null=True, blank=True)
    hr_approver     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='+')
    hr_approved_at  = models.DateTimeField(null=True, blank=True)

    rejected_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='+')
    rejected_at     = models.DateTimeField(null=True, blank=True)
    decision_notes  = models.TextField(blank=True, default='')

    # Finance (Pako / Kago) tick once loaded into payroll.
    payroll_processed    = models.BooleanField(default=False)
    payroll_processed_by = models.ForeignKey(
                               User, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='+')
    payroll_processed_at = models.DateTimeField(null=True, blank=True)

    # Amend trail (CFO 2026-07-22): an authorised person may correct a still-
    # pending request's amount/line details before it is processed in payroll.
    # The concrete old→new figures live in the immutable AuditLog; these two are
    # just for the UI badge ("amended 2026-07-22 by …"). See
    # incentive_service.amend_incentive.
    amended_at  = models.DateTimeField(null=True, blank=True)
    amended_by  = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='+')

    # Set when this request was auto-created from a RecurringIncentive template
    # for its period. Used to keep generation idempotent — one request per
    # (template, period). Null for ordinary manager-submitted requests.
    source_template = models.ForeignKey(
                          'RecurringIncentive', null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='generated_requests')

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Incentive Request'
        verbose_name_plural = 'Incentive Requests'

    def __str__(self):
        return f"{self.title} ({self.period}) — {self.status}"

    @property
    def total(self) -> Decimal:
        return sum((l.amount for l in self.lines.all()), Decimal('0.00'))

    @property
    def fully_signed(self) -> bool:
        # CFO directive 2026-07-23 (Unami / Head of Human Capital request):
        # an incentive now needs BOTH the CFO slot AND the HR slot (Unami) before
        # it is approved and eligible for payroll. This restores the in-app dual
        # control: HR asked for a hard sign-off gate because managers claim they
        # bring incentives but never show proof, so HR must sign each request too
        # — not only review the payment later at the bank. The maker≠approver and
        # two-different-humans rules still hold (segregation).
        # Supersedes the 2026-07-15 "single CFO sign-off" arrangement (which had
        # relied on the downstream FNB review as the second control).
        return bool(self.cfo_approved_at and self.hr_approved_at)


class IncentiveLine(AuditableMixin, BaseModel):
    """One person / category on an incentive request."""

    request  = models.ForeignKey(
                   IncentiveRequest, on_delete=models.CASCADE,
                   related_name='lines')
    employee = models.ForeignKey(
                   'payroll.Employee', null=True, blank=True,
                   on_delete=models.SET_NULL,
                   related_name='incentive_lines')
    name     = models.CharField(
                   max_length=160,
                   help_text='Person or category, e.g. "BW instant '
                             'insurance incentive" or an employee name.')
    basis    = models.CharField(max_length=200, blank=True, default='')
    amount   = models.DecimalField(max_digits=12, decimal_places=2)
    # Auto-feed to payroll (CFO 2026-08-28): when the request is approved, this
    # line's amount is pushed into a PENDING payroll amendment batch for its
    # month + entity. This link is the idempotency guard — a line already tied to
    # an amendment is never pushed twice. SET_NULL so deleting a draft batch
    # never deletes the incentive record.
    payroll_amendment = models.ForeignKey(
                   'payroll.PayrollAmendment', null=True, blank=True,
                   on_delete=models.SET_NULL,
                   related_name='incentive_lines')

    # Earned-incentive gate (CFO directive 2026-07-13): an incentive is for work
    # ABOVE day-to-day duties — not routine work. The manager must attest, per
    # person, and ALL must qualify or the request is rejected (see
    # incentive_service.submit_request). Stored for the audit trail and shown to
    # the CFO/HR before they sign.
    beyond_normal_duties = models.BooleanField(
        default=False,
        help_text='Work went beyond the employee’s normal day-to-day duties.')
    on_time            = models.BooleanField(
        default=False, help_text='Delivered on time.')
    error_free         = models.BooleanField(
        default=False, help_text='Delivered error-free.')
    needed_manager_fix = models.BooleanField(
        default=False,
        help_text='Manager had to spend significant time fixing it (disqualifies).')
    justification      = models.TextField(
        blank=True, default='',
        help_text='Why this exceeded normal duties (required, substantive).')

    class Meta(BaseModel.Meta):
        ordering = ['created_at']

    def __str__(self):
        return f"{self.name}: {self.amount}"


class RecurringIncentive(AuditableMixin, BaseModel):
    """A standing incentive template (CFO directive 2026-07-22).

    Bharath pays the same people the same incentive every month. Rather than
    re-key the list each period, he sets it ONCE here (one row per person /
    category), then clicks "Generate this month" — which materialises one
    ordinary IncentiveRequest per active template for the chosen period. The
    generated requests then go through the normal CFO sign-off + payroll route.

    Idempotency lives on IncentiveRequest.source_template: generation never
    double-creates for a (template, period) already generated.
    """

    name        = models.CharField(
                      max_length=160,
                      help_text='Person or category, mirrors IncentiveLine.name.')
    category    = models.CharField(
                      max_length=120, blank=True, default='',
                      help_text='Grouping label used in the generated request '
                                'title, e.g. "Motor claims incentive".')
    basis       = models.CharField(max_length=200, blank=True, default='')
    amount      = models.DecimalField(max_digits=12, decimal_places=2)

    # The "employee" half of "employee/name": stored as the payroll.Employee id
    # (no hard FK — keeps this table auth-only so the migration needs no cross-
    # app dependency). Resolved to a live Employee at generation time; the
    # generated IncentiveLine carries the real FK.
    employee_id = models.UUIDField(null=True, blank=True)
    department  = models.CharField(max_length=80, blank=True, default='')

    # Standing reason carried onto each generated incentive line. Optional here
    # (a recurring incentive is a pre-authorised arrangement); generation falls
    # back to a sensible standing justification when this is blank/short so the
    # generated line still satisfies the earned-incentive gate on any later amend.
    justification = models.TextField(blank=True, default='')
    note        = models.TextField(blank=True, default='')

    active      = models.BooleanField(default=True, db_index=True)

    created_by       = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL, related_name='+')
    created_by_email = models.CharField(max_length=254, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['name']
        verbose_name        = 'Recurring Incentive'
        verbose_name_plural = 'Recurring Incentives'

    def __str__(self):
        flag = '✓' if self.active else '✗'
        return f"{flag} {self.category or self.name}: {self.amount}"
