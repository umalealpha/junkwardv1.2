"""Email the salary-advance mismatch warning to the recipients Finance keep.

WHY (CFO decision 13-Sep-2026): Omni does not raise the payout for an early
salary advance — Finance raise the payment by hand. So Omni's records and the
bank can disagree three ways, and the CFO asked for a warning rather than
automation. This is that warning.

Two things it deliberately does NOT do:

  * it does not email a cheerful empty report. With nothing to say it sends
    nothing and exits 0 — a weekly "all clear" is read for a month and then
    never read again, and the week it matters it is missed with the rest;
  * it does not send to nobody, and it does not fall back to a hardcoded
    address. An empty recipient list is a configuration mistake, and a built-in
    fallback would mean the screen Finance were told to use had no effect.

--dry-run prints what would go out and sends nothing.

Scheduled by infra/cron/salary-advance-mismatch.cron — which schedules nothing
until infra/install-crons.sh is run on the box. Cron there is UTC; Gaborone is
UTC+2.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc
from payroll import advance_mismatch, advance_mismatch_report
from reporting.models import ReportRecipient

#: The key Finance's recipient list is stored under.
REPORT_SLUG = 'salary-advance-mismatch'

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


class Command(BaseCommand):
    help = ('Email the salary advances whose approval, payout and recovery do '
            'not agree. Read-only.')

    def add_arguments(self, parser):
        parser.add_argument('--grace-days', type=int,
                            default=advance_mismatch.DEFAULT_GRACE_DAYS,
                            help='Days before an approved-and-unpaid advance is '
                                 'listed (default 2).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be sent; send nothing.')
        parser.add_argument('--to', default='',
                            help='Override recipients, comma-separated (testing).')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        result = advance_mismatch.find_mismatches(opts['grace_days'], today)

        self.stdout.write(
            f"{result['count']} mismatch(es), P {result['value']:,.2f}, "
            f"as at {today:%Y-%m-%d}")

        if result['count'] == 0:
            # Nothing to say, so nothing is sent. Said on stdout (and in the
            # cron log) so a quiet week still looks different from a dead job.
            self.stdout.write('Nothing to report — no email sent.')
            return

        if opts['to']:
            to = [a.strip() for a in opts['to'].split(',') if a.strip()]
            cc: list[str] = []
        else:
            route = ReportRecipient.route_for(REPORT_SLUG)
            to = route['to'] if route else []
            cc = route['cc'] if route else []

        if not to:
            self.stderr.write(self.style.ERROR(
                'Nobody is on the salary-advance mismatch list — nothing sent. '
                'Add the recipients in Omni under Reports > Report recipients, '
                f'report "{REPORT_SLUG}".'))
            raise SystemExit(3)

        xlsx = advance_mismatch_report.build_xlsx(result)
        html = advance_mismatch_report.build_html(result)
        subject = advance_mismatch_report.subject(result)
        fname = advance_mismatch_report.filename(today)

        if opts['dry_run']:
            self.stdout.write(f'DRY RUN — would send "{subject}"')
            self.stdout.write(f'  to: {", ".join(to)}')
            self.stdout.write(f'  cc: {", ".join(cc) or "(none)"}')
            self.stdout.write(f'  attachment: {fname} '
                              f'({len(xlsx.getvalue()):,} bytes)')
            return

        sent = send_html_with_cfo_cc(
            subject=subject,
            html=html,
            to=to,
            cc=cc or None,
            attachments=[(fname, xlsx.getvalue(), _XLSX_MIME)],
            # The CFO approves every advance personally, so a disagreement about
            # one is his to see.
            cc_cfo=True,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Sent to {len(to)} recipient(s), {len(cc)} copied (send={sent}).'))
