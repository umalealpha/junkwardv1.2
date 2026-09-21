"""
Django management command: migrate_odoo

Pulls historical data from the legacy Odoo at odoo.alphadirect.co.bw into
alpha-finance:
  - Chart of accounts (account.account)
  - Vendor + customer master (res.partner)
  - Journal entries on or before 2026-03-31 (account.move + account.move.line)

Excludes Alpha Direct Insurance Company (Odoo company_id=4) entirely.

Credentials come from env vars only — never from the command line:
  ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD

Default is --dry-run. Use --commit to actually write.
"""

from __future__ import annotations

import json
import sys

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from ops.migrations.odoo.runner import run as run_migration


class Command(BaseCommand):
    help = (
        "Migrate Chart of Accounts, partners, and journal entries from "
        "Odoo into alpha-finance. Default is dry-run; pass --commit to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=True,
            help='Default. Connect to Odoo, count what would be imported, '
                 'but write nothing.',
        )
        parser.add_argument(
            '--commit', action='store_true', default=False,
            help='Actually write rows to the alpha-finance database.',
        )
        parser.add_argument(
            '--models', default='',
            help='Comma-separated subset: accounts,vendors,customers,journal_entries. '
                 'Default: all four, in dependency order.',
        )
        parser.add_argument(
            '--as-user', default='',
            help="Username (Django auth) to attribute the imports to. "
                 "Defaults to the first superuser.",
        )
        parser.add_argument(
            '--probe', action='store_true', default=False,
            help='Connect to Odoo, list companies + resolved ADIC id, exit. '
                 'No import, no DB writes.',
        )

    def handle(self, *args, **options):
        if options['probe']:
            return self._handle_probe()

        dry_run = not options['commit']    # commit wins

        if options['commit']:
            self.stdout.write(self.style.WARNING(
                "⚠  --commit specified. Real database writes are about to happen.\n"
                "    Confirm: have you done a successful --dry-run on the same\n"
                "    data first? Ctrl+C now if not."
            ))

        # Resolve the user we'll stamp on imported journal entries.
        as_user_name = options['as_user']
        if as_user_name:
            try:
                run_user = User.objects.get(username=as_user_name)
            except User.DoesNotExist as exc:
                raise CommandError(f"User '{as_user_name}' not found") from exc
        else:
            run_user = User.objects.filter(is_superuser=True).order_by('id').first()
            if run_user is None:
                raise CommandError(
                    "No superuser found. Pass --as-user <username> explicitly."
                )

        models = [m.strip() for m in options['models'].split(',') if m.strip()] or None

        try:
            summary = run_migration(
                dry_run=dry_run,
                models=models,
                run_user=run_user,
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Migration failed: {exc}"))
            sys.exit(1)

        # Pretty print summary to stdout.
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Odoo migration run {summary['run_id']}"
        ))
        self.stdout.write(
            f"  mode:   {'DRY-RUN' if summary['dry_run'] else 'COMMIT'}\n"
            f"  models: {', '.join(summary['models'])}\n"
            f"  by:     {run_user.username}\n"
        )

        for r in summary['results']:
            line = (
                f"  {r['model']:<35} "
                f"fetched={r['fetched']:>6} "
                f"imported={r['imported']:>6} "
                f"dup={r['skipped_duplicate']:>5} "
                f"adic={r['skipped_adic']:>4} "
                f"post={r['skipped_post_cutoff']:>5} "
                f"failed={r['failed']:>4}"
            )
            self.stdout.write(line)
            for err in r['first_errors']:
                self.stdout.write(self.style.WARNING(f"      ! {err}"))

        totals = summary['totals']
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f"TOTALS  fetched={totals['fetched']}  "
            f"imported={totals['imported']}  "
            f"skipped={totals['skipped']}  "
            f"failed={totals['failed']}"
        ))
        if summary['dry_run']:
            self.stdout.write(self.style.NOTICE(
                "Dry run — no rows written. Re-run with --commit to apply."
            ))

    # ------------------------------------------------------------------
    # Probe mode
    # ------------------------------------------------------------------

    def _handle_probe(self):
        from ops.migrations.odoo.client import OdooClient
        from ops.migrations.odoo.mapping import resolve_adic_id

        self.stdout.write(self.style.MIGRATE_HEADING("Probe — Odoo connectivity + companies"))
        try:
            c = OdooClient()
            uid = c.authenticate()
        except Exception as exc:
            raise CommandError(f"Odoo connection failed: {exc}") from exc

        self.stdout.write(f"  uid: {uid}\n  url: {c.url}\n  db:  {c.db}\n")

        cos = c._models.execute_kw(
            c.db, c._uid, c._password,
            'res.company', 'search_read', [[]],
            {'fields': ['id', 'name', 'currency_id'], 'limit': 100, 'order': 'id asc'},
        )
        self.stdout.write("\nres.company:")
        for r in cos:
            curr = r['currency_id'][1] if r.get('currency_id') else '-'
            self.stdout.write(f"  id={r['id']:>3}  {r['name']:<45}  {curr}")

        adic_id = resolve_adic_id(c)
        self.stdout.write('')
        if adic_id is None:
            self.stdout.write(self.style.WARNING(
                "ADIC NOT FOUND in res.company by name. Update ADIC_NAME_PATTERNS in mapping.py."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f"ADIC resolved to Odoo company_id={adic_id}"))
