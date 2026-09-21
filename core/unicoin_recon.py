"""UniCoin reconciliation exception → one owned, SLA'd Omni task.

CFO direction 2026-09-01 on Keetile Mokhendo's UniCoin Finance & Debtors
Automation plan. The plan's rule: detection is automatic (Power Automate / n8n),
but the DECISION and the action stay HUMAN. This module is the Omni end of that
rule — a detection flow raises a finding here and Omni turns it into one detailed,
owned, 7-day-SLA task on the existing task board. It NEVER stops a debit, cancels
a mandate, deactivates a policy or moves money.

Mirrors core.it_queue.raise_it_request deliberately: single-sourced owner routing
that refuses loudly rather than dropping work silently, and reuse of the existing
OmniTask + reminder/escalation engine instead of a parallel notifier.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

# Marks a task as belonging to the UniCoin recon queue, so it lists apart from
# ordinary work — same mechanism payment_request / it_request use.
RECON_SOURCE = "unicoin_recon"
SLA_DAYS = 7
# An exception must never be lost: if no owner is configured or resolves, it
# routes to the CFO, who can reassign — the account that always exists.
CFO_EMAIL = "pganesharajah@alphadirect.co.bw"


def _amount_decimal(v) -> Decimal:
    """Coerce an amount to Decimal. The JSON API passes it as a string, so the
    task-body formatter and the DecimalField must not assume it is numeric. A
    genuinely un-parseable value is a caller error, so it raises (→ 400) rather
    than silently becoming zero and hiding a real figure."""
    if isinstance(v, Decimal):
        return v
    if v in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"Amount {v!r} is not a number.")


def _resolve_user(email: str):
    email = (email or "").strip()
    if not email:
        return None
    return User.objects.filter(is_active=True, email__iexact=email).first()


def _default_owner():
    """The configured default owner (env UNICOIN_RECON_DEFAULT_OWNER), or the CFO
    as the never-lose fallback."""
    configured = (getattr(settings, "UNICOIN_RECON_DEFAULT_OWNER", "") or "").strip()
    return _resolve_user(configured) or _resolve_user(CFO_EMAIL)


def _task_body(exc) -> str:
    lines = [
        "A UniCoin reconciliation check raised this. Review and action it by hand —",
        "Omni does not stop debits, cancel mandates or move money automatically.",
        "",
        f"Policy: {exc.policy_number}",
        f"Finding: {exc.get_kind_display()}",
    ]
    if exc.period:
        lines.append(f"Period: {exc.period}")
    if exc.amount:
        lines.append(f"Amount: {exc.currency} {exc.amount:,.2f}")
    lines += ["", f"What was found: {exc.reason}"]
    if exc.recommended_action:
        lines += ["", f"Suggested action: {exc.recommended_action}"]
    lines += ["", "When done, clear the exception in Omni with a short note on what you did."]
    return "\n".join(lines)


def raise_recon_exception(*, policy_number, kind, reason, amount=0, currency="BWP",
                          period="", recommended_action="", source_ref="",
                          owner_email="", raised_by=None):
    """Upsert a UniCoin reconciliation exception and, if it is new and open,
    create ONE owned Omni task for it. Returns the exception.

    Idempotent on (policy_number, kind, period): the same finding re-sent on the
    next run refreshes an OPEN row's detail and does NOT spawn a second task; a
    row a human already CLEARED or DISMISSED is left as they decided.
    """
    from core.models import OmniTask, UniCoinReconException

    policy_number = (policy_number or "").strip()
    reason = (reason or "").strip()
    kind = (kind or "other").strip()
    period = (period or "").strip()
    amount = _amount_decimal(amount)

    if not policy_number:
        raise ValidationError("A reconciliation exception needs a policy number.")
    if not reason:
        raise ValidationError("A reconciliation exception needs a reason — say what was found.")
    valid_kinds = {k for k, _ in UniCoinReconException.Kind.choices}
    if kind not in valid_kinds:
        raise ValidationError(
            f"Unknown exception kind {kind!r} — use one of: {', '.join(sorted(valid_kinds))}."
        )

    owner = _resolve_user(owner_email) or _default_owner()
    assigner = raised_by if (raised_by and getattr(raised_by, "is_active", False)) else _resolve_user(CFO_EMAIL)
    if owner is None or assigner is None:
        raise ValidationError(
            "Cannot route the exception — no owner or system user resolves. "
            "Set UNICOIN_RECON_DEFAULT_OWNER to a real, active Omni login."
        )

    # Atomic: the register row and its task are created together or not at all.
    # This project has no ATOMIC_REQUESTS, so without this a failure between the
    # get_or_create and the task create would commit a row with task=None — and
    # idempotency would then cement it (every re-send hits created=False and never
    # makes the task). A finding with no task is a letter with no reply address.
    with transaction.atomic():
        exc, created = UniCoinReconException.objects.get_or_create(
            policy_number=policy_number, kind=kind, period=period,
            defaults=dict(
                reason=reason, amount=amount or 0, currency=(currency or "BWP")[:3],
                recommended_action=recommended_action or "", source_ref=source_ref or "",
                owner=owner, raised_by=(raised_by or assigner),
                status=UniCoinReconException.Status.OPEN,
            ),
        )

        if not created:
            # Same finding again. Refresh an OPEN row's detail; never silently
            # re-open one a human has already cleared or dismissed.
            if exc.status == UniCoinReconException.Status.OPEN:
                exc.reason = reason
                exc.amount = amount or 0
                if recommended_action:
                    exc.recommended_action = recommended_action
                if source_ref:
                    exc.source_ref = source_ref
                exc.save(update_fields=["reason", "amount", "recommended_action",
                                        "source_ref", "updated_at"])
            return exc

        task = OmniTask.objects.create(
            assigner=assigner, assignee=owner,
            title=f"UniCoin: {exc.get_kind_display()} — {policy_number}"[:200],
            body=_task_body(exc), priority=OmniTask.Priority.HIGH,
            due_at=timezone.localdate() + timedelta(days=SLA_DAYS),
            source=RECON_SOURCE,
        )
        exc.task = task
        exc.save(update_fields=["task", "updated_at"])
    return exc


def clear_exception(exc, *, by, note="", dismiss=False):
    """Close an exception a human has actioned (or dismissed as not real) and mark
    its task done. Does nothing to the policy, the mandate or any money."""
    from core.models import OmniTask, UniCoinReconException

    exc.status = (UniCoinReconException.Status.DISMISSED if dismiss
                  else UniCoinReconException.Status.CLEARED)
    exc.cleared_at = timezone.now()
    exc.cleared_by = by if getattr(by, "is_authenticated", False) else None
    exc.cleared_note = (note or "").strip()
    exc.save(update_fields=["status", "cleared_at", "cleared_by", "cleared_note", "updated_at"])
    if exc.task and exc.task.status not in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
        exc.task.status = OmniTask.Status.DONE
        exc.task.completed_at = timezone.now()
        exc.task.save(update_fields=["status", "completed_at", "updated_at"])
    return exc
