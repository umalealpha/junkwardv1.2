"""
hris/leave_encash_notify.py — emails for the leave-encashment approval chain.

Graph-backed helper (same as incentives). Each stage emails the NEXT approver
so the item lands on the right dashboard; the applicant is cc'd on progress and
told directly on approval/rejection. All senders are wrapped in try/except
upstream — an email outage must never block an approval.
"""
from __future__ import annotations

from core.notifications import send_html_with_cfo_cc

from .amendment_service import CFO_EMAIL, UNAMI_EMAIL
from .incentive_notify import FINANCE_EMAILS

PAGE_URL = 'https://omni.alphadirect.co.bw/hris/leave-encashment'
APPROVALS_URL = 'https://omni.alphadirect.co.bw/my-approvals'

_NAVY, _ORANGE = '#1D3270', '#F47C20'


def _wrap(title: str, body_html: str, cta_url: str = PAGE_URL,
          cta: str = 'Open Leave Encashment in omni') -> str:
    return (
        f"<div style='font-family:Segoe UI,Arial,sans-serif;color:#1a2233'>"
        f"<div style='background:{_NAVY};color:#fff;padding:12px 16px;"
        f"font-size:13pt;font-weight:bold'>Alpha Direct — omni HRIS</div>"
        f"<div style='padding:16px'>"
        f"<h3 style='margin:0 0 10px;color:{_NAVY}'>{title}</h3>{body_html}"
        f"<p style='margin-top:14px'><a href='{cta_url}'>{cta}</a></p></div></div>")


def _detail_table(enc) -> str:
    rows = [
        ('Employee',            enc.employee.full_name),
        ('Days',                f"{enc.days}"),
        ('Basic salary (BWP)',  f"{enc.basic_salary:,.2f}"
                                + (f" (period {enc.basic_source})" if enc.basic_source else '')),
        ('Daily rate (÷24)',    f"{enc.daily_rate:,.2f}"),
        ('Gross payout (BWP)',  f"{enc.amount:,.2f}"),
        # Approvers sign off on what actually leaves the bank — show the PAYE
        # split, not just the gross (CFO directive 2026-08-17).
        ('Less PAYE (BWP)',     f"{enc.tax_amount:,.2f}"),
        ('Net payable (BWP)',   f"<b>{enc.net_amount:,.2f}</b>"),
        ('Balance when applied', f"{enc.balance_at_request}"),
    ]
    if enc.reason:
        rows.append(('Reason', enc.reason))
    trs = ''.join(
        f"<tr><td style='padding:5px 10px;border:1px solid #d8deea'>{k}</td>"
        f"<td style='padding:5px 10px;border:1px solid #d8deea'>{v}</td></tr>"
        for k, v in rows)
    return f"<table style='border-collapse:collapse;font-size:10.5pt'>{trs}</table>"


# Who to email for the stage the request is NOW waiting on.
def _next_recipients(enc) -> list[str]:
    from .leave_encash_models import LeaveEncashment
    if enc.status == LeaveEncashment.Status.PENDING_CFO:
        return [CFO_EMAIL]
    if enc.status == LeaveEncashment.Status.PENDING_HR:
        return [UNAMI_EMAIL]
    if enc.status == LeaveEncashment.Status.PENDING_FINANCE:
        return list(FINANCE_EMAILS)
    return []


_STAGE_LABEL = {
    'pending_cfo': 'the CFO', 'pending_hr': 'HR',
    'pending_finance': 'Finance (FC/FM)',
}


def notify_applied(enc) -> None:
    """Employee applied → CFO (first approver). A LEAVER settlement (HR-raised,
    CFO 2026-09-05) says so plainly — it is the person's full balance, not a
    serving employee cashing out some days."""
    if getattr(enc, 'kind', '') == 'settlement':
        intro = (f"<p><b>{enc.applicant_email or 'HR'}</b> raised the <b>final leave pay</b> for "
                 f"<b>{enc.employee.full_name}</b>, who leaves on {enc.last_day}. It is their FULL "
                 f"annual-leave balance to that day, valued at the latest payslip BASIC ÷ 24 × days — "
                 f"nothing typed in. Once approved, Finance puts it on the final payslip as Leave Pay.</p>")
        title = 'Final leave pay for a leaver — awaiting your approval'
    else:
        intro = (f"<p><b>{enc.applicant_email or enc.employee.full_name}</b> applied to "
                 f"encash leave. The payout is computed from the latest payslip BASIC ÷ 24 "
                 f"× days — it cannot be typed in.</p>")
        title = 'Leave encashment awaiting your approval'
    html = _wrap(
        title,
        intro + _detail_table(enc) +
        "<p>Approval chain: CFO → HR → Finance (FC/FM) → payment. Approving "
        "passes it to the next approver.</p>",
        APPROVALS_URL, 'Approve in My Approvals')
    send_html_with_cfo_cc(
        f"Leave encashment — approval needed: {enc.employee.full_name} "
        f"({enc.days}d, BWP {enc.amount:,.2f} gross / {enc.net_amount:,.2f} net)",
        html, [CFO_EMAIL],
        cc=[enc.applicant_email] if enc.applicant_email else None,
        cc_cfo=False,
        text_fallback=f"Leave encashment for {enc.employee.full_name} awaits "
                      f"CFO approval: {APPROVALS_URL}")


def notify_stage_advanced(enc) -> None:
    """One leg signed; email the NEXT approver."""
    to = _next_recipients(enc)
    if not to:
        return
    who = _STAGE_LABEL.get(enc.status, 'the next approver')
    html = _wrap(
        f"Leave encashment now awaiting {who}",
        _detail_table(enc) +
        f"<p>The previous approval is done — this now needs <b>{who}</b>.</p>",
        APPROVALS_URL, 'Approve in My Approvals')
    send_html_with_cfo_cc(
        f"Leave encashment — {who} approval needed: {enc.employee.full_name} "
        f"(BWP {enc.amount:,.2f} gross / {enc.net_amount:,.2f} net)",
        html, to,
        cc=[enc.applicant_email] if enc.applicant_email else None,
        cc_cfo=False,
        text_fallback=f"Leave encashment for {enc.employee.full_name} now needs "
                      f"{who}: {APPROVALS_URL}")


def notify_approved(enc) -> None:
    """Fully approved (FC/FM signed) → Finance loads it for payment."""
    html = _wrap(
        'Leave encashment fully approved — for payment',
        _detail_table(enc) +
        f"<p>Signed by the CFO, HR and Finance (FC/FM). Please load "
        f"<b>BWP {enc.net_amount:,.2f}</b> — the <b>NET</b> figure, after PAYE of "
        f"BWP {enc.tax_amount:,.2f} — for payment to the employee, and mark it "
        f"paid in omni. The PAYE is remitted to BURS with the month's payroll, "
        f"not paid to the employee.</p>")
    send_html_with_cfo_cc(
        f"Leave encashment approved for payment — {enc.employee.full_name} "
        f"(net BWP {enc.net_amount:,.2f})",
        html, list(FINANCE_EMAILS),
        cc=[enc.applicant_email] if enc.applicant_email else None,
        cc_cfo=False,
        text_fallback=f"Leave encashment for {enc.employee.full_name} is fully "
                      f"approved: {PAGE_URL}")


def notify_rejected(enc) -> None:
    if not enc.applicant_email:
        return
    stage = _STAGE_LABEL.get(f'pending_{enc.rejected_stage}', enc.rejected_stage or 'an approver')
    html = _wrap(
        'Leave encashment rejected',
        _detail_table(enc) +
        f"<p>Rejected at the {stage} stage."
        + (f" Reason: {enc.decision_notes}" if enc.decision_notes else "") +
        "</p>")
    send_html_with_cfo_cc(
        f"Leave encashment rejected — {enc.employee.full_name}",
        html, [enc.applicant_email],
        cc_cfo=False,
        text_fallback=f"Leave encashment for {enc.employee.full_name} was "
                      f"rejected: {PAGE_URL}")
