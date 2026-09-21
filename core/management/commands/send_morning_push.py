"""send_morning_push — ONE personalised 07:30 push per person (CFO 2026-09-03, Omni staff app).

Replaces the two separate morning pushes (approvals at 07:30, tasks at 07:35)
with a single brief: "Good morning — N things waiting on you", and up to three
lines picked by priority — payments to sign (count + the itemiser's amounts),
other approvals (count, naming the biggest streams), tasks due today / overdue.
Nobody with nothing waiting is pushed. Runs once a day from cron — that IS the
dedupe. Fail-soft: push disabled → prints and exits 0; one person's error is
logged and never stops the loop.

Reuse, not copies: the counts come from core.approvals_views.pending_approvals_for
(the same 24 streams the inbox shows) and the amounts from
pending_approval_items_for (the itemiser) — nothing here derives a figure the
server does not already return per item. Tasks use the MyTasksView filters.

  python manage.py send_morning_push

Named send_morning_push, not send_morning_brief: hris already ships a
send_morning_brief (the emailed Time Doctor brief) and Django lets the app
listed first in INSTALLED_APPS shadow a same-named command silently — this
command did exactly that on 2026-09-04 and hijacked the HR cron + 5 tests.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)

# The streams that are "payments to sign" on the phone's Payments screen.
PAYMENT_STREAMS = ("payments", "payment_requests")

# Short names for the "other approvals" line; anything not listed falls back to
# its label with the "to approve / to authorise / awaiting …" tail trimmed.
_SHORT = {
    "leave": "leave",
    "petty_cash": "petty cash",
    "journal_entries": "journal entries",
    "staff_loans": "staff loans",
    "incentives": "incentives",
    "commissions": "commissions",
    "purchase_orders": "POs",
    "leave_encashments": "leave encashments",
    "expense_claims": "receipts",
}
_LABEL_TAIL = re.compile(r"\s+(to|awaiting|pending|for)\s.*$", re.I)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _short_name(stream: dict) -> str:
    key = stream.get("key") or ""
    if key in _SHORT:
        return _SHORT[key]
    label = (stream.get("label") or key or "approvals").strip().lower()
    return _LABEL_TAIL.sub("", label) or key


def _money(totals: "OrderedDict[str, float]") -> str:
    return " + ".join(f"{ccy} {amt:,.2f}" for ccy, amt in totals.items())


def _payment_line(count: int, item_streams) -> str:
    """'2 payments to sign — BWP 350.50'. The total is the sum of the amounts the
    itemiser already returns per item, grouped by currency; if some counted
    payments carry no itemised amount the line says so rather than under-state."""
    totals: "OrderedDict[str, float]" = OrderedDict()
    priced = 0
    for s in item_streams or []:
        if s.get("key") not in PAYMENT_STREAMS:
            continue
        for it in s.get("items") or []:
            amt = it.get("amount")
            if amt is None:
                continue
            try:
                value = float(amt)
            except (TypeError, ValueError):
                continue
            ccy = it.get("ccy") or "BWP"
            totals[ccy] = totals.get(ccy, 0.0) + value
            priced += 1
    line = f"{_plural(count, 'payment')} to sign"
    if totals:
        line += f" — {_money(totals)}"
        if priced < count:
            line += f" across {priced}, the rest on the Payments screen"
    return line


def _other_line(others: list) -> str:
    total = sum(s.get("count", 0) for s in others)
    top = sorted(others, key=lambda s: -s.get("count", 0))[:2]
    named = ", ".join(f"{s['count']} {_short_name(s)}" for s in top)
    return f"{_plural(total, 'other approval')} ({named})"


def compose_brief(streams, item_streams, task_count: int):
    """Pure composition. Returns (title, body, url) or None when nothing waits."""
    streams = [s for s in (streams or []) if s.get("count")]
    pay_count = sum(s["count"] for s in streams if s.get("key") in PAYMENT_STREAMS)
    others = [s for s in streams if s.get("key") not in PAYMENT_STREAMS]
    other_count = sum(s["count"] for s in others)
    total = pay_count + other_count + int(task_count or 0)
    if not total:
        return None

    lines = []
    if pay_count:
        lines.append(_payment_line(pay_count, item_streams))
    if other_count:
        lines.append(_other_line(others))
    if task_count:
        lines.append(f"{_plural(task_count, 'task')} due today or overdue")

    if pay_count and not other_count and not task_count:
        url = "/app/payments"
    elif task_count and not pay_count and not other_count:
        url = "/app/tasks"
    else:
        url = "/app"
    title = f"Good morning — {_plural(total, 'thing')} waiting on you"
    return title, "\n".join(lines[:3]), url


def _due_task_count(user, today) -> int:
    """Same filters as taskboard MyTasksView / send_task_push: open tasks due
    today or earlier; the CFO's payment tasks live on the Payments screen."""
    from core.models import OmniTask
    from taskboard.payment_bulk_views import _is_cfo
    qs = (OmniTask.objects.filter(assignee=user, due_at__lte=today)
          .exclude(status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED]))
    if _is_cfo(user):
        qs = qs.exclude(payment_request__isnull=False)
    return qs.count()


class Command(BaseCommand):
    help = "Send each subscribed person ONE morning brief of what is waiting on them."

    def handle(self, *args, **opts):
        from core.webpush import push_enabled, send_push_to_user
        if not push_enabled():
            self.stdout.write("Push disabled (no VAPID key) — nothing sent.")
            return

        from django.contrib.auth.models import User
        from django.utils import timezone
        from core.approvals_views import (pending_approval_items_for,
                                          pending_approvals_for)
        from core.models import PushSubscription

        today = timezone.localdate()
        user_ids = (PushSubscription.objects.values_list("user_id", flat=True)
                    .distinct())
        people = pushed = failed = 0
        for u in User.objects.filter(id__in=list(user_ids), is_active=True):
            try:
                streams = pending_approvals_for(u)
                items = pending_approval_items_for(u) if any(
                    s.get("key") in PAYMENT_STREAMS for s in streams) else []
                brief = compose_brief(streams, items, _due_task_count(u, today))
                if brief is None:
                    continue
                title, body, url = brief
                people += 1
                pushed += send_push_to_user(u, title, body, url=url)
            except Exception:  # noqa: BLE001 — one person's error never stops the loop
                failed += 1
                log.exception("morning brief failed for user id=%s", u.pk)
        self.stdout.write(
            f"Briefed {people} person(s); {pushed} device(s); {failed} failed.")
