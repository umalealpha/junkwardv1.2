"""
hris/management/commands/hris_weekly_change_report.py

Weekly audit report of HRIS record changes, emailed to EXCO / Finance.

CFO directive 2026-06-25: senior HR (Unami, Dorothy) now edit HRIS records
DIRECTLY — no per-change approval (see hris.amendment_service._can_self_apply).
This report is the oversight trail: every HRISAmendment in the window — who
changed what (old -> new), on whom, and whether it was applied directly or
dual-approved.

Schedule (prod cron, Mondays 07:00 local):
  docker exec alpha-finance-backend python manage.py hris_weekly_change_report
"""
from __future__ import annotations

import datetime as dt

from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris.amendment_models import HRISAmendment

# CFO directive 2026-06-25: report goes to Oprah, the CFO, Kago and Pako.
DEFAULT_RECIPIENTS = [
    'omogomotsi@alphadirect.co.bw',    # Oprah Mogomotsi
    'pganesharajah@alphadirect.co.bw',  # Prathap Ganesharajah (CFO)
    'ktshutlhedi@alphadirect.co.bw',   # Kago Tshutlhedi
    'pkago@alphadirect.co.bw',         # Pako Kago
]
NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _how(a: HRISAmendment) -> str:
    """How the change reached the live record."""
    if a.status == HRISAmendment.Status.APPROVED:
        if a.maker_email and a.maker_email == a.approver_email:
            return 'Applied directly (self)'
        return f'Approved by {a.approver_email or "—"}'
    if a.status == HRISAmendment.Status.PENDING:
        return 'Pending approval'
    if a.status == HRISAmendment.Status.REJECTED:
        return 'Rejected'
    return a.status


def _fields_text(a: HRISAmendment) -> str:
    return '; '.join(
        f"{c.get('label', f)}: '{c.get('old', '')}' -> '{c.get('new', '')}'"
        for f, c in (a.changes or {}).items()
    ) or '(no field detail)'


def _fields_html(a: HRISAmendment) -> str:
    return '<br>'.join(
        f"{c.get('label', f)}: <b>{c.get('old', '') or '—'}</b> &rarr; <b>{c.get('new', '') or '—'}</b>"
        for f, c in (a.changes or {}).items()
    ) or '(no field detail)'


class Command(BaseCommand):
    help = 'Email the weekly HRIS change (amendment) audit report to EXCO/Finance.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7,
                            help='Look-back window in days (default 7).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the report instead of emailing it.')
        parser.add_argument('--to', default='',
                            help='Comma-separated recipient override.')

    def handle(self, *args, **opts):
        days = opts['days']
        since = timezone.now() - dt.timedelta(days=days)
        rows = list(
            HRISAmendment.objects.filter(created_at__gte=since).order_by('-created_at')
        )
        recipients = ([x.strip() for x in opts['to'].split(',') if x.strip()]
                      or DEFAULT_RECIPIENTS)
        period = f"{since:%d %b %Y} - {timezone.now():%d %b %Y}"
        subject = f"Omni HRIS - weekly change report ({period}) - {len(rows)} change(s)"
        text, html = self._render(rows, period, days)

        if opts['dry_run']:
            self.stdout.write(subject)
            self.stdout.write(text)
            self.stdout.write(f"[dry-run] would send to: {recipients}")
            return

        msg = EmailMultiAlternatives(subject=subject, body=text,
                                     from_email=None, to=recipients)
        msg.attach_alternative(html, 'text/html')
        sent = msg.send(fail_silently=False)
        self.stdout.write(f"sent={sent} recipients={recipients} changes={len(rows)}")

    def _render(self, rows, period, days):
        lines = [
            "Omni HRIS - weekly change report",
            f"Window: last {days} days ({period})",
            f"Total changes: {len(rows)}",
            "",
        ]
        for a in rows:
            lines.append(
                f"- {a.created_at:%Y-%m-%d %H:%M} | {a.target_label or a.target_id} "
                f"| {_fields_text(a)} | by {a.maker_email or '-'} | {_how(a)}"
            )
        if not rows:
            lines.append("No HRIS record changes this week.")
        text = "\n".join(lines)

        body_rows = "".join(
            f"<tr>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{a.created_at:%d %b %H:%M}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{a.target_label or a.target_id}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{_fields_html(a)}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{a.maker_email or '—'}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{_how(a)}</td>"
            f"</tr>"
            for a in rows
        ) or '<tr><td colspan="5" style="padding:10px">No HRIS record changes this week.</td></tr>'

        html = (
            f"<div style=\"font-family:'Book Antiqua',Georgia,serif;color:{NAVY};max-width:840px\">"
            f"<h2 style='color:{NAVY};border-bottom:3px solid {ORANGE};padding-bottom:6px'>"
            f"Omni HRIS &mdash; Weekly Change Report</h2>"
            f"<p>Window: last {days} days ({period}). Total changes: <b>{len(rows)}</b>.</p>"
            f"<table cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:13px'>"
            f"<thead><tr style='background:{NAVY};color:#fff;text-align:left'>"
            f"<th style='padding:6px'>When</th><th style='padding:6px'>Employee</th>"
            f"<th style='padding:6px'>Change</th><th style='padding:6px'>By</th>"
            f"<th style='padding:6px'>How</th></tr></thead>"
            f"<tbody>{body_rows}</tbody></table>"
            f"<p style='color:#666;font-size:11px;margin-top:14px'>Senior HR (Unami, Dorothy) "
            f"edit HRIS records directly; this is the oversight trail. "
            f"Source: HRISAmendment audit log.</p></div>"
        )
        return text, html
