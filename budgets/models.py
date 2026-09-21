"""
budgets/models.py

Budget management for Alpha Direct Insurance.

Models:
  - Budget          A budget for a specific fiscal period and department
  - BudgetLine      Individual budget line items by GL account
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel
from ledger.models import Account, FiscalPeriod


class Budget(BaseModel):
    """
    A budget for a specific fiscal period (month) and department.
    Budgets can be created per-department or as a master budget.
    """

    class Status(models.TextChoices):
        DRAFT    = 'draft',    'Draft'
        APPROVED = 'approved', 'Approved'
        REVISED  = 'revised',  'Revised'

    class Department(models.TextChoices):
        MASTER       = 'master',       'Master (Company-wide)'
        FINANCE      = 'finance',      'Finance'
        CLAIMS       = 'claims',       'Claims'
        UNDERWRITING = 'underwriting', 'Underwriting'
        HEALTH       = 'health',       'Health'
        BD           = 'bd',           'Business Development'
        OPERATIONS   = 'operations',   'Operations'
        IT           = 'it',           'IT'

    fiscal_period = models.ForeignKey(
        FiscalPeriod, on_delete=models.PROTECT,
        related_name='budgets',
    )
    department = models.CharField(
        max_length=20,
        choices=Department.choices,
        default=Department.MASTER,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    description = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='budgets_created',
    )
    approved_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='budgets_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        unique_together = [('fiscal_period', 'department')]
        ordering = ['fiscal_period__start_date', 'department']

    def __str__(self):
        return f"Budget {self.fiscal_period.period_name} - {self.get_department_display()}"

    @property
    def total_revenue(self):
        return self.lines.filter(
            account__account_type='revenue'
        ).aggregate(t=models.Sum('amount'))['t'] or Decimal('0.00')

    @property
    def total_expenses(self):
        return self.lines.filter(
            account__account_type='expense'
        ).aggregate(t=models.Sum('amount'))['t'] or Decimal('0.00')


class BudgetLine(BaseModel):
    """Individual budget amount for one GL account within a budget."""

    budget = models.ForeignKey(
        Budget, on_delete=models.CASCADE,
        related_name='lines',
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT,
        related_name='budget_lines',
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal('0.00'),
        help_text='Budgeted amount for this account in this period (positive)',
    )
    notes = models.CharField(max_length=500, blank=True, default='')

    class Meta(BaseModel.Meta):
        unique_together = [('budget', 'account')]
        ordering = ['account__code']

    def __str__(self):
        return f"{self.account.code} - {self.amount}"


# ---------------------------------------------------------------------------
# Budget Library — a place to KEEP each year's budget pack (CFO 2026-06-27).
# Distinct from Budget/BudgetLine above (monthly per-account lines for
# budget-vs-actual): this stores the annual board-level pack — headline figures
# (GWP / EBITDA / PAT) plus the source files (the linked Excel model, the
# highlights deck, the reinsurance calculator) — versioned by fiscal year and
# entity, so Finance has one home for FY27, FY28, ... budgets.
# ---------------------------------------------------------------------------

class BudgetPack(BaseModel):
    """One fiscal year's budget pack for an entity (or Group)."""

    class Status(models.TextChoices):
        DRAFT      = 'draft',      'Draft'
        APPROVED   = 'approved',   'Approved (adopted plan)'
        SUPERSEDED = 'superseded', 'Superseded'

    fy_label     = models.CharField(max_length=20, help_text='e.g. "FY2026/27"')
    company      = models.ForeignKey(
        'core.Company', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='budget_packs', help_text='NULL = Group / consolidated.')
    period_start = models.DateField(help_text='Budget year start, e.g. 2026-07-01')
    period_end   = models.DateField(help_text='Budget year end, e.g. 2027-06-30')
    status       = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    scenario     = models.CharField(max_length=60, blank=True, default='',
                                    help_text='e.g. "Base case", "Cost-cut case".')

    # Headline figures (P Mn) — stored verbatim from the pack, NOT recomputed.
    gwp_target    = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ebitda        = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    pat           = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ebitda_margin = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                        help_text='EBITDA margin on NEP (%).')

    notes        = models.TextField(blank=True, default='', help_text='Headline summary / key assumptions.')
    source       = models.CharField(max_length=120, blank=True, default='',
                                    help_text='Who prepared it, e.g. "Kago Tshutlhedi".')
    created_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='budget_packs_created')

    class Meta(BaseModel.Meta):
        unique_together = [('fy_label', 'company', 'scenario')]
        ordering = ['-period_start', 'company__code']
        verbose_name = 'Budget pack'
        verbose_name_plural = 'Budget packs'

    def __str__(self):
        return f"{self.fy_label} — {self.company.code if self.company_id else 'Group'} ({self.status})"


class BudgetPackFile(BaseModel):
    """A file belonging to a budget pack (Excel model, deck, calculator, ...)."""

    class Kind(models.TextChoices):
        MODEL      = 'model',      'Financial model (xlsx)'
        HIGHLIGHTS = 'highlights', 'Highlights (deck / doc)'
        CALCULATOR = 'calculator', 'Calculator / tool'
        OTHER      = 'other',      'Other'

    pack  = models.ForeignKey(BudgetPack, on_delete=models.CASCADE, related_name='files')
    file  = models.FileField(upload_to='budgets/%Y/')
    kind  = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OTHER)
    label = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['kind', 'label']

    def __str__(self):
        return f"{self.pack.fy_label} — {self.label or self.file.name}"


# Statutory training levy — 0.5% of revenue (incl VAT), paid to and recoverable
# from the Botswana Qualifications Authority (BQA) for BQA-accredited trainings
# (CFO directive 2026-07-13, rate corrected to 0.5% — 125M revenue -> 625,000
# recoverable). Kept as a documented RATE; the revenue base is NOT auto-derived
# here (frozen-numbers rule) — pass a figure to training_levy().
TRAINING_LEVY_RATE = Decimal('0.005')  # 0.5%  (125,000,000 x 0.005 = 625,000)


def training_levy(revenue_incl_vat) -> Decimal:
    """The training levy on a supplied revenue figure (incl VAT). Never pulls a
    revenue number itself — caller passes it (frozen-numbers safety)."""
    return (Decimal(str(revenue_incl_vat)) * TRAINING_LEVY_RATE).quantize(Decimal('0.01'))


# Annual training-levy recovery ceiling (BWP) — the most training we can claim
# back from BQA in a year. ~0.5% of ~P125M revenue = 625,000 (CFO 2026-07-13).
# A documented figure, NOT auto-derived from GL revenue (frozen-numbers rule);
# override via settings.TRAINING_LEVY_CEILING_BWP if the revenue base changes.
TRAINING_LEVY_CEILING_BWP = Decimal('625000')


class SpendRequest(BaseModel):
    """A pre-spend approval request — event / entertainment / party / training /
    travel / travel-advance — submitted BEFORE money is committed. The requester
    attaches a budget; DeepSeek reads it and summarises it for the approver
    (budgets/spend_ai.py). NOT linked to GL / PO / payment: this is the written
    pre-approval only; the actual spend still flows through the normal PO /
    payment workflow once approved. CFO directive 2026-07-13."""

    class Type(models.TextChoices):
        EVENT           = 'event',           'Event'
        GOLF_DAY        = 'golf_day',        'Golf day'
        ENTERTAINMENT   = 'entertainment',   'Custom entertainment'
        STAFF_PARTY     = 'staff_party',     'Staff birthday party'
        BROKER_PARTY    = 'broker_party',    'Broker party'
        BROKER_BIRTHDAY = 'broker_birthday', 'Broker birthday'
        TRAINING        = 'training',        'Training'
        TRAVEL          = 'travel',          'Travel'
        TRAVEL_ADVANCE  = 'travel_advance',  'Travel advance'
        OTHER           = 'other',           'Other'

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        APPROVED  = 'approved',  'Approved'
        REJECTED  = 'rejected',  'Rejected'

    class AIStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DONE    = 'done',    'Done'
        SKIPPED = 'skipped', 'Skipped'
        ERROR   = 'error',   'Error'

    class BQARecovery(models.TextChoices):
        # Training levy (0.05% of revenue incl VAT, statutory) is recoverable from
        # the Botswana Qualifications Authority ONLY for BQA-accredited providers.
        # Non-accredited trainings are out-of-pocket. CFO directive 2026-07-13.
        NOT_APPLICABLE  = 'n/a',             'Not applicable'
        RECOVERABLE     = 'recoverable',     'Recoverable from BQA'
        NOT_RECOVERABLE = 'not_recoverable', 'Not recoverable — provider not BQA-accredited'
        CLAIMED         = 'claimed',         'Claim submitted to BQA'
        RECOVERED       = 'recovered',       'Recovered from BQA'

    request_type  = models.CharField(max_length=20, choices=Type.choices, default=Type.EVENT, db_index=True)
    title         = models.CharField(max_length=200)
    description   = models.TextField(blank=True, default='')          # the business case
    event_date    = models.DateField(null=True, blank=True)
    amount        = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'),
                        help_text='Amount requested, BWP.')
    within_budget = models.BooleanField(default=True,
                        help_text='Requester declares this is within the approved budget.')
    budget_note   = models.TextField(blank=True, default='',
                        help_text='Requester note on the budget position (in / out of budget, why).')
    attachment    = models.FileField(upload_to='spend_requests/%Y/%m/', null=True, blank=True,
                        help_text='The budget / ROI / quote document.')

    # Training-specific (only meaningful when request_type == TRAINING). BQA =
    # Botswana Qualifications Authority. The statutory training levy is recoverable
    # from BQA ONLY when the training provider is BQA-accredited; trainings with
    # non-accredited organisations are out-of-pocket (CFO directive 2026-07-13).
    training_provider   = models.CharField(max_length=200, blank=True, default='')
    bqa_accredited      = models.BooleanField(null=True, blank=True,
                            help_text='Is the training provider BQA-accredited? '
                                      'Accredited → the levy is recoverable from BQA.')
    bqa_recovery_status = models.CharField(max_length=16, choices=BQARecovery.choices,
                            default=BQARecovery.NOT_APPLICABLE, db_index=True)

    # DeepSeek's read of the attached budget (budgets/spend_ai.py).
    ai_status          = models.CharField(max_length=10, choices=AIStatus.choices, default=AIStatus.PENDING)
    ai_summary         = models.TextField(blank=True, default='')
    ai_extracted_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ai_flags           = models.TextField(blank=True, default='')

    status         = models.CharField(max_length=10, choices=Status.choices,
                         default=Status.SUBMITTED, db_index=True)
    requester      = models.ForeignKey(User, on_delete=models.PROTECT, related_name='spend_requests')
    company        = models.ForeignKey('core.Company', on_delete=models.SET_NULL, null=True, blank=True,
                         related_name='spend_requests')
    approver       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                         related_name='spend_decisions')
    decided_at     = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default='')

    # Actual-spend loop (#4, CFO 2026-07-13): once the approved request is really
    # spent (via the normal PO/payment flow), finance records what it actually
    # cost + the PO/payment reference, so budget-vs-actual variance is visible.
    actual_spent     = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                          help_text='What was actually spent once the PO/payment went through.')
    actual_reference = models.CharField(max_length=120, blank=True, default='',
                          help_text='PO / payment reference the spend was booked under.')

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['status', 'request_type']),
            models.Index(fields=['requester', 'status']),
        ]

    def __str__(self):
        return f"{self.get_request_type_display()} — {self.title} ({self.amount})"


# ---------------------------------------------------------------------------
# Strategic Plan Library (AD Insurtech 5-Year Plan)
# ---------------------------------------------------------------------------

class PlanPack(BaseModel):
    """One saved version of the AD Insurtech 5-Year Strategic Plan.

    Requested by Finance (Oprah Mogomotsi, 2026-08-04): "build the strategic plan
    library, it should work the same way the budget library and budget cockpit
    work". So this deliberately mirrors :class:`BudgetPack` — same shape, same
    file-attachment pattern, same Finance-only write rule — and lives beside the
    5-Year Plan Cockpit at /budgets/five-year rather than on a route of its own,
    because the CFO wants the plan and its archive in one place.

    ``base_figures`` is the source-of-record snapshot. It stays EMPTY while a pack
    is a draft, and the Library page then shows figures computed live from
    lib/fiveYearModel.ts — the same arithmetic the Cockpit uses, so a draft can
    never quote a number the Cockpit disagrees with. Approving a pack freezes the
    figures into this field, and from then on it is read verbatim and never
    recomputed. That is what makes an approved plan quotable to a board.
    """

    class Status(models.TextChoices):
        DRAFT    = 'draft',    'Draft'
        APPROVED = 'approved', 'Approved (board-adopted)'
        ARCHIVED = 'archived', 'Archived (superseded)'

    class Scenario(models.TextChoices):
        BASE         = 'base',         'Base case'
        CONSERVATIVE = 'conservative', 'Conservative'
        AGGRESSIVE   = 'aggressive',   'Aggressive'

    entity        = models.CharField(max_length=40, default='AD_INSURTECH',
                                     help_text='Matches the Omni entity filter.')
    label         = models.CharField(max_length=120,
                                     help_text='e.g. "FY2026–FY2030 · Base Case"')
    scenario_slug = models.CharField(max_length=20, choices=Scenario.choices,
                                     default=Scenario.BASE)
    status        = models.CharField(max_length=12, choices=Status.choices,
                                     default=Status.DRAFT)

    prepared_by   = models.CharField(max_length=120, blank=True, default='')
    prepared_date = models.DateField(null=True, blank=True)
    department    = models.CharField(max_length=120, blank=True, default='CFO Office')

    # Frozen on approval — see the class docstring. Never recomputed once set.
    base_figures  = models.JSONField(blank=True, default=dict)
    # Key assumption register: [{label, segment, fy26..fy30, source}, ...]
    assumptions   = models.JSONField(blank=True, default=list)
    narrative     = models.TextField(blank=True, default='', max_length=600)

    created_by    = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='plan_packs_created')
    approved_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='plan_packs_approved')
    approved_at   = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        unique_together = [('entity', 'label', 'scenario_slug')]
        ordering = ['status', 'scenario_slug']
        verbose_name = 'Strategic plan pack'
        verbose_name_plural = 'Strategic plan packs'

    def __str__(self):
        return f"{self.label} ({self.get_status_display()})"

    @property
    def figures_locked(self) -> bool:
        """True once the pack is approved and its figures are frozen."""
        return self.status == self.Status.APPROVED and bool(self.base_figures)


class PlanPackFile(BaseModel):
    """A file belonging to a plan pack (workbook, cockpit spec, deck, ...)."""

    class Kind(models.TextChoices):
        WORKBOOK = 'workbook', 'Financial model (xlsx)'
        SPEC     = 'spec',     'Specification / narrative'
        DECK     = 'deck',     'Board deck'
        APP      = 'app',      'Interactive page / app'
        OTHER    = 'other',    'Other'

    pack  = models.ForeignKey(PlanPack, on_delete=models.CASCADE, related_name='files')
    file  = models.FileField(upload_to='strategic-plan/%Y/')
    kind  = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OTHER)
    label = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['kind', 'label']

    def __str__(self):
        return f"{self.pack.label} — {self.label or self.file.name}"
