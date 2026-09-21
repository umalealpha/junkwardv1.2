"""
recruitment/models.py — native Omni Applicant Tracking (CFO 2026-07-11).

Replaces the idea of bolting on an external HRMS (AuraHR / TalentMatch — both
send CVs to OpenAI/Gemini, against AD-POL-AI-GOV-001). Here the candidate's CV
and personal details stay in Omni; CV↔job matching is done LOCALLY (deterministic
skills overlap, recruitment.matching) so nothing personal ever leaves.

Pipeline: a JobRequisition is the vacancy; a Candidate is a person + their CV;
an Application links the two, carries the match score, and moves through stages.
"""
from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class JobRequisition(models.Model):
    """A vacancy. HR writes the job description + the skills the role needs;
    the match engine scores candidates against required_skills."""
    STATUS = [
        ("draft", "Draft"),
        ("open", "Open — accepting candidates"),
        ("filled", "Filled"),
        ("closed", "Closed"),
    ]
    EMPLOYMENT = [
        ("permanent", "Permanent"),
        ("fixed_term", "Fixed term"),
        ("casual", "Casual"),
        ("intern", "Intern"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=120)
    department = models.CharField(max_length=80, blank=True, default="")
    company_id = models.UUIDField(null=True, blank=True)   # soft entity link
    employment_type = models.CharField(max_length=12, choices=EMPLOYMENT, default="permanent")
    headcount = models.PositiveSmallIntegerField(default=1)
    jd_text = models.TextField(blank=True, default="")
    required_skills = models.JSONField(default=list, blank=True)   # list[str]
    status = models.CharField(max_length=10, choices=STATUS, default="open")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="requisitions_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Job Requisition"

    def __str__(self):
        return f"{self.title} ({self.department})".strip()


class Candidate(models.Model):
    """A person who applied — PII (name/email/phone/CV file) lives HERE and is
    NEVER sent to any external AI. Only the skills detected from the CV are used
    for matching, and even those stay local (recruitment.matching)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    cv = models.FileField(upload_to="recruitment/cvs/", null=True, blank=True)
    cv_text = models.TextField(blank=True, default="")   # extracted locally
    skills = models.JSONField(default=list, blank=True)   # detected list[str]
    source = models.CharField(max_length=40, blank=True, default="")   # advert/referral/direct
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        # One identity per email (Manus QC 2026-08-27): the same person applying
        # to two vacancies used to spawn two Candidate rows. Case-insensitive so
        # Ann@x.com == ann@x.com; blank emails are exempt (walk-ins / referrals
        # captured without one). save() normalises before this ever fires.
        constraints = [
            models.UniqueConstraint(
                Lower("email"), name="uniq_candidate_email_ci",
                condition=~models.Q(email="")),
        ]

    def save(self, *args, **kwargs):
        # Normalise so uniqueness and lookups agree ("  Ann@X.com " -> "ann@x.com").
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


class Application(models.Model):
    """A candidate against a requisition — carries the match score + stage."""
    STAGE = [
        ("applied", "Applied"),
        ("screening", "Screening"),
        ("interview_1", "First interview"),
        ("interview_2", "Second interview"),
        ("offer", "Offer"),
        ("hired", "Hired"),
        ("rejected", "Rejected"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requisition = models.ForeignKey(JobRequisition, on_delete=models.CASCADE, related_name="applications")
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="applications")
    stage = models.CharField(max_length=12, choices=STAGE, default="applied", db_index=True)

    # Which stage moves are legal (Manus QC 2026-08-26, P1: the API accepted any
    # stage, so Applied -> Hired in one call skipped screening, interviews, offer
    # AND the Authority to Recruit). Forward one step, or reject/withdraw from
    # anywhere live. Backwards is allowed one step so a mis-click is correctable.
    # 'hired' additionally requires an APPROVED Authority to Recruit — enforced
    # in the view, since it needs a DB lookup.
    ALLOWED_STAGE_MOVES = {
        "applied":     {"screening", "interview_1", "rejected"},
        "screening":   {"interview_1", "applied", "rejected"},
        "interview_1": {"interview_2", "offer", "screening", "rejected"},
        "interview_2": {"offer", "interview_1", "rejected"},
        "offer":       {"hired", "interview_2", "rejected"},
        "hired":       set(),        # terminal — reverse it in the admin, with a trail
        "rejected":    {"applied", "screening"},   # re-open a declined candidate
    }
    match_score = models.PositiveSmallIntegerField(default=0)   # 0..100 (deterministic skills)
    matched_skills = models.JSONField(default=list, blank=True)
    missing_skills = models.JSONField(default=list, blank=True)
    # AI assessment (identity-stripped CV → reasoning model). Nullable until run.
    ai_analysed = models.BooleanField(default=False)
    ai_score = models.PositiveSmallIntegerField(null=True, blank=True)   # AI fit 0..100
    ai_summary = models.TextField(blank=True, default="")
    ai_strengths = models.JSONField(default=list, blank=True)
    ai_gaps = models.JSONField(default=list, blank=True)
    ai_questions = models.JSONField(default=list, blank=True)
    human_reviewed = models.BooleanField(default=False)   # blueprint: no auto-reject without a human
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-match_score", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["requisition", "candidate"], name="uniq_req_candidate"),
        ]

    def __str__(self):
        return f"{self.candidate.full_name} → {self.requisition.title} ({self.match_score}%)"


# ── Position tiers + who signs each (Unami Hiring-SOP, CFO 2026-09-02) ───────
# omni used to make the SAME five people sign EVERY Authority to Recruit,
# regardless of how junior or senior the role was. Unami's Hiring SOP (Oprah
# concurred) instead sets FIVE seniority tiers, each with its OWN set of
# signatories. This is the single source of truth for both — the standard
# chain per tier, and the extra approver(s) needed when a basic-salary offer
# breaks the tier's ceiling.
#
# CFO decisions 2026-09-02: the COO is NOT on any recruitment chain any more
# (Unami's model has no COO); the Board Chair signs C-suite hires by a secure
# email link (they have no omni login) and their address is read from settings.

# slug → (label, fixed email). 'hiring_manager' is resolved per-authority (it
# varies by role), so it is NOT here — see AuthorityToRecruit._resolve_slug.
SIGNATORY_DIRECTORY = {
    'ceo':             ('Chief Executive Officer',         'aiyer@alphadirect.co.bw'),
    'coo':             ('Chief Operating Officer',         'arjuniyer@alphadirect.co.bw'),
    'human_capital':   ('Human Capital Manager',           'ubutale@alphadirect.co.bw'),
    'hr_bp':           ('Human Resource Business Partner',  'dikgopoleng@alphadirect.co.bw'),
    'finance_manager': ('Finance Manager',                 'ktshutlhedi@alphadirect.co.bw'),
    'cfo':             ('Chief Financial Officer',          'pganesharajah@alphadirect.co.bw'),
    'board_chair':     ('Board Chairperson',               ''),   # from settings.RECRUIT_BOARD_CHAIR
}

TIER_NAMES = {
    1: 'Junior Associate',
    2: 'Senior Associate / Specialist / Team Lead',
    3: 'Controller',
    4: 'Manager & Senior Manager',
    5: 'C-suite',
}

# The standard signatories per tier, in signing order (signing is not sequential,
# the order is only for display). Straight from Unami's SOP.
TIER_STANDARD_CHAINS = {
    1: ['hr_bp', 'hiring_manager', 'human_capital', 'finance_manager', 'cfo'],
    2: ['hr_bp', 'hiring_manager', 'human_capital', 'finance_manager', 'cfo'],
    3: ['human_capital', 'hiring_manager', 'ceo', 'cfo'],
    4: ['human_capital', 'hiring_manager', 'ceo', 'cfo'],
    5: ['human_capital', 'ceo', 'cfo', 'board_chair'],
}

# Who must sign off when the proposed BASIC salary is above the tier ceiling.
# For tiers 1-4 these are already in the standard chain, so an over-band offer
# needs a written reason and their normal signature IS the exception approval.
TIER_EXCEPTION_SIGNERS = {
    1: ['human_capital', 'cfo'],
    2: ['human_capital', 'cfo'],
    3: ['human_capital', 'cfo'],
    4: ['human_capital', 'cfo'],
    5: ['ceo'],
}


class PositionTier(models.Model):
    """One of the five seniority tiers, with its basic-salary band.

    The band is BASIC salary only (allowances/benefits are out of scope, CFO
    2026-09-02). min/max are nullable so Unami can seed the rows first and fill
    the numbers later — nothing enforces a band until a max is set."""
    tier = models.PositiveSmallIntegerField(unique=True)          # 1..5
    name = models.CharField(max_length=80)
    basic_salary_min = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True)
    basic_salary_max = models.DecimalField(max_digits=14, decimal_places=2,
                                           null=True, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tier']
        verbose_name = 'Position Tier'
        verbose_name_plural = 'Position Tiers'

    def __str__(self):
        return f'Tier {self.tier} — {self.name}'

    def standard_slugs(self) -> list:
        return list(TIER_STANDARD_CHAINS.get(self.tier, []))

    def exception_slugs(self) -> list:
        return list(TIER_EXCEPTION_SIGNERS.get(self.tier, []))


class JobTitleTier(models.Model):
    """Tags a job title (free text on payroll.Employee) to a tier — so every
    current role can be mapped once and every person holding that title inherits
    the tier. 'Financial Controller' → tier 3 is the seed anchor (CFO 2026-09-02)."""
    title = models.CharField(max_length=120)
    tier = models.ForeignKey(PositionTier, on_delete=models.PROTECT, related_name='titles')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['title']
        # One tier per distinct title, case-insensitively ('Financial Controller'
        # and 'financial controller' are the same role).
        constraints = [
            models.UniqueConstraint(Lower('title'), name='uniq_jobtitletier_title_ci'),
        ]

    def __str__(self):
        return f'{self.title} → Tier {self.tier.tier}'


# ── Authority to Recruit (CFO 2026-08-03) ───────────────────────────────────
# The formal instrument that authorises filling a role at a stated package,
# signed off by the CEO, COO, Human Capital (x2) and the CFO. It sits BEFORE
# the offer: HR proposes the grade + package, the five sign, and only then does
# an offer go out. Visible ONLY to those five (recruitment.authority_access) —
# it carries a named individual's full pay structure.
#
# Two kinds, deliberately distinguished: an EXTERNAL hire is a recruit; moving
# an existing employee to a new grade is NOT recruitment, it is a regrade, and
# calling it "Authority to Recruit" hides a promotion inside a hiring approval.
# Both need the same five signatures, so they share the instrument but never
# the label (CFO 2026-08-03: Bobby Mothibi is a hire, Kago Tshutlhedi is not).
class AuthorityToRecruit(models.Model):
    class Kind(models.TextChoices):
        RECRUIT = 'recruit', 'Authority to Recruit — external appointment'
        REGRADE = 'regrade', 'Authority to Regrade — existing employee'

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        PENDING   = 'pending',   'Pending signatures'
        APPROVED  = 'approved',  'Approved — all signatories signed'
        DECLINED  = 'declined',  'Declined'
        WITHDRAWN = 'withdrawn', 'Withdrawn'

    # The five signatories, in signing order. Each entry is (slug, label, email).
    # Held here rather than in a view so the document, the API and the tests all
    # read one list — a signatory added in only one of the three is a hole.
    SIGNATORIES = (
        ('ceo',           'Chief Executive Officer',       'aiyer@alphadirect.co.bw'),
        ('coo',           'Chief Operating Officer',       'arjuniyer@alphadirect.co.bw'),
        ('human_capital', 'Human Capital Manager',         'ubutale@alphadirect.co.bw'),
        ('hr_bp',         'Human Resource Business Partner', 'dikgopoleng@alphadirect.co.bw'),
        ('cfo',           'Chief Financial Officer',       'pganesharajah@alphadirect.co.bw'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference   = models.CharField(max_length=32, unique=True, blank=True)
    kind        = models.CharField(max_length=8, choices=Kind.choices, default=Kind.RECRUIT, db_index=True)
    requisition = models.ForeignKey(JobRequisition, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='authorities')

    # The person. `employee` is set for a regrade (they already have a login);
    # a recruit has a name only until they are hired.
    person_name = models.CharField(max_length=120)
    employee    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='recruit_authorities')
    # The application this authority hires FROM (a recruit). Nullable — an
    # authority can be raised before an application exists, and a regrade has
    # none. Set it so "convert to employee" knows which candidate to hire.
    application = models.ForeignKey('recruitment.Application', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='authorities')

    entity      = models.CharField(max_length=120, default='Alpha Direct Insurance Company')
    position    = models.CharField(max_length=120)
    department  = models.CharField(max_length=120, blank=True, default='')
    level       = models.CharField(max_length=24, blank=True, default='')
    employment_type = models.CharField(max_length=12, choices=JobRequisition.EMPLOYMENT,
                                       default='permanent')
    headcount   = models.PositiveSmallIntegerField(default=1)
    effective_date = models.DateField(null=True, blank=True)
    justification  = models.TextField(blank=True, default='')

    # The grade structure exactly as HR built it: one row per item, so the
    # signed document shows what the workbook showed and can be reconciled
    # line by line. [{sn, item, monthly, annual, note}]
    salary_lines   = models.JSONField(default=list, blank=True)
    currency       = models.CharField(max_length=3, default='BWP')
    # What HR quoted as the package ("Total Package / CTC" on the workbook).
    quoted_ctc_monthly = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    quoted_ctc_annual  = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    status      = models.CharField(max_length=10, choices=Status.choices,
                                   default=Status.PENDING, db_index=True)
    # {slug: {"decision": "approved"|"declined", "by": "<name>", "at": "<iso>", "notes": "..."}}
    approvals   = models.JSONField(default=dict, blank=True)
    created_by  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='authorities_created')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    # Conversion outcome (H3, CFO 2026-08-26): one click, only once all five have
    # signed, creates the payroll Employee + starts onboarding. IT still makes the
    # login separately. converted_employee makes the action idempotent.
    converted_employee = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='hired_from_authorities')
    converted_at = models.DateTimeField(null=True, blank=True)

    # ── Position tier + salary band (Unami Hiring-SOP, CFO 2026-09-02) ───────
    # `tier` chooses the signatory chain AND the basic-salary band to check
    # against. Nullable: authorities raised before tiers existed keep the legacy
    # fixed five (see signatory_chain). `proposed_basic_salary` is monthly BASIC
    # only — the figure the band ceiling is tested against, kept apart from the
    # full CTC in quoted_ctc_*. Hiring manager varies per role, so it is captured
    # on the authority (name for the document, email to route the signature).
    tier = models.ForeignKey('recruitment.PositionTier', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='authorities')
    proposed_basic_salary = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    hiring_manager_name  = models.CharField(max_length=120, blank=True, default='')
    hiring_manager_email = models.EmailField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Authority to Recruit'
        verbose_name_plural = 'Authorities to Recruit'

    def __str__(self):
        return f'{self.reference or "ATR"} — {self.person_name} ({self.position})'

    # ── cost of employment ──────────────────────────────────────────────────
    # The workbook's own "Total Package" understated the cost: it netted the
    # EMPLOYEE's provident-fund contribution off the company cost and left the
    # leave-pay accrual out of the monthly column while the annual column
    # included it. The employee's contribution comes out of a base salary that
    # is already counted, so it is not a separate company cost and must never
    # be deducted; leave pay is an accrual inside base, not an extra cost.
    # We therefore keep HR's quoted figure (it is what the candidate was told)
    # AND compute the real one, so a signatory sees both.
    EMPLOYEE_BORNE = ('employee contribution',)
    ACCRUAL_ITEMS  = ('leave pay',)

    @staticmethod
    def _num(v):
        from decimal import Decimal, InvalidOperation
        try:
            return Decimal(str(v or 0))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal('0')

    def _is_excluded(self, line) -> bool:
        blob = f"{line.get('item', '')} {line.get('note', '')}".lower()
        if any(k in blob for k in self.EMPLOYEE_BORNE):
            return True
        return any(k in (line.get('item', '') or '').lower() for k in self.ACCRUAL_ITEMS)

    def cost_to_company(self):
        """(monthly, annual) true employer cost from the structure lines.

        Excludes the employee's own contributions and the leave-pay accrual,
        and ignores any row the workbook labelled TOTAL (we re-add ourselves).
        """
        from decimal import Decimal
        m = a = Decimal('0')
        for ln in (self.salary_lines or []):
            item = (ln.get('item') or '').strip().lower()
            if not item or item.startswith('total'):
                continue
            if self._is_excluded(ln):
                continue
            m += self._num(ln.get('monthly'))
            a += self._num(ln.get('annual'))
        return (m.quantize(Decimal('0.01')), a.quantize(Decimal('0.01')))

    def variance_to_quote(self):
        """(monthly, annual) by which the real cost exceeds what HR quoted."""
        m, a = self.cost_to_company()
        return (m - self._num(self.quoted_ctc_monthly),
                a - self._num(self.quoted_ctc_annual))

    # ── signatures ──────────────────────────────────────────────────────────
    def _resolve_slug(self, slug: str) -> tuple:
        """(slug, label, email) for one chain slug ON THIS authority.

        `hiring_manager` is the person captured on the authority; `board_chair`
        is read from settings (they have no omni login); everything else is a
        fixed role from SIGNATORY_DIRECTORY."""
        if slug == 'hiring_manager':
            name = (self.hiring_manager_name or '').strip()
            label = f'Hiring Manager ({name})' if name else 'Hiring Manager'
            return ('hiring_manager', label, (self.hiring_manager_email or '').strip().lower())
        label, email = SIGNATORY_DIRECTORY.get(slug, (slug, ''))
        if slug == 'board_chair':
            from django.conf import settings
            cfg = getattr(settings, 'RECRUIT_BOARD_CHAIR', {}) or {}
            email = (cfg.get('email') or email or '')
            name = (cfg.get('name') or '').strip()
            if name:
                label = f'Board Chairperson ({name})'
        return (slug, label, (email or '').strip().lower())

    def signatory_chain(self) -> list:
        """Ordered [(slug, label, email)] of who must sign THIS authority.

        Tier-driven when a tier is set (Unami's SOP); the legacy fixed five
        otherwise, so authorities raised before tiers existed are unaffected."""
        if self.tier_id and self.tier and self.tier.tier in TIER_STANDARD_CHAINS:
            return [self._resolve_slug(s) for s in TIER_STANDARD_CHAINS[self.tier.tier]]
        return [(slug, label, (addr or '').strip().lower())
                for slug, label, addr in self.SIGNATORIES]

    def signatory_for(self, user) -> str:
        """Which slug (if any) this user occupies on THIS authority — matched by
        email, so a per-role person (hiring manager) resolves correctly."""
        email = (getattr(user, 'email', '') or '').strip().lower()
        if not email:
            return ''
        for slug, _label, addr in self.signatory_chain():
            if addr and email == addr:
                return slug
        return ''

    def outstanding_signatories(self) -> list:
        done = {k for k, v in (self.approvals or {}).items()
                if (v or {}).get('decision') == 'approved'}
        return [slug for slug, _l, _e in self.signatory_chain() if slug not in done]

    # ── basic-salary band / over-ceiling exception (CFO 2026-09-02) ──────────
    def tier_ceiling(self):
        """The tier's basic-salary ceiling, or None if no tier/ceiling set."""
        if self.tier_id and self.tier and self.tier.basic_salary_max is not None:
            return self.tier.basic_salary_max
        return None

    def is_salary_exception(self) -> bool:
        """True when the proposed BASIC salary is ABOVE the tier ceiling.
        Below the floor is deliberately NOT flagged (CFO 2026-09-02)."""
        ceiling = self.tier_ceiling()
        if ceiling is None:
            return False
        return self._num(self.proposed_basic_salary) > ceiling

    def exception_signers(self) -> list:
        """[(slug, label, email)] who must approve an over-ceiling offer."""
        if not (self.tier_id and self.tier):
            return []
        return [self._resolve_slug(s) for s in self.tier.exception_slugs()]

    def validate_offer(self):
        """Raise ValidationError if the offer breaks the band rule without a
        documented justification. An over-ceiling offer may proceed ONLY with a
        written reason (the exception approvers then sign it in the normal chain)."""
        from django.core.exceptions import ValidationError
        if self.is_salary_exception() and not (self.justification or '').strip():
            cur = self.currency or 'BWP'
            ceiling = self.tier_ceiling()
            who = ', '.join(l for _s, l, _e in self.exception_signers()) or 'the exception approvers'
            raise ValidationError(
                f'The proposed basic salary ({cur} {self._num(self.proposed_basic_salary):,.2f}) '
                f'is above the Tier {self.tier.tier} ceiling ({cur} {ceiling:,.2f}). '
                f'A written justification is required, and the exception must be approved by {who}.')

    def recompute_status(self):
        approvals = self.approvals or {}
        if any((v or {}).get('decision') == 'declined' for v in approvals.values()):
            self.status = self.Status.DECLINED
        elif not self.outstanding_signatories():
            self.status = self.Status.APPROVED
        elif self.status not in (self.Status.WITHDRAWN, self.Status.DRAFT):
            self.status = self.Status.PENDING
        return self.status

    def save(self, *args, **kwargs):
        if not self.reference:
            prefix = 'ATR' if self.kind == self.Kind.RECRUIT else 'ARG'
            year = (self.effective_date or timezone.localdate()).year
            n = AuthorityToRecruit.objects.filter(reference__startswith=f'{prefix}-{year}-').count() + 1
            self.reference = f'{prefix}-{year}-{n:04d}'
        super().save(*args, **kwargs)


class InterviewScorecard(models.Model):
    """One interviewer's assessment of a candidate for one interview round.

    Kept per (application, interviewer, round) so a panel each file their own —
    HR sees the full set, never an averaged black box. Internal only: the
    candidate's public status page (recruitment.candidate_status) NEVER shows a
    scorecard, a score or a note.
    """
    ROUND = [
        ("screening", "Screening"),
        ("interview_1", "First interview"),
        ("interview_2", "Second interview"),
    ]
    RECOMMENDATION = [
        ("strong_yes", "Strong yes"),
        ("yes", "Yes"),
        ("maybe", "Maybe / on the fence"),
        ("no", "No"),
        ("strong_no", "Strong no"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, on_delete=models.CASCADE,
                                   related_name="scorecards")
    interviewer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="interview_scorecards")
    interviewer_name = models.CharField(max_length=120, blank=True, default="")   # snapshot
    round = models.CharField(max_length=12, choices=ROUND, default="interview_1")
    score = models.PositiveSmallIntegerField(default=0)   # overall 0..100
    recommendation = models.CharField(max_length=10, choices=RECOMMENDATION, default="maybe")
    strengths = models.TextField(blank=True, default="")
    concerns = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["application", "interviewer", "round"],
                                    name="uniq_scorecard_per_interviewer_round"),
        ]

    def __str__(self):
        return f"{self.interviewer_name or 'interviewer'} → {self.application_id} ({self.round})"
