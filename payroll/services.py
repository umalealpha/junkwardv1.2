"""
payroll/services.py

GL posting and reversal services for payroll.

The single public entry points are:
  - post_payroll_period(period, user)      -> JournalEntry
  - reverse_payroll_period(period, user, reason) -> JournalEntry
  - reconcile_payroll_period(period)       -> dict

Design: see .claude/specs/payroll-gl-posting/design.md
Steering rule enforced: rule 1 (every financial transaction posts to the GL).
"""

from decimal import Decimal
from typing import Dict, Iterable

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog
from ledger.models import Account, JournalEntry, JournalEntryLine
from payroll.models import (
    Payslip,
    PayslipComponent,
    PayslipLine,
    PayrollPeriod,
)


ZERO = Decimal('0.00')

# Account code used for the balancing CREDIT to net-pay-payable.
# CFO can override per-deployment via Django settings if a different chart of
# accounts is in use.
SALARY_PAYABLE_ACCOUNT_CODE = '2150'

# Balancing CREDIT for employer-cost (COMPANY_CONTRIBUTION) components. These
# debit an expense account but, unlike employee deductions, are NOT withheld
# from the employee's net pay — they are the employer's own liability to the
# fund/authority, so they need their own payable credit or the JE won't
# balance. CFO directive 2026-06-05.
COMPANY_CONTRIB_PAYABLE_CODE = '2151'

# Component kinds that are journalised on the DEBIT side.
DEBIT_KINDS = {
    PayslipComponent.Kind.EARNING,
    PayslipComponent.Kind.EARNING_NON_TAXABLE,
    PayslipComponent.Kind.COMPANY_CONTRIBUTION,
}

# Component kinds that are journalised on the CREDIT side (liabilities owed
# to third parties: tax authority, deduction recipients).
CREDIT_KINDS = {
    PayslipComponent.Kind.EMPLOYEE_DEDUCTION,
    PayslipComponent.Kind.EMPLOYEE_PRETAX,
    PayslipComponent.Kind.TAX,
}

# Component kinds that are subtotals — never produce a JE line.
COMPUTED_KINDS = {
    PayslipComponent.Kind.COMPUTED_GROSS,
    PayslipComponent.Kind.COMPUTED_NET,
    PayslipComponent.Kind.COMPUTED_CTC,
}


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def _can_post_payroll(user) -> bool:
    """A user may post payroll to the GL if they are a superuser, a profile
    administrator, hold a finance-approval title (CFO / Finance Manager /
    Financial Controller — UserProfile.APPROVAL_TITLES, the same set that may
    approve a journal entry), hold the FINANCE_MANAGER role, or are in the
    payroll_poster / finance_manager / cfo group.

    CFO directive 2026-06-25: this previously checked ONLY Django groups, so a
    Financial Controller / Finance Manager who holds the title/role but is not
    in a group could not post — and the finance_manager / cfo groups were never
    created, so the gate fell through to superuser-only. Align posting with
    omni's title/role layer (mirrors user_can_view_payroll, which already
    honours role assignments). Posting stays a FINANCE action — an HR Manager
    inputs/approves the import, Finance posts the GL leg (separation of duties).
    """
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(
        name__in=['payroll_poster', 'finance_manager', 'cfo']
    ).exists():
        return True
    from core.models import UserProfile, UserRoleAssignment, get_user_profile
    prof = get_user_profile(user)
    if prof is not None and getattr(prof, 'is_active', True):
        if getattr(prof, 'is_administrator', False):
            return True
        if prof.title in UserProfile.APPROVAL_TITLES:
            return True
    return UserRoleAssignment.objects.filter(
        user=user, role__code='FINANCE_MANAGER', revoked_at__isnull=True,
    ).exists()


def _can_reverse_payroll(user) -> bool:
    """Only the CFO (or a superuser) may reverse a posted payroll period."""
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name='cfo').exists()


# ---------------------------------------------------------------------------
# Validation & aggregation helpers
# ---------------------------------------------------------------------------

def _validate_component_mappings(
    period: PayrollPeriod,
) -> Dict[int, Account]:
    """
    Resolve every PayslipComponent that contributes a JE line in this period
    to a live ledger.Account. Raises ValidationError listing every unmapped
    component on first miss.
    """
    journalisable_kinds = DEBIT_KINDS | CREDIT_KINDS

    components = (
        PayslipComponent.objects
        .filter(
            lines__payslip__period=period,
            lines__payslip__status=Payslip.Status.APPROVED,
            kind__in=journalisable_kinds,
        )
        .distinct()
    )

    accounts: Dict[int, Account] = {}
    missing: list[str] = []
    bad_code: list[str] = []

    for comp in components:
        code = (comp.posting_account_code or '').strip()
        if not code:
            missing.append(f"{comp.code} ({comp.name})")
            continue
        try:
            acct = Account.objects.get(code=code, is_active=True)
        except Account.DoesNotExist:
            bad_code.append(f"{comp.code} (code '{code}' not found / inactive)")
            continue
        accounts[comp.id] = acct

    errors: list[str] = []
    if missing:
        errors.append(
            "Components missing posting_account_code: " + ", ".join(missing)
        )
    if bad_code:
        errors.append(
            "Components with unresolved account codes: " + ", ".join(bad_code)
        )
    if errors:
        raise ValidationError(errors)

    return accounts


def _aggregate_lines_by_component(
    period: PayrollPeriod,
) -> Dict[int, Decimal]:
    """
    Sum PayslipLine.amount per component across all APPROVED payslips in
    this period. Excludes COMPUTED_* kinds (subtotals) and zero-totals.
    """
    rows = (
        PayslipLine.objects
        .filter(
            payslip__period=period,
            payslip__status=Payslip.Status.APPROVED,
        )
        .exclude(component__kind__in=COMPUTED_KINDS)
        .values('component_id')
        .annotate(total=Sum('amount'))
    )
    return {r['component_id']: (r['total'] or ZERO) for r in rows if r['total']}


def _net_payable_total(period: PayrollPeriod) -> Decimal:
    """Sum of Payslip.net_amount across all APPROVED payslips in this period."""
    return (
        period.payslips
        .filter(status=Payslip.Status.APPROVED)
        .aggregate(t=Sum('net_amount'))['t']
        or ZERO
    )


# ---------------------------------------------------------------------------
# Public service: post
# ---------------------------------------------------------------------------

@transaction.atomic
def post_payroll_period(period: PayrollPeriod, user: User) -> JournalEntry:
    """
    Post the period's approved payslips to the General Ledger as a single
    balanced JournalEntry. See .claude/specs/payroll-gl-posting/design.md
    §"Posting algorithm" for the full flow.
    """
    if not _can_post_payroll(user):
        raise ValidationError(
            "You do not have permission to post payroll to the GL. "
            "Required group: payroll_poster, finance_manager, or cfo."
        )

    # Lock the period row to serialise concurrent posts.
    period = PayrollPeriod.objects.select_for_update().get(pk=period.pk)

    if period.status != PayrollPeriod.Status.APPROVED:
        raise ValidationError(
            f"Only APPROVED payroll periods can be posted to the GL. "
            f"{period.period_name} is currently {period.get_status_display()}."
        )
    if period.journal_entry_id is not None:
        raise ValidationError(
            f"Period {period.period_name} is already posted "
            f"(JE: {period.journal_entry.entry_number})."
        )

    approved_payslips = period.payslips.filter(status=Payslip.Status.APPROVED)
    if not approved_payslips.exists():
        raise ValidationError(
            f"Period {period.period_name} has no APPROVED payslips. "
            "Approve payslips before posting."
        )

    # Structural audit 2026-05-24 — fiscal-period close check.
    # Canonical ERPs forbid posting any GL movement to a closed period.
    # Without this guard, a payroll posted with pay_date inside a locked
    # FY would corrupt the audited TB.
    posting_date = period.pay_date or period.end_date
    from ledger.models import FiscalPeriod
    fp = FiscalPeriod.get_open_period_for_date(posting_date)
    if fp is None:
        raise ValidationError(
            f"No open fiscal period covers {posting_date}. Cannot post "
            "payroll into a closed period. Reopen the period or amend "
            "the payroll pay_date."
        )

    # FR-3: every journalisable component must resolve to a live Account.
    accounts = _validate_component_mappings(period)

    # Determine company + currency from the first approved payslip.
    sample = approved_payslips.select_related('company').first()
    if sample is None or sample.company_id is None:
        raise ValidationError(
            "Approved payslips in this period have no Company set. "
            "Cannot determine GL company / currency."
        )
    company = sample.company

    # Salary Payable account (balancing credit).
    try:
        salary_payable = Account.objects.get(
            code=SALARY_PAYABLE_ACCOUNT_CODE, is_active=True,
        )
    except Account.DoesNotExist as exc:
        raise ValidationError(
            f"Salary Payable account ({SALARY_PAYABLE_ACCOUNT_CODE}) is not "
            "in the chart of accounts (or is inactive). Run "
            "`python manage.py setup_chart_of_accounts` first."
        ) from exc

    # Employer Contributions Payable account (balancing credit for the
    # employer-cost side — resolved lazily; only required if the period
    # actually has COMPANY_CONTRIBUTION lines).
    contrib_payable = None

    je = JournalEntry.objects.create(
        entry_date    = period.pay_date or period.end_date,
        description   = f"Payroll {period.period_name}",
        journal_type  = JournalEntry.JournalType.PAYROLL,
        source_type   = 'payroll_period',
        source_id     = period.pk,
        currency_code = company.base_currency,
        exchange_rate = Decimal('1.00000000'),
        company       = company,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
        is_related_party = False,  # payroll runs are never related-party JEs
    )

    totals = _aggregate_lines_by_component(period)

    # DR / CR lines per component
    company_contrib_total = ZERO
    for comp_id, amount in totals.items():
        if amount == ZERO:
            continue
        component = PayslipComponent.objects.get(pk=comp_id)
        account = accounts[comp_id]
        if component.kind in DEBIT_KINDS:
            JournalEntryLine.objects.create(
                journal_entry = je,
                account       = account,
                debit_amount  = amount,
                credit_amount = ZERO,
                debit_bwp     = amount,
                credit_bwp    = ZERO,
            )
            # Employer contributions are an expense (debited above) AND a
            # liability to the fund — accumulate for the offsetting credit.
            if component.kind == PayslipComponent.Kind.COMPANY_CONTRIBUTION:
                company_contrib_total += amount
        elif component.kind in CREDIT_KINDS:
            JournalEntryLine.objects.create(
                journal_entry = je,
                account       = account,
                debit_amount  = ZERO,
                credit_amount = amount,
                debit_bwp     = ZERO,
                credit_bwp    = amount,
            )
        # COMPUTED_* already excluded by _aggregate_lines_by_component

    # Balancing CR Employer Contributions Payable for the employer-cost side.
    if company_contrib_total != ZERO:
        try:
            contrib_payable = Account.objects.get(
                code=COMPANY_CONTRIB_PAYABLE_CODE, is_active=True,
            )
        except Account.DoesNotExist as exc:
            raise ValidationError(
                f"Employer Contributions Payable account "
                f"({COMPANY_CONTRIB_PAYABLE_CODE}) is not in the chart of "
                "accounts (or is inactive). It is required to balance employer "
                "contribution lines. Run `python manage.py setup_chart_of_accounts`."
            ) from exc
        JournalEntryLine.objects.create(
            journal_entry = je,
            account       = contrib_payable,
            debit_amount  = ZERO,
            credit_amount = company_contrib_total,
            debit_bwp     = ZERO,
            credit_bwp    = company_contrib_total,
        )

    # Balancing CR Salary Payable for the net payable to employees.
    net_total = _net_payable_total(period)
    if net_total != ZERO:
        JournalEntryLine.objects.create(
            journal_entry = je,
            account       = salary_payable,
            debit_amount  = ZERO,
            credit_amount = net_total,
            debit_bwp     = ZERO,
            credit_bwp    = net_total,
        )

    # je.post() validates DR == CR before flipping to POSTED.
    je.post(user=user, _allow_direct=True)

    # Promote the period.
    period.status        = PayrollPeriod.Status.POSTED
    period.journal_entry = je
    period.save(
        audit_user=user,
        audit_description=f"Posted {period.period_name} to GL ({je.entry_number})",
    )

    AuditLog.objects.create(
        table_name='PayrollPeriod',
        record_id=str(period.pk),
        action=AuditLog.Action.POST,
        new_values={
            'status':        period.status,
            'journal_entry': je.entry_number,
            'net_payable':   str(net_total),
        },
        user=user,
        description=f"Posted {period.period_name} to GL: {je.entry_number}",
    )

    return je


# ---------------------------------------------------------------------------
# Public service: reverse
# ---------------------------------------------------------------------------

@transaction.atomic
def reverse_payroll_period(
    period: PayrollPeriod, user: User, reason: str,
) -> JournalEntry:
    """
    Reverse a posted payroll period. Creates a contra JournalEntry that
    mirrors the original. CFO-only. Blocked if any payslip is already PAID.
    """
    if not _can_reverse_payroll(user):
        raise ValidationError(
            "Only the CFO may reverse a posted payroll period."
        )
    if not (reason or '').strip():
        raise ValidationError("A reversal reason is required.")

    period = PayrollPeriod.objects.select_for_update().get(pk=period.pk)

    if period.status != PayrollPeriod.Status.POSTED:
        raise ValidationError(
            f"Only POSTED payroll periods can be reversed. "
            f"{period.period_name} is currently {period.get_status_display()}."
        )
    if period.payslips.filter(status=Payslip.Status.PAID).exists():
        raise ValidationError(
            "Cannot reverse: one or more payslips in this period are PAID."
        )
    original_je = period.journal_entry
    if original_je is None:
        raise ValidationError(
            f"Period {period.period_name} has no linked JE — cannot reverse."
        )

    contra = JournalEntry.objects.create(
        entry_date    = timezone.localdate(),
        description   = f"Reversal of {original_je.entry_number}: {reason.strip()}",
        journal_type  = JournalEntry.JournalType.PAYROLL,
        source_type   = 'payroll_period_reversal',
        source_id     = period.pk,
        currency_code = original_je.currency_code,
        exchange_rate = original_je.exchange_rate,
        company       = original_je.company,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
        is_related_party = False,
        reversal_of   = original_je,
    )

    for ln in original_je.lines.all():
        JournalEntryLine.objects.create(
            journal_entry = contra,
            account       = ln.account,
            debit_amount  = ln.credit_amount,
            credit_amount = ln.debit_amount,
            debit_bwp     = ln.credit_bwp,
            credit_bwp    = ln.debit_bwp,
        )

    contra.post(user=user, _allow_direct=True)

    # Mark the original as REVERSED — match the pattern used in ledger.
    JournalEntry.objects.filter(pk=original_je.pk).update(
        status=JournalEntry.Status.REVERSED,
        reversed_by=contra,
    )

    period.status        = PayrollPeriod.Status.APPROVED
    period.journal_entry = None
    period.save(
        audit_user=user,
        audit_description=(
            f"Reversed payroll posting for {period.period_name}: {reason.strip()}"
        ),
    )

    AuditLog.objects.create(
        table_name='PayrollPeriod',
        record_id=str(period.pk),
        action=AuditLog.Action.REVERSE,
        new_values={
            'status':         period.status,
            'original_je':    original_je.entry_number,
            'reversal_je':    contra.entry_number,
            'reason':         reason.strip(),
        },
        user=user,
        description=(
            f"Reversed payroll {period.period_name}: "
            f"{original_je.entry_number} -> {contra.entry_number}"
        ),
    )

    return contra


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_payroll_period(period: PayrollPeriod) -> dict:
    """
    Verify the three invariants for a posted period:
      1. JE DR == sum of (EARNING + EARNING_NON_TAXABLE + COMPANY_CONTRIBUTION)
      2. JE CR to Salary Payable == sum of Payslip.net_amount
      3. JE balances (DR == CR overall)

    Returns: {'ok': bool, 'checks': [...]}
    """
    checks: list[dict] = []
    if period.status != PayrollPeriod.Status.POSTED or period.journal_entry_id is None:
        return {
            'ok': False,
            'checks': [{
                'name': 'period_status',
                'ok': False,
                'detail': f'Period {period.period_name} is not POSTED.',
            }],
        }

    je = period.journal_entry
    lines = list(je.lines.select_related('account').all())

    # 1 — debit total vs. earning/contribution totals
    expected_dr = (
        PayslipLine.objects
        .filter(
            payslip__period=period,
            payslip__status=Payslip.Status.APPROVED,
            component__kind__in=[
                PayslipComponent.Kind.EARNING,
                PayslipComponent.Kind.EARNING_NON_TAXABLE,
                PayslipComponent.Kind.COMPANY_CONTRIBUTION,
            ],
        )
        .aggregate(t=Sum('amount'))['t'] or ZERO
    )
    actual_dr = sum((ln.debit_bwp for ln in lines), ZERO)
    checks.append({
        'name': 'debit_total_matches_earnings',
        'ok': expected_dr == actual_dr,
        'expected': str(expected_dr),
        'actual':   str(actual_dr),
    })

    # 2 — salary payable credit total vs. sum of net_amount
    expected_net = _net_payable_total(period)
    actual_salary_payable_cr = sum(
        (ln.credit_bwp for ln in lines
         if ln.account.code == SALARY_PAYABLE_ACCOUNT_CODE),
        ZERO,
    )
    checks.append({
        'name': 'salary_payable_credit_matches_net',
        'ok': expected_net == actual_salary_payable_cr,
        'expected': str(expected_net),
        'actual':   str(actual_salary_payable_cr),
    })

    # 3 — overall balance
    total_dr = sum((ln.debit_bwp for ln in lines), ZERO)
    total_cr = sum((ln.credit_bwp for ln in lines), ZERO)
    checks.append({
        'name': 'overall_balance',
        'ok': total_dr == total_cr,
        'expected': 'DR == CR',
        'actual':   f"DR {total_dr} / CR {total_cr}",
    })

    ok = all(c['ok'] for c in checks)
    return {'ok': ok, 'checks': checks, 'je': je.entry_number}
