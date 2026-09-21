"""
core/models.py

Foundation models for Alpha Direct Financial Management System:
  - BaseModel        Abstract base (UUID pk + timestamps)
  - AuditLog         Immutable action log
  - AuditableMixin   Reusable mixin — writes to AuditLog on save/delete
  - UserProfile      Extended user profile
  - Currency         Supported currencies
  - ExchangeRate     Daily exchange rates
  - TaxRate          VAT and other tax codes
"""

import uuid
import datetime as dt

from django.conf import settings
from django.db import models
from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseModel(models.Model):
    """Abstract base class for all Alpha Direct models."""

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


# ---------------------------------------------------------------------------
# Audit Log  (defined first so AuditableMixin can reference it)
# ---------------------------------------------------------------------------

class AuditLog(models.Model):
    """Immutable record of every important action in the system."""

    class Action(models.TextChoices):
        CREATE  = 'create',  'Create'
        UPDATE  = 'update',  'Update'
        DELETE  = 'delete',  'Delete'
        POST     = 'post',     'Post'
        REVERSE  = 'reverse',  'Reverse'
        APPROVE  = 'approve',  'Approve'
        DOWNLOAD = 'download', 'Download'
        READ     = 'read',     'Read'      # sensitive-PII read (DPA L-5, 2026-07-19)

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table_name  = models.CharField(max_length=100)
    record_id   = models.CharField(max_length=255)   # stores any PK type as string
    action      = models.CharField(max_length=10, choices=Action.choices)
    old_values  = models.JSONField(null=True, blank=True)
    new_values  = models.JSONField(null=True, blank=True)
    user        = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL, related_name='audit_logs',
                  )
    ip_address  = models.CharField(max_length=45, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering     = ['-created_at']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'
        # Perf 2026-06-04: core_auditlog is the largest table (490k+ rows) and
        # grows fastest under transaction volume. The dashboard "last 24h" feed
        # filters by created_at — index it so that stays an index-only scan
        # instead of a seq-scan over the whole trail. (No rows deleted; the
        # audit trail is retained in full.)
        indexes = [
            models.Index(fields=['-created_at'], name='auditlog_created_at_idx'),
            # PERF (2026-07-17): the audit-log viewer filters by table_name +
            # record_id ("history of this record"); without this it seq-scanned
            # the 490k-row trail. Composite so record-history lookups are indexed.
            models.Index(fields=['table_name', 'record_id'], name='auditlog_table_record_idx'),
        ]

    def __str__(self):
        return f"{self.action.upper()} {self.table_name}:{self.record_id} by {self.user}"


# ---------------------------------------------------------------------------
# Auditable Mixin
# ---------------------------------------------------------------------------

def _serialize_value(value):
    """Convert a field value to a JSON-serialisable primitive."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        # Never stringify a binary blob into the audit log (a stored PDF would
        # dump ~0.3-1 MB of escaped bytes into AuditLog.new_values). Record its
        # size only. (Fable audit 2026-07-08.)
        return f'<{len(bytes(value))} bytes>'
    return str(value)   # Decimal, etc.


def _model_to_dict(instance):
    """Return {field_name: serialisable_value} for a model instance."""
    return {
        field.name: _serialize_value(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
    }


class AuditableMixin:
    """
    Mixin that automatically writes to AuditLog on save() and delete().

    Usage:
        class MyModel(AuditableMixin, BaseModel):
            ...

    To capture the requesting user / IP, pass keyword arguments:
        instance.save(audit_user=request.user, audit_ip=request.META.get('REMOTE_ADDR'))
    """

    def _auto_audit_description(self, action, provided):
        # BUG-019 (Oprah QA, 2026-06-05): Employee/Contact (and any model whose
        # caller doesn't pass audit_description) were logging audit rows with a
        # null description, so the audit log showed no human-readable identifier.
        # Every entry must carry the record id (already set) AND a readable
        # description. Auto-derive one from the model's __str__ when the caller
        # didn't supply it. Applies to ALL AuditableMixin models centrally.
        if provided:
            return provided
        try:
            label = str(self)
        except Exception:  # noqa: BLE001
            label = ''
        label = (label or f"{self.__class__.__name__} {self.pk}").strip()[:140]
        return f"{action} {self.__class__.__name__}: {label}"

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None,
             skip_audit=False, **kwargs):
        # BUG-003 (Oprah QA, 2026-06-08): workflow methods (post / submit /
        # approve / reject / return) call save() AND then write their own
        # explicit AuditLog row → two near-identical rows at the same timestamp.
        # Those callers now pass skip_audit=True so the mixin saves to the DB but
        # skips its auto-row, leaving exactly one meaningful audit entry per
        # action. Ordinary saves keep auto-auditing unchanged.
        if skip_audit:
            super().save(*args, **kwargs)
            return

        is_new = self._state.adding
        old_values = None

        if not is_new:
            try:
                old_obj = self.__class__.objects.get(pk=self.pk)
                old_values = _model_to_dict(old_obj)
            except self.__class__.DoesNotExist:
                is_new = True

        super().save(*args, **kwargs)

        action = AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE
        AuditLog.objects.create(
            table_name=self.__class__.__name__,
            record_id=str(self.pk),
            action=action,
            old_values=old_values,
            new_values=_model_to_dict(self),
            user=audit_user,
            ip_address=audit_ip,
            description=self._auto_audit_description(
                'Updated' if not is_new else 'Created', audit_description),
        )

    def delete(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        old_values = _model_to_dict(self)
        record_id  = str(self.pk)
        table_name = self.__class__.__name__

        super().delete(*args, **kwargs)

        AuditLog.objects.create(
            table_name=table_name,
            record_id=record_id,
            action=AuditLog.Action.DELETE,
            old_values=old_values,
            new_values=None,
            user=audit_user,
            ip_address=audit_ip,
            description=self._auto_audit_description('Deleted', audit_description),
        )


# ---------------------------------------------------------------------------
# Company / Subsidiary
# ---------------------------------------------------------------------------

class Company(BaseModel):
    """
    A subsidiary / legal entity that owns invoices, payments, and journal
    entries. Reports can be scoped to a single company or rolled up across
    all companies.
    """

    code             = models.CharField(max_length=10, unique=True,
                          help_text='Short code, e.g. ADI, ADIZ — used as filter key')
    name             = models.CharField(max_length=200)
    legal_name       = models.CharField(max_length=300, null=True, blank=True)
    registration_number = models.CharField(max_length=50, null=True, blank=True)
    tax_id           = models.CharField(max_length=50, null=True, blank=True)
    country          = models.CharField(max_length=2, default='BW',
                          help_text='ISO 2-letter country code')
    base_currency    = models.ForeignKey(
                          'Currency', on_delete=models.PROTECT,
                          related_name='companies_base', default='BWP',
                       )
    address          = models.TextField(null=True, blank=True)
    is_active        = models.BooleanField(default=True)
    is_default       = models.BooleanField(default=False,
                          help_text='Used when no company is specified on a record')

    # Per-company payroll access gate — distinct from the org-wide HRIS
    # tier-2 password. CFO directive 2026-05-12: each subsidiary's payroll
    # data should require its own password so the HR Manager can hold the
    # HRIS shared credential without seeing VCM / QIH / ADIH payroll.
    payroll_password_hash = models.CharField(
                          max_length=255, blank=True, default='',
                          help_text='PBKDF2 hash of the per-company payroll gate '
                                    'password. Empty = no gate (open). Set via '
                                    '`python manage.py set_payroll_password <code>`.',
                      )

    # Agency / parent relationship. CFO directive 2026-05-12: most group
    # entities (ADIIC, ADIH, ADIL, ADSA, ADRG, RSA, VCM, QIH) are independent
    # legal entities. Unicoin is an AGENCY of Alpha Direct Insurance Co.,
    # meaning Unicoin's transactions consolidate INTO ADIC for statutory
    # reporting. Reports that need to roll an agency up to its principal
    # walk this FK; reports that show an agency standalone (for management
    # purposes) filter by the agency's own Company id and ignore the FK.
    parent_company   = models.ForeignKey(
                          'self', on_delete=models.PROTECT,
                          null=True, blank=True, related_name='agencies',
                          help_text='If set, this Company is an agency / branch '
                                    'whose books consolidate INTO the parent. '
                                    'Null = independent legal entity.',
                      )

    # Regulator + reporting framework + fiscal-year end. Lifted from the
    # ADSA pipeline (entities.json) on 2026-05-18 so the per-entity
    # metadata Alpha Direct used to scatter across spreadsheets has one
    # canonical home. Each is nullable so old seed rows stay valid.
    regulator        = models.CharField(
                          max_length=40, blank=True, default='',
                          help_text='e.g. NBFIRA (BW), FSCA (ZA), IRA (UG)',
                      )
    fy_end_month     = models.PositiveSmallIntegerField(
                          default=6,
                          help_text='1-12 — month the fiscal year closes. '
                                    'Alpha Direct group default: 6 (June).',
                      )
    framework        = models.CharField(
                          max_length=40, blank=True, default='IFRS',
                          help_text='Reporting framework: IFRS, IFRS for SMEs, etc.',
                      )

    # Drives the dashboard layout. CFO directive 2026-05-19 (Manus master
    # guide § 2): insurance entities show GWP / Claims / Combined Ratio;
    # trading entities show Revenue / GP / EBITDA / PAT. ADIC + ADSA are
    # the only insurance entities — everything else is trading.
    class EntityType(models.TextChoices):
        INSURANCE    = 'insurance',    'Insurance'
        TRADING      = 'trading',      'Trading'
        CONSOLIDATED = 'consolidated', 'Consolidated Group'

    entity_type     = models.CharField(
                          max_length=20,
                          choices=EntityType.choices,
                          default=EntityType.TRADING,
                          help_text='Picks the dashboard layout: insurance metrics '
                                    '(GWP / Claims) vs trading metrics (Revenue / PAT).',
                      )

    # Free-form structured metadata for everything else that doesn't
    # warrant a dedicated column — directors, FSP, CIPC, VAT registration
    # state, bank-feed config. Read by reporting + KYC; never required.
    metadata         = models.JSONField(
                          default=dict, blank=True,
                          help_text='Optional dict — directors, FSP, CIPC, '
                                    'VAT state, etc. Read via Company.meta(key).',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Company'
        verbose_name_plural = 'Companies'
        ordering            = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"

    @classmethod
    def get_default(cls):
        """Return the company flagged is_default, or the first active one."""
        return (
            cls.objects.filter(is_default=True, is_active=True).first()
            or cls.objects.filter(is_active=True).order_by('created_at').first()
        )

    def meta(self, key, default=None):
        """Safe getter for the structured metadata JSON blob.

        Returns the stored value when `key` is valid for this company's
        jurisdiction. Country-scoped keys (e.g. fsp_number is ZA-only)
        return `default` on a foreign entity even if a stale row left a
        value behind. See core.entity_metadata_schema.
        """
        from core.entity_metadata_schema import is_key_allowed
        if not is_key_allowed(key, self.country):
            return default
        try:
            return (self.metadata or {}).get(key, default)
        except Exception:
            return default

    def clean(self):
        """Refuse to save with metadata that violates jurisdiction scope.

        CFO directive 2026-05-18: FSP / CIPC etc. only on ZA entities.
        """
        from django.core.exceptions import ValidationError
        from core.entity_metadata_schema import violations_for
        bad = violations_for(self.country, self.metadata)
        if bad:
            raise ValidationError({
                'metadata': (
                    f'Country "{self.country}" cannot carry these scoped '
                    f'keys: {", ".join(sorted(bad))}. See '
                    f'core.entity_metadata_schema.COUNTRY_SCOPED_KEYS.'
                )
            })
        return super().clean() if hasattr(super(), 'clean') else None

    # --- Payroll password gate -------------------------------------------

    def has_payroll_password(self):
        return bool(self.payroll_password_hash)

    def set_payroll_password(self, raw_password: str):
        """Store a hashed password using Django's PBKDF2 hasher."""
        from django.contrib.auth.hashers import make_password
        self.payroll_password_hash = make_password(raw_password)

    def check_payroll_password(self, raw_password: str) -> bool:
        """Constant-time compare against stored hash. Returns True if no gate set."""
        from django.contrib.auth.hashers import check_password
        if not self.payroll_password_hash:
            return True
        return check_password(raw_password, self.payroll_password_hash)


# ---------------------------------------------------------------------------
# User Profile
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    """
    Extended profile attached to Django's built-in User via OneToOne.

    Two orthogonal axes of authorisation:

      role   — legacy permission category (Finance Admin / Accountant / etc.)
      title  — human-facing job title (CFO / Finance Manager / Financial Controller / ...)

    Approval permission for journal entries is derived from `title`:
      CFO, Finance Manager, Financial Controller   -> can approve JEs

    Administrator permission (manage users, assign roles/titles) is granted
    explicitly via the `is_administrator` flag, plus implicitly to:
      - any user with title = CFO
      - any Django superuser

    The CFO is the absolute authority on this system; only the CFO and other
    administrators can grant or revoke roles, titles, and admin rights.
    """

    class Role(models.TextChoices):
        FINANCE_ADMIN    = 'finance_admin',    'Finance Admin'
        ACCOUNTANT       = 'accountant',       'Accountant'
        FINANCE_REVIEWER = 'finance_reviewer', 'Finance Reviewer'
        OPERATIONS_STAFF = 'operations_staff', 'Operations Staff'
        EXECUTIVE        = 'executive',        'Executive'
        SYSTEM_API       = 'system_api',       'System API'

    class Title(models.TextChoices):
        # Top leadership — functional executives. CEO/COO hold full approve +
        # post authority (present in APPROVAL/CREATION/PAYROLL/SOD_CHECKER sets
        # below and FINANCIALS_VIEW). Deliberately NOT in SOD_MAKER, so the
        # maker-title != checker-title split stands. CFO directive 2026-08-04.
        CEO                  = 'ceo',                  'Chief Executive Officer'
        COO                  = 'coo',                  'Chief Operating Officer'
        CFO                  = 'cfo',                  'Chief Financial Officer'
        FINANCE_MANAGER      = 'finance_manager',      'Finance Manager'
        FINANCIAL_CONTROLLER = 'financial_controller', 'Financial Controller'
        # Department managers — used by tier-1 bill-variance approvals.
        # Each PO-raising department (Admin / Claims / HR) has a manager who
        # can authorise a small variance (≤ policy.tier1_pct) on their own.
        CLAIMS_MANAGER       = 'claims_manager',       'Claims Manager'
        OPERATIONS_MANAGER   = 'operations_manager',   'Operations Manager'
        HR_MANAGER           = 'hr_manager',           'HR Manager'
        # Claims team grades (Bonang Lentswe list, CFO directive 2026-07-08).
        # Seniors (Team Leader + Senior Associate) approve claims POs alongside
        # the Claims Manager; juniors and interns do not.
        CLAIMS_TEAM_LEADER       = 'claims_team_leader',       'Claims Team Leader'
        SENIOR_CLAIMS_ASSOCIATE  = 'senior_claims_associate',  'Senior Claims Associate'
        JUNIOR_CLAIMS_ASSOCIATE  = 'junior_claims_associate',  'Junior Claims Associate'
        CLAIMS_INTERN            = 'claims_intern',            'Claims Intern'
        ACCOUNTANT           = 'accountant',           'Accountant'
        SENIOR_ACCOUNTANT    = 'senior_accountant',    'Senior Accountant'
        BOOKKEEPER           = 'bookkeeper',           'Bookkeeper'
        FINANCE_ANALYST      = 'finance_analyst',      'Finance Analyst'
        AUDITOR              = 'auditor',              'Auditor (read-only)'
        EXECUTIVE            = 'executive',            'Executive (read-only)'
        OPERATIONS           = 'operations',           'Operations Staff'
        # Senior operational staff — a team-leader step above OPERATIONS
        # (CFO directive 2026-08-24). Resolves to the 'mgr' HRIS tier in
        # core.hris_access.hris_role: own data + see/approve their own team.
        # Deliberately in NONE of the finance/payroll/SoD/FINANCIALS_VIEW sets
        # below, so it carries NO salary, GL, payroll, JE or finance-approval
        # rights — same financial visibility as OPERATIONS (i.e. none).
        SENIOR_OPERATIONS    = 'senior_operations',    'Senior Operational Staff'
        SYSTEM_API           = 'system_api',           'System / API'

    # Titles whose holders may approve journal entries.
    APPROVAL_TITLES = frozenset({
        Title.CEO,
        Title.COO,
        Title.CFO,
        Title.FINANCE_MANAGER,
        Title.FINANCIAL_CONTROLLER,
    })

    # Titles whose holders may create journal entries.
    CREATION_TITLES = frozenset({
        Title.CEO,
        Title.COO,
        Title.CFO,
        Title.FINANCE_MANAGER,
        Title.FINANCIAL_CONTROLLER,
        Title.ACCOUNTANT,
        Title.SENIOR_ACCOUNTANT,
        Title.BOOKKEEPER,
        Title.FINANCE_ANALYST,
        Title.SYSTEM_API,
    })

    # Titles whose holders may approve a payroll import (CFO directive
    # 2026-06-20: payroll needs ONE approver — HR Manager, Finance Manager,
    # or CFO — down from dual approval).
    PAYROLL_APPROVAL_TITLES = frozenset({
        Title.CEO,
        Title.COO,
        Title.CFO,
        Title.FINANCE_MANAGER,
        Title.HR_MANAGER,
    })

    # Titles whose holders may view financial data (reports, GL, dashboards,
    # audit log). CFO directive 2026-06-20: lower-level / operational staff
    # must NOT see financials or payroll. Everyone EXCEPT OPERATIONS (and the
    # untitled) — finance, management, exec and auditor are included.
    FINANCIALS_VIEW_TITLES = frozenset({
        Title.CEO,
        Title.COO,
        Title.CFO,
        Title.FINANCE_MANAGER,
        Title.FINANCIAL_CONTROLLER,
        Title.ACCOUNTANT,
        Title.SENIOR_ACCOUNTANT,
        Title.BOOKKEEPER,
        Title.FINANCE_ANALYST,
        Title.AUDITOR,
        Title.EXECUTIVE,
        Title.CLAIMS_MANAGER,
        Title.OPERATIONS_MANAGER,
        # HR_MANAGER removed 2026-07-15 (Oprah's access-control audit): HR staff
        # must NOT see the accounting department's financial data — CFO directive.
        # HR keeps all HR/HRIS functions (gated separately by hris_role); this
        # only stops HR titles from reading GL/reports/dashboards. Unami (Head of
        # Human Capital) keeps financial visibility via her is_administrator flag,
        # not via the HR title — so removing the title here is the clean fix.
        Title.SYSTEM_API,
    })

    # ── Segregation-of-duties for high-risk controlled transactions ──────
    # (Workstream A, CFO-approved 2026-07-02 — bugs #1 voucher-clearing /
    #  posted-JE reversal, #2 outbound payments, #5 FX rate loading.)
    # Control model: the MAKER (originator) and the CHECKER (approver) must be
    # different job titles AND different people. Makers ORIGINATE; only a
    # checker APPROVES; enforced additionally at the record level (approver !=
    # creator/submitter) in the service/model layer — never bypassed by
    # superuser on the identity check.
    #
    # MAKER = Financial Controller / Senior Accountant / Accountant.
    # Finance Manager is DELIBERATELY EXCLUDED from origination of these
    # actions (that was the fraud/collusion gap the auditor flagged).
    SOD_MAKER_TITLES = frozenset({
        Title.FINANCIAL_CONTROLLER,
        Title.SENIOR_ACCOUNTANT,
        Title.ACCOUNTANT,
    })
    # CHECKER = Finance Manager (with CFO as a super-approver). Finance Manager
    # keeps approval authority; it loses origination authority (above).
    SOD_CHECKER_TITLES = frozenset({
        # CFO directive 2026-08-04: CEO / COO approve high-risk transactions
        # (outbound payments, voucher clearing, FX) as CHECKERS. Deliberately
        # NOT in SOD_MAKER_TITLES, so maker-title != checker-title stays intact;
        # the record-level approver != creator check still bars self-approval.
        Title.CEO,
        Title.COO,
        Title.FINANCE_MANAGER,
        Title.CFO,
    })

    user             = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role             = models.CharField(max_length=20, choices=Role.choices)
    title            = models.CharField(
                           max_length=30, choices=Title.choices,
                           default=Title.ACCOUNTANT,
                           help_text='Job title — drives approval permissions.',
                       )
    department       = models.CharField(max_length=100, null=True, blank=True)
    # The department this person's task "Assign to" picker treats as "your team"
    # (top group), when it should differ from their own department. Blank = use
    # their real department. For executives who oversee everyone (department =
    # C-Suite) this lets, say, the CFO put Finance at the top of their picker
    # without changing their HR record. Display/convenience only — no permission
    # effect. CFO directive 2026-08-31.
    assignee_home_department = models.CharField(
                           max_length=100, blank=True, default='',
                           help_text='Department to show first in this user\'s task '
                                     'assignee picker; blank = their own department.',
                       )
    # Display-only HR job title, sourced from the Odoo HR master. This is the
    # full human-readable designation (e.g. "Underwriting Agent", "Receptionist")
    # shown in the staff/user lists. It DELIBERATELY does NOT drive any
    # permission — all access is governed by `title` / `role` / `is_administrator`
    # above. Safe to set to any Odoo string without affecting authorisation.
    job_title        = models.CharField(
                           max_length=150, blank=True, default='',
                           help_text='HR job title (from Odoo) — display only, does '
                                     'NOT affect permissions.',
                       )
    is_administrator = models.BooleanField(
                           default=False,
                           help_text='If True, this user can grant/revoke roles, titles, '
                                     'and admin rights to other users.',
                       )
    is_access_delegate = models.BooleanField(
                           default=False,
                           help_text='Limited access administrator (CFO directive 2026-09-15). '
                                     'Can grant and revoke only the titles in '
                                     'core.access_delegate.delegable_titles() — never payroll, '
                                     'manager-level, financial or administrator access, never '
                                     'their own profile, and never another administrator.',
                       )
    is_active        = models.BooleanField(default=True)

    # HRIS password gate (CFO directive 2026-05-18): even whitelisted
    # users (Prathap / Arun / Kago / Pako / Unami) must enter the shared
    # HRIS password before HR pages render. The unlock sticks until this
    # timestamp; default window is 8 hours. NULL = locked.
    hris_unlocked_until = models.DateTimeField(
                              null=True, blank=True,
                              help_text='HRIS unlock expiry. NULL = locked.',
                          )
    agent_bank_unlocked_until = models.DateTimeField(
                              null=True, blank=True,
                              help_text='Agent-portal bank-number reveal expiry. NULL = masked.',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.username} ({self.get_title_display()})"

    def save(self, *args, **kwargs):
        """Keep the SIGN-IN flag in step with this profile's Active tick.

        Reported by Unami Butale, 2026-09-18: "I am unable to remove other
        parties." She was right, and it was not the screen.

        Omni's Deactivate button, and the Active tick on the Users screen, only
        ever wrote UserProfile.is_active. Nothing wrote auth.User.is_active —
        and THAT is the flag the sign-in actually reads
        (core.staff_login_views._is_alpha_direct_user / _get_user), along with
        the browser-token check (core.token_auth) and the phone-session check
        (core.device_auth). So deactivating somebody removed their powers but
        left the door open: they could still sign in.

        Live proof on prod the same day: Bame Sebape, who left on 26 June, had
        profile.is_active False and user.is_active TRUE — deactivated on screen,
        still able to log in, 84 days later.

        A Fable audit already found this on 2026-09-02 (H8) and fixed ONE
        permission check (core.permissions, "a DEACTIVATED profile has no
        authority left") rather than the cause. Patching readers one at a time
        cannot work: the next reader that forgets is the next hole. So the sync
        lives on save(), where no caller — the Users screen, the bulk-access
        screen, a management command, the Django admin, a future one nobody has
        written yet — can route around it.

        Reactivating a profile re-opens the login, which is what an admin
        ticking Active back on plainly means.
        """
        was_active = None
        if self.pk:
            was_active = (type(self).objects.filter(pk=self.pk)
                          .values_list('is_active', flat=True).first())

        super().save(*args, **kwargs)

        if was_active is None or was_active == self.is_active:
            return
        user = self.user
        if user is None or user.is_active == self.is_active:
            return
        user.is_active = self.is_active
        user.save(update_fields=['is_active'])
        if not self.is_active:
            # Closing the account is what stops them; ending the live sessions
            # is what stops them RIGHT NOW rather than in 15 hours (browser) or
            # 30 days (phone app).
            from core.session_teardown import end_all_sessions
            end_all_sessions(user)

    # ------------------------------------------------------------------ permissions

    @property
    def is_read_only_identity(self) -> bool:
        """True for the locked quality-control / screenshot identities.

        core.token_auth refuses their tokens on anything but GET, so they cannot
        write, approve, delete or move money in ANY view. Several capability flags
        below grant themselves on `user.is_superuser` — and these identities ARE
        superusers, purely so no page is hidden from a check. Without this guard
        they advertised authority they physically do not have: the quality-control
        viewer reported can_approve_payroll and can_administer_users as True
        (spotted live 2026-07-29). The flags now tell the truth.

        Read-only VIEW flags are unaffected — seeing everything is the point.
        """
        from core.screenshot_bot import READ_ONLY_USERNAMES
        return getattr(self.user, 'username', '') in READ_ONLY_USERNAMES

    @property
    def can_approve_journal_entries(self) -> bool:
        """Holders of CFO / Finance Manager / Financial Controller titles."""
        return self.is_active and self.title in self.APPROVAL_TITLES

    @property
    def can_create_journal_entries(self) -> bool:
        return self.is_active and self.title in self.CREATION_TITLES

    @property
    def can_originate_controlled_txn(self) -> bool:
        """MAKER side of the SoD control (outbound payments, FX-rate loading,
        posted-JE clearing requests). Financial Controller / Senior Accountant
        / Accountant, plus system/API automation. Finance Manager is NOT a
        maker on these actions — that removal is the whole point of the fix."""
        if not self.is_active or self.is_read_only_identity:
            return False
        if self.title == self.Title.SYSTEM_API or self.user.is_superuser:
            return True
        return self.title in self.SOD_MAKER_TITLES

    @property
    def can_check_controlled_txn(self) -> bool:
        """CHECKER side of the SoD control — Finance Manager (or CFO). The
        record-level 'approver must differ from the originator' rule is
        enforced in the service/model layer and is NEVER waived, not even for
        a superuser."""
        if not self.is_active or self.is_read_only_identity:
            return False
        if self.user.is_superuser:
            return True
        return self.title in self.SOD_CHECKER_TITLES

    @property
    def can_approve_payroll(self) -> bool:
        """CFO directive 2026-06-20: a payroll import needs ONE approval, from
        an HR Manager, Finance Manager or CFO (admins/superusers included)."""
        if not self.is_active or self.is_read_only_identity:
            return False
        if self.is_administrator or self.user.is_superuser:
            return True
        return self.title in self.PAYROLL_APPROVAL_TITLES

    @property
    def can_view_financials(self) -> bool:
        """CFO directive 2026-06-20: financial reports, GL, dashboards and the
        audit log are for finance + management, not lower-level / operational
        staff. Admins/superusers always allowed."""
        if not self.is_active:
            return False
        if self.is_administrator or self.user.is_superuser:
            return True
        return self.title in self.FINANCIALS_VIEW_TITLES

    @property
    def can_administer_users(self) -> bool:
        """Explicit admin flag, or CFO title, or Django superuser."""
        if not self.is_active or self.is_read_only_identity:
            return False
        return (
            self.is_administrator
            or self.title == self.Title.CFO
            or self.user.is_superuser
        )

    @property
    def can_post_directly(self) -> bool:
        """
        True only for system / API users that bypass the approval workflow
        (integration events from Graphite, automated rule-based postings, etc.).
        """
        return self.is_active and self.title == self.Title.SYSTEM_API

    @property
    def can_manage_periods(self) -> bool:
        """
        True when the user can open / close / lock fiscal periods.

        CFO directive 2026-06-02: Period Management is a role-level
        capability of the Finance Manager — not a CFO-only or
        administrator-only function. Mirrors the existing backend
        authorisation in ``ledger/api_views.py::FiscalPeriodViewSet.sign_lock``
        which already accepts the FM signature from a Finance Manager.

        Holders:
          * Title CFO / FINANCE_MANAGER / FINANCIAL_CONTROLLER
          * UserProfile.is_administrator
          * django superuser
        """
        if not self.is_active or self.is_read_only_identity:
            return False
        if self.is_administrator or self.user.is_superuser:
            return True
        return self.title in (
            self.Title.CFO,
            self.Title.FINANCE_MANAGER,
            self.Title.FINANCIAL_CONTROLLER,
        )


def get_user_profile(user):
    """Convenience accessor — returns the profile or None."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return getattr(user, 'profile', None)


def user_is_approver_only(user) -> bool:
    """True for a checker-only role that must NOT originate/enter data.

    CFO directive 2026-07-05: 'the Finance Manager acts only as an approver;
    he must not create banks or input any data — real control is essential.'

    This is TITLE-based, not superuser-based: the Finance Manager is blocked
    from data entry EVEN IF also a Django superuser (the omogomotsi case — the
    FX-rate SoD fix removed exactly this superuser bypass, core/api_views.py).
    The CFO title is never blocked (the control owner), and makers
    (Financial Controller / Senior Accountant / Accountant) always originate.
    """
    prof = get_user_profile(user)
    if not prof:
        return False
    # Checker titles are {Finance Manager, CFO}; only the Finance Manager is
    # approver-only. The CFO may input data; everyone non-checker is unaffected.
    return (prof.title in UserProfile.SOD_CHECKER_TITLES
            and prof.title != UserProfile.Title.CFO)


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

class Currency(models.Model):
    """Supported currencies. Code (e.g. BWP) is the primary key."""

    code           = models.CharField(max_length=3, primary_key=True)
    name           = models.CharField(max_length=50)
    symbol         = models.CharField(max_length=5)
    decimal_places = models.PositiveSmallIntegerField(default=2)
    is_active      = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Currencies'
        ordering            = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# Exchange Rate
# ---------------------------------------------------------------------------

class ExchangeRate(BaseModel):
    """Daily exchange rate between two currencies."""

    class Source(models.TextChoices):
        MANUAL           = 'manual',           'Manual Entry'
        BANK_OF_BOTSWANA = 'bank_of_botswana', 'Bank of Botswana'

    from_currency  = models.ForeignKey(
                         Currency, on_delete=models.PROTECT, related_name='rates_from',
                     )
    to_currency    = models.ForeignKey(
                         Currency, on_delete=models.PROTECT, related_name='rates_to',
                         default='BWP',
                     )
    rate           = models.DecimalField(max_digits=18, decimal_places=8)
    effective_date = models.DateField()
    source         = models.CharField(
                         max_length=20, choices=Source.choices, default=Source.BANK_OF_BOTSWANA,
                     )
    # FX-001 (CFO/Oprah directive 2026-05-28): rates must be approved by a
    # Finance Manager before they can be used in any revaluation run.
    loaded_by      = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='fx_rates_loaded',
                     )
    approved_by    = models.ForeignKey(
                         User, null=True, blank=True, on_delete=models.SET_NULL,
                         related_name='fx_rates_approved',
                     )
    approved_at    = models.DateTimeField(null=True, blank=True)
    notes          = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Exchange Rate'
        verbose_name_plural = 'Exchange Rates'
        constraints         = [
            models.UniqueConstraint(
                fields=['from_currency', 'to_currency', 'effective_date'],
                name='unique_rate_per_currency_pair_per_day',
            )
        ]

    @property
    def is_approved(self) -> bool:
        return bool(self.approved_by_id and self.approved_at)

    def __str__(self):
        return (
            f"{self.from_currency_id}/{self.to_currency_id}"
            f" @ {self.rate} on {self.effective_date}"
        )


# ---------------------------------------------------------------------------
# Tax Rate
# ---------------------------------------------------------------------------

class TaxRate(BaseModel):
    """VAT and other tax codes applicable in Botswana."""

    tax_code       = models.CharField(max_length=20, unique=True)
    name           = models.CharField(max_length=100)
    rate           = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to   = models.DateField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Tax Rate'
        verbose_name_plural = 'Tax Rates'

    def __str__(self):
        rate_display = f"{self.rate}%" if self.rate is not None else "Exempt"
        return f"{self.tax_code} — {rate_display}"


# ---------------------------------------------------------------------------
# RBAC — hierarchical roles & permissions (2026-05-12)
# ---------------------------------------------------------------------------
#
# Additive layer on top of UserProfile. Existing approval workflows still read
# UserProfile.title; new code should check Permission codes via the helpers
# at the bottom of this section. Both work in parallel during migration.
#
# Role hierarchy (lower level = higher authority):
#   0  SUPER_ADMIN        system root
#   1  EXECUTIVE          C-suite (CEO/CFO/COO/CRO/CTO)
#   2  DEPARTMENT_HEAD    Head of <Finance|Claims|...>
#   3  MANAGER            dept-scoped managers
#   4  SENIOR             senior specialists
#   5  OFFICER            day-to-day staff
#   6  READ_ONLY          board / external auditor / regulator
#   9  SYSTEM             non-human service accounts
#
# Management rule: a role at level N may assign/revoke roles at level > N
# within its scope_department (level 0 has no scope; level 1 is company-wide).


class Department(models.TextChoices):
    EXECUTIVE    = 'executive',    'Executive'
    FINANCE      = 'finance',      'Finance'
    CLAIMS       = 'claims',       'Claims'
    UNDERWRITING = 'underwriting', 'Underwriting'
    REINSURANCE  = 'reinsurance',  'Reinsurance'
    COMPLIANCE   = 'compliance',   'Compliance & Risk'
    HR           = 'hr',           'Human Resources'
    IT           = 'it',           'Information Technology'
    OPERATIONS   = 'operations',   'Operations'
    EXTERNAL     = 'external',     'External (auditors / regulators)'
    SYSTEM       = 'system',       'System'


class Permission(models.Model):
    """
    A discrete authorisation code, e.g. 'je.approve'.

    Permission codes are stable strings owned by the code that checks them.
    The catalogue is seeded by `manage.py seed_roles` and is the single source
    of truth — never reference a permission that isn't in the seed.
    """

    code        = models.CharField(max_length=80, primary_key=True)
    category    = models.CharField(max_length=40, help_text='Grouping for UI (ledger, payments, …)')
    description = models.CharField(max_length=255)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'code']

    def __str__(self):
        return self.code


class Role(BaseModel):
    """
    A named bundle of permissions in the org hierarchy.

    Seeded roles are flagged is_system=True and cannot be deleted via the API.
    Custom roles created by admins are is_system=False.
    """

    code        = models.CharField(max_length=40, unique=True, help_text='Stable code, e.g. CFO, FINANCE_MANAGER')
    name        = models.CharField(max_length=80, help_text='Human-readable name')
    description = models.TextField(blank=True)
    level       = models.PositiveSmallIntegerField(
                      help_text='0 = highest authority (Super Admin); 9 = system accounts',
                  )
    department  = models.CharField(
                      max_length=20, choices=Department.choices,
                      null=True, blank=True,
                      help_text='Natural department; null means cross-department (e.g. Super Admin, CEO)',
                  )
    permissions = models.ManyToManyField(Permission, related_name='roles', blank=True)
    is_system   = models.BooleanField(default=False, help_text='Seed role; protected from deletion')
    is_active   = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['level', 'department', 'name']

    def __str__(self):
        return self.name

    def can_manage(self, other_role) -> bool:
        """
        Can a holder of THIS role manage assignments of `other_role`?

        Rules:
        - level 0 (SUPER_ADMIN) can manage anything
        - level 1 (EXECUTIVE) can manage anything level >= 2 across the org
        - level 2+ can only manage roles within their own department, at a
          strictly higher level number
        """
        if not (self.is_active and other_role.is_active):
            return False
        if self.level == 0:
            return True
        if self.level >= other_role.level:
            return False
        # Department scope check
        if self.level == 1:  # executive — company-wide
            return True
        if self.department and other_role.department and self.department != other_role.department:
            return False
        return True


class UserRoleAssignment(BaseModel):
    """
    A grant of a Role to a User, optionally scoped to a specific Department
    (when the user holds a role in a department other than the role's natural one).

    A user may hold multiple active assignments simultaneously (e.g. CFO
    holds SUPER_ADMIN + CFO during transition).
    """

    user             = models.ForeignKey(
                           User, on_delete=models.CASCADE,
                           related_name='role_assignments',
                       )
    role             = models.ForeignKey(
                           Role, on_delete=models.PROTECT,
                           related_name='assignments',
                       )
    scope_department = models.CharField(
                           max_length=20, choices=Department.choices,
                           null=True, blank=True,
                           help_text='If set, overrides role.department for this assignment',
                       )
    assigned_by      = models.ForeignKey(
                           User, on_delete=models.PROTECT,
                           related_name='assignments_granted',
                           null=True, blank=True,
                       )
    assigned_at      = models.DateTimeField(auto_now_add=True)
    revoked_at       = models.DateTimeField(null=True, blank=True)
    revoked_by       = models.ForeignKey(
                           User, on_delete=models.PROTECT,
                           related_name='assignments_revoked',
                           null=True, blank=True,
                       )
    revocation_reason = models.CharField(max_length=255, blank=True)

    # Governance fields (NBFIRA / audit best practice)
    justification    = models.CharField(
                           max_length=500, blank=True,
                           help_text='Business reason for the grant. REQUIRED for elevated roles (level <= 2).',
                       )
    expires_at       = models.DateTimeField(
                           null=True, blank=True,
                           help_text='If set, assignment becomes inactive after this timestamp '
                                     '(used for external auditors, contractors, break-glass access).',
                       )
    notes            = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'User Role Assignment'
        indexes      = [
            models.Index(fields=['user', 'revoked_at']),
            models.Index(fields=['role', 'revoked_at']),
        ]

    def __str__(self):
        suffix = f" / {self.get_scope_department_display()}" if self.scope_department else ''
        active = '' if self.revoked_at is None else ' [revoked]'
        return f"{self.user.username} → {self.role.code}{suffix}{active}"

    @property
    def is_currently_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if not self.role.is_active:
            return False
        if self.expires_at is not None:
            from django.utils import timezone
            if self.expires_at <= timezone.now():
                return False
        return True

    @property
    def effective_department(self):
        return self.scope_department or self.role.department


# ---------------------------------------------------------------------------
# RBAC helper functions
# ---------------------------------------------------------------------------
# These are the canonical permission-check entry points. New code should
# always go through them rather than poking at UserRoleAssignment directly.


def _active_assignments(user):
    """All currently-active role assignments for a user (cached on request)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    cached = getattr(user, '_active_role_assignments', None)
    if cached is None:
        from django.db.models import Q
        from django.utils import timezone
        now = timezone.now()
        cached = list(
            user.role_assignments
                .select_related('role')
                .prefetch_related('role__permissions')
                .filter(revoked_at__isnull=True, role__is_active=True)
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        )
        user._active_role_assignments = cached
    return cached


def user_has_permission(user, perm_code: str, department: str = None) -> bool:
    """
    True iff the user holds any active role granting `perm_code`, optionally
    constrained to a department. Super Admin (level 0) bypasses all checks.

    `perm_code` may be a literal code ('je.approve') — wildcards are deliberately
    NOT supported here to keep the check trail explicit.
    """
    # Django superuser shortcut — system root
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True

    for asg in _active_assignments(user):
        # Super Admin role grants everything
        if asg.role.level == 0:
            return True
        if perm_code in {p.code for p in asg.role.permissions.all()}:
            if department is None:
                return True
            # Department-scoped check
            if asg.effective_department in (None, department):
                return True
    return False


def user_roles(user):
    """List of Role objects the user currently holds (active)."""
    return [a.role for a in _active_assignments(user)]


def user_max_authority_level(user) -> int:
    """Lowest (highest-authority) level number across active assignments."""
    levels = [a.role.level for a in _active_assignments(user)]
    if user is not None and getattr(user, 'is_superuser', False):
        levels.append(0)
    return min(levels) if levels else 99


def user_can_manage_role(user, target_role: 'Role') -> bool:
    """
    True iff the user holds at least one role that can manage `target_role`
    per the hierarchy rules in Role.can_manage().
    """
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    for asg in _active_assignments(user):
        if asg.role.can_manage(target_role):
            return True
    return False


# ---------------------------------------------------------------------------
# Monthly Commandments cache (CFO directive 2026-05-18)
# ---------------------------------------------------------------------------
#
# Caches DeepSeek-generated "10 commandments" per (category, year-month).
# First view of a new month triggers generation; every subsequent view
# in that month serves from the row. Old months are preserved.

class MonthlyCommandments(BaseModel):
    """One row per (category, year-month). See core.commandments."""

    category     = models.CharField(
                       max_length=40,
                       help_text='Lowercase key — finance, claims, hr, it, etc.',
                   )
    year_month   = models.CharField(
                       max_length=7,
                       help_text='YYYY-MM — month the row was generated for.',
                   )
    commandments = models.JSONField(
                       default=list,
                       help_text='List of {short, long} dicts. Length=10.',
                   )
    generated_at = models.DateTimeField(auto_now_add=True)
    source       = models.CharField(max_length=20, default='deepseek')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Monthly Commandments'
        verbose_name_plural = 'Monthly Commandments'
        unique_together     = [('category', 'year_month')]
        ordering            = ['-year_month', 'category']

    def __str__(self):
        return f'{self.category} {self.year_month}'


# ---------------------------------------------------------------------------
# Admin IP allowlist (DB-backed)
# ---------------------------------------------------------------------------

class AdminAllowlistEntry(BaseModel):
    """
    A CIDR (or /32 single IP) that's permitted to reach /admin/ and
    /hris/admin/. CFO directive 2026-05-19 (Manus Final Verification
    Audit § 4): the Django admin panel must not be open to the public
    internet. Rather than ship a hard-coded env value (which would have
    locked the CFO out the moment we got it wrong), this table is
    self-service:

        GET /api/v1/admin/lock-to-my-ip/   (superuser only)

    captures the caller's leftmost X-Forwarded-For IP, inserts a row
    here, and from then on the AdminIPAllowlistMiddleware respects it.

    A row with is_active=False is ignored but kept for audit history.
    """

    cidr        = models.CharField(
                      max_length=64, unique=True,
                      help_text='IPv4/IPv6 CIDR. /32 for a single host.',
                  )
    label       = models.CharField(
                      max_length=120, blank=True, default='',
                      help_text='Where this IP belongs (e.g. "CFO desk").',
                  )
    is_active   = models.BooleanField(default=True)
    created_by  = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='admin_allowlist_entries',
                  )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Admin allowlist entry'
        verbose_name_plural = 'Admin allowlist entries'
        ordering            = ['-created_at']

    def __str__(self):
        flag = '✓' if self.is_active else '✗'
        return f'{flag} {self.cidr} ({self.label or "no label"})'


# ---------------------------------------------------------------------------
# ApiKey — long-lived service-to-service authentication
# ---------------------------------------------------------------------------

class ApiKey(BaseModel):
    """
    A permanent, scope-restricted API key for headless automation
    agents (e.g. Manus). Stored as a PBKDF2 hash so a database leak
    can't yield usable keys. The plaintext key is shown ONCE on
    creation via the management endpoint.

    Format:
      label                       — human-readable purpose
      key_prefix                  — first 8 chars of plaintext (for ID)
      key_hash                    — PBKDF2 hash of full key (verify path)
      service_user                — Django User the request authenticates as
      allowed_scopes              — list[str], e.g. ["smart-upload"]
      is_active                   — disabled keys reject all requests
      last_used_at / last_used_ip — telemetry for audit / dormant-key cleanup

    Authorization header form:
      Authorization: ApiKey <plaintext-64-hex>

    See core.api_key_auth.ApiKeyAuthentication + ApiKeyScopePermission.
    """

    class Scope(models.TextChoices):
        SMART_UPLOAD   = 'smart-upload',   'Smart Upload (preview / commit / sections / read companies + accounts)'
        READ_ONLY      = 'read-only',      'Read-only (GET only — excludes the audit log, payslips, the staff file and HR)'
        HR_EXTRACT     = 'hr-extract',     'HR data extract (read-only: HRIS, employees, payslips — no accounting data)'
        NOTEBOOK_WRITE = 'notebook-write', 'Notebook write (Claude Code — update the shared CFO notebook)'
        PO_CLAIM_READ  = 'po-claim-read',  'PO-by-claim (read-only: purchase orders for one Graphite claim)'
        GRAPHITE_EVENTS = 'graphite-events', 'Graphite inbound events (WRITE — raises invoices / bills / credit notes)'
        CLAIMS_EVENTS  = 'claims-events',  'Claims automation (Graphite claim events in + insight out; drafts and tasks only)'
        ADMIN          = 'admin',          'Admin (everything — use sparingly)'

    label         = models.CharField(max_length=120)
    key_prefix    = models.CharField(max_length=12, db_index=True)
    key_hash      = models.CharField(max_length=255)
    service_user  = models.ForeignKey(
                        User, on_delete=models.PROTECT,
                        related_name='api_keys',
                        help_text='Service-account user the key authenticates as.',
                    )
    allowed_scopes = models.JSONField(
                        default=list,
                        help_text='List of scope strings — e.g. ["smart-upload"].',
                    )
    is_active     = models.BooleanField(default=True)
    last_used_at  = models.DateTimeField(null=True, blank=True)
    last_used_ip  = models.CharField(max_length=64, blank=True, default='')
    created_by    = models.ForeignKey(
                        User, null=True, blank=True,
                        on_delete=models.SET_NULL,
                        related_name='api_keys_created',
                    )

    class Meta(BaseModel.Meta):
        verbose_name        = 'API key'
        verbose_name_plural = 'API keys'
        ordering            = ['-created_at']

    def __str__(self):
        flag = '✓' if self.is_active else '✗'
        return f'{flag} {self.label} ({self.key_prefix}…)'


# ---------------------------------------------------------------------------
# UserCompanyAccess — per-user entity allowlist (CFO directive 2026-05-22)
# ---------------------------------------------------------------------------
class UserCompanyAccess(BaseModel):
    """
    Grants a Django user access to one Alpha Direct group entity.

    Until 2026-05-22 the omni backend authenticated users but did NOT
    gate them per-entity — any logged-in user could pass ?company=<uuid>
    and pull any company's data. CFO required strict compartmentalisation:
    an ADIC user should not see ADSA / QIH / VCM unless explicitly
    granted.

    Enforcement points (see core/mixins.py:CompanyScopedViewSetMixin):
      * If the request specifies a company, the user must have a row here.
      * If no company is specified, querysets filter to the user's set.

    Bypass:
      * superusers, profile.is_administrator, title=cfo  → all companies.

    Grant lifecycle:
      can_view + can_write are separate. View = read reports + register
      entries. Write = post / upload / approve. CFO + admins write all.
    """
    user        = models.ForeignKey(
                      User, on_delete=models.CASCADE,
                      related_name='company_access',
                  )
    company     = models.ForeignKey(
                      'core.Company', on_delete=models.CASCADE,
                      related_name='user_access',
                  )
    can_view    = models.BooleanField(default=True)
    can_write   = models.BooleanField(default=False)
    granted_by  = models.ForeignKey(
                      User, null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='company_access_granted',
                  )
    notes       = models.CharField(max_length=255, blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'User company access'
        verbose_name_plural = 'User company access'
        unique_together     = [('user', 'company')]
        indexes             = [
            models.Index(fields=['user', 'company']),
        ]

    def __str__(self):
        v = 'rw' if self.can_write else ('r' if self.can_view else '-')
        return f'{self.user.username} → {self.company.code} ({v})'


def allowed_company_ids(user) -> set:
    """
    Returns the set of Company.id (UUID str) the user may access.

    The sentinel '{"*"}' means 'unrestricted' (superuser / administrator
    / CFO). Callers should treat that as "no filter".
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    if getattr(user, 'is_superuser', False):
        return {'*'}
    prof = getattr(user, 'profile', None)
    if prof is not None and (prof.is_administrator
            or (prof.title or '').lower() == 'cfo'):
        return {'*'}
    return set(str(cid) for cid in
               UserCompanyAccess.objects.filter(user=user, can_view=True)
               .values_list('company_id', flat=True))


def user_can_write_company(user, company_id) -> bool:
    """Quick write-gate check. Superusers/admins/CFO always True."""
    allowed = allowed_company_ids(user)
    if allowed == {'*'}:
        return True
    if str(company_id) not in allowed:
        return False
    return UserCompanyAccess.objects.filter(
        user=user, company_id=company_id, can_write=True,
    ).exists()


def payroll_processor_emails() -> set:
    """Emails allowed to PROCESS payroll (upload + commit) for their OWN
    entity only — an operational payroll processor who is NOT a Finance
    manager. Their company access still limits which entity they may touch,
    and the approval (sign-off) step remains a separate person.

    Override with env OMNI_PAYROLL_PROCESSOR_EMAILS or
    settings.PAYROLL_PROCESSOR_EMAILS (comma-separated). Defaults to the
    Veritas / Motor Liquidators processor (CFO directive 2026-07-23:
    Tshephang processes Veritas payroll, Unami signs off, CFO pays).
    """
    import os
    from django.conf import settings
    raw = os.environ.get('OMNI_PAYROLL_PROCESSOR_EMAILS')
    if raw is None:
        raw = getattr(settings, 'PAYROLL_PROCESSOR_EMAILS', '') or ''
    vals = list(raw) if isinstance(raw, (list, tuple, set)) else str(raw).split(',')
    emails = {e.strip().lower() for e in vals if e.strip()}
    return emails or {'tshephang@motorliquidators.co.bw'}


def is_payroll_processor(user) -> bool:
    """True when `user` is a designated entity payroll processor."""
    if not (user and getattr(user, 'is_authenticated', False)
            and getattr(user, 'is_active', True)):
        return False
    return (getattr(user, 'email', '') or '').strip().lower() in payroll_processor_emails()


# ---------------------------------------------------------------------------
# Internal tasking + presence (CFO directive 2026-05-24).
#
# Phase 1 — replace internal email with a peer-to-peer task inbox. Every
# logged-in user can hand a task to any other user. Privacy: the recipient
# sees only tasks given to them; the sender sees only tasks they gave.
# Status transitions: pending → in_progress → done | partial | blocked.
# Audit trail of comments + status changes lives on OmniTaskComment.
#
# OnlinePresence is heartbeat-bumped on every authenticated request via
# core.middleware.PresenceHeartbeatMiddleware. /api/v1/presence/online/
# returns users seen within the past 5 minutes.
# ---------------------------------------------------------------------------

class OmniTask(BaseModel):
    """A single task handed from one omni user to another."""

    class Priority(models.TextChoices):
        LOW    = "low",    "Low"
        NORMAL = "normal", "Normal"
        HIGH   = "high",   "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        PENDING     = "pending",     "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        DONE        = "done",        "Done"
        PARTIAL     = "partial",     "Partially complete"
        BLOCKED     = "blocked",     "Blocked"
        CANCELLED   = "cancelled",   "Cancelled"

    assigner     = models.ForeignKey(
                       User, on_delete=models.PROTECT,
                       related_name="tasks_assigned",
                   )
    assignee     = models.ForeignKey(
                       User, on_delete=models.PROTECT,
                       related_name="tasks_received",
                   )
    title        = models.CharField(max_length=200)
    body         = models.TextField(blank=True, default="")
    due_at       = models.DateField(null=True, blank=True)
    # Time-of-day the task is due ON due_at (CFO 2026-07-13: "all tasks due at
    # 4pm"). Africa/Gaborone. Null = end-of-day / no specific time. A task is
    # overdue once past due_at + due_time, not merely after midnight.
    due_time     = models.TimeField(null=True, blank=True)
    priority     = models.CharField(
                       max_length=10, choices=Priority.choices,
                       default=Priority.NORMAL,
                   )
    status       = models.CharField(
                       max_length=15, choices=Status.choices,
                       default=Status.PENDING, db_index=True,
                   )
    completed_at = models.DateTimeField(null=True, blank=True)
    # When recipient first sees the task — drives the inbox unread badge.
    seen_at      = models.DateTimeField(null=True, blank=True)
    # Weekly-planning support (CFO 2026-07-13): the Monday the task belongs to,
    # so the CFO dashboard + personal boards can group "this week's plan". Set by
    # the planning-meeting ingest; null for ad-hoc tasks.
    week_of      = models.DateField(null=True, blank=True, db_index=True)
    source       = models.CharField(
                       max_length=30, blank=True, default="",
                       help_text="e.g. 'planning_meeting' when auto-created from the weekly plan.",
                   )
    # Idempotency key for create-once (Omni Mobile Quick Task, Workstream G): a
    # client-generated UUID so a double-tap / network retry resolves to the SAME
    # task instead of a duplicate. Scoped per-assigner in the create view; blank
    # for server-created / legacy rows.
    client_key   = models.CharField(
                       max_length=64, blank=True, default="", db_index=True,
                       help_text="Client idempotency key (Quick Task); blank = not set.",
                   )
    # How complete the task is, 0-100 (CFO 2026-07-13). Set from the dashboard
    # status control: Done=100, Partially done=25/50/75/100, Not done=0. Null =
    # never reported (distinct from an explicit 0).
    completion_pct = models.PositiveSmallIntegerField(
                       null=True, blank=True,
                       help_text="Percent complete (0-100). Null = not yet reported.",
                   )
    # Staff-rewards gamification (CFO 2026-07-13): the points this task has
    # credited to the assignee's Staff Rewards score so far. When the assigner
    # (CFO) changes their Done/Partial/Not-done feedback, staff_rewards reconciles
    # the delta against this figure so re-feedback never double-counts — and a
    # task flipped back to Not-done forfeits the points it previously earned.
    performance_points_awarded = models.IntegerField(
                       default=0,
                       help_text="Staff-rewards points this task has credited so far.",
                   )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        indexes  = [
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["assigner", "status"]),
            models.Index(fields=["week_of"]),
        ]
        verbose_name        = "Omni task"
        verbose_name_plural = "Omni tasks"

    def __str__(self):
        return f"{self.title} ({self.assigner.username} → {self.assignee.username})"

    # TASK-DATE-01 (CFO 2026-08-15, Manus QC F4): a Payment Authorisation task
    # for BWP 66,880.13 sat with due_at = 0206-08-01 (year 206) — the year got
    # mis-parsed on write and there was no sanity fence, so the task looked
    # 1800+ years overdue on every board.
    #
    # The guard rejects a nonsense YEAR, not a "distance from today". A ±5y
    # window would look tighter but breaks two legitimate uses already in prod:
    #   - hris.ghost_payroll uses far-future sentinels (year 2099) for tasks
    #     that must never age out.
    #   - the Graphite refund importer passes a datetime (not a date) here.
    # Rule: year in [1900, today.year + 100]. That still catches the real
    # class of bug — a truncated / mis-parsed year like 0206 — while letting
    # sentinels through. Coerce datetime → date so a naive < comparison works.
    def save(self, *args, **kwargs):
        if self.due_at is not None:
            import datetime as _dt

            from django.core.exceptions import ValidationError
            from django.utils import timezone as dj_tz

            due = self.due_at
            if isinstance(due, _dt.datetime):
                due = due.date()
            today = dj_tz.localdate()
            if not (1900 <= due.year <= today.year + 100):
                raise ValidationError({
                    'due_at': (
                        f"Task due date {due.isoformat()} has a nonsense year "
                        f"(expected 1900 to {today.year + 100}). This usually "
                        "means a date-parse bug on the source record — fix it "
                        "there. (TASK-DATE-01)"
                    )
                })
            # Normalise so downstream reads always see a date.
            self.due_at = due
        super().save(*args, **kwargs)


class OmniTaskComment(BaseModel):
    """Per-task append-only audit row (status changes + free-form replies)."""

    task        = models.ForeignKey(
                      OmniTask, on_delete=models.CASCADE,
                      related_name="comments",
                  )
    author      = models.ForeignKey(User, on_delete=models.PROTECT)
    body        = models.TextField(blank=True, default="")
    new_status  = models.CharField(
                      max_length=15, choices=OmniTask.Status.choices,
                      blank=True, default="",
                      help_text="If set, this comment also transitions the task.",
                  )
    # Evidence for a completion or a blocker (CFO 2026-07-13: "explain why you
    # cannot complete, with evidence"). Optional per comment; the API requires it
    # (or a reason) when a task is marked BLOCKED / PARTIAL.
    evidence    = models.FileField(
                      upload_to="task_evidence/", null=True, blank=True,
                      help_text="Proof of completion or of the blocker (screenshot / doc). Max 10 MB.",
                  )

    class Meta(BaseModel.Meta):
        ordering = ["created_at"]
        verbose_name        = "Omni task comment"
        verbose_name_plural = "Omni task comments"


class BriefNote(models.Model):
    """A 25-word note from a staff member that lands in a morning brief.

    CFO 2026-09-10: "we want to limit commnications and wasting time of the
    CEO". Two spaces — one feeding the CEO's 06:30 brief (the seven named
    executives), one feeding the CFO's (his named staff plus ADIC / Unicoin /
    Veritas / Risksoftware employees). Who may write where lives in
    core.brief_note_access.

    THE LIMIT IS THE FEATURE. One note per person, per space, per morning,
    capped at 25 words. That cap is enforced here in clean() and again by the
    unique constraint below, so no view, shell or import can route around it —
    if it could, this stops being a digest and becomes another inbox.

    Notes stay editable and withdrawable right up until the brief is sent; once
    `sent_at` is stamped the row is frozen, because the CEO has already read it
    and history must not change underneath him.
    """

    WORD_LIMIT = 25

    class Audience(models.TextChoices):
        CEO = "ceo", "CEO morning brief"
        CFO = "cfo", "CFO morning brief"

    class Status(models.TextChoices):
        QUEUED    = "queued",    "Queued for the next brief"
        SENT      = "sent",      "Delivered in the brief"
        WITHDRAWN = "withdrawn", "Withdrawn before sending"

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audience   = models.CharField(max_length=3, choices=Audience.choices, db_index=True)
    author     = models.ForeignKey(User, on_delete=models.PROTECT,
                                   related_name="brief_notes")
    body       = models.CharField(max_length=400)
    #: The brief this note belongs to — NOT created_at. A note written at 23:50
    #: is for tomorrow's brief, and the brief must be able to ask for "today's"
    #: notes without a timezone argument.
    for_date   = models.DateField(db_index=True)
    status     = models.CharField(max_length=10, choices=Status.choices,
                                  default=Status.QUEUED, db_index=True)
    sent_at    = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-for_date", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["audience", "author", "for_date"],
                name="one_brief_note_per_person_per_morning",
            ),
        ]
        indexes = [models.Index(fields=["audience", "for_date", "status"])]
        verbose_name        = "Brief note"
        verbose_name_plural = "Brief notes"

    def __str__(self):
        return f"{self.author.username} -> {self.audience} ({self.for_date})"

    @staticmethod
    def word_count(text: str) -> int:
        return len((text or "").split())

    @property
    def is_locked(self) -> bool:
        """Sent notes are history. Nobody edits them, including the author."""
        return self.status == self.Status.SENT or self.sent_at is not None

    def clean(self):
        from django.core.exceptions import ValidationError
        body = (self.body or "").strip()
        if not body:
            raise ValidationError({"body": "Write something, or withdraw the note."})
        n = self.word_count(body)
        if n > self.WORD_LIMIT:
            raise ValidationError({"body": (
                f"Keep it to {self.WORD_LIMIT} words — that is the whole point. "
                f"This is {n}."
            )})
        self.body = body

    def save(self, *args, **kwargs):
        self.full_clean(exclude=["author"])
        super().save(*args, **kwargs)


class StuckWorkEscalation(BaseModel):
    """One row per (stuck-work queue, day) the CFO was alarmed about.

    Makes core.stuck_work.sweep() idempotent for the day: a duplicate cron entry,
    a manual re-run or a retry must not re-alarm. Same claim-row trick as
    taskboard.TaskReminderEmailLog, and for the same reason.
    """

    watcher_key = models.CharField(max_length=40, db_index=True)
    sent_on     = models.DateField(db_index=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=['watcher_key', 'sent_on'],
                                    name='uniq_stuck_escalation_per_day'),
        ]
        verbose_name        = 'Stuck-work escalation'
        verbose_name_plural = 'Stuck-work escalations'

    def __str__(self):
        return f'{self.watcher_key} @ {self.sent_on}'


class TaskFeedback(BaseModel):
    """CFO (or a manager) performance feedback on a task — usually raised when a
    task is unfinished/overdue. CFO directive 2026-07-13: 'a button that lets me
    give immediate performance feedback from the dashboard'. Shows on the
    assignee's personal dashboard; they must acknowledge; it feeds the ELRA
    performance check-ins (hris.PerformanceReview) at review time.
    """
    task         = models.ForeignKey(
                       OmniTask, on_delete=models.CASCADE, related_name="feedback",
                   )
    from_user    = models.ForeignKey(
                       User, on_delete=models.PROTECT, related_name="task_feedback_given",
                   )
    to_user      = models.ForeignKey(
                       User, on_delete=models.PROTECT, related_name="task_feedback_received",
                   )
    body         = models.TextField(help_text="The feedback message shown to the employee.")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        indexes  = [models.Index(fields=["to_user", "acknowledged_at"])]
        verbose_name        = "Task feedback"
        verbose_name_plural = "Task feedback"


class OnlinePresence(BaseModel):
    """Heartbeat row — one per user. Updated on every authenticated request."""

    user      = models.OneToOneField(
                    User, on_delete=models.CASCADE,
                    related_name="presence",
                )
    last_seen = models.DateTimeField(db_index=True)

    class Meta(BaseModel.Meta):
        verbose_name        = "Online presence"
        verbose_name_plural = "Online presence"

    def __str__(self):
        return f"{self.user.username} @ {self.last_seen.isoformat()}"


# ---------------------------------------------------------------------------
# ARIA persistent chat (CFO directive 2026-05-24)
#
# Two-table conversation log so the front-end can re-open a previous
# ARIA thread instead of starting from scratch every time. Stores:
#   * AriaConversation — the thread (mood + activity timestamps)
#   * AriaMessage      — append-only message rows, including tool calls
#
# Designed to survive the function-calling tool-use loop (see
# `core/ai_assist.py::aria_chat`): every iteration's tool request +
# tool result is persisted as its own AriaMessage row.
# ---------------------------------------------------------------------------

class AriaConversation(models.Model):
    """One ARIA chat thread."""

    id                = models.UUIDField(
                            primary_key=True, default=uuid.uuid4, editable=False,
                        )
    user              = models.ForeignKey(
                            User, on_delete=models.CASCADE,
                            related_name="aria_conversations",
                        )
    started_at        = models.DateTimeField(auto_now_add=True)
    mood              = models.CharField(max_length=20, default="sharp")
    last_activity_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_activity_at"]
        indexes  = [
            models.Index(fields=["user", "-last_activity_at"]),
        ]
        verbose_name        = "ARIA conversation"
        verbose_name_plural = "ARIA conversations"

    def __str__(self):
        return f"ARIA[{self.user.username} @ {self.started_at.isoformat()}]"


class AriaMessage(models.Model):
    """Single message in an ARIA conversation. Append-only."""

    ROLE_CHOICES = (
        ("user",      "User"),
        ("assistant", "Assistant"),
        ("tool",      "Tool"),
        ("system",    "System"),
    )

    id              = models.UUIDField(
                          primary_key=True, default=uuid.uuid4, editable=False,
                      )
    conversation    = models.ForeignKey(
                          AriaConversation, on_delete=models.CASCADE,
                          related_name="messages",
                      )
    role            = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content         = models.TextField(blank=True, default="")
    tool_calls      = models.JSONField(blank=True, default=dict)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes  = [
            models.Index(fields=["conversation", "created_at"]),
        ]
        verbose_name        = "ARIA message"
        verbose_name_plural = "ARIA messages"

    def __str__(self):
        return f"{self.role}@{self.created_at.isoformat()}"


class AriaPopup(models.Model):
    """A popup message sent via Aria from the CFO to any staff member.

    Shows as a large overlay on the recipient's screen the next time they
    are on Omni. The recipient dismisses it with a button. CFO-only feature.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='aria_popups_sent')
    recipient   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='aria_popups_received')
    message     = models.TextField()
    is_read     = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    read_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', 'created_at']),
        ]
        verbose_name = 'Aria Popup'


# ---------------------------------------------------------------------------
# Intercompany Account Policy (FA-001 — CFO directive 2026-05-29)
# ---------------------------------------------------------------------------

class IntercompanyAccountPolicy(BaseModel):
    """Singleton — GL codes used by intercompany flows (FA-001 transfers,
    future intercompany loans, etc.).

    Fail-safe: while either code is blank, intercompany asset transfers
    cannot post. Mirrors the BillApprovalPolicy "PLACEHOLDER until CFO
    confirms" pattern.
    """
    receivable_account_code = models.CharField(
        max_length=20, blank=True, default='',
        help_text='GL code for "Due from related companies" — debited in the '
                  'sender JE of an intercompany asset transfer.',
    )
    payable_account_code    = models.CharField(
        max_length=20, blank=True, default='',
        help_text='GL code for "Due to related companies" — credited in the '
                  'receiver JE of an intercompany asset transfer.',
    )
    notes                   = models.TextField(blank=True, default='')
    updated_by              = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='intercompany_policy_updates',
    )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Intercompany Account Policy'
        verbose_name_plural = 'Intercompany Account Policy'

    def __str__(self):
        return (
            f"IC accounts: recv={self.receivable_account_code or '—'} "
            f"pay={self.payable_account_code or '—'}"
        )

    @classmethod
    def current(cls) -> 'IntercompanyAccountPolicy':
        row = cls.objects.order_by('created_at').first()
        if row is None:
            row = cls.objects.create()
        return row

    @property
    def is_configured(self) -> bool:
        return bool(self.receivable_account_code and self.payable_account_code)


# ---------------------------------------------------------------------------
# CFO Secrets Vault — encrypted credential store (CFO directive 2026-06)
# ---------------------------------------------------------------------------
class VaultSecret(BaseModel):
    """A single secret in the CFO-only vault (HRIS / portal / integration
    passwords + API credentials). The secret value is encrypted at rest with
    Fernet (see core/vault_crypto.py); plaintext is never stored and is only
    returned by the explicit, audited reveal endpoint. Visible to CFO /
    administrator / superuser only — enforced in core/vault_views.py."""

    class Category(models.TextChoices):
        HRIS     = 'hris',     'HRIS'
        PORTAL   = 'portal',   'Portal / Paygate'
        API      = 'api',      'API / Integration'
        DATABASE = 'database', 'Database'
        EMAIL    = 'email',    'Email / M365'
        CLOUD    = 'cloud',    'Cloud / Infra'
        OTHER    = 'other',    'Other'

    name              = models.CharField(max_length=160, unique=True,
                                         help_text='Label, e.g. "HRIS unlock password" or "RealPay 16244 login".')
    category          = models.CharField(max_length=12, choices=Category.choices,
                                         default=Category.OTHER)
    username          = models.CharField(max_length=200, blank=True, default='',
                                         help_text='Optional username / account (not secret).')
    url               = models.CharField(max_length=300, blank=True, default='')
    notes             = models.TextField(blank=True, default='')
    secret_ciphertext = models.TextField(blank=True, default='',
                                         help_text='Fernet-encrypted secret value. Never plaintext.')
    created_by        = models.ForeignKey(User, null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name='vault_secrets_created')
    last_revealed_at  = models.DateTimeField(null=True, blank=True)
    last_revealed_by  = models.ForeignKey(User, null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name='vault_secrets_revealed')

    class Meta(BaseModel.Meta):
        ordering            = ['category', 'name']
        verbose_name        = 'Vault Secret'
        verbose_name_plural = 'Vault Secrets'

    def __str__(self):
        return f'{self.get_category_display()} · {self.name}'

    def set_secret(self, plaintext: str) -> None:
        from core.vault_crypto import encrypt
        self.secret_ciphertext = encrypt(plaintext or '')

    def reveal(self) -> str:
        from core.vault_crypto import decrypt
        return decrypt(self.secret_ciphertext)


# ---------------------------------------------------------------------------
# Team chat — lightweight in-app chatroom (CFO directive 2026-06-09)
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    """One message in the in-app team chat. Single shared room ('general')
    for now — everyone logged in sees it. Polled (omni is WSGI, no websockets).
    A message whose body is '/task @user <desc>' also creates an OmniTask for
    that user (handled in core/chat_views) and links it here, so chat can drive
    the assignee's task inbox/dashboard."""
    room    = models.CharField(max_length=40, default='general', db_index=True)
    sender  = models.ForeignKey(User, on_delete=models.CASCADE,
                                related_name='chat_messages')
    body    = models.TextField()
    is_task = models.BooleanField(default=False)
    task    = models.ForeignKey('core.OmniTask', null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='chat_messages')
    # Bug e79e4166 (Oprah 2026-06-17): senders can edit/delete their own messages.
    # Soft-delete keeps the row (and any task link) for audit; edited_at stamps edits.
    edited_at  = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta(BaseModel.Meta):
        ordering = ['created_at']
        indexes  = [models.Index(fields=['room', 'created_at'])]
        verbose_name = 'Chat Message'
        verbose_name_plural = 'Chat Messages'

    def __str__(self):
        return f'{self.sender_id}: {self.body[:40]}'


class BugReport(BaseModel):
    """A user-submitted system bug report (CFO directive 2026-06-10 — give
    staff a single 'Report a System Bug' channel so they stop emailing the CFO
    directly). The /report-bug page requires >=50 words + >=3 screenshots; on
    submit the report is stored here AND emailed to excoboard@ with the
    screenshots attached. The screenshots themselves are NOT persisted to the
    DB/media (they ride the email only) — this row is the audit trail.
    """

    class Status(models.TextChoices):
        NEW         = 'new',         'New'
        TRIAGED     = 'triaged',     'Triaged'
        IN_PROGRESS = 'in_progress', 'In Progress'
        RESOLVED    = 'resolved',    'Resolved'
        WONT_FIX    = 'wont_fix',    "Won't Fix"

    reporter         = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bug_reports')
    reporter_email   = models.EmailField(blank=True)   # snapshot at submit time
    description      = models.TextField()
    word_count       = models.PositiveIntegerField(default=0)
    screenshot_count = models.PositiveIntegerField(default=0)
    page_url         = models.CharField(max_length=500, blank=True)
    status           = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    emailed_ok       = models.BooleanField(default=False)
    # Triage / feedback loop (CFO directive 2026-06-10): admins move status +
    # leave a resolution note; the reporter sees status in-app and gets an email
    # when it changes.
    resolution_note  = models.TextField(blank=True)
    resolved_at      = models.DateTimeField(null=True, blank=True)
    triaged_by       = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bug_reports_triaged')
    # AI-triage queue (CFO directive 2026-06-10). An admin clicks "Request AI
    # fix" → triage_requested=True. The off-box triage runner polls these,
    # produces a DRAFT PR (never deploys), writes triage_pr_url back and clears
    # the flag. A human still reviews/merges/deploys.
    triage_requested     = models.BooleanField(default=False, db_index=True)
    triage_requested_at  = models.DateTimeField(null=True, blank=True)
    triage_requested_by  = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bug_reports_triage_requested')
    triage_pr_url        = models.CharField(max_length=500, blank=True)
    # Manus QC channel (CFO directive 2026-08-29) — a SEPARATE pickup queue from
    # the off-box triage runner above, so the two never step on each other. An
    # admin clicks "Send to Manus for QC" → qc_requested=True. Manus polls the
    # flagged items through its own scoped key ('qc-manus'), does the QC, and
    # posts findings back via POST /bug-reports/<id>/qc-result/: the pickup
    # stamp, a plain finding note and the draft-PR link. Manus never deploys — a
    # human still reviews and merges.
    qc_requested     = models.BooleanField(default=False, db_index=True)
    qc_requested_at  = models.DateTimeField(null=True, blank=True)
    qc_requested_by  = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bug_reports_qc_requested')
    qc_picked_up_at  = models.DateTimeField(null=True, blank=True)
    qc_result_note   = models.TextField(blank=True)
    qc_result_pr_url = models.CharField(max_length=500, blank=True)
    qc_result_at     = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Bug Report'
        verbose_name_plural = 'Bug Reports'
        constraints = [
            # The board's open queue is "everything that is not resolved and not
            # wont_fix", so ANY status outside the five choices is open forever:
            # invisible to every filter and impossible to clear from the screen.
            # One row reached status='closed' this way (kbotana 2026-08-06, closed
            # on the CFO's confirmation) and sat on the open board for four days.
            # The API already rejects an unknown status; a shell or a data script
            # does not, because CharField.choices is only enforced by full_clean().
            # A database check closes every write path at once.
            models.CheckConstraint(
                check=models.Q(status__in=['new', 'triaged', 'in_progress',
                                           'resolved', 'wont_fix']),
                name='bugreport_status_is_a_real_choice',
            ),
        ]

    def __str__(self):
        return f'BugReport {self.id} ({self.status}) by {self.reporter_email or "unknown"}'


# ---------------------------------------------------------------------------
# Frozen-component governance (Oprah / Internal Audit, 2026-06-23)
#
# Makes the "frozen ADIC P&L" rule a SYSTEM control, not an informal agreement:
#   - FrozenComponent       registry of the locked components (is_frozen=True)
#   - FrozenChangeRequest    maker-checker — file → CFO approve/reject + comment
# Every attempt + outcome is written to the immutable AuditLog. The guard +
# logging live in core/frozen_controls.py. Replaces the shared override
# password for frozen-figure changes (CFO directive 2026-06-23); the separate
# closed-period JE lock in ledger/locks.py is untouched.
# ---------------------------------------------------------------------------

class FrozenComponent(BaseModel):
    """A part of the system the CFO has frozen. Edits require an approved
    FrozenChangeRequest. Seeded with the three components Internal Audit named:
    the ADIC MA P&L layout, the revenue mapping, and the GWP figure."""

    class Key(models.TextChoices):
        ADIC_MA_PL_LAYOUT    = 'adic_ma_pl_layout',    'ADIC MA P&L layout'
        ADIC_REVENUE_MAPPING = 'adic_revenue_mapping', 'ADIC revenue mapping'
        ADIC_GWP_FIGURE      = 'adic_gwp_figure',      'ADIC GWP figure'

    key         = models.CharField(max_length=40, choices=Key.choices, unique=True)
    label       = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    is_frozen   = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Frozen Component'
        verbose_name_plural = 'Frozen Components'
        ordering = ['label']

    def __str__(self):
        return f'{self.label} ({"frozen" if self.is_frozen else "open"})'


class FrozenChangeRequest(BaseModel):
    """Maker-checker request to change a frozen component. Finance/admins file
    it (what / why / board impact); the CFO approves or rejects with a mandatory
    comment. Immutable once decided. An approved request authorises ONE change
    and is then marked consumed."""

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending CFO decision'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    component        = models.ForeignKey(
                           FrozenComponent, on_delete=models.PROTECT,
                           related_name='change_requests',
                       )
    summary          = models.CharField(max_length=200)   # what is changing
    reason           = models.TextField()                 # why
    board_impact     = models.TextField()                 # impact on board figures
    status           = models.CharField(
                           max_length=10, choices=Status.choices,
                           default=Status.PENDING, db_index=True,
                       )
    requested_by     = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='frozen_change_requests_made',
                       )
    decided_by       = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='frozen_change_requests_decided',
                       )
    decision_comment = models.TextField(blank=True, default='')   # mandatory on decide
    decided_at       = models.DateTimeField(null=True, blank=True)
    consumed         = models.BooleanField(default=False)   # approved + used once

    class Meta(BaseModel.Meta):
        verbose_name = 'Frozen Change Request'
        verbose_name_plural = 'Frozen Change Requests'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.component.label}: {self.summary[:40]} [{self.status}]'


# ---------------------------------------------------------------------------
# Helpdesk Comment
# ---------------------------------------------------------------------------

class HelpdeskComment(models.Model):
    """
    Django-side comment on an IT Help Desk ticket.

    Ticket IDs mirror the SharePoint TicketID field (e.g. "TKT-0001").
    Written by management commands (helpdesk_add_comment) running via SSM
    and surfaced in the Help Desk SPA at /helpdesk/ via the read-only
    /api/helpdesk/comments/?ticket=TKT-0001 endpoint.
    """
    ticket_id  = models.CharField(max_length=20, db_index=True)
    author     = models.CharField(max_length=120, default="Prathap Ganesharajah")
    text       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    source     = models.CharField(max_length=30, default="claude_code")

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.ticket_id} [{self.created_at:%Y-%m-%d %H:%M}]"


class EmailLoginCode(models.Model):
    """One-time email code (2nd factor) for the staff email/password login.

    Emailed after a correct password; must be entered to finish sign-in.
    Stored HASHED (sha256), single-use, short-lived, attempt-capped. Issuing a
    new code for an email invalidates that email's earlier unconsumed codes.
    CFO directive 2026-07-07 (non-SSO staff login).
    """
    class Purpose(models.TextChoices):
        SIGN_IN = 'sign_in', 'Sign in'
        RESET   = 'reset',   'Password reset'

    email      = models.EmailField(db_index=True)
    code_hash  = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts   = models.PositiveSmallIntegerField(default=0)
    consumed   = models.BooleanField(default=False)
    # SEC-05 (security assessment 2026-08-06): a code used to carry no record of
    # what it was issued FOR, and both the sign-in check and the password-reset
    # check simply took the newest unconsumed code for the email. So a code a
    # colleague was talked into reading out — believing it only let someone look
    # at their screen — could be spent on the reset endpoint instead, setting a
    # new password and taking the account for good. Binding the purpose turns
    # that from a permanent takeover into one failed sign-in.
    purpose    = models.CharField(max_length=16, choices=Purpose.choices,
                                  default=Purpose.SIGN_IN, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['email', 'consumed']),
                   models.Index(fields=['email', 'purpose', 'consumed'])]

    def __str__(self):
        return f"{self.email} ({'used' if self.consumed else 'active'})"


class PrivacyNoticeAcknowledgement(BaseModel):
    """A staff member's signed acknowledgement of a versioned privacy notice.

    Shown as a login pop-up (see core/privacy_notice.py). One row per
    (user, version); bump NOTICE_VERSION to require everyone to sign again.
    This is the consent record for Omni's task-completion monitoring under the
    Botswana Data Protection Act 2024 (CFO directive 2026-07-09)."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="privacy_acknowledgements")
    version = models.CharField(max_length=32)
    # Snapshot of the name shown at signing, for the audit trail.
    signed_name = models.CharField(max_length=200, blank=True, default="")

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["user", "version"],
                name="uniq_privacy_ack_user_version"),
        ]
        indexes = [models.Index(fields=["version"], name="core_privack_ver_idx")]

    def __str__(self):
        return f"user {self.user_id} signed privacy notice {self.version}"


# Manual "What's New" store — kept in a separate module for tidiness; imported
# here so Django discovers the models (mirrors payroll.contract_models).
from .manual_models import (   # noqa: E402,F401
    ManualFeatureEntry, ManualUpdateRun,
)


class AISpeedLog(models.Model):
    """Per-call timing of external-AI (reasoning) calls — the 'speed log'
    (CFO directive 2026-07-19). Proves response times to the auditor and flags
    when the model is slowing down. Written best-effort from
    core.ai_assist.reasoning_complete; a logging failure NEVER breaks the AI
    call. Timing + engine only — no prompt or answer content is ever stored."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    feature    = models.CharField(max_length=64, blank=True, default='')
    engine     = models.CharField(max_length=32)             # Ollama / DeepSeek / Gemini / ...
    model_name = models.CharField(max_length=80, blank=True, default='')
    ms         = models.PositiveIntegerField()               # wall-clock milliseconds
    ok         = models.BooleanField(default=True)
    escalated  = models.BooleanField(default=False)          # cheap tier failed → escalated

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'AI speed log'

    def __str__(self):
        return f'{self.engine} {self.ms}ms {"ok" if self.ok else "fail"}'


class DpoChecklistRun(models.Model):
    """Monthly COMPULSORY data-protection checklist the DPO must complete + submit
    (covers Graphite + omni). C-suite can see it; an overdue run sits on the CFO /
    C-suite Data Protection dashboard and can be converted to a disciplinary action
    in one click. CFO directive 2026-07-19. Template lives in core.dpa_checklist."""

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        SUBMITTED = 'submitted', 'Submitted'
        OVERDUE   = 'overdue',   'Overdue'

    period       = models.CharField(max_length=7, unique=True, db_index=True)  # 'YYYY-MM'
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    due_date     = models.DateField()
    responses    = models.JSONField(default=dict, blank=True)   # {item_key: {response, note}}
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey('auth.User', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    # One-click disciplinary escalation (CFO 2026-07-19) — the task raised for an
    # overdue run; presence = "disciplinary action raised".
    disciplinary_task = models.ForeignKey('core.OmniTask', null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-period']
        verbose_name = 'DPO checklist run'

    def __str__(self):
        return f'DPO checklist {self.period} ({self.status})'


class BreachIncident(models.Model):
    """Data-breach / security-incident register (DPA audit S-6). Logged by the DPO /
    C-suite; drives the 72-hour IDPC-notification clock. CFO directive 2026-07-19."""

    class Severity(models.TextChoices):
        LOW = 'low', 'Low'; MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'; CRITICAL = 'critical', 'Critical'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'; CONTAINED = 'contained', 'Contained'; CLOSED = 'closed', 'Closed'

    title             = models.CharField(max_length=200)
    description       = models.TextField(blank=True, default='')
    discovered_at     = models.DateTimeField()
    severity          = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM)
    reportable        = models.BooleanField(default=True)   # notifiable to the IDPC?
    status            = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    idpc_notified     = models.BooleanField(default=False)
    idpc_notified_at  = models.DateTimeField(null=True, blank=True)
    subjects_notified = models.BooleanField(default=False)
    remedial          = models.TextField(blank=True, default='')
    created_by        = models.ForeignKey('auth.User', null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-discovered_at']
        verbose_name = 'Breach incident'

    def __str__(self):
        return f'{self.title} ({self.severity}/{self.status})'

    @property
    def notify_deadline(self):
        from datetime import timedelta
        return self.discovered_at + timedelta(hours=72) if self.discovered_at else None

    @property
    def hours_left(self):
        """Hours to the 72-hour IDPC deadline (negative = past). None if not applicable."""
        from django.utils import timezone
        if not self.reportable or self.idpc_notified or self.status == self.Status.CLOSED or not self.discovered_at:
            return None
        return round((self.notify_deadline - timezone.now()).total_seconds() / 3600, 1)

    @property
    def overdue(self):
        hl = self.hours_left
        return hl is not None and hl < 0


class DataSubjectRequest(models.Model):
    """A data-subject request — access / correct / delete / restrict / object /
    portability — with the statutory response clock (DPA audit H-3). Logged by the
    DPO/HR; C-suite can see it on the Data Protection dashboard. CFO 2026-07-20."""

    class Kind(models.TextChoices):
        ACCESS = 'access', 'Access'; RECTIFY = 'rectify', 'Rectification'
        ERASE = 'erase', 'Erasure'; RESTRICT = 'restrict', 'Restriction'
        OBJECT = 'object', 'Objection'; PORTABILITY = 'portability', 'Portability'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'; IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'; REJECTED = 'rejected', 'Rejected'

    STATUTORY_DAYS = 30   # DPA response window — policy default; confirm with legal.

    subject_name  = models.CharField(max_length=200)
    subject_email = models.CharField(max_length=254, blank=True, default='')
    subject_type  = models.CharField(max_length=20, default='customer')  # customer|staff
    kind          = models.CharField(max_length=15, choices=Kind.choices, default=Kind.ACCESS)
    details       = models.TextField(blank=True, default='')
    status        = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    received_at   = models.DateTimeField()
    due_date      = models.DateField()
    resolution    = models.TextField(blank=True, default='')
    handled_by    = models.ForeignKey('auth.User', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='+')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'Data-subject request'

    def __str__(self):
        return f'{self.kind} — {self.subject_name} ({self.status})'

    @property
    def days_left(self):
        if self.status in (self.Status.COMPLETED, self.Status.REJECTED) or not self.due_date:
            return None
        from django.utils import timezone
        return (self.due_date - timezone.localdate()).days

    @property
    def overdue(self):
        dl = self.days_left
        return dl is not None and dl < 0


class PushSubscription(models.Model):
    """A browser/PWA Web Push subscription for the approvals nudge (CFO
    2026-07-23). One per device; the same user can have several. Stored keys are
    the standard endpoint + p256dh + auth the Push API hands back on subscribe."""
    user       = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name='push_subscriptions')
    endpoint   = models.URLField(max_length=600, unique=True)
    p256dh     = models.CharField(max_length=200)
    auth       = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"push:{self.user_id}:{self.endpoint[-12:]}"


class DpoWorkbook(models.Model):
    """Latest DPO compliance workbook (Oratile's DPA-2024 framework xlsx), parsed
    to JSON for the Data Protection dashboard. Reuse-not-rebuild (CFO 2026-07-24):
    the DPO maintains the workbook offline and uploads it; we parse every sheet,
    display it, and flag the gaps — no per-sheet models to keep in sync.

    Only the most recent upload is shown (ordered -created_at). The Training /
    Quiz sheets carry staff PII (names/emails) — stored server-side only; the
    dashboard renders those as AGGREGATES, never the raw list to the C-suite."""

    file        = models.FileField(upload_to='dpo_workbook/', null=True, blank=True)
    file_name   = models.CharField(max_length=200, blank=True, default='')
    uploaded_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    sheets      = models.JSONField(default=dict)   # {sheet: {headers:[...], rows:[{col:val}]}}
    summary     = models.JSONField(default=dict)   # computed counts + gaps
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'DPO Workbook'

    def __str__(self):
        return f"DPO workbook {self.file_name} ({self.created_at:%Y-%m-%d})"


class ComplianceBrainSummary(models.Model):
    """Nightly PII-FREE compliance summary fetched from Alpha Brain (the Graphite
    bolt-on) + our own AI's plain-English read of it. One row per nightly run;
    the Compliance/AML dashboard shows the latest. No customer data is stored —
    only aggregate counts (CFO directive 2026-07-24)."""
    created_at   = models.DateTimeField(auto_now_add=True)
    as_of        = models.DateField()
    source_url   = models.CharField(max_length=300, blank=True, default='')
    fetched_ok   = models.BooleanField(default=False)
    counts       = models.JSONField(default=dict)   # {team: n} aggregate from the brain (PII-free)
    kyc          = models.JSONField(default=dict)   # KYC by category/broker + claims/no-docs + monthly checks
    ai_narrative = models.TextField(blank=True, default='')
    ai_engine    = models.CharField(max_length=40, blank=True, default='')
    note         = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Compliance Brain Summary'
        verbose_name_plural = 'Compliance Brain Summaries'

    def __str__(self):
        return f"Compliance brain {self.as_of} ({'ok' if self.fetched_ok else 'awaiting feed'})"


class OutboundEmailLog(BaseModel):
    """Metadata for every email omni sends — the CFO's daily oversight feed.

    CFO instruction 2026-07-25 (option A): rather than CC the CFO on thousands
    of emails a month, every send is recorded here and one digest goes out
    daily. See core.email_backends.GuardedEmailBackend, which writes these rows,
    and core.management.commands.email_outbound_digest, which reports them.

    BODIES ARE DELIBERATELY NOT STORED. Bodies carry payslip figures and live
    sign-in codes; persisting them would create a worse exposure than the one
    this was built to close. Subject + addresses + counts are enough to answer
    "what went out, to whom, and was anything blocked".
    """

    class Status(models.TextChoices):
        SENT    = 'sent',    'Sent'
        BLOCKED = 'blocked', 'Blocked (all recipients barred)'
        FAILED  = 'failed',  'Failed (send error)'

    subject          = models.CharField(max_length=255, blank=True, default='')
    from_email       = models.CharField(max_length=255, blank=True, default='')
    to_addrs         = models.TextField(blank=True, default='')
    cc_addrs         = models.TextField(blank=True, default='')
    bcc_addrs        = models.TextField(blank=True, default='')
    blocked_addrs    = models.TextField(
                           blank=True, default='',
                           help_text='Addresses stripped because they are on '
                                     'NEVER_DELIVER_EMAILS.')
    attachment_count = models.PositiveIntegerField(default=0)
    status           = models.CharField(max_length=10, choices=Status.choices,
                                        default=Status.SENT)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = 'Outbound Email Log'
        verbose_name_plural = 'Outbound Email Log'

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.status} — {self.subject[:60]}'


class CalendarInviteLog(BaseModel):
    """One row per calendar invite Omni tried to create, for oversight.

    Sits beside OutboundEmailLog and follows the same rule: metadata only,
    never the body. The row that matters most is `organiser_upn` — it is the
    only mailbox the invite was written into, and it is always the mailbox of
    the person who was signed in. Auditing this column is how the per-user
    isolation guarantee is checked after the fact rather than only asserted.

    A write failure here is swallowed by the caller: oversight must never stop
    a staff member getting their meeting in the diary.
    """

    class Status(models.TextChoices):
        CREATED = 'created', 'Created'
        REFUSED = 'refused', 'Refused (mailbox not allowed)'
        FAILED  = 'failed',  'Failed (Microsoft error)'

    organiser_upn  = models.CharField(max_length=255, db_index=True,
                                      help_text='The only calendar written to.')
    requested_by   = models.ForeignKey('auth.User', null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name='calendar_invites')
    subject        = models.CharField(max_length=255, blank=True, default='')
    purpose        = models.CharField(max_length=64, blank=True, default='',
                                      help_text='Which Omni feature asked for it.')
    starts_at      = models.CharField(max_length=64, blank=True, default='')
    attendee_count = models.PositiveIntegerField(default=0)
    attendees      = models.TextField(blank=True, default='',
                                      help_text='Invited addresses, comma separated.')
    graph_event_id = models.CharField(max_length=255, blank=True, default='')
    status         = models.CharField(max_length=10, choices=Status.choices,
                                      default=Status.CREATED)
    error          = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = 'Calendar Invite Log'
        verbose_name_plural = 'Calendar Invite Log'

    def __str__(self):
        return (f'{self.created_at:%Y-%m-%d %H:%M} {self.status} — '
                f'{self.organiser_upn} — {self.subject[:50]}')


class NotebookPage(BaseModel):
    """The shared plain-text notebook between the CFO and Claude.

    CFO 2026-07-25: "why do not we create a secret vault in omni, a clean text
    base notebook which you can read quickly than a book in one drive".

    He is right and the earlier objection was wrong. Reading a page from Omni
    over the web is ~0.2s (measured); the 30-60s figure quoted against this idea
    was the SSM/Django-shell route, not an HTTP read. Omni also beats a OneDrive
    file on the thing he actually cares about: both machines and his phone see
    the SAME text with no sync delay.

    Why it exists at all: Claude starts every conversation blank AND its memory
    is per-machine, so the Mac Mini cannot see anything the Windows sessions
    learned. He was re-asked Bharath's job title about ten times. This page is
    read before Claude's first reply, and it OVERRIDES what Omni's own database
    says — Bharath's `financial_controller` title is a known data error.

    Deliberately ONE row per slug of plain text, not a structured table. The
    moment it becomes fields and forms it stops being something he can correct
    in ten seconds, which is the only reason it will stay current.

    CFO 2026-07-25 follow-up: company financial figures BELONG here (GWP, PAT,
    PO totals, month-end position, what is being built) so they are answered in
    one read. What must never be here: passwords, keys, bank account numbers,
    Omang/ID numbers, and individual staff salaries — a shared login exposed
    staff pay that same day. Credentials go in the Secrets Vault (VaultSecret).
    """

    slug       = models.SlugField(max_length=60, unique=True, default='main',
                                  help_text="Page name. 'main' is the one Claude "
                                            "reads at the start of every session.")
    title      = models.CharField(max_length=140, blank=True, default='Notebook')
    body       = models.TextField(blank=True, default='',
                                  help_text='Plain text / Markdown. No secrets.')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='notebook_edits')

    class Meta:
        ordering = ['slug']
        verbose_name = 'Notebook page'
        verbose_name_plural = 'Notebook pages'

    def __str__(self):
        return f'{self.slug} ({len(self.body)} chars)'


class ProcessorDPA(models.Model):
    """A Data Processing Agreement with an external processor / sub-processor
    (Botswana DPA No. 18 of 2024 — controller-processor duties + Part IX transfers
    outside Botswana). Closes audit finding H-6.

    The agreement text is FROZEN into `document_html` when the record is created,
    and a SHA-256 of exactly what was on screen is stored at signing — so the
    signed version can never be altered afterwards. Signature is captured through a
    signed, no-login token link emailed to the processor (same tamper-proof pattern
    as one-click leave approval): the signed token IS the credential."""

    class Status(models.TextChoices):
        DRAFT  = 'draft',  'Draft'
        SENT   = 'sent',   'Sent for signature'
        SIGNED = 'signed', 'Signed'

    processor_name  = models.CharField(max_length=200)          # legal entity + trading name
    processor_email = models.EmailField()                       # signing-link recipient
    country         = models.CharField(max_length=100, blank=True, default='')
    adequate        = models.BooleanField(default=False)        # on Botswana's adequacy list?
    purpose         = models.CharField(max_length=400, default='')
    data_categories = models.CharField(max_length=400, blank=True, default='')
    reference       = models.CharField(max_length=40, blank=True, default='')   # e.g. DPA-2026-0001
    status          = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    document_html   = models.TextField(blank=True, default='')  # frozen agreement text (what is signed)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    sent_at     = models.DateTimeField(null=True, blank=True)
    signed_at   = models.DateTimeField(null=True, blank=True)

    signatory_name    = models.CharField(max_length=200, blank=True, default='')
    signatory_title   = models.CharField(max_length=200, blank=True, default='')
    signatory_ip      = models.CharField(max_length=64, blank=True, default='')
    signed_doc_sha256 = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Processor DPA'
        verbose_name_plural = 'Processor DPAs'

    def __str__(self):
        return f"{self.processor_name} — {self.get_status_display()}"



class MailAutoReply(models.Model):
    """One row per person we have auto-replied to, so nobody gets spammed.

    Background (CFO 2026-07-31): staff kept emailing excoboard@ / omni@ when they
    hit a bug or wanted a feature, instead of using the in-app channels. The
    `auto_reply_omni_mail` management command watches the CFO's mailbox for internal
    mail addressed to those two addresses and replies once with the two big buttons.

    Dedupe is per sender, not per message: a person who sends five emails in an
    afternoon gets exactly one nudge, and not another until the cool-off has passed.
    """

    sender_email    = models.EmailField(unique=True, db_index=True)
    last_sent_at    = models.DateTimeField()
    times_sent      = models.PositiveIntegerField(default=1)
    last_subject    = models.CharField(max_length=300, blank=True, default='')
    last_message_id = models.CharField(max_length=300, blank=True, default='')
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_sent_at']
        verbose_name = 'Mail auto-reply'
        verbose_name_plural = 'Mail auto-replies'

    def __str__(self):
        return f'{self.sender_email} (x{self.times_sent})'

class RowCountSnapshot(models.Model):
    """Hourly row count of every table, so vanished data is noticed within the hour.

    The CFO spent an hour in July trying to establish whether uploaded development
    dialogues had been deleted (they had not). This makes that a two-second question
    and, when data really does go, an email rather than a discovery weeks later.
    See core/row_watchdog.py.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    taken_at = models.DateTimeField(auto_now_add=True, db_index=True)
    counts = models.JSONField(default=dict)          # {"app.Model": row_count}
    table_count = models.PositiveIntegerField(default=0)
    total_rows = models.BigIntegerField(default=0)
    alerted = models.BooleanField(default=False)
    note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-taken_at']
        verbose_name = 'row count snapshot'

    def __str__(self):
        return f'{self.taken_at:%Y-%m-%d %H:%M} — {self.table_count} tables, {self.total_rows:,} rows'


# ---------------------------------------------------------------------------
# Speaker audience feedback  (CFO directive 2026-08-03)
# ---------------------------------------------------------------------------

class SpeakerFeedback(BaseModel):
    """One audience member's answers to a pre-session feedback form.

    CFO 2026-08-03. Prathap keynotes external events (first one: YPO Pan Africa
    2026-2027 learning year opener, "The AI Advantage: Building Your AI
    Roadmap", 25-Aug-2026). He needs the members' REAL pain points before he
    writes the talk, so the organiser circulates ONE public link
    (omni.alphadirect.co.bw/speak/<slug>) and each member rates ten pain
    statements 1-5 and answers three open questions.

    CONFIDENTIALITY — the whole point of the module. Responses are visible to
    the CFO ONLY (core.speaker_feedback_views.user_can_read_feedback: positive
    match on his own email local-part, env-overridable, NO superuser or
    is_administrator bypass). Respondents are told this on the form, which is
    why they answer honestly about their own business.

    Identity is OPTIONAL — a member may answer completely anonymously. Nothing
    here is Botswana-DPA special-category data: no Omang, no bank details, no
    medical data, no residential addresses. `submitter_ip` is kept only as
    abuse evidence for the public endpoint, same as the vendor-invite flow.
    """

    # Which talk this response belongs to. Slug lives in the URL the organiser
    # shares; SURVEYS in speaker_feedback_views is the whitelist of valid ones.
    survey_slug   = models.SlugField(max_length=60, db_index=True)

    # The ten pain ratings, 1 (not a problem for us) - 5 (a huge problem).
    # Null = the member skipped that statement; never coerce a skip to a score,
    # it would drag the averages toward the middle and hide the real pain.
    pain_ratings  = models.JSONField(default=dict)   # {"p1": 4, "p2": null, ...}

    # Open questions.
    biggest_question = models.TextField(blank=True)   # the ONE thing to answer
    wish_ai_did      = models.TextField(blank=True)   # task to hand to AI
    tried_already    = models.TextField(blank=True)   # what they tried, outcome

    # Optional identity — blank means anonymous, which is allowed and expected.
    respondent_name    = models.CharField(max_length=200, blank=True)
    respondent_email   = models.EmailField(blank=True)
    respondent_role    = models.CharField(max_length=120, blank=True)
    company_name       = models.CharField(max_length=200, blank=True)
    industry           = models.CharField(max_length=120, blank=True)
    country            = models.CharField(max_length=120, blank=True)
    headcount_band     = models.CharField(max_length=40, blank=True)

    # May the speaker quote this answer (anonymously) on a slide?
    may_quote     = models.BooleanField(default=False)

    # Abuse evidence for the public, unauthenticated endpoint.
    submitter_ip  = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'speaker audience feedback'
        verbose_name_plural = 'speaker audience feedback'

    def __str__(self):
        who = self.company_name or self.respondent_name or 'Anonymous'
        return f'{self.survey_slug} — {who} ({self.created_at:%Y-%m-%d})'


# ---------------------------------------------------------------------------
# UniCoin reconciliation exception  (CFO direction 2026-09-01)
# ---------------------------------------------------------------------------

class UniCoinReconException(BaseModel):
    """One UniCoin finance/debtors reconciliation finding, raised by a detection
    flow (Power Automate / n8n) and turned into ONE owned, SLA'd Omni task for a
    human to action by hand.

    CFO direction 2026-09-01 on Keetile Mokhendo's UniCoin Finance & Debtors
    Automation plan: detection is automatic, but the DECISION and the action stay
    human. This spine NEVER stops a debit, cancels a mandate or moves money — it
    only raises a task. The register here is the audit trail the plan asks for
    (every exception cleared or logged within its SLA), and the unique key
    de-duplicates so the same finding re-sent on the next run updates the open
    row instead of spawning a second task.

    Holds identifiers + figures only — policy number, amount, the machine reason —
    never a client name or any special-category data.
    """
    class Kind(models.TextChoices):
        MULTIPLE_GATES       = 'multiple_gates',       'Multiple pay gates on one policy'
        COLLECTING_CANCELLED = 'collecting_cancelled', 'Still collecting on a cancelled policy'
        COLLECTING_INACTIVE  = 'collecting_inactive',  'Collecting on an inactive policy'
        DEBIT_PAST_PLAN      = 'debit_past_plan',      'Debit beyond the agreed instalment plan'
        DEACTIVATED_PAYING   = 'deactivated_paying',   'Deactivated policy still paying'
        NOT_POSTED           = 'not_posted',           'Collected but not posted to Graphite'
        OTHER                = 'other',                'Other'

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'
        CLEARED   = 'cleared',   'Cleared'
        DISMISSED = 'dismissed', 'Dismissed — not a real exception'

    policy_number      = models.CharField(max_length=64, db_index=True)
    kind               = models.CharField(max_length=24, choices=Kind.choices,
                                          default=Kind.OTHER, db_index=True)
    period             = models.CharField(
                             max_length=16, blank=True, default='',
                             help_text="Reporting period, e.g. '2026-09'. Part of the dedupe key.")
    amount             = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency           = models.CharField(max_length=3, default='BWP')
    reason             = models.TextField(
                             help_text='Plain-language finding — identifiers + figures only, no client names.')
    recommended_action = models.TextField(blank=True, default='')
    source_ref         = models.CharField(
                             max_length=120, blank=True, default='',
                             help_text="The detection flow's own reference for this finding.")
    status             = models.CharField(max_length=10, choices=Status.choices,
                                          default=Status.OPEN, db_index=True)
    owner              = models.ForeignKey(User, null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='unicoin_recon_owned')
    task               = models.ForeignKey('core.OmniTask', null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='unicoin_recon_exceptions')
    raised_by          = models.ForeignKey(User, null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='unicoin_recon_raised',
                                           help_text='Service account or staff user that raised it.')
    cleared_at         = models.DateTimeField(null=True, blank=True)
    cleared_by         = models.ForeignKey(User, null=True, blank=True,
                                           on_delete=models.SET_NULL,
                                           related_name='unicoin_recon_cleared')
    cleared_note       = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'UniCoin reconciliation exception'
        verbose_name_plural = 'UniCoin reconciliation exceptions'
        constraints = [
            models.UniqueConstraint(
                fields=['policy_number', 'kind', 'period'],
                name='uniq_unicoin_recon_policy_kind_period',
            ),
        ]
        indexes = [models.Index(fields=['status', 'kind'],
                                name='core_unicoi_status_kind_idx')]

    def __str__(self):
        return f'{self.get_kind_display()} — {self.policy_number} ({self.status})'


# Screen-usage telemetry (CFO 2026-09-03) — lives in its own file, same pattern as hris.
from .screen_view_models import ScreenView  # noqa: E402,F401
# Omni staff phone app 30-day device sessions (CFO 2026-09-03).
from .device_session_models import StaffDeviceSession                 # noqa: E402,F401


class NamedModuleAccess(BaseModel):
    """
    A named person's access to one module, held as DATA rather than as a list
    in the source code.

    CFO 2026-09-15: *"i am getting these kind of request all the time i dont want
    to do this anymore."* He was right about the cause. BONU membership lived in
    `bonu/access.py` as a Python set, so every single grant — Patience (11 Aug),
    Kelvin (9 Sep), Karabo (15 Sep), Mbako (15 Sep) — needed a developer, a pull
    request, a review and a production deploy. Four deploys to add four names.

    Rows here are read by `bonu.access.bonu_team_emails()` alongside the existing
    code constant and the env override, so nothing already granted moves or
    breaks; this only adds a third source that a delegated access administrator
    can edit from a screen, with no deploy.

    Revoking is `is_active = False`, never a delete — who had access to a legal
    claims book last March is an audit question.
    """

    class Module(models.TextChoices):
        BONU        = 'bonu',        'BONU'
        BONU_INTAKE = 'bonu_intake', 'BONU claim intake (no financials)'

    module     = models.CharField(max_length=32, choices=Module.choices)
    email      = models.EmailField()
    is_active  = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
                     User, on_delete=models.SET_NULL, null=True, blank=True,
                     related_name='module_access_grants',
                 )
    note       = models.CharField(max_length=200, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=['module', 'email'],
                                    name='uniq_named_module_access'),
        ]
        ordering = ['module', 'email']

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.email} → {self.module}' + ('' if self.is_active else ' (revoked)')


def named_module_emails(module: str) -> set[str]:
    """Live grants for one module, lower-cased. Empty set if the table is not
    migrated yet, so a half-applied deploy never locks anybody out."""
    from django.db import OperationalError, ProgrammingError
    try:
        return set(
            NamedModuleAccess.objects
            .filter(module=module, is_active=True)
            .values_list('email', flat=True)
        )
    except (ProgrammingError, OperationalError):
        # The table is not there yet. Narrow on purpose: a bare `except
        # Exception` would hide a typo'd field name for ever as "nobody has
        # been granted anything", which is the silent-failure class this
        # codebase keeps getting caught by.
        import logging
        logging.getLogger(__name__).warning(
            'NamedModuleAccess table unavailable; %s access falls back to the '
            'code list only', module)
        return set()
