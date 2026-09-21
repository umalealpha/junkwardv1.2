"""Email the weekly failed-debits chase list to the recipients Finance keep.

Runs Friday 12:00 UTC = 14:00 in Gaborone (infra/cron/failed-debits-report.cron),
which is the slot the CFO asked for (2026-09-12): the chase list on the desk
before the weekend, not at the start of the following week.

THIS REPLACES A HUMAN STEP, so it has to fail the way a human would notice.
Three things it deliberately does NOT do:

  * it does not send a cheerful "0 failed debits" email when the data is not
    there. It sends nothing and exits non-zero, so the morning it breaks looks
    different from a quiet week;
  * it does not send to nobody. An empty recipient list is a configuration
    mistake, not a reason to report success;
  * it does not fall back to a hardcoded address. If Finance have not filled the
    list in, the command says so and stops — a built-in fallback would mean the
    screen they were told to use had no effect, which is the whole problem this
    was built to fix.

--dry-run prints what would go out and sends nothing. That is how this was
proven against production data before it was ever allowed to send.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc
from realpay import failed_debits, failed_debits_report
from reporting.models import ReportRecipient

#: The key Finance's recipient list is stored under.
REPORT_SLUG = 'failed-debits-weekly'

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


class Command(BaseCommand):
    help = ('Email the week\'s failed debit orders, split by agent, to the '
            'recipient list Finance keep in Omni.')

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=failed_debits.DEFAULT_DAYS,
                            help='Days back to cover (default 7).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be sent; send nothing.')
        parser.add_argument('--to', default='',
                            help='Override recipients, comma-separated (testing).')

    def handle(self, *args, **opts):
        days = max(1, int(opts['days']))
        today = timezone.localdate()
        start, end = failed_debits.window(days, today)

        try:
            rows = failed_debits.failed_rows(days, today)
        except failed_debits.GraphiteUnavailable as exc:
            # Could not look. Never the same as "nothing failed".
            self.stderr.write(self.style.ERROR(
                f'Graphite is unreachable — no report sent ({exc}).'))
            raise SystemExit(2)

        # An empty result is only good news if the failure feed is alive. As at
        # 2026-09-11 it is NOT: RealPay's instalment advice for DOM/COM stopped
        # on 3 June 2026 and has been zero since (Pramod Bisen, ADRisk IT,
        # 10-Sep), so an unguarded run would email "0 failed debits" during a
        # week that really had eighteen. Refuse rather than reassure.
        try:
            health = failed_debits.book_is_reporting(days, today)
        except failed_debits.GraphiteUnavailable as exc:
            self.stderr.write(self.style.ERROR(
                f'Graphite is unreachable — no report sent ({exc}).'))
            raise SystemExit(2)
        if not health['reporting']:
            self.stderr.write(self.style.ERROR(
                f"No result has come back for ANY of the {health['no_outcome']} "
                f"commercial/domestic debits due {start:%d %b} to {end:%d %b}, so "
                f"this cannot say whether anything failed. Nothing sent. "
                f"RealPay's failure advice for DOM/COM stopped on 3 June 2026 "
                f"and has to be restored at source before this report means "
                f"anything."))
            raise SystemExit(4)

        summary = failed_debits.summarise(rows)
        self.stdout.write(
            f"{summary['count']} failed debits, P {summary['amount']:,.2f}, "
            f"{start:%Y-%m-%d}..{end:%Y-%m-%d}")


        if opts['to']:
            to = [a.strip() for a in opts['to'].split(',') if a.strip()]
            cc: list[str] = []
        else:
            route = ReportRecipient.route_for(REPORT_SLUG)
            to = route['to'] if route else []
            cc = route['cc'] if route else []

        if not to:
            self.stderr.write(self.style.ERROR(
                'Nobody is on the failed-debits list — nothing sent. Add the '
                'recipients in Omni under Collections > Failed debits.'))
            raise SystemExit(3)

        if not rows:
            # A genuinely clean week is worth saying out loud. Silence would be
            # indistinguishable from the job having died.
            self.stdout.write('No failed debits this week.')

        xlsx = failed_debits_report.build_xlsx(rows, summary, start, end)
        html = failed_debits_report.build_html(rows, summary, start, end)
        subject = failed_debits_report.subject(end)
        fname = failed_debits_report.filename(end)

        if opts['dry_run']:
            self.stdout.write(f'DRY RUN — would send "{subject}"')
            self.stdout.write(f'  to: {", ".join(to)}')
            self.stdout.write(f'  cc: {", ".join(cc) or "(none)"}')
            self.stdout.write(f'  attachment: {fname} '
                              f'({len(xlsx.getvalue()):,} bytes)')
            for a in summary['by_agent'][:15]:
                self.stdout.write(f"    {a['agent']}: {a['count']} "
                                  f"/ P {a['amount']:,.2f}")
            return

        sent = send_html_with_cfo_cc(
            subject=subject,
            html=html,
            to=to,
            cc=cc or None,
            attachments=[(fname, xlsx.getvalue(), _XLSX_MIME)],
            # Finance operational mail. The CFO is not auto-copied on a weekly
            # collections chase; anyone who wants it adds themselves to the list.
            cc_cfo=False,
            # NO "do not reply, log it in Omni" banner. This email asks in as
            # many words for a reply — "when a client pays by EFT, reply to
            # Debtors with the proof of payment" — and a banner telling the
            # reader not to reply would contradict the sentence above it. The
            # standing rule is that the banner never goes on an email that asks
            # a question. (Recipient-domain handling is done inside
            # send_html_with_cfo_cc anyway, so passing a flag here added nothing.)
            no_reply=False,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Sent to {len(to)} recipient(s), {len(cc)} copied (send={sent}).'))
