"""
procurement/kyc_models.py

Vendor KYC (Know Your Customer) — counterparty due diligence records.

A VendorKYC row is attached to a billing.Contact (1:1). It collects the
documentation Alpha Direct's compliance/finance teams need on file before
sending material payments to that counterparty:

  - Tax Identification Number (TIN) certificate
  - Certificate of Incorporation (COI)
  - Bank confirmation letter
  - Beneficial owners (UBOs) — JSON list, each {name, id_number, pct, role}
  - Sanctions screening status + last-checked timestamp
  - PEP (Politically Exposed Person) status
  - KYC validity expiry — re-screen cadence per AML policy

The Contact's `kyc_status` property — installed in procurement/models.py —
returns one of {missing, expired, flagged, ok} based on this row.

The payment-side guard lives in procurement/kyc_service.py and is called from
payments/models.py::Payment.confirm() as a one-line additive hook.

Conventions:
  - UUID PK from BaseModel
  - AuditableMixin tracks create/update in AuditLog
  - FileField uploads land under media/vendor-kyc/<contact_id>/<filename>
  - JSONB beneficial_owners default = []
"""

from __future__ import annotations

from django.db import models

from core.models import AuditableMixin, BaseModel


def _kyc_upload_to(instance, filename):
    """Group every KYC attachment for a contact under a single folder."""
    return f'vendor-kyc/{instance.contact_id}/{filename}'


class VendorKYC(AuditableMixin, BaseModel):
    """Counterparty due-diligence record attached 1:1 to a billing.Contact."""

    class SanctionsStatus(models.TextChoices):
        CLEAN     = 'clean',     'Clean — screened, no match'
        FLAGGED   = 'flagged',   'Flagged — name match requires review'
        UNCHECKED = 'unchecked', 'Unchecked — screening not yet run'

    class PEPStatus(models.TextChoices):
        NONE      = 'none',      'Not a PEP'
        PEP       = 'pep',       'Politically Exposed Person'
        ASSOCIATE = 'associate', 'Close associate / family of a PEP'
        UNCHECKED = 'unchecked', 'Not yet screened'

    contact = models.OneToOneField(
        'billing.Contact',
        on_delete=models.CASCADE,
        related_name='kyc',
        help_text='The counterparty this KYC record covers.',
    )

    # ---- Document attachments ------------------------------------------------
    tin_attachment = models.FileField(
        upload_to=_kyc_upload_to, null=True, blank=True,
        help_text='Tax Identification Number certificate (BURS).',
    )
    coi_attachment = models.FileField(
        upload_to=_kyc_upload_to, null=True, blank=True,
        help_text='Certificate of Incorporation.',
    )
    bank_letter_attachment = models.FileField(
        upload_to=_kyc_upload_to, null=True, blank=True,
        help_text='Bank confirmation letter — verifies the account details '
                  'we pay into are owned by this counterparty.',
    )

    # ---- Beneficial owners ---------------------------------------------------
    beneficial_owners = models.JSONField(
        default=list, blank=True,
        help_text='List of ultimate beneficial owners. Each entry should be '
                  'a dict: {"name": str, "id_number": str, "pct": float, '
                  '"role": str}. Empty list = none captured yet.',
    )

    # ---- Sanctions & PEP -----------------------------------------------------
    sanctions_checked_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the most recent sanctions screening was run.',
    )
    sanctions_status = models.CharField(
        max_length=10,
        choices=SanctionsStatus.choices,
        default=SanctionsStatus.UNCHECKED,
    )
    pep_status = models.CharField(
        max_length=10,
        choices=PEPStatus.choices,
        default=PEPStatus.UNCHECKED,
    )

    # ---- Validity / re-screening ---------------------------------------------
    kyc_expires_on = models.DateField(
        null=True, blank=True,
        help_text='Date this KYC record expires and must be re-validated. '
                  'NULL = no expiry set (treated as ok for the threshold guard).',
    )

    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-updated_at']
        verbose_name = 'Vendor KYC'
        verbose_name_plural = 'Vendor KYC records'
        indexes = [
            models.Index(fields=['sanctions_status']),
            models.Index(fields=['pep_status']),
            models.Index(fields=['kyc_expires_on']),
        ]

    def __str__(self):
        return f'KYC for {self.contact.name} ({self.sanctions_status}/{self.pep_status})'
