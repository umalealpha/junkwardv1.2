"""Give the legacy dialogues a competency structure to score.

35 of the 42 current dialogues came from the nine-box seed and carry no
competency sections, so a manager opens one and there is nothing to fill in.
This fills in the blank Alpha Direct framework on those records only.

It MERGES — a record can hold a development plan, career aspirations or value
scores with no competency sections, and replacing `dd` would wipe them across 35
live rows with no backup. It never touches a record that already has sections,
never an odd legacy shape, and never a locked (signed-off) period.

Dry-run by default — pass --apply to write.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from hris.dd_template import needs_template, seed_into
from hris.models import DevelopmentDialogue


class Command(BaseCommand):
    help = 'Fill the blank competency template into current dialogues that have none.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes (default is a dry run).')

    def handle(self, *args, **opts):
        apply_it = opts['apply']
        rows = list(DevelopmentDialogue.objects.filter(is_current=True))
        touched = skipped_locked = 0

        with transaction.atomic():
            for row in rows:
                payload = dict(row.payload or {})
                if not needs_template(payload.get('dd')):
                    continue
                if row.locked:
                    skipped_locked += 1
                    continue
                touched += 1
                self.stdout.write(f'  {"filling" if apply_it else "would fill"}: '
                                  f'{row.name or row.ref}')
                if apply_it:
                    payload['dd'] = seed_into(payload.get('dd'))
                    row.payload = payload
                    row.save(update_fields=['payload'])

        self.stdout.write(self.style.SUCCESS(
            f'{touched} dialogue(s) {"filled" if apply_it else "would be filled"}; '
            f'{skipped_locked} locked and left alone; '
            f'{len(rows)} current in total.'))
        if not apply_it:
            self.stdout.write('Dry run — re-run with --apply to write.')
