"""hris/leave_reversal_email.py — telling the approver, and telling the employee.

Two messages, both required by the report:
  * the approver gets the claim WITH the original leave record beside it;
  * the employee is told the outcome "immediately with the approver's stated reason".

House template, plain English, no red do-not-reply banner: both of these invite a
reply (the approver acts, the employee may query a decline).
"""
from __future__ import annotations

import logging
from html import escape

from hris.leave_reversal_models import LeaveReversal
from hris.leave_reversal_service import approver_for

log = logging.getLogger(__name__)

_CSS = """
body{font-family:'Book Antiqua',Georgia,serif;color:#111827;font-size:14px;line-height:1.55;margin:0;padding:0;background:#F9FAFB}
.wrap{max-width:640px;margin:0 auto;background:#fff}
.hdr{background:#0D1B2A;padding:18px 24px}
.hdr h1{color:#F4A623;font-size:18px;margin:0}
.body{padding:22px 24px}
h2{color:#0D1B2A;font-size:15px;border-bottom:2px solid #F4A623;padding-bottom:5px;margin:22px 0 10px}
table{border-collapse:collapse;width:100%;margin:10px 0}
th{background:#0D1B2A;color:#fff;padding:7px 10px;text-align:left;font-size:13px}
td{padding:7px 10px;border-bottom:1px solid #EEF0F3;font-size:13px;vertical-align:top}
.q{color:#6B7280}
.ftr{padding:16px 24px;color:#6B7280;font-size:12px;border-top:1px solid #E5E7EB}
"""


def _shell(title: str, inner: str) -> str:
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_CSS}</style>'
            f'</head><body><div class="wrap"><div class="hdr"><h1>{escape(title)}</h1>'
            f'</div><div class="body">{inner}</div>'
            f'<div class="ftr">Alpha Direct Insurance Company (Pty) Ltd</div>'
            f'</div></body></html>')


def _employee_name(rev: LeaveReversal) -> str:
    try:
        return rev.leave_request.profile.employee.full_name or 'An employee'
    except Exception:      # noqa: BLE001
        return 'An employee'


def _original_table(rev: LeaveReversal) -> str:
    lr = rev.leave_request
    lt = lr.leave_type.name if lr.leave_type_id else 'Leave'
    return (
        '<table>'
        '<tr><th colspan="2">The original leave, as approved</th></tr>'
        f'<tr><td class="q">Leave type</td><td>{escape(lt)}</td></tr>'
        f'<tr><td class="q">Dates</td><td>{rev.original_start_date} to '
        f'{rev.original_end_date}</td></tr>'
        f'<tr><td class="q">Days approved</td><td>{float(rev.original_days or 0):.1f}</td></tr>'
        '</table>')


def _claim_table(rev: LeaveReversal) -> str:
    return (
        '<table>'
        '<tr><th colspan="2">What is being claimed</th></tr>'
        f'<tr><td class="q">Days worked, to credit back</td><td><strong>'
        f'{float(rev.days or 0):.1f}</strong></td></tr>'
        f'<tr><td class="q">Reason given</td><td>{escape(rev.reason)}</td></tr>'
        f'<tr><td class="q">Supporting document attached</td>'
        f'<td>{"Yes" if rev.attachment else "No"}</td></tr>'
        '</table>')


def _evidence_block(rev: LeaveReversal) -> str:
    """Time Doctor hours for the leave period, plus one advisory line.

    The manager decides; this is only the evidence beside the claim.
    """
    from hris.leave_reversal_service import evidence_vs_claim, timedoctor_evidence
    ev = timedoctor_evidence(rev.leave_request)
    note = escape(evidence_vs_claim(rev.leave_request, rev.days))
    if not ev['available']:
        return f'<h2>Time Doctor</h2><p>{note}</p>'
    rows = ''.join(
        '<tr><td>{d}</td><td>{h:.2f}</td><td>{m}</td></tr>'.format(
            d=escape(x['date']), h=x['tracked_hours'],
            m='looks worked' if x['looks_worked'] else '—')
        for x in ev['days'])
    return (
        '<h2>Time Doctor for these dates</h2>'
        f'<p>{note}</p>'
        '<table><tr><th>Date</th><th>Hours tracked</th><th></th></tr>'
        f'{rows}</table>')


def notify_reversal_raised(rev: LeaveReversal) -> int:
    """Tell the approver a claim is waiting. Returns the number of mails sent."""
    from core.notifications import send_html_with_cfo_cc

    approver = approver_for(rev.leave_request)
    addr = (getattr(approver, 'email', '') or '').strip()
    if not addr:
        log.warning('leave reversal %s has no approver address — nobody was told', rev.pk)
        return 0

    name = _employee_name(rev)
    inner = (
        f'<p>{escape((approver.get_full_name() or approver.username))},</p>'
        f'<p><strong>{escape(name)}</strong> says they worked '
        f'{float(rev.days or 0):.1f} day(s) of leave you approved, and is asking for '
        f'those days back.</p>'
        + _claim_table(rev) + _original_table(rev) + _evidence_block(rev) +
        '<h2>What to do</h2>'
        '<p>Open Omni and go to My Approvals. Approving credits the days back to their '
        'balance and shortens the leave record. Declining needs a reason — the employee '
        'is shown exactly what you write.</p>'
        '<p>This is your call. Omni does not judge whether the days were worked.</p>'
        '<p>Regards,<br>Omni</p>')
    return send_html_with_cfo_cc(
        subject=f'Leave reversal to approve — {name}, {float(rev.days or 0):.1f} day(s)',
        html=_shell('Leave reversal waiting for you', inner), to=[addr])


def notify_reversal_decided(rev: LeaveReversal) -> int:
    """Tell the employee the outcome, with the approver's own words on a decline."""
    from core.notifications import send_html_with_cfo_cc

    addr = ''
    try:
        addr = (rev.leave_request.profile.employee.email or '').strip()
    except Exception:      # noqa: BLE001
        pass
    if not addr:
        log.warning('leave reversal %s: employee has no email on file', rev.pk)
        return 0

    approved = rev.status == LeaveReversal.Status.APPROVED
    who = ((rev.approver.get_full_name() or rev.approver.username)
           if rev.approver_id else 'Your approver')
    if approved:
        title = 'Your leave reversal was approved'
        lead = (f'<p>{escape(who)} approved your claim. '
                f'<strong>{float(rev.days or 0):.1f} day(s)</strong> have been credited '
                f'back to your leave balance, and the original leave record has been '
                f'shortened to match.</p>')
    else:
        title = 'Your leave reversal was declined'
        lead = (f'<p>{escape(who)} declined your claim, so those days stay as leave '
                f'taken. The reason given was:</p>'
                f'<p style="border-left:3px solid #F4A623;padding-left:12px;'
                f'color:#374151">{escape(rev.decision_notes or "")}</p>'
                f'<p>If you think that is wrong, reply to '
                f'{escape(who)} directly or speak to Human Capital.</p>')

    inner = (f'<p>Hello,</p>{lead}' + _claim_table(rev) + _original_table(rev) +
             '<p>Regards,<br>Omni</p>')
    return send_html_with_cfo_cc(
        subject=('Leave reversal approved' if approved else 'Leave reversal declined'),
        html=_shell(title, inner), to=[addr])
