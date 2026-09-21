"""The IT request queue — an IT request is an OmniTask assigned to IT.

CFO directive 2026-06-29 ("link this section to our tasks") already established
that IT Help Desk work belongs on the omni task board: helpdesk_pending_reminder
upserts an OmniTask per owner so pending SharePoint tickets surface in /tasks.
This raises requests the same way, so IT has ONE place to look rather than two.

Why not write to the real Help Desk: the tickets live in an external SharePoint
list and the omni Graph app holds READ access only (see the REQUIRES note in
core/management/commands/helpdesk_pending_reminder.py). Creating one there needs
a Microsoft write-scope grant nobody has issued, so an omni-native queue is the
honest option — and it needs no permission from anyone to work.

ROUTING IS BY NAMED EMAIL, DELIBERATELY. Prod carries no usable IT department or
title data: no active UserProfile has department='it', and both Help Desk owners
sit on unrelated titles (Sechele is recorded as 'accountant', Molefe as
'operations'). Routing on department or title would resolve to nobody and drop
requests silently. The owner list is single-sourced here and imported by the
reminder command so the two can never disagree.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

# The IT Help Desk owners — Kelebogile Molefe + Sechele. Single source of truth;
# core/management/commands/helpdesk_pending_reminder.py imports this list.
IT_OWNERS = ["kmolefe@alphadirect.co.bw", "isechele@alphadirect.co.bw"]

# Marks a task as belonging to the IT queue, so it can be listed apart from
# ordinary work. Same mechanism payment_request uses.
IT_REQUEST_SOURCE = "it_request"

_PRIORITIES = {"low", "normal", "high", "urgent"}


def it_owner_users():
    """The IT owners that resolve to a real active login, in list order."""
    by_email = {
        (u.email or "").strip().lower(): u
        for u in User.objects.filter(is_active=True, email__in=IT_OWNERS)
    }
    return [by_email[e] for e in IT_OWNERS if e in by_email]


def raise_it_request(requester, *, subject, body="", priority="normal"):
    """Put an IT request on the queue as an OmniTask. Returns the task.

    Refuses loudly when no IT owner resolves — an IT request that silently
    reaches nobody is worse than one that fails in front of you.
    """
    from core.models import OmniTask

    subject = (subject or "").strip()
    if not subject:
        raise ValidationError("An IT request needs a subject — say what is broken.")

    priority = (priority or "normal").strip().lower()
    if priority not in _PRIORITIES:
        raise ValidationError(
            f"Unknown priority {priority!r} — use one of: {', '.join(sorted(_PRIORITIES))}."
        )

    owners = it_owner_users()
    if not owners:
        raise ValidationError(
            "No IT Help Desk owner has an active omni login "
            f"({', '.join(IT_OWNERS)}) — cannot route this request. "
            "Fix the account before raising IT work here."
        )

    assignee = owners[0]
    others = [u for u in owners[1:]]
    footer = ""
    if others:
        footer = "\n\nAlso on the IT queue: " + ", ".join(
            (u.get_full_name() or u.username) for u in others
        )

    return OmniTask.objects.create(
        assigner=requester,
        assignee=assignee,
        title=subject[:200],
        body=(body or "").strip() + footer,
        priority=priority,
        source=IT_REQUEST_SOURCE,
    )
