"""
seed_jobs — load the job descriptions and the protected/switchable split.

Reads the JSON the off-line build wrote (one file per job, from DeepSeek+Gemini,
machine-checked for plain English and a valid protected flag), and upserts a
ScheduledJob per entry. `is_protected` is taken from CODE (jobs.protected),
never from the JSON, so the file cannot unlock a backup. Jobs in NOT_A_SWITCH
are skipped entirely.

  manage.py seed_jobs --dir /tmp/cronjobs
"""
from __future__ import annotations

import json
import pathlib

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Seed / refresh the scheduled-job descriptions.'

    def add_arguments(self, parser):
        parser.add_argument('--dir', required=True)

    def handle(self, *a, **o):
        from jobs.models import ScheduledJob
        from jobs.protected import NOT_A_SWITCH, is_protected

        d = pathlib.Path(o['dir'])
        made = updated = skipped = 0
        for f in sorted(d.glob('*.json')):
            try:
                r = json.loads(f.read_text())
            except Exception as e:      # noqa: BLE001
                self.stderr.write(f'  bad json {f.name}: {e}')
                continue
            name = (r.get('name') or f.stem).strip()
            if name in NOT_A_SWITCH:
                skipped += 1
                continue
            obj, created = ScheduledJob.objects.update_or_create(
                name=name,
                defaults=dict(
                    what_it_does=(r.get('what_it_does') or '')[:2000],
                    who_it_affects=(r.get('who_it_affects') or '')[:200],
                    if_switched_off=(r.get('if_switched_off') or '')[:2000],
                    category=r.get('category') or 'sends',
                    is_protected=is_protected(name),   # CODE, not the file
                ))
            made += created
            updated += (0 if created else 1)
        self.stdout.write(self.style.SUCCESS(
            f'seeded {made} new, updated {updated}, skipped {skipped} '
            f'(one-offs / OS jobs)'))
