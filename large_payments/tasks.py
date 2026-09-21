"""The CFO's task for a large-payment authorisation request.

CFO 2026-09-11: "when large payment request is made it makes me a task."

One task per request, created once. If the task cannot be created the request is
still perfectly valid and visible on the page — a notification failing must never
take the record down with it.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

from core.models import OmniTask

from .permissions import cfo_user

log = logging.getLogger(__name__)


def raise_cfo_task(req) -> OmniTask | None:
    """Put the request in the CFO's task inbox. Idempotent across threads.

    Fable 5.1, 2026-09-11: reading `req.cfo_task_id` off the instance in hand is
    no guard at all when two enrichment threads finish the same request — both
    hold a stale copy that says None and both create a task. The check re-reads
    from the database, and the claim is staked with a conditional UPDATE, so the
    database decides which thread won rather than whichever wrote last.
    """
    from .models import LargePaymentRequest

    existing = (LargePaymentRequest.objects
                .filter(pk=req.pk)
                .values_list('cfo_task_id', flat=True).first())
    if existing:
        req.cfo_task_id = existing
        return req.cfo_task

    cfo = cfo_user()
    if not cfo:
        log.warning('large_payments: no CFO user, no task raised for %s', req.ref)
        return None

    lines = list(req.lines.all())
    flagged = [ln for ln in lines if ln.flags]

    body_lines = [
        f'{len(lines)} claim payment(s) totalling BWP {req.total:,.2f} are ready '
        f'for your authorisation.',
        '',
        'These payments have already been raised, signed off by Finance and loaded '
        'to FNB through Omni\'s normal payment path. Approving here authorises the '
        'request to the CEO — it does not move money and it does not change any '
        'payment.',
        '',
    ]
    for ln in lines[:20]:
        name = ln.insured_name or '(no insured name in Graphite)'
        body_lines.append(
            f'  • {ln.claim_number or "no claim no."} — {name} — '
            f'{ln.payee or "payee not stated"} — BWP {ln.amount:,.2f}'
            + ('  [NEEDS A LOOK]' if ln.flags else '')
        )
    if len(lines) > 20:
        body_lines.append(f'  … and {len(lines) - 20} more on the page.')

    if flagged:
        body_lines += [
            '',
            f'{len(flagged)} of these need a look before you approve:',
        ]
        for ln in flagged[:10]:
            for f in ln.flags[:2]:
                body_lines.append(f'  • {ln.claim_number or ln.payment_ref}: {f["message"]}')

    body_lines += ['', f'Open it in Omni: Payments → Large Payment Requests → {req.ref}']

    try:
        task = OmniTask.objects.create(
            assigner=req.raised_by,
            assignee=cfo,
            title=f'Authorise {req.ref} — {len(lines)} claim payment(s), '
                  f'BWP {req.total:,.2f}',
            body='\n'.join(body_lines),
            priority=OmniTask.Priority.HIGH,
            due_at=timezone.localtime().date(),
            source='large_payment_request',
        )
    except Exception:                                    # noqa: BLE001
        log.exception('large_payments: could not raise the CFO task for %s', req.ref)
        return None

    # Stake the claim conditionally: only the thread that finds the column still
    # empty keeps its task. A loser deletes the one it just made rather than
    # leaving the CFO two identical tasks for one request.
    from .models import LargePaymentRequest
    won = (LargePaymentRequest.objects
           .filter(pk=req.pk, cfo_task__isnull=True)
           .update(cfo_task=task))
    if not won:
        task.delete()
        req.refresh_from_db(fields=['cfo_task'])
        return req.cfo_task

    req.cfo_task = task
    return task


def raise_ceo_task(req) -> OmniTask | None:
    """A task for the CEO. A RECORD AND A CHASE — never the way he acts.

    CFO 2026-09-12: "Arun won't use omni." So this task must not be the route by
    which he approves anything; that is the email and its no-login buttons. It
    exists because the CFO asked for it ("when i approve it sends a mail and makes
    a taks for ceo also") and because a task with a due date is what makes Omni
    chase — due_at IS the switch between silent and chased (both
    sweep_due_and_overdue and email_open_reminders filter due_at__isnull=False).

    Idempotent across re-sends: a second send must not stack up a second task.
    """
    from .models import LargePaymentRequest

    existing = (LargePaymentRequest.objects
                .filter(pk=req.pk)
                .values_list('ceo_task_id', flat=True).first())
    if existing:
        req.ceo_task_id = existing
        return req.ceo_task

    from .ceo_email import ceo_user
    from django.contrib.auth import get_user_model

    ceo = ceo_user()
    if not ceo:
        log.warning('large_payments: no CEO account, no CEO task for %s', req.ref)
        return None

    lines = list(req.lines.all())
    total = sum((ln.amount for ln in lines), Decimal('0.00'))

    body = (
        f'{len(lines)} claim payment(s) totalling BWP {total:,.2f} are with you '
        f'for authorisation on request {req.ref}.\n\n'
        'THE EMAIL IS HOW YOU ANSWER — you do not need to open Omni. It carries '
        'the full payment table and four buttons that work without signing in: '
        'approve all, refuse, ask one person a question, or ask for more detail.\n\n'
        'These payments are already loaded at the bank, so your approval is '
        'recorded as the CEO authorisation of this request and does not itself '
        'release any money.\n\n'
        'This task is only a reminder, and it closes itself when you answer.'
    )

    try:
        task = OmniTask.objects.create(
            assigner=req.decided_by or req.raised_by,
            assignee=ceo,
            title=f'Authorise {req.ref} — {len(lines)} claim payment(s), '
                  f'BWP {total:,.2f}',
            body=body,
            priority=OmniTask.Priority.URGENT,
            # due_at is what makes Omni chase him. Without it the task is silent.
            due_at=timezone.localtime().date(),
            source='large_payment_ceo',
        )
    except Exception:                                    # noqa: BLE001
        log.exception('large_payments: could not raise the CEO task for %s', req.ref)
        return None

    won = (LargePaymentRequest.objects
           .filter(pk=req.pk, ceo_task__isnull=True)
           .update(ceo_task=task))
    if not won:
        task.delete()
        req.refresh_from_db(fields=['ceo_task'])
        return req.ceo_task

    req.ceo_task = task
    return task
