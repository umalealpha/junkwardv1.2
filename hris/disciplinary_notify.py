"""
hris/disciplinary_notify.py

House-style HTML emails for the disciplinary chain. Every case CCs EXCO
(cc_cfo=True) for the HR audit trail; HR (Unami) is the direct recipient at the
review stage. Senders are best-effort — a mail failure never blocks a state
change (callers wrap in try/except).
"""
from __future__ import annotations

from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc
from hris.amendment_service import CFO_EMAIL, UNAMI_EMAIL

PAGE_URL = 'https://omni.alphadirect.co.bw/hris/disciplinary'
_NAVY, _ORANGE, _MUT = '#1D3270', '#F47C20', '#6B7280'


def _wrap(title: str, intro: str, rows: str, cta_label: str = 'Open Disciplinary') -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Book Antiqua',Georgia,serif">
<div style="max-width:640px;margin:0 auto;background:#fff">
  <div style="background:{_NAVY};padding:16px 22px">
    <div style="color:{_ORANGE};font-size:18px;font-weight:700">Alpha Direct — omni HRIS</div>
    <div style="color:#fff;font-size:15px;margin-top:2px">{title}</div>
  </div>
  <div style="padding:20px 22px;color:#1F2937;font-size:14px;line-height:1.5">
    <p style="margin:0 0 12px">{intro}</p>
    <table style="border-collapse:collapse;width:100%;font-size:13px">{rows}</table>
    <p style="margin:18px 0 0">
      <a href="{PAGE_URL}" style="background:{_ORANGE};color:#fff;text-decoration:none;
         padding:9px 16px;border-radius:6px;font-weight:700">{cta_label}</a>
    </p>
    <p style="color:{_MUT};font-size:11px;margin-top:16px">Confidential HR record. Do not forward.</p>
  </div>
</div></body></html>"""


def _row(label: str, value: str) -> str:
    return (f'<tr><td style="padding:5px 8px;border-bottom:1px solid #EEF0F3;color:{_MUT}">{label}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #EEF0F3;font-weight:600">{value}</td></tr>')


def _salutation(full_name: str) -> str:
    """How to open a letter to a member of staff.

    Standing rule (CFO, 2026-07-27, given in capitals): NEVER the bare first
    name. Title + first + surname where the honorific is known — but it is NOT
    known here, and guessing one misgenders people, so this uses the FULL NAME
    with no title, which is the documented fallback and is never wrong.

    Middle names are dropped: "Pako Lisley Kago" -> "Pako Kago", because that is
    how the person is actually addressed. A single-word name is used as-is.
    """
    parts = [p for p in (full_name or '').split() if p]
    if not parts:
        return 'Colleague'
    if len(parts) == 1:
        return parts[0]
    return f'{parts[0]} {parts[-1]}'


def _rows(case) -> str:
    return (
        _row('Employee', case.subject_name or str(case.subject_employee_id))
        + _row('Category', case.get_category_display())
        + _row('Incident date', case.incident_date.isoformat() if case.incident_date else '—')
        + _row('Raised by', case.raised_by_email or '—')
        + _row('Status', case.get_status_display())
        + (_row('Employee response', 'Recorded on the case')
           if case.employee_responded_at else
           _row('Employee response', f'None — deadline was {case.response_deadline:%d %b %Y}')
           if case.response_deadline else '')
    )


def notify_raised(case) -> None:
    """New case → HR (Unami) to review; EXCO CC'd for the audit trail."""
    intro = ('A disciplinary case has been raised and needs HR review. '
             'The full allegation is on the case in omni (not repeated in this email).')
    html = _wrap('Disciplinary — HR review needed', intro, _rows(case), 'Review in omni')
    send_html_with_cfo_cc(
        f'Disciplinary — HR review needed: {case.subject_name} ({case.get_category_display()})',
        html, [UNAMI_EMAIL], cc_cfo=True)


def notify_advanced(case) -> None:
    """After HR review: either CFO sign-off is needed, or the case is issued."""
    from .disciplinary_models import DisciplinaryCase
    if case.status == DisciplinaryCase.Status.PENDING_CFO:
        intro = ('HR has reviewed this case. It is a suspension / dismissal, so it '
                 'goes through a final internal review before it can be issued.')
        html = _wrap('Disciplinary — final review needed', intro, _rows(case), 'Sign off in omni')
        send_html_with_cfo_cc(
            f'Disciplinary — final review needed: {case.subject_name} ({case.get_category_display()})',
            html, [CFO_EMAIL], cc_cfo=False)   # the final reviewer is the direct recipient
    elif case.status == DisciplinaryCase.Status.ISSUED:
        intro = 'This disciplinary case has been reviewed and issued.'
        html = _wrap('Disciplinary — issued', intro, _rows(case), 'View in omni')
        to = [e for e in [case.raised_by_email, UNAMI_EMAIL] if e]
        send_html_with_cfo_cc(
            f'Disciplinary — issued: {case.subject_name} ({case.get_category_display()})',
            html, to, cc_cfo=True)


def notify_inquiry(case) -> int:
    """The invitation to respond — the ONE letter in this chain that goes to the
    employee the case is about (CFO directive 2026-08-11).

    Two deliberate differences from every other mail here:
      • no_reply=False — the letter ASKS the employee a question, so the red
        "do not reply, log it in omni" banner must be OFF (standing rule).
      • reply_to HR, so an employee who answers by email still reaches Unami
        rather than the omni sender. The omni response box remains the record.

    Returns the number of messages sent so the caller can roll the case back if
    the letter did not go out — a case must never be marked "served" unserved.
    """
    to_email = (case.inquiry_sent_to
                or (getattr(case.subject_employee, 'email', '') or '')).strip()
    if not to_email:
        return 0
    from .disciplinary_models import DisciplinaryCase
    salutation = _salutation(case.subject_name)
    deadline = case.response_deadline.strftime('%d %B %Y') if case.response_deadline else '—'
    # A case that is ALREADY DECIDED gets different wording. Telling someone "no
    # decision will be recorded until you answer" when the warning is already on
    # their file would be untrue, and an untrue letter is worse than no letter.
    already_issued = case.status == DisciplinaryCase.Status.ISSUED
    rows = (
        _row('Outcome already recorded' if already_issued else 'Category under consideration',
             case.get_category_display())
        + _row('Incident date', case.incident_date.isoformat() if case.incident_date else '—')
        + _row('Respond by', deadline)
        + _row('Where to respond', 'omni → HRIS → My Profile → Disciplinary')
    )
    if already_issued:
        opening = (
            'A disciplinary outcome has been recorded on your file. It was decided before you '
            'were asked for your account of it, and that should not have happened. You are '
            'now invited to give your side, and what you write will be placed on the same '
            'record. The allegation is set out below.')
        bullets = (
            f'<li>Write your account in omni by <strong>{deadline}</strong>.</li>'
            '<li>Open omni, go to HRIS, then My Profile. Your case is under '
            '"Disciplinary record" with a box to type your answer.</li>'
            '<li>To be clear: your response is added to the record. It does not by itself '
            'change the outcome already recorded — if you want the outcome itself '
            'reconsidered, say so and HR will take it up.</li>'
            f'<li>If you would rather answer by email, reply to this message — it reaches HR '
            f'({UNAMI_EMAIL}).</li>')
    else:
        opening = (
            'A disciplinary matter has been raised concerning you. Before any decision is '
            'taken, you are invited to give your side of it. The allegation is set out below.')
        bullets = (
            f'<li>Write your explanation in omni by <strong>{deadline}</strong>.</li>'
            '<li>Open omni, go to HRIS, then My Profile. Your case is under '
            '"Disciplinary record" with a box to type your answer.</li>'
            '<li>No decision will be recorded until you have answered or that date has passed.</li>'
            f'<li>If you would rather answer by email, reply to this message — it reaches HR '
            f'({UNAMI_EMAIL}).</li>')
    intro = (
        f'{salutation},<br><br>{opening}'
        f'<br><br><strong>Allegation</strong><br>{escape(case.allegation)}'
        + (f'<br><br><strong>Action recorded</strong><br>{escape(case.proposed_action)}'
           if case.proposed_action and already_issued else
           f'<br><br><strong>Action being considered</strong><br>{escape(case.proposed_action)}'
           if case.proposed_action else '')
        + '<br><br>Points:'
        f'<ul style="margin:8px 0 0;padding-left:18px">{bullets}</ul>'
    )
    html = _wrap('Disciplinary — your response is invited', intro, rows,
                 'Respond in omni')
    subject = (f'Disciplinary matter on your file — your response is invited by {deadline}'
               if already_issued else
               f'Disciplinary matter — your response is invited by {deadline}')
    return send_html_with_cfo_cc(
        subject,
        html, [to_email],
        cc=[UNAMI_EMAIL],
        reply_to=[UNAMI_EMAIL],
        cc_cfo=True,          # EXCO keeps the HR audit trail for the whole chain
        no_reply=False)       # the letter asks a question — banner OFF


def notify_response_recorded(case) -> None:
    """The employee has answered → back to HR (and the raiser) to review."""
    intro = ('The employee has given their explanation. It is on the case record in omni '
             '(not repeated in this email). The case is back with HR to review.')
    rows = _rows(case) + _row('Responded', case.employee_responded_at.strftime('%d %b %Y %H:%M')
                              if case.employee_responded_at else '—')
    html = _wrap('Disciplinary — employee has responded', intro, rows, 'Review in omni')
    to = [e for e in [UNAMI_EMAIL, case.raised_by_email] if e]
    send_html_with_cfo_cc(
        f'Disciplinary — employee has responded: {case.subject_name} ({case.get_category_display()})',
        html, to, cc_cfo=True)


def notify_rejected(case) -> None:
    intro = f'This disciplinary case was rejected at the {case.rejected_stage.upper()} stage.'
    html = _wrap('Disciplinary — rejected', intro, _rows(case), 'View in omni')
    to = [e for e in [case.raised_by_email, UNAMI_EMAIL] if e]
    send_html_with_cfo_cc(
        f'Disciplinary — rejected: {case.subject_name} ({case.get_category_display()})',
        html, to, cc_cfo=True)


def notify_overridden(case) -> None:
    """The CFO overturned a rejection. Goes to the raiser, HR and EXCO — the
    people who saw the rejection email and would otherwise be left believing the
    case was closed. The EMPLOYEE is not told from here (CFO 2026-08-12)."""
    intro = ('This case was rejected at the HR stage and has been overturned by the '
             'CFO, who has issued it. The rejection and the reason for overturning '
             'it are both on the case in omni.')
    rows = _rows(case) + _row('Overturned by', getattr(case.cfo_approver, 'email', '') or '—')
    html = _wrap('Disciplinary — rejection overturned by the CFO', intro, rows, 'View in omni')
    to = [e for e in [case.raised_by_email, UNAMI_EMAIL] if e]
    send_html_with_cfo_cc(
        f'Disciplinary — overturned and issued: {case.subject_name} ({case.get_category_display()})',
        html, to, cc_cfo=True)


def notify_tool_ready(to_email: str, to_name: str = '') -> None:
    """One-off enablement email to a manager (CFO directive 2026-07-22): the
    disciplinary tool is live; here is how to use it."""
    name = _salutation(to_name) if to_name else (to_email or 'Colleague')
    rows = (
        _row('Where', 'omni → HRIS → Disciplinary')
        + _row('You can', 'Raise a case, describe the incident (≥50 words), propose an action')
        + _row('Routing', 'Every case goes to HR (Unami) to review')
        + _row('Sign-off', 'Suspension / dismissal goes through a final internal review'))
    intro = (f'{name},<br><br>The disciplinary tool is now live in omni so you can raise and '
             'track disciplinary cases yourself. Points:')
    html = _wrap('Disciplinary tool — now live', intro, rows, 'Open Disciplinary')
    send_html_with_cfo_cc('omni — Disciplinary tool is live (raise your cases here)',
                          html, [to_email], cc=[UNAMI_EMAIL], cc_cfo=True)
