from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from hris.contract_module import run_reminders


class Command(BaseCommand):
    help = 'Send contract renewal reminders (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Send emails and write reminder logs.')
        parser.add_argument('--today', help='Override today as YYYY-MM-DD for testing.')

    def handle(self, *args, **options):
        today_str = options.get('today')
        if today_str:
            try:
                today = date.fromisoformat(today_str)
            except ValueError:
                self.stderr.write('Invalid --today. Use YYYY-MM-DD.')
                return
        else:
            today = timezone.localdate()

        counts = run_reminders(today, commit=bool(options.get('commit', False)))

        if not options.get('commit'):
            self.stdout.write('DRY-RUN: no emails sent, no logs written.')
        self.stdout.write(f"sent={counts['sent']} skipped={counts['skipped']} errors={counts['errors']}")
        # Probation ends (HC 19-Sep-2026): 30 days ahead, weekly until decided.
        from hris.contract_followup import run_probation_reminders
        pcounts = run_probation_reminders(today, commit=bool(options.get('commit', False)))
        self.stdout.write(f'probation: {pcounts}')
