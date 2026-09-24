"""healthcare/models.py — Vendor (service provider) onboarding records.

Captures the AFA / Alpha Direct Healthcare vendor intake + the signed AFA
Service Provider Network Agreement. Intake/document module only — does NOT post
to any GL / PO / payroll chain (steering: no financial-chain writes here).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel


class VendorOnboarding(models.Model):
    STATUS_CHOICES = [
        ("pending_review", "Pending review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    COUNCIL_CHOICES = [
        ("BHPC", "Botswana Health Professions Council"),
        ("PharmacyCouncil", "Botswana Pharmacy Council"),
        ("Other", "Other"),
    ]

    reference_number = models.CharField(max_length=32, unique=True, db_index=True)

    # --- Entity (CIPA) ---
    company_name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=64)
    registration_date = models.CharField(max_length=64, blank=True)
    registered_address = models.TextField(blank=True)
    tin = models.CharField(max_length=32, blank=True)
    vat_number = models.CharField(max_length=32, blank=True)

    # --- Directors / owners (list of {full_name, id_number, role}) ---
    directors = models.JSONField(default=list, blank=True)

    # --- Practitioner / credentials ---
    practitioner_name = models.CharField(max_length=255, blank=True)
    council_type = models.CharField(max_length=32, choices=COUNCIL_CHOICES, default="BHPC")
    council_registration_number = models.CharField(max_length=64, blank=True)
    discipline = models.CharField(max_length=128, blank=True)
    practice_address = models.TextField(blank=True)

    # --- Banking (manual only; masked in UI/PDF) ---
    bank_name = models.CharField(max_length=128, blank=True)
    branch_name = models.CharField(max_length=128, blank=True)
    branch_code = models.CharField(max_length=32, blank=True)
    account_holder = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=32, blank=True)

    # --- Services ---
    service_category = models.CharField(max_length=128, blank=True)
    services_offered = models.TextField(blank=True)

    # --- AFA Service Provider Network Agreement ---
    trading_name = models.CharField(max_length=255, blank=True)
    representative_name = models.CharField(max_length=255, blank=True)
    representative_capacity = models.CharField(max_length=128, blank=True)
    principal_place_of_business = models.TextField(blank=True)
    effective_date = models.CharField(max_length=64, blank=True)
    contact_tel = models.CharField(max_length=64, blank=True)
    contact_email = models.EmailField(blank=True)
    agreement_accepted = models.BooleanField(default=False)
    agreement_accepted_at = models.DateTimeField(null=True, blank=True)

    # --- Consent (Botswana DPA) ---
    consent_version = models.CharField(max_length=64, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)

    # --- Declaration / signature ---
    signatory_full_name = models.CharField(max_length=255, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_data_url = models.TextField(blank=True)  # base64 PNG

    # --- Staff-assisted intake + remote e-sign flow (CFO 2026-06-05) ---
    # Staff (Alana/Medu) prepare the record; the doctor signs remotely via a
    # tokenised link. `signing_status` tracks that flow independently of the
    # review `status` below.
    SIGNING_CHOICES = [
        ("draft", "Draft (staff filling)"),
        ("ready", "Ready for signature"),
        ("sent", "Signature link sent"),
        ("viewed", "Link opened by signer"),
        ("signed", "Signed"),
        ("completed", "Completed (agreement emailed)"),
        ("expired", "Link expired"),
    ]
    signing_status = models.CharField(max_length=20, choices=SIGNING_CHOICES, default="draft")
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_onboardings_prepared",
    )
    prepared_at = models.DateTimeField(null=True, blank=True)

    # --- Workflow / audit ---
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending_review")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_onboardings",
    )
    deepseek_used = models.BooleanField(default=False)
    agreement_email_sent = models.BooleanField(default=False)
    agreement_emailed_to = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Vendor onboarding"
        verbose_name_plural = "Vendor onboardings"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.reference_number} — {self.company_name} ({self.status})"

    @property
    def masked_account(self) -> str:
        a = self.account_number or ""
        return ("•" * max(0, len(a) - 4) + a[-4:]) if a else ""


class VendorSignatureRequest(models.Model):
    """A single-use, expiring, tokenised remote e-sign request for a prepared
    VendorOnboarding record. The raw token NEVER touches the DB — only its
    SHA-256 hash is stored; the raw token lives only in the emailed link.
    """
    onboarding = models.ForeignKey(
        VendorOnboarding, on_delete=models.CASCADE, related_name="signature_requests",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    sent_to_email = models.EmailField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_sig_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)   # single-use guard
    # e-sign audit trail (evidentiary weight under Botswana electronic-records law)
    signer_ip = models.CharField(max_length=45, blank=True)
    signer_user_agent = models.TextField(blank=True)
    consent_accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Vendor signature request"

    def __str__(self) -> str:
        return f"SigReq {self.onboarding.reference_number} ({'used' if self.used_at else 'open'})"

    def is_valid(self, now) -> tuple[bool, str]:
        if self.revoked_at:
            return False, "This signing link has been revoked."
        if self.used_at:
            return False, "This signing link has already been used."
        if now >= self.expires_at:
            return False, "This signing link has expired."
        return True, ""


class VendorOnboardingInvite(models.Model):
    """Single-use, expiring, tokenised invite for a service provider to fill in
    their OWN onboarding form with NO login (CFO 2026-07-28).

    Distinct from VendorSignatureRequest: that flow has staff prepare a record
    and the doctor only *signs* it; here the provider captures EVERYTHING
    themselves via a private link the onboarding team generates and sends.
    The raw token never touches the DB — only its SHA-256 hash is stored; the
    raw token lives only in the emailed/shared link.
    """
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    invited_email = models.EmailField(blank=True)
    invited_name = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vendor_onboarding_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)   # single-use guard
    onboarding = models.ForeignKey(
        VendorOnboarding, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="invites",
    )
    # audit trail (who submitted, from where)
    submitter_ip = models.CharField(max_length=45, blank=True)
    submitter_user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Vendor onboarding invite"

    def __str__(self) -> str:
        who = self.invited_email or self.invited_name or "(no email)"
        return f"Invite {who} ({'used' if self.used_at else 'open'})"

    def is_valid(self, now) -> tuple[bool, str]:
        if self.revoked_at:
            return False, "This onboarding link has been revoked."
        if self.used_at:
            return False, "This onboarding link has already been used."
        if now >= self.expires_at:
            return False, "This onboarding link has expired."
        return True, ""


# ---------------------------------------------------------------------------
# Bordereaux / claim / treaty uploads — CFO + Tlamelo Chimidza directive
# 2026-06-05: Healthcare needs Smart-Upload trackers under three tabs:
#   REVENUE  (GWP Master Bordereaux, monthly xlsx)
#   CLAIMS   (ADI_AFT_PmtRun, weekly xlsx)
#   TREATY   (Bordereaux RECEIVED FROM BROKER / SENT TO BROKER, monthly xlsx)
# All three use the same shape — file-blob in, parsed-rows + headline
# totals out. Direction flag is treaty-only.
# ---------------------------------------------------------------------------

import uuid


class HealthcareUpload(models.Model):
    """One uploaded xlsx bordereaux / remit / treaty file.

    Stores: who uploaded, what kind, the raw parsed rows + computed
    headline totals. Lives off-GL — never posts a JE. Pure operational
    register so HR + Healthcare team can keep the underlying data inside
    omni instead of e-mail attachments.
    """

    class Kind(models.TextChoices):
        REVENUE = 'revenue', 'Premium bordereaux (Revenue)'
        CLAIMS  = 'claims',  'AFT claims remit (Claims)'
        TREATY  = 'treaty',  'Treaty bordereaux'

    class Direction(models.TextChoices):
        NA       = 'na',       'n/a'
        INBOUND  = 'inbound',  'Received from broker'
        OUTBOUND = 'outbound', 'Sent to broker'

    class Status(models.TextChoices):
        PARSED = 'parsed', 'Parsed'
        FAILED = 'failed', 'Parse failed'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind        = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    direction   = models.CharField(max_length=10, choices=Direction.choices, default=Direction.NA)
    file_name   = models.CharField(max_length=300)
    file_size   = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='healthcare_uploads',
                  )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Header / cover detection ─ parsed at upload time
    period_label  = models.CharField(max_length=64, blank=True, default='',
                                     help_text='"May 2026", "2026-W19", etc — derived from the xlsx cover sheet.')
    period_year   = models.PositiveSmallIntegerField(null=True, blank=True)
    period_month  = models.PositiveSmallIntegerField(null=True, blank=True)

    # Headline totals (Decimal stored as float for portability;
    # use of float is safe because these are display roll-ups only —
    # the per-row decimal precision is preserved inside raw_payload).
    total_rows         = models.PositiveIntegerField(default=0)
    total_lives_count  = models.PositiveIntegerField(default=0)
    gross_amount       = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    paid_amount        = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # AFT weekly claims importer (CFO/Tlamelo 2026-06-13).
    #  source_hash — sha256 of the uploaded bytes → idempotent re-import (the
    #                same file twice never double-counts).
    #  remit_date  — the run's Remit Date (claims dedupe key: one run per date;
    #                a corrected re-send for the same date supersedes the old).
    #  ai_summary  — DeepSeek plain-English commentary built from NON-PII
    #                aggregates only (member/account/diagnosis NEVER sent out).
    source_hash  = models.CharField(max_length=64, blank=True, default='', db_index=True)
    remit_date   = models.DateField(null=True, blank=True, db_index=True)
    superseded   = models.BooleanField(default=False, db_index=True)
    ai_summary   = models.TextField(blank=True, default='')

    # Raw payload — list of dicts per parsed row, plus the canonical
    # header row that was matched. Capped at 50 MB in the view so a
    # rogue 10-million-row sheet can't fill the DB.
    raw_payload  = models.JSONField(default=dict, blank=True)

    status      = models.CharField(max_length=10, choices=Status.choices, default=Status.PARSED)
    error_log   = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Healthcare upload'
        verbose_name_plural = 'Healthcare uploads'
        indexes = [models.Index(fields=['kind', '-uploaded_at'])]

    def __str__(self) -> str:
        d = f' / {self.direction}' if self.direction != self.Direction.NA else ''
        return f"{self.kind}{d} {self.period_label or '(?)'} — {self.file_name}"


# ─────────────────────────────────────────────────────────────────────────────
# Group Health Quotations (CFO/Tlamelo 2026-06-17). Persisted quotes so they
# stop disappearing: history, edit, download, and invoice-from-approved-quote.
# Off-GL operational register — no JE writes (steering).
# ─────────────────────────────────────────────────────────────────────────────

class HealthQuote(models.Model):
    """A saved group-health quotation for an employer group.

    Members live on HealthQuoteMember. Totals are cached at save time from the
    OFFICE rate card (healthcare.health_rates). A quote can carry members across
    several plan tiers — the quote/invoice render one tab per tier.
    """

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        SUBMITTED = 'submitted', 'In review'
        APPROVED  = 'approved',  'Approved'
        INVOICED  = 'invoiced',  'Invoiced'
        REJECTED  = 'rejected',  'Rejected'

    class ReviewStage(models.TextChoices):
        NONE    = '',        'Not in review'
        REVIEW1 = 'review1', 'Reviewer 1 (Ritah)'
        REVIEW2 = 'review2', 'Reviewer 2 (Meduduetso)'
        FINAL   = 'final',   'Final approval (CFO)'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref           = models.CharField(max_length=40, unique=True, db_index=True)
    # Employer-group details — captured at quote stage, carried into the invoice.
    client_name   = models.CharField(max_length=200)
    client_address = models.TextField(blank=True, default='')
    contact_name  = models.CharField(max_length=150, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=40, blank=True, default='')
    vat_no        = models.CharField(max_length=40, blank=True, default='')

    benefit_start = models.DateField(null=True, blank=True)
    billing_period = models.CharField(max_length=30, blank=True, default='')
    underwriting  = models.CharField(max_length=120, blank=True, default='Standard + Exclusions')

    status        = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    # Sequential review chain (CFO/Tlamelo 2026-06-17): creator → Ritah →
    # Meduduetso → CFO (final, delegable). review_stage = whose turn it is.
    review_stage  = models.CharField(max_length=10, choices=ReviewStage.choices, blank=True, default='')
    submitted_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='health_quotes_submitted')
    submitted_at  = models.DateTimeField(null=True, blank=True)
    review1_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='health_quotes_review1')
    review1_at    = models.DateTimeField(null=True, blank=True)
    review2_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='health_quotes_review2')
    review2_at    = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=300, blank=True, default='')
    # Per-tier discount % applied to this quote (Tlamelo 2026-06-25). Maps a tier
    # key -> percent string, e.g. {"AD_LITE": "25", "AD_ESSENTIAL": "15"}. Empty
    # = use healthcare.health_rates.TIER_DISCOUNT_DEFAULTS for each tier.
    tier_discounts = models.JSONField(default=dict, blank=True)
    # gross_excl = rack premium before discount; discount_excl = total discount;
    # subtotal_excl / vat / total_incl are NET (what the client is quoted).
    gross_excl    = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_excl = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    subtotal_excl = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat           = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_incl    = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes         = models.TextField(blank=True, default='')

    created_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='health_quotes_created')
    approved_by   = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='health_quotes_approved')
    approved_at   = models.DateTimeField(null=True, blank=True)

    # Emailed to the client from the app (Omni Mobile health quote).
    emailed_to    = models.EmailField(blank=True, default='')
    emailed_at    = models.DateTimeField(null=True, blank=True)

    invoice_no    = models.CharField(max_length=40, blank=True, default='')
    invoice_date  = models.DateField(null=True, blank=True)

    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Health quote'
        verbose_name_plural = 'Health quotes'
        constraints = [
            # Two invoices must never share a number. The allocator takes an
            # advisory lock (see quote_views._next_invoice_no), but the database
            # is the only place that can actually guarantee it — CFO 2026-07-30,
            # bug 03a2b875 follow-up. Blank invoice_no is the un-invoiced state,
            # so the constraint is conditional.
            models.UniqueConstraint(
                fields=['invoice_no'],
                condition=~models.Q(invoice_no=''),
                name='uniq_healthquote_invoice_no',
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ref} — {self.client_name} ({self.status})"

    def recompute(self):
        """Re-total from the member rows, applying the per-tier discount.

        Members carry the rack (office) premium. We sum the rack per tier, apply
        that tier's discount %, and store NET subtotal/VAT/total — what the client
        is quoted. gross_excl + discount_excl keep the rack figures for the
        quotation's discount line. VAT is charged on the NET (discounted) excl.
        """
        from decimal import Decimal, ROUND_HALF_UP
        from . import health_rates as HR
        Q2 = Decimal('0.01')
        overrides = self.tier_discounts or {}
        by_tier = {}
        for m in self.members.all():
            by_tier[m.tier] = by_tier.get(m.tier, Decimal('0.00')) + m.premium_excl
        gross = sum(by_tier.values(), Decimal('0.00'))
        discount = Decimal('0.00')
        for tier, tier_gross in by_tier.items():
            pct = HR.discount_for(tier, overrides)
            discount += (tier_gross * pct / Decimal('100')).quantize(Q2, rounding=ROUND_HALF_UP)
        net_excl = gross - discount
        net_vat = (net_excl * HR.VAT_RATE).quantize(Q2, rounding=ROUND_HALF_UP)
        self.gross_excl = gross
        self.discount_excl = discount
        self.subtotal_excl = net_excl
        self.vat = net_vat
        self.total_incl = net_excl + net_vat


class HealthQuoteMember(models.Model):
    """One life on a quote. Premium is the OFFICE rate for
    (tier, gender, member_type, age-band), snapshotted at save."""

    class MemberType(models.TextChoices):
        MAIN      = 'main',      'Policy Holder'
        ADULT_DEP = 'adult_dep', 'Adult Dependant'
        CHILD_DEP = 'child_dep', 'Child Dependant'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote       = models.ForeignKey(HealthQuote, on_delete=models.CASCADE, related_name='members')
    full_name   = models.CharField(max_length=200)
    member_type = models.CharField(max_length=12, choices=MemberType.choices, default=MemberType.MAIN)
    gender      = models.CharField(max_length=1, default='M')
    date_of_birth = models.DateField(null=True, blank=True)
    age         = models.PositiveIntegerField(default=0)
    age_band    = models.CharField(max_length=12, blank=True, default='')
    tier        = models.CharField(max_length=20, default='AD_ESSENTIAL')

    premium_excl = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vat          = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    premium_incl = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    position    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'full_name']

    def __str__(self) -> str:
        return f"{self.full_name} [{self.tier}]"


# ---------------------------------------------------------------------------
# ADH → AFA member load file (Phase 2).
#
# The daily pipe-delimited membership file Alpha Direct Health submits to AFA,
# who administer the scheme on iMed. Built in Omni from the Graphite read
# replica (see healthcare/afa_members.py); Graphite itself is never written to.
# ---------------------------------------------------------------------------

class AfaGroupNameMap(models.Model):
    """Graphite employer group → the EXACT group name loaded in iMed.

    AFA's layout requires the group name to match iMed character for character,
    but Graphite stores whatever was typed at capture. Without this table a
    single stray space silently rejects a whole group's rows at AFA, which is
    the most likely way this integration fails quietly. A group with no active
    mapping has its rows HELD, never guessed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employer_group_id = models.CharField(max_length=50, unique=True, db_index=True)
    graphite_name     = models.CharField(max_length=255, blank=True, default='')
    imed_group_name   = models.CharField(max_length=255)
    region_name       = models.CharField(max_length=255, blank=True, default='')
    is_active         = models.BooleanField(default=True)

    # The missing link between the two systems. Omni's invoices point at a
    # billing Contact and have no idea what a Graphite employer group is, so
    # without this the "paid invoice puts members on cover" trigger cannot
    # fire at all. Kept here rather than adding a field to billing.Contact so
    # the whole group mapping lives in one place and billing is untouched.
    billing_contact   = models.ForeignKey(
                            'billing.Contact', null=True, blank=True,
                            on_delete=models.SET_NULL, related_name='afa_group_maps',
                            help_text='The Omni contact invoiced for this employer group.',
                        )
    confirmed_by      = models.CharField(max_length=150, blank=True, default='')
    confirmed_at      = models.DateTimeField(null=True, blank=True)
    notes             = models.TextField(blank=True, default='')
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['imed_group_name']
        verbose_name = 'AFA group name mapping'

    def __str__(self) -> str:
        return f'{self.employer_group_id} → {self.imed_group_name}'


class AfaMemberSnapshot(models.Model):
    """The last state we successfully SENT to AFA, one row per life.

    This is what the daily full pull is diffed against — the file is never an
    accumulated list of events, so a missed day self-heals and a double run is
    harmless.

    `dependant_no` is PERSISTED here and reused for the life of the member.
    Graphite has no dependant number at all, only row order, and recomputing
    from position would renumber the survivors whenever a dependant leaves —
    handing one person's claim history to another (checklist H20).
    """

    class State(models.TextChoices):
        ACTIVE     = 'active',     'On cover'
        RESIGNED   = 'resigned',   'Resigned / cancelled'
        SUSPENDED  = 'suspended',  'Suspended'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_number = models.CharField(max_length=60, db_index=True)
    dependant_no  = models.PositiveIntegerField(default=0)

    employer_group_id = models.CharField(max_length=50, db_index=True, blank=True, default='')
    graphite_policy_id     = models.BigIntegerField(null=True, blank=True)
    graphite_beneficiary_id = models.BigIntegerField(null=True, blank=True)

    # sha256 of the 35 rendered fields — the diff compares hashes, so no
    # policyholder detail is duplicated into this table.
    row_hash   = models.CharField(max_length=64, blank=True, default='')
    state      = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)

    first_sent_run = models.CharField(max_length=40, blank=True, default='')
    last_sent_run  = models.CharField(max_length=40, blank=True, default='')
    last_sent_at   = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['policy_number', 'dependant_no']
        constraints = [
            models.UniqueConstraint(
                fields=['policy_number', 'dependant_no'],
                name='uniq_afa_snapshot_policy_dependant',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.policy_number}/{self.dependant_no} [{self.state}]'


class AfaLoadFileRun(models.Model):
    """One day's load file: what was built, what was held, and what was sent."""

    class Status(models.TextChoices):
        BUILT    = 'built',    'Built — awaiting release'
        RELEASED = 'released', 'Released by a person'
        SENT     = 'sent',     'Delivered to AFA'
        FAILED   = 'failed',   'Transfer failed'
        ABORTED  = 'aborted',  'Aborted by the safety fence'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_date  = models.DateField(unique=True, db_index=True)
    status    = models.CharField(max_length=12, choices=Status.choices, default=Status.BUILT)

    row_count      = models.PositiveIntegerField(default=0)
    new_count      = models.PositiveIntegerField(default=0)
    changed_count  = models.PositiveIntegerField(default=0)
    departure_count = models.PositiveIntegerField(default=0)
    held_count     = models.PositiveIntegerField(default=0)
    held_reasons   = models.JSONField(default=dict, blank=True)

    file_name   = models.CharField(max_length=200, blank=True, default='')
    file_sha256 = models.CharField(max_length=64, blank=True, default='')
    # The rendered pipe-delimited body. Full policyholder data — read is gated
    # by the same permission as release, and it is never logged.
    file_body   = models.TextField(blank=True, default='')

    # What was actually IN this file, so the snapshot can be committed from a
    # LATER request (checklist H26: state stashed on an in-memory instance in
    # the build request is always gone by the time Release is clicked).
    # {"policy_number|dependant_no": {"h": row_hash, "g": group, "p": policy_id,
    #  "b": beneficiary_id}} — hashes and ids only, no member detail.
    sent_keys      = models.JSONField(default=dict, blank=True)
    # The keys whose RESIGNATION row was in this file. Only these may be marked
    # resigned — a departure that was held never reached AFA, and marking it
    # resigned would leave AFA covering someone our snapshot says has left.
    departure_keys = models.JSONField(default=list, blank=True)

    # Safety fence (checklist H25). An empty or truncated replica read must
    # never be written out as everybody having resigned.
    source_row_count   = models.PositiveIntegerField(default=0)
    replica_lag_seconds = models.IntegerField(null=True, blank=True)
    abort_reason       = models.TextField(blank=True, default='')

    built_at    = models.DateTimeField(auto_now_add=True)
    released_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='afa_runs_released',
                  )
    released_at = models.DateTimeField(null=True, blank=True)
    sent_at     = models.DateTimeField(null=True, blank=True)
    send_error  = models.TextField(blank=True, default='')

    # Nullable until AFA confirm whether they return an acknowledgement at all
    # (Phase 0 Q3).
    ack_status = models.CharField(max_length=40, blank=True, default='')
    ack_detail = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-run_date']

    def __str__(self) -> str:
        return f'AFA load file {self.run_date} [{self.status}]'


class ServiceProvider(AuditableMixin, BaseModel):
    """ADH service-provider network registry + readiness tracker.

    The operational master of Alpha Direct Health's provider network: who is in
    the network, their AFA registration / contract status, and whether they are
    READY to accept ADH clients. Distinct from `VendorOnboarding` (that is the
    one-off application + signed-agreement + e-sign artefact) — this is the live
    "state of the network" list the ADH team maintains.

    Fields mirror ADH's own working spreadsheet ("ADH SERVICE PROVIDERS",
    Meduduetso Tlagae) EXACTLY — no invented columns. The one key is the AFA
    practice number, stored as TEXT so leading zeros survive.

    Readiness (`afa_registered`, `qc_confirmed_flag`, `adh_ready`) is DERIVED and
    never manually editable — see the properties below. The team's own manual
    "ADH Acceptance (Ready)" column is preserved verbatim in `adh_acceptance` so
    the derived flag can be reconciled against it (`ready_mismatch`), never
    silently overwriting their judgement.

    Audit: AuditableMixin writes a field-level AuditLog row (old/new/user/time)
    on every save. No hard deletes — deactivate via `is_active` only.
    """

    # --- Identity (spreadsheet: PRACTICE) — the natural key, TEXT ---
    practice_number = models.CharField(
        max_length=32, unique=True, db_index=True,
        help_text="AFA practice number. Stored as text; leading zeros preserved.",
    )

    # --- Provider details (verbatim sheet columns) ---
    name            = models.CharField(max_length=255)                 # Prac Name
    discipline      = models.CharField(max_length=128, blank=True)     # Discipline
    town            = models.CharField(max_length=128, blank=True)     # Town
    email           = models.CharField(max_length=255, blank=True)     # Email Addr (may be ';'-joined — not EmailField)
    contact_number  = models.CharField(max_length=128, blank=True)     # Contact No
    location        = models.TextField(blank=True)                     # Location
    vendor_system   = models.CharField(max_length=64, blank=True)      # VENDOR (their pharmacy software)

    # --- Contract / AFA registration state (sheet: Contract Status) ---
    # Free-text mirror of the sheet: "Signed" / "AFA Signing" / blank.
    contract_status = models.CharField(max_length=64, blank=True)
    afa_registration_date = models.CharField(max_length=64, blank=True)  # kept as text; sheet dates are messy

    # --- QC / onboarding readiness inputs (staff-editable) ---
    welcome_pack        = models.CharField(max_length=16, blank=True)   # Welcome Pack Provided
    sticker_displayed   = models.CharField(max_length=16, blank=True)   # ADH 'Accepted Here' Sticker Displayed
    provider_orientation= models.CharField(max_length=16, blank=True)   # Provider Orientation
    onboarding_link     = models.CharField(max_length=512, blank=True)  # Provider Onboarding Link
    date_contacted      = models.CharField(max_length=64, blank=True)   # DATE CONTANCTED (kept as text)

    # Explicit QC sign-off gate (prompt spec). Distinct from the physical
    # sticker: a staff member deliberately confirms the provider passed QC.
    qc_confirmed = models.BooleanField(default=False)
    qc_date      = models.DateField(null=True, blank=True)

    # --- The team's own manual readiness call (sheet: ADH Acceptance (Ready)) ---
    # Preserved verbatim ("YES"/"NO"/"NA"/blank). NEVER used to derive; kept only
    # so the derived `adh_ready` can be reconciled against human judgement.
    adh_acceptance = models.CharField(max_length=16, blank=True)

    comment = models.TextField(blank=True)                              # COMMENT

    # --- Lifecycle (no hard delete — deactivate only) ---
    is_active = models.BooleanField(default=True, db_index=True)

    # --- Provenance ---
    source_file     = models.CharField(max_length=255, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Service provider"
        verbose_name_plural = "Service providers"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.practice_number} — {self.name}"

    # ---- Derived readiness (NEVER manually editable) ----------------------
    @property
    def afa_registered(self) -> str:
        """Yes / Pending / No, derived from the contract status.

        ADH's own sheet writes YES / NO in the Contract Status column; only a
        handful of rows carry the word "Signed". Testing for "signed" alone read
        210 of 222 registered providers as NOT registered, so the tile showed 11
        (bug 186a514c, raised by the ADH team 14-Sep-2026; the CFO ruled that YES in
        that column means AFA-registered). Pending is tested FIRST so the single
        "AFA Signing" row is not swallowed by the registered test.
        """
        s = (self.contract_status or "").strip().lower()
        if "signing" in s or s == "pending":
            return "Pending"
        if s in {"signed", "yes", "y", "registered"}:
            return "Yes"
        return "No"

    @property
    def qc_confirmed_flag(self) -> bool:
        return bool(self.qc_confirmed)

    @property
    def adh_ready(self) -> bool:
        """DERIVED: ready only if AFA-registered AND QC-confirmed.

        This used to also answer True whenever the sheet's "ADH Acceptance
        (Ready)" said YES. That first branch answered first, so the second was
        never reached: readiness was the spreadsheet column read back, and the
        tile could not disagree with the file it was meant to check. Ritah
        Tonkope's ruling (21-Sep-2026) is that QC validates AFA's readiness
        rather than overriding it — validating needs the two to be able to
        differ, so the manual column is now reconciled against this number
        (`ready_mismatch`) and never feeds it.
        """
        return self.afa_registered == "Yes" and self.qc_confirmed_flag

    @property
    def ready_mismatch(self) -> bool:
        """True when the team's manual 'ADH Acceptance (Ready)' disagrees with
        the derived readiness — surfaced for QC, never auto-corrected."""
        manual = (self.adh_acceptance or "").strip().upper()
        if manual not in {"YES", "NO"}:   # NA / blank are not a disagreement
            return False
        # Compare against the SAME property the tile counts. This used to
        # recompute readiness inline, so the check and the number it checked
        # could drift apart.
        return (manual == "YES") != self.adh_ready


class ServiceProviderApplication(BaseModel):
    """A provider's own application to join the ADH network, captured through the
    PUBLIC no-login apply link. Deliberately light — it is an expression of
    interest the ADH team reviews, NOT the full AFA agreement / e-sign (that
    still comes later via the token invite once accepted). Kept separate from
    ServiceProvider because an applicant may not have an AFA practice number yet,
    and the registry is keyed on that number.
    """
    STATUS_CHOICES = [
        ("pending", "Pending review"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
    ]

    name           = models.CharField(max_length=255)
    discipline     = models.CharField(max_length=128, blank=True)
    town           = models.CharField(max_length=128, blank=True)
    contact_number = models.CharField(max_length=128, blank=True)
    email          = models.CharField(max_length=255, blank=True)
    practice_number = models.CharField(max_length=32, blank=True)   # may not have one yet
    note           = models.TextField(blank=True)

    status      = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="provider_applications_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)

    # Audit for the public (anonymous) submit.
    submitter_ip = models.CharField(max_length=45, blank=True)
    submitter_user_agent = models.TextField(blank=True)

    class Meta:
        verbose_name = "Service provider application"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class ProviderDashboardConfig(BaseModel):
    """Single-row switch for the evening exec dashboard. OFF by default; the ADH
    team turns it ON once they have imported the latest provider file, so the
    6pm send only goes out on fresh data (CFO 2026-09-01)."""
    enabled = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = "Provider dashboard switch"

    def __str__(self) -> str:
        return f"Evening dashboard: {'ON' if self.enabled else 'OFF'}"

    @classmethod
    def current(cls) -> "ProviderDashboardConfig":
        obj = cls.objects.first()
        return obj or cls.objects.create()


class ProviderDailySnapshot(BaseModel):
    """One row per day capturing the provider-network headline numbers so EXCO
    can see daily momentum (added / signed / QC'd / ADH-ready) rather than
    only a point-in-time count. Written by ``manage.py snapshot_provider_counts``
    scheduled after the evening dashboard send."""
    date = models.DateField(unique=True, db_index=True)
    total = models.IntegerField(default=0)
    afa_registered = models.IntegerField(default=0)
    afa_pending = models.IntegerField(default=0)
    adh_ready = models.IntegerField(default=0)
    qc_confirmed = models.IntegerField(default=0)
    mismatches = models.IntegerField(default=0)
    pending_applications = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Provider daily snapshot"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.date} — {self.adh_ready} ready / {self.total} total"


class AdhSettlementRun(models.Model):
    """One Saturday's ADH claims EFT settlement load (B4).

    What arrived, which of the four files was picked, why the other three were
    not, and what the run turned into. This is the record the weekly screen
    reads. It carries counts and amounts, never claimant names.
    """

    class Status(models.TextChoices):
        LOADED  = 'loaded',  'Loaded'
        PARTIAL = 'partial', 'Loaded with problems'
        NOTHING = 'nothing', 'Nothing to load'
        FAILED  = 'failed',  'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    loaded_on = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.LOADED)

    file_name = models.CharField(max_length=255, blank=True, default='')
    #: The bytes we read, fingerprinted. UNIQUE when set, so the SAME file
    #: arriving twice cannot become a second run — the database says no, not a
    #: Python check that a concurrent run could race past. Blank on a failed
    #: run (there were no bytes), hence the partial constraint below.
    file_sha256 = models.CharField(max_length=64, blank=True, default='')

    #: [{name, reason}] — the files that were NOT processed, each with the one
    #: condition it failed. Three of these every Saturday.
    rejected = models.JSONField(default=list, blank=True)

    line_count    = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count  = models.PositiveIntegerField(default=0)
    #: [{claim_number, reason}] — lines that produced no payment request.
    problems = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-loaded_on', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['file_sha256'],
                condition=models.Q(file_sha256__gt=''),
                name='adh_settlement_one_run_per_file'),
        ]

    def __str__(self) -> str:
        return f'ADH settlement {self.loaded_on} [{self.status}]'


class AdhSettlementLine(models.Model):
    """One claim line that has been turned into a payment request.

    THE NO-DOUBLE-LOAD GUARD. `dedupe_key` is UNIQUE at the database level —
    see healthcare.claims_settlement.compute_line_dedupe_key for what goes into
    it and why (it is modelled on banking.models.compute_line_dedupe_key,
    occurrence counter and all). The row is written BEFORE the payment request
    is created, so a file arriving twice loses the insert and skips; it is the
    constraint that stops the double load, not the code around it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AdhSettlementRun, on_delete=models.CASCADE,
                            related_name='lines')
    dedupe_key = models.CharField(max_length=64, unique=True)

    claim_number = models.CharField(max_length=60, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    #: The payment request this line became. Not a ForeignKey on purpose — a
    #: request that is later cancelled or purged must not take the dedupe key
    #: with it and let the same settlement load a second time.
    payment_request_id = models.UUIDField(null=True, blank=True)
    payment_ref = models.CharField(max_length=48, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'ADH settlement line {self.claim_number}'
