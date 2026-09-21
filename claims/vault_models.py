"""
claims/vault_models.py

Claim Forms Vault — Omni's own copy of every blank claim form.

CFO directive 2026-12-Aug: "wire them also to omni (all the claim forms in a
separate vault in omni)". This is a staff-facing reference library: the blank,
official forms for every claim type in one place, downloadable, versioned.

It is DELIBERATELY separate from:
  - the HR document vault (hris.HRDocument) — that holds signed personal
    agreements (PII); this holds blank templates only, no PII;
  - the AI document-parsing uploads (documents.DocumentUpload) — that is an
    ingestion pipeline; this is a read-mostly library.

These are the same forms Graphite emails claimants (Graphite is the send path);
Omni holds them so Finance / Claims / anyone internal can find and download the
current version without going into Graphite.
"""

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class ClaimForm(AuditableMixin, BaseModel):
    """One blank claim form. Files live in media/claim_forms/.

    No customer PII — blank official templates only, so this is readable by any
    authenticated staff member. Upload/replace is admin-gated in the view.
    """

    class Category(models.TextChoices):
        MOTOR       = 'motor',        'Motor'
        PROPERTY    = 'property',     'Property & assets'
        LIABILITY   = 'liability',    'Liability'
        ENGINEERING = 'engineering',  'Engineering'
        MARINE      = 'marine',       'Marine'
        LIFE_HEALTH = 'life_health',  'Life & health'
        SPECIALTY   = 'specialty',    'Specialty'
        OTHER       = 'other',        'Other'

    # Stable slug — matches the Graphite repo filename (motor-accident, etc.),
    # so the two libraries can be reconciled and nothing drifts silently.
    slug        = models.CharField(max_length=80, unique=True, db_index=True)
    title       = models.CharField(max_length=200)
    category    = models.CharField(max_length=20, choices=Category.choices,
                                   default=Category.OTHER, db_index=True)
    file        = models.FileField(upload_to='claim_forms/')
    # The original filename it was supplied under, kept for provenance.
    source_name = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    active      = models.BooleanField(default=True, db_index=True)
    sort_order  = models.PositiveIntegerField(default=100)
    uploaded_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='claim_forms_uploaded')

    class Meta(BaseModel.Meta):
        ordering = ['sort_order', 'title']
        verbose_name = 'claim form'
        verbose_name_plural = 'claim forms'

    def __str__(self):
        return f'{self.get_category_display()} — {self.title}'
