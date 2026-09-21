"""
ledger/management/commands/generate_recurring_journal_entries.py

Run as a scheduled task (cron) once per day, or manually:

    python manage.py generate_recurring_journal_entries
    python manage.py generate_recurring_journal_entries --target-date 2026-05-31
    python manage.py generate_recurring_journal_entries --user prathap
"""

from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ledger.recurring import generate_all_due


class Command(BaseCommand):
    help = 'Generate draft journal entries for every active recurring template that has reached its schedule.'

    def add_arguments(self, parser):
        parser.add_argument('--target-date', help='Date to generate up to (YYYY-MM-DD). Default: today.')
        parser.add_argument('--user', default='admin',
                            help='Username to credit as the creator of generated drafts.')

    def handle(self, *args, **opts):
        if opts.get('target_date'):
            try:
                target = datetime.strptime(opts['target_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError(f"Invalid date: {opts['target_date']}")
        else:
            # BOTSWANA's today (settings.TIME_ZONE): on a UTC box date.today()
            # is yesterday between 00:00 and 02:00 Gaborone, so a run in that
            # window skipped every template due today.
            target = timezone.localdate()

        try:
            user = User.objects.get(username=opts['user'])
        except User.DoesNotExist:
            user = User.objects.filter(is_superuser=True).first()
            if user is None:
                raise CommandError('No superuser found to credit drafts to.')

        result = generate_all_due(target, user)

        self.stdout.write(self.style.SUCCESS(
            f'\nRecurring JE generation up to {result.period_end}'
        ))
        self.stdout.write(f'  Templates considered: {result.templates_considered}')
        self.stdout.write(f'  Entries generated:    {result.entries_generated}')
        self.stdout.write(f'  Templates with nothing due: {result.entries_skipped}')
        if result.errors:
            self.stdout.write(self.style.WARNING(f'  Errors: {len(result.errors)}'))
            for err in result.errors[:10]:
                self.stdout.write(f'    - {err}')
