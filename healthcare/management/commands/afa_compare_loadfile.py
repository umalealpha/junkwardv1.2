"""Phase 6 — prove the automated file matches the hand-built one.

    python manage.py afa_compare_loadfile --against /path/to/accepted.csv

Compares what the automation produces against the last file AFA ACTUALLY
ACCEPTED — not merely the last one sent. A rejected file is a poisoned
baseline, and matching it would prove the wrong thing.

Exit codes: 0 identical membership · 1 differences found · 2 could not run.

The comparison is on the KEY FIELDS AFA act on — policy number, dependant
number, names, ID, plan, dates — not on byte order, because a different row
order is not a defect. Differences are reported by policy number, never by
printing whole rows: this file is full of member data and the output of this
command ends up in terminals and logs.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from healthcare import afa_loadfile as engine
from healthcare import afa_service

# The fields a mismatch actually matters on.
_KEY_FIELDS = (
    'ADH Policy Number', 'Dependant no', 'Group Name', 'Registration Date',
    'First Name', 'Surname', 'Date of Birth', 'IDNumber', 'Passport', 'Gender',
    'Option', 'Resignation date', 'Resignation reason', 'Suspension',
)


def _read_reference(path: Path) -> dict:
    """Read the accepted file. Accepts pipe or comma — AFA's own template
    ships comma-separated even though submissions must be pipe-delimited."""
    raw = path.read_text(encoding='utf-8-sig').splitlines()
    if not raw:
        raise CommandError(f'{path} is empty.')
    delim = '|' if raw[0].count('|') >= 34 else ','
    rows = list(csv.reader(raw, delimiter=delim))
    header = [h.strip() for h in rows[0]]
    out = {}
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        rec = dict(zip(header, row))
        key = (rec.get('ADH Policy Number', '').strip(),
               (rec.get('Dependant no', '') or '0').strip())
        out[key] = rec
    return out


def _index_built(body: str) -> dict:
    lines = body.strip().split('\n')
    out = {}
    for line in lines[1:]:
        parts = line.split(engine.DELIMITER)
        if len(parts) != len(engine.COLUMNS):
            continue
        rec = dict(zip(engine.COLUMNS, parts))
        key = (rec['ADH Policy Number'].strip(), (rec['Dependant no'] or '0').strip())
        out[key] = rec
    return out


class Command(BaseCommand):
    help = "Compare the automated AFA load file against the last file AFA accepted."

    def add_arguments(self, parser):
        parser.add_argument('--against', required=True,
                            help='Path to the last load file AFA ACCEPTED.')
        parser.add_argument('--show', type=int, default=25,
                            help='How many differing policy numbers to list.')

    def handle(self, *args, **opts):
        path = Path(opts['against']).expanduser()
        if not path.exists():
            raise CommandError(f'{path} does not exist.')

        reference = _read_reference(path)

        try:
            outcome, meta = afa_service.build()
        except engine.LoadFileAborted as exc:
            self.stderr.write(self.style.ERROR(f'Could not build: {exc}'))
            sys.exit(2)

        built = _index_built(engine.render_file(outcome.rows))

        only_built = sorted(set(built) - set(reference))
        only_ref   = sorted(set(reference) - set(built))
        differing  = []
        for key in sorted(set(built) & set(reference)):
            b, r = built[key], reference[key]
            bad = [f for f in _KEY_FIELDS
                   if (b.get(f, '') or '').strip() != (r.get(f, '') or '').strip()]
            if bad:
                differing.append((key, bad))

        self.stdout.write(f'reference : {len(reference)} lives  ({path.name})')
        self.stdout.write(f'automated : {len(built)} lives')
        self.stdout.write(f'  only in the automated file : {len(only_built)}')
        self.stdout.write(f'  only in the accepted file  : {len(only_ref)}')
        self.stdout.write(f'  present in both but differing: {len(differing)}')
        if outcome.held:
            self.stdout.write(f'  held back by validation      : {len(outcome.held)}')

        show = opts['show']
        for key in only_built[:show]:
            self.stdout.write(f'    + {key[0]}/{key[1]} not in the accepted file')
        for key in only_ref[:show]:
            self.stdout.write(f'    - {key[0]}/{key[1]} missing from the automated file')
        for key, fields in differing[:show]:
            # field NAMES only — never the values, which are member data.
            self.stdout.write(f'    ~ {key[0]}/{key[1]} differs on: {", ".join(fields)}')

        if only_built or only_ref or differing:
            self.stdout.write(self.style.WARNING(
                'DIFFERENT — do not send until each difference is explained.'))
            sys.exit(1)

        self.stdout.write(self.style.SUCCESS(
            'IDENTICAL on every field AFA act on.'))
