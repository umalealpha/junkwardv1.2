"""Omni backend actions — CFO-authority actions run from the backend, no Chrome.

CFO 2026-08-12 (verbatim): "I should have full access to Omni, using Claude
code from the backend, to do whatever I need. Currently I cannot close tasks,
submit IT requests, or approve purchase orders. Claude code relies on Chrome
for these actions, which I am not happy about. I want backend access with the
CFO's authority to perform these tasks." Plan: docs/plans/cfo-backend-actions-plan.md.

Design: one action per existing in-app service function — NEVER a raw ORM
write. Every guard the UI enforces (segregation of duties, duplicate-payment
gate, status checks) lives inside those service functions, so calling through
them keeps every control intact. Run via `manage.py omni_do` (see that command).

Deliberately a SEPARATE registry from core.magic_action.ACTIONS: that one is
also reachable by a no-login emailed link, and this file's own policy (see its
docstring) is that money-adjacent approvals must never be one email click away.
PO approval belongs here — backend-only, run by Claude/CFO directly — not there.

Money: approving a PO does not move money — money only leaves via the FNB app.
Closing a payment_request task IS the click that releases a payment, so
`task_close` refuses those outright; use the payment flow instead.
"""
from django.core.exceptions import ValidationError


def _require(opts, field, action):
    value = (opts.get(field) or "").strip()
    if not value:
        raise ValidationError(f"--{field} is required for --action {action}.")
    return value


def _task_close_act(actor, **opts):
    from core.models import OmniTask
    from taskboard.services import complete_task

    id = _require(opts, "id", "task_close")
    note = opts.get("note") or ""

    try:
        task = OmniTask.objects.get(pk=id)
    except OmniTask.DoesNotExist:
        raise ValidationError(f"Task {id} not found.")

    if task.source == "payment_request":
        raise ValidationError(
            "Refused: this is a payment task — completing it is the click "
            "that releases the payment. Use the payment flow, not omni_do."
        )

    complete_task(task, actor, note, 0)
    return f"Task {id} ({task.title!r}) closed."


def _po_approve_act(actor, **opts):
    from procurement.models import PurchaseOrder
    from procurement.services import cfo_approve

    id = _require(opts, "id", "po_approve")

    try:
        po = PurchaseOrder.objects.get(pk=id)
    except PurchaseOrder.DoesNotExist:
        raise ValidationError(f"PO {id} not found.")

    cfo_approve(po, actor)
    return f"PO {po.po_number} approved (CFO)."


def _it_request_act(actor, **opts):
    """Raise an IT request on the omni-native queue (see core/it_queue.py for
    why it does not write to the SharePoint Help Desk)."""
    from core.it_queue import raise_it_request

    task = raise_it_request(
        actor,
        subject=_require(opts, "subject", "it_request"),
        body=opts.get("body") or "",
        priority=opts.get("priority") or "normal",
    )
    who = task.assignee.get_full_name() or task.assignee.username
    return f"IT request {task.pk} raised with {who}: {task.title!r}."


# action key -> act(actor, **opts) -> result message. Each handler validates the
# arguments it needs. Raises ValidationError on refusal.
BACKEND_ACTIONS = {
    "task_close": _task_close_act,
    "po_approve": _po_approve_act,
    "it_request": _it_request_act,
}

# Actions whose target is an existing row — omni_do stamps the audit row with it.
# it_request creates its target, so its id is only known afterwards.
ACTIONS_WITH_TARGET_ID = {"task_close", "po_approve"}
