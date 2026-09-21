"""
hris/management/commands/discretionary_leave_report.py

Monthly list of who keeps using DISCRETIONARY leave (CFO 2026-09-10).

Compassionate / study / special are granted at the company's discretion, and
they were being used to preserve annual-leave days. One request tells you
nothing; the PATTERN does. This emails the CFO (cc the Chief Human Capital
Officer) a table of everyone who has used these types in the last 12 months,
worst first, with what they still hold in annual leave next to it — the
comparison that makes the abuse obvious.

Schedule (prod cron, 1st of the month 07:00 local = 05:00 UTC):
  python manage.py discretionary_leave_report

Sends nothing at all in a month with no discretionary leave — a monthly empty
table trains people to ignore the mail.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris import discretionary_leave as dl
from hris.models import LeaveRequest

DEFAULT_TO = ['pganesharajah@alphadirect.co.bw']    # CFO
DEFAULT_CC = ['ubutale@alphadirect.co.bw']          # Unami Butale, CHCO
NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _annual_available(profile) -> str:
    try:
        from hris.leave_balance import balances_for_profile
        row = next((b for b in balances_for_profile(profile)
                    if b['code'] == 'annual'), None)
        return f"{float(row['available']):g}" if row else '—'
    except Exception:      # noqa: BLE001 — a balance hiccup must not kill the report
        return '—'


class Command(BaseCommand):
    help = ('Email the CFO a 12-month list of repeat users of discretionary '
            '(compassionate / study / special) leave.')

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=12,
                            help='Look-back window in months (default 12).')
        parser.add_argument('--min-requests', type=int, default=1,
                            help='Only list people with at least this many (default 1).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print it instead of sending it.')
        parser.add_argument('--to', default='', help='Comma-separated recipient override.')

    def handle(self, *args, **opts):
        months = opts['months']
        since = timezone.localdate() - dt.timedelta(days=months * 30)
        rows = (LeaveRequest.objects
                .filter(leave_type__code__in=dl.DISCRETIONARY_TYPES,
                        start_date__gte=since,
                        status__in=[LeaveRequest.Status.APPROVED,
                                    LeaveRequest.Status.PENDING])
                .select_related('profile__employee', 'leave_type')
                .order_by('start_date'))

        people: dict = defaultdict(lambda: {'name': '', 'department': '', 'profile': None,
                                            'count': 0, 'days': Decimal('0'),
                                            'types': defaultdict(int)})
        for lr in rows:
            emp = lr.profile.employee
            rec = people[lr.profile_id]
            rec['name'] = emp.full_name
            rec['department'] = emp.department or '—'
            rec['profile'] = lr.profile
            rec['count'] += 1
            rec['days'] += Decimal(str(lr.days or 0))
            rec['types'][lr.leave_type.name] += 1

        listed = [r for r in people.values() if r['count'] >= opts['min_requests']]
        listed.sort(key=lambda r: (-r['count'], -float(r['days'])))

        if not listed:
            self.stdout.write('No discretionary leave in the window — nothing sent.')
            return

        period = f'{since:%d %b %Y} – {timezone.localdate():%d %b %Y}'
        subject = (f'Discretionary leave – {len(listed)} people, '
                   f'{sum(r["count"] for r in listed)} requests ({period})')
        text, html = self._render(listed, period, months)

        if opts['dry_run']:
            self.stdout.write(subject)
            self.stdout.write(text)
            return

        to = [x.strip() for x in opts['to'].split(',') if x.strip()] or DEFAULT_TO
        msg = EmailMultiAlternatives(subject=subject, body=text, from_email=None,
                                     to=to, cc=(DEFAULT_CC if not opts['to'] else None))
        msg.attach_alternative(html, 'text/html')
        sent = msg.send(fail_silently=False)
        self.stdout.write(f'sent={sent} to={to} people={len(listed)}')

    def _render(self, listed, period, months):
        lines = [f'Discretionary leave (compassionate / study / special), last {months} months',
                 f'Window: {period}', '']
        for r in listed:
            kinds = ', '.join(f'{k} x{v}' for k, v in sorted(r['types'].items()))
            lines.append(f"- {r['name']} ({r['department']}): {r['count']} request(s), "
                         f"{float(r['days']):g} day(s) — {kinds}. "
                         f"Annual still available: {_annual_available(r['profile'])}d")
        text = '\n'.join(lines)

        body = ''.join(
            f"<tr>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{r['name']}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>{r['department']}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee;text-align:right'>"
            f"<b>{r['count']}</b></td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee;text-align:right'>"
            f"{float(r['days']):g}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee'>"
            f"{', '.join(f'{k} &times;{v}' for k, v in sorted(r['types'].items()))}</td>"
            f"<td style='padding:6px;border-bottom:1px solid #eee;text-align:right'>"
            f"{_annual_available(r['profile'])}</td>"
            f"</tr>"
            for r in listed)

        html = (
            f"<div style=\"font-family:'Book Antiqua',Georgia,serif;color:{NAVY};max-width:880px\">"
            f"<h2 style='color:{NAVY};border-bottom:3px solid {ORANGE};padding-bottom:6px'>"
            f"Discretionary leave &mdash; who keeps using it</h2>"
            f"<p>Compassionate, study and special leave over the last {months} months "
            f"({period}). Sorted by number of requests. The last column is the annual "
            f"leave they still hold &mdash; the days this leave type let them keep.</p>"
            f"<table cellpadding='0' cellspacing='0' "
            f"style='border-collapse:collapse;width:100%;font-size:13px'>"
            f"<thead><tr style='background:{NAVY};color:#fff;text-align:left'>"
            f"<th style='padding:6px'>Person</th><th style='padding:6px'>Department</th>"
            f"<th style='padding:6px;text-align:right'>Requests</th>"
            f"<th style='padding:6px;text-align:right'>Days</th>"
            f"<th style='padding:6px'>Types</th>"
            f"<th style='padding:6px;text-align:right'>Annual left</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
            f"<p style='color:#666;font-size:11px;margin-top:14px'>Every one of these was "
            f"signed off by the CFO before it could be approved. Pending requests are "
            f"included. Source: Omni leave records.</p></div>"
        )
        return text, html
