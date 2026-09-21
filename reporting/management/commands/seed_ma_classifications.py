"""
reporting/management/commands/seed_ma_classifications.py

CFO directive 2026-05-20 — apply the Management-Accounts classification
mapping from `reporting/_ma_target_2026_05_20.json` to every matching
`Account.fs_line_item` on prod.

The JSON file is the canonical truth for which MA line each GL code
rolls up to. The P&L side is also encoded in `reporting/ma_pl_spec.py`
(used by `build_ma_pl`). The Balance-Sheet side lives on
`Account.fs_line_item` (used by the BS report grouping).

This command is idempotent: re-runs only update rows whose current
fs_line_item differs from the target.

USAGE
-----
Dry-run (default):
    python manage.py seed_ma_classifications

Commit:
    python manage.py seed_ma_classifications --commit

Options:
    --section pl|bs|both   Filter to only PL or BS rows. Defaults to both.
    --code CODE            Only touch a specific code. Repeatable.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction


SPEC_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / '_ma_target_2026_05_20.json'
)


class Command(BaseCommand):
    help = 'Apply MA classifications (BS Account.fs_line_item) from JSON spec.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--section', choices=('pl', 'bs', 'both'), default='both',
            help='Only touch rows where pl_bs matches. Default: both.',
        )
        parser.add_argument(
            '--code', action='append', default=[],
            help='Limit to a specific GL code. Repeatable.',
        )
        parser.add_argument(
            '--commit', action='store_true',
            help='Apply the updates. Without this flag the command is a dry-run.',
        )

    def handle(self, *args, **opts):
        from ledger.models import Account

        section = opts['section']
        wanted  = set(opts['code'])
        commit  = opts['commit']

        spec = json.loads(SPEC_PATH.read_text())

        updated  = []
        unchanged = 0
        missing  = []

        with transaction.atomic():
            for code in sorted(spec):
                entry = spec[code]
                if wanted and code not in wanted:
                    continue
                if section != 'both' and entry['pl_bs'].lower() != section:
                    continue

                target_label = entry['ma_line']

                # Match the bare code first (ADIC convention). If absent,
                # also try every prefixed variant (RSA_, UNI_, ...).
                accounts = list(Account.objects.filter(code=code))
                accounts += list(Account.objects.filter(code__endswith=f'_{code}'))

                if not accounts:
                    missing.append((code, target_label, entry.get('name', '')))
                    continue

                for acct in accounts:
                    current = (acct.fs_line_item or '').strip()
                    if current == target_label:
                        unchanged += 1
                        continue
                    acct.fs_line_item = target_label
                    if commit:
                        acct.save(update_fields=['fs_line_item'])
                    updated.append((acct.code, current, target_label))

            if not commit:
                transaction.set_rollback(True)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'seed_ma_classifications section={section} commit={commit}'
        ))
        self.stdout.write(f'unchanged: {unchanged}')
        self.stdout.write(f'updated  : {len(updated)}')
        for code, old, new in updated[:80]:
            self.stdout.write(f'  {code:14} {old!r:35} → {new!r}')
        if len(updated) > 80:
            self.stdout.write(f'  ... +{len(updated) - 80} more')
        self.stdout.write(f'missing  : {len(missing)}')
        for code, label, name in missing[:30]:
            self.stdout.write(f'  {code:14} {name[:40]:40} (would be {label!r})')
        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — re-run with --commit to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Applied.'))
