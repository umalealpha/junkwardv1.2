"""Pre-deploy guard: fail if the migration graph has conflicts (CFO 2026-08-12).

Two sessions pushing migrations that both fork from the same parent leave the graph
with multiple leaf nodes; `migrate` then refuses and the backend crash-loops. On
2026-08-12 that took the API down for a few minutes. golive.sh runs this BEFORE it
boots any slot, so a forked graph aborts the deploy as a no-op instead of an outage.

Reads migration FILES only (connection=None) — no DB needed, safe to run pre-boot.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.migrations.loader import MigrationLoader


class Command(BaseCommand):
    help = 'Exit non-zero if the migration graph has multiple leaf nodes (a merge conflict).'

    def handle(self, *args, **opts):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        conflicts = loader.detect_conflicts()
        if conflicts:
            lines = [f'  {app}: {", ".join(sorted(leaves))}' for app, leaves in conflicts.items()]
            raise CommandError(
                'Migration graph has multiple leaf nodes — two branches forked it:\n'
                + '\n'.join(lines)
                + '\nFix: python manage.py makemigrations --merge, commit, redeploy.')
        self.stdout.write(self.style.SUCCESS('migration graph OK — one leaf per app'))
