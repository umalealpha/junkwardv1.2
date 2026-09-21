"""Email the monthly Instant Insurance admin fees to Finance for review.

Runs on the 26th (infra/cron/instant-admin-fees.cron), covering the 27th of the
previous month to the 26th of this one.

THIS REPLACES A HUMAN STEP, so it has to fail the way a human would notice.
Three things it deliberately does NOT do:

  * it does not send a cheerful all-zero email when the data is not there. If
    Graphite cannot be read it sends nothing and exits non-zero, so the morning it
    breaks looks different from a quiet month;
  * it does not send to nobody. An empty recipient list is a configuration
    mistake, not a reason to report success;
  * it does not fall back to a hardcoded address. If Finance have not filled the
    list in, the command says so and stops — a built-in fallback would mean the
    screen they were told to use had no effect.

It also does not post anything. The admin fee goes to Finance for review and GL
posting, which is a human step (spec B5, and the standing rule that Omni never
moves money).

--dry-run prints what would go out, INCLUDING the Graphite agency names each
merchant pattern actually matched, and sends nothing. Run it once before go-live:
that print is how a wrong store spelling is caught in one run rather than by a
partner asking where their fee went.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.notifications import send_html_with_cfo_cc
from reporting import instant_admin_fees, instant_admin_fees_report
from reporting.models import ReportRecipient

#: The key Finance's recipient list is stored under.
REPORT_SLUG = 'instant-admin-fees-monthly'

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


class Command(BaseCommand):
    help = ('Email the monthly Instant Insurance admin fees (27th to 26th) for '
            'Choppies, Sefalana, Trans and Yash Cell to the recipient list '
            'Finance keep in Omni.')

    def add_arguments(self, parser):
        parser.add_argument('--month', default='',
                            help='Period to report, YYYY-MM (the month the 26th '
                                 'falls in). Defaults to the period that has '
                                 'most recently closed, in Botswana time.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be sent; send nothing.')
        parser.add_argument('--to', default='',
                            help='Override recipients, comma-separated (testing).')

    def handle(self, *args, **opts):
        year, month = self._period(opts['month'])

        try:
            data = instant_admin_fees.collect(year, month)
        except instant_admin_fees.GraphiteUnavailable as exc:
            # Could not look. Never the same as "no fees this month".
            self.stderr.write(self.style.ERROR(
                f'Graphite is unreachable — no report sent ({exc}).'))
            raise SystemExit(2)

        self.stdout.write(
            f"{data['label']}: {data['policies']:,} policies, premium "
            f"P {data['premium']:,.2f}, payable P {data['payable']:,.2f}")
        for block in data['blocks']:
            if block['nil']:
                self.stdout.write(f"  {block['nil_message']}")
            else:
                self.stdout.write(
                    f"  {block['merchant']}: {block['policies']:,} policies, "
                    f"premium P {block['premium']:,.2f}, "
                    f"payable P {block['payable']:,.2f}")

        if opts['to']:
            to = [a.strip() for a in opts['to'].split(',') if a.strip()]
            cc: list[str] = []
        else:
            route = ReportRecipient.route_for(REPORT_SLUG)
            to = route['to'] if route else []
            cc = route['cc'] if route else []

        if not to:
            self.stderr.write(self.style.ERROR(
                'Nobody is on the instant admin fees list — nothing sent. Add '
                'the recipients in Omni under Reporting > Report recipients '
                f'({REPORT_SLUG}).'))
            raise SystemExit(3)

        xlsx = instant_admin_fees_report.build_xlsx(data)
        html = instant_admin_fees_report.build_html(data)
        subject = instant_admin_fees_report.subject(data)
        fname = instant_admin_fees_report.filename(data)

        if opts['dry_run']:
            self.stdout.write(f'DRY RUN — would send "{subject}"')
            self.stdout.write(f'  to: {", ".join(to)}')
            self.stdout.write(f'  cc: {", ".join(cc) or "(none)"}')
            self.stdout.write(f'  attachment: {fname} '
                              f'({len(xlsx.getvalue()):,} bytes)')
            # The one thing worth reading before this ever sends: which Graphite
            # agency names each merchant pattern actually caught.
            for block in data['blocks']:
                names = ', '.join(block['source_names']) or '(nothing matched)'
                self.stdout.write(f"    {block['merchant']} <- {names}")
            if not data['plan_available']:
                self.stdout.write(self.style.WARNING(
                    '  product plan could not be read — plan column shows a dash'))
            if data['unmatched']:
                self.stdout.write(self.style.WARNING(
                    f"  premium against unmatched stores: "
                    f"{', '.join(sorted(data['unmatched']))}"))
            return

        sent = send_html_with_cfo_cc(
            subject=subject,
            html=html,
            to=to,
            cc=cc or None,
            attachments=[(fname, xlsx.getvalue(), _XLSX_MIME)],
            # Finance operational mail. Anyone who wants it adds themselves to
            # the list on screen.
            cc_cfo=False,
        )
        self.stdout.write(self.style.SUCCESS(
            f'Sent to {len(to)} recipient(s), {len(cc)} copied (send={sent}).'))

    def _period(self, raw: str):
        raw = (raw or '').strip()
        if not raw:
            # timezone.localdate(), never date.today() — see current_period().
            return instant_admin_fees.current_period(timezone.localdate())
        try:
            d = datetime.datetime.strptime(raw, '%Y-%m')
        except ValueError:
            raise CommandError(f'--month must be YYYY-MM, got {raw!r}')
        return d.year, d.month
