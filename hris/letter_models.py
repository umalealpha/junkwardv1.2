"""
hris/letter_models.py — staff letter requests (employment-confirmation etc.).

Oprah Mogomotsi asked HR to keep the company letterhead in omni's document bank
so any letter can be written on the branded template, and the CFO asked that
employment-confirmation letters auto-fill from HRIS and be signed only by a
manager (2026-07-15).

A LetterRequest is the request→sign-off record:
  * A staff member asks omni for a letter about THEMSELVES (self-service).
  * omni fills the wording from the payroll.Employee master record.
  * The person's manager (or HR) signs it off — only then is it "issued" and
    a branded PDF (Alpha Direct letterhead) is produced. Staff cannot issue
    their own letter.

The issued facts are snapshotted onto the row at sign-off time so the PDF is a
stable, verifiable record even if the employee's title / status changes later
(the same discipline as the payslip). Rendering lives in hris/letter_pdf.py.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel
from payroll.models import Employee


# ---------------------------------------------------------------------------
# Letter-type registry — one entry per kind of letter omni can auto-fill.
# The human wording template for each lives in hris/letter_pdf.py (LETTER_BODIES)
# so the legal copy sits next to the renderer. Adding a new letter type = add a
# choice here + a body there + (optionally) a self-service form on the frontend.
# ---------------------------------------------------------------------------

class LetterType(models.TextChoices):
    EMPLOYMENT_CONFIRMATION = 'employment_confirmation', 'Employment confirmation'


# Reference prefix per type (e.g. ADIC/HR/EL/2026/0001). Kept short + stable.
LETTER_REF_CODE = {
    LetterType.EMPLOYMENT_CONFIRMATION: 'EL',
}


class LetterRequest(AuditableMixin, BaseModel):
    """A staff letter request and its manager sign-off."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Awaiting manager sign-off'
        ISSUED   = 'issued',   'Signed off — letter issued'
        DECLINED = 'declined', 'Declined'

    # Subject of the letter (normally the requester themselves).
    employee     = models.ForeignKey(Employee, on_delete=models.PROTECT,
                                     related_name='letter_requests')
    letter_type  = models.CharField(max_length=40, choices=LetterType.choices,
                                    default=LetterType.EMPLOYMENT_CONFIRMATION)
    addressee    = models.CharField(max_length=200, default='To Whom It May Concern')
    # Optional free-text reason the staff member gives (e.g. "bank loan",
    # "visa application"). Never required — shown to the manager for context.
    purpose      = models.CharField(max_length=200, blank=True, default='')
    # Optional extra details HR/the manager types at sign-off (e.g. salary band,
    # contract type, a specific fact the recipient asked for). The omni AI helper
    # turns this raw note into a clean sentence added to the letter; the raw note
    # is kept here for the audit trail. Never required.
    additional_details = models.CharField(max_length=1000, blank=True, default='')

    status       = models.CharField(max_length=10, choices=Status.choices,
                                    default=Status.PENDING, db_index=True)

    requested_by = models.ForeignKey(User, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name='letters_requested')

    # The manager (or HR) who signed it off — letterheads are signed by managers.
    signatory        = models.ForeignKey(Employee, null=True, blank=True,
                                         on_delete=models.SET_NULL,
                                         related_name='letters_signed')
    decided_by       = models.ForeignKey(User, null=True, blank=True,
                                         on_delete=models.SET_NULL,
                                         related_name='letters_decided')
    decided_at       = models.DateTimeField(null=True, blank=True)
    decline_reason   = models.CharField(max_length=300, blank=True, default='')

    # Human reference stamped on the PDF + QR verify link (set at sign-off).
    reference        = models.CharField(max_length=40, blank=True, default='', db_index=True)

    # Frozen facts as at sign-off — the PDF renders from this, not the live
    # record, so a re-download years later still shows what was certified.
    # Keys: full_name, job_title, department, hire_date, employee_number,
    #       employment_status, company_name, signatory_name, signatory_title,
    #       issued_date, addressee, purpose.
    issued_snapshot  = models.JSONField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Letter Request'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['employee', '-created_at']),
        ]

    def __str__(self) -> str:
        who = self.employee.full_name if self.employee_id else '—'
        return f'{self.get_letter_type_display()} · {who} ({self.status})'
