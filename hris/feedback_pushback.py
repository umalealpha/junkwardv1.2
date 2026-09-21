"""
hris/feedback_pushback.py — the employee's right of reply, with teeth.

CFO 2026-08-26: "we will create a feature where the employee doesn't accept the
feedback and rejects and requests the manager to put additional comments so it's
going to be fair."

Declining was already possible (EmployeeDecision, 18-Aug). What was missing is
the half that makes it fair: a decline that the manager is REQUIRED to answer.
So:

  request_comments()  — the employee asks for more. Sets a flag, emails the
                        manager, and raises a task on the manager's board so it
                        cannot be closed by ignoring the email.
  answer_request()    — the manager replies. Only this clears the flag.
  outstanding()       — everything still waiting on a manager, for the chase job.

An auto-posted month is the case this matters most: nobody wrote it, so the
employee's push-back is the ONLY way a human ever looks at it. request_comments
therefore works on an auto-posted check-in too, and routes to the manager the
system would have asked in the first place.
"""
from __future__ import annotations

import logging

import datetime as dt

from django.core.exceptions import ValidationError
from django.utils.html import escape
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

MIN_REQUEST_CHARS = 30
MIN_ANSWER_CHARS = 50

def _html_text(value) -> str:
    """Human-typed text for an email body: ESCAPE first, then line breaks.

    Both of these carry words an employee or manager typed. Interpolating them
    raw let anything HTML-shaped through into the message.
    """
    return escape((value or '').strip()).replace('\n', '<br>')



def request_comments(checkin, *, reason: str, user=None):
    """The employee says: I don't accept this, tell me more.

    The record is committed FIRST, on its own, and the email + task come after.
    That ordering is load-bearing: when the side effects lived inside the same
    atomic block, a failed OmniTask (assignee is non-nullable and an auto-posted
    month has no reviewer) raised, got swallowed by the except, and Django then
    rolled the WHOLE block back on exit — silently. request_comments returned
    "OK" and the employee's rejection had vanished. Never put a swallowed write
    inside the transaction that holds the thing you must not lose.
    """
    reason = (reason or '').strip()
    if len(reason) < MIN_REQUEST_CHARS:
        raise ValidationError(
            f'Say what you disagree with — at least {MIN_REQUEST_CHARS} characters.')
    if checkin.is_locked:
        raise ValidationError('This feedback is already signed off by both sides.')

    with transaction.atomic():
        checkin.employee_decision = checkin.EmployeeDecision.DECLINE
        checkin.employee_response = reason
        checkin.employee_requested_comments = True
        checkin.employee_requested_at = timezone.now()
        # A new request re-opens the loop: any previous manager answer is stale.
        checkin.manager_followup = ''
        checkin.manager_followup_at = None
        checkin.manager_followup_by = None
        checkin.full_clean()
        checkin.save()

    # Side effects only — a failure here must never lose the request above.
    _notify_manager(checkin)
    _raise_manager_task(checkin)
    return checkin


def answer_request(checkin, *, comments: str, user=None):
    """The manager answers. The ONLY thing that clears the request."""
    comments = (comments or '').strip()
    if not checkin.employee_requested_comments:
        raise ValidationError('There is no outstanding request on this feedback.')
    if len(comments) < MIN_ANSWER_CHARS:
        raise ValidationError(
            f'Give a real answer — at least {MIN_ANSWER_CHARS} characters. '
            f'The employee asked a specific question.')

    with transaction.atomic():
        checkin.manager_followup = comments
        checkin.manager_followup_at = timezone.now()
        checkin.manager_followup_by = user
        checkin.employee_requested_comments = False
        # The employee has NOT been made to accept it — they simply now have an
        # answer, and can accept or hold their position.
        checkin.full_clean()
        checkin.save()

    _notify_employee_answered(checkin)          # side effect, outside the write
    return checkin


def outstanding(days_old: int | None = None):
    """Every feedback where an employee is still waiting on their manager."""
    from hris.performance_feedback_models import MonthlyCheckIn
    qs = MonthlyCheckIn.objects.filter(employee_requested_comments=True)
    if days_old:
        cutoff = timezone.now() - dt.timedelta(days=days_old)
        qs = qs.filter(employee_requested_at__lte=cutoff)
    return qs.select_related('profile__employee', 'reviewer')


# --------------------------------------------------------------------------- #
def _manager_email(checkin) -> str:
    """The reviewer if there is one; otherwise the profile's line manager —
    an auto-posted month has no reviewer, and that is exactly the case where
    somebody still has to answer."""
    r = getattr(checkin, 'reviewer', None)
    if r and (r.email or '').strip():
        return r.email.strip()
    # co_manager matters: _reports() resolves BOTH, so a profile with only a
    # co-manager does get auto-posted — and without this fallback their rejection
    # reached nobody at all (Fable, 2026-08-26).
    for attr in ('manager', 'co_manager'):
        mgr = getattr(checkin.profile, attr, None)
        addr = (getattr(mgr, 'email', '') or '').strip() if mgr else ''
        if addr:
            return addr
    return ''


def _notify_manager(checkin):
    from core.notifications import send_html_with_cfo_cc
    to = _manager_email(checkin)
    if not to:
        log.warning('checkin %s: employee asked for comments but no manager email',
                    checkin.pk)
        return
    emp = checkin.profile.employee.full_name
    period = f'{checkin.period_year}-{checkin.period_month:02d}'
    auto = ('<p style="margin:0 0 12px;color:#D14343">This month\'s feedback was '
            'posted by Omni because no feedback came back from you, so this is the '
            'first time a person has looked at it.</p>'
            if checkin.auto_posted else '')
    html = (f'<div style="font-family:Book Antiqua,Georgia,serif;font-size:14px;'
            f'line-height:1.6;color:#222;max-width:620px">'
            f'<p><b>{emp}</b> does not accept their {period} feedback and has asked '
            f'you to add comments.</p>{auto}'
            f'<p style="margin:0 0 4px;color:#6B7280;font-size:12px">What they said</p>'
            f'<p style="margin:0 0 14px;padding:10px;background:#f7f9fb;'
            f'border-radius:8px">{_html_text(checkin.employee_response)}</p>'
            f'<p>Please answer them in Omni. It stays on your board until you do.</p>'
            f'<p>Regards,<br><b>Omni</b></p></div>')
    send_html_with_cfo_cc(
        subject=f'{emp} has asked you to comment on their {period} feedback',
        html=html, to=[to],
        text_fallback=f'{emp} does not accept their {period} feedback and asked for '
                      f'your comments.',
        no_reply=False, allow_named_exec=True)


def _notify_employee_answered(checkin):
    from core.notifications import send_html_with_cfo_cc
    emp_user = getattr(checkin.profile.employee, 'user', None)
    to = (getattr(emp_user, 'email', '') or '').strip()
    if not to:
        return
    period = f'{checkin.period_year}-{checkin.period_month:02d}'
    html = (f'<div style="font-family:Book Antiqua,Georgia,serif;font-size:14px;'
            f'line-height:1.6;color:#222;max-width:620px">'
            f'<p>Your manager has answered your question on the {period} feedback.</p>'
            f'<p style="margin:0 0 14px;padding:10px;background:#f7f9fb;'
            f'border-radius:8px">{_html_text(checkin.manager_followup)}</p>'
            f'<p>You can accept it now, or keep your position on record — both are '
            f'fine, and both are kept.</p>'
            f'<p>Regards,<br><b>Omni</b></p></div>')
    send_html_with_cfo_cc(
        subject=f'Your manager answered — {period} feedback',
        html=html, to=[to],
        text_fallback='Your manager has answered your question on your feedback.',
        no_reply=False, allow_named_exec=True, cc_cfo=False)


def _manager_user(checkin):
    """A real User to hang the task on. OmniTask.assignee/assigner are NOT
    nullable, so an auto-posted month (reviewer=None) has to resolve one from
    the profile's line manager or the task simply cannot be created."""
    r = getattr(checkin, 'reviewer', None)
    if r is not None:
        return r
    for attr in ('manager', 'co_manager'):
        mgr = getattr(checkin.profile, attr, None)
        user = getattr(mgr, 'user', None) if mgr else None
        if user is not None:
            return user
    return None


def _raise_manager_task(checkin):
    """A board task, so ignoring the email is not enough to make it go away."""
    user = _manager_user(checkin)
    if user is None:
        log.info('checkin %s: no manager login — email only, no board task',
                 checkin.pk)
        return
    try:
        from core.models import OmniTask
        emp = checkin.profile.employee.full_name
        period = f'{checkin.period_year}-{checkin.period_month:02d}'
        # Own savepoint: if this write fails, only IT rolls back. Without the
        # nested atomic a swallowed DB error would poison whatever transaction
        # the caller happens to be in.
        with transaction.atomic():
            OmniTask.objects.create(
                assignee=user,
                assigner=user,
                title=f'{emp} asked you to comment on their {period} feedback'[:200],
                body=(checkin.employee_response or '')[:2000],
                priority=OmniTask.Priority.HIGH,
            )
    except Exception as exc:                                     # noqa: BLE001
        # The email is the primary channel; a task failure must not lose the
        # employee's request, which is already committed.
        log.warning('checkin %s: could not raise manager task: %s', checkin.pk, exc)
