"""Monthly "who was given access, and by whom" report.

CFO directive 2026-09-15. He delegated day-to-day access administration to Unopa
Male and, asked whether he wanted sight of it, chose a monthly report to BOTH of
them: *"one email on the 1st: everything granted and revoked last month, by whom."*

This is the safety net that makes delegating safe. The guard in
`core.access_delegate` stops the dangerous grants; this shows him the ordinary
ones without him having to go and look.

Reads the audit log that user administration already writes — no new plumbing.

Run:  python manage.py email_access_report [--month YYYY-MM] [--dry-run]
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import AuditLog

GABS = ZoneInfo('Africa/Gaborone')
NAVY = '#0D1B2A'
ORANGE = '#F4A623'

#: Both the CFO and the delegate see the same report — he asked for that
#: deliberately, so nobody is being audited behind their back.
DEFAULT_TO = ['pganesharajah@alphadirect.co.bw', 'umale@alphadirect.co.bw']


def _window(month: str | None):
    """(start, end, label) for the month to report on — last month by default."""
    now = timezone.localtime(timezone.now(), GABS)
    if month:
        year, mon = (int(p) for p in month.split('-'))
    else:
        year, mon = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    start = datetime(year, mon, 1, tzinfo=GABS)
    end = (datetime(year + 1, 1, 1, tzinfo=GABS) if mon == 12
           else datetime(year, mon + 1, 1, tzinfo=GABS))
    return start, end, start.strftime('%B %Y')


def _changed(row) -> str:
    """One plain-English line for what this row actually changed."""
    old = row.old_values or {}
    new = row.new_values or {}
    bits = []
    for field, label in (('title', 'job title'),
                         ('role', 'role'),
                         ('is_administrator', 'administrator'),
                         ('is_access_delegate', 'access administrator'),
                         ('is_active', 'account active')):
        if field in new and old.get(field) != new.get(field):
            bits.append(f'{label}: {old.get(field, "—")} → {new.get(field)}')
    return '; '.join(bits) or (row.description or '—')


def build_report(month: str | None = None):
    start, end, label = _window(month)
    rows = list(
        AuditLog.objects
        .filter(table_name__in=['core.UserProfile', 'core.NamedModuleAccess'], created_at__gte=start, created_at__lt=end)
        .order_by("created_at")
    )
    subject = f'Omni access changes — {label} ({len(rows)})'

    if rows:
        body_rows = ''.join(
            '<tr>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">'
            f'{escape(timezone.localtime(r.created_at, GABS).strftime("%d %b"))}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">'
            f'{escape(str(getattr(r, "user", None) or "—"))}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">'
            f'{escape(str(r.description or "—"))}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee;">'
            f'{escape(_changed(r))}</td>'
            '</tr>'
            for r in rows
        )
        table = (
            '<table style="width:100%;border-collapse:collapse;font-size:14px;">'
            f'<tr style="background:{NAVY};color:#fff;text-align:left;">'
            '<th style="padding:8px 10px;">Date</th>'
            '<th style="padding:8px 10px;">Changed by</th>'
            '<th style="padding:8px 10px;">Who</th>'
            '<th style="padding:8px 10px;">What changed</th></tr>'
            f'{body_rows}</table>'
        )
        lead = f'{len(rows)} access change(s) in {label}.'
    else:
        table = '<p style="font-size:15px;">Nothing changed.</p>'
        lead = f'No access changes in {label}.'

    html = (
        '<div style="font-family:Georgia,\'Book Antiqua\',serif;max-width:760px;'
        'margin:0 auto;color:#222;">'
        f'<div style="background:{NAVY};padding:18px 22px;">'
        f'<div style="color:{ORANGE};font-size:20px;font-weight:bold;">'
        'Omni — access changes</div>'
        f'<div style="color:#dfe6ee;font-size:13px;margin-top:4px;">{escape(label)}</div>'
        '</div>'
        f'<div style="padding:20px 22px;"><p style="font-size:15px;">{escape(lead)}</p>'
        f'{table}'
        '<p style="font-size:13px;color:#666;margin-top:18px;">'
        'Manager-level, payroll, financial and administrator access cannot be granted '
        'here — those stay with the CFO.</p></div></div>'
    )
    text = f'{lead}\n\n' + '\n'.join(
        f'{timezone.localtime(r.created_at, GABS):%d %b} | '
        f'{getattr(r, "user", None) or "—"} | {r.description or "—"} | {_changed(r)}'
        for r in rows
    )
    return subject, html, text, len(rows)


class Command(BaseCommand):
    help = 'Email the monthly Omni access-change report to the CFO and the access administrator.'

    def add_arguments(self, parser):
        parser.add_argument('--month', default=None, help='YYYY-MM (default: last month).')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--to', nargs='*', default=None)

    def handle(self, *args, **opts):
        subject, html, text, count = build_report(opts['month'])
        recipients = opts['to'] or list(
            getattr(settings, 'ACCESS_REPORT_TO', None) or DEFAULT_TO)

        if opts['dry_run']:
            self.stdout.write(f'DRY-RUN -> {recipients}: {subject}')
            self.stdout.write(text)
            return

        from django.core.mail import EmailMultiAlternatives
        frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
               or getattr(settings, 'DEFAULT_FROM_EMAIL', None))
        msg = EmailMultiAlternatives(subject=subject, body=text,
                                     from_email=frm, to=recipients)
        msg.attach_alternative(html, 'text/html')
        msg.send()
        self.stdout.write(self.style.SUCCESS(
            f'Access report emailed to {recipients}: {count} change(s).'))
