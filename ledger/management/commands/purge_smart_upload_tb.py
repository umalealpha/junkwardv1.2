"""
ledger/management/commands/purge_smart_upload_tb.py

CFO directive 2026-05-19 — clean up duplicate / bad TB JEs created by the
smart-upload commit endpoint before the future `mode=replace` flag landed.

USAGE
-----
Dry-run (default) — show what *would* be deleted:

    python manage.py purge_smart_upload_tb --target dupes --keep newest

Commit the delete:

    python manage.py purge_smart_upload_tb --target dupes --keep newest --commit

Other targets:

    --target dupes          : for every (company, entry_date) pair with >1 JE,
                              delete all but one (selected by --keep).
    --target specific       : delete the JEs for the (company, entry_date)
                              pairs supplied via --pair (repeatable).
    --target all            : delete ALL smart_upload_tb JEs. Use with care.

Pair argument format (repeatable):

    --pair ADIC:2025-06-30 --pair ADSA:2024-06-30

--keep newest|oldest|none
    Only meaningful for --target dupes (or --target specific where the pair
    matches >1 JE). Defaults to 'newest' (sort by id desc, keep first).

Safety:
- QuerySet.delete() bypasses JournalEntry.delete()'s POSTED guard by design
  — these JEs are posted, that is the whole reason we're purging them.
- Cascades to JournalEntryLine via FK on_delete=CASCADE.
- Wrapped in a transaction; rolled back on dry-run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count


SOURCE_TYPE = 'smart_upload_tb'


def _parse_pair(s: str) -> tuple[str, date]:
    try:
        code, iso = s.split(':', 1)
    except ValueError:
        raise CommandError(
            f'Bad --pair {s!r}; expected COMPANY:YYYY-MM-DD (e.g. ADIC:2025-06-30).'
        )
    try:
        d = date.fromisoformat(iso.strip())
    except ValueError:
        raise CommandError(f'Bad date in --pair {s!r}; expected YYYY-MM-DD.')
    return code.strip().upper(), d


class Command(BaseCommand):
    help = 'Purge smart_upload_tb JournalEntry rows (CFO directive 2026-05-19).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--target', choices=('dupes', 'specific', 'all'), default='dupes',
            help='Which JEs to purge. Default: dupes.',
        )
        parser.add_argument(
            '--keep', choices=('newest', 'oldest', 'none'), default='newest',
            help='For dupes / specific with multiple matches, which JE to keep. '
                 'Default: newest.',
        )
        parser.add_argument(
            '--pair', action='append', default=[],
            help='Repeatable. Format COMPANY:YYYY-MM-DD. Used with --target specific.',
        )
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually delete. Without this flag the command is a dry-run.',
        )

    def handle(self, *args, **opts):
        from ledger.models import JournalEntry

        target = opts['target']
        keep = opts['keep']
        commit = opts['commit']
        pairs = [_parse_pair(p) for p in opts['pair']]

        qs = JournalEntry.objects.filter(source_type=SOURCE_TYPE).select_related('company')

        # Build candidate JE list per (company_code, entry_date) bucket.
        buckets: dict[tuple[str, date], list[JournalEntry]] = defaultdict(list)
        for je in qs.order_by('id'):
            buckets[(je.company.code, je.entry_date)].append(je)

        if target == 'all':
            scope = list(buckets.items())
        elif target == 'dupes':
            scope = [(k, v) for k, v in buckets.items() if len(v) > 1]
        elif target == 'specific':
            if not pairs:
                raise CommandError('--target specific requires at least one --pair.')
            wanted = set(pairs)
            scope = [(k, v) for k, v in buckets.items() if k in wanted]
            missing = wanted - {k for k, _ in scope}
            if missing:
                self.stdout.write(self.style.WARNING(
                    f'No smart_upload_tb JEs found for: '
                    f'{", ".join(f"{c}:{d}" for c, d in sorted(missing))}'
                ))
        else:
            raise CommandError(f'Unknown --target {target!r}.')

        # Decide which JEs in each bucket to delete.
        to_delete: list[int] = []
        to_keep: list[int] = []
        report_lines: list[str] = []

        for (code, d), jes in sorted(scope):
            jes_sorted = sorted(jes, key=lambda je: je.id)
            if target == 'all' or keep == 'none':
                delete = jes_sorted
                kept = []
            else:
                if len(jes_sorted) == 1:
                    delete = []
                    kept = jes_sorted
                else:
                    if keep == 'newest':
                        kept = [jes_sorted[-1]]
                        delete = jes_sorted[:-1]
                    else:  # oldest
                        kept = [jes_sorted[0]]
                        delete = jes_sorted[1:]
            for je in delete:
                to_delete.append(je.id)
                report_lines.append(
                    f'  DEL  {code:8} {d}  id={str(je.id)}  number={je.entry_number}  lines={je.lines.count()}'
                )
            for je in kept:
                to_keep.append(je.id)
                report_lines.append(
                    f'  KEEP {code:8} {d}  id={str(je.id)}  number={je.entry_number}'
                )

        # Header
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'purge_smart_upload_tb target={target} keep={keep} '
            f'commit={commit} pairs={len(pairs)}'
        ))
        for line in report_lines:
            self.stdout.write(line)

        if not to_delete:
            self.stdout.write(self.style.SUCCESS('Nothing to delete.'))
            return

        # Per-company purge count, for the report Manus asked for.
        per_company: dict[str, int] = defaultdict(int)
        for je in qs.filter(id__in=to_delete).select_related('company'):
            per_company[je.company.code] += 1

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_LABEL('Per-company purge count:'))
        for code, n in sorted(per_company.items()):
            self.stdout.write(f'  {code:8} -> {n}')
        self.stdout.write(f'TOTAL_PURGED={len(to_delete)}')
        self.stdout.write(f'TOTAL_KEPT={len(to_keep)}')

        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — pass --commit to actually delete.'
            ))
            return

        with transaction.atomic():
            deleted, breakdown = JournalEntry.objects.filter(id__in=to_delete).delete()
            self.stdout.write(self.style.SUCCESS(
                f'DELETED {deleted} object(s). breakdown={breakdown}'
            ))
