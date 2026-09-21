"""
Daily statutory-tax compliance sweep.

One command, run once a day by cron, doing both halves in order:

  1. generate any missing obligations for the next 18 months
  2. advance the state machine and send the day's reminders

They are one command rather than two crons on purpose: a deadline that falls
inside its own reminder window on the day it is first generated must be reminded
about in the SAME run, not tomorrow.

    python manage.py tax_compliance_sweep
    python manage.py tax_compliance_sweep --dry-run
"""
from django.core.management.base import BaseCommand

from regulatory.tax_workflow import generate_tasks, run_reminders, today_gabs


class Command(BaseCommand):
    help = 'Generate statutory tax obligations and send the day\'s reminders.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would happen without saving or emailing.',
        )
        parser.add_argument(
            '--months-ahead', type=int, default=None,
            help='Override how far ahead obligations are generated.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        self.stdout.write(f'Tax compliance sweep — {today_gabs():%d %b %Y} (Gaborone)'
                          + (' [DRY RUN]' if dry else ''))

        if dry:
            # Generation writes rows, so a dry run must not call it. The reminder
            # half still reports honestly against whatever is already there.
            self.stdout.write('  generation: skipped (dry run)')
        else:
            gen = generate_tasks(months_ahead=opts['months_ahead'])
            self.stdout.write(
                f"  generated: {gen['created']} new of {gen['considered']} obligations in window"
            )

        stats = run_reminders(dry_run=dry)
        self.stdout.write(
            '  activated={activated} breached={breached} reminders={reminders_sent} '
            'breach_alerts={breach_alerts} no_owner={skipped_no_owner}'.format(**stats)
        )
        if stats['skipped_no_owner']:
            self.stdout.write(self.style.WARNING(
                f"  {stats['skipped_no_owner']} obligation(s) have no owner with an email — "
                'nobody is being reminded about those. Assign an owner on the Tax Calendar page.'
            ))
        self.stdout.write(self.style.SUCCESS('  done'))
