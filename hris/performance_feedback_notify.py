"""hris/performance_feedback_notify.py — tell the manager when the employee has
responded to a monthly performance feedback (CFO 2026-08-18).

The employee can Accept / Partially accept / Decline the feedback and, on a
partial/decline, must reply in >= 50 words. When they respond, the manager who
gave the feedback (the reviewer) is emailed so it doesn't sit unseen. Best-effort
— every caller wraps this in try/except; a mail failure never blocks the employee.
"""
from __future__ import annotations

from django.utils.html import escape

from core.notifications import send_html_with_cfo_cc

_NAVY, _ORANGE = '#1D3270', '#F47C20'

_LABEL = {
    'accept':  ('Accepted', '#059669'),
    'partial': ('Partially accepted', '#F4A623'),
    'decline': ('Not accepted', '#DC2626'),
}


def notify_manager_of_response(checkin) -> int:
    """Email the reviewer (manager) that the employee responded. Returns messages
    sent (0 if there is no manager email to send to)."""
    # reviewer is None on EVERY auto-posted month, so keying only on it returned
    # 0 silently and the employee's decline reached nobody — the exact case where
    # a human most needs to look. Fall back through the same resolution the
    # push-back module uses (line manager, then co-manager).
    reviewer = getattr(checkin, 'reviewer', None)
    to = (getattr(reviewer, 'email', '') or '').strip()
    if not to:
        from hris.feedback_pushback import _manager_email
        to = _manager_email(checkin)
    if not to:
        return 0
    emp = checkin.profile.employee
    label, colour = _LABEL.get(checkin.employee_decision, (checkin.employee_decision or '—', _NAVY))
    period = f'{checkin.period_year}-{checkin.period_month:02d}'
    resp = (checkin.employee_response or '').strip() or '(no comment)'
    html = (
        f"<div style='font-family:Segoe UI,Arial,sans-serif;color:#1a2233;max-width:640px'>"
        f"<div style='background:{_NAVY};color:#fff;padding:12px 16px;font-size:13pt;font-weight:bold'>"
        f"Alpha Direct — Performance feedback response</div>"
        f"<div style='padding:16px'>"
        f"<p><b>{escape(emp.full_name)}</b> has responded to their {escape(period)} "
        f"performance feedback.</p>"
        f"<p style='margin:12px 0'>Response: "
        f"<span style='display:inline-block;background:{colour};color:#fff;font-weight:bold;"
        f"padding:3px 12px;border-radius:999px;font-size:12px'>{escape(label)}</span></p>"
        f"<p style='margin:6px 0 4px;color:#6B7280;font-size:12px'>Their comment</p>"
        f"<div style='background:#F7F9FC;border:1px solid #EEF0F3;border-radius:8px;"
        f"padding:12px 14px;font-size:13px;white-space:pre-wrap'>{escape(resp)}</div>"
        f"<p style='margin-top:14px'><a href='https://omni.alphadirect.co.bw/hris/monthly-feedback'>"
        f"Open it in omni</a></p></div></div>")
    return send_html_with_cfo_cc(
        subject=f'Feedback {label.lower()} — {emp.full_name} ({period})',
        html=html, to=[to], cc_cfo=False,
        text_fallback=f'{emp.full_name} {label.lower()} their {period} performance feedback.')


def notify_employee_of_autopost(checkin) -> int:
    """Tell the employee their feedback was posted by Omni, and that they can
    push back (CFO 2026-08-26).

    This function's ABSENCE was the worst defect in the first cut of the
    auto-feedback build: monthly_feedback_autopost called it behind an
    `except ImportError: pass`, so every employee would have received a permanent
    feedback record and never been told — which means the fairness valve the CFO
    asked for could never fire. Caught by /fabe 2026-08-26.
    """
    emp = checkin.profile.employee
    to = ((getattr(getattr(emp, 'user', None), 'email', '') or '')
          or (getattr(emp, 'email', '') or '')).strip()
    if not to:
        return 0
    period = f'{checkin.period_year}-{checkin.period_month:02d}'
    # escape BEFORE turning newlines into <br> — never the other way round
    facts = escape((checkin.evidence or '').strip()).replace('\n', '<br>')
    html = (
        f"<div style='font-family:Book Antiqua,Georgia,serif;font-size:14px;"
        f"line-height:1.6;color:#222;max-width:640px'>"
        f"<div style='background:{_NAVY};color:#fff;padding:12px 16px;font-size:15px;"
        f"font-weight:bold'>Your {escape(period)} feedback</div>"
        f"<div style='padding:16px'>"
        f"<p>{escape(emp.full_name.split()[0])}</p>"
        f"<p>Your feedback for {escape(period)} has been recorded by Omni. Your "
        f"manager was sent it to confirm or change and did not reply, so what is "
        f"on record is the facts for the month and <b>no rating has been given</b>.</p>"
        f"<div style='background:#f7f9fb;border-radius:8px;padding:11px;margin:12px 0;"
        f"font-size:13px'>{facts}</div>"
        f"<p><b>If you do not agree, say so.</b> Open the feedback in Omni and ask "
        f"your manager to add their comments. They have to answer you — it stays on "
        f"their list until they do. You do not have to accept it.</p>"
        f"<p>Regards,<br><b>Omni</b><br>"
        f"<span style='color:#6B7280'>Alpha Direct ERP</span></p>"
        f"</div></div>")
    text = (f"Your {period} feedback has been recorded by Omni because your manager "
            f"did not reply. No rating was given. If you do not agree, open it in "
            f"Omni and ask your manager to comment - they must answer you.")
    from core.notifications import send_html_with_cfo_cc
    return send_html_with_cfo_cc(
        subject=f'Your {period} feedback is on record',
        html=html, to=[to], text_fallback=text,
        no_reply=False,            # it invites a reply/push-back
        cc_cfo=False,              # an individual's own feedback — not an EXCO copy
        allow_named_exec=True)
