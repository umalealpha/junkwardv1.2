"""
hris/leave_email.py — the rich leave-approval email (CFO 2026-07-14).

Replaces the old four-line plain-text notice. The approver now gets, in the
Alpha Direct house colours:
  * the employee + dates + reason
  * how many annual (normal) days they have, and sick days remaining AFTER
    this leave
  * how much leave the employee already has pending
  * any dashboard tasks that fall in the leave window (so work isn't dropped)
  * Approve / Decline buttons that work from a phone or computer (one-click,
    via a signed token — see hris.leave_actions)

`build_leave_email(lr)` returns {subject, html, to, cc} or None (no manager /
no email). `balance_context_rows` and `tasks_at_risk_rows` are shared with the
one-click confirmation page so both surfaces show identical figures.
"""
from __future__ import annotations

from datetime import date

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape

from core.date_format import format_dt
from hris.models import LeaveRequest

NAVY = '#0D1B2A'
ORANGE = '#F4A623'

# CFO instruction 2026-07-25 — the catch-all reviewer.
#
# The shared `admin` login was blocked that day: it was worked by junior clerks,
# and anyone signed into it could open the leave-payout register, which lists
# every employee's BASIC salary. While tracing it we found a second, quieter
# fault: build_leave_email returned None when a profile had no manager, so the
# request reached NOBODY and failed silently — no error, no queue, nothing.
#
# Scale of that gap, measured on prod 2026-07-25: 66 of 161 HRIS profiles carry
# no manager, but 58 of those are `M365-`-prefixed shells auto-created from
# mailboxes by licensing.sync_m365_active_users — no login, no company, not real
# payroll records. Only THREE real active staff genuinely lack a manager
# (ADIC_320, ADIC_514, ADIC_346). The manager data is in good shape; this guard
# exists for those three, for leavers' rows, and for the next shell the mailbox
# sync invents — not because routing is broadly broken.
#
# So: anything that cannot resolve a LIVE, non-blocklisted reviewer lands on the
# EXCO mailbox instead. Silence and shared clerk mailboxes are both unacceptable
# outcomes; a request landing one level too high is merely untidy.
_DEFAULT_FALLBACK_APPROVER = 'excoboard@alphadirect.co.bw'


def _fmt_days(n) -> str:
    """0.5 / 12 / 12.5 — no trailing .0 for whole days."""
    return f'{float(n or 0):g}'


def _requested_code(lr: LeaveRequest) -> str:
    return ((lr.leave_type.code or lr.leave_type.name or '').lower().strip()
            if lr.leave_type_id else '')


def balance_context_rows(lr: LeaveRequest) -> str:
    """HTML block: annual days now, sick remaining after this leave, pending
    leave. Numbers come from hris.leave_balance (same source as the API)."""
    from hris.leave_balance import balances_for_profile
    profile = lr.profile
    balances = {b['code']: b for b in balances_for_profile(profile)}
    req_code = _requested_code(lr)

    annual = balances.get('annual')
    sick = balances.get('sick')
    annual_now = _fmt_days(annual['available']) if annual else '—'
    # 'available' already nets PENDING requests (this request included), so it
    # IS the figure "after this leave".
    sick_after = _fmt_days(sick['available']) if sick else '—'

    # Pending leave the employee already has in the queue (all types).
    pending = (LeaveRequest.objects
               .filter(profile=profile, status=LeaveRequest.Status.PENDING)
               .select_related('leave_type'))
    pending_days = sum(float(p.days or 0) for p in pending)
    pending_n = pending.count()

    # Requested-type callout (if not annual/sick, show its remaining too).
    extra = ''
    if req_code and req_code not in ('annual', 'sick') and req_code in balances:
        b = balances[req_code]
        extra = (f'<tr><td>{escape(b["name"])} remaining after this</td>'
                 f'<td><b>{_fmt_days(b["available"])}</b> days</td></tr>')

    pending_txt = (f'{_fmt_days(pending_days)} days across {pending_n} request(s)'
                   if pending_n else 'None')

    return f"""
      <div style="margin:16px 0;">
        <div style="font-weight:700;color:{NAVY};border-bottom:2px solid {ORANGE};
                    padding-bottom:5px;margin-bottom:8px;">Leave position</div>
        <table class="kv" style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Annual (normal) days available now</td>
              <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;text-align:right;font-weight:700;">{annual_now} days</td></tr>
          <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Sick days remaining after this leave</td>
              <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;text-align:right;font-weight:700;">{sick_after} days</td></tr>
          {extra}
          <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Leave already pending</td>
              <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;text-align:right;font-weight:700;">{escape(pending_txt)}</td></tr>
        </table>
      </div>"""


def tasks_at_risk_rows(lr: LeaveRequest) -> str:
    """HTML block: the employee's open dashboard tasks that fall inside the
    leave window, so the approver can see if work would be dropped."""
    from core.models import OmniTask
    emp = lr.profile.employee
    user_id = getattr(emp, 'user_id', None)
    if not user_id:
        return ''
    open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS,
                   OmniTask.Status.BLOCKED]
    qs = (OmniTask.objects
          .filter(assignee_id=user_id, status__in=open_states)
          .order_by('due_at'))

    def _in_window(t) -> bool:
        return bool(t.due_at and lr.start_date <= t.due_at <= lr.end_date)

    during = [t for t in qs if _in_window(t)]
    total_open = qs.count()
    if total_open == 0:
        return (f'<div style="margin:16px 0;padding:12px 16px;background:#ECFDF5;'
                f'border-left:4px solid #059669;border-radius:6px;font-size:14px;">'
                f'✓ No open tasks on their dashboard — leave won\'t hold up work.</div>')

    rows = ''
    highlight = during if during else list(qs[:5])
    for t in highlight:
        due = format_dt(t.due_at, '%-d %b') if t.due_at else 'no date'
        flag = ' 🔴' if _in_window(t) else ''
        rows += (f'<tr><td style="padding:6px 0;border-bottom:1px solid #EEF0F3;">'
                 f'{escape(t.title)}{flag}</td>'
                 f'<td style="padding:6px 0;border-bottom:1px solid #EEF0F3;text-align:right;'
                 f'color:#6B7280;white-space:nowrap;">due {escape(due)}</td></tr>')

    banner_colour = '#FEF3C7' if during else '#F9FAFB'
    border = '#F59E0B' if during else '#E5E7EB'
    lead = (f'{len(during)} task(s) fall inside the leave dates'
            if during else f'{total_open} open task(s) on their dashboard')
    return f"""
      <div style="margin:16px 0;padding:12px 16px;background:{banner_colour};
                  border-left:4px solid {border};border-radius:6px;">
        <div style="font-weight:700;color:{NAVY};margin-bottom:6px;font-size:14px;">
          Tasks on their dashboard — {escape(lead)}</div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>
      </div>"""


def _blocked_addresses() -> set[str]:
    """Mailboxes no approval mail may ever reach (shared/junior boxes)."""
    try:
        from core.notifications import _NEVER_CC
    except Exception:            # pragma: no cover - defensive import
        _NEVER_CC = set()
    raw = getattr(settings, 'NEVER_CC_EMAILS', None) or _NEVER_CC
    return {(a or '').strip().lower() for a in raw}


def _is_live_recipient(addr: str, user) -> bool:
    """Can this address actually review a request?

    A missing address, a blocklisted shared mailbox, or a deactivated login all
    mean "no". `user is None` is NOT a failure — a manager with an email but no
    omni account still gets the mail and signs in to act (see the button
    fallback below), which is long-standing behaviour we must not break.
    """
    addr = (addr or '').strip()
    if not addr or addr.lower() in _blocked_addresses():
        return False
    if user is not None and not getattr(user, 'is_active', True):
        return False
    return True


def _fallback_approver() -> tuple[object | None, str]:
    """(user, address) of the EXCO catch-all — ('', None) if it is unusable."""
    from django.contrib.auth import get_user_model

    addr = (getattr(settings, 'LEAVE_FALLBACK_APPROVER_EMAIL', '')
            or _DEFAULT_FALLBACK_APPROVER).strip()
    if not addr or addr.lower() in _blocked_addresses():
        return None, ''
    user = (get_user_model().objects
            .filter(email__iexact=addr, is_active=True)
            .order_by('id').first())
    return user, addr


def is_auto_raised(leave_request) -> bool:
    """Did OMNI raise this row, rather than the employee applying for it?

    Keyed on the cron's own stamp, NOT on the leave type: `td_deduct` is also an
    employee self-service type (CFO 2026-08-25), so a self-applied deduction
    must not be described as auto-raised. enforce_td_deductions writes the
    'Auto-applied:' prefix into `reason`.
    """
    code = ''
    if getattr(leave_request, 'leave_type_id', None):
        code = (getattr(leave_request.leave_type, 'code', '') or '').lower()
    reason = (getattr(leave_request, 'reason', '') or '').lstrip()
    return code == 'td_deduct' and reason.startswith('Auto-applied')


def _lead_sentence(leave_request, lt: str) -> str:
    """The opening line of the approver email.

    Saying "has applied" about a row Omni raised itself made approvers believe
    staff had requested it, and staff then saw an approved row carrying a
    manager's name for leave they never asked for (bug b7e41c7e).
    """
    if is_auto_raised(leave_request):
        return (f'did not apply for this. Omni raised an unpaid <b>{escape(lt)}</b> '
                f'automatically — please confirm or decline.')
    return f'has applied for <b>{escape(lt)}</b> — please approve or decline.'


def build_leave_email(lr: LeaveRequest) -> dict | None:
    """Assemble the full HTML email for a pending leave request.

    Returns {subject, html, to, cc} or None when nobody to send it to.

    CFO directive 2026-07-15: the employee picks who reviews their request
    (LeaveRequest.requested_approver, restricted to genuine people-managers —
    see hris.feature_views.leave_managers). That choice wins when present;
    HRISProfile.manager remains the fallback for legacy requests that predate
    the picker, so nothing already in flight breaks.
    """
    from hris.leave_actions import action_url

    profile = getattr(lr, 'profile', None)
    employee = getattr(profile, 'employee', None)
    if profile is None or employee is None:
        return None

    requested = getattr(lr, 'requested_approver', None)
    manager = getattr(profile, 'manager', None)
    if requested is not None:
        to = (requested.email or '').strip()
        approver_user = requested
    elif manager is not None:
        to = (getattr(manager, 'email', '') or '').strip()
        approver_user = getattr(manager, 'user', None)
    else:
        to, approver_user = '', None

    # No live named reviewer (no manager at all, a blocklisted shared mailbox,
    # or a deactivated login) → the EXCO catch-all rather than silence.
    if not _is_live_recipient(to, approver_user):
        approver_user, to = _fallback_approver()
    if not to:
        return None

    lt = lr.leave_type.name if lr.leave_type_id else 'Leave'
    breakdown = lr.day_breakdown()
    subject = f'Leave approval needed: {employee.full_name} — {lt} ({breakdown})'

    summary = f"""
      <table class="kv" style="width:100%;border-collapse:collapse;font-size:14px;margin:8px 0 4px;">
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;width:42%;">Employee</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(employee.full_name)}</td></tr>
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Leave type</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(lt)}</td></tr>
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">When</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(breakdown)}</td></tr>
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Reason</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(lr.reason or '—')}</td></tr>
      </table>"""

    balances = balance_context_rows(lr)
    tasks = tasks_at_risk_rows(lr)

    # One-click buttons. If the manager has no user account, fall back to the
    # in-app queue link (they'll sign in) rather than a dead token.
    if approver_user is not None:
        page = action_url(lr, approver_user)
        buttons = f"""
          <table role="presentation" width="100%" style="margin:20px 0 6px;"><tr>
            <td style="padding-right:6px;">
              <a href="{escape(page)}" style="display:block;text-align:center;background:#F4A623;
                 color:#0D1B2A;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;font-size:16px;">
                 ✓ Approve</a></td>
            <td style="padding-left:6px;">
              <a href="{escape(page)}#decline" style="display:block;text-align:center;background:#DC2626;
                 color:#fff;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;
                 font-size:16px;">✕ Decline</a></td>
          </tr></table>
          <p style="text-align:center;color:#9CA3AF;font-size:12px;margin:4px 0 0;">
            Opens a secure page — no sign-in needed. Works on your phone or computer.</p>"""
    else:
        from django.conf import settings
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
        buttons = f"""
          <p style="margin:20px 0;"><a href="{base}/hris/leave" style="display:block;text-align:center;
             background:{NAVY};color:#fff;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;">
             Open the leave queue to decide</a></p>"""

    inner = f"""
      <h2 style="margin:0 0 4px;font-size:20px;color:{NAVY};">{escape(employee.full_name)}</h2>
      <p style="color:#6B7280;font-size:14px;margin:0 0 4px;">{_lead_sentence(lr, lt)}</p>
      {summary}{balances}{tasks}{buttons}"""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;">
  <div style="max-width:600px;margin:0 auto;padding:20px 12px;">
    <div style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">
      <div style="background:{NAVY};padding:20px 26px;">
        <div style="color:{ORANGE};font-size:18px;font-weight:700;">Alpha Direct · Leave Approval</div>
      </div>
      <div style="padding:22px 26px;">{inner}</div>
    </div>
    <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">
      Omni ERP — omni.alphadirect.co.bw</p>
  </div>
</body></html>"""

    return {'subject': subject, 'html': html, 'to': [to], 'cc': None}


# ─────────────────────────────────────────────────────────────────────────────
# Telling the EMPLOYEE what happened (CFO 2026-08-07)
#
# Until now the only leave email was the one asking the manager to decide. The
# employee was never told the answer — approved or declined, they had to go
# looking for it, and most never did. This closes that loop: the decision goes
# straight back to the person who applied.
# ─────────────────────────────────────────────────────────────────────────────

def _decision_shell(header: str, inner: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:#F3F4F6;font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;">
  <div style="max-width:600px;margin:0 auto;padding:20px 12px;">
    <div style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">
      <div style="background:{NAVY};padding:20px 26px;">
        <div style="color:{ORANGE};font-size:18px;font-weight:700;">Alpha Direct · {escape(header)}</div>
      </div>
      <div style="padding:22px 26px;">{inner}</div>
    </div>
    <p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">
      Omni ERP — omni.alphadirect.co.bw</p>
  </div>
</body></html>"""


def build_leave_decision_email(lr: LeaveRequest) -> dict | None:
    """The answer, sent to the person who applied.

    Returns {subject, html, to, cc} or None when there is nobody to tell (no
    employee email on file). Covers approved, declined and cancelled.
    """
    profile = getattr(lr, 'profile', None)
    employee = getattr(profile, 'employee', None)
    if profile is None or employee is None:
        return None
    to = (getattr(employee, 'email', '') or '').strip()
    if not to:
        return None

    approved = lr.status == LeaveRequest.Status.APPROVED
    cancelled = lr.status == LeaveRequest.Status.CANCELLED
    lt = lr.leave_type.name if lr.leave_type_id else 'Leave'
    breakdown = lr.day_breakdown()

    if approved:
        word, colour, tone = 'approved', '#059669', 'Enjoy the time off.'
    elif cancelled:
        word, colour, tone = 'cancelled', '#6B7280', 'Nothing further is needed.'
    else:
        word, colour, tone = 'declined', '#DC2626', 'Speak to your manager if you need to discuss it.'

    subject = f'Your leave was {word}: {lt} ({breakdown})'
    decided_by = ''
    if lr.approver_id:
        decided_by = (lr.approver.get_full_name() or lr.approver.username)

    notes_row = ''
    if (lr.decision_notes or '').strip():
        notes_row = (f'<tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Note</td>'
                     f'<td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">'
                     f'{escape(lr.decision_notes)}</td></tr>')
    by_row = ''
    if decided_by:
        by_row = (f'<tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Decided by</td>'
                  f'<td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">'
                  f'{escape(decided_by)}</td></tr>')

    from django.conf import settings
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')

    inner = f"""
      <h2 style="margin:0 0 6px;font-size:22px;color:{colour};">Your leave was {escape(word)}</h2>
      <p style="color:#6B7280;font-size:14px;margin:0 0 4px;">{escape(tone)}</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;margin:14px 0 4px;">
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;width:42%;">Leave type</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(lt)}</td></tr>
        <tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">When</td>
            <td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(breakdown)}</td></tr>
        {by_row}{notes_row}
      </table>
      <p style="margin:20px 0 0;"><a href="{base}/hris/leave" style="display:block;text-align:center;
         background:{NAVY};color:#fff;text-decoration:none;padding:14px;border-radius:10px;font-weight:700;">
         See all my leave</a></p>"""
    return {'subject': subject, 'html': _decision_shell('Leave Decision', inner),
            'to': [to], 'cc': None}
