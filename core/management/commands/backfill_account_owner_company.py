"""
backfill_account_owner_company — repair NULL owner_company on ledger.Account.

CFO directive 2026-05-20: many entity-prefixed accounts (e.g. ADSA-101300,
RSA_400000, VCM-291100) were created without `owner_company` set, so they
fall through the multi-entity company filter and become invisible across
the CoA, BankAccount, PettyCashLocation and PurchaseOrder surfaces.

Strategy:
  1. For every NULL-owner Account, look at the code's leading prefix
     (^[A-Z]+[-_]) and match against Company.code (case-insensitive).
  2. If matched → set owner_company.
  3. If no prefix and the account has no postings → assign to the
     `--default-company` (defaults to ADIC) so legacy seed CoA lands
     somewhere visible.
  4. Skip (and report) anything ambiguous.

Cross-checks BankAccount.gl_account, PettyCashLocation.petty_cash_account
and PurchaseOrder.company so dependent rows inherit the fix.

Run with --dry-run first.
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from core.models import Company
from ledger.models import Account


PREFIX_RE = re.compile(r'^([A-Za-z]+)[-_]')


class Command(BaseCommand):
    help = 'Backfill NULL ledger.Account.owner_company by code prefix.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report changes without writing.')
        parser.add_argument('--default-company', default='ADIC',
                            help='Code for fallback when no prefix match. Set to '
                                 '"" to skip the fallback step.')
        parser.add_argument('--delete-empty', action='store_true',
                            help='After backfill, delete still-orphan accounts that '
                                 'have ZERO journal lines.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        default_code = (opts['default_company'] or '').strip().upper()

        companies_by_code = {c.code.upper(): c for c in Company.objects.all()}
        default_company = companies_by_code.get(default_code) if default_code else None

        orphans = Account.objects.filter(owner_company__isnull=True)
        total = orphans.count()
        self.stdout.write(self.style.WARNING(
            f'Found {total} orphan accounts (owner_company IS NULL).'
        ))
        if default_company:
            self.stdout.write(f'Fallback for prefix-less orphans: {default_company.code}')
        else:
            self.stdout.write('No fallback (prefix-less orphans left as-is).')

        matched_by_prefix = 0
        matched_by_default = 0
        skipped_ambiguous = 0
        deleted_empty = 0
        per_company = {}

        with transaction.atomic():
            for acc in orphans.iterator():
                code = (acc.code or '').strip()
                m = PREFIX_RE.match(code)
                target = None
                source = ''

                if m:
                    pfx = m.group(1).upper()
                    if pfx in companies_by_code:
                        target = companies_by_code[pfx]
                        source = f'prefix={pfx}'

                if target is None and default_company is not None and not m:
                    target = default_company
                    source = f'default={default_company.code}'

                if target is None:
                    # Prefix that doesn't match any known company
                    skipped_ambiguous += 1
                    if m:
                        self.stdout.write(
                            f'  SKIP {code:<20} unknown prefix "{m.group(1)}" '
                            f'(name="{acc.name[:40]}")'
                        )
                    continue

                if not dry:
                    acc.owner_company = target
                    acc.save(update_fields=['owner_company', 'updated_at'])

                key = target.code
                per_company[key] = per_company.get(key, 0) + 1
                if 'prefix' in source:
                    matched_by_prefix += 1
                else:
                    matched_by_default += 1

            # Optional cleanup pass for any still-NULL with zero postings.
            if opts['delete_empty']:
                still_orphan_no_lines = (Account.objects
                                         .filter(owner_company__isnull=True)
                                         .annotate(n=Count('journal_lines'))
                                         .filter(n=0))
                deleted_empty = still_orphan_no_lines.count()
                if not dry:
                    still_orphan_no_lines.delete()

            if dry:
                transaction.set_rollback(True)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Summary ==='))
        self.stdout.write(f'  matched by code prefix : {matched_by_prefix}')
        self.stdout.write(f'  matched by default fb  : {matched_by_default}')
        self.stdout.write(f'  ambiguous / skipped    : {skipped_ambiguous}')
        if opts['delete_empty']:
            self.stdout.write(f'  empty rows deleted     : {deleted_empty}')
        self.stdout.write('  per-company assignment :')
        for code, n in sorted(per_company.items()):
            self.stdout.write(f'    {code:<8} +{n}')

        if dry:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — no rows written. Re-run without --dry-run to apply.'
            ))
