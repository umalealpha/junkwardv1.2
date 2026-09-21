"""Load a BONU performance workbook into the Omni schedule store.

    python manage.py bonu_import_schedule "/path/BONU PERFORMANCE REPORT 2025.xlsx"

--dry-run parses and prints the per-sheet counts WITHOUT writing (check before you
commit). The workbook holds member names — parsed on the box, never sent anywhere.
"""
from django.core.management.base import BaseCommand, CommandError

from bonu import schedule


class Command(BaseCommand):
    help = 'Import a BONU performance workbook into the schedule store (all sheets, verbatim).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--wipe', action='store_true',
                            help='Delete all existing schedule sheets first (clean replacement).')
        parser.add_argument('--source-note', default='')

    def handle(self, *args, **opts):
        import os
        path = opts['path']
        if not os.path.exists(path):
            raise CommandError(f'File not found: {path}')
        if opts['wipe']:
            from bonu.models import BonuScheduleSheet
            n = BonuScheduleSheet.objects.count()
            verb = 'WOULD WIPE' if opts['dry_run'] else 'WIPING'
            self.stdout.write(self.style.WARNING(f'{verb} {n} existing schedule sheet(s) first.'))
        summary = schedule.import_workbook(
            path, source_note=opts['source_note'] or os.path.basename(path),
            commit=not opts['dry_run'], wipe=opts['wipe'])
        verb = 'WOULD import' if opts['dry_run'] else 'imported'
        for s in summary:
            self.stdout.write(f"{verb} {s['title']:<22} key={s['key']:<18} "
                              f"cols={s['columns']:<3} rows={s['rows']:<5} "
                              f"amount_col={s['amount_column'] or '-'}")
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(summary)} sheet(s)."))
