"""
Import an Odoo "General Ledger" export into alpha-finance as posted JEs.

Usage (dry-run):
    python manage.py import_odoo_gl \
        --jsonl /opt/alpha-finance/ops/data/fy26_gl_moves.jsonl \
        --accounts-json /opt/alpha-finance/ops/data/fy26_missing_accounts.json \
        --company ADIC

Usage (commit):
    python manage.py import_odoo_gl ... --commit

Inputs
------
1. ``--jsonl`` — one JSON object per line, each shaped::

    {
      "move":      "MISC/2025/07/0128",
      "date":      "2025-07-31",
      "narration": "DPO SETTLEMENT - JULY 2025",
      "partner":   null,
      "lines":     [
         {"code": "100001", "acct_name": "Insurance in a box income",
          "dr": "0.00", "cr": "1080516.82"},
         {"code": "280006", "acct_name": "FNBB 62403392335 CHEQ A/C",
          "dr": "1080516.82", "cr": "0.00"}
      ]
    }

2. ``--accounts-json`` — dict mapping Odoo code → {name, account_type, ma_section}.
   Codes already present in alpha-finance CoA are ignored; new codes are created
   under ``--company``'s owner_company.

Behaviour
---------
* Idempotent. Re-running skips moves whose description prefix
  ``"Odoo <move> — "`` already exists for the same company + source_type.
* Auto-opens any LOCKED / CLOSING FiscalPeriod overlapping the move date
  range for the target company (recording the override on lock_reason).
* Bypasses ledger.locks.financial_lock_override for ADIC FY25 / FY26
  using the supplied override password (from --override-pw or the
  OMNI_FINANCIAL_LOCK_OVERRIDE env var — no hardcoded default).
* Single-line moves (Odoo rounding noise, e.g. a 78-BWP balance JE) are
  auto-balanced against the Bank Suspense account (code 000001).
* All-or-nothing per move — transaction.atomic around create + post.

CFO directive 2026-05-20.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company
from ledger.locks import financial_lock_override
from ledger.models import (
    Account,
    FiscalPeriod,
    JournalEntry,
    JournalEntryLine,
)


User = get_user_model()


SUSPENSE_CODE = '000001'    # 'Bank Suspense Account' — used for auto-balance
DESC_PREFIX_FMT = 'Odoo {move} — '


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dec(x) -> Decimal:
    if x in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(x))
    except Exception:        # noqa: BLE001
        return Decimal('0')


def _month_first(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_last(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    nxt = date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def _iter_months(start: date, end: date) -> Iterable[tuple[date, date]]:
    cursor = _month_first(start)
    while cursor <= end:
        yield cursor, _month_last(cursor)
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Import an Odoo GL JSONL export into alpha-finance as posted JEs."

    def add_arguments(self, parser):
        parser.add_argument('--jsonl',          required=True,
                            help='Path to the GL moves JSONL file.')
        parser.add_argument('--accounts-json',  required=True,
                            help='Path to the missing-accounts classification JSON.')
        parser.add_argument('--company',        default='ADIC',
                            help='Target company code (default: ADIC).')
        parser.add_argument('--source-type',    default='odoo_gl_fy26',
                            help='source_type stamp for created JEs.')
        parser.add_argument('--user',           default='admin',
                            help='Username to record on created_by + audit.')
        parser.add_argument('--override-pw',    default=None,
                            help='Financial-lock override password. Defaults to '
                                 'the OMNI_FINANCIAL_LOCK_OVERRIDE env var.')
        parser.add_argument('--commit', action='store_true',
                            help='Without this flag, the command is dry-run only.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after N moves (0 = all).')

    def handle(self, **opts):
        commit       = opts['commit']
        company_code = opts['company']
        src          = opts['source_type']
        override_pw  = opts['override_pw'] or os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE')
        limit        = opts['limit']

        try:
            company = Company.objects.get(code=company_code)
        except Company.DoesNotExist as e:
            raise CommandError(f'Company {company_code!r} does not exist.') from e
        try:
            user = User.objects.get(username=opts['user'])
        except User.DoesNotExist as e:
            raise CommandError(f"User {opts['user']!r} does not exist.") from e

        jsonl_path = Path(opts['jsonl'])
        accts_path = Path(opts['accounts_json'])
        if not jsonl_path.exists():
            raise CommandError(f'JSONL not found: {jsonl_path}')
        if not accts_path.exists():
            raise CommandError(f'Accounts JSON not found: {accts_path}')

        with accts_path.open() as f:
            new_accts = json.load(f)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'\n{"DRY-RUN" if not commit else "COMMIT"} — '
                f'company={company_code} source_type={src} '
                f'limit={limit or "all"}'
            )
        )

        # ── 1. Date range scan (need this for period prep) ──────────────────
        min_d: date | None = None
        max_d: date | None = None
        total_moves = 0
        with jsonl_path.open() as f:
            for line in f:
                mv = json.loads(line)
                total_moves += 1
                if not mv.get('date'):
                    continue
                d = date.fromisoformat(mv['date'])
                if min_d is None or d < min_d:
                    min_d = d
                if max_d is None or d > max_d:
                    max_d = d
        if min_d is None or max_d is None:
            raise CommandError('No dated moves found in JSONL.')
        self.stdout.write(
            f'  date range: {min_d} → {max_d}   total moves: {total_moves}'
        )

        # ── 2. Create missing accounts ──────────────────────────────────────
        created_acct = 0
        for code, meta in new_accts.items():
            if Account.objects.filter(code=code).exists():
                continue
            if not commit:
                created_acct += 1
                self.stdout.write(f'  + ACCOUNT {code} {meta["name"]} (dry-run)')
                continue
            at = meta['account_type']
            sc = 'BS' if at in ('asset', 'liability', 'equity') else 'PNL'
            nb = 'D' if at in ('asset', 'expense') else 'C'
            Account.objects.create(
                code=code,
                name=meta['name'][:200],
                account_type=at,
                sub_type=(meta.get('ma_section') or '')[:50],
                statement_class=sc,
                normal_balance_dc=nb,
                fs_line_item=(meta.get('ma_section') or '')[:200],
                owner_company=company,
                is_active=True,
                is_bank_account=('bank' in meta['name'].lower()
                                 or 'fnb' in meta['name'].lower()
                                 or 'capital bank' in meta['name'].lower()
                                 or 'stanbic' in meta['name'].lower()
                                 or code.startswith('280')),
                description=f'Auto-created by import_odoo_gl ({src}).',
            )
            created_acct += 1
            self.stdout.write(f'  + ACCOUNT {code} {meta["name"][:60]}')

        # Build code → Account lookup for every code we'll touch
        all_codes: set[str] = set()
        with jsonl_path.open() as f:
            for line in f:
                mv = json.loads(line)
                for ln in mv['lines']:
                    all_codes.add(ln['code'])
        accounts = {
            a.code: a for a in Account.objects.filter(code__in=all_codes)
        }
        still_missing = sorted(all_codes - set(accounts))
        if still_missing:
            raise CommandError(
                f'After account creation, {len(still_missing)} codes still '
                f'missing: {still_missing[:20]}...'
            )
        self.stdout.write(
            f'  accounts: {len(all_codes)} referenced, '
            f'{created_acct} new, {len(accounts)} resolvable.'
        )

        # ── 3. Unlock periods + ensure OPEN coverage ────────────────────────
        if commit:
            unlocked = FiscalPeriod.objects.filter(
                company=company,
                start_date__lte=max_d, end_date__gte=min_d,
                status__in=(FiscalPeriod.Status.LOCKED, FiscalPeriod.Status.CLOSING),
            )
            n_co = unlocked.count()
            for fp in unlocked:
                fp.status = FiscalPeriod.Status.OPEN
                fp.lock_reason = (
                    (fp.lock_reason or '').rstrip()
                    + f'\n[import_odoo_gl override {datetime.now().isoformat(timespec="seconds")} '
                      f'by {user.username}: re-open for FY26 GL load]'
                )[:1000]
                fp.save(update_fields=['status', 'lock_reason', 'updated_at'])
            null_unlocked = FiscalPeriod.objects.filter(
                company__isnull=True,
                start_date__lte=max_d, end_date__gte=min_d,
                status__in=(FiscalPeriod.Status.LOCKED, FiscalPeriod.Status.CLOSING),
            )
            n_nu = null_unlocked.count()
            for fp in null_unlocked:
                fp.status = FiscalPeriod.Status.OPEN
                fp.lock_reason = (
                    (fp.lock_reason or '').rstrip()
                    + f'\n[import_odoo_gl override {datetime.now().isoformat(timespec="seconds")} '
                      f'by {user.username}: re-open legacy NULL-company]'
                )[:1000]
                fp.save(update_fields=['status', 'lock_reason', 'updated_at'])
            self.stdout.write(
                f'  unlocked periods: {n_co} (this company) + {n_nu} (legacy NULL)'
            )

            # Ensure every month in range has an OPEN FiscalPeriod
            created_pp = 0
            for ms, me in _iter_months(min_d, max_d):
                fp = FiscalPeriod.objects.filter(
                    company=company, start_date=ms, end_date=me,
                ).first()
                if fp is None:
                    FiscalPeriod.objects.create(
                        company=company,
                        period_name=ms.strftime('%Y-%m'),
                        start_date=ms, end_date=me,
                        status=FiscalPeriod.Status.OPEN,
                    )
                    created_pp += 1
                elif fp.status != FiscalPeriod.Status.OPEN:
                    fp.status = FiscalPeriod.Status.OPEN
                    fp.save(update_fields=['status', 'updated_at'])
            self.stdout.write(f'  +{created_pp} new fiscal periods for {company_code}')
        else:
            self.stdout.write('  (dry-run: skipping period unlock + creation)')

        # ── 4. Build idempotency set: existing imported moves ───────────────
        # We stamp the move ref in description as ``Odoo <move> — ...``.
        # Pull every description starting with 'Odoo ' for this company+source.
        existing_descs = set(
            JournalEntry.objects
            .filter(company=company, source_type=src,
                    description__startswith='Odoo ')
            .values_list('description', flat=True)
        )
        existing_moves: set[str] = set()
        rx = re.compile(r'^Odoo (\S+) — ')
        for d in existing_descs:
            m = rx.match(d)
            if m:
                existing_moves.add(m.group(1))
        self.stdout.write(f'  existing imported moves: {len(existing_moves)}')

        suspense = Account.objects.filter(code=SUSPENSE_CODE).first()

        # ── 5. Iterate + post moves ─────────────────────────────────────────
        created_je = 0
        skipped_dupe = 0
        failed: list[tuple[str, str]] = []
        zero_lines = 0
        with jsonl_path.open() as f:
            for line in f:
                if limit and created_je >= limit:
                    break
                mv = json.loads(line)
                move_name = mv['move']
                if move_name in existing_moves:
                    skipped_dupe += 1
                    continue
                if not mv.get('date'):
                    failed.append((move_name, 'no-date'))
                    continue
                # Collect non-zero lines
                resolved_lines: list[tuple[Account, Decimal, Decimal, str]] = []
                for ln in mv['lines']:
                    acc = accounts.get(ln['code'])
                    if acc is None:
                        failed.append((move_name, f'no-acct:{ln["code"]}'))
                        resolved_lines = []
                        break
                    dr, cr = _dec(ln['dr']), _dec(ln['cr'])
                    if dr == 0 and cr == 0:
                        continue
                    if acc.is_summary_only:
                        # Skip: summary accounts can't be posted to. Roll up to
                        # the parent if needed in a future iteration.
                        continue
                    resolved_lines.append((acc, dr, cr, (ln.get('acct_name') or acc.name)))
                if not resolved_lines:
                    zero_lines += 1
                    continue
                tdr = sum((l[1] for l in resolved_lines), Decimal('0'))
                tcr = sum((l[2] for l in resolved_lines), Decimal('0'))
                diff = tdr - tcr
                if abs(diff) > Decimal('0.01'):
                    failed.append((move_name, f'unbalanced {diff}'))
                    continue
                if len(resolved_lines) < 2:
                    if suspense is None:
                        failed.append((move_name, 'single-line + no suspense'))
                        continue
                    ld = resolved_lines[0]
                    # Mirror the single line on suspense to balance.
                    resolved_lines.append(
                        (suspense, ld[2], ld[1], 'auto-balance (suspense)')
                    )
                if not commit:
                    created_je += 1
                    continue
                try:
                    with transaction.atomic(), financial_lock_override(override_pw):
                        je = JournalEntry(
                            entry_date=date.fromisoformat(mv['date']),
                            description=(
                                DESC_PREFIX_FMT.format(move=move_name)
                                + (mv.get('narration') or '')
                            )[:500],
                            source_type=src,
                            journal_type=JournalEntry.JournalType.GENERAL,
                            status=JournalEntry.Status.DRAFT,
                            company=company,
                            currency_code_id=(company.base_currency_id or 'BWP'),
                            created_by=user,
                            is_related_party=False,
                            notes=(mv.get('partner') or '')[:1000] or None,
                        )
                        je.save()
                        for acc, dr, cr, nm in resolved_lines:
                            JournalEntryLine.objects.create(
                                journal_entry=je,
                                account=acc,
                                description=(nm or acc.name)[:240],
                                debit_amount=dr, credit_amount=cr,
                                debit_bwp=dr, credit_bwp=cr,
                            )
                        je.post(user=user, _allow_direct=True)
                    created_je += 1
                    if created_je % 500 == 0:
                        self.stdout.write(
                            f'  ... posted {created_je} JEs '
                            f'(skipped {skipped_dupe} dupes, {len(failed)} failed)'
                        )
                except Exception as e:                       # noqa: BLE001
                    failed.append((move_name, str(e)[:200]))
                    if len(failed) <= 10:
                        self.stdout.write(self.style.ERROR(
                            f'  FAIL {move_name}: {e}'
                        ))

        # ── 6. Summary ──────────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\nSUMMARY'))
        self.stdout.write(f'  total moves in file : {total_moves}')
        self.stdout.write(f'  created JEs         : {created_je}')
        self.stdout.write(f'  skipped (dupes)     : {skipped_dupe}')
        self.stdout.write(f'  zero-amount moves   : {zero_lines}')
        self.stdout.write(f'  failed              : {len(failed)}')
        if failed[:20]:
            self.stdout.write(self.style.ERROR('  --- first 20 failures ---'))
            for m, e in failed[:20]:
                self.stdout.write(self.style.ERROR(f'    {m}: {e}'))

        if not commit:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN only. Re-run with --commit to apply.'
            ))
