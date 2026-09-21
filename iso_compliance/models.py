"""
iso_compliance/models.py — ISO 27001 + supporting controls audit register.

The "10 Commandments" is a CFO-friendly distillation of ISO/IEC 27001:2022
(Annex A) plus regulatory neighbours (DPA Botswana, NBFIRA conduct rules).
Each commandment maps to one or more Annex-A clauses and is scored:

  GOOD     — controls operating, evidence current
  DONE     — controls implemented, periodic check passes
  PARTIAL  — implemented but evidence gap / outdated
  PENDING  — required but not implemented
  N/A      — not applicable to scope

Findings are produced by the `iso_audit` management command which scans
the live system (settings, dependencies, models, recent admin actions)
and writes one or more AuditFinding rows per commandment. The frontend
/compliance/iso page renders the 10 cards plus drill-down findings.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Commandment(models.Model):
    """One of the 10 distilled ISO commandments. Seeded once, edited rarely."""

    STATUS_GOOD = 'good'
    STATUS_DONE = 'done'
    STATUS_PARTIAL = 'partial'
    STATUS_PENDING = 'pending'
    STATUS_NA = 'na'

    STATUS_CHOICES = [
        (STATUS_GOOD, 'Good — operating'),
        (STATUS_DONE, 'Done — implemented'),
        (STATUS_PARTIAL, 'Partial — evidence gap'),
        (STATUS_PENDING, 'Pending — not yet built'),
        (STATUS_NA, 'N/A — out of scope'),
    ]

    number = models.PositiveSmallIntegerField(unique=True)
    title = models.CharField(max_length=120)
    summary = models.CharField(max_length=400)
    iso_clauses = models.CharField(
        max_length=200,
        help_text='Comma-separated Annex A clauses, e.g. "A.5.15, A.8.2"',
    )
    why_it_matters = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    last_audited_at = models.DateTimeField(null=True, blank=True)
    owner = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['number']

    def __str__(self) -> str:
        return f'{self.number:02d}. {self.title}'


class AuditFinding(models.Model):
    """One finding from the latest iso_audit run, attached to a commandment."""

    SEVERITY_GOOD = 'good'
    SEVERITY_INFO = 'info'
    SEVERITY_LOW = 'low'
    SEVERITY_MED = 'medium'
    SEVERITY_HIGH = 'high'
    SEVERITY_CRIT = 'critical'

    SEVERITY_CHOICES = [
        (SEVERITY_GOOD, 'Good'),
        (SEVERITY_INFO, 'Info'),
        (SEVERITY_LOW, 'Low'),
        (SEVERITY_MED, 'Medium'),
        (SEVERITY_HIGH, 'High'),
        (SEVERITY_CRIT, 'Critical'),
    ]

    STATE_OPEN = 'open'
    STATE_RESOLVED = 'resolved'
    STATE_ACCEPTED = 'accepted'   # CFO accepted the risk

    STATE_CHOICES = [
        (STATE_OPEN, 'Open'),
        (STATE_RESOLVED, 'Resolved'),
        (STATE_ACCEPTED, 'Risk accepted'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    commandment = models.ForeignKey(Commandment, on_delete=models.CASCADE, related_name='findings')
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_INFO)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_OPEN)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default='')
    fix_hint = models.TextField(blank=True, default='')
    evidence = models.TextField(blank=True, default='')   # raw output of the check
    detected_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-detected_at']
        indexes = [
            models.Index(fields=['commandment', 'state']),
            models.Index(fields=['severity', 'state']),
        ]

    def __str__(self) -> str:
        return f'[{self.severity}] {self.title}'


class AuditRun(models.Model):
    """One full run of the iso_audit command. Latest run = displayed score."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    actor = models.CharField(max_length=80, blank=True, default='system')
    findings_created = models.PositiveIntegerField(default=0)
    score_pct = models.PositiveSmallIntegerField(default=0)   # 0-100 across the 10
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']

    def __str__(self) -> str:
        return f'AuditRun {self.started_at:%Y-%m-%d %H:%M} → {self.score_pct}%'


# ── ISO 27001:2022 Statement of Applicability ─────────────────────────

class SoAControl(models.Model):
    """One Annex A control from ISO/IEC 27001:2022 (93 in total, A.5.1 – A.8.34).

    Auditors expect a documented Statement of Applicability listing every
    control, whether it is applicable, the justification, current
    implementation status, and a link to evidence.
    """

    CONTROL_TYPE_PREVENTIVE = 'preventive'
    CONTROL_TYPE_DETECTIVE = 'detective'
    CONTROL_TYPE_CORRECTIVE = 'corrective'
    CONTROL_TYPES = [
        (CONTROL_TYPE_PREVENTIVE, 'Preventive'),
        (CONTROL_TYPE_DETECTIVE, 'Detective'),
        (CONTROL_TYPE_CORRECTIVE, 'Corrective'),
    ]

    DOMAIN_ORG = 'organisational'
    DOMAIN_PEOPLE = 'people'
    DOMAIN_PHYSICAL = 'physical'
    DOMAIN_TECH = 'technological'
    DOMAINS = [
        (DOMAIN_ORG, 'Organisational (A.5)'),
        (DOMAIN_PEOPLE, 'People (A.6)'),
        (DOMAIN_PHYSICAL, 'Physical (A.7)'),
        (DOMAIN_TECH, 'Technological (A.8)'),
    ]

    STATUS_IMPLEMENTED = 'implemented'
    STATUS_PARTIAL = 'partial'
    STATUS_PLANNED = 'planned'
    STATUS_NOT_IMPLEMENTED = 'not_implemented'
    STATUS_EXCLUDED = 'excluded'
    STATUS_CHOICES = [
        (STATUS_IMPLEMENTED, 'Implemented'),
        (STATUS_PARTIAL, 'Partial'),
        (STATUS_PLANNED, 'Planned'),
        (STATUS_NOT_IMPLEMENTED, 'Not implemented'),
        (STATUS_EXCLUDED, 'Excluded'),
    ]

    clause = models.CharField(max_length=20, unique=True,
                              help_text='e.g. A.5.15 — must match ISO 27001:2022.')
    title = models.CharField(max_length=200)
    domain = models.CharField(max_length=20, choices=DOMAINS)
    control_type = models.CharField(max_length=20, choices=CONTROL_TYPES,
                                    default=CONTROL_TYPE_PREVENTIVE)
    description = models.TextField(blank=True, default='')
    applicable = models.BooleanField(default=True)
    justification = models.TextField(
        blank=True, default='',
        help_text='Why applicable / why excluded. Required by ISO 27001 6.1.3 d).',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED)
    owner = models.CharField(max_length=80, blank=True, default='')
    evidence_ref = models.TextField(blank=True, default='',
                                    help_text='Link / path / doc reference.')
    commandment = models.ForeignKey(
        Commandment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='soa_controls',
    )
    last_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['clause']

    def __str__(self) -> str:
        return f'{self.clause} {self.title}'


# ── Risk Register ─────────────────────────────────────────────────────

class Risk(models.Model):
    """ISO 27005-style risk row. 1–5 scale per axis; score = L × I."""

    TREATMENT_REDUCE = 'reduce'
    TREATMENT_TRANSFER = 'transfer'
    TREATMENT_AVOID = 'avoid'
    TREATMENT_ACCEPT = 'accept'
    TREATMENTS = [
        (TREATMENT_REDUCE, 'Reduce — implement controls'),
        (TREATMENT_TRANSFER, 'Transfer — insure / outsource'),
        (TREATMENT_AVOID, 'Avoid — stop the activity'),
        (TREATMENT_ACCEPT, 'Accept — sign off the residual'),
    ]

    STATUS_OPEN = 'open'
    STATUS_MITIGATED = 'mitigated'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_MITIGATED, 'Mitigated'),
        (STATUS_CLOSED, 'Closed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref = models.CharField(max_length=20, unique=True,
                           help_text='Stable reference e.g. R-001')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    asset = models.CharField(max_length=120, blank=True, default='')
    threat = models.CharField(max_length=200, blank=True, default='')
    vulnerability = models.CharField(max_length=200, blank=True, default='')
    likelihood = models.PositiveSmallIntegerField(default=3)   # 1..5
    impact = models.PositiveSmallIntegerField(default=3)       # 1..5
    treatment = models.CharField(max_length=20, choices=TREATMENTS,
                                 default=TREATMENT_REDUCE)
    treatment_plan = models.TextField(blank=True, default='')
    owner = models.CharField(max_length=80, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_OPEN)
    residual_likelihood = models.PositiveSmallIntegerField(null=True, blank=True)
    residual_impact = models.PositiveSmallIntegerField(null=True, blank=True)
    controls = models.ManyToManyField(SoAControl, blank=True, related_name='risks')
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['ref']

    @property
    def score(self) -> int:
        return int(self.likelihood or 0) * int(self.impact or 0)

    @property
    def residual_score(self) -> int | None:
        if self.residual_likelihood and self.residual_impact:
            return self.residual_likelihood * self.residual_impact
        return None

    def __str__(self) -> str:
        return f'{self.ref} {self.title}'


# ── CAPA — Corrective / Preventive action lifecycle ──────────────────

class CAPA(models.Model):
    """ISO 9001/27001 CAPA: nonconformity → root cause → action → verify."""

    STATUS_DRAFT = 'draft'
    STATUS_OPEN = 'open'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_VERIFICATION = 'verification'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_OPEN, 'Open'),
        (STATUS_IN_PROGRESS, 'In progress'),
        (STATUS_VERIFICATION, 'Pending verification'),
        (STATUS_CLOSED, 'Closed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref = models.CharField(max_length=20, unique=True,
                           help_text='Stable reference e.g. CAPA-001')
    finding = models.ForeignKey(AuditFinding, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='capas')
    commandment = models.ForeignKey(Commandment, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='capas')
    title = models.CharField(max_length=200)
    nonconformity = models.TextField(blank=True, default='',
                                     help_text='The deviation found.')
    root_cause = models.TextField(blank=True, default='',
                                  help_text='5-whys / fishbone result.')
    corrective_action = models.TextField(blank=True, default='')
    preventive_action = models.TextField(blank=True, default='')
    owner = models.CharField(max_length=80, blank=True, default='')
    due_date = models.DateField(null=True, blank=True)
    verifier = models.CharField(max_length=80, blank=True, default='')
    verified_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.ref} {self.title}'


# ── Policy register ──────────────────────────────────────────────────

class Policy(models.Model):
    """Information-Security Policy and friends (BCP, AUP, IRP, etc.)."""

    STATUS_DRAFT = 'draft'
    STATUS_REVIEW = 'review'
    STATUS_APPROVED = 'approved'
    STATUS_RETIRED = 'retired'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_REVIEW, 'Under review'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_RETIRED, 'Retired'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True,
                            help_text='e.g. POL-INFOSEC-001')
    title = models.CharField(max_length=200)
    version = models.CharField(max_length=20, default='1.0')
    summary = models.TextField(blank=True, default='')
    document_url = models.URLField(blank=True, default='')
    owner = models.CharField(max_length=80, blank=True, default='')
    approver = models.CharField(max_length=80, blank=True, default='')
    approved_at = models.DateTimeField(null=True, blank=True)
    review_due = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)
    controls = models.ManyToManyField(SoAControl, blank=True, related_name='policies')

    class Meta:
        ordering = ['code']

    def __str__(self) -> str:
        return f'{self.code} {self.title} v{self.version}'


class FrameworkRequirement(models.Model):
    """One Botswana DPA 2024 requirement as already mapped by the DPO — 73 rows,
    seeded from DPA_2024_requirements.xlsx (Developer Build Brief, 2026-09-08).
    Feeds the ROPA module's legal-basis, DPIA-trigger and vendor-risk logic so it
    cites a real clause instead of a keyword guess. Not a live legal feed."""

    CATEGORY_CHOICES = [
        ('principles', 'Principles'),
        ('lawful_basis', 'Lawful basis for processing'),
        ('consent', 'Consent'),
        ('special_categories', 'Special categories of personal data'),
        ('childrens_data', "Processing of children's data"),
        ('data_subject_rights', 'Rights of the data subject'),
        ('controller_processor', 'Controller and processor'),
        ('transfers', 'Transfers of personal data'),
        ('security_breach', 'Security and breach notification'),
        ('dpo_dpia', 'Data protection officer and DPIA'),
    ]

    requirement_code = models.CharField(max_length=30, primary_key=True,
                                        help_text="e.g. 'S48.1' — matches the DPO spreadsheet")
    requirement_name = models.TextField(help_text='The actual DPA 2024 clause text')
    plain_description = models.TextField(blank=True, default='',
                                         help_text="The DPO's plain-language explanation")
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES, default='principles')
    source_act = models.CharField(max_length=120, default='Botswana DPA 2024')
    policies = models.ManyToManyField('InternalPolicy', through='RequirementPolicyLink',
                                      related_name='requirements', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'requirement_code']

    def __str__(self) -> str:
        return f'{self.requirement_code} — {self.requirement_name[:60]}'


class InternalPolicy(models.Model):
    """An Alpha Direct policy document the DPA requirements map to. Separate from
    the ISO-27001 infosec Policy register above; this is the DPO/DPA policy set.
    Placeholder rows are auto-created when a requirement cites a policy name not
    yet in the system (is_placeholder=True until the DPO fills it in)."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('published', 'Published'),
        ('archived', 'Archived'),
        ('deprecated', 'Deprecated'),
    ]

    policy_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_name = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    version = models.CharField(max_length=20, blank=True, default='')
    document_link = models.CharField(max_length=500, blank=True, default='')
    last_reviewed = models.DateField(null=True, blank=True)
    next_review_due = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='owned_dpa_policies')
    is_placeholder = models.BooleanField(
        default=False, help_text='Auto-created from a requirement reference; DPO to complete.')
    is_gap = models.BooleanField(
        default=False, help_text='The DPO flagged this policy as a known gap in the mapping.')
    gap_note = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['policy_name']
        verbose_name_plural = 'internal policies'

    def __str__(self) -> str:
        return self.policy_name


class RequirementPolicyLink(models.Model):
    """Which internal policy governs which DPA requirement (many-to-many). Auto-
    parsed from the spreadsheet's 'Policy: X; Y' text; the DPO CONFIRMS each link
    (confirmed=False until then — never trust free-text parsing blindly)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requirement = models.ForeignKey(FrameworkRequirement, on_delete=models.CASCADE,
                                    related_name='links')
    policy = models.ForeignKey(InternalPolicy, on_delete=models.CASCADE,
                               related_name='links')
    specific_section = models.CharField(max_length=120, blank=True, default='',
                                        help_text="e.g. 'Section 9' or 'Clause 3.7'")
    confirmed = models.BooleanField(default=False,
                                    help_text='DPO has confirmed this auto-parsed link.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['requirement__requirement_code']
        unique_together = [('requirement', 'policy', 'specific_section')]

    def __str__(self) -> str:
        return f'{self.requirement_id} ↔ {self.policy.policy_name}'


class PiiFieldReview(models.Model):
    """The DPO's review decision for one detected personal-data FIELD (column) in
    the Omni/Graphite schema (Developer Build Brief — ROPA detection logic). One
    row per (app, model, field); absence = not yet reviewed."""

    STATUS_NEEDS = 'needs_review'
    STATUS_FLAGGED = 'flagged'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DISMISSED = 'dismissed'
    STATUS_CHOICES = [
        (STATUS_NEEDS, 'Needs review'),
        (STATUS_FLAGGED, 'Flagged'),
        (STATUS_CONFIRMED, 'Confirmed personal data'),
        (STATUS_DISMISSED, 'Not personal data'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app_label = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    field_name = models.CharField(max_length=150)
    category = models.CharField(max_length=40, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEEDS)
    note = models.TextField(blank=True, default='')
    reviewed_by = models.CharField(max_length=120, blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['app_label', 'model_name', 'field_name']
        unique_together = [('app_label', 'model_name', 'field_name')]

    def __str__(self) -> str:
        return f'{self.app_label}.{self.model_name}.{self.field_name} [{self.status}]'


# ── ROPA registry (Developer Build Brief, DPO Oratile 2026-09-08) ─────

class RulebookRequirement(models.Model):
    """A finite, DPO-maintained compliance rule a ROPA entry is checked against
    (NOT the full legal text). If an entry matches trigger_field/value and the
    satisfying_evidence is not met, the entry is flagged_incomplete."""

    requirement_id = models.CharField(max_length=40, primary_key=True,
                                      help_text='e.g. DPA-s48, POL-AIGOV-S9')
    source = models.CharField(max_length=200, help_text='e.g. "Botswana DPA 2024, s.48"')
    description = models.TextField()
    trigger_field = models.CharField(max_length=80, help_text='ROPA field that activates this rule')
    trigger_value = models.CharField(max_length=120, blank=True, default='')
    satisfying_evidence = models.TextField(
        blank=True, default='',
        help_text='machine hint: "<field> filled" or "vendor.<field>=<value>"')
    framework_requirement = models.ForeignKey('FrameworkRequirement', null=True, blank=True,
                                              on_delete=models.SET_NULL, related_name='rulebook_rules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['requirement_id']

    def __str__(self) -> str:
        return f'{self.requirement_id}: {self.description[:50]}'


class LegalBasisRule(models.Model):
    """DPO-maintained lookup that only SUGGESTS a lawful basis — never confirms."""

    BASIS_CHOICES = [
        ('consent', 'Consent'),
        ('contract', 'Performance of a contract'),
        ('legal_obligation', 'Legal obligation'),
        ('vital_interests', 'Vital interests'),
        ('public_task', 'Public task'),
        ('legitimate_interests', 'Legitimate interests'),
    ]

    rule_id = models.CharField(max_length=40, primary_key=True)
    match_department = models.CharField(max_length=80, blank=True, default='',
                                        help_text='blank = any department')
    match_purpose_keywords = models.JSONField(default=list,
                                              help_text='list of keywords, e.g. ["claim","policy servicing"]')
    suggested_basis = models.CharField(max_length=30, choices=BASIS_CHOICES)
    reason_template = models.TextField(
        help_text='e.g. "This looks like {purpose}, which usually relies on performance of a contract."')
    framework_requirement = models.ForeignKey('FrameworkRequirement', null=True, blank=True,
                                              on_delete=models.SET_NULL, related_name='basis_rules',
                                              help_text='the lawful_basis requirement this cites')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rule_id']

    def __str__(self) -> str:
        return f'{self.rule_id} -> {self.suggested_basis}'


class VendorRegister(models.Model):
    """A processor/vendor with a SUGGESTED risk score; the DPO confirms the level."""

    RISK_CHOICES = [('Low', 'Low'), ('Medium', 'Medium'), ('High', 'High')]

    vendor_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor_name = models.CharField(max_length=200, unique=True)
    dpa_contract_signed = models.BooleanField(default=False)
    sub_processors_used = models.BooleanField(default=False)
    sub_processor_names = models.TextField(blank=True, default='')
    last_review_date = models.DateField(null=True, blank=True)
    safeguards_on_file = models.TextField(blank=True, default='')
    # suggested (system) — never the record until confirmed
    suggested_risk_score = models.IntegerField(default=0)
    suggested_risk_reason = models.JSONField(default=list, help_text='itemised contributing factors')
    suggested_risk_level = models.CharField(max_length=10, blank=True, default='')
    # confirmed (DPO)
    confirmed_risk_level = models.CharField(max_length=10, choices=RISK_CHOICES, blank=True, default='')
    risk_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='confirmed_vendor_risks')
    risk_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['vendor_name']

    def __str__(self) -> str:
        return self.vendor_name


class RopaEntry(models.Model):
    """One processing activity. System auto-fills detection + suggestions; the DPO
    (or department head) fills the human fields; a suggestion NEVER self-finalises —
    confirmed_lawful_basis is a required human action."""

    FLAG_DRAFT = 'draft_needs_review'
    FLAG_CONFIRMED = 'confirmed'
    FLAG_INCOMPLETE = 'flagged_incomplete'
    FLAG_CHOICES = [
        (FLAG_DRAFT, 'Draft — needs review'),
        (FLAG_CONFIRMED, 'Confirmed'),
        (FLAG_INCOMPLETE, 'Flagged — incomplete'),
    ]
    XBORDER_CHOICES = [('Yes', 'Yes'), ('No', 'No')]
    DPIA_CHOICES = [('Yes', 'Yes'), ('No', 'No'), ('Pending', 'Pending')]

    entry_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # auto (system, read-only to users)
    source_table = models.CharField(max_length=150, blank=True, default='')
    source_field = models.CharField(max_length=150, blank=True, default='')
    detected_data_category = models.CharField(max_length=60, blank=True, default='')
    date_last_reviewed = models.DateTimeField(auto_now=True)
    flag_status = models.CharField(max_length=25, choices=FLAG_CHOICES, default=FLAG_DRAFT)
    flag_reasons = models.JSONField(default=list, help_text='unresolved rulebook requirement_ids')
    suggested_lawful_basis = models.CharField(max_length=30, blank=True, default='')
    suggested_lawful_basis_reason = models.TextField(blank=True, default='')
    # human-entered / confirmed
    department = models.CharField(max_length=80, blank=True, default='')
    processing_activity = models.CharField(max_length=200, blank=True, default='')
    purpose_of_processing = models.TextField(blank=True, default='')
    data_subject_categories = models.TextField(blank=True, default='')
    confirmed_lawful_basis = models.CharField(max_length=30, choices=LegalBasisRule.BASIS_CHOICES,
                                              blank=True, default='')
    lawful_basis_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                                  on_delete=models.SET_NULL, related_name='confirmed_ropa_bases')
    lawful_basis_confirmed_at = models.DateTimeField(null=True, blank=True)
    processor_vendor = models.ForeignKey(VendorRegister, null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name='ropa_entries')
    recipients_third_parties = models.TextField(blank=True, default='')
    cross_border_transfer = models.CharField(max_length=3, choices=XBORDER_CHOICES, blank=True, default='')
    cross_border_details = models.TextField(blank=True, default='')
    retention_period = models.CharField(max_length=200, blank=True, default='')
    security_measures = models.TextField(blank=True, default='')
    description_of_risks = models.TextField(blank=True, default='')
    dpia_required = models.CharField(max_length=8, choices=DPIA_CHOICES, blank=True, default='')
    dpia_status = models.CharField(max_length=120, blank=True, default='')
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='confirmed_ropa_entries')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['flag_status', 'department', 'source_table', 'source_field']
        unique_together = [('source_table', 'source_field')]

    def __str__(self) -> str:
        return self.processing_activity or f'{self.source_table}.{self.source_field}'


# ── Evidence repository ──────────────────────────────────────────────

class Evidence(models.Model):
    """A single piece of evidence attached to a control or finding."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    control = models.ForeignKey(SoAControl, null=True, blank=True,
                                on_delete=models.CASCADE, related_name='evidence_items')
    finding = models.ForeignKey(AuditFinding, null=True, blank=True,
                                on_delete=models.CASCADE, related_name='evidence_items')
    commandment = models.ForeignKey(Commandment, null=True, blank=True,
                                    on_delete=models.CASCADE, related_name='evidence_items')
    label = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    url = models.URLField(blank=True, default='')
    captured_by = models.CharField(max_length=80, blank=True, default='')
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-captured_at']

    def __str__(self) -> str:
        return self.label


# ── Internal Audit + Management Review ───────────────────────────────

class InternalAudit(models.Model):
    """Annual / interim internal audit per clause 9.2."""

    STATUS_PLANNED = 'planned'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETE = 'complete'
    STATUS_CHOICES = [
        (STATUS_PLANNED, 'Planned'),
        (STATUS_IN_PROGRESS, 'In progress'),
        (STATUS_COMPLETE, 'Complete'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ref = models.CharField(max_length=20, unique=True,
                           help_text='e.g. IA-2026-Q3')
    scope = models.TextField(help_text='Scope of this audit (sites, processes, controls).')
    lead_auditor = models.CharField(max_length=80, blank=True, default='')
    scheduled_for = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
                              default=STATUS_PLANNED)
    summary = models.TextField(blank=True, default='')
    findings_count = models.PositiveIntegerField(default=0)
    report_url = models.URLField(blank=True, default='')

    class Meta:
        ordering = ['-scheduled_for', 'ref']

    def __str__(self) -> str:
        return f'{self.ref} ({self.get_status_display()})'


class ManagementReview(models.Model):
    """Periodic top-management review per clause 9.3."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review_date = models.DateField()
    chair = models.CharField(max_length=80, blank=True, default='CFO')
    attendees = models.TextField(blank=True, default='')
    inputs_considered = models.TextField(
        blank=True, default='',
        help_text='Status of actions; changes; performance; nonconformities; audit results; risks.',
    )
    decisions = models.TextField(blank=True, default='')
    action_items = models.TextField(blank=True, default='')
    next_review_due = models.DateField(null=True, blank=True)
    minutes_url = models.URLField(blank=True, default='')

    class Meta:
        ordering = ['-review_date']

    def __str__(self) -> str:
        return f'Management Review {self.review_date:%Y-%m-%d}'


# == SOP Bank - ISO 9001 QMS document library + read-acknowledgment =====
#
# CFO directive 2026-06-10 (BOBS ISO 9001 submission, Unami's audit-evidence
# email): every staff member must be able to reach their department's SOPs,
# and the company must be able to PROVE who has read what. The SOP Bank is
# that proof: documents live in media (alpha_finance_media volume), reads
# are acknowledged per user per revision, and coverage is reportable per
# department.

class SOPDocument(models.Model):
    """One Standard Operating Procedure document (ISO 9001 QMS evidence)."""

    STATUS_ACTIVE = 'active'
    STATUS_DRAFT = 'draft'          # AI/owner draft pending approval
    STATUS_OBSOLETE = 'obsolete'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_DRAFT, 'Draft - pending owner approval'),
        (STATUS_OBSOLETE, 'Obsolete'),
    ]

    OMNI_FIT_OK = 'ok'
    OMNI_FIT_GAP = 'gap'            # references processes omni has replaced
    OMNI_FIT_NA = 'na'              # process outside omni scope
    OMNI_FIT_UNREVIEWED = 'unreviewed'
    OMNI_FIT_CHOICES = [
        (OMNI_FIT_OK, 'Aligned with omni'),
        (OMNI_FIT_GAP, 'Gap - references replaced process'),
        (OMNI_FIT_NA, 'Not applicable to omni'),
        (OMNI_FIT_UNREVIEWED, 'Not yet reviewed'),
    ]

    # A single library holds two document kinds side by side per department:
    # SOPs (how-to procedures) and Policies (governance rules). Unami's
    # "Corporate Governance -> <Department> -> All SOPs / All Policies" view
    # (CFO directive 2026-07-27). Everything else (read-acknowledgement,
    # coverage, search) works identically for both.
    DOC_TYPE_SOP = 'sop'
    DOC_TYPE_POLICY = 'policy'
    DOC_TYPE_CHOICES = [
        (DOC_TYPE_SOP, 'SOP'),
        (DOC_TYPE_POLICY, 'Policy'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.CharField(max_length=80, db_index=True)
    doc_type = models.CharField(max_length=10, choices=DOC_TYPE_CHOICES,
                                default=DOC_TYPE_SOP, db_index=True)
    sop_number = models.CharField(max_length=20, blank=True, default='',
                                  help_text='Mastersheet reference number where known.')
    title = models.CharField(max_length=255)
    owner = models.CharField(max_length=80, blank=True, default='')
    revision_date = models.DateField(null=True, blank=True)
    iso_compliant = models.BooleanField(
        null=True, blank=True,
        help_text='Mastersheet "ISO 9001:2015 Compliant" flag. NULL = not assessed.')
    file = models.FileField(upload_to='sop_bank/%Y/', max_length=400)
    file_type = models.CharField(max_length=10, blank=True, default='')
    size_bytes = models.PositiveBigIntegerField(default=0)
    content_text = models.TextField(
        blank=True, default='',
        help_text='Extracted plain text (docx) - powers search. Empty for video/pdf.')
    source_path = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Original Data Room relative path, for traceability.')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_ACTIVE, db_index=True)
    omni_fit = models.CharField(max_length=12, choices=OMNI_FIT_CHOICES,
                                default=OMNI_FIT_UNREVIEWED)
    omni_fit_notes = models.TextField(
        blank=True, default='',
        help_text='What the omni-alignment review flagged (legacy system refs etc.).')
    uploaded_by = models.CharField(
        max_length=80, blank=True, default='',
        help_text='Username of the staff member who uploaded this via the UI '
                  '(blank for the original bulk Data Room ingest).')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'sop_number', 'title']
        indexes = [models.Index(fields=['department', 'status'])]
        verbose_name = 'SOP document'

    def __str__(self) -> str:
        return f'[{self.department}] {self.title}'


class SOPAcknowledgement(models.Model):
    """User X confirmed they read + understood SOP Y. The ISO 9001 training
    evidence row - auditors ask exactly this."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE,
                            related_name='acknowledgements')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE,
                             related_name='sop_acknowledgements')
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-acknowledged_at']
        constraints = [
            models.UniqueConstraint(fields=['sop', 'user'],
                                    name='uniq_sop_ack_per_user'),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} read {self.sop_id} at {self.acknowledged_at:%Y-%m-%d}'


# ── DPO / DPIA register (Data Protection Impact Assessments) ──────────
# CFO directive 2026-08-13. Botswana DPA: special-category (health) data => P1.
class DPIA(models.Model):
    # Constants live on the class (house convention, e.g. SOPDocument.STATUS_*)
    # so both the ORM and the seed command reference DPIA.RATING_P1 etc.
    RATING_P1 = 'P1'
    RATING_P2 = 'P2'
    RATING_P3 = 'P3'
    RATING_CHOICES = [
        (RATING_P1, 'P1 — High risk'),
        (RATING_P2, 'P2 — Medium risk'),
        (RATING_P3, 'P3 — Low risk'),
    ]

    RESIDUAL_LOW = 'low'
    RESIDUAL_MEDIUM = 'medium'
    RESIDUAL_HIGH = 'high'
    RESIDUAL_CHOICES = [
        (RESIDUAL_LOW, 'Low'),
        (RESIDUAL_MEDIUM, 'Medium'),
        (RESIDUAL_HIGH, 'High'),
    ]

    STATUS_DRAFT = 'draft'
    STATUS_IN_REVIEW = 'in_review'
    STATUS_CONDITIONS_OPEN = 'conditions_open'
    STATUS_APPROVED = 'approved'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_IN_REVIEW, 'In review'),
        (STATUS_CONDITIONS_OPEN, 'Conditions open'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_CLOSED, 'Closed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.CharField(max_length=160)
    description = models.TextField(blank=True, default='')
    data_types = models.TextField(blank=True, default='')
    special_category = models.BooleanField(default=False)
    risk_rating = models.CharField(max_length=2, choices=RATING_CHOICES, default=RATING_P3)
    residual_risk = models.CharField(max_length=6, choices=RESIDUAL_CHOICES, default=RESIDUAL_MEDIUM)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    dpo_reviewer = models.CharField(max_length=120, blank=True, default='')
    dpo_signed = models.BooleanField(default=False)
    dpo_signed_at = models.DateTimeField(null=True, blank=True)
    compliance_officer = models.CharField(max_length=120, blank=True, default='')
    compliance_signed = models.BooleanField(default=False)
    compliance_signed_at = models.DateTimeField(null=True, blank=True)
    cfo_signed = models.BooleanField(default=False)
    cfo_signed_at = models.DateTimeField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Full DPIA form (matches the Alpha Direct DPIA form, e.g. DPIA-2026-004) ──
    # CFO 2026-08-13. All optional so existing thin records stay valid.
    NCE_CHOICES = [('new', 'New'), ('change', 'Change'), ('existing', 'Existing')]
    LAWFUL_CHOICES = [
        ('contract', 'Contract'),
        ('legal_obligation', 'Legal obligation'),
        ('legitimate_interests', 'Legitimate interests'),
        ('consent', 'Consent'),
        ('vital_interests', 'Vital interests'),
        ('public_task', 'Public task'),
    ]
    DECISION_PROCEED = 'proceed'
    DECISION_CONDITIONS = 'proceed_conditions'
    DECISION_NO = 'do_not_proceed'
    DECISION_CHOICES = [
        (DECISION_PROCEED, 'Proceed'),
        (DECISION_CONDITIONS, 'Proceed with conditions'),
        (DECISION_NO, 'Do not proceed'),
    ]

    # Header
    dpia_ref = models.CharField(max_length=40, blank=True, default='')
    date_opened = models.DateField(null=True, blank=True)
    department = models.CharField(max_length=160, blank=True, default='')
    process_owner = models.CharField(max_length=200, blank=True, default='')
    system_used = models.TextField(blank=True, default='')
    new_change_existing = models.CharField(max_length=10, choices=NCE_CHOICES, blank=True, default='')
    planned_go_live = models.CharField(max_length=200, blank=True, default='')
    vendor_involved = models.BooleanField(default=False)
    cross_border = models.BooleanField(default=False)
    # 1. What are you doing (description above) + purpose
    purpose = models.TextField(blank=True, default='')
    # 2. Data & whose data
    data_subjects = models.TextField(blank=True, default='')
    data_categories = models.JSONField(default=dict, blank=True)
    data_other = models.TextField(blank=True, default='')
    special_category_detail = models.TextField(blank=True, default='')
    vulnerable = models.BooleanField(default=False)
    vulnerable_note = models.TextField(blank=True, default='')
    volume = models.CharField(max_length=200, blank=True, default='')
    # 3. DPIA triggers
    triggers = models.JSONField(default=dict, blank=True)
    # 4. Lawful basis & transparency
    lawful_basis = models.CharField(max_length=24, choices=LAWFUL_CHOICES, blank=True, default='')
    lawful_basis_note = models.TextField(blank=True, default='')
    special_category_basis = models.TextField(blank=True, default='')
    how_informed = models.TextField(blank=True, default='')
    how_rights = models.TextField(blank=True, default='')
    # 5. Sharing, vendors, transfers
    internal_sharing = models.TextField(blank=True, default='')
    external_vendors = models.TextField(blank=True, default='')
    cross_border_detail = models.TextField(blank=True, default='')
    # 6. Retention
    retention_period = models.TextField(blank=True, default='')
    retention_reason = models.TextField(blank=True, default='')
    disposal_method = models.TextField(blank=True, default='')
    # 7. Security controls (checklist)
    security_controls = models.JSONField(default=dict, blank=True)
    security_note = models.TextField(blank=True, default='')
    # 9. Decision & sign-off
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True, default='')
    dpo_review_note = models.TextField(blank=True, default='')
    statement_of_alignment = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.project} [{self.risk_rating}]'


class DPIACondition(models.Model):
    PHASE_PILOT = 'pilot'
    PHASE_ROLLOUT = 'rollout'
    PHASE_CHOICES = [
        (PHASE_PILOT, 'Pilot (current scope)'),
        (PHASE_ROLLOUT, 'Before customer rollout'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dpia = models.ForeignKey(DPIA, on_delete=models.CASCADE, related_name='conditions')
    phase = models.CharField(max_length=8, choices=PHASE_CHOICES, default=PHASE_PILOT)
    text = models.CharField(max_length=400)
    owner = models.CharField(max_length=120, blank=True, default='')
    due_date = models.DateField(null=True, blank=True)
    done = models.BooleanField(default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['phase', 'order']

    def __str__(self):
        return f'[{self.phase}] {self.text[:50]}'


class DPIARisk(models.Model):
    """One row of the DPIA form's Section 8 risk table."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dpia = models.ForeignKey(DPIA, on_delete=models.CASCADE, related_name='risks')
    order = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=200)
    what_could_go_wrong = models.TextField(blank=True, default='')
    controls = models.TextField(blank=True, default='')
    likelihood = models.PositiveSmallIntegerField(default=1)   # 1–5
    impact = models.PositiveSmallIntegerField(default=1)       # 1–5
    mitigation = models.TextField(blank=True, default='')
    residual_score = models.PositiveSmallIntegerField(null=True, blank=True)
    residual_band = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        ordering = ['order']

    @property
    def score(self):
        return (self.likelihood or 0) * (self.impact or 0)

    def __str__(self):
        return self.title[:60]


# -- AML/CFT + market-conduct registers (CFO 2026-09-09) ---------------------
# Kept in their own module for readability; imported here so Django registers
# them with the iso_compliance app.
from .aml_models import (                                          # noqa: E402,F401
    AMLTrainingRecord, ComplianceReport, CustomerComplaint, RegulatoryBreach,
    SanctionsScreening, SuspiciousTransactionReport,
)
