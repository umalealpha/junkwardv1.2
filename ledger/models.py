"""
ledger/models.py

The accounting backbone of Alpha Direct Financial Management System.

Models:
  - Account           Chart of Accounts
  - FiscalPeriod      Monthly accounting periods (Botswana FY: July–June)
  - JournalEntry      Double-entry journal entries with business-rule enforcement
  - JournalEntryLine  Individual debit/credit lines

Every monetary field: DecimalField(max_digits=18, decimal_places=2)
"""

import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditLog, AuditableMixin, BaseModel, Currency


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------

class Account(BaseModel):
    """A single node in the chart of accounts."""

    class AccountType(models.TextChoices):
        ASSET     = 'asset',     'Asset'
        LIABILITY = 'liability', 'Liability'
        EQUITY    = 'equity',    'Equity'
        REVENUE   = 'revenue',   'Revenue'
        EXPENSE   = 'expense',   'Expense'

    class StatementClass(models.TextChoices):
        BS  = 'BS',  'Balance Sheet'
        PNL = 'PNL', 'Profit & Loss'

    class NormalBalance(models.TextChoices):
        DEBIT  = 'D', 'Debit-natural'
        CREDIT = 'C', 'Credit-natural'

    # Canonical sub_type vocabulary (structural audit 2026-05-24).
    # Drives BS/PL bucketing in reports/reports.py and the CoA page sub-type
    # column. Free-form historical sub_types were normalised to this set on
    # 2026-05-24 (914 rows snapped). Stays a CharField for legacy free-form
    # rows to survive — choices is advisory at the form layer.
    class SubType(models.TextChoices):
        # Assets
        CURRENT_ASSET            = 'current_asset',            'Current Asset'
        BANK                     = 'bank',                     'Bank'
        FIXED_ASSET              = 'fixed_asset',              'Fixed Asset'
        ACCUMULATED_DEPRECIATION = 'accumulated_depreciation', 'Accumulated Depreciation'
        MID_TERM_ASSET           = 'mid_term_asset',           'Mid-Term Asset'
        OTHER_ASSET              = 'other_asset',              'Other Asset'
        # Liabilities
        CURRENT_LIABILITY        = 'current_liability',        'Current Liability'
        LONG_TERM_LIABILITY      = 'long_term_liability',      'Long-Term Liability'
        PROVISION                = 'provision',                'Provision'
        TAX_LIABILITY            = 'tax_liability',            'Tax Liability'
        # Equity
        EQUITY                   = 'equity',                   'Equity'
        RETAINED_EARNINGS        = 'retained_earnings',        'Retained Earnings'
        SHARE_CAPITAL            = 'share_capital',            'Share Capital'
        # Revenue
        OPERATING_REVENUE        = 'operating_revenue',        'Operating Revenue'
        OTHER_REVENUE            = 'other_revenue',            'Other Revenue'
        # Expenses
        OPERATING_EXPENSE        = 'operating_expense',        'Operating Expense'
        COGS                     = 'cogs',                     'Cost of Goods Sold'
        EXPENSE_DEPRECIATION     = 'expense_depreciation',     'Depreciation Expense'
        EXPENSE_DIRECT_COST      = 'expense_direct_cost',      'Direct Cost'
        FINANCE_COST             = 'finance_cost',             'Finance Cost'
        TAXATION                 = 'taxation',                 'Taxation'

    # account_type → allowed sub_types (validation in Account.clean()).
    SUBTYPE_BY_ACCOUNT_TYPE = {
        'asset':     {SubType.CURRENT_ASSET, SubType.BANK, SubType.FIXED_ASSET,
                      SubType.ACCUMULATED_DEPRECIATION, SubType.MID_TERM_ASSET,
                      SubType.OTHER_ASSET},
        'liability': {SubType.CURRENT_LIABILITY, SubType.LONG_TERM_LIABILITY,
                      SubType.PROVISION, SubType.TAX_LIABILITY},
        'equity':    {SubType.EQUITY, SubType.RETAINED_EARNINGS, SubType.SHARE_CAPITAL},
        'revenue':   {SubType.OPERATING_REVENUE, SubType.OTHER_REVENUE},
        'expense':   {SubType.OPERATING_EXPENSE, SubType.COGS,
                      SubType.EXPENSE_DEPRECIATION, SubType.EXPENSE_DIRECT_COST,
                      SubType.FINANCE_COST, SubType.TAXATION},
    }

    code            = models.CharField(max_length=20, unique=True)
    name            = models.CharField(max_length=200)
    account_type    = models.CharField(max_length=15, choices=AccountType.choices)
    sub_type        = models.CharField(max_length=50, choices=SubType.choices, blank=True)
    currency_code   = models.ForeignKey(
                          Currency, on_delete=models.PROTECT,
                          related_name='accounts', default='BWP',
                      )
    is_bank_account = models.BooleanField(default=False)
    # Receivables flag — single source of truth for what the dashboard
    # tile, BS "Receivables" line, AR aging, and CFO dashboard all sum.
    # Default False; backfilled per CFO directive 2026-05-24 from GL
    # prefixes 201/202/203/208/212/240/260. Finance maintains via admin.
    is_receivable   = models.BooleanField(default=False, db_index=True)
    parent          = models.ForeignKey(
                          'self', null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='children',
                      )
    is_active       = models.BooleanField(default=True)
    is_summary_only = models.BooleanField(default=False)
    description     = models.TextField(null=True, blank=True)
    external_ref    = models.CharField(
                          max_length=100, blank=True, default='', db_index=True,
                      )

    # CFO-controlled classification (populated by /cfo-upload CoA upload).
    # Blank-default so existing rows are non-breaking; CFO populates by
    # uploading the authoritative CoA CSV.
    statement_class   = models.CharField(
                            max_length=3, blank=True, default='',
                            choices=StatementClass.choices,
                        )
    fs_line_item      = models.CharField(max_length=200, blank=True, default='')
    normal_balance_dc = models.CharField(
                            max_length=1, blank=True, default='',
                            choices=NormalBalance.choices,
                        )
    owner_company     = models.ForeignKey(
                            'core.Company', null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='owned_accounts',
                        )
    # Marked True for accounts not in the most recent CoA upload — hidden
    # from selectors but kept for JE history.
    is_archived       = models.BooleanField(default=False, db_index=True)

    class Meta(BaseModel.Meta):
        ordering            = ['code']
        verbose_name        = 'Account'
        verbose_name_plural = 'Chart of Accounts'
        constraints = [
            # CFO directive 2026-05-25 (COA-001): one active name per company,
            # forever. This was created in the DB by migration
            # 0018_account_unique_name_per_company via AddConstraint, but the
            # matching declaration was never added here — so makemigrations has
            # perpetually wanted to DROP it to match the model. Declaring it
            # (identically to 0018) re-syncs model↔state↔DB with zero DB change
            # and keeps the integrity rule from being silently regenerated away.
            models.UniqueConstraint(
                fields=['owner_company', 'name'],
                condition=models.Q(is_active=True),
                name='uniq_active_account_name_per_company',
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    _OWNER_PREFIX_RE = __import__('re').compile(r'^([A-Za-z]+)[-_]')

    def save(self, *args, **kwargs):
        """CFO directive 2026-05-20: never let a new Account land with
        owner_company=NULL. If the code is prefixed (e.g. ADSA-101300),
        resolve the owner from the prefix; otherwise default to ADIC.

        Backstop against the 893-orphan incident — seed_coa_v2 and the
        Odoo importer both used to create accounts without owner_company,
        leaving them invisible to every multi-entity-scoped page.
        """
        if self._state.adding and self.owner_company_id is None:
            from core.models import Company
            target = None
            m = Account._OWNER_PREFIX_RE.match(self.code or '')
            if m:
                target = Company.objects.filter(code__iexact=m.group(1)).first()
            if target is None:
                target = Company.objects.filter(code='ADIC').first()
            if target is not None:
                self.owner_company = target
        super().save(*args, **kwargs)

    # Structural audit 2026-05-24: hierarchy guards. The parent FK was a
    # free-for-all — nothing stopped a cycle (A.parent=B, B.parent=A) or an
    # arbitrarily deep chain. Canonical ERPs (django-ledger MP_Node, hordak
    # MPTT) carry depth limits + cycle prevention at the data layer.
    MAX_TREE_DEPTH = 8

    def clean(self):
        super().clean() if hasattr(super(), 'clean') else None
        from django.core.exceptions import ValidationError
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError({'parent': 'Account cannot be its own parent.'})
        # Walk up the chain looking for a cycle or excessive depth.
        seen = {self.pk} if self.pk else set()
        cursor = self.parent
        depth = 1
        while cursor is not None:
            if cursor.pk in seen:
                raise ValidationError({'parent': 'Parent chain forms a cycle.'})
            seen.add(cursor.pk)
            if depth >= self.MAX_TREE_DEPTH:
                raise ValidationError({
                    'parent': f'CoA tree exceeds max depth of {self.MAX_TREE_DEPTH}.',
                })
            cursor = cursor.parent
            depth += 1


# ---------------------------------------------------------------------------
# Fiscal Year — CFO directive 2026-05-20 (post-Manus-TB-audit)
# ---------------------------------------------------------------------------

class FiscalYear(BaseModel):
    """
    Annual reporting period for a single Company. A FiscalYear is the parent
    container for the 12 monthly FiscalPeriods that make up the year.

    Why this exists (Manus audit 2026-05-20): the legacy system stored only
    monthly FiscalPeriods globally, so a trial balance "for FY25 (1 Jul 2024
    → 30 Jun 2025)" couldn't be expressed — the report inferred the period
    from the calendar month containing as_of, giving a 1-month window
    instead of a 12-month year.

    Lock workflow mirrors FiscalPeriod's. A FiscalYear lock cascades to its
    child FiscalPeriods through `_status_locks_writes()`.

    Label convention: 'FY25' = the fiscal year ending in calendar 2025.
    For Botswana entities with fy_end_month=6, FY25 = Jul 2024 → Jun 2025.
    """

    class Status(models.TextChoices):
        OPEN    = 'open',    'Open'
        LOCKED  = 'locked',  'Locked'
        CLOSING = 'closing', 'Closing'
        CLOSED  = 'closed',  'Closed'

    company    = models.ForeignKey(
                     'core.Company', on_delete=models.CASCADE,
                     related_name='fiscal_years',
                 )
    label      = models.CharField(
                     max_length=10,
                     help_text='Short label, e.g. "FY25". Unique per company.',
                 )
    start_date = models.DateField(
                     help_text='First day of the fiscal year (e.g. 2024-07-01).',
                 )
    end_date   = models.DateField(
                     help_text='Last day of the fiscal year (e.g. 2025-06-30).',
                 )
    status     = models.CharField(
                     max_length=10, choices=Status.choices, default=Status.OPEN,
                 )
    locked_at  = models.DateTimeField(null=True, blank=True)
    locked_by  = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL,
                     related_name='fiscal_years_locked',
                 )
    closed_at  = models.DateTimeField(null=True, blank=True)
    closed_by  = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL,
                     related_name='fiscal_years_closed',
                 )
    lock_reason = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-end_date', 'company__code']
        verbose_name        = 'Fiscal Year'
        verbose_name_plural = 'Fiscal Years'
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'label'],
                name='uniq_fiscal_year_company_label',
            ),
            models.UniqueConstraint(
                fields=['company', 'end_date'],
                name='uniq_fiscal_year_company_end_date',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['start_date', 'end_date']),
        ]

    def __str__(self):
        return f'{self.company.code} {self.label}'

    @property
    def is_locked(self) -> bool:
        return self.status in (self.Status.LOCKED,
                               self.Status.CLOSING,
                               self.Status.CLOSED)


# ---------------------------------------------------------------------------
# Fiscal Period
# ---------------------------------------------------------------------------

class FiscalPeriod(BaseModel):
    """A monthly accounting period.

    Lock workflow (CFO directive 2026-05-14):
      OPEN  → LOCKED   (requires BOTH cfo + fm signatures; no posting allowed)
      LOCKED → CLOSING → CLOSED  (year-end finalisation)

    Manus audit 2026-05-20: FiscalPeriod now also carries optional company +
    fiscal_year FKs so the same calendar month can be tracked separately per
    legal entity (different lock state, different fiscal-year parent). Legacy
    rows have both FKs NULL and behave system-wide as before.
    """

    class Status(models.TextChoices):
        OPEN    = 'open',    'Open'
        LOCKED  = 'locked',  'Locked'
        CLOSING = 'closing', 'Closing'
        CLOSED  = 'closed',  'Closed'

    period_name = models.CharField(max_length=10)   # e.g. "2025-07"
    start_date  = models.DateField()
    end_date    = models.DateField()
    status      = models.CharField(
                      max_length=10, choices=Status.choices, default=Status.OPEN,
                  )
    company     = models.ForeignKey(
                      'core.Company', on_delete=models.CASCADE,
                      null=True, blank=True,
                      related_name='fiscal_periods',
                      help_text='If set, this period is scoped to a single legal entity. '
                                'NULL = legacy / system-wide (pre-2026-05-20 schema).',
                  )
    fiscal_year = models.ForeignKey(
                      FiscalYear, on_delete=models.CASCADE,
                      null=True, blank=True,
                      related_name='periods',
                      help_text='Parent fiscal year. Set automatically by '
                                'seed_fiscal_years; NULL on legacy rows.',
                  )
    closed_by   = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='closed_periods',
                  )
    closed_at   = models.DateTimeField(null=True, blank=True)

    # Dual-protection lock signatures (CFO directive 2026-05-14).
    locked_by_cfo    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='periods_signed_cfo',
                      )
    locked_by_cfo_at = models.DateTimeField(null=True, blank=True)
    locked_by_fm     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='periods_signed_fm',
                      )
    locked_by_fm_at  = models.DateTimeField(null=True, blank=True)
    lock_reason      = models.TextField(blank=True, default='')

    # ── Deferred auto-lock (CFO directive 2026-06-09) ───────────────────────
    # TB import sets auto_lock_at = now() + 5 days. While the timestamp is in
    # the future the period stays effectively OPEN (so users can amend in the
    # grace window). Once the timestamp passes, `is_locked` returns True and
    # JE posting is blocked. No dual-sign required because the lock activation
    # is by clock + audit trail of the TB-import event in lock_reason.
    auto_lock_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['start_date']
        verbose_name        = 'Fiscal Period'
        verbose_name_plural = 'Fiscal Periods'
        constraints = [
            # Same calendar month across different companies is allowed; the
            # combination (company, period_name) must still be unique. For
            # legacy rows where company=NULL the period_name alone stays
            # unique via a partial UNIQUE INDEX below.
            models.UniqueConstraint(
                fields=['company', 'period_name'],
                name='uniq_fiscal_period_company_name',
            ),
            models.UniqueConstraint(
                fields=['period_name'],
                condition=models.Q(company__isnull=True),
                name='uniq_fiscal_period_name_when_company_null',
            ),
        ]

    def __str__(self):
        return f"{self.period_name} ({self.get_status_display()})"

    @classmethod
    def get_open_period_for_date(cls, date, company=None):
        """Return the open fiscal period covering *date*, or None.

        Company-scoped fix 2026-06-04 (the "every entry matches 14 periods"
        bug): periods are per-company, but legacy global rows (company IS NULL)
        also exist for the same months. Without scoping, a single date matches
        one OPEN row per company (~14) and the lookup is ambiguous — which is
        why uploads choked. When `company` is given, prefer that company's own
        period and fall back to a legacy global (company-null) period. When
        omitted, behaviour is unchanged (un-scoped first()) so existing callers
        do not regress.
        """
        qs = cls.objects.filter(
            start_date__lte=date,
            end_date__gte=date,
            status=cls.Status.OPEN,
        )
        if company is not None:
            return (qs.filter(company=company).first()
                    or qs.filter(company__isnull=True).first())
        return qs.first()

    @property
    def is_locked(self) -> bool:
        # Dual-sign or system-closed: hard locked.
        if self.status in (
            self.Status.LOCKED, self.Status.CLOSING, self.Status.CLOSED,
        ):
            return True
        # Deferred auto-lock (CFO 2026-06-09): when the 5-day grace window
        # after a TB import has elapsed, treat the period as locked even if
        # status is still OPEN. The mgmt command `auto_lock_aged_periods`
        # can later flip status to LOCKED for explicit storage, but this
        # property is the source of truth for all gates.
        if self.auto_lock_at is not None and self.auto_lock_at <= timezone.now():
            return True
        return False

    @property
    def auto_lock_pending(self) -> bool:
        """Auto-lock is scheduled but hasn't activated yet (grace window)."""
        return (self.status == self.Status.OPEN
                and self.auto_lock_at is not None
                and self.auto_lock_at > timezone.now())

    @property
    def has_full_lock_signoff(self) -> bool:
        return bool(self.locked_by_cfo_id and self.locked_by_fm_id)

    def sign_lock(self, user, role: str, reason: str = ''):
        """Record one of the two required signatures. Flips to LOCKED when both present."""
        role = role.lower().strip()
        if role not in ('cfo', 'fm'):
            raise ValueError("role must be 'cfo' or 'fm'")
        if self.status not in (self.Status.OPEN, self.Status.LOCKED):
            raise ValueError(
                f"Cannot sign lock on period in status {self.status!r}."
            )
        if role == 'cfo':
            if self.locked_by_cfo_id and self.locked_by_cfo_id != user.id:
                raise ValueError("CFO signature already recorded by another user.")
            self.locked_by_cfo = user
            self.locked_by_cfo_at = timezone.now()
        else:
            if self.locked_by_fm_id and self.locked_by_fm_id != user.id:
                raise ValueError("FM signature already recorded by another user.")
            self.locked_by_fm = user
            self.locked_by_fm_at = timezone.now()
        if reason and not self.lock_reason:
            self.lock_reason = reason
        if self.has_full_lock_signoff and self.status == self.Status.OPEN:
            self.status = self.Status.LOCKED
        self.save(update_fields=[
            'locked_by_cfo', 'locked_by_cfo_at',
            'locked_by_fm', 'locked_by_fm_at',
            'lock_reason', 'status', 'updated_at',
        ])

    def clear_lock(self, user, role: str):
        """Clear caller's own signature. Both must clear to unlock fully."""
        role = role.lower().strip()
        if role not in ('cfo', 'fm'):
            raise ValueError("role must be 'cfo' or 'fm'")
        if role == 'cfo':
            if self.locked_by_cfo_id != user.id:
                raise ValueError("Only the CFO who signed can clear the CFO signature.")
            self.locked_by_cfo = None
            self.locked_by_cfo_at = None
        else:
            if self.locked_by_fm_id != user.id:
                raise ValueError("Only the FM who signed can clear the FM signature.")
            self.locked_by_fm = None
            self.locked_by_fm_at = None
        if not self.has_full_lock_signoff and self.status == self.Status.LOCKED:
            self.status = self.Status.OPEN
            self.lock_reason = ''
        self.save(update_fields=[
            'locked_by_cfo', 'locked_by_cfo_at',
            'locked_by_fm', 'locked_by_fm_at',
            'lock_reason', 'status', 'updated_at',
        ])


# ---------------------------------------------------------------------------
# Entry-number generator
# ---------------------------------------------------------------------------

def _generate_entry_number(company=None):
    """
    Thread-safe auto-incrementing entry number.

    Format (CFO/Oprah directive 2026-05-28 — per-company sequence):
      - With a company:    JE-{CO}-{YYYY}-{000001}   e.g. JE-ADIC-2026-000001
      - No company (legacy): JE-{YYYY}-{000001}      e.g. JE-2026-000001

    Sequence is scoped to the prefix, so ADIC and VCM each get their own
    JE-CO-YYYY-000001..N stream and the global unique constraint on
    entry_number still holds. Uses SELECT FOR UPDATE inside an atomic block
    to prevent duplicates under concurrency.
    """
    year   = timezone.now().year
    code   = None
    if company is not None:
        code = getattr(company, 'code', None) if not isinstance(company, str) else company
    if code:
        prefix = f"JE-{code}-{year}-"
    else:
        prefix = f"JE-{year}-"
    with transaction.atomic():
        last = (
            JournalEntry.objects
            .select_for_update()
            .filter(entry_number__startswith=prefix)
            .order_by('-entry_number')
            .values_list('entry_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{prefix}{next_seq:06d}"


# ---------------------------------------------------------------------------
# Journal Entry
# ---------------------------------------------------------------------------

class JournalEntry(AuditableMixin, BaseModel):
    """
    A balanced double-entry journal entry.

    Business rules enforced in code:
    - Once posted, the entry is immutable; corrections require a reversal.
    - entry_date must fall within an open fiscal period.
    - All debit_bwp totals must equal all credit_bwp totals before posting.
    - Original-currency amounts must also balance.
    - At least two lines are required.
    """

    class Status(models.TextChoices):
        DRAFT            = 'draft',            'Draft'
        PENDING_APPROVAL = 'pending_approval', 'Pending Approval'
        RETURNED         = 'returned',         'Returned for Correction'
        REJECTED         = 'rejected',         'Rejected'
        POSTED           = 'posted',           'Posted'
        REVERSED         = 'reversed',         'Reversed'

    class JournalType(models.TextChoices):
        SALES               = 'sales',               'Sales'
        PURCHASES           = 'purchases',           'Purchases'
        CASH_RECEIPTS       = 'cash_receipts',       'Cash Receipts'
        CASH_PAYMENTS       = 'cash_payments',       'Cash Payments'
        BANK                = 'bank',                'Bank'
        GENERAL             = 'general',             'General'
        PAYROLL             = 'payroll',             'Payroll'
        COMMITMENT          = 'commitment',          'PO Commitment'
        COMMITMENT_REVERSAL = 'commitment_reversal', 'PO Commitment Reversal'

    entry_number  = models.CharField(max_length=30, unique=True, blank=True)
    entry_date    = models.DateField(
                        help_text='Single-date semantics: the end date of the '
                                  'period this JE represents. Use period_start '
                                  'to capture multi-month JEs (e.g. TB imports).',
                    )
    # CFO directive 2026-05-20 (Manus TB-architecture audit):
    # JEs that summarise a period of activity (TB imports, FY-end accruals)
    # need both ends of the range so reports can place opening / period /
    # closing buckets correctly. Point-in-time JEs leave this NULL and the
    # reporting layer treats it as period_start == entry_date.
    period_start  = models.DateField(
                        null=True, blank=True,
                        help_text='Start of the period this JE represents. NULL '
                                  '= point-in-time JE (period_start = entry_date).',
                    )
    posted_date   = models.DateTimeField(null=True, blank=True)
    description   = models.CharField(max_length=500)
    source_type   = models.CharField(max_length=50, default='manual')
    source_id     = models.UUIDField(null=True, blank=True)
    journal_type  = models.CharField(max_length=25, choices=JournalType.choices)
    status        = models.CharField(
                        max_length=20, choices=Status.choices, default=Status.DRAFT,
                    )
    company       = models.ForeignKey(
                        'core.Company', on_delete=models.PROTECT,
                        related_name='journal_entries', null=True, blank=True,
                    )
    # Self-referential FKs for reversal linkage
    reversed_by   = models.ForeignKey(
                        'self', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='+',
                    )
    reversal_of   = models.ForeignKey(
                        'self', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='+',
                    )
    currency_code = models.ForeignKey(
                        Currency, on_delete=models.PROTECT,
                        related_name='journal_entries',
                    )
    exchange_rate = models.DecimalField(
                        max_digits=18, decimal_places=8, default=Decimal('1.00000000'),
                    )
    created_by    = models.ForeignKey(
                        User, on_delete=models.PROTECT,
                        related_name='journal_entries_created',
                    )
    submitted_by  = models.ForeignKey(
                        User, null=True, blank=True,
                        on_delete=models.SET_NULL,
                        related_name='journal_entries_submitted',
                        help_text='Who submitted the entry for approval.',
                    )
    submitted_at  = models.DateTimeField(null=True, blank=True)
    approved_by   = models.ForeignKey(
                        User, null=True, blank=True,
                        on_delete=models.SET_NULL,
                        related_name='journal_entries_approved',
                        help_text='Who approved the entry. Must differ from created_by '
                                  '(segregation of duties).',
                    )
    approved_at   = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(
                        null=True, blank=True,
                        help_text='Reason given by the approver when an entry is rejected.',
                    )
    notes         = models.TextField(null=True, blank=True)

    # ---- Related-party transaction classification (IAS 24) -----------------
    # Three-state field — must be explicitly answered before submit_for_approval
    # will accept the entry. The frontend renders this as a required radio.
    is_related_party = models.BooleanField(
                        null=True, blank=True, default=None,
                        help_text='IAS 24 classification: True if any line on this entry '
                                  'transacts with a related party (director, subsidiary, '
                                  'KMP family etc.). MUST be set before the entry can be '
                                  'submitted for approval — no default, no implicit answer.',
                    )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Journal Entry'
        verbose_name_plural = 'Journal Entries'
        # Perf 2026-06-04 (high-volume readiness): every aggregation report
        # filters JournalEntryLine by journal_entry__{status, company,
        # entry_date}. Without a composite index this is a growing seq-scan as
        # volume climbs to millions of rows. These keep the per-company +
        # all-company report paths index-driven.
        indexes = [
            models.Index(fields=['company', 'status', 'entry_date'],
                         name='je_company_status_date_idx'),
            models.Index(fields=['status', 'entry_date'],
                         name='je_status_date_idx'),
        ]

    def __str__(self):
        return f"{self.entry_number} — {self.description}"

    # ------------------------------------------------------------------
    # save / delete  — entry-number generation + immutability guard
    # ------------------------------------------------------------------

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None,
             lock_override=None, **kwargs):
        # Auto-generate entry number on first save (per-company sequence
        # when self.company is set — CFO/Oprah directive 2026-05-28).
        if not self.entry_number:
            company_for_number = None
            if self.company_id:
                from core.models import Company as _Co
                company_for_number = (_Co.objects.filter(pk=self.company_id)
                                      .values_list('code', flat=True).first())
            self.entry_number = _generate_entry_number(company_for_number)

        # Historical-period hard lock (CFO directive 2026-05-17).
        # See ledger/locks.py. Whitelisted sources (e.g. tb_csv_import) and
        # callers holding the override pass through silently.
        from ledger.locks import assert_can_write_journal_entry
        company_code = None
        if self.company_id:
            from core.models import Company
            company_code = (Company.objects
                            .filter(pk=self.company_id)
                            .values_list('code', flat=True)
                            .first())
        assert_can_write_journal_entry(
            entry_date=self.entry_date,
            company_code=company_code,
            source_type=self.source_type or 'manual',
            override_value=lock_override,
        )

        # Immutability: block edits once the entry is posted/reversed
        if self.pk:
            try:
                db_status = (
                    JournalEntry.objects
                    .values_list('status', flat=True)
                    .get(pk=self.pk)
                )
                if db_status in (self.Status.POSTED, self.Status.REVERSED):
                    raise ValidationError(
                        f"{self.entry_number} is {db_status} and cannot be modified. "
                        "Create a reversal entry to correct it."
                    )
            except JournalEntry.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    def delete(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        if self.status in (self.Status.POSTED, self.Status.REVERSED):
            raise ValidationError(
                f"Cannot delete {self.entry_number}: it is {self.status}."
            )
        super().delete(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Internal validation
    # ------------------------------------------------------------------

    def _validate_for_posting(self):
        """Raise ValidationError if this entry cannot be posted."""
        if not self.pk:
            raise ValidationError("Save the journal entry before posting it.")

        # BUG-002 (Oprah QA, 2026-06-05): block ANY future-dated entry from
        # posting — tightened from the old >366-day guard per QA directive.
        # A posted entry records something that has happened, so its date can
        # never be after today. (Trade-off: a future-dated FY-end TB must now
        # be dated on/before the post date — flagged to the CFO.)
        if self.entry_date:
            from django.utils import timezone as _tz
            # "Today" must be BOTSWANA's today (settings.TIME_ZONE), never the
            # server clock's. _tz.now().date() is the UTC date on a UTC box, so
            # between 00:00 and 02:00 Gaborone every entry dated today was
            # rejected as "future" (nightly CI flake + a real nightly window
            # for users; found 18-Aug-2026).
            if self.entry_date > _tz.localdate():
                raise ValidationError(
                    f"Entry date {self.entry_date} is in the future. Journal "
                    f"entries cannot be dated after today."
                )

        # Allowed source statuses for transition into POSTED:
        #   DRAFT             — direct post (system / API users only)
        #   PENDING_APPROVAL  — normal post via approval workflow
        if self.status not in (self.Status.DRAFT, self.Status.PENDING_APPROVAL):
            raise ValidationError(
                f"Only draft or pending-approval entries can be posted. "
                f"Current status: {self.status}."
            )

        lines = list(self.lines.select_related('account').all())
        if len(lines) < 2:
            raise ValidationError(
                "A journal entry must have at least 2 lines before it can be posted."
            )

        # Summary-only accounts are calculated subtotals (e.g. 4300 Net Earned
        # Premium, 5300 Net Claims Incurred). Posting to them breaks the MA
        # P&L roll-up because they would double-count their constituent lines.
        summary_lines = [ln for ln in lines if ln.account.is_summary_only]
        if summary_lines:
            codes = ', '.join(sorted({ln.account.code for ln in summary_lines}))
            raise ValidationError(
                f"Cannot post to summary-only account(s): {codes}. These are "
                f"calculated subtotals derived from their child accounts — "
                f"post to the underlying account instead."
            )

        # Fiscal period check — scope to THIS entry's company so we don't
        # match one of ~14 same-month rows across all companies (2026-06-04).
        period = FiscalPeriod.get_open_period_for_date(
            self.entry_date, company=self.company,
        )
        if not period:
            raise ValidationError(
                f"No open fiscal period covers {self.entry_date}. "
                "Create or open a fiscal period for this date."
            )

        # Balance check — original currency
        total_debit  = sum(ln.debit_amount  for ln in lines)
        total_credit = sum(ln.credit_amount for ln in lines)
        if total_debit != total_credit:
            raise ValidationError(
                f"Entry is not balanced in {self.currency_code_id}: "
                f"debits {total_debit} ≠ credits {total_credit}."
            )

        # Balance check — BWP functional currency
        total_debit_bwp  = sum(ln.debit_bwp  for ln in lines)
        total_credit_bwp = sum(ln.credit_bwp for ln in lines)
        if total_debit_bwp != total_credit_bwp:
            raise ValidationError(
                f"Entry is not balanced in BWP: "
                f"debits {total_debit_bwp} ≠ credits {total_credit_bwp}."
            )

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    @transaction.atomic
    def post(self, user=None, *, _allow_direct=False):
        """
        Validate and post this journal entry.

        Direct posting from DRAFT is allowed only for:
          - system / API users (UserProfile.can_post_directly)
          - Django superusers
          - calls from approve() (which passes _allow_direct=True)

        Manual users must go through submit_for_approval() -> approve().

        Raises ValidationError on any rule violation.
        """
        from core.models import get_user_profile

        # Concurrency guard (premortem 2026-06-10): lock this row and re-read
        # the *committed* status before posting. @transaction.atomic gives
        # atomicity, not isolation against a read-modify-write race — without
        # this lock two simultaneous post() calls (double-click / client
        # retry) both read DRAFT/PENDING in their own snapshot and BOTH post
        # to the GL, double-counting the entry. select_for_update blocks the
        # second caller until the first commits, after which it sees POSTED
        # and aborts.
        if self.pk:
            _locked = (
                JournalEntry.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list('status', flat=True)
                .first()
            )
            if _locked is not None and _locked not in (
                self.Status.DRAFT, self.Status.PENDING_APPROVAL,
            ):
                raise ValidationError(
                    f"{self.entry_number} is already '{_locked}' — cannot post again."
                )

        self._validate_for_posting()

        # Direct post from DRAFT requires elevated authority
        if self.status == self.Status.DRAFT and not _allow_direct:
            profile = get_user_profile(user)
            is_superuser = bool(getattr(user, 'is_superuser', False))
            allowed = is_superuser or (profile is not None and profile.can_post_directly)
            if not allowed:
                raise ValidationError(
                    "Direct posting is not permitted. Submit the entry for "
                    "approval first; an authorised approver (CFO, Finance Manager, "
                    "or Financial Controller) will post it."
                )

        self.status      = self.Status.POSTED
        self.posted_date = timezone.now()

        # At this point DB status is still draft/pending_approval so the
        # immutability guard passes.
        self.save(
            audit_user=user,
            audit_description=f"Posted {self.entry_number}",
            skip_audit=True,   # BUG-003: explicit POST AuditLog below is the row
        )

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.POST,
            new_values={
                'status':      self.Status.POSTED,
                'posted_date': self.posted_date.isoformat(),
                'approved_by': str(self.approved_by_id) if self.approved_by_id else None,
            },
            user=user,
            description=f"Posted {self.entry_number}",
        )

    # ------------------------------------------------------------------
    # Approval workflow  (maker / checker, segregation of duties)
    # ------------------------------------------------------------------

    @transaction.atomic
    def submit_for_approval(self, user):
        """
        DRAFT -> PENDING_APPROVAL.

        Validates the entry as if for posting (balance, fiscal period, lines)
        so we never park an unbalanced entry in the approval queue.

        Enforces the related-party classification: the user must explicitly
        answer Yes or No before the entry can leave Draft. Internal control —
        no posting without a conscious related-party decision.
        """
        if self.status not in (self.Status.DRAFT, self.Status.RETURNED):
            raise ValidationError(
                f"Only draft or returned-for-correction entries can be submitted. "
                f"Current status: {self.status}."
            )
        if self.is_related_party is None:
            raise ValidationError(
                "Classify this entry as related-party (Yes / No) before submitting. "
                "IAS 24 requires every transaction to be explicitly assessed."
            )
        # Validate as if posting — same checks must pass
        self._validate_for_posting()

        # Auto-flag lines from related-party contacts. Lines whose contact is
        # related-party are tagged unless already overridden.
        for line in self.lines.select_related('contact').all():
            if line.contact_id and not line.is_related_party:
                if getattr(line.contact, 'is_related_party', False):
                    line.is_related_party = True
                    line.save()

        self.status       = self.Status.PENDING_APPROVAL
        self.submitted_by = user
        self.submitted_at = timezone.now()
        # Clear any prior rejection — this is a fresh submission round
        self.rejection_reason = None
        self.save(
            audit_user=user,
            audit_description=f"Submitted {self.entry_number} for approval",
            skip_audit=True,   # BUG-003: explicit AuditLog below is the row
        )

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.UPDATE,
            new_values={'status': self.Status.PENDING_APPROVAL},
            user=user,
            description=f"Submitted {self.entry_number} for approval",
        )

        # Notify eligible approvers — failures are silent (see core.notifications)
        try:
            from core.notifications import notify_je_pending_approval
            notify_je_pending_approval(self)
        except Exception:  # noqa: BLE001
            pass

    @transaction.atomic
    def approve(self, user):
        """
        PENDING_APPROVAL -> POSTED.

        Enforces:
          1. Status must be PENDING_APPROVAL.
          2. The approving user must hold an approval-eligible title
             (CFO, Finance Manager, Financial Controller).
          3. Segregation of duties: the approver cannot be the creator.

        On success, posts the entry to the GL in the same transaction.
        """
        from core.models import get_user_profile

        if self.status != self.Status.PENDING_APPROVAL:
            raise ValidationError(
                f"Only pending-approval entries can be approved. "
                f"Current status: {self.status}."
            )

        profile = get_user_profile(user)
        is_superuser = bool(getattr(user, 'is_superuser', False))
        if not is_superuser and (profile is None or not profile.can_approve_journal_entries):
            raise ValidationError(
                "You do not have authority to approve journal entries. "
                "Approval requires a CFO, Finance Manager, or Financial Controller title."
            )

        # Segregation of duties — the maker cannot be the checker.
        # Superusers are NOT exempt; regulators don't accept "I'm the admin" either.
        if self.created_by_id == getattr(user, 'id', None):
            raise ValidationError(
                "Segregation of duties: the creator of an entry cannot also approve it. "
                "Ask another approver."
            )

        self.approved_by = user
        self.approved_at = timezone.now()
        # Save approved_by/at first so the post() audit log captures them
        self.save(
            audit_user=user,
            audit_description=f"Approved {self.entry_number}",
            skip_audit=True,   # BUG-003: explicit APPROVE AuditLog below is the row
        )

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.APPROVE,
            new_values={'approved_by': str(user.id), 'approved_at': self.approved_at.isoformat()},
            user=user,
            description=f"Approved {self.entry_number}",
        )

        # Close the "Approve JE …" reminder tasks so approvers stop being nagged
        # about an entry that is now decided (mirrors close_payment_approval_tasks;
        # CFO 2026-07-22 — "I've done this already and it's still reminding me").
        try:
            from core.notifications import close_je_approval_tasks
            close_je_approval_tasks(self, 'approved')
        except Exception:  # noqa: BLE001
            pass

        # Hand off to post() with _allow_direct so it bypasses the
        # "must be system user" check (we just enforced approval rules above).
        self.post(user=user, _allow_direct=True)

    @transaction.atomic
    def reject(self, user, reason):
        """
        PENDING_APPROVAL -> REJECTED, with a mandatory reason.

        Same authority requirement as approve(); SoD applies (a rejector
        cannot be the creator either, otherwise creators could trivially
        bounce their own entries).
        """
        from core.models import get_user_profile

        if self.status != self.Status.PENDING_APPROVAL:
            raise ValidationError(
                f"Only pending-approval entries can be rejected. "
                f"Current status: {self.status}."
            )
        if not reason or not str(reason).strip():
            raise ValidationError("A rejection reason is required.")

        profile = get_user_profile(user)
        is_superuser = bool(getattr(user, 'is_superuser', False))
        if not is_superuser and (profile is None or not profile.can_approve_journal_entries):
            raise ValidationError(
                "You do not have authority to reject journal entries."
            )
        if self.created_by_id == getattr(user, 'id', None):
            raise ValidationError(
                "Segregation of duties: the creator cannot reject their own entry. "
                "Ask another approver, or withdraw it via Edit."
            )

        self.status = self.Status.REJECTED
        self.rejection_reason = str(reason).strip()
        self.save(
            audit_user=user,
            audit_description=f"Rejected {self.entry_number}: {reason}",
            skip_audit=True,   # BUG-003: explicit AuditLog below is the row
        )

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.UPDATE,
            new_values={'status': self.Status.REJECTED, 'rejection_reason': self.rejection_reason},
            user=user,
            description=f"Rejected {self.entry_number}",
        )

        # Close the open "Approve JE …" tasks — the entry is decided (CFO 2026-07-22).
        try:
            from core.notifications import close_je_approval_tasks
            close_je_approval_tasks(self, 'rejected')
        except Exception:  # noqa: BLE001
            pass

    @transaction.atomic
    def return_for_correction(self, user, reason, audit_ip=None):
        """
        PENDING_APPROVAL -> RETURNED, with a mandatory reason.

        Side state distinct from REJECTED (CFO/Oprah directive 2026-05-28).
        The Finance Manager sends the entry back to the capturer to fix —
        unlike REJECTED, the capturer can edit it in place and resubmit.
        Same SoD + authority rules as approve()/reject(): creator cannot
        return their own entry.
        """
        from core.models import get_user_profile

        if self.status != self.Status.PENDING_APPROVAL:
            raise ValidationError(
                f"Only pending-approval entries can be returned for correction. "
                f"Current status: {self.status}."
            )
        if not reason or not str(reason).strip():
            raise ValidationError("A return-for-correction reason is required.")

        profile = get_user_profile(user)
        is_superuser = bool(getattr(user, 'is_superuser', False))
        if not is_superuser and (profile is None or not profile.can_approve_journal_entries):
            raise ValidationError(
                "You do not have authority to return journal entries."
            )
        if self.created_by_id == getattr(user, 'id', None):
            raise ValidationError(
                "Segregation of duties: the creator cannot return their own entry."
            )

        self.status = self.Status.RETURNED
        self.rejection_reason = str(reason).strip()  # reuse field for the note
        self.save(
            audit_user=user,
            audit_ip=audit_ip,
            audit_description=f"Returned {self.entry_number} for correction: {reason}",
            skip_audit=True,   # BUG-003: explicit AuditLog below is the row
        )

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.UPDATE,
            new_values={
                'status': self.Status.RETURNED,
                'return_reason': self.rejection_reason,
                'entry_number': self.entry_number,
            },
            user=user,
            ip_address=audit_ip,
            description=f"Returned {self.entry_number} for correction",
        )

    @transaction.atomic
    def reopen_after_rejection(self, user):
        """
        REJECTED -> DRAFT, only by the creator.

        Lets the maker fix the entry in response to the rejection reason and
        resubmit. Approver-only metadata (approved_by/at, rejection_reason)
        is preserved as audit trail until next submission.
        """
        if self.status != self.Status.REJECTED:
            raise ValidationError(
                f"Only rejected entries can be reopened. Current status: {self.status}."
            )
        if self.created_by_id != getattr(user, 'id', None):
            raise ValidationError("Only the creator of an entry may reopen it.")

        self.status = self.Status.DRAFT
        self.save(
            audit_user=user,
            audit_description=f"Reopened {self.entry_number} after rejection",
        )

    @transaction.atomic
    def reverse(self, user, reason, *, _allow_direct=False, entry_date=None):
        """
        Create and post a reversal entry (debits ↔ credits swapped).
        Returns the new reversal JournalEntry.

        _allow_direct mirrors post(): callers whose OWN control is the gate
        (petty cash, where two signatures already authorised the entry being
        corrected) pass True, so the reversal is not blocked by the manual
        post-approval chain. Default False — every existing caller keeps the
        approval requirement it has today.

        entry_date overrides the reversal's date; it defaults to the date of the
        entry being reversed so both sides land in the same accounting period.
        """
        # Concurrency guard (premortem 2026-06-10): lock + re-read committed
        # status so two simultaneous reverse() calls can't both pass the
        # POSTED check and create TWO reversal entries (which would zero out
        # the original twice and corrupt the GL).
        if self.pk:
            _locked = (
                JournalEntry.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list('status', flat=True)
                .first()
            )
            if _locked != self.Status.POSTED:
                raise ValidationError(
                    f"Only posted entries can be reversed. Current status: {_locked}."
                )
        if self.status != self.Status.POSTED:
            raise ValidationError(
                f"Only posted entries can be reversed. Current status: {self.status}."
            )

        # Build reversal header
        reversal = JournalEntry(
            # Default: reverse in the period the entry itself sits in, so the
            # original and its reversal net to nil inside the SAME month. Dating
            # the reversal "today" left a cross-month correction showing the full
            # expense in one month and a negative in the next — right in total,
            # wrong in both months' management accounts. Callers can override.
            entry_date    = entry_date or self.entry_date,
            description   = f"Reversal of {self.entry_number}: {reason}",
            source_type   = 'manual',
            # A reversal belongs to the same entity as the entry it reverses.
            # Without this it was created company=None, which took the legacy
            # numbering stream, validated its period against ANY company's open
            # row, and short-circuited the ADIC historical FY lock — the exact
            # defect _je_company() exists to prevent on the forward entry.
            company       = self.company,
            # Bug 27d6d5f7: a reversal always carries a type. Normally it inherits
            # the source's type; if the source somehow has none (legacy/test rows),
            # fall back to GENERAL so the reversal never shows '---' in the JE list.
            journal_type  = self.journal_type or self.JournalType.GENERAL,
            status        = self.Status.DRAFT,
            reversal_of   = self,
            currency_code = self.currency_code,
            exchange_rate = self.exchange_rate,
            created_by    = user,
            notes         = reason,
        )
        reversal.save(
            audit_user=user,
            audit_description=f"Created reversal of {self.entry_number}",
        )

        # Swap debits and credits for each line
        for line in self.lines.all():
            JournalEntryLine.objects.create(
                journal_entry = reversal,
                account       = line.account,
                description   = f"Reversal: {line.description or ''}".strip(': '),
                debit_amount  = line.credit_amount,
                credit_amount = line.debit_amount,
                debit_bwp     = line.credit_bwp,
                credit_bwp    = line.debit_bwp,
            )

        reversal.post(user=user, _allow_direct=_allow_direct)

        # Mark original as reversed — use direct DB update to bypass immutability guard
        JournalEntry.objects.filter(pk=self.pk).update(
            status      = self.Status.REVERSED,
            reversed_by = reversal,
        )
        self.status      = self.Status.REVERSED
        self.reversed_by = reversal

        AuditLog.objects.create(
            table_name='JournalEntry',
            record_id=str(self.pk),
            action=AuditLog.Action.REVERSE,
            old_values={'status': 'posted'},
            new_values={'status': 'reversed', 'reversed_by': str(reversal.pk)},
            user=user,
            description=f"Reversed by {reversal.entry_number}: {reason}",
        )

        return reversal


# ---------------------------------------------------------------------------
# Journal Entry Line
# ---------------------------------------------------------------------------

class JournalEntryLine(BaseModel):
    """
    A single debit or credit line within a journal entry.
    Every monetary field: DecimalField(max_digits=18, decimal_places=2).
    """

    journal_entry = models.ForeignKey(
                        JournalEntry, on_delete=models.CASCADE, related_name='lines',
                    )
    account       = models.ForeignKey(
                        Account, on_delete=models.PROTECT, related_name='journal_lines',
                    )
    description   = models.CharField(max_length=500, null=True, blank=True)

    # Amounts in the entry's original currency
    debit_amount  = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    credit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    # Amounts in BWP functional currency
    debit_bwp     = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    credit_bwp    = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    contact       = models.ForeignKey(
                        'billing.Contact', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='journal_lines',
                    )
    is_related_party = models.BooleanField(
                        default=False,
                        help_text='Auto-set from contact.is_related_party at posting time, but '
                                  'can be manually overridden at the line level for cases where '
                                  'a single JE mixes related and unrelated parties.',
                    )

    class Meta(BaseModel.Meta):
        ordering            = ['created_at']
        verbose_name        = 'Journal Entry Line'
        verbose_name_plural = 'Journal Entry Lines'

    def __str__(self):
        if self.debit_amount:
            return f"Dr {self.account} {self.debit_amount}"
        return f"Cr {self.account} {self.credit_amount}"

    def clean(self):
        """Structural audit 2026-05-24: enforce 'only leaves take legs'.

        Posting to an account flagged `is_summary_only=True` corrupts roll-up
        reports because the summary account double-counts the leaves under
        it. Canonical ERPs (hordak Leg, django-ledger TransactionModel) all
        forbid this at the line level. Omni had the field but never enforced
        the rule — closing the loophole.
        """
        super().clean() if hasattr(super(), 'clean') else None
        from django.core.exceptions import ValidationError
        if self.account_id and self.account.is_summary_only:
            raise ValidationError({
                'account': (f"Account {self.account.code} is summary-only and "
                            "cannot receive journal entry lines. Post to a leaf "
                            "account under this header instead."),
            })
        # Belt-and-braces: a line must have exactly one of debit/credit non-zero.
        dr = self.debit_amount or Decimal('0.00')
        cr = self.credit_amount or Decimal('0.00')
        if dr < 0 or cr < 0:
            raise ValidationError('Debit and credit amounts must be non-negative.')
        if dr > 0 and cr > 0:
            raise ValidationError('A line cannot be both debit and credit.')

    def save(self, *args, **kwargs):
        """Lock writes once parent JE is POSTED/REVERSED (structural audit
        2026-05-24). The parent .save() already guards JournalEntry, but
        a caller could still mutate lines directly via ORM. Hordak / Capone
        enforce ledger-immutability at the line layer — this matches.
        """
        from django.core.exceptions import ValidationError
        parent_status = None
        if self.pk:
            parent_status = (JournalEntryLine.objects
                             .select_related('journal_entry')
                             .filter(pk=self.pk)
                             .values_list('journal_entry__status', flat=True)
                             .first())
        elif self.journal_entry_id:
            parent_status = (JournalEntry.objects
                             .filter(pk=self.journal_entry_id)
                             .values_list('status', flat=True)
                             .first())
        if parent_status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            raise ValidationError(
                f"Parent JE is {parent_status}; line is immutable. "
                "Post a reversal entry to correct."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        parent_status = (JournalEntryLine.objects
                         .select_related('journal_entry')
                         .filter(pk=self.pk)
                         .values_list('journal_entry__status', flat=True)
                         .first())
        if parent_status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
            raise ValidationError(
                f"Parent JE is {parent_status}; line cannot be deleted."
            )
        super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Supporting documents — attached to journal entries for audit evidence
# ---------------------------------------------------------------------------

def _je_attachment_path(instance, filename):
    """Year/month-bucketed upload path: media/je-attachments/2026/05/<je>-<file>."""
    today = timezone.localdate()
    safe = filename.replace('/', '_').replace('\\', '_')[:200]
    return f"je-attachments/{today.year}/{today.month:02d}/{instance.journal_entry_id}-{safe}"


class JournalEntryAttachment(AuditableMixin, BaseModel):
    """
    Supporting document attached to a journal entry — invoice PDFs,
    contracts, board resolutions, etc. Auditors expect every JE above a
    materiality threshold to have evidence.
    """

    journal_entry  = models.ForeignKey(
                         JournalEntry, on_delete=models.CASCADE,
                         related_name='attachments',
                     )
    file           = models.FileField(upload_to=_je_attachment_path)
    filename       = models.CharField(
                         max_length=255,
                         help_text='Original filename as uploaded.',
                     )
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    content_type   = models.CharField(max_length=100, blank=True, default='')
    description    = models.CharField(max_length=500, blank=True, default='')
    uploaded_by    = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='je_attachments_uploaded',
                     )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Journal Entry Attachment'
        verbose_name_plural = 'Journal Entry Attachments'

    def __str__(self):
        return f"{self.filename} on {self.journal_entry.entry_number}"


# ---------------------------------------------------------------------------
# Recurring journal entries — templates that auto-generate draft JEs
# ---------------------------------------------------------------------------

class RecurringJournalEntry(AuditableMixin, BaseModel):
    """
    Template for a journal entry that recurs on a schedule.

    On the scheduled day, `generate_recurring_journal_entries` management
    command (or scheduled task) creates a DRAFT JournalEntry from this
    template. The maker still has to submit, and an approver still has to
    approve — recurrence does not bypass the approval workflow.
    """

    class Frequency(models.TextChoices):
        MONTHLY    = 'monthly',    'Monthly'
        QUARTERLY  = 'quarterly',  'Quarterly'
        SEMIANNUAL = 'semiannual', 'Semi-annual'
        ANNUAL     = 'annual',     'Annual'

    name           = models.CharField(
                         max_length=200,
                         help_text='e.g. "Monthly office rent accrual"',
                     )
    description    = models.CharField(max_length=500)
    journal_type   = models.CharField(
                         max_length=25, choices=JournalEntry.JournalType.choices,
                         default=JournalEntry.JournalType.GENERAL,
                     )
    frequency      = models.CharField(
                         max_length=12, choices=Frequency.choices,
                         default=Frequency.MONTHLY,
                     )
    day_of_period  = models.PositiveSmallIntegerField(
                         default=1,
                         help_text='Day of month (1-28, use 28 for safe end-of-month) the JE '
                                   'should be dated. Quarterly/annual templates use the same '
                                   'day in the relevant period.',
                     )
    company        = models.ForeignKey(
                         'core.Company', on_delete=models.PROTECT,
                         related_name='recurring_journal_entries',
                         null=True, blank=True,
                     )
    currency_code  = models.ForeignKey(
                         Currency, on_delete=models.PROTECT,
                         related_name='recurring_journal_entries',
                     )
    start_date     = models.DateField(
                         help_text='First period that should generate a JE.',
                     )
    end_date       = models.DateField(
                         null=True, blank=True,
                         help_text='Optional — stop generating after this date.',
                     )
    last_generated_for = models.DateField(
                         null=True, blank=True,
                         help_text='High-water mark — date of the last JE generated by this '
                                   'template, prevents duplicate generation.',
                     )
    is_active      = models.BooleanField(default=True)
    created_by     = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='recurring_journal_entries_created',
                     )

    class Meta(BaseModel.Meta):
        ordering = ['name']
        verbose_name        = 'Recurring Journal Entry'
        verbose_name_plural = 'Recurring Journal Entries'

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"


class RecurringJournalEntryLine(BaseModel):
    """
    A single line on a recurring-JE template. When a JE is generated, each
    template line becomes a JournalEntryLine on the new JE.
    """

    template      = models.ForeignKey(
                        RecurringJournalEntry, on_delete=models.CASCADE,
                        related_name='lines',
                    )
    account       = models.ForeignKey(
                        Account, on_delete=models.PROTECT,
                        related_name='recurring_je_lines',
                    )
    description   = models.CharField(max_length=500, blank=True, default='')
    debit_amount  = models.DecimalField(
                        max_digits=18, decimal_places=2, default=Decimal('0.00'),
                    )
    credit_amount = models.DecimalField(
                        max_digits=18, decimal_places=2, default=Decimal('0.00'),
                    )
    contact       = models.ForeignKey(
                        'billing.Contact', null=True, blank=True,
                        on_delete=models.SET_NULL, related_name='recurring_je_lines',
                    )

    class Meta(BaseModel.Meta):
        ordering = ['created_at']
        verbose_name        = 'Recurring JE Line'
        verbose_name_plural = 'Recurring JE Lines'

    def __str__(self):
        if self.debit_amount:
            return f"Dr {self.account} {self.debit_amount}"
        return f"Cr {self.account} {self.credit_amount}"


# ---------------------------------------------------------------------------
# FrozenFigure — CFO-locked audit headline values with drift detection
# ---------------------------------------------------------------------------
#
# CFO directive 2026-05-17 (handover § P1): the eight audited management-
# accounts values for ADIC must be pinned at the dashboard layer with
# automatic drift detection. On every dashboard fetch, the server compares
# the rendered KPI tiles to the corresponding `FrozenFigure.value_bwp`. If
# any deviation exceeds `tolerance_pct`, the response carries a
# `frozen_drift` block which the frontend renders as a red banner.
#
# Override path: `OMNI_FINANCIAL_LOCK_OVERRIDE` env var matches the row's
# `acknowledged_by_override` flag once an admin acknowledges a drift via
# the API. Override value comes only from OMNI_FINANCIAL_LOCK_OVERRIDE
# (no hardcoded default — fail-closed if unset; 2026-07-17 audit).

class FrozenFigure(BaseModel):
    """Audit-locked headline number for a (period, line_label) pair."""

    period       = models.CharField(
                       max_length=20,
                       help_text="Free-text period code, e.g. 'FY25_Jun2025', 'FY26_Mar2026'.",
                   )
    line_label   = models.CharField(
                       max_length=50,
                       help_text="Free-text tile label, e.g. 'GWP', 'PAT', 'Total Assets', 'Cash & Bank'.",
                   )
    value_bwp    = models.DecimalField(max_digits=18, decimal_places=2)
    tolerance_pct = models.DecimalField(
                       max_digits=5, decimal_places=2,
                       default=Decimal('1.00'),
                       help_text="Allowed deviation as %; drift > tolerance triggers warning.",
                   )
    locked_by    = models.ForeignKey(
                       User, null=True, blank=True,
                       on_delete=models.SET_NULL,
                       related_name='frozen_figures_locked',
                   )
    locked_at    = models.DateTimeField(auto_now_add=True)
    notes        = models.TextField(blank=True, default='')
    is_active    = models.BooleanField(default=True)

    # When a drift is detected, an admin can acknowledge via API by
    # supplying the OMNI_FINANCIAL_LOCK_OVERRIDE password. Acknowledgement
    # silences the banner for THIS specific (period, line_label) until the
    # admin clears it.
    acknowledged_by_override = models.BooleanField(default=False)
    acknowledged_by          = models.ForeignKey(
                                   User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='frozen_figures_ack',
                               )
    acknowledged_at          = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['period', 'line_label']
        verbose_name        = 'Frozen Figure'
        verbose_name_plural = 'Frozen Figures'
        constraints = [
            models.UniqueConstraint(
                fields=['period', 'line_label'],
                name='uniq_frozen_figure_period_label',
            ),
        ]

    def __str__(self):
        return f"{self.period} · {self.line_label} = {self.value_bwp:,.2f}"

    def drift_for(self, actual_value) -> dict:
        """Return drift summary against a measured `actual_value` (Decimal).

        Returns None if within tolerance, else dict with keys:
            label, period, frozen, actual, diff, diff_pct, tolerance_pct.
        """
        actual = Decimal(str(actual_value or 0))
        frozen = self.value_bwp or Decimal('0')
        if frozen == 0:
            return None
        diff      = actual - frozen
        diff_pct  = (abs(diff) / abs(frozen)) * Decimal('100')
        if diff_pct <= self.tolerance_pct:
            return None
        return {
            'label':         self.line_label,
            'period':        self.period,
            'frozen':        str(frozen.quantize(Decimal('0.01'))),
            'actual':        str(actual.quantize(Decimal('0.01'))),
            'diff':          str(diff.quantize(Decimal('0.01'))),
            'diff_pct':      str(diff_pct.quantize(Decimal('0.01'))),
            'tolerance_pct': str(self.tolerance_pct),
            'acknowledged':  self.acknowledged_by_override,
        }


# ---------------------------------------------------------------------------
# AccountMergeAudit — CFO directive 2026-05-25 (COA-001)
# ---------------------------------------------------------------------------

class AccountMergeAudit(models.Model):
    """One row per duplicate-name merge decision. Append-only audit trail.

    Generated by `manage.py merge_duplicate_accounts`. Never edited.
    The auditors will rely on this to explain why account X stopped
    receiving postings on a given date.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    account_name = models.CharField(max_length=200, db_index=True)
    keeper_id    = models.UUIDField(db_index=True)
    keeper_code  = models.CharField(max_length=20)
    merged_id    = models.UUIDField(db_index=True)
    merged_code  = models.CharField(max_length=20)
    je_lines_moved = models.IntegerField(default=0)
    rule_applied   = models.CharField(max_length=80)

    company = models.ForeignKey(
        'core.Company', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='account_merge_audits',
    )
    performed_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    performed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-performed_at']
        indexes = [
            models.Index(fields=['company', 'account_name'],
                         name='ama_co_name_idx'),
        ]

    def __str__(self) -> str:
        return (f'{self.company_id} "{self.account_name}" '
                f'{self.merged_code}->{self.keeper_code} '
                f'(+{self.je_lines_moved} lines)')


# ---------------------------------------------------------------------------
# JEClearingRequest — maker-checker reversal of duplicate / wrong JEs
# ---------------------------------------------------------------------------
# CFO directive 2026-05-26: Cash balance overstated because the team
# duplicated postings ("TVs"). Build a workflow where Pako/Legakwa
# (makers) submit POSTED JournalEntry rows for reversal, and Kago
# Tshutlhedi (checker) approves → the system posts a reversal entry via
# JournalEntry.reverse() that the GL already supports.
# ---------------------------------------------------------------------------

class JEClearingRequest(BaseModel):
    """Maker-checker queue for clearing duplicate or wrong POSTED JEs.

    Finance directive 2026-07-02 (Oprah Mogomotsi, EXCO-endorsed, forwarded
    by the CFO) — REVERSES the 2026-05-26 hard-delete escalation. On approval
    the workflow now posts an auditable REVERSING journal entry for each
    target JE instead of hard-deleting it. Nothing is ever removed from the
    GL: the original stays, marked ``REVERSED`` and linked to its reversal, so
    every clearing leaves a permanent, auditable ledger trail. Audit trail:

      1.  ``reversal_entry``       — FK to the posted reversal JE (single mode).
      2.  ``deleted_je_snapshot``  — JSON dump of the JE header + lines as they
                                     were at approval, plus the reversal entry
                                     number for each (name kept for back-compat;
                                     it is now a *pre-reversal* snapshot, not a
                                     pre-delete one).
      3.  ``AuditLog`` ``REVERSE`` — one row per reversed JE, written by
                                     ``JournalEntry.reverse()``.

    Two modes:

    *  **single**  — ``journal_entry`` points at one JE; approval reverses it.
    *  **bulk**    — ``journal_entry`` is NULL, ``bulk_scope`` carries
                     ``{company, from_date, to_date, source_type?}`` and
                     approval reverses every POSTED JE that matches.
    """

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending approval'
        APPROVED = 'approved', 'Approved & reversed'
        REJECTED = 'rejected', 'Rejected'

    journal_entry = models.ForeignKey(
        JournalEntry, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='clearing_requests',
        help_text='The POSTED JE the maker wants deleted. Null for bulk requests.',
    )
    reason = models.TextField(
        help_text='Why this JE / period should be cleared.',
    )

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )

    submitted_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name='je_clearings_submitted',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='je_clearings_decided',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default='')

    # The posted reversal JE for a single-mode approval (Finance directive
    # 2026-07-02). NULL for bulk approvals (which post one reversal per matched
    # JE — see deleted_je_snapshot for the per-entry reversal numbers) and for
    # rows created under the 2026-05-26 hard-delete regime.
    reversal_entry = models.OneToOneField(
        JournalEntry, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='clearing_request',
    )

    # ── Hard-delete metadata (CFO directive 2026-05-26) ────────────────────
    bulk_scope = models.JSONField(
        null=True, blank=True,
        help_text=(
            'Bulk-wipe scope when journal_entry is NULL. Keys: company '
            '(UUID), from_date (ISO), to_date (ISO), optional source_type.'
        ),
    )
    deleted_je_snapshot = models.JSONField(
        null=True, blank=True,
        help_text=(
            'Frozen JSON dump of the JE(s) deleted on approval. Header, '
            'lines, and metadata, so an auditor can fully reconstruct the '
            'entry after the row is gone from the GL.'
        ),
    )
    deleted_count = models.PositiveIntegerField(
        default=0,
        help_text='Number of journal entries hard-deleted on approval (1 for single, N for bulk).',
    )

    class Meta:
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['status', '-submitted_at'],
                         name='jecr_status_idx'),
        ]

    def __str__(self):
        if self.journal_entry_id:
            return f'Clearing#{self.id} {self.journal_entry.entry_number} ({self.status})'
        return f'Clearing#{self.id} BULK ({self.status})'
