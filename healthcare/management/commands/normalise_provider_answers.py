"""Put the provider yes/no columns into one spelling.

The ADH sheet answered four ways where it meant two — on 21-Sep-2026 the
sticker column held NO (257), Yes (17), YES (10), No (7) — so any count of
"YES" saw 10 of 27. The importer now canonicalises on the way in; this fixes
the rows already stored.

Touches ONLY the four yes/no columns, and only where the canonical form
differs. An answer that is not yes/no/na is left exactly as it is.

Dry-run by default — pass --apply to write.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from healthcare.models import ServiceProvider
from healthcare.provider_registry import _YESNO_FIELDS, canon_yesno


class Command(BaseCommand):
    help = 'Canonicalise the provider yes/no columns to one spelling.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes (default is a dry run).')

    def handle(self, *args, **opts):
        apply_it = opts['apply']
        touched = 0
        by_field: dict[str, int] = {}

        with transaction.atomic():
            for p in ServiceProvider.objects.all():
                changes = {}
                for f in _YESNO_FIELDS:
                    old = getattr(p, f) or ''
                    new = canon_yesno(old)
                    if new != old:
                        changes[f] = (old, new)
                if not changes:
                    continue
                touched += 1
                for f, (old, new) in changes.items():
                    by_field[f] = by_field.get(f, 0) + 1
                    setattr(p, f, new)
                self.stdout.write(
                    f'  {"fixing" if apply_it else "would fix"}: {p.practice_number} '
                    + ', '.join(f'{f} {old!r}->{new!r}' for f, (old, new) in changes.items()))
                if apply_it:
                    p.save(update_fields=list(changes))

        self.stdout.write(self.style.SUCCESS(
            f'{touched} provider(s) {"fixed" if apply_it else "would be fixed"}'
            + (f' — {by_field}' if by_field else '')))
        if not apply_it:
            self.stdout.write('Dry run — re-run with --apply to write.')
