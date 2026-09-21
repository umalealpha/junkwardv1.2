"""
taskboard/services.py — reminder engine + enforced completion (server-side).

All rules that must not be bypassable live here (not in the view, not the frontend):
  * complete_task      the 30-char completion-note gate (dwell gate removed 2026-07-18)
  * notify_on_assign   one gentle assign-day notice (idempotent)
  * sweep_due_and_overdue   day-granular due/overdue notice creation (cron: sweep_task_reminders)
  * completion_rate    done / assigned for a person over a window (EOS ~90% benchmark)

Dates use timezone.localdate() -> Africa/Gaborone (settings.TIME_ZONE).
"""
from __future__ import annotations

import logging
from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import OmniTask

# Module logger (the loop below binds a LOCAL `log` from get_or_create, so a
# module-level name must be distinct to stay usable throughout the function).
logger = logging.getLogger(__name__)

from .models import CompletionNote, Notification, TaskReminderEmailLog

_OPEN_STATUSES = [
    OmniTask.Status.PENDING,
    OmniTask.Status.IN_PROGRESS,
    OmniTask.Status.PARTIAL,
    OmniTask.Status.BLOCKED,
]


def notify_on_assign(task: OmniTask) -> Notification | None:
    """One gentle, dismissible assign-day toast per task. Idempotent."""
    if Notification.objects.filter(
        task=task, type=Notification.Type.ASSIGN_DAY
    ).exists():
        return None
    return Notification.objects.create(
        recipient=task.assignee, task=task, type=Notification.Type.ASSIGN_DAY
    )


def email_task_assigned(task: OmniTask) -> int:
    """Email the assignee the FULL task the moment a person hands it to them —
    title, priority, due date/time, WHO gave it, and the whole details/body, not
    just a summary line (CFO 2026-07-28: "when people put tasks, send details of
    the tasks via email").

    Called ONLY from the direct person-to-person hand-off endpoint
    (core.api_views.omni_task). The approval-workflow tasks (JE, payments, petty
    cash, payroll, dialogue) create their OmniTasks through their own notify_*
    helpers and keep their own email policy, so they are never double-mailed.

    Best-effort: never raises, so a flaky mailer can't block task creation.
    Returns 1 on send, 0 otherwise (no email address, self-assigned, or failure).
    """
    from django.conf import settings
    from django.utils.html import escape

    assignee = task.assignee
    email = (getattr(assignee, "email", "") or "").strip()
    # Don't email a person a task they gave themselves, and skip if no address.
    if not email or task.assigner_id == task.assignee_id:
        return 0

    first = (assignee.get_full_name() or "").split(" ")[0] or assignee.username
    assigner_name = task.assigner.get_full_name() or task.assigner.username
    base = getattr(settings, "PUBLIC_BASE_URL",
                   "https://omni.alphadirect.co.bw").rstrip("/")

    prio_label = task.get_priority_display()
    prio_color = ("#B04E00" if task.priority in
                  (OmniTask.Priority.HIGH, OmniTask.Priority.URGENT) else "#0D1B2A")
    if task.due_at:
        due_txt = f"{task.due_at:%d %b %Y}"
        if task.due_time:
            due_txt += f" by {task.due_time:%H:%M}"
    else:
        due_txt = "No deadline set"

    raw = (task.body or "").strip()
    details = (escape(raw).replace("\n", "<br>") if raw else
               '<span style="color:#6B7280;">(no extra details were added)</span>')

    html = (
        f'<p>{escape(first)},</p>'
        f'<p><strong>{escape(assigner_name)}</strong> has given you a task in Omni:</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        '<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;width:110px;'
        'color:#6B7280;">Task</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-weight:600;">'
        f'{escape(task.title)}</td></tr>'
        '<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        'Priority</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;color:{prio_color};'
        f'font-weight:600;">{prio_label}</td></tr>'
        '<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#6B7280;">'
        'Due</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{due_txt}</td></tr>'
        '</table>'
        '<p style="margin:14px 0 4px;font-weight:600;color:#0D1B2A;">Details</p>'
        '<div style="padding:10px 12px;background:#F9FAFB;border-left:3px solid #F4A623;'
        f'border-radius:4px;">{details}</div>'
        '<p style="margin-top:14px;">Open it in Omni &rarr; <strong>Tasks</strong> to '
        f'update or complete it:<br><a href="{base}/tasks">{base}/tasks</a></p>'
    )
    text = (
        f"{first}, {assigner_name} has given you a task in Omni.\n\n"
        f"Task: {task.title}\nPriority: {prio_label}\nDue: {due_txt}\n\n"
        f"Details:\n{raw or '(no extra details were added)'}\n\n"
        f"Open: {base}/tasks"
    )
    try:
        from core.notifications import send_html_with_cfo_cc
        return send_html_with_cfo_cc(
            subject=f"Omni — new task from {assigner_name}: {task.title}"[:150],
            html=html, to=[email], text_fallback=text, cc_cfo=False)
    except Exception:  # noqa: BLE001 — mail must never block task creation
        return 0


def email_task_comment(task: OmniTask, comment, author: User) -> int:
    """Email a task comment to the people it is actually aimed at.

    CFO 2026-07-29: he wrote "I have authorized the known payments, but I don't
    know who Johannes is. Please email me the supporting documents." on a payment
    authorisation task — and nobody was told. A comment nobody is notified about
    is a note to himself.

    Recipients for a payment authorisation (`source='payment_request'`):
      * whoever RAISED the request  — the loader, the person who can actually
        produce the supporting documents (often NOT the task's assigner: the
        clerk loads it, finance signs it off, and only finance appears on the
        CFO's task)
      * the finance approver who signed it off
      * the task's assigner and assignee
    minus the author, minus anyone with no address. Deduplicated.

    Scoped to payment authorisations for now — widen by dropping the `source`
    check if the same is wanted for every task comment.

    Best-effort: never raises. Returns 1 on send, 0 otherwise.
    """
    if task.source != 'payment_request':
        return 0

    from django.conf import settings
    from django.utils.html import escape

    people = [task.assigner, task.assignee]
    try:
        from .models import PaymentRequest
        pr = (PaymentRequest.objects
              .select_related('created_by', 'first_approver')
              .filter(task=task).first())
        if pr is not None:
            people += [pr.created_by, pr.first_approver]
    except Exception:  # noqa: BLE001 — a missing request must not lose the mail
        pr = None

    seen, to = set(), []
    for p in people:
        addr = (getattr(p, 'email', '') or '').strip()
        key = addr.lower()
        if not addr or key in seen or (p and p.id == author.id):
            continue
        seen.add(key)
        to.append(addr)
    if not to:
        return 0

    author_name = author.get_full_name() or author.username
    base = getattr(settings, 'PUBLIC_BASE_URL',
                   'https://omni.alphadirect.co.bw').rstrip('/')
    body = (getattr(comment, 'body', '') or '').strip()
    status_line = ''
    new_status = (getattr(comment, 'new_status', '') or '').strip()
    if new_status:
        label = dict(OmniTask.Status.choices).get(new_status, new_status)
        status_line = ('<p style="margin:10px 0 0;">He also moved it to '
                       f'<strong>{escape(str(label))}</strong>.</p>')
    proof = ('<p style="margin:10px 0 0;color:#6B7280;font-size:12px;">'
             'A file was attached in Omni.</p>'
             if getattr(comment, 'evidence', None) else '')

    html = (
        f'<p><strong>{escape(author_name)}</strong> has commented on a payment '
        f'authorisation you are involved in:</p>'
        f'<p style="font-weight:600;color:#0D1B2A;">{escape(task.title)}</p>'
        '<div style="padding:11px 13px;background:#F9FAFB;border-left:3px solid '
        f'#F4A623;border-radius:4px;white-space:pre-wrap;">{escape(body) or "(no text)"}</div>'
        + status_line + proof +
        '<p style="margin-top:14px;">Reply in Omni &rarr; <strong>Tasks</strong>, '
        'or send what has been asked for:<br>'
        f'<a href="{base}/tasks">{base}/tasks</a></p>'
    )
    text = (f'{author_name} has commented on a payment authorisation:\n\n'
            f'{task.title}\n\n{body or "(no text)"}\n\n'
            f'Reply in Omni: {base}/tasks\n')
    try:
        from core.notifications import send_html_with_cfo_cc
        return send_html_with_cfo_cc(
            subject=f'Omni — {author_name} on {task.title}'[:150],
            html=html, to=to, text_fallback=text, cc_cfo=False)
    except Exception:  # noqa: BLE001 — mail must never block the comment
        return 0


def _override_is_complete(pr) -> bool:
    """Is this duplicate override properly justified, or just typed?

    Until 2026-08-09 the answer was "25 characters of free text", and that was
    the ONLY thing between a detected duplicate and the money. Claim G2026004368
    (ARCON CRAFTS, BWP 13,662.67) was correctly flagged on
    PAY/ADIC/2026/08/07/0004 as already carried on PAY/ADIC/2026/07/28/0001 —
    and both are marked paid. The person who created the duplicate cleared their
    own warning.

    An override now needs all four, and fails CLOSED if any is missing:
      * a written reason (unchanged),
      * a category from the fixed list — free text hides the real cause,
      * a document, because "the bank rejected it" is checkable,
      * a second person, who must NOT be whoever raised the request.
    """
    from .payment_duplicates import MIN_OVERRIDE_CHARS

    if len((pr.duplicate_override_reason or '').strip()) < MIN_OVERRIDE_CHARS:
        return False
    if not (pr.duplicate_override_category or '').strip():
        return False
    if pr.duplicate_override_evidence_id is None:
        return False
    approver_id = pr.duplicate_override_approved_by_id
    if approver_id is None:
        return False
    # Self-approval is the whole failure mode — the loader who created the
    # duplicate must not be the one who clears it.
    if pr.created_by_id is not None and approver_id == pr.created_by_id:
        return False
    return True


def _assert_not_duplicate_payment(task) -> None:
    """Refuse to pay a request whose lines are already raised or paid elsewhere.

    Only a PENDING_CFO request is paid by completing its task; at stage 1 the
    linked task is the finance sign-off, which goes through payment_request_decide
    instead. An override already written on the record carries through — the
    approver justified it once and does not re-justify it at every step.
    """
    from .models import PaymentRequest
    from .payment_duplicates import (
        CONTROL_CODE, MIN_OVERRIDE_CHARS, blocking_message, find_duplicates,
        hard_total,
    )

    pr = (PaymentRequest.objects
          .filter(task=task, status=PaymentRequest.Status.PENDING_CFO)
          .first())
    if pr is None:
        return
    if pr.exception_control == CONTROL_CODE and pr.exception_decision == 'approve':
        return   # the payment committee decided this duplicate may be paid (CFO 2026-09-04)
    if _override_is_complete(pr):
        return
    dup = find_duplicates(pr.line_items or [], currency=pr.currency,
                          exclude_pk=pr.pk, payee=pr.payee or '')
    if not dup['hard']:
        return
    # context='authorise': the CFO is reading somebody else's pack from a task in
    # their inbox. The raiser's wording ("remove those lines… write why in the box
    # below") describes the new-payment form, which is not the screen they are on —
    # it left the CFO with a refusal and no action on 3 Aug 2026. blocking_message
    # now closes with what IS available here, so nothing is appended.
    # code=PAY-DUP-01 so the API can label this a payment-control refusal rather
    # than a generic 400. The front end needs that label to render the finding in
    # full instead of letting the 240-character toast truncate it.
    raise ValidationError(
        blocking_message(dup['hard'], currency=pr.currency,
                         total_hard=hard_total(dup['hard']),
                         context='authorise'),
        code=CONTROL_CODE,
    )


def _assert_no_partial_line_decisions(task) -> None:
    """Refuse to mark a whole payment request paid once the CFO has started
    deciding it line by line (any line held or rejected).

    Otherwise the whole-request "mark all as paid" (this completion path, reached
    from the payments page OR the task drawer) would sweep a HELD or REJECTED line
    straight into PAID (Fable 5, 2026-08-31). Once per-line decisions have started
    the request must be closed through the per-line path
    (taskboard.payment_views.payment_request_decide_lines).
    """
    from .models import PaymentRequest
    pr = (PaymentRequest.objects
          .filter(task=task, status=PaymentRequest.Status.PENDING_CFO)
          .first())
    if pr is None:
        return
    if any(PaymentRequest.line_state(ln) in ('held', 'rejected')
           for ln in (pr.line_items or [])):
        raise ValidationError(
            "You have held or rejected some lines on this request. Finish those "
            "in the line-by-line panel — the whole request can no longer be "
            "marked paid in one go.")


@transaction.atomic
def complete_task(
    task: OmniTask, user: User, body: str, interaction_seconds: int
) -> CompletionNote:
    """Server-side completion gate: len(body) >= MIN_NOTE_CHARS.
    (The 30s dwell gate was removed per CFO 2026-07-18; interaction_seconds is
    still recorded for audit.)

    On success: write the CompletionNote, flip status -> done + stamp completed_at,
    and acknowledge any open force-modal notifications for the task.
    """
    body = (body or "").strip()
    try:
        secs = max(0, int(interaction_seconds))
    except (TypeError, ValueError):
        secs = 0
    if len(body) < CompletionNote.MIN_NOTE_CHARS:
        raise ValidationError(
            f"The completion note must be at least {CompletionNote.MIN_NOTE_CHARS} "
            f"characters (got {len(body)}) — say what you did and how."
        )
    if task.status == OmniTask.Status.DONE:
        raise ValidationError("This task is already completed.")

    # ── Duplicate payment gate at the point of payment (CFO 2026-08-03) ──────
    # PAY-DUP-01, last line of defence. Completing a payment_request task IS the
    # payment — this is the click that releases the money, so it is checked even
    # though creation and sign-off already were. It is what stops the requests
    # that predate the control: the ten sitting in the queue on 3 Aug were raised
    # before any check existed and would otherwise have been paid on a click.
    # Deliberately NOT best-effort: a duplicate must stop the completion, unlike
    # the status sync below which must never break it.
    if task.source == "payment_request":
        _assert_not_duplicate_payment(task)
        # Once the CFO has held/rejected any line, block the blanket "mark all
        # as paid" so a held line can never be swept into PAID (Fable 5).
        _assert_no_partial_line_decisions(task)

    note, _ = CompletionNote.objects.update_or_create(
        task=task,
        defaults={"author": user, "body": body, "interaction_seconds": secs},
    )
    task.status = OmniTask.Status.DONE
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "updated_at"])
    Notification.objects.filter(task=task, acknowledged=False).update(
        acknowledged=True, seen_at=timezone.now()
    )

    # Payment authorisations (bug ktshutlhedi 2026-07-25): the CFO pays a
    # request by completing its "mark as paid" task here. Advance the linked
    # PaymentRequest to the terminal PAID state so it leaves the queue — before
    # this it stayed 'pending_cfo' for ever (the payment lived on the task and
    # never fed back). ONLY a PENDING_CFO request is paid this way: at stage 1
    # the linked task is the finance sign-off task, and completing THAT must
    # never mark a payment paid (finance sign-off goes through decide(), which
    # enforces SoD and hands to the CFO). Best-effort: never block completion.
    if task.source == "payment_request":
        try:
            from .models import PaymentRequest

            pr = (PaymentRequest.objects
                  .filter(task=task, status=PaymentRequest.Status.PENDING_CFO)
                  .first())
            if pr is not None:
                pr.status = PaymentRequest.Status.PAID
                pr.save(update_fields=["status", "updated_at"])
        except Exception:  # noqa: BLE001 — payment sync must not break completion
            pass

    return note


def close_payment_request_lines(pr, user):
    """Close a per-line-decided PaymentRequest once no line is unresolved.

    Called after the CFO sets per-line status (approve/hold/reject) on a
    PENDING_CFO request. Does nothing while any line is still pending or held —
    that is how "approve 9, hold 1" keeps the request open on the last line. Once
    every line is approved or rejected it terminates the request:
      * PAID      if at least one line was approved,
      * CANCELLED if every line was rejected,
    and closes the linked OmniTask with a system completion note ("9 approved,
    1 rejected — PR-…"). Money already loaded to FNB at finance sign-off, so this
    is bookkeeping — mirrors the PAID sync in complete_task.

    PAY-DUP-01 is re-checked against the APPROVED lines only (the ones actually
    authorised) as a last line of defence; a hard clash raises ValidationError
    and the request is NOT closed (same behaviour as completing the whole task).

    Returns the terminal status string, or None if the request still has open
    lines / is not PENDING_CFO.
    """
    from .models import PaymentRequest
    if pr.status != PaymentRequest.Status.PENDING_CFO or not pr.all_lines_resolved:
        return None

    approved = pr.approved_lines()
    prog = pr.line_progress()

    if approved:
        # Duplicate safety on the lines actually being authorised. An override
        # already justified on the record carries through, exactly as at the
        # whole-request gate.
        if not _override_is_complete(pr):
            from .payment_duplicates import (
                CONTROL_CODE, blocking_message, find_duplicates, hard_total,
            )
            appr_items = [(pr.line_items or [])[i] for i in approved]
            dup = find_duplicates(appr_items, currency=pr.currency, exclude_pk=pr.pk,
                                  payee=pr.payee or '')
            if dup['hard']:
                raise ValidationError(
                    blocking_message(dup['hard'], currency=pr.currency,
                                     total_hard=hard_total(dup['hard']),
                                     context='authorise'),
                    code=CONTROL_CODE)
        pr.status = PaymentRequest.Status.PAID
    else:
        pr.status = PaymentRequest.Status.CANCELLED
    pr.save(update_fields=['status', 'updated_at'])

    task = pr.task
    if task and task.status not in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
        parts = [f"{prog['approved']} approved"]
        if prog['rejected']:
            parts.append(f"{prog['rejected']} rejected")
        note_body = ", ".join(parts) + f" — {pr.ref}"
        task.status = (OmniTask.Status.DONE if approved
                       else OmniTask.Status.CANCELLED)
        task.completed_at = timezone.now()
        task.save(update_fields=['status', 'completed_at', 'updated_at'])
        try:
            CompletionNote.objects.update_or_create(
                task=task,
                defaults={'author': user, 'body': note_body,
                          'interaction_seconds': 0})
        except Exception:  # noqa: BLE001 — the note is a bonus, never block close
            pass
        Notification.objects.filter(task=task, acknowledged=False).update(
            acknowledged=True, seen_at=timezone.now())
    return pr.status


def is_overdue(task, now=None) -> bool:
    """A task is overdue once past its deadline. Time-aware (CFO 2026-07-13): a
    task due TODAY at due_time is overdue only AFTER that time, not at midnight.
    Africa/Gaborone via timezone.localtime()."""
    if not task.due_at or task.status in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
        return False
    now = now or timezone.localtime()
    if task.due_at < now.date():
        return True
    if task.due_at == now.date() and task.due_time and now.time() > task.due_time:
        return True
    return False


def overdue_tasks_for(user, now=None) -> list:
    """Open tasks assigned to `user` that are past their deadline.

    Reuses is_overdue() so the leave gate and the reminder engine agree on what
    "overdue" means (CFO 2026-08-12: block leave while tasks are overdue — an
    employee applied for leave with overdue work outstanding and omni let it
    through because nothing ever checked). Ordered soonest-overdue first.
    """
    now = now or timezone.localtime()
    qs = (OmniTask.objects
          .filter(assignee=user, status__in=_OPEN_STATUSES,
                  due_at__isnull=False, due_at__lte=now.date())
          .order_by("due_at"))
    return [t for t in qs if is_overdue(t, now)]


def clear_task_reminders(task) -> int:
    """Acknowledge a task's standing due/overdue reminders. Call whenever a task
    reaches a terminal state (DONE/CANCELLED) OUTSIDE complete_task() — e.g. the
    dashboard status control — so the force-modal stops nagging immediately
    instead of waiting for the next sweep. Returns rows cleared."""
    return (Notification.objects
            .filter(task=task, acknowledged=False,
                    type__in=[Notification.Type.DUE_DAY, Notification.Type.OVERDUE])
            .update(acknowledged=True, seen_at=timezone.now()))


def sweep_due_and_overdue(today: date | None = None) -> dict:
    """Create due_day / overdue reminders for open tasks. Idempotent: only creates a
    new notice when no *unacknowledged* one of that type already exists for the task,
    so a standing reminder persists (frontend re-shows it) without duplicating.
    Time-aware: a task due today flips from 'due today' to 'overdue' once past its
    due_time (e.g. 16:00), so the hourly sweep builds urgency toward the deadline."""
    now = timezone.localtime()
    today = today or now.date()
    created = {"due_day": 0, "overdue": 0, "cleared": 0, "feedback_closed": 0,
               "cycle_closed": 0, "toasts_cleared": 0}

    # Close monthly-cycle tasks whose work is already recorded, BEFORE the
    # self-heal below — so the same run also acknowledges their standing
    # reminders. BOTH halves of the monthly performance cycle raise a task per
    # manager per month and nothing ever flipped it when the work was filed, so
    # a manager who filed on the 1st was still emailed a nudge every morning
    # after. Two closers on purpose — they read different completion signals
    # (feedback = a MonthlyCheckIn per report; the manager return = the return
    # row reaching submitted/cleared). Both must survive any future merge here.
    #   feedback  — bug 13869f41: a manager filed all five of her team's August
    #               check-ins on 1-Sep and was still nudged on 6, 7, 8 and 9-Sep.
    #   manager return — same flaw, caught before it fired (9-Sep-2026).
    from hris.feedback_task_close import close_completed_feedback_tasks
    from hris.manager_return_task_close import close_completed_manager_return_tasks
    created["feedback_closed"] = close_completed_feedback_tasks()
    created["cycle_closed"] = close_completed_manager_return_tasks()

    # Self-heal orphaned reminders (CFO 2026-07-15). complete_task() clears a
    # task's standing reminders, but a task can reach DONE/CANCELLED by OTHER
    # paths (the dashboard status control, the CFO decision modal, admin) that
    # don't run complete_task — leaving an unacknowledged due/overdue
    # Notification that the force-modal keeps re-showing forever ("I finished
    # it, why is it still nagging me?"). Acknowledge any unacknowledged
    # reminder whose task is no longer open, so completion via ANY path clears
    # the nag within one sweep.
    stale = Notification.objects.filter(
        acknowledged=False,
        type__in=[Notification.Type.DUE_DAY, Notification.Type.OVERDUE],
        task__status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED],
    )
    created["cleared"] = stale.update(acknowledged=True, seen_at=now)

    # The gentle "assigned to you" toast outlived its own task for ever, because
    # the self-heal above only ever cleared the two force-modal types — 368 of
    # them were sitting unacknowledged on done/cancelled tasks across 16 people
    # on 9-Sep-2026, saying they had work that no longer existed (CFO: "there is
    # tasks pending but I don't have any tasks"). Counted SEPARATELY from
    # `cleared`, which callers and tests read as "standing nags cleared" — a
    # toast is not a nag, and folding the two together would change what that
    # number means.
    created["toasts_cleared"] = Notification.objects.filter(
        acknowledged=False,
        type=Notification.Type.ASSIGN_DAY,
        task__status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED],
    ).update(acknowledged=True, seen_at=now)

    base = OmniTask.objects.filter(status__in=_OPEN_STATUSES, due_at__isnull=False)

    def _raise(task, ntype, key):
        if not Notification.objects.filter(task=task, type=ntype, acknowledged=False).exists():
            Notification.objects.create(recipient=task.assignee, task=task, type=ntype)
            created[key] += 1

    for task in base.filter(due_at=today):
        if is_overdue(task, now):
            _raise(task, Notification.Type.OVERDUE, "overdue")     # due today, past due_time
        else:
            _raise(task, Notification.Type.DUE_DAY, "due_day")

    for task in base.filter(due_at__lt=today):
        _raise(task, Notification.Type.OVERDUE, "overdue")
    return created


def email_open_reminders(today: date | None = None) -> dict:
    """Email each person a nudge listing their tasks that are due today or overdue
    (CFO 2026-07-13 'make omni send reminders'). Runs alongside the in-app sweep;
    the daily cron calls it once so nobody is spammed. Only tasks WITH a deadline
    are chased — undated tasks carry no reminder by design (the 'be fair' rule).
    Returns {'people': n, 'emailed': n}.

    Consolidated 'morning to-dos' (CONSOLIDATED_EMAILS_ENABLED, CFO 2026-08-05):
    when the flag is ON this is the SINGLE 06:30 sender. A line manager who has an
    unexplained team-tracking gap gets their Manager Accountability note folded
    into this same email (and send_manager_accountability self-skips), so they get
    ONE morning email, not two. A manager with a gap but NO due tasks still gets
    ONE email — just the note. Someone with neither tasks nor a gap gets nothing.
    Flag OFF ⇒ every branch below is skipped and this is byte-for-byte the
    per-person task digest it has always been.
    """
    from django.conf import settings

    now = timezone.localtime()
    today = today or now.date()

    # Manager-accountability sections, keyed by lower-case manager email. Empty
    # (and never even computed) when the flag is off. Best-effort: the note is
    # ADDITIVE, so a Time Doctor outage must never block the task reminders — any
    # failure just means no section is folded today (managers still get chased by
    # the next clean run). managers_on_the_hook persists the notes the answer-link
    # and the escalation sweep depend on, so this is not a read-only compute.
    consolidated = bool(getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False))
    acct: dict[str, str] = {}
    esc_cc: list[str] | None = None
    if consolidated:
        try:
            from hris.manager_accountability import (
                ESCALATION_EMAILS, managers_on_the_hook,
            )
            acct = managers_on_the_hook() or {}
            esc_cc = list(ESCALATION_EMAILS)
        except Exception:  # noqa: BLE001 — accountability is additive; never break the nudge
            acct = {}
            # Additive, so we swallow rather than break the task nudge — but never
            # silently: a failure here also means the ManagerAccountabilityNote rows
            # + answer-link tokens were NOT persisted, so the escalation sweep has
            # nothing to run on. Log it LOUD so the ops digest / nightly QC surfaces it.
            logger.exception('managers_on_the_hook failed — no accountability folded '
                             'into task reminders and no notes persisted this run.')

    open_dated = (OmniTask.objects
                  .filter(status__in=_OPEN_STATUSES, due_at__isnull=False, due_at__lte=today)
                  .select_related("assignee")
                  .order_by("due_at"))
    by_person: dict[int, list] = {}
    for t in open_dated:
        by_person.setdefault(t.assignee_id, []).append(t)

    emailed = 0
    for tasks in by_person.values():
        assignee = tasks[0].assignee
        email = (assignee.email or "").strip()
        if not email:
            continue
        # One digest per person per day, no matter how many times the sweep runs
        # (duplicate cron entry / manual re-run / retry). The unique constraint
        # on (recipient, sent_on) makes this atomic even for two runs in the same
        # minute — the loser gets created=False and skips (Pramod, 2026-07-24:
        # the digest was firing 5-6x/day). The claim row is deleted below if the
        # send raises, so a genuine failure can still be retried later that day.
        log, created = TaskReminderEmailLog.objects.get_or_create(
            recipient=assignee, sent_on=today)
        if not created:
            continue
        from django.utils.html import escape
        rows = []
        for t in tasks:
            overdue = is_overdue(t, now)
            tag = ('<span style="color:#B04E00;font-weight:600;">Overdue</span>'
                   if overdue else '<span style="color:#0D1B2A;">Due today</span>')
            # Show the task's actual details under its title (CFO 2026-07-28 —
            # the reminder used to carry only the title). Trimmed to keep the
            # digest scannable; escaped because it's staff-typed free text.
            raw = (t.body or '').strip()
            detail = ''
            if raw:
                snippet = raw[:300] + ('…' if len(raw) > 300 else '')
                detail = ('<div style="color:#6B7280;font-size:12px;margin-top:3px;">'
                          f'{escape(snippet).replace(chr(10), "<br>")}</div>')
            rows.append(
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
                f'<strong>{escape(t.title)}</strong>{detail}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{tag}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
                f'{t.due_at:%d %b}</td></tr>')
        first = assignee.get_full_name().split(' ')[0] if assignee.get_full_name() else assignee.username
        html = (
            f'<p>{first},</p>'
            f'<p>You have {len(tasks)} task(s) needing attention in Omni:</p>'
            '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
            '<tr style="background:#0D1B2A;color:#fff;text-align:left;">'
            '<th style="padding:7px 10px;">Task</th><th style="padding:7px 10px;">Status</th>'
            '<th style="padding:7px 10px;">Due</th></tr>'
            + ''.join(rows) +
            '</table>'
            '<p style="margin-top:12px;">Open Omni &rarr; <strong>Tasks</strong> to update or '
            'complete them.</p>')

        # Fold this person's Manager Accountability note into the SAME email when
        # the flag is on and they are a manager with a gap. Pop it so they are NOT
        # also sent a standalone note below, and CC Human Resources
        # (ESCALATION_EMAILS) — never the CFO — on the merged send, matching the
        # standalone note's routing (people matters are HR matters, CFO 2026-08-03).
        subject = f'Omni — {len(tasks)} task(s) due or overdue'
        cc = None
        section = acct.pop(email.lower(), None) if consolidated else None
        if section:
            html = html + '<div style="height:20px"></div>' + section
            subject = 'Omni — your morning to-dos (tasks + team tracking)'
            cc = esc_cc
        try:
            from core.notifications import send_html_with_cfo_cc
            emailed += send_html_with_cfo_cc(
                subject=subject,
                html=html, to=[email], cc=cc, cc_cfo=False)
        except Exception:  # noqa: BLE001 — one bad address must not stop the rest
            # Send failed — drop the claim row so a later run today can retry
            # this person (the guard only suppresses genuine duplicate sends).
            log.delete()
            continue

    # Managers with an accountability gap but NO due tasks (flag ON only): their
    # single email is just the note. Same one-per-person-per-day dedup and same HR
    # CC. A manager with no omni User account can't be deduped via the send-ledger
    # (its recipient is a User FK) — fall back to an unguarded send, which is no
    # worse than the standalone command this replaces (it had no send-ledger and
    # relied on running once).
    if consolidated and acct:
        from core.notifications import send_html_with_cfo_cc
        for mgr_email, section in acct.items():
            user = User.objects.filter(email__iexact=mgr_email, is_active=True).first()
            log = None
            if user is not None:
                log, created = TaskReminderEmailLog.objects.get_or_create(
                    recipient=user, sent_on=today)
                if not created:
                    continue
            try:
                emailed += send_html_with_cfo_cc(
                    subject="Omni — your team's tracking needs action",
                    html=section, to=[mgr_email], cc=esc_cc, cc_cfo=False)
            except Exception:  # noqa: BLE001 — one bad address must not stop the rest
                if log is not None:
                    log.delete()
                continue

    return {"people": len(by_person), "emailed": emailed}


def welcome_back_digest(today: date | None = None) -> dict:
    """ONE warm 'welcome back' email per person after a public-holiday break —
    the tasks that piled up (due/overdue) in a single message instead of a burst
    of separate reminders (CFO directive 2026-07-19). Same data source as
    email_open_reminders, friendlier framing; the sweep skips its per-item task
    email on this day so nobody is double-mailed. Returns {'people','emailed'}."""
    now = timezone.localtime()
    today = today or now.date()
    open_dated = (OmniTask.objects
                  .filter(status__in=_OPEN_STATUSES, due_at__isnull=False, due_at__lte=today)
                  .select_related("assignee")
                  .order_by("due_at"))
    by_person: dict[int, list] = {}
    for t in open_dated:
        by_person.setdefault(t.assignee_id, []).append(t)

    emailed = 0
    for tasks in by_person.values():
        assignee = tasks[0].assignee
        email = (assignee.email or "").strip()
        if not email:
            continue
        from django.utils.html import escape
        rows = []
        for t in tasks:
            overdue = is_overdue(t, now)
            tag = ('<span style="color:#B04E00;font-weight:600;">Overdue</span>'
                   if overdue else '<span style="color:#0D1B2A;">Due</span>')
            raw = (t.body or '').strip()
            detail = ''
            if raw:
                snippet = raw[:300] + ('…' if len(raw) > 300 else '')
                detail = ('<div style="color:#6B7280;font-size:12px;margin-top:3px;">'
                          f'{escape(snippet).replace(chr(10), "<br>")}</div>')
            rows.append(
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
                f'<strong>{escape(t.title)}</strong>{detail}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{tag}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
                f'{t.due_at:%d %b}</td></tr>')
        first = assignee.get_full_name().split(' ')[0] if assignee.get_full_name() else assignee.username
        html = (
            f'<p>Welcome back, {first}.</p>'
            f'<p>Hope you had a good break. Here is what built up while Omni was quiet '
            f'over the public holidays — {len(tasks)} task(s) to pick up:</p>'
            '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
            '<tr style="background:#0D1B2A;color:#fff;text-align:left;">'
            '<th style="padding:7px 10px;">Task</th><th style="padding:7px 10px;">Status</th>'
            '<th style="padding:7px 10px;">Due</th></tr>'
            + ''.join(rows) +
            '</table>'
            '<p style="margin-top:12px;">Open Omni &rarr; <strong>Tasks</strong> to update them, '
            'and check <strong>My Approvals</strong> for anything waiting on your sign-off.</p>')
        try:
            from core.notifications import send_html_with_cfo_cc
            emailed += send_html_with_cfo_cc(
                subject='Omni — welcome back: your tasks after the break',
                html=html, to=[email], cc_cfo=False)
        except Exception:  # noqa: BLE001 — one bad address must not stop the rest
            continue
    return {"people": len(by_person), "emailed": emailed}


def completion_rate(user: User, start: date, end: date) -> dict:
    """done / assigned for tasks whose due_at falls in [start, end] (EOS ~90% bench).
    NOTE: per-person analytics = employee monitoring under the Botswana DPA 2024;
    gated on a DPIA + privacy notice before staff-facing go-live.

    ⚠️ RECONCILIATION (2026-09-01): this counts a DIFFERENT thing from the Alpha
    League (`cfo_views._score_over`: priority-weighted, rolling 14 days, keyed on
    completed_at) and from HR's monthly panel (`hris.perf_panel`: keyed on
    created_at). This one is windowed by DUE date and does not apply
    `_real_tasks()`. It is not wired to any live screen. Do NOT surface it next to
    the League / Rewards / HR figures without first agreeing one shared definition
    — otherwise it becomes a fourth "tasks done" number that cannot match the
    others (the exact "screens don't talk to each other" class the CFO flagged)."""
    qs = OmniTask.objects.filter(assignee=user, due_at__gte=start, due_at__lte=end)
    assigned = qs.count()
    done = qs.filter(status=OmniTask.Status.DONE).count()
    return {
        "assigned": assigned,
        "done": done,
        "rate": round(done / assigned, 3) if assigned else None,
    }
