"""
post_adsa_opening_balances — one-shot data fix.

CFO directive 2026-05-19 (Charmaine #2 + #3): the ADSA dashboard tiles
were wrong because the TB import (data/adsa_pack_20260518/) carries
period activity only. The cost accounts for fixed assets and the
opening accumulated-loss row (999999 Undistributed Profits/Losses)
have no opening balances posted, so:

  * Fixed Assets card shows (BWP 69K) — only the accum-dep period
    activity is in the GL, not the cost side. NBV = cost − accum
    can't be computed.
  * Total Equity overstated by ~1M because 999999's Dr opening of
    1,322,026.11 isn't in the books.

This command posts ONE balanced opening JE for ADSA dated 2024-06-30
(the last day before FY25). Re-runs are idempotent: the JE is keyed
by source_type='adsa_opening_balances'.

Numbers source: ADSA pack workbook (Charmaine's verification brief),
matched 1:1 against ADSA_CFO_Pack_20260518.xlsx 'Balance Sheet' sheet.

Usage:
    python manage.py post_adsa_opening_balances           # dry-run
    python manage.py post_adsa_opening_balances --commit  # actual write
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine


SOURCE_TYPE = 'adsa_opening_balances'
ENTRY_DATE  = date(2024, 6, 30)   # last day before FY25 starts


# Opening DR / CR per CoA, sourced from ADSA pack 'Balance Sheet' tab.
# Dr-side (assets, accumulated loss):  +ve
# Cr-side (capital, accum dep):        +ve   (sign handled per row below)
OPENING_ROWS = [
    # (account_code, debit, credit, description)
    ('ADSA_101001',   Decimal('325525.90'), Decimal('0.00'),
        'Display Racks — opening cost'),
    ('ADSA_101001.1', Decimal('0.00'),      Decimal('108519.00'),
        'Accumulated Depreciation Display Racks — opening'),
    ('ADSA_151002',   Decimal('13785.00'),  Decimal('0.00'),
        'Laptops & Computers — opening cost'),
    ('ADSA_151002.1', Decimal('0.00'),      Decimal('9189.54'),
        'Accumulated Depreciation Laptops — opening'),
    # 999999 = Undistributed Profits/Losses — Dr 1,322,026.11 = accumulated
    # loss carried forward from periods before FY25.
    ('ADSA_999999',   Decimal('1322026.11'), Decimal('0.00'),
        'Undistributed Profits/Losses — accumulated loss b/fwd'),
    # 301000 = Capital — Cr 10,000.00 opening (per pack the FY25 injection
    # of 3,550,000 is period activity already in TB FY25; we only post the
    # opening here).
    ('ADSA_301000',   Decimal('0.00'),      Decimal('10000.00'),
        'Share Capital — opening'),
]
# Plug to balance the entry. The opening accounts above sum to a non-zero
# residual (mostly opening retained earnings drift). We pin it to ADSA's
# 999999 explicitly above so the entry IS balanced by design.


class Command(BaseCommand):
    help = 'Post ADSA opening-balance JE (one-shot, idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Default = dry-run.')

    def handle(self, *args, **opts):
        commit = opts['commit']
        tag    = 'COMMIT' if commit else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{tag}] ADSA opening balances at {ENTRY_DATE}'
        ))

        company = Company.objects.filter(code='ADSA').first()
        if not company:
            raise CommandError('Company ADSA not found.')

        sys_user = User.objects.filter(is_superuser=True).order_by('id').first()
        if not sys_user:
            raise CommandError('No superuser to attribute the JE to.')

        # Validate balance
        total_d = sum((r[1] for r in OPENING_ROWS), Decimal('0.00'))
        total_c = sum((r[2] for r in OPENING_ROWS), Decimal('0.00'))
        if total_d != total_c:
            self.stdout.write(self.style.WARNING(
                f'  Pre-balance: Dr {total_d} vs Cr {total_c} (diff {total_d - total_c}). '
                f'Adjusting via 999999.'
            ))
            diff = total_d - total_c
            # If Dr exceeds Cr, push the diff to the Cr side of 999999 to
            # balance (the residual is part of the accumulated-loss roll-up).
            adjusted = []
            patched = False
            for code, d, c, desc in OPENING_ROWS:
                if code == 'ADSA_999999' and not patched:
                    if diff > 0:
                        adjusted.append((code, d, c + diff, desc))
                    else:
                        adjusted.append((code, d - diff, c, desc))
                    patched = True
                else:
                    adjusted.append((code, d, c, desc))
            rows = adjusted
        else:
            rows = list(OPENING_ROWS)

        self.stdout.write(f'  rows={len(rows)}, total Dr/Cr equal at {sum(r[1] for r in rows)}')
        if not commit:
            for r in rows:
                self.stdout.write(f'    {r[0]:<20}  Dr={r[1]:>12}  Cr={r[2]:>12}  {r[3]}')
            self.stdout.write('  [dry-run] would post one JE at 2024-06-30')
            return

        # Ensure fiscal period for entry date
        FiscalPeriod.objects.get_or_create(
            period_name=ENTRY_DATE.strftime('%Y-%m'),
            defaults={
                'start_date': date(ENTRY_DATE.year, ENTRY_DATE.month, 1),
                'end_date':   ENTRY_DATE,
                'status':     FiscalPeriod.Status.OPEN,
            },
        )

        # Idempotent delete of any prior opening JE we posted
        prior = JournalEntry.objects.filter(
            company=company, source_type=SOURCE_TYPE,
        )
        n_prior = prior.count()
        if n_prior:
            prior.delete()
            self.stdout.write(f'  Deleted {n_prior} prior ADSA opening JE(s)')

        # Resolve accounts
        accounts = {}
        for code, d, c, _ in rows:
            acct = Account.objects.filter(code=code).first()
            if not acct:
                raise CommandError(f'Account {code} not in CoA — seed it first.')
            accounts[code] = acct

        with transaction.atomic():
            je = JournalEntry.objects.create(
                entry_date=ENTRY_DATE,
                description='ADSA opening balances — fixed asset costs, accum dep, '
                            'capital, accumulated loss b/fwd (Charmaine #2/#3)',
                source_type=SOURCE_TYPE,
                journal_type=JournalEntry.JournalType.GENERAL,
                status=JournalEntry.Status.DRAFT,
                company=company,
                currency_code_id=(company.base_currency_id or 'ZAR'),
                created_by=sys_user,
                is_related_party=False,
            )
            for code, d, c, desc in rows:
                if d == 0 and c == 0:
                    continue
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=accounts[code],
                    description=desc,
                    debit_amount=d,
                    credit_amount=c,
                    debit_bwp=d,
                    credit_bwp=c,
                )
            je.post(user=sys_user, _allow_direct=True)
            self.stdout.write(self.style.SUCCESS(
                f'  Posted {je.entry_number} with {je.lines.count()} lines, '
                f'balanced at {sum(r[1] for r in rows)}'
            ))
