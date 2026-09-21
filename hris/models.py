"""
hris/models.py

Alpha Direct HRIS — Human Resource Information System.

CFO-mandated 2026-05-10. The HRIS sits ON TOP of the existing payroll
Employee model and adds talent-management dimensions: pay grades, OKRs,
performance ratings, competencies, leave management, merit projections.

Scale target: 300-400 employees across the Alpha Direct group of companies
(ADIC, ADIIC, ADIH, ADIL, and any future entities).

Design principles
-----------------
- Payroll Employee remains the master record (bank, Omang, hire date, status).
- HRIS extends it with talent fields — kept in HRISProfile linked 1:1 so
  payroll-only staff can exist without HRIS data and vice versa.
- Every entity is auditable (AuditableMixin) — NBFIRA / DPA traceability.
- Multi-company aware throughout (Company FK on every record).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.date_format import format_dt
from core.models import AuditableMixin, BaseModel, Company


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Pay Grades — taken from the TMS Orbit "GRADES" structure
# ---------------------------------------------------------------------------

class Grade(AuditableMixin, BaseModel):
    """A pay grade with midpoint and spread.

    Example: M-4 'Chief Officers' level 8, spread 60%, midpoint 63,750.
    Salary range = midpoint × (1 ± spread/200) — so spread=60 gives ±30%.
    """

    code      = models.CharField(max_length=12, unique=True)
    name      = models.CharField(max_length=80)
    level     = models.PositiveSmallIntegerField(
                    help_text='Hierarchy level 1-8 (1=junior, 8=chief officer).',
                )
    spread    = models.PositiveSmallIntegerField(
                    default=40,
                    help_text='Range spread in percent — salary band width.',
                )
    midpoint  = models.DecimalField(
                    max_digits=12, decimal_places=2,
                    help_text='Midpoint monthly salary in BWP.',
                )
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering            = ['-level', 'code']
        verbose_name        = 'Pay Grade'
        verbose_name_plural = 'Pay Grades'

    def __str__(self):
        return f"{self.code} — {self.name} (P {self.midpoint:,.0f})"

    @property
    def band_min(self):
        return self.midpoint * (Decimal('100') - Decimal(self.spread) / 2) / Decimal('100')

    @property
    def band_max(self):
        return self.midpoint * (Decimal('100') + Decimal(self.spread) / 2) / Decimal('100')


# ---------------------------------------------------------------------------
# HRIS Profile — extends payroll.Employee with talent dimensions
# ---------------------------------------------------------------------------

class HRISProfile(AuditableMixin, BaseModel):
    """One-to-one talent-management extension of payroll.Employee."""

    class Gender(models.TextChoices):
        FEMALE      = 'F', 'Female'
        MALE        = 'M', 'Male'
        OTHER       = 'O', 'Other / prefer not to say'

    class TalentSegment(models.TextChoices):
        STAR              = 'star',              'Star (high performance, high potential)'
        HIGH_POTENTIAL    = 'high_potential',    'High potential'
        SOLID_PERFORMER   = 'solid_performer',   'Solid performer'
        SPECIALIST        = 'specialist',        'Technical specialist'
        DEVELOPING        = 'developing',        'Developing'
        UNDERPERFORMING   = 'underperforming',   'Underperforming'
        NEW_HIRE          = 'new_hire',          'New hire (under 6 months)'

    employee   = models.OneToOneField(
                     'payroll.Employee', on_delete=models.CASCADE,
                     related_name='hris_profile',
                 )
    grade      = models.ForeignKey(
                     Grade, null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='employees',
                 )
    manager    = models.ForeignKey(
                     'payroll.Employee', null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='direct_reports',
                 )
    default_leave_approver = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='default_leave_approvals',
                     help_text='Preferred leave reviewer for this employee. Falls back to the line manager.',
                 )
    # Co-review / dotted line (CFO 2026-07-26, after Kago asked why the senior
    # accountants were not on his team). The senior accountants report to the CFO,
    # but the Finance Manager works with them daily, so BOTH rate them and the
    # score is split. `manager` stays the real line manager — leave, approvals and
    # the org chart are unaffected. This ONLY adds a second voice on feedback, and
    # gives the CFO a live read on the bench in case the Finance Manager leaves.
    co_manager = models.ForeignKey(
                     'payroll.Employee', null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='co_managed_reports',
                     help_text='Second person who also rates this employee. '
                               'Does NOT approve leave and is not the line manager.',
                 )
    co_manager_weight = models.PositiveSmallIntegerField(
                     default=50,
                     help_text='Co-reviewer share of the combined score, 0-100. '
                               'The line manager gets the remainder. Default 50/50.',
                 )
    location   = models.CharField(max_length=80, blank=True, default='')
    # Contract-reminder groups (HC 19-Sep-2026). Set by HR from its own lists —
    # never inferred from nationality (blank for everyone) or job title.
    is_controller = models.BooleanField(
                     default=False,
                     help_text='NBFIRA controller position — contract reminder 4 months ahead.',
                 )
    is_expatriate = models.BooleanField(
                     default=False,
                     help_text='Expatriate — contract reminder 6 months ahead.',
                 )
    gender     = models.CharField(
                     max_length=1, choices=Gender.choices, blank=True, default='',
                 )
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=80, blank=True, default='')
    # Free-text demographic field for diversity reporting — kept optional &
    # always overridable.
    demographic_marker = models.CharField(
                     max_length=80, blank=True, default='',
                     help_text='Optional self-identified diversity marker. '
                               'Never required.',
                 )
    talent_segment = models.CharField(
                     max_length=20, choices=TalentSegment.choices,
                     blank=True, default='',
                 )
    initials   = models.CharField(max_length=4, blank=True, default='')

    # ── Personal / HR record (Unami / HR request 2026-07-01) ────────────────
    # National ID lives on payroll.Employee.national_id (masked in the API).
    # All fields optional, audited (AuditableMixin), behind the HR-whitelist-
    # gated HRIS module. Disabilities/allergies are special-category data —
    # never required, HR-visible only.
    class MaritalStatus(models.TextChoices):
        SINGLE   = 'single',   'Single'
        MARRIED  = 'married',  'Married'
        DIVORCED = 'divorced', 'Divorced'
        WIDOWED  = 'widowed',  'Widowed'
        OTHER    = 'other',    'Other / prefer not to say'

    marital_status  = models.CharField(
                     max_length=10, choices=MaritalStatus.choices, blank=True, default='',
                 )
    passport_number = models.CharField(max_length=40, blank=True, default='')
    permit_number   = models.CharField(
                     max_length=40, blank=True, default='',
                     help_text='Work / residence permit number (non-citizens).',
                 )
    disabilities    = models.TextField(
                     blank=True, default='', help_text='Optional, HR-only. Never required.',
                 )
    allergies       = models.TextField(
                     blank=True, default='', help_text='Optional, HR-only. Never required.',
                 )
    emergency_contact_name         = models.CharField(max_length=120, blank=True, default='')
    emergency_contact_phone        = models.CharField(max_length=40,  blank=True, default='')
    emergency_contact_relationship = models.CharField(max_length=60,  blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['employee__full_name']
        verbose_name        = 'HRIS Profile'
        verbose_name_plural = 'HRIS Profiles'

    def __str__(self):
        return f"{self.employee.full_name} — {self.grade.code if self.grade else 'no grade'}"


# ---------------------------------------------------------------------------
# Competency / Performance assessment
# ---------------------------------------------------------------------------

class CompetencyArea(BaseModel):
    """A competency rubric — mirrors TMS Orbit's LD / BU / RE / PE / DI codes."""

    code = models.CharField(max_length=4, unique=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True, default='')
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['sort_order', 'code']
        verbose_name = 'Competency Area'
        verbose_name_plural = 'Competency Areas'

    def __str__(self):
        return f"{self.code} — {self.name}"


class PerformanceReview(AuditableMixin, BaseModel):
    """A single performance cycle review for one employee."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        FINALISED = 'finalised', 'Finalised'

    profile     = models.ForeignKey(
                      HRISProfile, on_delete=models.CASCADE,
                      related_name='performance_reviews',
                  )
    period      = models.CharField(max_length=20, help_text='e.g. 2024-H1, FY24')
    overall_rating = models.DecimalField(
                      max_digits=4, decimal_places=2, null=True, blank=True,
                      help_text='Overall rating 1.00 - 5.00',
                  )
    competency_scores = models.JSONField(
                      default=dict, blank=True,
                      help_text='Dict of {competency_code: [score, score, score, score]} '
                                'matching the TMS Orbit comp structure.',
                  )
    values_scores = models.JSONField(
                      default=list, blank=True,
                      help_text='Array of 5 value scores (1-5).',
                  )
    potential_scores = models.JSONField(
                      default=list, blank=True,
                      help_text='Array of 4 potential scores (1-5).',
                  )
    status      = models.CharField(
                      max_length=12, choices=Status.choices, default=Status.DRAFT,
                  )
    review_date = models.DateField(null=True, blank=True)
    reviewer    = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='hris_reviews_done',
                  )
    notes       = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-period', 'profile__employee__full_name']
        unique_together = [('profile', 'period')]
        verbose_name = 'Performance Review'
        verbose_name_plural = 'Performance Reviews'

    def __str__(self):
        return f"{self.profile.employee.full_name} — {self.period} ({self.overall_rating or '—'})"


# ---------------------------------------------------------------------------
# OKR / Objectives
# ---------------------------------------------------------------------------

class OKR(AuditableMixin, BaseModel):
    """One objective for one employee in one period.

    Weighting must sum to 100% across an employee's OKRs in a period — the
    business logic that enforces this lives in services.py.
    """

    # profile is nullable so company/department-level objectives (which belong to
    # no single person) can be tree roots for the OKR alignment tree (CFO
    # 2026-07-21). Individual OKRs still require a profile — enforced in the
    # okr-tree create view, not the DB, so existing rows stay valid.
    profile     = models.ForeignKey(
                      HRISProfile, on_delete=models.CASCADE,
                      related_name='okrs', null=True, blank=True,
                  )

    class Scope(models.TextChoices):
        COMPANY    = 'company',    'Company'
        DEPARTMENT = 'department', 'Department'
        INDIVIDUAL = 'individual', 'Individual'
    # scope + parent drive the alignment tree: company → department → individual.
    scope       = models.CharField(
                      max_length=12, choices=Scope.choices, default=Scope.INDIVIDUAL)
    parent      = models.ForeignKey(
                      'self', null=True, blank=True, on_delete=models.SET_NULL,
                      related_name='children',
                  )
    period      = models.CharField(max_length=20)
    name        = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    weight_pct  = models.DecimalField(
                      max_digits=5, decimal_places=2, default=ZERO,
                      help_text='Weight 0-100. Sum across employee+period must = 100.',
                  )
    target      = models.CharField(max_length=200, blank=True, default='')
    score_h1    = models.DecimalField(
                      max_digits=4, decimal_places=2, null=True, blank=True,
                      help_text='Mid-year score 0-5.',
                  )
    score_h2    = models.DecimalField(
                      max_digits=4, decimal_places=2, null=True, blank=True,
                      help_text='Year-end score 0-5.',
                  )

    class Meta(BaseModel.Meta):
        ordering = ['-period', 'profile__employee__full_name', '-weight_pct']
        verbose_name = 'OKR'
        verbose_name_plural = 'OKRs'

    def __str__(self):
        return f"{self.name} ({self.weight_pct}%)"


# ---------------------------------------------------------------------------
# Leave management
# ---------------------------------------------------------------------------

class LeaveType(BaseModel):
    """Configurable leave categories (Annual, Sick, Compassionate, etc.).

    CoS-2023 §7 rule encoding (added 2026-06-03 from Unami's policy upload):
      - carry_over_cap_days  → §7.2 caps accumulation at this figure
                                (typical: 2 × default_annual_days)
      - max_carry_over_years → §7.2.3 max successive years a balance may roll
      - probation_months     → minimum tenure required to take this leave
      - requires_medical_cert → SICK / MATERNITY need a doctor's note
      - paid_pct             → 100 for full-pay, 50 for MATERNITY day-1, 50 for HOSP_HALF
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    default_annual_days = models.PositiveSmallIntegerField(default=0)
    is_paid    = models.BooleanField(default=True)
    is_active  = models.BooleanField(default=True)

    # ── PAY-009 CoS §7 rule encoding (CFO directive 2026-06-03) ─────────────
    carry_over_cap_days   = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='Max accumulated balance — §7.2 default = 2× annual.',
                            )
    max_carry_over_years  = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='Successive years a balance may roll — §7.2.3 = 3.',
                            )
    probation_months      = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='Months of tenure required before taking this leave.',
                            )
    requires_medical_cert = models.BooleanField(
                                default=False,
                                help_text='True for SICK / MATERNITY (CoS §7.6, §7.8).',
                            )
    paid_pct              = models.PositiveSmallIntegerField(
                                default=100,
                                help_text='Pay percent during this leave — 50 for MATERNITY, 50 for HOSP_HALF.',
                            )

    # ── ELRA 2025 statutory encoding (HRIS blueprint, ref ADI/HC/HRIS/2026) ──
    # Externalises the leave-engine rules so a statutory change is a DATA edit
    # (this table, via admin), not a code deploy. Keyed by country so the
    # group's multi-country entities (BW/RSA/ZA) can differ. These fields are
    # read at runtime by hris.feature_views.get_leave_rules(), which overlays
    # active rows onto the frozen ELRA defaults. All additive with safe
    # defaults — an unseeded row behaves exactly as before.
    class AccrualMethod(models.TextChoices):
        MONTHLY   = 'monthly',   'Accrues monthly'
        FRONTLOAD = 'frontload', 'Full entitlement available up front'

    country_code            = models.CharField(
                                max_length=2, default='BW',
                                help_text='ISO country — statutory rules differ by jurisdiction (BW/RSA/ZA).')
    accrual_method          = models.CharField(
                                max_length=10, choices=AccrualMethod.choices,
                                default=AccrualMethod.FRONTLOAD,
                                help_text="'monthly' accrues 1/12 per month (annual s.219); "
                                          "'frontload' = full entitlement immediately (sick s.220).")
    statutory_min_days      = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='ELRA statutory floor. The company entitlement '
                                          '(default_annual_days) may exceed it.')
    min_mandatory_take_days = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='ELRA s.219: 8 annual days must be taken within 6 months of cycle end.')
    leave_window_weeks      = models.PositiveSmallIntegerField(
                                default=0,
                                help_text='Window within which the leave must be taken — paternity s.227 = 14.')
    blocks_termination      = models.BooleanField(
                                default=False,
                                help_text='ELRA s.224: no termination notice while on this leave (maternity).')
    proof_type              = models.CharField(
                                max_length=40, blank=True, default='',
                                help_text='Document required — e.g. medical_certificate, birth_certificate.')
    statutory_ref           = models.CharField(
                                max_length=40, blank=True, default='',
                                help_text='Governing-law reference shown to staff — e.g. "ELRA s.222".')

    class Meta(BaseModel.Meta):
        ordering = ['name']
        verbose_name = 'Leave Type'
        verbose_name_plural = 'Leave Types'

    def __str__(self):
        return self.name


class Policy(AuditableMixin, BaseModel):
    """Standalone HR / governance policy document indexable by ARIA.

    CoS-2023 covers some of these inline (Confidentiality §3.3.3,
    Discipline §10, Leave §7) but ARIA needs each policy as its own
    record so it can cite "POL-LEAVE-v2023" / "POL-DISCIPLINARY-v2023"
    with a version + approval date when answering "what does our leave
    policy say about Botswana public holidays?". CFO directive 2026-06-03
    (after Unami did not include policies-for-aria/ in the wish-list zip).

    Body field holds the verbatim text extracted from the source. Aria
    pulls it through `aria.tools.fetch_policy(code)`. PDF source URL is
    optional — if absent, the body is the authoritative text.
    """

    code           = models.CharField(max_length=40, unique=True)
    title          = models.CharField(max_length=200)
    version        = models.CharField(max_length=20, default='v2023')
    owner          = models.CharField(max_length=80, default='HR')
    approval_date  = models.DateField(null=True, blank=True)
    review_due     = models.DateField(null=True, blank=True)
    body_md        = models.TextField(
                         blank=True, default='',
                         help_text='Verbatim policy text in markdown. ARIA ingests this.',
                     )
    source_doc     = models.CharField(
                         max_length=200, blank=True, default='',
                         help_text='Filename of source document (e.g. "CoS-2023 §7").',
                     )
    is_active      = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['code']
        verbose_name        = 'Policy'
        verbose_name_plural = 'Policies'

    def __str__(self):
        return f'{self.code} {self.version} — {self.title}'


class PublicHoliday(BaseModel):
    """
    Per-country public holidays. Used by LeaveRequest.compute_days() so a
    leave that spans a public holiday or weekend doesn't burn balance for
    those days. Botswana 2025-26 calendar seeded by data migration.

    Structural audit 2026-05-24: omni previously counted Saturdays, Sundays
    AND public holidays as leave days, over-charging every employee.
    Canonical pattern (OCA hr_holidays_public) keeps this table per-country.
    """

    country_code = models.CharField(max_length=2, default='BW', db_index=True)
    holiday_date = models.DateField(db_index=True)
    name         = models.CharField(max_length=120)
    is_active    = models.BooleanField(default=True)
    # Workforce brief (CFO 2026-07-14): staff work ONLY on "paid" public
    # holidays. Default False = a normal day off (0 required hours). Tag the
    # holidays on which staff are expected to work as a working day → the
    # normal weekday/Saturday required-hours rule then applies.
    is_working_day = models.BooleanField(
                         default=False,
                         help_text='Staff work on this holiday (an UNPAID public holiday) → normal '
                                   'required hours. Unset = PAID day off, no hours required.',
                     )

    class Meta(BaseModel.Meta):
        ordering = ['holiday_date']
        unique_together = [('country_code', 'holiday_date')]
        verbose_name = 'Public Holiday'
        verbose_name_plural = 'Public Holidays'

    def __str__(self):
        return f"{self.holiday_date} — {self.name} ({self.country_code})"


class LeaveRequest(AuditableMixin, BaseModel):
    """One leave request workflow."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        PENDING   = 'pending',   'Pending approval'
        APPROVED  = 'approved',  'Approved'
        REFUSED   = 'refused',   'Refused'
        CANCELLED = 'cancelled', 'Cancelled'

    # Half-day support (Kago Tshutlhedi feature request 2026-07-13). Each
    # boundary date carries its own day-type so reporting stays unambiguous:
    # FULL = whole day, AM = morning half, PM = afternoon half. A half-day
    # deducts 0.5 from the working-day count. Additive with FULL defaults →
    # every existing/legacy request behaves exactly as before.
    class DayType(models.TextChoices):
        FULL = 'full', 'Full day'
        AM   = 'am',   'Half day (AM)'
        PM   = 'pm',   'Half day (PM)'

    profile    = models.ForeignKey(
                     HRISProfile, on_delete=models.CASCADE,
                     related_name='leave_requests',
                 )
    leave_type = models.ForeignKey(
                     LeaveType, on_delete=models.PROTECT, related_name='requests',
                 )
    start_date = models.DateField()
    end_date   = models.DateField()
    # Day-type per boundary (half-day support). Stored per-date rather than a
    # single boolean so a range like "start PM half → end full" is unambiguous.
    start_day_type = models.CharField(
                         max_length=4, choices=DayType.choices, default=DayType.FULL,
                     )
    end_day_type   = models.CharField(
                         max_length=4, choices=DayType.choices, default=DayType.FULL,
                     )
    days       = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    reason     = models.TextField(blank=True, default='')
    status     = models.CharField(
                     max_length=12, choices=Status.choices, default=Status.PENDING,
                 )
    approver   = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='leave_decisions',
                 )
    # CFO directive 2026-07-15: the employee picks WHO should review their
    # request (restricted to genuine people-managers, see hris.feature_views
    # .leave_managers) instead of it always routing to HRISProfile.manager and
    # cc'ing EXCO. Distinct from `approver` above, which records who actually
    # DECIDED — this records who was ASKED. Null = legacy behaviour (falls
    # back to profile.manager, see hris.leave_email.build_leave_email).
    requested_approver = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL, related_name='leave_requests_to_review',
                 )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default='')
    # CFO directive 2026-05-18 — Alpha Direct CoS §7.6.1 requires a
    # medical certificate for every sick-leave application. Stored under
    # MEDIA_ROOT/leave_certs/. Backend rejects sick-leave POSTs without
    # this attachment; other leave types may include it optionally.
    medical_certificate = models.FileField(
        upload_to='leave_certs/', null=True, blank=True,
        help_text='Required for Sick Leave (CoS §7.6.1). Max 5 MB.',
    )

    # ── Fixed reason category (Unami Butale, HR, 2026-07-27) ────────────────
    # HR asked for the leave reason to be a FIXED pick-list, not a mandatory
    # free-text "why": an employee is not obliged to disclose the reason for
    # their leave, and a forced justification is a prejudice/discrimination
    # exposure. The category is a structured, non-incriminating field; the free
    # `reason` above stays but becomes OPTIONAL. Reverses the 2026-07-22 15-word
    # mandate at HR's request (via the CFO). Additive: legacy rows keep '' which
    # renders as "—".
    class ReasonCategory(models.TextChoices):
        PERSONAL     = 'personal',     'Personal / rest'
        FAMILY       = 'family',       'Family responsibility'
        MEDICAL      = 'medical',      'Medical / health'
        BEREAVEMENT  = 'bereavement',  'Bereavement'
        TRAVEL       = 'travel',       'Travel'
        RELIGIOUS    = 'religious',    'Religious / cultural'
        STUDY        = 'study',        'Study / exams'
        OTHER        = 'other',        'Other'
        UNDISCLOSED  = 'undisclosed',  'Prefer not to say'
    reason_category = models.CharField(
        max_length=16, choices=ReasonCategory.choices, blank=True, default='',
        help_text='Fixed reason category (Unami 2026-07-27). Free-text reason is optional.',
    )

    # ── Discretionary leave interrogation (CFO 2026-09-10) ──────────────────
    # Compassionate / study / special are granted at the company's discretion,
    # not earned, and were being used to preserve annual-leave days. For those
    # three types ONLY the applicant answers a compulsory question set (defined
    # in hris.discretionary_leave), writes 50+ words, and signs the
    # acknowledgement below; the CFO then countersigns before the manager can
    # approve. Statutory leave (annual, sick, maternity, paternity) is
    # untouched — `policy_answers` stays {} and `policy_ack` False for it.
    policy_answers = models.JSONField(
        default=dict, blank=True,
        help_text='Answers to the discretionary-leave questions, keyed by question id.',
    )
    policy_ack = models.BooleanField(
        default=False,
        help_text='Applicant ticked "I remain at work until this leave is approved".',
    )

    # ── HR dual-approval / verification stage (Unami Butale, 2026-07-27) ─────
    # A manager approving leave is stage 1. For leave that carries a document to
    # authenticate — sick leave with a medical certificate (LeaveType
    # .requires_medical_cert) — HR reviews the dates logged and authenticates the
    # certificate as a fraud control. This is an OVERLAY on the manager decision,
    # not a second blocker: the leave is already APPROVED once the manager signs;
    # HR then VERIFIES or FLAGS it for follow-up. `not_required` for everything
    # that needs no HR authentication → existing behaviour unchanged.
    class HRReviewState(models.TextChoices):
        NOT_REQUIRED = 'not_required', 'No HR review needed'
        PENDING      = 'pending',      'Awaiting HR verification'
        VERIFIED     = 'verified',     'HR verified'
        FLAGGED      = 'flagged',      'HR flagged'
    hr_review_state = models.CharField(
        max_length=12, choices=HRReviewState.choices,
        default=HRReviewState.NOT_REQUIRED, db_index=True,
    )
    hr_reviewer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='leave_hr_reviews',
    )
    hr_reviewed_at   = models.DateTimeField(null=True, blank=True)
    hr_review_notes  = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-start_date']
        verbose_name = 'Leave Request'
        verbose_name_plural = 'Leave Requests'

    def __str__(self):
        return f"{self.profile.employee.full_name} — {self.leave_type.name} {self.start_date}"

    # ------------------------------------------------------------------
    # Structural audit 2026-05-24 — canonical leave invariants
    # ------------------------------------------------------------------
    def compute_days(self, country_code: str = 'BW') -> Decimal:
        """Working-day leave charge between start_date and end_date inclusive.

        Alpha Direct works Saturday MORNINGS, so each day is worth:
          * Mon–Fri  → 1.0 day
          * Saturday → 0.5 day  (the worked morning only)
          * Sunday / active PublicHoliday → 0.0

        A half-day on a boundary (start_day_type / end_day_type AM or PM) charges
        only the worked half of that day: on a normal weekday AM or PM = 0.5; on a
        Saturday the AM (or a full booking) = 0.5 but the PM = 0.0 (the afternoon
        is never worked). Returns Decimal. (Saturday-as-half: staff Saturday
        half-days were computing to 0 days — CFO directive 2026-08-12.)
        """
        from datetime import timedelta
        if not (self.start_date and self.end_date):
            return ZERO
        if self.end_date < self.start_date:
            return ZERO
        # Maternity is a CALENDAR-day entitlement (CoS §7.8.1 — "84 calendar
        # days"), so it must NOT strip weekends/holidays. Every other leave
        # type counts working days. (Paternity §7.9.1 is working days —
        # correct under the default branch below.) Half-days are a working-day
        # concept only, so maternity ignores the day-type flags.
        code = (self.leave_type.code.lower()
                if self.leave_type_id and self.leave_type else '')
        if code == 'maternity':
            return Decimal((self.end_date - self.start_date).days + 1)
        holidays = set(
            PublicHoliday.objects.filter(
                country_code=country_code,
                is_active=True,
                holiday_date__gte=self.start_date,
                holiday_date__lte=self.end_date,
            ).values_list('holiday_date', flat=True)
        )

        HALF = {self.DayType.AM, self.DayType.PM}
        HALF_DAY = Decimal('0.5')

        def _work_value(day) -> Decimal:
            """Full leave charge for taking the whole of `day` off."""
            if day in holidays:
                return ZERO
            wd = day.weekday()
            if wd == 6:                       # Sunday
                return ZERO
            if wd == 5:                       # Saturday — morning only
                return HALF_DAY
            return Decimal('1')               # Mon–Fri

        def _boundary_value(day, day_type) -> Decimal:
            """Charge for a boundary day, honouring an AM/PM half booking. AM/PM
            takes half a normal day; on a Saturday only the AM is worked, so a
            Saturday PM boundary charges nothing while AM (or full) charges 0.5."""
            base = _work_value(day)
            if day_type not in HALF or base == ZERO:
                return base                   # full day, or a non-working day
            if base == HALF_DAY:              # Saturday
                return HALF_DAY if day_type == self.DayType.AM else ZERO
            return HALF_DAY                   # normal weekday half

        # Single day → one boundary. The half flag may arrive on either side
        # (the UI puts it on start for an AM booking, on start as PM otherwise),
        # so honour whichever boundary carries it.
        if self.start_date == self.end_date:
            day_type = (self.start_day_type if self.start_day_type in HALF
                        else self.end_day_type)
            return _boundary_value(self.start_date, day_type)

        total = ZERO
        d = self.start_date
        while d <= self.end_date:
            if d == self.start_date:
                total += _boundary_value(d, self.start_day_type)
            elif d == self.end_date:
                total += _boundary_value(d, self.end_day_type)
            else:
                total += _work_value(d)
            d += timedelta(days=1)
        return max(total, ZERO)

    def day_breakdown(self) -> str:
        """Human-readable summary for approvers / notifications, e.g.
        '2.5 days (13 Jul PM – 15 Jul)' or '0.5 days (13 Jul AM)'.
        The AM/PM tag only appears on a boundary that is actually a half-day.
        """
        def _fmt(d, day_type):
            if not d:
                return ''
            # %-d is POSIX-only — core.date_format renders it on Windows too.
            label = format_dt(d, '%-d %b')
            tag = {self.DayType.AM: ' AM', self.DayType.PM: ' PM'}.get(day_type, '')
            return f'{label}{tag}'

        # One decimal for a half (0.5, 2.5) but no trailing ".0" for whole days.
        days_str = f'{float(self.days or 0):g}'
        if self.start_date == self.end_date:
            return f'{days_str} days ({_fmt(self.start_date, self.start_day_type)})'
        return (f'{days_str} days ({_fmt(self.start_date, self.start_day_type)} '
                f'– {_fmt(self.end_date, self.end_day_type)})')

    def clean(self):
        super().clean() if hasattr(super(), 'clean') else None
        from django.core.exceptions import ValidationError
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot precede start date.'})
        # Zero-length half-day: a PM-start that coincides with an AM-end on the
        # SAME date describes afternoon→morning of one day — a negative/zero
        # window. Block it with a clear message (Kago req §3).
        if (self.start_date and self.end_date and self.start_date == self.end_date
                and self.start_day_type == self.DayType.PM
                and self.end_day_type == self.DayType.AM):
            raise ValidationError({
                '__all__': ('A PM half-day start cannot share the same date as an '
                            'AM half-day end — that is a zero-length request.'),
            })
        # Overlap detection — reject if another APPROVED/PENDING request for
        # the same profile overlaps this window. Canonical OCA hr_holidays
        # forbids overlapping requests for the same employee.
        if self.profile_id and self.start_date and self.end_date:
            qs = (LeaveRequest.objects
                  .filter(profile_id=self.profile_id,
                          status__in=[self.Status.APPROVED, self.Status.PENDING],
                          start_date__lte=self.end_date,
                          end_date__gte=self.start_date))
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                clash = qs.first()
                raise ValidationError({
                    '__all__': (f"Overlaps with existing {clash.status} "
                                f"request {clash.start_date}→{clash.end_date}."),
                })

    def save(self, *args, **kwargs):
        # Recompute days server-side every save so frontend cannot fake a
        # smaller-than-actual leave value to evade balance limits.
        if self.start_date and self.end_date and self.end_date >= self.start_date:
            self.days = self.compute_days()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Leave opening balances — as-at-date bulk upload (bug 9c0aa7d3)
# ---------------------------------------------------------------------------

class LeaveOpeningBalance(AuditableMixin, BaseModel):
    """A corrected as-at-date opening position for an employee's leave,
    uploaded in bulk by HR (bug 9c0aa7d3 — HR).

    The system otherwise only accrues forward from the year start; this lets HR
    set the true entitlement / opening balance / accrued figures as at a cutoff
    date (e.g. 31 March 2026) for quarter-end readiness + audit. `leave_balances`
    picks the LATEST row with `as_at_date <= today` per (profile, leave_type_code)
    as the baseline and accrues forward from there. ADDITIVE: a profile/type with
    no opening-balance row keeps the legacy year-start accrual unchanged — zero
    regression for everyone not uploaded.
    """
    profile = models.ForeignKey(
        HRISProfile, on_delete=models.CASCADE, related_name='leave_opening_balances')
    # CoS leave code (annual/sick/maternity/paternity/compassionate/study/special)
    # — matches the keys in hris.feature_views.COS_LEAVE_RULES, the balance engine.
    leave_type_code = models.CharField(max_length=20)
    as_at_date = models.DateField(help_text='Date these figures are correct as at.')
    entitlement_days     = models.DecimalField(max_digits=7, decimal_places=3, default=ZERO)
    opening_balance_days = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)
    accrued_days         = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)
    batch       = models.CharField(max_length=60, blank=True, default='')
    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='leave_opening_uploads')

    class Meta(BaseModel.Meta):
        ordering = ['profile__employee__full_name', 'leave_type_code', '-as_at_date']
        verbose_name = 'Leave Opening Balance'
        verbose_name_plural = 'Leave Opening Balances'
        indexes = [models.Index(fields=['profile', 'leave_type_code', 'as_at_date'])]
        constraints = [
            # ONE figure per employee, leave type and as-at date. Without this, a corrective
            # upload added a SECOND row for the same key, the balance query had no tie-break,
            # and the old figure could win — bug c2888ba7, "OMNI accepts the report but does
            # not update the balances", with 63 colliding keys on production. The constraint
            # is what makes the corrective upload race-safe as well: update_or_create can
            # only retry on IntegrityError if there is a constraint to violate.
            models.UniqueConstraint(fields=['profile', 'leave_type_code', 'as_at_date'],
                                    name='hris_uniq_leave_opening_key'),
        ]

    def __str__(self):
        return f"{self.profile.employee.full_name} {self.leave_type_code} as-at {self.as_at_date}"


# ---------------------------------------------------------------------------
# Peer Recognition (Kudos) — CFO directive 2026-05-20 (Manus HRIS audit §5)
# ---------------------------------------------------------------------------

class Recognition(AuditableMixin, BaseModel):
    """
    A peer-to-peer "kudos" tied to one of the Alpha Direct values.
    Senders + receivers are HRISProfile rows. Points feed into the annual
    values-section composite of the performance review.
    """

    class Value(models.TextChoices):
        INTEGRITY  = 'integrity',  'Integrity'
        EXCELLENCE = 'excellence', 'Excellence'
        OWNERSHIP  = 'ownership',  'Ownership'
        TEAMWORK   = 'teamwork',   'Teamwork'
        INNOVATION = 'innovation', 'Innovation'
        CUSTOMER   = 'customer',   'Customer Focus'

    sender    = models.ForeignKey(
                    HRISProfile, on_delete=models.CASCADE,
                    related_name='kudos_sent',
                )
    receiver  = models.ForeignKey(
                    HRISProfile, on_delete=models.CASCADE,
                    related_name='kudos_received',
                )
    value_demonstrated = models.CharField(
                    max_length=20, choices=Value.choices,
                )
    message   = models.CharField(
                    max_length=500,
                    help_text='≤ 500 chars. Shown verbatim on the kudos feed.',
                )
    points    = models.PositiveSmallIntegerField(
                    default=1,
                    help_text='1–5. Higher values flagged for HR review (advisory only).',
                )
    is_public = models.BooleanField(
                    default=True,
                    help_text='Private kudos visible only to receiver + their manager.',
                )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Recognition (Kudos)'
        verbose_name_plural = 'Recognition (Kudos)'

    def __str__(self):
        return (
            f'{self.sender.employee.full_name} → '
            f'{self.receiver.employee.full_name} ({self.value_demonstrated})'
        )


# ---------------------------------------------------------------------------
# Onboarding journey + 30-60-90 manager checklist
# CFO directive 2026-05-20 (Manus HRIS audit Part 4 — wow-factor extensions).
# ---------------------------------------------------------------------------

class OnboardingTask(AuditableMixin, BaseModel):
    """
    One step in a new-hire onboarding journey.

    Spawned automatically when an Employee row is created (see
    hris.signals — out-of-scope here; admin can also create them
    manually). The 30-60-90 manager checklist is just a sub-set of
    these tasks where category in {'check30','check60','check90'}.
    """

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        DONE      = 'done',      'Done'
        SKIPPED   = 'skipped',   'Skipped'

    class Category(models.TextChoices):
        IT          = 'it',         'IT account setup'
        ACCESS      = 'access',     'Building / system access'
        TRAINING    = 'training',   'Training / orientation'
        PAPERWORK   = 'paperwork',  'Paperwork / signatures'
        BUDDY       = 'buddy',      'Buddy / introductions'
        CHECK30     = 'check30',    '30-day manager check-in'
        CHECK60     = 'check60',    '60-day manager check-in'
        CHECK90     = 'check90',    '90-day manager check-in'
        OTHER       = 'other',      'Other'

    employee   = models.ForeignKey(
                     'payroll.Employee', on_delete=models.CASCADE,
                     related_name='onboarding_tasks',
                 )
    owner_user = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL,
                     related_name='owned_onboarding_tasks',
                     help_text='Who needs to action this task.',
                 )
    title      = models.CharField(max_length=160)
    category   = models.CharField(
                     max_length=12, choices=Category.choices, default=Category.OTHER,
                 )
    due_date   = models.DateField(null=True, blank=True)
    status     = models.CharField(
                     max_length=10, choices=Status.choices, default=Status.OPEN,
                 )
    notes      = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['due_date', 'created_at']
        verbose_name = 'Onboarding Task'
        verbose_name_plural = 'Onboarding Tasks'

    def __str__(self):
        return f'{self.employee.full_name} — {self.title} ({self.status})'


# ---------------------------------------------------------------------------
# Re-exported from sibling files. Keeps hris/models.py the single import
# site Django needs but lets new feature blocks live in their own files.
# (CFO directive 2026-05-24 — payroll + HR upgrades pass.)
# ---------------------------------------------------------------------------

from .expense_claim_models import ExpenseClaim                    # noqa: E402,F401
from .alerts_models      import HRISAlert                         # noqa: E402,F401
from .amendment_models   import HRISAmendment                     # noqa: E402,F401
from .incentive_models   import IncentiveLine, IncentiveRequest   # noqa: E402,F401
from .leave_encash_models import (                                # noqa: E402,F401
    LeaveEncashment, LeaveEncashmentPayrollLine,
)
from .leave_reversal_models import LeaveReversal                  # noqa: E402,F401
from .performance_feedback_models import (                        # noqa: E402,F401
    MonthlyCheckIn, PerformanceCheckRating, PerformanceImprovementPlan,
)
from .letter_models       import LetterRequest                    # noqa: E402,F401
from .talent_cockpit_models import DevelopmentDialogue             # noqa: E402,F401
from .okr_models import KeyResult, OKRCheckIn                      # noqa: E402,F401
from .performance_target_models import (                           # noqa: E402,F401
    PerformanceTarget, PerformanceTargetResult,
)
from .manager_return_models import (                               # noqa: E402,F401
    ManagerMonthlyReturn, ReturnStatus, ReviewVerdict,
)
from .weekly_objective_models import (                             # noqa: E402,F401
    Cadence, Direction, WeeklyObjective, WeeklyObjectiveRun,
)
from .co_review_models     import CoRating, RaterRole              # noqa: E402,F401
from .roster_flag_models   import RosterFlag, FlagKind, FlagStatus # noqa: E402,F401
from .pulse_models          import PulseResponse                   # noqa: E402,F401
from .skills_models         import Skill, EmployeeSkill            # noqa: E402,F401
from .feature_adoption_models import FeatureAcceptance             # noqa: E402,F401
from .onboarding_models import OnboardingRequest                    # noqa: E402,F401


class HRDocument(AuditableMixin, BaseModel):
    """HR document vault — onboarding packs, signed agreements, policies, exit
    interviews, disciplinary records. Files live in media/hr_documents/.

    Dorothy 2026-06-26: store the onboarding documents new joiners sign, with
    room for exit-interview / disciplinary docs later (hence the categories).
    HR-gated (the vault holds personal signed agreements = employee PII)."""

    class Category(models.TextChoices):
        ONBOARDING   = 'onboarding',     'Onboarding'
        PREBOARDING  = 'preboarding',    'Pre-boarding pack'
        CV           = 'cv',             'CV / résumé'
        CERTIFICATE  = 'certificate',    'Certificate / qualification'
        POLICY       = 'policy',         'Policy'
        CONTRACT     = 'contract',       'Contract / agreement'
        JOB_DESCRIPTION = 'job_description', 'Job Description'
        RESIGNATION  = 'resignation',    'Resignation'
        EXIT         = 'exit_interview', 'Exit interview'
        DISCIPLINARY = 'disciplinary',   'Disciplinary'
        DEVELOPMENT_DIALOGUE = 'development_dialogue', 'Development Dialogue'
        # HC 19-Sep-2026 staff document checklist
        ID_DOCUMENT      = 'id_document',      'ID / passport'
        BANK_LETTER      = 'bank_letter',      'Bank confirmation letter'
        POLICE_CLEARANCE = 'police_clearance', 'Police clearance'
        NBFIRA_LETTER    = 'nbfira_letter',    'NBFIRA letter (controllers)'
        CONTROLLER_DOCS  = 'controller_docs',  'Controller documents'
        OFFBOARDING_IT   = 'offboarding_it',   'IT offboarding document'
        OFFBOARDING_HC   = 'offboarding_hc',   'HC offboarding document'
        OTHER        = 'other',          'Other'

    title         = models.CharField(max_length=200)
    category      = models.CharField(max_length=20, choices=Category.choices,
                                     default=Category.ONBOARDING, db_index=True)
    file          = models.FileField(upload_to='hr_documents/')
    description   = models.TextField(blank=True, default='')
    # A blank template / policy is company-wide; a signed agreement for one
    # person is personal (PII) — flag it so the UI marks it restricted.
    is_personal   = models.BooleanField(default=False)
    employee      = models.ForeignKey('hris.HRISProfile', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='hr_documents')
    employee_name = models.CharField(max_length=200, blank=True, default='')
    uploaded_by   = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='hr_documents_uploaded')

    class Meta(BaseModel.Meta):
        ordering = ['category', 'title']
        verbose_name = 'HR document'
        verbose_name_plural = 'HR documents'

    def __str__(self):
        return f'{self.get_category_display()} — {self.title}'


from .daily_task_models import HRISDailyTaskRun  # noqa: E402,F401
from .exec_signoff_models import ExecSignoff     # noqa: E402,F401
from .late_notice_models import LateNotice   # noqa: E402,F401
# These two were reachable only through the URLconf (their views import them),
# so they registered as a side effect of system checks. Any command that skips
# checks — which is what call_command() does by default, and how the test runner
# invokes migrate — then saw 49 of 52 hris models and reported the missing three
# as unmigrated drift; `makemigrations hris --skip-checks` would happily write a
# migration DELETING the live disciplinary and transfer tables. Import them here
# so registration never depends on the URLconf being loaded.
from .disciplinary_models import (                # noqa: E402,F401
    DisciplinaryAttachment, DisciplinaryCase,
)
from .transfer_models import EmployeeTransfer     # noqa: E402,F401
from .maternity_override_models import MaternityDateOverride     # noqa: E402,F401


# ---------------------------------------------------------------------------
# Career Tracks — promotion-readiness records (CFO directive 2026-07-13)
# ---------------------------------------------------------------------------

class CareerTrack(AuditableMixin, BaseModel):
    """Who is being developed toward which role, and the concrete business
    milestones that must be achieved before the move is considered.

    CFO directive 2026-07-13 (triggered by M. Tlagae's development-path
    request to HR): the promotion path must live ON the employee record in
    the HRIS, not in email threads. HR maintains it; talent-tier roles view
    it alongside succession / IDP.
    """

    class Status(models.TextChoices):
        ACTIVE   = 'active',   'Active'
        ACHIEVED = 'achieved', 'Achieved'
        ON_HOLD  = 'on_hold',  'On hold'

    employee    = models.ForeignKey(
                      'payroll.Employee', on_delete=models.CASCADE,
                      related_name='career_tracks',
                  )
    target_role = models.CharField(max_length=120)
    status      = models.CharField(
                      max_length=12, choices=Status.choices,
                      default=Status.ACTIVE,
                  )
    context     = models.TextField(
                      blank=True, default='',
                      help_text='Background: current role, achievements to '
                                'date, and why this path was opened.',
                  )
    created_by_name = models.CharField(max_length=120, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Career Track'
        verbose_name_plural = 'Career Tracks'

    def __str__(self):
        return f"{self.employee.full_name} → {self.target_role} ({self.status})"


class CareerMilestone(AuditableMixin, BaseModel):
    """A concrete, checkable condition on a CareerTrack."""

    class Status(models.TextChoices):
        PENDING     = 'pending',     'Pending'
        IN_PROGRESS = 'in_progress', 'In progress'
        DONE        = 'done',        'Done'

    track       = models.ForeignKey(
                      CareerTrack, on_delete=models.CASCADE,
                      related_name='milestones',
                  )
    order       = models.PositiveSmallIntegerField(default=1)
    description = models.TextField()
    status      = models.CharField(
                      max_length=12, choices=Status.choices,
                      default=Status.PENDING,
                  )
    evidence    = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['order', 'created_at']

    def __str__(self):
        return f"[{self.status}] {self.description[:60]}"


# ---------------------------------------------------------------------------
# Workforce Daily Brief + hours justification (CFO 2026-07-14)
# ---------------------------------------------------------------------------

class WorkdayJustification(AuditableMixin, BaseModel):
    """One employee's record for one working day: required vs Time-Doctor-
    tracked hours, and — when short — the employee's justification.

    Retained so month-end can show, per employee, how many hours are justified
    (count as working) vs unjustified (count as non-working). Pure hours + a
    reason; no raw Time Doctor activity/window titles are stored here
    (AD-POL-AI-GOV-001)."""

    class Reason(models.TextChoices):
        NONE            = 'none',            'No shortfall'
        ON_LEAVE        = 'on_leave',        'On leave'
        EXTERNAL_MEETING = 'external_meeting', 'External meeting'
        CLIENT_VISIT    = 'client_visit',    'Client visit'
        OTHER           = 'other',           'Other'

    class Status(models.TextChoices):
        MET         = 'met',         'Met required hours'
        NOT_REQUIRED = 'not_required', 'No hours required (off day)'
        JUSTIFIED   = 'justified',   'Shortfall justified'
        UNJUSTIFIED = 'unjustified', 'Shortfall NOT justified'
        PENDING     = 'pending',     'Awaiting employee response'
        # A self-reported explanation is NOT proof: it parks the day here until
        # a manager approves/rejects it (Fable review 2026-07-14 — before this,
        # "Other" self-cleared the whole shortfall with no sign-off). Approved
        # leave on record still auto-justifies without going through here.
        EXPLAINED   = 'explained',   'Explained — pending manager review'

    profile   = models.ForeignKey(
                    HRISProfile, on_delete=models.CASCADE,
                    related_name='workday_justifications',
                )
    work_date = models.DateField(db_index=True)

    required_hours  = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tracked_hours   = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)
    justified_hours = models.DecimalField(max_digits=6, decimal_places=2, default=ZERO)

    reason        = models.CharField(max_length=20, choices=Reason.choices, default=Reason.NONE)
    status        = models.CharField(max_length=14, choices=Status.choices, default=Status.PENDING)
    justification = models.TextField(blank=True, default='',
                                     help_text='Employee explanation for the shortfall.')

    # External-meeting / visit fields (capped at 1.5h — see workforce.MEETING_CAP_MINUTES).
    meeting_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    location        = models.CharField(max_length=160, blank=True, default='',
                                       help_text='Where the external visit took place.')

    # Auto-created leave link when the employee answers "I was on leave".
    linked_leave = models.ForeignKey(
                       'hris.LeaveRequest', null=True, blank=True,
                       on_delete=models.SET_NULL, related_name='justifications',
                   )
    responded_at = models.DateTimeField(null=True, blank=True)

    # Manager sign-off on a self-reported explanation (status EXPLAINED →
    # JUSTIFIED / UNJUSTIFIED). Approved-leave days never need this.
    reviewed_by  = models.ForeignKey(User, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    review_note  = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-work_date', 'profile__employee__full_name']
        unique_together = [('profile', 'work_date')]
        verbose_name = 'Workday Justification'
        verbose_name_plural = 'Workday Justifications'
        indexes = [models.Index(fields=['work_date', 'status'])]

    def __str__(self):
        return f"{self.profile.employee.full_name} {self.work_date} [{self.status}]"

    @property
    def shortfall(self) -> Decimal:
        gap = (self.required_hours or ZERO) - (self.tracked_hours or ZERO)
        return gap if gap > ZERO else ZERO


class ClientVisit(AuditableMixin, BaseModel):
    """A sales/relationship visit an employee logged to justify time out of
    office. Structured so it can later roll up into a sales pipeline. Client
    identity is sensitive — treat under DPA; do not expose beyond HR/mgmt."""

    profile     = models.ForeignKey(
                      HRISProfile, on_delete=models.CASCADE, related_name='client_visits',
                  )
    justification = models.ForeignKey(
                      WorkdayJustification, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='client_visits',
                  )
    visit_date  = models.DateField(db_index=True)
    client_name = models.CharField(max_length=160)
    reason      = models.TextField(blank=True, default='', help_text='Why did you see the client?')
    outcome     = models.TextField(blank=True, default='', help_text='What happened afterward?')
    amount      = models.DecimalField(
                      max_digits=14, decimal_places=2, null=True, blank=True,
                      help_text='Potential premium (BWP) from this meeting. Optional.',
                  )

    class Meta(BaseModel.Meta):
        ordering = ['-visit_date']
        verbose_name = 'Client Visit'
        verbose_name_plural = 'Client Visits'

    def __str__(self):
        return f"{self.profile.employee.full_name} → {self.client_name} ({self.visit_date})"


class Announcement(BaseModel):
    """Company / HR item surfaced in the daily brief: announcements, HR
    matters, scheduled meetings, disciplinary notices. Audience is either
    everyone, or a single employee (targeted HR/disciplinary matter)."""

    class Category(models.TextChoices):
        ANNOUNCEMENT = 'announcement', 'Company announcement'
        HR_MATTER    = 'hr_matter',    'HR matter'
        MEETING      = 'meeting',      'Meeting / schedule'
        DISCIPLINARY = 'disciplinary', 'Disciplinary'

    category   = models.CharField(max_length=14, choices=Category.choices,
                                  default=Category.ANNOUNCEMENT)
    title      = models.CharField(max_length=160)
    body       = models.TextField(blank=True, default='')
    # Null audience = whole company; set to target one employee (private HR item).
    audience   = models.ForeignKey(
                     HRISProfile, null=True, blank=True,
                     on_delete=models.CASCADE, related_name='announcements',
                 )
    starts_on  = models.DateField(db_index=True)
    ends_on    = models.DateField(null=True, blank=True,
                                  help_text='Last day to show in the brief. Blank = show once from starts_on.')
    is_active  = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-starts_on', 'category']
        verbose_name = 'Announcement'
        verbose_name_plural = 'Announcements'
        indexes = [models.Index(fields=['starts_on', 'is_active'])]

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"


class TrackingDirective(BaseModel):
    """CFO/HR override of whether an employee is EXPECTED to track time
    (CFO 2026-07-14: 'a dashboard where I can just click and say track / don't
    track'). An explicit directive WINS over the keyword/role guess in
    hris.eligibility — both ways: a 'track' directive includes someone a keyword
    would have excluded, a 'don't track' excludes someone otherwise expected.
    One row per employee; absence of a row = fall back to the keyword guess."""

    employee = models.OneToOneField('payroll.Employee', on_delete=models.CASCADE,
                                    related_name='tracking_directive')
    expected_to_track = models.BooleanField(default=True)
    note       = models.CharField(max_length=200, blank=True, default='')
    updated_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        verbose_name = 'Tracking Directive'
        verbose_name_plural = 'Tracking Directives'

    def __str__(self):
        who = getattr(self.employee, 'full_name', self.employee_id)
        return f"{who}: {'tracks' if self.expected_to_track else 'does NOT track'}"


class WorkforceBriefSetting(BaseModel):
    """Singleton on/off switch for the Workforce Daily Brief (CFO 2026-07-14).

    Toggled from the omni Time Doctor page by the CFO / Arun / Arjun only
    (server-enforced in hris.workforce_views). The 01:00 cron reads this — when
    off, no briefs are sent. A DB flag (not just the env var) so it can be
    flipped in-app without a deploy."""

    enabled    = models.BooleanField(default=False)
    updated_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        verbose_name = 'Workforce Brief Setting'
        verbose_name_plural = 'Workforce Brief Setting'

    def __str__(self):
        return f"Workforce brief: {'ON' if self.enabled else 'OFF'}"

    @classmethod
    def solo(cls) -> 'WorkforceBriefSetting':
        obj = cls.objects.order_by('created_at').first()
        return obj or cls.objects.create(enabled=False)

    @classmethod
    def is_enabled(cls) -> bool:
        obj = cls.objects.order_by('created_at').first()
        return bool(obj and obj.enabled)


class LeaveExcuseAutoResponse(AuditableMixin, BaseModel):
    """Idempotency ledger for the Leave Excuse auto-responder (CFO 2026-07-22).

    One row per (employee, work_date, rule) — the unique_together guarantees a
    person is handled at most once per day per rule even if the command re-runs.
    `sent_at` is the real email gate: a row can exist (recorded the verdict) while
    LEAVE_EXCUSE_AUTOSEND is OFF with sent_at NULL — nothing was emailed. When the
    gate is flipped ON, the next run finds the row, sees sent_at is NULL, emails
    once and stamps sent_at, and every subsequent run skips it. So the ledger
    survives a warm-up period yet still sends exactly one email per person/date/
    rule once enabled. See hris/leave_excuse_rules.py for the rule definitions."""

    employee  = models.ForeignKey('payroll.Employee', on_delete=models.CASCADE,
                                  related_name='leave_excuse_autoresponses')
    work_date = models.DateField(db_index=True)
    rule      = models.CharField(max_length=8,
                                 help_text="Which auto-rule fired: 'A' (power cut) "
                                           "or 'B' (tracker / no IT ticket).")
    sent_at   = models.DateTimeField(null=True, blank=True,
                                     help_text='When the email actually went out. '
                                               'NULL = recorded but not sent (autosend OFF).')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Leave Excuse Auto Response'
        verbose_name_plural = 'Leave Excuse Auto Responses'
        ordering            = ['-work_date']
        unique_together     = [('employee', 'work_date', 'rule')]
        indexes             = [models.Index(fields=['work_date', 'rule'],
                                            name='hris_lexc_wd_rule_idx')]

    def __str__(self):
        who = getattr(self.employee, 'full_name', self.employee_id)
        state = 'sent' if self.sent_at else 'recorded'
        return f"{who} {self.work_date} Rule {self.rule} [{state}]"


class ManagerAccountabilityNote(AuditableMixin, BaseModel):
    """Firm, interactive note to a line manager when their reports go 3+ days
    dark on Time Doctor with no leave / client-visit logged. The manager must
    answer via a signed no-login link; silence past the deadline escalates to
    CEO / COO / CFO / HR / Dorothy with the manager's name on it (CFO 2026-07-25).
    One note per manager per day."""

    manager      = models.ForeignKey(
                       'payroll.Employee', on_delete=models.CASCADE,
                       related_name='accountability_notes',
                   )
    for_date     = models.DateField(db_index=True)
    # [{name, last_tracked, days_dark}] — the manager's dark, unexplained reports.
    reports      = models.JSONField(default=list)
    deadline     = models.DateTimeField()
    response     = models.TextField(blank=True, default='')
    responded_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-for_date']
        unique_together = [('manager', 'for_date')]
        verbose_name = 'Manager accountability note'

    def __str__(self):
        who = getattr(self.manager, 'full_name', self.manager_id)
        state = 'answered' if self.responded_at else ('escalated' if self.escalated_at else 'open')
        return f"{who} {self.for_date} ({len(self.reports)} dark) [{state}]"


class SuccessionNominee(AuditableMixin, BaseModel):
    """A CFO/HR-named successor for a critical-role incumbent, overriding the
    auto-suggested downline ranking (CFO 2026-08-30). Named nominees fill the
    top succession slots in rank order; the auto-suggestion fills any remaining
    slots. Lets the CFO lock exact picks (e.g. the Finance Manager for the CFO)
    instead of relying on patchy performance ratings."""
    incumbent = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='succession_nominees_for',
        help_text='The role incumbent this person is named to succeed.')
    nominee = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='succession_nominations',
        help_text='The named successor.')
    rank = models.PositiveSmallIntegerField(
        default=1, help_text='1 = first choice, 2 = second, 3 = third.')

    class Meta(BaseModel.Meta):
        ordering = ['incumbent', 'rank']
        constraints = [
            models.UniqueConstraint(fields=['incumbent', 'nominee'],
                                    name='uniq_succession_nominee'),
        ]

    def __str__(self):
        return f"{self.incumbent_id} -> {self.nominee_id} (#{self.rank})"


from .contract_reminder_models import (    # noqa: E402,F401
    ContractReminderRule, ContractRenewalDecision, ContractReminderLog, HRSetting,
)

from .lifecycle_models import (    # noqa: E402,F401
    OffboardingCase, OffboardingStep, EmployeeAcknowledgement, ProbationDecision,
    OfferLetter, RoleSystemRequirement, LoginClassification,
)

from .training_models import (    # noqa: E402,F401
    TrainingCourse, TrainingSlide, TrainingQuestion, ExamVersion,
    TrainingAssignment, TrainingAttempt, TrainingCertificate,
)
