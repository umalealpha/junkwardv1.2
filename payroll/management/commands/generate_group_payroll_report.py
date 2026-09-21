from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from payroll.group_report import generate, notify, preview_data
from payroll.models import PayrollPeriod


class Command(BaseCommand):
    help = 'Generate versioned group payroll report snapshots across all payroll companies'

    def add_arguments(self, parser):
        parser.add_argument('--period', help='Payroll period in YYYY-MM format')
        parser.add_argument('--commit', action='store_true', help='Write the snapshot to the database')
        parser.add_argument('--notify', action='store_true', help='Send the group report email after generation')
        parser.add_argument(
            '--trigger',
            choices=['schedule', 'manual'],
            default='schedule',
            help='How this generation was triggered',
        )

    def handle(self, *args, **options):
        period_name = options.get('period') or timezone.localdate().strftime('%Y-%m')

        try:
            period = PayrollPeriod.objects.get(period_name=period_name)
        except PayrollPeriod.DoesNotExist:
            raise CommandError(
                f"Payroll period {period_name} does not exist. Create it before generating the group report."
            )

        rows, excluded, totals = preview_data(period)

        for row in rows:
            company_name = row['company'].name
            if row['status'] == 'not_run':
                self.stdout.write(
                    f"{company_name}: not_run, headcount={row['headcount']}, gross=P{row['gross']:,.2f}"
                )
            else:
                self.stdout.write(
                    f"{company_name}: {row['status']}, headcount={row['headcount']}, "
                    f"gross=P{row['gross']:,.2f}, paye=P{row['paye']:,.2f}, "
                    f"net=P{row['net']:,.2f}, ctc=P{row['ctc']:,.2f}"
                )

        self.stdout.write(
            f"Group totals: headcount={totals['headcount']}, ctc=P{Decimal(totals['ctc']):,.2f}"
        )
        if excluded:
            self.stdout.write(f"Companies not yet run: {len(excluded)}")

        if not options.get('commit'):
            self.stdout.write("DRY RUN: nothing written. Use --commit to write and --notify to email.")
            return

        snap = generate(period, trigger=options.get('trigger'), user=None)
        self.stdout.write(
            f"Created snapshot period={snap.period.period_name} version={snap.version} trigger={snap.trigger}"
        )

        if options.get('notify'):
            sent = notify(snap)
            self.stdout.write(f"Notification email sent to {sent} recipients")
