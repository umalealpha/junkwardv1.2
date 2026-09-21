"""
payroll/models.py

Alpha Direct Insurance payroll module.

Design principles
- Components catalogued separately so the CFO can add / rename / disable
  any line item without code changes (28 default components seeded by
  setup_payroll_components — exactly the headers the CFO uses today).
- PAYE bands are stored in TaxBracket and editable. The system never
  hard-codes BURS rates — when BURS publishes new bands, the CFO updates
  them via the admin and the next payroll run picks them up.
- Payslip totals (Gross, PAYE, Net, CTC) are recomputed from components
  whenever the payslip is saved — never trusted from imports.
- Imports go through PayrollImportBatch with the same dual-authorisation
  pattern as Asset and Recovery imports (CFO + Finance Manager, two
  distinct approvers, never the uploader).
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Company
from core.crypto_fields import EncryptedCharField  # field-level encryption (DPA S-5)
from django.utils import timezone


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------

class Employee(AuditableMixin, BaseModel):
    """Alpha Direct staff member on the payroll register."""

    class Status(models.TextChoices):
        ACTIVE     = 'active',     'Active'
        ON_LEAVE   = 'on_leave',   'On leave'
        SUSPENDED  = 'suspended',  'Suspended'
        TERMINATED = 'terminated', 'Terminated'

    employee_number = models.CharField(max_length=30, unique=True, blank=True)
    full_name       = models.CharField(max_length=200)
    department      = models.CharField(max_length=100, blank=True, default='')
    job_title       = models.CharField(max_length=100, blank=True, default='')
    email           = models.EmailField(blank=True, default='')
    phone           = models.CharField(max_length=50, blank=True, default='')
    national_id     = EncryptedCharField(
                          max_length=255, blank=True, default='',
                          help_text='Omang — ENCRYPTED at rest (DPA S-5). Stored locally; not in '
                                    'API responses beyond the payslip context.',
                      )
    hire_date       = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    # Back-link to the signed Authority to Recruit/Regrade this employee (or
    # their current pay) was seeded from (Prompt 03, Shared Contract v1,
    # 2026-09-12) — a quick reference on the employee record itself, in
    # addition to the existing authority -> employee forward link
    # (AuthorityToRecruit.converted_employee). The authority stays
    # confidential to its signatories; this is just the reference string, not
    # a relation that widens who can see it.
    recruit_authority_ref = models.CharField(max_length=32, blank=True, default='')
    # Highest qualifications / education (free text). Captured on the profile and
    # used by succession planning (CFO / Unami directive 2026-07-23). Not PII-
    # sensitive like Omang, so plain text is fine.
    qualifications  = models.TextField(blank=True, default='')
    company         = models.ForeignKey(
                          Company, on_delete=models.PROTECT,
                          related_name='employees', null=True, blank=True,
                      )
    status          = models.CharField(
                          max_length=15, choices=Status.choices, default=Status.ACTIVE,
                      )
    # Bank
    bank_name       = models.CharField(max_length=100, blank=True, default='')
    bank_account_no = EncryptedCharField(max_length=255, blank=True, default='')  # encrypted at rest (DPA S-5)
    bank_branch     = models.CharField(max_length=100, blank=True, default='')
    # Numeric BW sort/branch code (distinct from the free-text bank_branch name).
    # The bank NAME is derived from this — see payroll.bank_codes.derive_bank_name.
    bank_branch_code = models.CharField(max_length=20, blank=True, default='')
    # Source-system tracking — for the future Odoo / Graphite / HRMS import
    external_ref    = models.CharField(max_length=100, blank=True, default='')
    # Not a person. The QA harness needs real Employee rows to drive the real
    # code paths, so they cannot simply be deleted — but they must never appear on
    # a report a human reads as a list of staff. Oprah Mogomotsi's leave spot-check
    # (2026-08-10) found two of them accruing leave alongside real employees.
    # An explicit flag rather than matching on names like "(automated QA)", which
    # would break the moment somebody renamed one.
    is_test_record  = models.BooleanField(
        default=False, db_index=True,
        help_text='A QA/automation account, not a real employee. Excluded from '
                  'staff reports.')
    # Linked Django auth user — populated by setup_employee_users for staff
    # who need to log into alpha-finance themselves. Null for staff who only
    # appear on the payroll register without a system login.
    user            = models.OneToOneField(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='employee_record',
                      )

    # ── Keep access after exit (CFO 2026-09-18) ────────────────────────────
    # A leaver's Omni login is closed automatically once their last working day
    # has passed (payroll.offboard_access). A few people keep working with us
    # AFTER that date — an external contractor on a consulting agreement is the
    # live case (Chipo Bamusi, confirmed by the CFO 2026-09-18). Without this
    # flag the automation would cut a working contractor off mid-engagement,
    # which is why it is part of the first version and not a later refinement.
    #
    # It is deliberately NOT inferred from the termination reason: 'end_of_
    # contract' is the reason used for people who genuinely left, so reading
    # intent off the reason would keep the wrong people signed in.
    keep_access_after_exit = models.BooleanField(
        default=False, db_index=True,
        help_text='Keep the Omni login open after the exit date (external '
                  'contractor or similar). HR sets this — the automatic '
                  'close-off skips anyone ticked here.')

    # ── Terminated Employee Archive (Oprah Mogomotsi feature request, bug
    # report d0f05edc-06f5-4084-94ce-b613e54e7665, 2026-08-13) ──────────────
    # A manual HR action taken AFTER termination — separate from `status`.
    # Archiving hides the record from active lists/pay runs but never deletes
    # data; payroll fields become read-only while archived (enforced in
    # payroll.api_views.EmployeeViewSet). Retention default is 7 years from
    # termination per Botswana DPA Act No. 18 of 2024 + BURS payroll
    # retention rules — configurable per record, no auto-purge.
    is_archived     = models.BooleanField(default=False, db_index=True)
    archived_at     = models.DateTimeField(null=True, blank=True)
    archived_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='+',
                      )
    archive_reason  = models.TextField(blank=True, default='')
    retention_years = models.PositiveSmallIntegerField(default=7)

    @property
    def retention_expiry_date(self):
        """Termination date + retention_years. None if never terminated."""
        if not self.termination_date:
            return None
        try:
            return self.termination_date.replace(year=self.termination_date.year + self.retention_years)
        except ValueError:
            # 29 Feb termination + non-leap target year.
            return self.termination_date.replace(month=2, day=28,
                                                   year=self.termination_date.year + self.retention_years)

    # ── PAY-008 housing benefit (BURS § 32 — CFO directive 2026-05-29) ──────
    # When salary_sacrifice_housing=True + housing_benefit_type ∈ {rated,
    # non_rated}, the payslip's HOUSING_ALLOWANCE line is force-set to 0 and a
    # HOUSING_BENEFIT line is inserted with the BURS-favorable formula value:
    #   rated      → 10% × rateable_value / 12
    #   non_rated  → 8% × P250 × floor_area_m2 / 12
    # Furniture benefit (if housing_furniture_cost > P15,000):
    #   FURNITURE_BENEFIT = 10% × (cost − 15000) / 12
    # PAYE auto-recomputes from the small taxable benefit, not the cash allowance.
    class HousingBenefitType(models.TextChoices):
        NONE      = 'none',      'No housing benefit (cash allowance or nothing)'
        RATED     = 'rated',     'Company-leased — rated area (10% rateable value)'
        NON_RATED = 'non_rated', 'Company-leased — non-rated area (8% × P250 × m²)'

    housing_benefit_type = models.CharField(
                              max_length=10,
                              choices=HousingBenefitType.choices,
                              default=HousingBenefitType.NONE,
                          )
    housing_rateable_value = models.DecimalField(
                              max_digits=14, decimal_places=2,
                              null=True, blank=True,
                              help_text='Annual rateable value of the property (BWP) — used '
                                        'when housing_benefit_type=rated. From council valuation roll.',
                          )
    housing_floor_area_m2  = models.DecimalField(
                              max_digits=8, decimal_places=2,
                              null=True, blank=True,
                              help_text='Floor area (m²) — used when housing_benefit_type=non_rated. '
                                        'Tribal land / unrated area only.',
                          )
    housing_furniture_cost = models.DecimalField(
                              max_digits=14, decimal_places=2,
                              null=True, blank=True,
                              help_text='Total cost of company-provided furniture (BWP). Furniture '
                                        'benefit fires only on the excess above P15,000.',
                          )
    salary_sacrifice_housing = models.BooleanField(
                              default=False,
                              help_text='When True, the payslip generator forces HOUSING_ALLOWANCE to '
                                        'zero and inserts a HOUSING_BENEFIT (and FURNITURE_BENEFIT) '
                                        'line computed from the fields above. Requires a signed '
                                        'employment-contract amendment.',
                          )

    class Meta(BaseModel.Meta):
        ordering = ['full_name']
        verbose_name        = 'Employee'
        verbose_name_plural = 'Employees'

    def __str__(self):
        return f"{self.full_name} ({self.department or '—'})"

    # ── PAY-008 helpers ─────────────────────────────────────────────────────
    def monthly_housing_benefit_bwp(self) -> Decimal:
        """BURS § 32 housing benefit value per month (BWP). Returns 0 if the
        employee is not on the salary-sacrifice + benefit arrangement."""
        if not self.salary_sacrifice_housing:
            return ZERO
        t = self.housing_benefit_type
        if t == self.HousingBenefitType.RATED:
            rv = Decimal(self.housing_rateable_value or 0)
            if rv <= 0:
                return ZERO
            return (rv * Decimal('0.10') / Decimal('12')).quantize(Decimal('0.01'))
        if t == self.HousingBenefitType.NON_RATED:
            area = Decimal(self.housing_floor_area_m2 or 0)
            if area <= 0:
                return ZERO
            return (Decimal('0.08') * Decimal('250') * area / Decimal('12')).quantize(Decimal('0.01'))
        return ZERO

    def monthly_furniture_benefit_bwp(self) -> Decimal:
        """BURS furniture benefit: 10% × (cost − P15,000) / 12, floor 0."""
        if not self.salary_sacrifice_housing:
            return ZERO
        cost = Decimal(self.housing_furniture_cost or 0)
        excess = cost - Decimal('15000')
        if excess <= 0:
            return ZERO
        return (excess * Decimal('0.10') / Decimal('12')).quantize(Decimal('0.01'))

    @property
    def current_contract(self):
        """Return the currently-active EmploymentContract on Botswana's today.

        Definition of "active":
          status == 'active' AND start_date <= today AND
          (end_date is null OR end_date >= today)

        Returns None if no contract matches. If multiple match (legacy
        rows from before clean() enforced single-active-window) the most
        recent start_date wins.
        """
        today = timezone.localdate()
        qs = (
            self.contracts
                .filter(status='active', start_date__lte=today)
                .filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=today))
                .order_by('-start_date')
        )
        return qs.first()

    @property
    def contract_type(self) -> str:
        """CoS contract type from the active (else most-recent) EmploymentContract.

        Returns '' when the employee has no contract on file. HR edits this via
        the HRIS amendment form (EMPLOYEE target); the write is handled specially
        in hris.amendment_service._apply_contract_type (get-or-create the active
        contract) since it lives on a related record, not on Employee itself.
        """
        c = self.current_contract or self.contracts.order_by('-start_date').first()
        return getattr(c, 'contract_type', '') or ''


# ---------------------------------------------------------------------------
# Tax brackets — configurable PAYE
# ---------------------------------------------------------------------------

class TaxBracket(AuditableMixin, BaseModel):
    """
    A single band of a progressive tax schedule.

    Annual taxable income strictly greater than `lower_bound` and less than or
    equal to `upper_bound` (or unbounded if upper_bound is null) attracts:

        tax = base_amount + rate_pct% * (income - lower_bound)

    The ACTIVE schedule is the set of brackets where is_active=True. The CFO
    edits these via /api/v1/tax-brackets/ when BURS publishes new bands.
    """

    name         = models.CharField(
                       max_length=80,
                       help_text='Human label, e.g. "Resident individual 2024-2025"',
                   )
    effective_from = models.DateField(
                       help_text='First date this band applies for monthly runs.',
                   )
    lower_bound  = models.DecimalField(
                       max_digits=18, decimal_places=2,
                       help_text='Annual taxable income, exclusive lower bound.',
                   )
    upper_bound  = models.DecimalField(
                       max_digits=18, decimal_places=2, null=True, blank=True,
                       help_text='Annual taxable income, inclusive upper bound. NULL = no cap.',
                   )
    base_amount  = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='Cumulative tax due at lower_bound.',
                   )
    rate_pct     = models.DecimalField(
                       max_digits=5, decimal_places=2,
                       help_text='Marginal rate within this band (e.g. 12.00 for 12%).',
                   )
    is_active    = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['effective_from', 'lower_bound']
        verbose_name        = 'Tax Bracket (PAYE)'
        verbose_name_plural = 'Tax Brackets (PAYE)'

    def __str__(self):
        upper = self.upper_bound or '∞'
        return f"{self.lower_bound}–{upper} @ {self.rate_pct}% (+{self.base_amount})"


# ---------------------------------------------------------------------------
# Component catalogue
# ---------------------------------------------------------------------------

class PayslipComponent(AuditableMixin, BaseModel):
    """
    One line-item on the payslip. The CFO has provided 28 headers — each is
    a row in this table. Renaming, adding, or disabling components does not
    require code changes.
    """

    class Kind(models.TextChoices):
        EARNING                 = 'earning',                 'Earning (taxable)'
        EARNING_NON_TAXABLE     = 'earning_non_taxable',     'Earning (non-taxable)'
        EMPLOYEE_DEDUCTION      = 'employee_deduction',      'Employee deduction (post-tax)'
        EMPLOYEE_PRETAX         = 'employee_pretax',         'Employee deduction (pre-tax)'
        COMPANY_CONTRIBUTION    = 'company_contribution',    'Company contribution (employer cost)'
        TAX                     = 'tax',                     'Tax (PAYE)'
        COMPUTED_GROSS          = 'computed_gross',          'Computed: Gross'
        COMPUTED_NET            = 'computed_net',            'Computed: Net Salary'
        COMPUTED_CTC            = 'computed_ctc',            'Computed: Cost to Company'

    class ComputationKind(models.TextChoices):
        FIXED      = 'fixed',      'Fixed amount (no formula)'
        PERCENT_OF = 'percent_of', 'Percent of another line'
        FORMULA    = 'formula',    'Custom safe formula'

    code       = models.CharField(max_length=40, unique=True)
    name       = models.CharField(
                     max_length=100,
                     help_text='Header label as it appears on the payslip.',
                 )
    kind       = models.CharField(max_length=25, choices=Kind.choices)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active  = models.BooleanField(default=True)
    is_taxable = models.BooleanField(
                     default=False,
                     help_text='If True, contributes to gross taxable income for PAYE. '
                               'Pre-tax deductions reduce taxable income.',
                 )
    # CFO 2026-09-20 — the month roll-forward had no way to tell a salary from
    # a one-off. Step 1 of apply_amendment_batch copied EVERY baseline line
    # into the next period and excluded exactly one code, LOAN_REPAYMENT, so a
    # commission / incentive / leave pay / severance was PAID AGAIN the
    # following month (P64,750 for one person from an EMPTY batch, HTTP 200).
    # Defaults True so every existing and future component keeps rolling
    # forward unless it is explicitly marked one-off; migration 0030 marks the
    # known ones and setup_payroll_components seeds them the same way.
    is_recurring = models.BooleanField(
                     default=True,
                     help_text='If True this component rolls forward into the next '
                               'period with the baseline copy. Set False for ONE-OFF '
                               'pay (commission, incentive, leave pay, severance, '
                               'bonus, overtime, arrears, reimbursement) — it belongs '
                               'to the month it was earned and must never be re-paid.',
                 )
    posting_account_code = models.CharField(
                     max_length=20, blank=True, default='',
                     help_text='Optional GL account code that this component should be '
                               'posted to when generating the payroll JE. Leave blank to '
                               'configure later.',
                 )
    # CFO directive 2026-05-24 (payroll+HR upgrades pass) — formula engine.
    # Existing components untouched; new fields default to 'fixed' / ''.
    computation_kind = models.CharField(
                     max_length=12,
                     choices=ComputationKind.choices,
                     default=ComputationKind.FIXED,
                     help_text='How this component is computed. Drives '
                               'payroll/formula_engine.py; ignored when fixed.',
                 )
    formula    = models.CharField(
                     max_length=200, blank=True, default='',
                     help_text='Safe expression evaluated by formula_engine. '
                               'Whitelisted names: BASIC, GROSS, PRIOR_LINES_DICT.',
                 )

    class Meta(BaseModel.Meta):
        ordering = ['sort_order', 'name']
        verbose_name        = 'Payslip Component'
        verbose_name_plural = 'Payslip Components'

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Payroll period
# ---------------------------------------------------------------------------

class PayrollPeriod(AuditableMixin, BaseModel):
    """A single payroll month (or other cadence)."""

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        LOCKED    = 'locked',    'Locked (calculated, awaiting approval)'
        APPROVED  = 'approved',  'Approved (ready to pay)'
        POSTED    = 'posted',    'Posted to GL'
        PAID      = 'paid',      'Paid'

    period_name = models.CharField(max_length=10, unique=True, help_text='e.g. 2026-05')
    start_date  = models.DateField()
    end_date    = models.DateField()
    pay_date    = models.DateField(null=True, blank=True)
    status      = models.CharField(
                      max_length=15, choices=Status.choices, default=Status.OPEN,
                  )
    notes       = models.TextField(blank=True, default='')
    # GL linkage — populated by payroll.services.post_payroll_period.
    # See .claude/steering/erp-relationships.md rule 1.
    journal_entry = models.ForeignKey(
                      'ledger.JournalEntry',
                      null=True, blank=True,
                      on_delete=models.PROTECT,
                      related_name='payroll_periods',
                      help_text='The GL entry created when this period was posted. '
                                'NULL until POSTED, cleared on reversal.',
                  )

    class Meta(BaseModel.Meta):
        ordering = ['-start_date']
        verbose_name        = 'Payroll Period'
        verbose_name_plural = 'Payroll Periods'

    def __str__(self):
        return f"Payroll {self.period_name}"


# ---------------------------------------------------------------------------
# Payslip + lines
# ---------------------------------------------------------------------------

class Payslip(AuditableMixin, BaseModel):
    """One employee × one period."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        APPROVED  = 'approved',  'Approved'
        PAID      = 'paid',      'Paid'
        CANCELLED = 'cancelled', 'Cancelled'

    employee = models.ForeignKey(
                   Employee, on_delete=models.PROTECT,
                   related_name='payslips',
               )
    period   = models.ForeignKey(
                   PayrollPeriod, on_delete=models.PROTECT,
                   related_name='payslips',
               )
    company  = models.ForeignKey(
                   Company, on_delete=models.PROTECT,
                   related_name='payslips', null=True, blank=True,
               )
    # Computed totals — recomputed from lines on save
    gross_amount   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    paye_amount    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    net_amount     = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    ctc_amount     = models.DecimalField(
                         max_digits=18, decimal_places=2, default=ZERO,
                         help_text='Cost to Company = Gross + Company contributions.',
                     )
    # ── Source currency (CFO 2026-06-20) ────────────────────────────────────
    # gross/paye/net/ctc above are ALWAYS the BWP reporting figures (group
    # reports + Time Doctor costing read these — do not change their meaning).
    # For entities paid in another currency (e.g. ADRisk = INR), the original
    # amounts + the currency are stored here so a payslip can be issued in the
    # local currency. source_*=None means the slip is BWP-native (= the *_amount
    # fields). fx_rate_to_bwp = BWP per 1 unit of source currency.
    source_currency = models.CharField(max_length=3, default='BWP',
                          help_text='ISO currency the employee is actually paid in (e.g. INR for ADRisk).')
    source_gross    = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True,
                          help_text='Gross in the source currency (None = same as gross_amount, BWP).')
    source_paye     = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    source_net      = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    fx_rate_to_bwp  = models.DecimalField(max_digits=18, decimal_places=8, default=Decimal('1'),
                          help_text='BWP per 1 unit of source currency (e.g. INR→BWP ≈ 0.142857 at 7:1).')

    @property
    def is_foreign_currency(self) -> bool:
        return bool(self.source_currency and self.source_currency != 'BWP')

    status   = models.CharField(
                   max_length=12, choices=Status.choices, default=Status.DRAFT,
               )
    notes    = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-period__start_date', 'employee__full_name']
        unique_together = [('employee', 'period')]
        verbose_name        = 'Payslip'
        verbose_name_plural = 'Payslips'

    def __str__(self):
        return f"{self.employee.full_name} — {self.period.period_name}"

    def apply_housing_sacrifice(self) -> dict:
        """PAY-008 (CFO directive 2026-05-29). If the employee is on the
        BURS § 32 salary-sacrifice arrangement, force HOUSING_ALLOWANCE to
        zero and rewrite HOUSING_BENEFIT + FURNITURE_BENEFIT lines from the
        employee's housing fields. Returns a dict {allowance_suppressed,
        benefit_amount, furniture_amount} for diagnostics.

        Safe to call multiple times — idempotent on the same employee
        config. No-op if salary_sacrifice_housing=False.
        """
        if not self.employee.salary_sacrifice_housing:
            return {'allowance_suppressed': ZERO, 'benefit_amount': ZERO, 'furniture_amount': ZERO}

        benefit  = self.employee.monthly_housing_benefit_bwp()
        furnish  = self.employee.monthly_furniture_benefit_bwp()

        # Zero out any HOUSING_ALLOWANCE line on this payslip.
        allow_suppressed = ZERO
        allow_line = self.lines.select_related('component').filter(
            component__code='HOUSING_ALLOWANCE').first()
        if allow_line and allow_line.amount and allow_line.amount > ZERO:
            allow_suppressed = allow_line.amount
            allow_line.amount = ZERO
            allow_line.save()

        # Upsert HOUSING_BENEFIT line.
        bcomp = PayslipComponent.objects.filter(code='HOUSING_BENEFIT').first()
        if bcomp:
            bline, _ = self.lines.get_or_create(
                component=bcomp, defaults={'amount': benefit})
            if bline.amount != benefit:
                bline.amount = benefit
                bline.save()

        # Upsert FURNITURE_BENEFIT line (only if non-zero — keep payslip lean).
        fcomp = PayslipComponent.objects.filter(code='FURNITURE_BENEFIT').first()
        if fcomp:
            fline = self.lines.filter(component=fcomp).first()
            if furnish > ZERO:
                if fline:
                    if fline.amount != furnish:
                        fline.amount = furnish
                        fline.save()
                else:
                    self.lines.create(component=fcomp, amount=furnish)
            elif fline and fline.amount != ZERO:
                fline.amount = ZERO
                fline.save()

        return {
            'allowance_suppressed': allow_suppressed,
            'benefit_amount':       benefit,
            'furniture_amount':     furnish,
        }

    def recompute_totals(self, *, recompute_paye: bool = True):
        """Aggregate lines into the four computed totals. Doesn't save.

        CFO directive 2026-05-24 (Track-B audit, payroll gap #7): when
        `recompute_paye=True` (default), the PAYE line is overwritten
        from the active BURS brackets after gross/pretax/deductions are
        summed. Previously the PAYE line stayed at whatever the
        amendment upload typed — so amending BASIC mid-period left the
        tax line silently stale. PAYE-only line is replaced; manual
        TAX_OVERRIDE amendments (a future field) bypass this.

        PAY-008 (CFO directive 2026-05-29): housing-benefit salary-sacrifice
        rewrite fires here BEFORE the aggregate so HOUSING_ALLOWANCE is
        forced to zero and the small BURS § 32 benefit value lands in the
        taxable-gross sum — which is what PAYE re-derives from.
        """
        # ── Foreign-currency payslips (CFO 2026-06-20) ──────────────────────
        # ADRisk (INR) and any non-BWP entity: totals are computed in the
        # SOURCE currency, then converted to the BWP reporting fields via
        # fx_rate_to_bwp. Botswana BURS PAYE is NEVER applied to a foreign
        # payroll — the tax withheld abroad (e.g. India TDS) is taken as-is
        # from the TAX line / source_paye. Returns before housing + BURS.
        if self.is_foreign_currency:
            from decimal import ROUND_HALF_UP
            cents = Decimal('0.01')
            fx = self.fx_rate_to_bwp or Decimal('1')
            lines = list(self.lines.select_related('component').all())
            has_earning = any(
                ln.component.kind in (PayslipComponent.Kind.EARNING,
                                      PayslipComponent.Kind.EARNING_NON_TAXABLE)
                for ln in lines
            )
            s_company = ZERO
            if has_earning:
                # Same sign-robust rule as the BWP branch below (bug 2026-07-23):
                # gross = earnings only; every deduction / contribution / tax by
                # magnitude so a negative-stored line can't flip into gross.
                s_gross = ZERO; s_paye = ZERO; s_emp_ded = ZERO
                for ln in lines:
                    kind = ln.component.kind
                    amt = ln.amount or ZERO
                    if kind in (PayslipComponent.Kind.EARNING,
                                PayslipComponent.Kind.EARNING_NON_TAXABLE):
                        s_gross += amt
                    elif kind in (PayslipComponent.Kind.EMPLOYEE_PRETAX,
                                  PayslipComponent.Kind.EMPLOYEE_DEDUCTION):
                        s_emp_ded += abs(amt)
                    elif kind == PayslipComponent.Kind.COMPANY_CONTRIBUTION:
                        s_company += abs(amt)
                    elif kind == PayslipComponent.Kind.TAX:
                        s_paye += abs(amt)
                self.source_gross = s_gross
                self.source_paye  = s_paye
                self.source_net   = s_gross - s_paye - s_emp_ded
            else:
                # headline-only import (Gross/PAYE/Net columns, no breakdown)
                s_gross = self.source_gross or ZERO
                tax_line = sum((ln.amount or ZERO) for ln in lines
                               if ln.component.kind == PayslipComponent.Kind.TAX)
                s_paye = tax_line if tax_line else (self.source_paye or ZERO)
                self.source_paye = s_paye
                if self.source_net is None:
                    self.source_net = s_gross - s_paye
            s_net = self.source_net or ZERO
            self.gross_amount = (s_gross * fx).quantize(cents, rounding=ROUND_HALF_UP)
            self.paye_amount  = (s_paye  * fx).quantize(cents, rounding=ROUND_HALF_UP)
            self.net_amount   = (s_net   * fx).quantize(cents, rounding=ROUND_HALF_UP)
            self.ctc_amount   = ((s_gross + s_company) * fx).quantize(cents, rounding=ROUND_HALF_UP)
            return

        # PAY-008 housing sacrifice rewrite (no-op when not opted in).
        try:
            self.apply_housing_sacrifice()
        except Exception:    # noqa: BLE001 — recompute must never crash on housing
            pass

        gross = ZERO
        paye  = ZERO
        emp_deductions = ZERO
        company_contributions = ZERO
        taxable_gross = ZERO   # earnings, less pre-tax (deductible) contributions
        # Sign-robust aggregation (bug Kago/Legakwa 2026-07-23): deduction lines
        # are stored as NEGATIVE amounts by the payroll import, but this routine
        # used `gross -= amt`, so `gross - (-993.50)` ADDED the contribution to
        # gross — inflating GROSS, PAYE and NET and driving TOTAL DEDUCTIONS
        # negative. Fixes:
        #   - GROSS is EARNINGS ONLY (matches the June import; a pre-tax
        #     contribution reduces the TAX BASE and NET, never displayed gross);
        #   - use the MAGNITUDE (abs) of every deduction / pre-tax / tax / company
        #     line so the stored sign can never flip it into the wrong bucket;
        #   - a pre-tax contribution (pension) reduces taxable_gross AND net; a
        #     post-tax deduction (e.g. medical aid EE) reduces net only.
        for ln in self.lines.select_related('component').all():
            kind = ln.component.kind
            amt = ln.amount or ZERO
            if kind == PayslipComponent.Kind.EARNING:
                gross += amt
                taxable_gross += amt
            elif kind == PayslipComponent.Kind.EARNING_NON_TAXABLE:
                gross += amt
            elif kind == PayslipComponent.Kind.EMPLOYEE_PRETAX:
                taxable_gross -= abs(amt)    # tax-deductible → reduces the PAYE base
                emp_deductions += abs(amt)   # and is money out → reduces net
            elif kind == PayslipComponent.Kind.EMPLOYEE_DEDUCTION:
                emp_deductions += abs(amt)   # post-tax → reduces net only
            elif kind == PayslipComponent.Kind.COMPANY_CONTRIBUTION:
                company_contributions += abs(amt)
            elif kind == PayslipComponent.Kind.TAX:
                paye += abs(amt)

        if recompute_paye:
            try:
                from .paye import active_brackets_from_db, calculate_monthly_paye
                brackets = active_brackets_from_db()
                if brackets and taxable_gross > 0:
                    fresh_paye = calculate_monthly_paye(taxable_gross, brackets)
                    paye = fresh_paye
                    # Overwrite the PAYE PayslipLine in-place if one
                    # exists; otherwise leave the line set to take effect
                    # only on the next save() of lines. Defer line write
                    # so this method stays side-effect-free on the model.
                    self._recomputed_paye = fresh_paye
            except Exception:    # noqa: BLE001
                # PAYE engine missing / no brackets seeded → fall back to
                # whatever was on the line. Same behaviour as before fix.
                pass

        self.gross_amount = gross
        self.paye_amount  = paye
        self.net_amount   = gross - paye - emp_deductions
        self.ctc_amount   = gross + company_contributions

    def overwrite_paye_line(self, paye_amount: 'Decimal', user=None) -> None:
        """Persist a fresh PAYE line — replaces all existing TAX-kind lines.

        Called by the amendment applier after recompute_totals() runs,
        so the TAX line in the DB matches the live BURS calculation.
        """
        paye_comp = (
            PayslipComponent.objects
            .filter(kind=PayslipComponent.Kind.TAX, is_active=True)
            .first()
        )
        if not paye_comp:
            return
        self.lines.filter(component=paye_comp).delete()
        PayslipLine.objects.create(
            payslip=self, component=paye_comp,
            amount=paye_amount,
            notes='Auto-recomputed from BURS brackets',
        )


class PayslipLine(BaseModel):
    payslip   = models.ForeignKey(
                    Payslip, on_delete=models.CASCADE, related_name='lines',
                )
    component = models.ForeignKey(
                    PayslipComponent, on_delete=models.PROTECT,
                    related_name='lines',
                )
    amount    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    notes     = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['component__sort_order']
        unique_together = [('payslip', 'component')]
        verbose_name        = 'Payslip Line'
        verbose_name_plural = 'Payslip Lines'

    def __str__(self):
        return f"{self.payslip.employee.full_name} · {self.component.name}: {self.amount}"


# ---------------------------------------------------------------------------
# Import staging — append-only, dual-authorisation
# ---------------------------------------------------------------------------

class PayrollImportBatch(AuditableMixin, BaseModel):
    """
    Bulk import of a previous-period payroll (typically Odoo XLSX export).
    Same dual-authorisation pattern as Asset / Recovery imports — two
    distinct approvers from CFO / Finance Manager / Financial Controller
    must sign off before commit. Append-only: existing payslips are not
    overwritten; conflicts (employee × period already populated) are skipped.
    """

    class Status(models.TextChoices):
        DRAFT              = 'draft',              'Draft (preview)'
        PARTIALLY_APPROVED = 'partially_approved', 'Awaiting second approval'
        APPROVED           = 'approved',           'Fully approved — ready to commit'
        COMMITTED          = 'committed',          'Committed'
        REJECTED           = 'rejected',           'Rejected'
        FAILED             = 'failed',             'Failed'

    source            = models.CharField(max_length=40, default='odoo')
    file_name         = models.CharField(max_length=255, blank=True, default='')
    period            = models.ForeignKey(
                            PayrollPeriod, on_delete=models.PROTECT,
                            related_name='import_batches',
                            null=True, blank=True,
                            help_text='Period the imported payslips will land in. '
                                      'Created on the fly if the file references one not in the system.',
                        )
    rows_total        = models.PositiveIntegerField(default=0)
    rows_valid        = models.PositiveIntegerField(default=0)
    rows_invalid      = models.PositiveIntegerField(default=0)
    rows_skipped_dup  = models.PositiveIntegerField(default=0)
    rows_imported     = models.PositiveIntegerField(default=0)
    parsed_rows       = models.JSONField(default=list, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    status            = models.CharField(
                            max_length=20, choices=Status.choices,
                            default=Status.DRAFT,
                        )
    company           = models.ForeignKey(
                            Company, on_delete=models.PROTECT,
                            related_name='payroll_import_batches',
                            null=True, blank=True,
                        )
    created_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='payroll_import_batches',
                        )
    # Dual-authorisation slots
    first_approved_by  = models.ForeignKey(User, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='payroll_imports_first_approved')
    first_approved_at  = models.DateTimeField(null=True, blank=True)
    second_approved_by = models.ForeignKey(User, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='payroll_imports_second_approved')
    second_approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by        = models.ForeignKey(User, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='payroll_imports_rejected')
    rejected_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason   = models.TextField(blank=True, default='')
    committed_at       = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Payroll Import Batch'
        verbose_name_plural = 'Payroll Import Batches'

    def __str__(self):
        return f"Payroll import — {self.file_name or self.id} ({self.status})"

    @property
    def is_fully_approved(self):
        return (
            self.first_approved_by_id is not None
            and self.second_approved_by_id is not None
            and self.first_approved_by_id != self.second_approved_by_id
        )


# ---------------------------------------------------------------------------
# Payroll amendments — between-period changes
# CFO directive 2026-05-21: monthly payroll is "last month + amendments".
# Operator uploads an XLSX of changes (hires, terminations, raises, bonuses,
# allowance tweaks, deduction overrides). The system applies them to the
# baseline period's payslips to materialise the new period.
# ---------------------------------------------------------------------------

class PayrollAmendmentBatch(AuditableMixin, BaseModel):
    """One XLSX upload of amendments. Append-only, audit-trailed."""

    class Status(models.TextChoices):
        PARSED        = 'parsed',        'Parsed (preview)'
        APPLIED       = 'applied',       'Applied to period'
        REJECTED      = 'rejected',      'Rejected'

    target_period = models.ForeignKey(
                        PayrollPeriod, on_delete=models.PROTECT,
                        related_name='amendment_batches',
                        help_text='The period being CREATED (e.g. May 2026). '
                                  'Amendments shift the baseline (April 2026) → this period.',
                    )
    baseline_period = models.ForeignKey(
                        PayrollPeriod, on_delete=models.PROTECT,
                        related_name='used_as_baseline',
                        help_text='Source period whose payslips form the starting point.',
                    )
    company        = models.ForeignKey(
                        Company, on_delete=models.PROTECT,
                        related_name='payroll_amendment_batches',
                        null=True, blank=True,
                    )
    file_name      = models.CharField(max_length=200, blank=True, default='')
    raw_xlsx       = models.FileField(upload_to='payroll/amendments/', null=True, blank=True)
    row_count      = models.PositiveIntegerField(default=0)
    status         = models.CharField(max_length=12, choices=Status.choices,
                                      default=Status.PARSED)
    notes          = models.TextField(blank=True, default='')
    uploaded_by    = models.ForeignKey(
                        User, null=True, blank=True,
                        on_delete=models.SET_NULL,
                        related_name='payroll_amendment_batches',
                    )
    applied_at     = models.DateTimeField(null=True, blank=True)
    # Who applied the batch to the target period (audit trail — CFO 2026-07-23:
    # staging SoD moved downstream, so the uploader may now apply; we still
    # record uploader AND applier so who-did-what stays fully auditable).
    applied_by     = models.ForeignKey(
                        User, null=True, blank=True,
                        on_delete=models.SET_NULL,
                        related_name='payroll_amendment_batches_applied',
                    )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Payroll Amendment Batch'
        verbose_name_plural = 'Payroll Amendment Batches'

    def __str__(self):
        return f'Amendments → {self.target_period.period_name} ({self.row_count} rows, {self.status})'


class PayrollAmendment(AuditableMixin, BaseModel):
    """One row in an amendment batch — one change for one employee."""

    class Kind(models.TextChoices):
        HIRE              = 'hire',               'New hire'
        TERMINATE         = 'terminate',          'Termination'
        SALARY_CHANGE     = 'salary_change',      'Salary change (basic)'
        ALLOWANCE_ADD     = 'allowance_add',      'Add / change allowance'
        ALLOWANCE_REMOVE  = 'allowance_remove',   'Remove allowance'
        DEDUCTION_ADD     = 'deduction_add',      'Add / change deduction'
        DEDUCTION_REMOVE  = 'deduction_remove',   'Remove deduction'
        BONUS             = 'bonus',              'One-off bonus'
        OVERTIME          = 'overtime',           'Overtime'
        ARREARS           = 'arrears',            'Arrears / back-pay'
        TAX_OVERRIDE      = 'tax_override',       'PAYE override'
        OTHER             = 'other',              'Other (free-text reason)'

    batch          = models.ForeignKey(
                        PayrollAmendmentBatch, on_delete=models.CASCADE,
                        related_name='amendments',
                    )
    employee       = models.ForeignKey(
                        Employee, on_delete=models.PROTECT,
                        related_name='payroll_amendments',
                        null=True, blank=True,
                        help_text='Resolved at parse time. Null = unresolved (see resolution_error).',
                    )
    employee_ref   = models.CharField(
                        max_length=120, blank=True, default='',
                        help_text='Raw employee identifier from the spreadsheet '
                                  '(employee_number or full_name).',
                    )
    kind           = models.CharField(max_length=20, choices=Kind.choices)
    component      = models.ForeignKey(
                        PayslipComponent, on_delete=models.PROTECT,
                        related_name='amendments',
                        null=True, blank=True,
                        help_text='The payslip line being added / changed / removed. Null for hire/terminate.',
                    )
    amount         = models.DecimalField(
                        max_digits=18, decimal_places=2, default=ZERO,
                        help_text='Signed BWP. For HIRE this is the new gross. '
                                  'For SALARY_CHANGE the new basic. For BONUS / OVERTIME / '
                                  'ARREARS the one-off amount.',
                    )
    reason         = models.CharField(max_length=300, blank=True, default='')
    approver       = models.CharField(max_length=120, blank=True, default='',
                        help_text='Free-text approver name from the spreadsheet.')
    applied        = models.BooleanField(default=False)
    resolution_error = models.CharField(max_length=300, blank=True, default='')
    # No-double-pay guard for the AUTOMATIC feeds (Build Spec B10/B11/B12,
    # 2026-09-13). A deterministic fingerprint of (which feed, which month,
    # which entity, which person, which payslip line) — see
    # payroll.feed_common.make_feed_key — held UNIQUE AT THE DATABASE LEVEL.
    # A Python check-then-write is not a control: two requests, two workers or
    # a re-run inside a race can both pass it. The unique index cannot be
    # raced; the second write is refused by Postgres itself.
    #
    # NULL for every hand-keyed / spreadsheet amendment, and NULLs stay
    # distinct under a unique index, so nothing already on file is affected.
    feed_key       = models.CharField(
                        max_length=64, null=True, blank=True, unique=True,
                        editable=False,
                        help_text='Automatic-feed fingerprint. Unique — the '
                                  'database-level no-double-pay guard. NULL '
                                  'for hand-keyed rows.',
                    )

    class Meta(BaseModel.Meta):
        ordering = ['batch', 'employee__full_name', 'kind']
        verbose_name        = 'Payroll Amendment'
        verbose_name_plural = 'Payroll Amendments'

    def __str__(self):
        emp = self.employee.full_name if self.employee_id else self.employee_ref
        return f'{emp} — {self.kind} ({self.amount})'


# ---------------------------------------------------------------------------
# Bank-details bulk import — single HR-Manager approval (CFO 2026-07-15)
# ---------------------------------------------------------------------------

class BankDetailImportBatch(AuditableMixin, BaseModel):
    """Bulk upload of staff bank details (account no + branch code) that a
    single HR-Manager approver signs off before it commits to Employee.

    Flow:
      1. Uploader (Pako / Legakwa / Tshephang) POSTs the file to
         /api/v1/payroll/bank-imports/ -> rows parsed + matched to employees +
         bank NAME derived from the branch code (payroll.bank_codes) -> a
         PENDING batch. NOTHING is written to any Employee yet.
      2. Dorothy (HR Manager) reviews the preview and approves
         -> /bank-imports/<id>/approve/ commits each matched row to
         Employee.bank_account_no / bank_branch_code / bank_name (audited).
      3. Or rejects with a reason.

    Maker-checker: the approver must be a DIFFERENT person from the uploader
    and an authorised bank-import approver (HR Manager / Unami / CFO). Account
    numbers are typed by a human into the file and committed by a human
    approver — the system never keys them.
    """

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending approval'
        APPROVED = 'approved', 'Approved & committed'
        REJECTED = 'rejected', 'Rejected'

    file_name      = models.CharField(max_length=255, blank=True, default='')
    rows_total     = models.PositiveIntegerField(default=0)
    rows_matched   = models.PositiveIntegerField(default=0)
    rows_committed = models.PositiveIntegerField(default=0)
    parsed_rows    = models.JSONField(
                         default=list, blank=True,
                         help_text='Preview payload: one dict per row (employee '
                                   'match, account, branch code, derived bank, status).',
                     )
    status         = models.CharField(
                         max_length=12, choices=Status.choices, default=Status.PENDING,
                     )
    created_by     = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='bank_import_batches',
                     )
    approved_by    = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='bank_imports_approved',
                     )
    approved_at    = models.DateTimeField(null=True, blank=True)
    rejected_by    = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='bank_imports_rejected',
                     )
    rejected_at    = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Bank Detail Import Batch'
        verbose_name_plural = 'Bank Detail Import Batches'

    def __str__(self):
        return f'Bank import {self.file_name or self.pk} — {self.status} ({self.rows_matched} matched)'


# ---------------------------------------------------------------------------
# Config store — no hardcoded business values (shared contract, payroll
# automation pack, 2026-09-12). One versioned, audited key/value row per
# setting; Finance/admin edits it via the admin, no deploy needed. Read via
# payroll.config.get_setting(key, default) — never read this model directly.
# ---------------------------------------------------------------------------

class PayrollSetting(AuditableMixin, BaseModel):
    """One named config value. Values are always stored as text; callers in
    payroll.config parse to the type they expect (bool/int/decimal/str)."""

    key         = models.CharField(max_length=100, unique=True)
    value       = models.CharField(max_length=300)
    description = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['key']
        verbose_name        = 'Payroll Setting'
        verbose_name_plural = 'Payroll Settings'

    def __str__(self):
        return f'{self.key} = {self.value}'


# ---------------------------------------------------------------------------
# Re-exported models from sibling files — keeps payroll/models.py the single
# import-site Django needs but lets large feature blocks live in their own
# files. (CFO directive 2026-05-22.)
# ---------------------------------------------------------------------------

from .contract_models import (    # noqa: E402,F401
    EmploymentContract,
    EmployeeLoan,
    LoanRepayment,
)

from .signoff_models import (     # noqa: E402,F401
    PayrollSignOff,
    live_payroll_totals,
)

from .addition_models import (    # noqa: E402,F401
    PayrollAdditionRequest,
)

from .salary_advance_models import (   # noqa: E402,F401
    SalaryAdvance, SalaryAdvancePayout,
)

from .orchestration_models import (   # noqa: E402,F401
    PayrollOrchestrationRun,
    PayrollOrchestrationStep,
)

from .group_report_models import (   # noqa: E402,F401
    GroupPayrollSnapshot,
    GroupPayrollLine,
)
