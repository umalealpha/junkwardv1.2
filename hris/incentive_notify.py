"""
hris/incentive_notify.py — emails for the incentive-approval workflow.

Uses core.notifications.send_html_with_cfo_cc (the same Graph-backed helper
the HRIS amendment workflow uses). cc_cfo=False throughout: the CFO is a
direct participant here (approver), so no excoboard auto-cc.

All senders swallow their own failures upstream (callers wrap in try/except)
— an email outage must never block an approval.
"""
from __future__ import annotations

from core.notifications import send_html_with_cfo_cc

from .amendment_service import CFO_EMAIL, UNAMI_EMAIL

FINANCE_EMAILS = ['pkago@alphadirect.co.bw', 'ktshutlhedi@alphadirect.co.bw']
PAGE_URL = 'https://omni.alphadirect.co.bw/hris/incentives'

_NAVY, _ORANGE = '#1D3270', '#F47C20'


def _lines_table(req) -> str:
    from django.utils.html import escape
    rows = ''
    for l in req.lines.all():
        rows += (
            f"<tr><td style='padding:5px 10px;border:1px solid #d8deea'>{l.name}</td>"
            f"<td style='padding:5px 10px;border:1px solid #d8deea'>{l.basis}</td>"
            f"<td style='padding:5px 10px;border:1px solid #d8deea;text-align:right'>"
            f"BWP {l.amount:,.2f}</td></tr>")
        # Show the ≥50-word justification the CFO/HR are actually signing off on
        # (CFO 2026-08-18) — spans the row, under the person it belongs to.
        if (l.justification or '').strip():
            rows += (
                f"<tr><td colspan='3' style='padding:5px 10px;border:1px solid #d8deea;"
                f"background:#f7f9fc;font-size:9.5pt;color:#44506a'>"
                f"<b>Why (justification):</b> {escape(l.justification)}</td></tr>")
    return (
        f"<table style='border-collapse:collapse;font-size:10.5pt'>"
        f"<tr style='background:{_NAVY};color:#fff'>"
        f"<td style='padding:5px 10px'>Name / category</td>"
        f"<td style='padding:5px 10px'>Basis</td>"
        f"<td style='padding:5px 10px;text-align:right'>Amount</td></tr>"
        f"{rows}"
        f"<tr style='background:{_ORANGE};font-weight:bold'>"
        f"<td style='padding:5px 10px' colspan='2'>Total</td>"
        f"<td style='padding:5px 10px;text-align:right'>BWP {req.total:,.2f}</td>"
        f"</tr></table>")


def _wrap(title: str, body_html: str) -> str:
    return (
        f"<div style='font-family:Segoe UI,Arial,sans-serif;color:#1a2233'>"
        f"<div style='background:{_NAVY};color:#fff;padding:12px 16px;"
        f"font-size:13pt;font-weight:bold'>Alpha Direct — omni HRIS</div>"
        f"<div style='padding:16px'>"
        f"<h3 style='margin:0 0 10px;color:{_NAVY}'>{title}</h3>{body_html}"
        f"<p style='margin-top:14px'><a href='{PAGE_URL}'>Open the Incentives "
        f"page in omni</a></p></div></div>")


def _req_head(req) -> str:
    return (f"<p><b>{req.title}</b> — period {req.period}"
            f"{' — ' + req.department if req.department else ''}<br>"
            f"Requested by {req.maker_email or 'unknown'}</p>")


def _action_buttons(req, email: str) -> str:
    """One-click Approve / Decline for `email` — signed, login-free links (CFO
    2026-07-24; two buttons 2026-08-18). Both open the SAME safe confirmation
    page (opening it has no side effect, so a mail scanner can't auto-decide);
    the tap on that page is what commits. Decline lands on the reason box.
    Returns '' if the address can't be mapped to an active omni user (the email
    still carries the omni page link from _wrap)."""
    from django.contrib.auth.models import User
    from .incentive_actions import action_url
    local = (email or '').split('@')[0]
    user = (User.objects.filter(email__iexact=email, is_active=True).first()
            or User.objects.filter(username__iexact=email, is_active=True).first()
            or User.objects.filter(username__iexact=local, is_active=True).first())
    if user is None:
        return ''
    url = action_url(req, user)
    return (
        f"<table role='presentation' width='100%' style='margin:18px 0 6px'><tr>"
        f"<td style='padding-right:6px'>"
        f"<a href='{url}' style='display:block;text-align:center;background:#F4A623;"
        f"color:#0D1B2A;text-decoration:none;padding:14px;border-radius:10px;"
        f"font-weight:bold;font-size:16px'>✓ Approve</a></td>"
        f"<td style='padding-left:6px'>"
        f"<a href='{url}#decline' style='display:block;text-align:center;background:#DC2626;"
        f"color:#fff;text-decoration:none;padding:14px;border-radius:10px;"
        f"font-weight:bold;font-size:16px'>✕ Decline</a></td>"
        f"</tr></table>"
        f"<p style='font-size:12px;color:#6B7280;margin:0'>Opens a quick page on your "
        f"phone — no omni sign-in needed. Approve or decline right there.</p>")


def notify_submitted(req) -> None:
    """Email the CFO and HR — each gets a one-click Approve button bound to
    their own signature slot (so a tap signs the right leg, no omni login)."""
    base_body = (
        _req_head(req) + _lines_table(req) +
        "<p>Both the CFO and HR signatures are required before Finance "
        "processes it in payroll.</p>")
    subject = f"Incentive approval needed — {req.title} ({req.period})"
    for email in (CFO_EMAIL, UNAMI_EMAIL):
        html = _wrap('Incentive request awaiting your approval',
                     base_body + _action_buttons(req, email))
        send_html_with_cfo_cc(
            subject, html, [email],
            # cc the requester once (on the CFO copy) so they know it's in.
            cc=[req.maker_email] if (req.maker_email and email == CFO_EMAIL) else None,
            cc_cfo=False,
            text_fallback=f"Incentive request {req.title} ({req.period}) awaits "
                          f"your approval: {PAGE_URL}",
        )


def notify_approved(req) -> None:
    html = _wrap(
        'Incentive request fully approved — for payroll',
        _req_head(req) + _lines_table(req) +
        "<p>Signed by the CFO and Head of Human Capital. Please consider it "
        "in the payroll run and mark it processed in omni.</p>")
    send_html_with_cfo_cc(
        f"Incentives approved for payroll — {req.title} ({req.period})",
        html,
        [UNAMI_EMAIL] + FINANCE_EMAILS,
        cc=[req.maker_email] if req.maker_email else None,
        cc_cfo=False,
        text_fallback=f"Incentive request {req.title} ({req.period}) is fully "
                      f"approved: {PAGE_URL}",
    )


def notify_rejected(req) -> None:
    if not req.maker_email:
        return
    html = _wrap(
        'Incentive request rejected',
        _req_head(req) +
        (f"<p>Reason: {req.decision_notes}</p>" if req.decision_notes else '') +
        _lines_table(req))
    send_html_with_cfo_cc(
        f"Incentive request rejected — {req.title} ({req.period})",
        html,
        [req.maker_email],
        cc_cfo=False,
        text_fallback=f"Incentive request {req.title} ({req.period}) was "
                      f"rejected: {PAGE_URL}",
    )


def notify_amended(req, *, old_total, new_total, editor_email='',
                   signatures_reset=False) -> None:
    """A still-pending request was corrected (amount / line detail)."""
    reset_note = (
        "<p><b>The amount changed, so the earlier sign-off was cleared — this "
        "request must be approved again.</b></p>"
        if signatures_reset else
        "<p>Line detail was corrected; the amount total is unchanged.</p>")
    html = _wrap(
        'Incentive request amended',
        _req_head(req) +
        f"<p>Amended by {editor_email or 'an authorised user'}. "
        f"Total BWP {old_total:,.2f} → <b>BWP {new_total:,.2f}</b>.</p>" +
        reset_note +
        _lines_table(req))
    # If re-approval is needed, loop HR back in; otherwise just keep the CFO
    # (and the maker) informed of the correction.
    recipients = [CFO_EMAIL, UNAMI_EMAIL] if signatures_reset else [CFO_EMAIL]
    send_html_with_cfo_cc(
        f"Incentive request amended — {req.title} ({req.period})",
        html,
        recipients,
        cc=[req.maker_email] if req.maker_email else None,
        cc_cfo=False,
        text_fallback=f"Incentive request {req.title} ({req.period}) amended "
                      f"(BWP {old_total} → BWP {new_total}): {PAGE_URL}",
    )
