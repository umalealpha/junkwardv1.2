"""
ledger/period_close.py

Period-close workflow.

Closing a fiscal period (FiscalPeriod.Status.OPEN -> CLOSED) requires:

  1. No DRAFT or PENDING_APPROVAL journal entries dated in the period.
  2. No open Purchase Orders raised in the period (DRAFT, pending approval,
     APPROVED, PARTIALLY_RECEIVED, FULLY_RECEIVED-but-unbilled).
  3. If the company holds any non-BWP balance-sheet balances, an FX revaluation
     must have been POSTED for the period.
  4. The closing user must hold the CFO title (or be a Django superuser).

Use:

    from ledger.period_close import close_period, dry_run_close_checks

    issues = dry_run_close_checks(period)
    if not issues:
        close_period(period, user)

The CLI wrapper is `ledger close_period <period_name>`.
"""

from __future__ import annotations

from typing import Dict, List

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog, Currency, get_user_profile, UserProfile

from .models import FiscalPeriod, JournalEntry, JournalEntryLine


# ---------------------------------------------------------------------------
# Pre-close checks
# ---------------------------------------------------------------------------

def dry_run_close_checks(period: FiscalPeriod) -> Dict[str, List[str]]:
    """
    Return a dict of {check_name: [issue_messages]} for *period*.

    An empty dict means the period is ready to close.
    """
    issues: Dict[str, List[str]] = {}

    # --- 1. Open journal entries -------------------------------------------
    open_jes = list(
        JournalEntry.objects
        .filter(
            entry_date__gte=period.start_date,
            entry_date__lte=period.end_date,
            status__in=[JournalEntry.Status.DRAFT, JournalEntry.Status.PENDING_APPROVAL],
        )
        .values_list('entry_number', 'status')
    )
    if open_jes:
        issues['open_journal_entries'] = [
            f"{en} ({s})" for en, s in open_jes
        ]

    # --- 2. Open Purchase Orders -------------------------------------------
    try:
        from procurement.services import check_open_pos_for_period
        blocking_pos = check_open_pos_for_period(period)
        if blocking_pos:
            issues['open_purchase_orders'] = [
                f"{po.po_number} — {po.supplier.name} ({po.get_status_display()})"
                for po in blocking_pos
            ]
    except ImportError:
        pass  # procurement app not installed yet

    # --- 3. FX revaluation required? ---------------------------------------
    has_foreign_bs_balances = (
        JournalEntryLine.objects
        .filter(
            journal_entry__entry_date__lte=period.end_date,
            journal_entry__status=JournalEntry.Status.POSTED,
            account__account_type__in=['asset', 'liability', 'equity'],
        )
        .exclude(journal_entry__currency_code_id='BWP')
        .exists()
    )
    if has_foreign_bs_balances:
        try:
            from fx.models import FXRevaluation
            posted = FXRevaluation.objects.filter(
                period=period, status=FXRevaluation.Status.POSTED,
            ).exists()
            if not posted:
                issues['fx_revaluation_missing'] = [
                    f"Period contains foreign-currency balance-sheet activity. "
                    f"Run an FX revaluation for {period.period_name} before closing."
                ]
        except ImportError:
            pass

    return issues


# ---------------------------------------------------------------------------
# Close period
# ---------------------------------------------------------------------------

@transaction.atomic
def close_period(period: FiscalPeriod, user: User) -> FiscalPeriod:
    """Move *period* OPEN -> CLOSED. Raises ValidationError if checks fail."""
    if period.status != FiscalPeriod.Status.OPEN:
        raise ValidationError(
            f"Period {period.period_name} is {period.status}, not OPEN."
        )

    # Authorisation — CFO or superuser only
    profile      = get_user_profile(user)
    is_superuser = bool(getattr(user, 'is_superuser', False))
    is_cfo       = profile is not None and profile.title == UserProfile.Title.CFO
    if not (is_superuser or is_cfo):
        raise ValidationError("Only the CFO may close a fiscal period.")

    issues = dry_run_close_checks(period)
    if issues:
        msg_parts = []
        for check, items in issues.items():
            msg_parts.append(f"{check}: " + ", ".join(items))
        raise ValidationError(
            "Cannot close period — outstanding items:\n" + "\n".join(msg_parts)
        )

    period.status    = FiscalPeriod.Status.CLOSED
    period.closed_by = user
    period.closed_at = timezone.now()
    period.save()

    AuditLog.objects.create(
        table_name='FiscalPeriod',
        record_id=str(period.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': period.status, 'closed_by': str(user.pk)},
        user=user,
        description=f"Closed fiscal period {period.period_name}",
    )
    return period


# ---------------------------------------------------------------------------
# 3) Reopen (CLOSED -> OPEN) — CFO directive 2026-05-18.
#
# Mirrors close_period: only the CFO (or a superuser) may flip a closed
# period back to open. Required so the CFO can correct a premature close
# without dropping to the Django admin.
# ---------------------------------------------------------------------------

@transaction.atomic
def reopen_period(period: FiscalPeriod, user: User, reason: str = '') -> FiscalPeriod:
    """Move *period* CLOSED -> OPEN. Raises ValidationError if not permitted."""
    if period.status != FiscalPeriod.Status.CLOSED:
        raise ValidationError(
            f"Period {period.period_name} is {period.status}, not CLOSED."
        )

    profile      = get_user_profile(user)
    is_superuser = bool(getattr(user, 'is_superuser', False))
    is_cfo       = profile is not None and profile.title == UserProfile.Title.CFO
    if not (is_superuser or is_cfo):
        raise ValidationError("Only the CFO may reopen a fiscal period.")

    if period.has_full_lock_signoff:
        raise ValidationError(
            "Period is dual-locked — clear both lock signatures before reopening."
        )

    period.status    = FiscalPeriod.Status.OPEN
    period.closed_by = None
    period.closed_at = None
    period.save()

    AuditLog.objects.create(
        table_name='FiscalPeriod',
        record_id=str(period.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': period.status, 'reopened_by': str(user.pk),
                    'reason': reason or '(none)'},
        user=user,
        description=(f"Reopened fiscal period {period.period_name}" +
                     (f" — {reason}" if reason else '')),
    )
    return period
