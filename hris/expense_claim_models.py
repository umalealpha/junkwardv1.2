"""
hris/expense_claim_models.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #4.

ExpenseClaim sits in HRIS (the employee submits via the HRIS self-service
portal) but bridges either:
    - AP        → a draft vendor bill is created in billing/ at approval time.
    - PAYROLL   → a PayslipLine is added to the next OPEN PayrollPeriod
                   against component_code='REIMBURSEMENT'.

The bridge logic itself lives in hris/services_expense.py — this file
only defines the model. It's imported into hris/models.py so Django's
app registry picks it up.

Bible-check guard: hris/ + payroll/ touch only. The billing model is
*read* (and a draft row is created via its public API) — we do not
mutate billing/ source.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


ZERO = Decimal('0.00')


class ExpenseClaim(AuditableMixin, BaseModel):
    """One out-of-pocket expense claimed by an employee."""

    class Status(models.TextChoices):
        DRAFT         = 'draft',         'Draft'
        SUBMITTED     = 'submitted',     'With accountant'      # awaiting the chosen senior accountant
        PENDING_CFO   = 'pending_cfo',   'With CFO'             # accountant loaded + uploaded proof
        APPROVED      = 'approved',      'Approved'             # CFO approved (payment done in FNB)
        REJECTED      = 'rejected',      'Returned to requester'
        PAID          = 'paid',          'Paid'

    class ReimbursementMethod(models.TextChoices):
        AP      = 'ap',      'Accounts Payable (draft vendor bill)'
        PAYROLL = 'payroll', 'Payroll (next-period PayslipLine)'

    profile        = models.ForeignKey(
                         'core.UserProfile', on_delete=models.PROTECT,
                         related_name='expense_claims',
                     )
    expense_date   = models.DateField()
    category       = models.CharField(max_length=80, blank=True, default='')
    amount         = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    currency       = models.CharField(max_length=3, default='BWP')
    attachment     = models.FileField(
                         upload_to='expense_claims/', null=True, blank=True,
                     )
    description    = models.TextField(blank=True, default='')
    status         = models.CharField(
                         max_length=12, choices=Status.choices,
                         default=Status.DRAFT,
                     )
    reimbursement_method = models.CharField(
                         max_length=10, choices=ReimbursementMethod.choices,
                         default=ReimbursementMethod.AP,
                     )
    approved_by    = models.ForeignKey(
                         User, null=True, blank=True,
                         on_delete=models.SET_NULL,
                         related_name='expense_claims_approved',
                     )
    approved_at    = models.DateTimeField(null=True, blank=True)

    # ── Workflow (CFO 2026-07-13 refund process) ────────────────────────────
    # Requester picks the on-duty senior accountant (never themselves). That
    # accountant loads the payment into FNB manually + uploads proof, or rejects
    # back to the requester; then the CFO approves or rejects (→ requester).
    approver       = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='expense_claims_to_process',
                         help_text='The senior accountant chosen to process this refund.',
                     )
    submitted_at   = models.DateTimeField(null=True, blank=True)
    processed_by   = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='expense_claims_processed',
                         help_text='Senior accountant who loaded the payment + uploaded proof.',
                     )
    processed_at   = models.DateTimeField(null=True, blank=True)
    payment_proof  = models.FileField(
                         upload_to='expense_claims/proof/', null=True, blank=True,
                         help_text='FNB payment proof uploaded by the accountant.',
                     )
    # GL account the accountant links at processing so the refund is easy to post
    # after payment (CFO 2026-07-13). A LINK/tag only — no journal entry is posted
    # here; posting stays a deliberate finance step.
    gl_account     = models.ForeignKey(
                         'ledger.Account', null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='expense_claims',
                         help_text='Expense GL account to post this refund to.',
                     )
    reject_reason  = models.TextField(blank=True, default='')
    # ── Already-paid close-out (Bharath Balasubramanian 2026-08-17) ──────────
    # Status.PAID existed from the start but nothing ever set it: the only ways
    # to close a refund were approve (which requires the accountant to load the
    # payment and upload FNB proof) or reject. A refund paid OUTSIDE that flow
    # had nowhere to go, so people rejected it with the reason typed "PAID" —
    # which records "Returned to requester" against money that was actually
    # paid, and emails the staff member that their refund "needs changes".
    paid_by        = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='expense_claims_marked_paid',
                         help_text='Who confirmed this refund was paid.',
                     )
    paid_at        = models.DateTimeField(null=True, blank=True)
    paid_reference = models.CharField(
                         max_length=120, blank=True, default='',
                         help_text='Bank / payroll reference proving the payment, '
                                   'e.g. an FNB reference or a payroll period.',
                     )
    paid_note      = models.TextField(
                         blank=True, default='',
                         help_text='How it was paid, if it did not go through the '
                                   'normal accountant + CFO route.',
                     )

    class Meta(BaseModel.Meta):
        ordering            = ['-expense_date', '-created_at']
        verbose_name        = 'Expense Claim'
        verbose_name_plural = 'Expense Claims'

    def __str__(self):
        return f'{self.profile} · {self.expense_date} · {self.amount} {self.currency}'


class ExpenseClaimAttachment(BaseModel):
    """An invoice / receipt attached to a claim — a claim can have many (CFO
    2026-07-13: 'attach the relevant invoices'). The accountant's FNB proof lives
    on ExpenseClaim.payment_proof, not here."""
    claim       = models.ForeignKey(
                      ExpenseClaim, on_delete=models.CASCADE, related_name='invoices',
                  )
    file        = models.FileField(upload_to='expense_claims/invoices/')
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta(BaseModel.Meta):
        ordering = ['created_at']
        verbose_name = 'Expense Claim Invoice'
        verbose_name_plural = 'Expense Claim Invoices'
