"""Email the weekly claims update to the recipients Finance keep.

Runs Monday 05:00 UTC = 07:00 in Gaborone (infra/cron/weekly-claims-update.cron),
the slot the CFO chose: on Finance's desk when they start the week.

THIS REPLACES A HUMAN STEP, so it has to fail the way a human would notice.
Four things it deliberately does NOT do:

  * it does not send a cheerful "0 claims" email when the mirror is empty or the
    detail sync has died. It sends nothing and exits non-zero, so the morning it
    breaks looks different from a quiet week;
  * it does not send to nobody. An empty recipient list is a configuration
    mistake, not a reason to report success;
  * it does not fall back to a hardcoded address. If Finance have not filled the
    list in, the command says so and stops — a built-in fallback would mean the
    screen they were told to use had no effect;
  * it does not quietly bucket a policy prefix it does not know. Those claims
    are counted, named, and put in front of the reader in the body of the email.

--dry-run prints what would go out and sends nothing. That is how this is proven
against production data before it is ever allowed to send.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from claims import weekly_update, weekly_update_report
from core.notifications import send_html_with_cfo_cc
from reporting.models import ReportRecipient

#: The key Finance's recipient list is stored under.
REPORT_SLUG = 'weekly-claims-update'

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


class Command(BaseCommand):
    help = ('Email the weekly claims update (inception-to-date, by month '
            'reported) to the recipient list Finance keep in Omni.')

    def add_arguments(self, parser):
        parser.add_argument('--weeks', type=int,
                            default=weekly_update.DEFAULT_WEEKS,
                            help='Weeks counted as "recently reported" in the '
                                 'covering email (default 1).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be sent; send nothing.')
        parser.add_argument('--to', default='',
                            help='Override recipients, comma-separated (testing).')

    def handle(self, *args, **opts):
        weeks = max(1, int(opts['weeks']))
        today = timezone.localdate()

        # Could we look at all, and is what we are looking at carrying figures?
        # "No claims" and "the detail sync died" both produce a perfect, fully
        # footed, entirely zero report. Neither may be emailed as good news.
        try:
            health = weekly_update.mirror_is_reporting()
        except weekly_update.SourceUnavailable as exc:
            self.stderr.write(self.style.ERROR(
                f'The claims mirror is unreadable — no report sent ({exc}).'))
            raise SystemExit(2)
        if not health['reporting']:
            self.stderr.write(self.style.ERROR(
                f"The claims mirror holds {health['claims']:,} claim(s) and "
                f"{health['detailed']:,} of them have ever had their detail "
                f"synced from Graphite, so every reserve and payment figure in "
                f"this report would be zero. Nothing sent. Fix the Graphite "
                f"claims sync (pull_graphite_claims) before this report means "
                f"anything."))
            raise SystemExit(4)

        try:
            claim_rows = weekly_update.rows()
        except weekly_update.SourceUnavailable as exc:
            self.stderr.write(self.style.ERROR(
                f'The claims mirror is unreadable — no report sent ({exc}).'))
            raise SystemExit(2)

        summary = weekly_update.summarise(claim_rows, weeks=weeks, today=today)
        self.stdout.write(
            f"{summary['count']:,} claims, reserve P {summary['reserve']:,.2f}, "
            f"paid P {summary['payment']:,.2f}, as at {today:%Y-%m-%d}")
        if summary['unrecognised']:
            # Printed to the console as well as emailed: the cron log is where
            # somebody looks first when a group total moves unexpectedly.
            for u in summary['unrecognised']:
                self.stdout.write(self.style.WARNING(
                    f"  unrecognised policy prefix {u['prefix']}: "
                    f"{u['count']} claim(s), e.g. {u['example']}"))
        if summary['undated']:
            self.stdout.write(self.style.WARNING(
                f"  {summary['undated']} claim(s) have no reported date"))

        if opts['to']:
            to = [a.strip() for a in opts['to'].split(',') if a.strip()]
            cc: list[str] = []
        else:
            route = ReportRecipient.route_for(REPORT_SLUG)
            to = route['to'] if route else []
            cc = route['cc'] if route else []

        if not to:
            self.stderr.write(self.style.ERROR(
                'Nobody is on the weekly claims update list — nothing sent. '
                'Add the recipients in Omni under the report recipients screen '
                f'against "{REPORT_SLUG}".'))
            raise SystemExit(3)

        xlsx = weekly_update_report.build_xlsx(summary)
        html = weekly_update_report.build_html(summary)
        subject = weekly_update_report.subject(today)
        fname = weekly_update_report.filename(today)

        if opts['dry_run']:
            self.stdout.write(f'DRY RUN — would send "{subject}"')
            self.stdout.write(f'  to: {", ".join(to)}')
            self.stdout.write(f'  cc: {", ".join(cc) or "(none)"}')
            self.stdout.write(f'  attachment: {fname} '
                              f'({len(xlsx.getvalue()):,} bytes)')
            for g in summary['by_group']:
                self.stdout.write(f"    {g['group']}: {g['count']:,} claims "
                                  f"/ reserve P {g['reserve']:,.2f} "
                                  f"/ paid P {g['payment']:,.2f}")
            return

        sent = send_html_with_cfo_cc(
            subject=subject,
            html=html,
            to=to,
            cc=cc or None,
            attachments=[(fname, xlsx.getvalue(), _XLSX_MIME)],
            # Finance operational mail. The CFO is not auto-copied on a weekly
            # claims position; anyone who wants it adds themselves to the list.
            cc_cfo=False,
            # This email ASKS A QUESTION when a prefix is unrecognised ("please
            # tell Finance what group these belong to"), and the standing rule
            # is that the do-not-reply banner never goes on an email that asks
            # one. It is off in both cases so the reader sees one consistent
            # thing week to week.
            no_reply=False,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Sent to {len(to)} recipient(s), {len(cc)} copied (send={sent}).'))
