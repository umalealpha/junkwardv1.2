"""
seed_entity_metadata — load per-entity metadata from a JSON file.

The schema mirrors the ADSA pipeline (entities.json) so the data
department's existing artefact drops in unchanged. Run on demand:

    python manage.py seed_entity_metadata config/entities.json
    python manage.py seed_entity_metadata --dry-run config/entities.json

Each entry MUST carry `code` (matches Company.code). Other keys are
optional:
    regulator       → Company.regulator
    fy_end_month    → Company.fy_end_month (int 1-12)
    framework       → Company.framework
    legal_name      → Company.legal_name
    country         → Company.country  (ISO 2-letter)
    registration    → merged into Company.metadata['registration']
    directors       → Company.metadata['directors']
    banks           → Company.metadata['banks']
    notes           → Company.metadata['notes']
    + anything else → Company.metadata[<key>]
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.entity_metadata_schema import is_key_allowed
from core.models import Company


KNOWN_COLUMN_KEYS = {
    'regulator', 'fy_end_month', 'framework', 'legal_name', 'country',
}


class Command(BaseCommand):
    help = 'Seed per-entity metadata onto core.Company from a JSON file.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Path to entities.json')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the diff and bail without writing.')

    def handle(self, *args, **opts):
        path = Path(opts['path']).expanduser()
        if not path.exists():
            raise CommandError(f'File not found: {path}')

        raw = json.loads(path.read_text())
        entries = raw.get('entities', raw)
        if not isinstance(entries, list):
            raise CommandError('Top-level must be `entities: [...]` or a list.')

        touched = 0
        for entry in entries:
            code = (entry.get('code') or entry.get('id') or '').upper().strip()
            if not code:
                self.stderr.write(f'Skipped: entry missing code → {entry!r}')
                continue
            company = Company.objects.filter(code__iexact=code).first()
            if company is None:
                self.stderr.write(f'Skipped: no Company with code={code}')
                continue

            # Direct column updates ------------------------------------
            changed: dict = {}
            for k in KNOWN_COLUMN_KEYS:
                if k in entry and getattr(company, k, None) != entry[k]:
                    changed[k] = entry[k]
                    setattr(company, k, entry[k])

            # Merge everything else into JSON metadata ------------------
            # CFO directive 2026-05-18: refuse to write jurisdiction-
            # scoped keys (FSP / CIPC / FSCA / SARS-VAT etc.) onto an
            # entity whose country doesn't recognise them. The seed
            # file may carry the ADSA-pipeline shape unchanged; we just
            # skip the SA-only keys when applying it to a BW row.
            meta = dict(company.metadata or {})
            country_now = entry.get('country', company.country)
            for k, v in entry.items():
                if k in KNOWN_COLUMN_KEYS or k in ('id', 'code', 'active', 'is_primary'):
                    continue
                if not is_key_allowed(k, country_now):
                    self.stderr.write(
                        f'  skipped {code}.{k} — not valid for country={country_now}'
                    )
                    continue
                if meta.get(k) != v:
                    meta[k] = v
                    changed.setdefault('metadata', {})[k] = v
            company.metadata = meta

            if changed:
                self.stdout.write(self.style.NOTICE(f'{code}: {sorted(changed.keys())}'))
                if not opts['dry_run']:
                    company.save()
                    touched += 1

        verb = 'would update' if opts['dry_run'] else 'updated'
        self.stdout.write(self.style.SUCCESS(f'{verb} {touched} companies.'))
