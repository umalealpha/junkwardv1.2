"""
regulatory/tax_workflow.py — the statutory-tax reminder engine.

Two jobs, both idempotent, both safe to run twice in a day:

    generate_tasks()    materialise the computed calendar into TaxComplianceTask
                        rows, 18 months ahead. Never touches a row that already
                        exists, so a filing that is half-done is never reset.

    run_reminders()     advance the state machine and send the day's emails.

The state machine, exactly as the reporter specified it:

    SCHEDULED  --(today >= target_date)-->  REMINDING
    REMINDING  --(preparer marks done)  -->  PREPARER_COMPLETE   [reminders CONTINUE]
    *          --(CFO verifies)         -->  VERIFIED, or LATE if the target passed
    *          --(due date passed, not verified) --> BREACH

BREACH is the one transition this module makes on its own, because it is the one
nobody wants to make. Everything else is a human action through the API.

THE THING THIS MODULE MUST NOT DO: stop reminding because the preparer said they
were finished. PREPARER_COMPLETE is in REMINDING_STATUSES on purpose. If that
ever changes, the CFO verification step becomes optional in practice and the
whole control is theatre.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc

from .models import (TaxCalendarEditor, TaxComplianceSettings, TaxComplianceTask,
                     TaxObligationOwner)
from .tax_calendar import REMINDER_LEAD_DAYS, TaxType, build_calendar

logger = logging.getLogger(__name__)

# House brand. Dark navy + orange, per the Finance standard.
NAVY   = '#0D1B2A'
ORANGE = '#F4A623'
RED    = '#B3261E'


def today_gabs():
    """Gaborone's today. `timezone.localdate()` respects settings.TIME_ZONE
    (Africa/Gaborone); `date.today()` returns the server's UTC day and rolls
    over two hours early. Never use the latter in this codebase."""
    return timezone.localdate()


# ── generation ───────────────────────────────────────────────────────────────

def generate_tasks(*, months_ahead: int | None = None) -> dict:
    """Create any missing TaxComplianceTask rows for the window ahead.

    Matching is on `obligation_key`, which is unique, so this can run nightly
    forever without duplicating a filing. Existing rows are left completely
    alone — including their status, owner and notes.
    """
    settings_row = TaxComplianceSettings.load()
    months = months_ahead or settings_row.months_ahead

    today = today_gabs()
    # Reach one lead-window back so a deadline that is already inside its
    # reminder window when this first runs still gets a row.
    start = today - timedelta(days=REMINDER_LEAD_DAYS)
    end   = today + timedelta(days=int(months * 30.5))

    obligations = build_calendar(
        start, end,
        vat_cycle=settings_row.vat_cycle,
        fy_end_month=settings_row.financial_year_end_month,
        vat_due_day=settings_row.vat_due_day,
    )

    owners = {o.tax_type: o.owner for o in TaxObligationOwner.objects.select_related('owner')}
    existing = set(
        TaxComplianceTask.objects
        .filter(obligation_key__in=[o.key for o in obligations])
        .values_list('obligation_key', flat=True)
    )

    created = 0
    for ob in obligations:
        if ob.key in existing:
            continue
        TaxComplianceTask.objects.create(
            obligation_key = ob.key,
            tax_type       = ob.tax_type,
            period_label   = ob.period_label,
            period_start   = ob.period_start,
            period_end     = ob.period_end,
            due_date       = ob.due_date,
            target_date    = ob.target_date,
            owner          = owners.get(ob.tax_type),
            status         = TaxComplianceTask.Status.SCHEDULED,
        )
        created += 1

    return {'considered': len(obligations), 'created': created}


# ── the daily run ────────────────────────────────────────────────────────────

def run_reminders(*, dry_run: bool = False) -> dict:
    """Advance every open task and send the day's emails.

    Returns counts so the cron log says what happened rather than just "ok".
    """
    today = today_gabs()
    stats = {'activated': 0, 'breached': 0, 'reminders_sent': 0, 'breach_alerts': 0, 'skipped_no_owner': 0}

    tasks = list(
        TaxComplianceTask.objects
        .filter(status__in=TaxComplianceTask.OPEN_STATUSES)
        .select_related('owner')
        .order_by('due_date')
    )

    for task in tasks:
        # 1. SCHEDULED -> REMINDING once the 10-day window opens.
        if task.status == TaxComplianceTask.Status.SCHEDULED and today >= task.target_date:
            task.status = TaxComplianceTask.Status.REMINDING
            if not dry_run:
                task.save(update_fields=['status', 'updated_at'])
            stats['activated'] += 1

        # 2. Anything still unverified once the statutory date has PASSED is a
        #    breach. Note `>`, not `>=`: filing ON the due date is on time.
        if (today > task.due_date
                and task.status != TaxComplianceTask.Status.BREACH
                and not task.is_closed):
            task.status = TaxComplianceTask.Status.BREACH
            if not dry_run:
                task.save(update_fields=['status', 'updated_at'])
            stats['breached'] += 1

            # The breach alert is its own email to the CFO, sent ONCE. It is a
            # different severity from the daily nag and must not be buried in it.
            if not task.breach_flagged_at:
                if not dry_run:
                    _send_breach_alert(task, today)
                    task.breach_flagged_at = timezone.now()
                    task.save(update_fields=['breach_flagged_at', 'updated_at'])
                stats['breach_alerts'] += 1

        # 3. The daily reminder.
        if task.status not in TaxComplianceTask.REMINDING_STATUSES:
            continue
        if task.last_reminded_on == today:
            continue  # already sent today — a second cron run must not double up
        if task.owner is None or not (task.owner.email or '').strip():
            # An unassigned statutory deadline is itself a problem, but silently
            # skipping it would hide it. Counted, and it shows red on the board.
            stats['skipped_no_owner'] += 1
            continue

        if not dry_run:
            _send_reminder(task, today)
            task.last_reminded_on = today
            task.reminder_count += 1
            task.save(update_fields=['last_reminded_on', 'reminder_count', 'updated_at'])
        stats['reminders_sent'] += 1

    return stats


# ── human actions (called by the API) ────────────────────────────────────────

@transaction.atomic
def mark_preparer_complete(task: TaxComplianceTask, user, note: str = '') -> TaxComplianceTask:
    """The preparer says it is filed. This does NOT close the task and does NOT
    stop the reminders — only CFO verification does that."""
    task.completed_by    = user
    task.completed_at    = timezone.now()
    task.completion_note = note or task.completion_note
    if task.status in (TaxComplianceTask.Status.SCHEDULED, TaxComplianceTask.Status.REMINDING):
        task.status = TaxComplianceTask.Status.PREPARER_COMPLETE
    task.save()
    return task


@transaction.atomic
def verify_and_close(task: TaxComplianceTask, user, note: str = '', late_reason: str = '') -> TaxComplianceTask:
    """CFO verification. The only thing that closes a task.

    Lands in LATE rather than VERIFIED when the preparer finished after the
    10-day internal target but still before the statutory date — the distinction
    the reporter asked for, so an internal service miss is visible without being
    mislabelled as a regulatory breach.

    A task already in BREACH is NOT closed here. A missed statutory date is
    settled through `close_breach`, by the CFO, with a written reason.
    """
    if task.status == TaxComplianceTask.Status.BREACH:
        raise ValueError(
            'This task is in BREACH — the statutory date was missed. Close it '
            'through the breach route, which requires a written reason.'
        )

    finished_on = (task.completed_at or timezone.now()).astimezone(
        timezone.get_current_timezone()
    ).date()

    task.verified_by   = user
    task.verified_at   = timezone.now()
    task.verified_note = note
    if finished_on > task.target_date:
        task.status      = TaxComplianceTask.Status.LATE
        task.late_reason = late_reason
    else:
        task.status = TaxComplianceTask.Status.VERIFIED
    task.save()
    return task


@transaction.atomic
def close_breach(task: TaxComplianceTask, user, breach_note: str) -> TaxComplianceTask:
    """Close a BREACH. CFO only, and only with a written reason — enforced at the
    API layer for permission and here for content. The note is the audit trail
    that a BURS penalty assessment or an NBFIRA inspection will ask for."""
    if task.status != TaxComplianceTask.Status.BREACH:
        raise ValueError('Not a breach.')
    if not (breach_note or '').strip():
        raise ValueError('A breach cannot be closed without a written reason.')
    task.breach_note  = breach_note.strip()
    task.verified_by  = user
    task.verified_at  = timezone.now()
    task.status       = TaxComplianceTask.Status.LATE
    task.save()
    return task


# ── emails ───────────────────────────────────────────────────────────────────

def _shell(title: str, accent: str, rows: str, body: str) -> str:
    return f"""
<div style="font-family:'Book Antiqua',Georgia,serif;max-width:640px;margin:0 auto;color:{NAVY}">
  <div style="background:{NAVY};padding:18px 24px">
    <div style="color:#fff;font-size:18px;letter-spacing:.3px">{title}</div>
    <div style="color:{accent};font-size:12px;margin-top:4px">Alpha Direct Insurance — statutory tax compliance</div>
  </div>
  <div style="padding:22px 24px;background:#fff;border:1px solid #e6e8ec;border-top:0">
    {body}
    <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:14px">{rows}</table>
  </div>
</div>""".strip()


def _row(k: str, v: str, strong: bool = False) -> str:
    weight = '600' if strong else '400'
    return (f'<tr><td style="padding:7px 0;color:#5a6472;width:190px">{k}</td>'
            f'<td style="padding:7px 0;font-weight:{weight}">{v}</td></tr>')


def _send_reminder(task: TaxComplianceTask, today) -> None:
    days = task.days_to_due(today)
    breach = task.status == TaxComplianceTask.Status.BREACH
    accent = RED if breach else ORANGE

    if breach:
        headline = (f'<p style="margin:0 0 12px;color:{RED};font-weight:600">'
                    f'The statutory deadline passed {abs(days)} day(s) ago and this filing '
                    f'is still not verified. This is a live BURS compliance breach.</p>')
    elif task.status == TaxComplianceTask.Status.PREPARER_COMPLETE:
        headline = ('<p style="margin:0 0 12px">Marked complete by the preparer and waiting for '
                    'CFO verification. The reminder continues until it is verified.</p>')
    else:
        headline = (f'<p style="margin:0 0 12px">Due in <strong>{days} day(s)</strong>. '
                    f'Please prepare and file, then mark it complete in Omni.</p>')

    rows = (
        _row('Obligation', task.label, strong=True)
        + _row('Period', f'{task.period_start:%d %b %Y} – {task.period_end:%d %b %Y}')
        + _row('Statutory due date', f'{task.due_date:%d %B %Y}', strong=True)
        + _row('Internal target', f'{task.target_date:%d %B %Y}')
        + _row('Status', task.get_status_display())
        + _row('Owner', task.owner.get_full_name() or task.owner.username)
    )

    body = headline + (
        '<p style="margin:12px 0 0;font-size:13px;color:#5a6472">'
        'Open Omni → Tax Calendar to mark this complete. A filing is only closed '
        'once the CFO has verified it.</p>'
    )

    subject = ('[BREACH] ' if breach else '') + f'{task.label} — due {task.due_date:%d %b %Y}'

    # The CFO is copied only from 3 days out, or immediately on a breach. Before
    # that it is the preparer's to run, and copying him from day 10 would turn
    # ~200 emails a year into background noise.
    send_html_with_cfo_cc(
        subject = subject,
        html    = _shell('Statutory filing reminder', accent, rows, body),
        to      = [task.owner.email],
        cc_cfo  = breach or task.escalates_to_cfo(today),
    )


def _send_breach_alert(task: TaxComplianceTask, today) -> None:
    """Sent ONCE, to the CFO, the day a statutory date is missed. Deliberately a
    separate email from the daily reminder: a breach is a different severity and
    must not arrive looking like the nag that preceded it."""
    owner = task.owner.get_full_name() or task.owner.username if task.owner else 'UNASSIGNED'
    rows = (
        _row('Obligation', task.label, strong=True)
        + _row('Statutory due date', f'{task.due_date:%d %B %Y}', strong=True)
        + _row('Days overdue', str(abs(task.days_to_due(today))), strong=True)
        + _row('Owner', owner)
        + _row('Preparer marked done', f'{task.completed_at:%d %b %Y}' if task.completed_at else 'No')
        + _row('Reminders sent', str(task.reminder_count))
    )
    body = (
        f'<p style="margin:0 0 12px;color:{RED};font-weight:600">'
        f'A statutory filing deadline has been missed.</p>'
        '<p style="margin:0 0 12px">This carries BURS penalty and interest exposure and is '
        'materially more serious than an internally late submission. It stays on the '
        'compliance board until you close it with a written reason.</p>'
    )
    send_html_with_cfo_cc(
        subject = f'[BREACH] Statutory deadline missed — {task.label}',
        html    = _shell('STATUTORY COMPLIANCE BREACH', RED, rows, body),
        to      = [],
        cc_cfo  = True,
    )


@transaction.atomic
def change_due_date(task: TaxComplianceTask, user, new_due, reason: str) -> TaxComplianceTask:
    """Move a statutory date. CFO instruction 2026-09-11 — Oprah, Kago and
    Legakwa can do this, not only the CFO, because BURS shifts dates (public
    holidays, extensions) and waiting on one person to retype it is how a
    deadline gets missed.

    The guardrails that make that safe, all of them load-bearing:

    * The ORIGINAL date is kept forever. A moved date must never be able to
      rewrite what the deadline actually was.
    * A written reason is mandatory. "Changed by Kago" with no why is not an
      audit trail.
    * A BREACH cannot be undone by moving the date. This is the one that
      matters: two of the three editors are also preparers, so without it a
      late filing could be made to look on time by dragging its deadline
      forward. The breach stands; only the CFO closes it, in writing.
    * The CFO is emailed on every change, so the permission is visible rather
      than silent.
    """
    if not (reason or '').strip():
        raise ValueError('A date change needs a written reason.')
    if task.status == TaxComplianceTask.Status.BREACH:
        raise ValueError(
            'This filing is already in breach. Moving the date cannot undo that — '
            'the deadline was missed. Close the breach with a reason instead.'
        )
    # The breach guard above protects the STATE. On its own that is worth very
    # little, because the same person can stop the state from ever being reached:
    # BREACH is only raised by the nightly sweep when `today > due_date`, so a
    # preparer had the whole due day — and until 06:30 the next morning — to push
    # their own deadline later and be recorded as on time, with no breach, no
    # alert, and a green board. Guard 3 would simply never have fired for anyone
    # who knew the system. (Fable, PR #885; CFO approved this narrowing.)
    #
    # So: nobody extends their OWN filing. Earlier is fine, someone else's is
    # fine — it is pushing your own deadline out that is refused. Same separation
    # the verify endpoint already enforces.
    if (new_due > task.due_date
            and not getattr(user, 'is_superuser', False)
            and user.id in {task.owner_id, task.completed_by_id}):
        raise ValueError(
            'You prepare this filing, so you cannot push its deadline later. '
            'Ask someone else in Finance, or the CFO, to move it.'
        )
    if task.is_closed:
        raise ValueError('This filing is closed. Its dates are history now.')
    if new_due == task.due_date:
        raise ValueError('That is the date it already has.')

    old_due = task.due_date
    # Keep the FIRST computed date, not the previous one — otherwise a second
    # change quietly erases what the rules originally said.
    if task.original_due_date is None:
        task.original_due_date = old_due

    task.due_date           = new_due
    task.target_date        = new_due - timedelta(days=REMINDER_LEAD_DAYS)
    task.date_change_reason = reason.strip()
    task.date_changed_by    = user
    task.date_changed_at    = timezone.now()

    # Moving the date backwards can push a task out of its reminder window, or
    # forwards can pull it in. Re-derive rather than leaving a stale status.
    today = today_gabs()
    if task.status in (TaxComplianceTask.Status.SCHEDULED, TaxComplianceTask.Status.REMINDING):
        task.status = (TaxComplianceTask.Status.REMINDING if today >= task.target_date
                       else TaxComplianceTask.Status.SCHEDULED)
    # Let it remind again today under the new date rather than staying silent
    # because a reminder already went out under the old one.
    task.last_reminded_on = None
    task.save()

    _send_date_change_notice(task, user, old_due)
    return task


def _send_date_change_notice(task: TaxComplianceTask, user, old_due) -> None:
    """Tell the CFO a statutory date moved. Sent every time, no batching — the
    whole reason a preparer is trusted with this permission is that using it is
    visible."""
    who = user.get_full_name() or user.username
    rows = (
        _row('Obligation', task.label, strong=True)
        + _row('Was due', f'{old_due:%d %B %Y}')
        + _row('Now due', f'{task.due_date:%d %B %Y}', strong=True)
        + _row('Originally computed', f'{task.original_due_date:%d %B %Y}')
        + _row('New prepare-by', f'{task.target_date:%d %B %Y}')
        + _row('Changed by', who, strong=True)
        + _row('Reason', task.date_change_reason)
    )
    direction = 'LATER' if task.due_date > old_due else 'EARLIER'
    body = (
        f'<p style="margin:0 0 12px">A statutory filing date was moved <strong>{direction}</strong> '
        f'by {who}.</p>'
        '<p style="margin:0 0 12px;font-size:13px;color:#5a6472">You are told about every date '
        'change because the people who can make them also prepare the filings. The original '
        'computed date is kept on the record and a date change can never clear a breach.</p>'
    )
    send_html_with_cfo_cc(
        subject = f'Tax date changed — {task.label} now due {task.due_date:%d %b %Y}',
        html    = _shell('Statutory date changed', ORANGE, rows, body),
        to      = [],
        cc_cfo  = True,
    )


def can_edit_dates(user) -> bool:
    """The CFO, or someone explicitly on the TaxCalendarEditor list.

    Job title deliberately does NOT grant this on its own. It used to, and that
    was a trap: all three named editors are Finance Managers on prod, so they
    passed through the title gate while the list itself sat empty — which meant
    taking the right away from one of them by removing their row would have done
    nothing at all, silently. The list is now the only grant, so what the page
    shows is what is true, and adding and removing a person both work.

    (Found by Fable on PR #885. Its reading of the live titles was wrong — it
    had Oprah as an Accountant and expected her to be locked out — but the
    underlying point was right and the live check made it sharper, not softer.)
    """
    if getattr(user, 'is_superuser', False):
        return True
    return TaxCalendarEditor.objects.filter(user=user).exists()
