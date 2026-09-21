"""
Load employee start/hire dates from Dorothy's spreadsheet.

Usage:
    python manage.py load_start_dates /path/to/spreadsheet.xlsx
    python manage.py load_start_dates /path/to/spreadsheet.xlsx --commit

Dry-run by default. Pass --commit to write.
"""
import datetime
import logging

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)

SKIP_MARKERS = frozenset({
    'attache does not accrue leave',
    'consultant',
    'resigned',
})


def _parse_date(val):
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    s = str(val).strip()
    if not s or s.lower() in SKIP_MARKERS:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d/%m%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = 'Load employee start dates from the spreadsheet Dorothy sent back.'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the .xlsx file')
        parser.add_argument('--commit', action='store_true',
                            help='Actually write to the database (dry-run by default)')

    def handle(self, *args, **options):
        import openpyxl
        from payroll.models import Employee

        path = options['file']
        commit = options['commit']
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active

        updated, skipped, not_found, already_set = 0, 0, 0, 0

        for row in ws.iter_rows(min_row=6, values_only=True):
            name = row[0]
            email = (row[4] or '').strip().lower() if row[4] else ''
            raw_date = row[10]  # Column K

            if not name or not email:
                continue

            date = _parse_date(raw_date)
            if date is None:
                raw_str = str(raw_date or '').strip().lower()
                if raw_str in SKIP_MARKERS:
                    skipped += 1
                    self.stdout.write(f'  SKIP  {name}: {raw_str}')
                else:
                    skipped += 1
                    self.stdout.write(f'  EMPTY {name} ({email}): no date filled in')
                continue

            emp = (Employee.objects
                   .filter(email__iexact=email, is_test_record=False)
                   .order_by('-status', 'created_at')
                   .first())
            if emp is None:
                not_found += 1
                self.stdout.write(f'  MISS  {name} ({email}): no Employee record')
                continue

            if emp.hire_date:
                already_set += 1
                self.stdout.write(
                    f'  KEPT  {name}: already has {emp.hire_date} (sheet says {date})')
                continue

            if commit:
                emp.hire_date = date
                emp.save(update_fields=['hire_date', 'updated_at'])
            updated += 1
            self.stdout.write(f'  {"SET " if commit else "WOULD"} {name}: {date}')

        mode = 'COMMITTED' if commit else 'DRY RUN'
        self.stdout.write(
            f'\n{mode}: {updated} updated, {already_set} already had a date, '
            f'{skipped} skipped, {not_found} not found in Omni.')
