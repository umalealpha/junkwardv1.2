"""
pull_bank_feeds — cron-friendly entrypoint that runs every ACTIVE config.

Add to crontab:
    0 6 * * * /path/to/venv/bin/python /path/to/manage.py pull_bank_feeds

Per-config schedules live on BankFeedConfig.schedule_cron — when a real
scheduler (e.g. Celery Beat) is wired, it reads that field. For now,
running this command nightly handles all daily feeds together.
"""

from django.core.management.base import BaseCommand

from bank_feeds.models import BankFeedConfig
from bank_feeds.services import run_feed


class Command(BaseCommand):
    help = 'Run every active bank feed configuration.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--config',
            type=str,
            help='Run only the named config (matches BankFeedConfig.name).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List which configs would run, do not execute.',
        )

    def handle(self, *args, **options):
        qs = BankFeedConfig.objects.filter(status=BankFeedConfig.Status.ACTIVE)
        if options.get('config'):
            qs = qs.filter(name=options['config'])

        if not qs.exists():
            self.stdout.write(self.style.WARNING(
                'No active bank feed configs to run.'
            ))
            return

        for cfg in qs:
            if options.get('dry_run'):
                self.stdout.write(f'(dry-run) would run: {cfg.name}')
                continue
            run = run_feed(cfg, triggered_by=None)
            colour = (
                self.style.SUCCESS if run.outcome == 'success'
                else self.style.WARNING if run.outcome == 'empty'
                else self.style.ERROR
            )
            self.stdout.write(colour(
                f'{cfg.name}: {run.outcome} '
                f'(files={run.files_seen}, lines={run.lines_created})'
            ))
