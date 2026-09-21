"""payroll/payslip_email.py — email one employee their payslip PDF.

Shared by the on-demand send actions on ``PayslipViewSet`` (send-email /
send-batch), the button HR (Unami, Dorothy) and Finance use to send an
individual staff member their payslip (CFO directive 2026-07-28).

It deliberately routes through the SAME release gate as the employee's own
self-service copy (``signoff_service.release_blocked_reason``): a payroll that
the CFO has not signed off — or one from 2026-07 onward that has drifted since
sign-off — must not be pushed to staff from here either. That is the whole
point of the sign-off control, so this button respects it rather than being a
side door around it.

The batch management command ``send_payslips_for_period`` predates this and is
left as-is; this module is the on-demand single/selected-send path.
"""
from __future__ import annotations

import html as _html
import logging

from django.core.mail import EmailMultiAlternatives

from .models import PayslipComponent
from .pdf import generate_payslip_pdf
from .signoff_service import release_blocked_reason

log = logging.getLogger(__name__)

FROM_EMAIL = 'Omni ERP <omni@alphadirect.co.bw>'

# House brand (CFO standard): navy header, orange accent, Book Antiqua title.
NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _money(v) -> str:
    return f'BWP {float(v or 0):,.2f}'


def _summary_rows(payslip) -> str:
    """Earnings / deductions breakdown from the payslip's own component lines.

    Written for the 2026-07-29 register fix: Commission and Incentive are
    material earnings that the old plain-text email never showed, so staff had
    to open the PDF to see whether their commission was in the run at all.
    Deductions are shown as magnitudes with a minus sign — the stored sign is
    not consistent across importers, so never render it raw.
    """
    K = PayslipComponent.Kind
    earning_kinds = (K.EARNING, K.EARNING_NON_TAXABLE)
    out = []
    for ln in payslip.lines.select_related('component').all():
        kind = ln.component.kind
        if kind == K.COMPANY_CONTRIBUTION:
            continue          # cost to company, not part of the employee's pay
        amount = float(ln.amount or 0)
        if not amount:
            continue
        is_earning = kind in earning_kinds
        value = _money(abs(amount)) if is_earning else f'&minus;{_money(abs(amount))}'
        colour = '#1e293b' if is_earning else '#B91C1C'
        out.append(
            f'<tr><td style="padding:4px 14px 4px 0;color:#64748b;">'
            f'{_html.escape(ln.component.name)}</td>'
            f'<td style="padding:4px 0;text-align:right;color:{colour};">{value}</td></tr>'
        )
    return ''.join(out)


def _body_html(payslip, first_name: str) -> str:
    e = _html.escape
    period = payslip.period
    rows = _summary_rows(payslip)
    breakdown = (
        f'<table style="border-collapse:collapse;margin:14px 0;font-size:13px;width:100%;'
        f'max-width:380px;">{rows}'
        f'<tr><td style="padding:8px 14px 4px 0;border-top:1px solid #e2e8f0;'
        f'color:#64748b;font-weight:600;">Net pay</td>'
        f'<td style="padding:8px 0 4px;border-top:1px solid #e2e8f0;text-align:right;'
        f'font-weight:700;color:{ORANGE};">{_money(payslip.net_amount)}</td></tr>'
        f'</table>'
    ) if rows else (
        f'<table style="border-collapse:collapse;margin:14px 0;font-size:13px;">'
        f'<tr><td style="padding:4px 14px 4px 0;color:#64748b;">Gross</td>'
        f'<td style="padding:4px 0;text-align:right;">{_money(payslip.gross_amount)}</td></tr>'
        f'<tr><td style="padding:4px 14px 4px 0;color:#64748b;">PAYE</td>'
        f'<td style="padding:4px 0;text-align:right;color:#B91C1C;">'
        f'&minus;{_money(payslip.paye_amount)}</td></tr>'
        f'<tr><td style="padding:8px 14px 4px 0;border-top:1px solid #e2e8f0;'
        f'color:#64748b;font-weight:600;">Net pay</td>'
        f'<td style="padding:8px 0 4px;border-top:1px solid #e2e8f0;text-align:right;'
        f'font-weight:700;color:{ORANGE};">{_money(payslip.net_amount)}</td></tr>'
        f'</table>'
    )
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto;">
  <div style="background:{NAVY};padding:18px 22px;border-radius:10px 10px 0 0;">
    <div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#fff;font-size:20px;font-weight:600;">Your payslip</div>
    <div style="color:{ORANGE};font-size:11px;letter-spacing:.08em;margin-top:2px;">ALPHA DIRECT · {e(period.period_name).upper()} PAYROLL</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 10px 10px;padding:20px 22px;color:#1e293b;font-size:14px;line-height:1.55;">
    <p>Hi {e(first_name)},</p>
    <p>Your payslip for {e(period.period_name)} is attached as a PDF. Here is the summary:</p>
    {breakdown}
    <p style="color:#64748b;font-size:12px;">The attached PDF is your full payslip.</p>
    <p>If anything looks wrong, reply to this email or contact the Human Capital team.</p>
    <p style="margin-top:18px;">Regards,<br/>Alpha Direct &mdash; Payroll</p>
  </div>
</div>"""


def send_one_payslip(payslip, *, user=None, request=None) -> dict:
    """Email ``payslip`` to its employee.

    Returns an outcome dict and never raises for the expected business cases,
    so a batch loop can keep going and report per-slip:
        {'ok': True,  'email': <addr>}
        {'ok': False, 'reason': 'no_email'|'blocked'|'pdf_error'|'send_error',
                      'detail': <human string>}
    """
    emp = payslip.employee
    email = (getattr(emp, 'email', '') or '').strip()
    if not email:
        return {'ok': False, 'reason': 'no_email',
                'detail': f'{emp.full_name} has no email address on file.'}

    blocked = release_blocked_reason(payslip)
    if blocked:
        return {'ok': False, 'reason': 'blocked', 'detail': blocked}

    try:
        pdf = generate_payslip_pdf(payslip)
    except Exception:  # report, don't crash a batch send
        log.exception('payslip PDF generation failed for %s', payslip.pk)
        return {'ok': False, 'reason': 'pdf_error',
                'detail': f'Could not build the payslip PDF for {emp.full_name}. '
                          'Please try again or contact IT.'}

    period = payslip.period
    first = (emp.full_name or '').split()[0] if (emp.full_name or '').strip() else 'there'
    subject = f'Your payslip — {period.period_name}'
    text = (
        f'Hi {first},\n\n'
        f'Your payslip for the {period.period_name} payroll period is attached '
        f'as a PDF.\n\n'
        f'Gross: {_money(payslip.gross_amount)}\n'
        f'PAYE: -{_money(payslip.paye_amount)}\n'
        f'Net pay: {_money(payslip.net_amount)}\n\n'
        f'For any payroll queries, reply to this email or contact the Human '
        f'Capital team.\n\n'
        f'— Alpha Direct Payroll (via Omni)\n'
    )
    # House-brand HTML (CFO standard: every email HTML). The old body was the
    # plain text with newlines swapped for <br/>, which rendered unbranded and
    # showed no figures — staff had to open the PDF to see if their commission
    # was in the run.
    try:
        html = _body_html(payslip, first)
    except Exception:  # a template slip must never block a real payslip send
        log.exception('payslip HTML body failed for %s; sending plain', payslip.pk)
        html = text.replace('\n', '<br/>')
    try:
        msg = EmailMultiAlternatives(subject, text, FROM_EMAIL, [email])
        msg.attach_alternative(html, 'text/html')
        msg.attach(
            f'payslip-{emp.employee_number}-{period.period_name}.pdf',
            pdf, 'application/pdf',
        )
        msg.send()
    except Exception:
        log.exception('payslip email send failed for %s -> %s', payslip.pk, email)
        return {'ok': False, 'reason': 'send_error',
                'detail': f'The payslip could not be emailed to {email}. Please try again.'}

    # Immutable audit trail — who sent whose payslip out, when. A payslip PDF
    # leaving the system is a DOWNLOAD-class event (not a READ), so record it
    # as one. Best-effort: an audit hiccup must never undo a successful send.
    try:
        from core.models import AuditLog
        ip = None
        if request is not None:
            ip = ((request.META.get('HTTP_X_FORWARDED_FOR', '') or '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR'))
        AuditLog.objects.create(
            table_name='payroll.Payslip',
            record_id=str(payslip.pk),
            action=AuditLog.Action.DOWNLOAD,
            user=user if getattr(user, 'is_authenticated', False) else None,
            ip_address=ip,
            description=f'Emailed payslip {period.period_name} to {email}'[:500],
        )
    except Exception:
        # Best-effort — a broken audit write must never undo a real send, but
        # log it so a silently-failing trail is at least visible.
        log.exception('audit log for payslip send failed (%s -> %s)', payslip.pk, email)

    return {'ok': True, 'email': email}
