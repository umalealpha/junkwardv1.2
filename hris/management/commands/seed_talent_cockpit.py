"""Seed the Development Dialogue "Talent Cockpit" with the finance team's
existing dialogues (migrated from the CFO's Excel files, 2026-07-17).

Idempotent and NON-destructive: creates a row only if that person's `ref`
is absent, so re-running never overwrites edits HR/CFO have since made in
omni. Pass --force to overwrite existing rows from the seed file.

    python manage.py seed_talent_cockpit
    python manage.py seed_talent_cockpit --force
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from hris.models import DevelopmentDialogue

SEED_PATH = Path(__file__).resolve().parents[2] / 'data' / 'talent_cockpit_seed.json'


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = "Seed Development Dialogue records from the finance-team Excel migration."

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Overwrite existing rows from the seed file.')

    def handle(self, *args, **opts):
        if not SEED_PATH.exists():
            self.stderr.write(f'Seed file not found: {SEED_PATH}')
            return
        people = json.loads(SEED_PATH.read_text(encoding='utf-8'))
        created = updated = skipped = 0
        for p in people:
            ref = str(p.get('id'))
            if not ref or ref == 'None':
                continue
            row = DevelopmentDialogue.objects.filter(ref=ref).first()
            if row and not opts['force']:
                skipped += 1
                continue
            row = row or DevelopmentDialogue(ref=ref)
            row.name       = str(p.get('name', '') or '')[:200]
            row.department = str(p.get('dept', '') or '')[:200]
            row.position   = str(p.get('position', '') or '')[:200]
            row.period     = str(p.get('period', '') or '')[:120]
            row.supervisor = str(p.get('supervisor', '') or '')[:200]
            row.color      = str(p.get('color', '') or '')[:9]
            row.performance = _num(p.get('performance')) or 0.5
            row.potential   = _num(p.get('potential')) or 0.5
            row.overall     = _num(p.get('overall'))
            row.rating      = str(p.get('rating', '') or '')[:120]
            row.payload     = p
            existed = bool(row.pk)
            row.save()
            updated += 1 if existed else 0
            created += 0 if existed else 1
        self.stdout.write(self.style.SUCCESS(
            f'Talent Cockpit seed: {created} created, {updated} updated, {skipped} left untouched.'))
