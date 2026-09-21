"""
core/management/commands/check_migration_safety.py

Decide whether the PENDING migrations are safe to apply while the OLD code is
still serving traffic — i.e. whether a zero-downtime (blue/green) deploy is
allowed, or whether this release needs the brief-restart path instead.

Why this matters: in a blue/green deploy the new container runs migrations while
the old container is still answering requests. For a few seconds BOTH versions of
the code talk to the SAME database. That is completely safe for additive changes
(new table, new nullable column, new index) and NOT safe for destructive ones —
drop a column and the old code 500s on its very next query for it.

So this is the gate. Exit codes:
    0  safe   — every pending migration is additive; blue/green is fine
    1  unsafe — at least one destructive operation; use the restart path
    2  error  — could not determine; treated as unsafe by the caller

It inspects the real SQL Django would run (`sqlmigrate`), not the operation
classes, because that is what actually hits the database.

    python manage.py check_migration_safety
    python manage.py check_migration_safety --json
"""
from __future__ import annotations

import io
import json
import re

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor

# Patterns that break an older container still serving requests.
DESTRUCTIVE = [
    (re.compile(r'\bDROP\s+TABLE\b', re.I),            'drops a table'),
    (re.compile(r'\bDROP\s+COLUMN\b', re.I),           'drops a column'),
    (re.compile(r'\bDROP\s+CONSTRAINT\b.*NOT\s+NULL', re.I), 'drops a NOT NULL constraint'),
    (re.compile(r'\bRENAME\s+COLUMN\b', re.I),         'renames a column'),
    (re.compile(r'\bRENAME\s+TO\b', re.I),             'renames a table'),
    (re.compile(r'\bALTER\s+COLUMN\b.*\bTYPE\b', re.I), 'changes a column type'),
    (re.compile(r'\bSET\s+NOT\s+NULL\b', re.I),        'makes an existing column NOT NULL'),
]


class Command(BaseCommand):
    help = 'Are the pending migrations safe to apply while the old code still serves?'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Machine-readable output.')
        parser.add_argument('--database', default=DEFAULT_DB_ALIAS)

    def handle(self, *args, **opts):
        conn = connections[opts['database']]
        executor = MigrationExecutor(conn)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)

        pending = [f'{m.app_label}.{m.name}' for m, _backwards in plan]
        findings: list[dict] = []

        for migration, _backwards in plan:
            buf = io.StringIO()
            try:
                call_command('sqlmigrate', migration.app_label, migration.name,
                             stdout=buf, database=opts['database'])
            except Exception as exc:                       # noqa: BLE001
                # Cannot read the SQL → cannot claim it is safe.
                findings.append({
                    'migration': f'{migration.app_label}.{migration.name}',
                    'reason': f'could not generate SQL ({exc})',
                    'severity': 'unknown',
                })
                continue
            sql = buf.getvalue()
            for pattern, why in DESTRUCTIVE:
                for line in sql.splitlines():
                    if pattern.search(line):
                        findings.append({
                            'migration': f'{migration.app_label}.{migration.name}',
                            'reason': why,
                            'sql': line.strip()[:160],
                            'severity': 'destructive',
                        })
                        break

        safe = not findings
        result = {
            'safe_for_zero_downtime': safe,
            'pending_count': len(pending),
            'pending': pending,
            'findings': findings,
        }

        if opts['json']:
            self.stdout.write(json.dumps(result, indent=2))
        else:
            if not pending:
                self.stdout.write('No pending migrations — blue/green is safe.')
            else:
                self.stdout.write(f'Pending migrations ({len(pending)}): {", ".join(pending)}')
                if safe:
                    self.stdout.write(self.style.SUCCESS(
                        'All additive. Safe to run while the old version still serves.'))
                else:
                    self.stdout.write(self.style.ERROR(
                        'NOT safe for a zero-downtime deploy — the old container would break '
                        'the moment this lands:'))
                    for f in findings:
                        self.stdout.write(f'   {f["migration"]}: {f["reason"]}')
                        if f.get('sql'):
                            self.stdout.write(f'      {f["sql"]}')
                    self.stdout.write(
                        'Use the brief-restart deploy for this release, or split the change '
                        'into two: ship the additive half now, drop the old column next time.')

        raise SystemExit(0 if safe else 1)
