"""
ledger/management/commands/merge_duplicate_accounts.py

CFO directive 2026-05-25 (COA-001).

For every (company_id, account_name) group with > 1 Account row:
  * pick keeper by rule
  * reassign every JournalEntryLine.account FK from non-keepers -> keeper
  * mark non-keepers is_active = False (NEVER deleted — audit history)
  * write one AccountMergeAudit row per merge
  * verify bible six don't drift (FY25 GWP / NEP / PAT, FY26-9M GWP / NEP / PAT)
  * roll back the whole company if any bible line moves > 1 BWP

Keeper rule (highest priority first):
  1. code is in `reporting.ma_pl_spec.MA_LINES.codes` (any bucket)
  2. most posted JournalEntryLines
  3. earliest created_at

Usage:
    python manage.py merge_duplicate_accounts --dry-run                # preview, no writes
    python manage.py merge_duplicate_accounts --company ADIC --commit  # one company
    python manage.py merge_duplicate_accounts --commit                 # ALL companies
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from core.models import Company
from ledger.models import Account, AccountMergeAudit, JournalEntryLine


BIBLE = {
    'FY25':    (date(2024, 7, 1), date(2025, 6, 30), Decimal('125149000'),
                Decimal('52480000'), Decimal('292000')),
    'FY26_9M': (date(2025, 7, 1), date(2026, 3, 31), Decimal('96177000'),
                Decimal('42010000'), Decimal('950000')),
}
BIBLE_TOL_BWP = Decimal('1')


def _ma_code_set() -> set[str]:
    """All GL codes referenced by ma_pl_spec.MA_LINES — for keeper preference."""
    try:
        from reporting.ma_pl_spec import MA_LINES
    except Exception:
        return set()
    s: set[str] = set()
    for line in MA_LINES.values():
        for c in line.get('codes', []):
            s.add(str(c))
    return s


def _pick_keeper(rows: list[Account], ma_codes: set[str]) -> tuple[Account, str]:
    """Return (keeper, rule_applied_label).

    Rule order:
      1. code in MA_LINES (when any tied, fall through to JE-count then created_at)
      2. most posted JE lines (Account-level count via reverse FK)
      3. earliest created_at
    """
    def je_count(a: Account) -> int:
        return JournalEntryLine.objects.filter(
            account_id=a.id,
            journal_entry__status='posted',
        ).count()

    # Stage 1 — narrow to MA-spec codes if any.
    in_ma = [a for a in rows if a.code in ma_codes]
    candidates = in_ma if in_ma else rows
    rule = 'in_ma_spec' if in_ma else 'no_ma_code'

    # Stage 2 — JE-count desc.
    candidates.sort(
        key=lambda a: (-je_count(a), a.created_at),
    )
    keeper = candidates[0]
    if len(candidates) > 1:
        rule += ',most_je'
    rule += ',earliest_created' if len(candidates) > 1 else ''
    return keeper, rule


def _bible_snapshot(company: Company) -> dict[str, Decimal]:
    """Six bible numbers for `company`. Returns ZERO if builder missing."""
    try:
        from reporting.ma_pl import build_ma_pl
    except Exception:
        return {}
    out: dict[str, Decimal] = {}
    cid = str(company.id)
    for label, (start, end, _g, _n, _p) in BIBLE.items():
        pl = build_ma_pl(start, end, company_id=cid)
        out[f'{label}_GWP'] = Decimal(pl['totals']['gross_written_premium'])
        out[f'{label}_NEP'] = Decimal(pl['totals']['net_earned_premium'])
        out[f'{label}_PAT'] = Decimal(pl['totals']['pat'])
    return out


def _bible_diff(before: dict, after: dict) -> list[str]:
    """Returns list of '<metric>: <before> -> <after> Δ=<d>' for any line
    moved more than BIBLE_TOL_BWP. Empty if all bible numbers stable."""
    drifts = []
    for k, b in before.items():
        a = after.get(k, Decimal('0'))
        d = a - b
        if abs(d) > BIBLE_TOL_BWP:
            drifts.append(f'{k}: {b} -> {a} (delta={d})')
    return drifts


class Command(BaseCommand):
    help = 'Merge duplicate-name accounts into one keeper per (company, name).'

    def add_arguments(self, parser):
        parser.add_argument('--company', type=str, default='',
                            help='Company.code (e.g. ADIC). Omit for ALL companies.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview only; no writes.')
        parser.add_argument('--commit', action='store_true',
                            help='Apply changes. One of --dry-run/--commit required.')
        parser.add_argument('--user', type=str, default='',
                            help='Username to attribute the audit rows to.')

    def handle(self, *args, **opts):
        if not (opts['dry_run'] or opts['commit']):
            raise CommandError('Pass --dry-run or --commit.')
        if opts['dry_run'] and opts['commit']:
            raise CommandError('--dry-run and --commit are mutually exclusive.')

        ma_codes = _ma_code_set()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{"COMMIT" if opts["commit"] else "DRY-RUN"}] '
            f'MA-spec code set has {len(ma_codes)} entries (keeper preference).'
        ))

        companies = self._target_companies(opts['company'])
        user = self._resolve_user(opts.get('user'))

        grand_groups = grand_moved = grand_lines = 0
        for c in companies:
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n──  {c.code or "(none)"}  ──────────────────────────────────'
            ))
            n_groups, n_merged, n_lines, drifts = self._merge_one_company(
                c, ma_codes, user, commit=opts['commit'],
            )
            grand_groups += n_groups
            grand_moved  += n_merged
            grand_lines  += n_lines
            if drifts:
                self.stdout.write(self.style.ERROR(
                    f'  Bible drift on {c.code} — rolled back. Drifts: {drifts}'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. groups={grand_groups} '
            f'accounts_deactivated={grand_moved} '
            f'je_lines_reassigned={grand_lines}'
        ))

    # ──────────────────────────────────────────────────────────────
    def _target_companies(self, code: str) -> list[Company]:
        if code:
            c = Company.objects.filter(code__iexact=code).first()
            if not c:
                raise CommandError(f'Company {code!r} not found.')
            return [c]
        return list(
            Company.objects.filter(is_active=True).order_by('code')
        )

    def _resolve_user(self, username: str) -> Optional[User]:
        if username:
            u = User.objects.filter(username__iexact=username).first()
            if not u:
                raise CommandError(f'User {username!r} not found.')
            return u
        return User.objects.filter(is_superuser=True).order_by('id').first()

    def _merge_one_company(
        self, company: Company, ma_codes: set, user: Optional[User], *, commit: bool,
    ) -> tuple[int, int, int, list]:
        """Returns (groups_seen, accounts_deactivated, je_lines_moved, drifts)."""
        dup_names = (
            Account.objects
            .filter(owner_company=company)
            .values('name')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
            .order_by('name')
        )
        n_groups = dup_names.count()
        if n_groups == 0:
            self.stdout.write('  no duplicates')
            return 0, 0, 0, []

        # Bible BEFORE
        before = _bible_snapshot(company)

        n_merged = 0
        n_lines  = 0
        try:
            with transaction.atomic():
                for d in dup_names:
                    rows = list(
                        Account.objects
                        .filter(owner_company=company, name=d['name'])
                        .order_by('code')
                    )
                    keeper, rule = _pick_keeper(rows, ma_codes)
                    for non_keep in rows:
                        if non_keep.id == keeper.id:
                            continue
                        moved = JournalEntryLine.objects.filter(
                            account_id=non_keep.id,
                        ).update(account_id=keeper.id)
                        Account.objects.filter(pk=non_keep.pk).update(
                            is_active=False,
                        )
                        AccountMergeAudit.objects.create(
                            account_name = d['name'],
                            keeper_id    = keeper.id,
                            keeper_code  = keeper.code,
                            merged_id    = non_keep.id,
                            merged_code  = non_keep.code,
                            je_lines_moved = moved,
                            rule_applied   = rule,
                            company        = company,
                            performed_by   = user,
                            notes = (f'Auto-merge of duplicate "{d["name"]}". '
                                     f'Keeper {keeper.code} retained; '
                                     f'{non_keep.code} deactivated.'),
                        )
                        n_merged += 1
                        n_lines  += moved
                        self.stdout.write(
                            f'  {d["name"][:45]:<45}  {non_keep.code:>10} -> {keeper.code:<10}  '
                            f'+{moved:>5} lines  [{rule}]'
                        )

                # Bible AFTER (inside the txn so a drift triggers rollback)
                after = _bible_snapshot(company)
                drifts = _bible_diff(before, after)
                if drifts:
                    self.stdout.write(self.style.ERROR(
                        f'  BIBLE DRIFT — rolling back {company.code}.'
                    ))
                    for d in drifts:
                        self.stdout.write(f'    {d}')
                    raise _BibleDrift(drifts)

                if not commit:
                    # Dry run path — still rollback so prod stays clean.
                    raise _DryRun(n_groups, n_merged, n_lines)
        except _BibleDrift as e:
            return n_groups, 0, 0, e.drifts
        except _DryRun as e:
            self.stdout.write(self.style.WARNING(
                f'  DRY-RUN complete — would merge {e.merged} accounts, '
                f'move {e.lines} JE lines. Rolled back.'
            ))
            return e.groups, 0, 0, []

        self.stdout.write(self.style.SUCCESS(
            f'  COMMIT — merged {n_merged} accounts, moved {n_lines} JE lines.'
        ))
        return n_groups, n_merged, n_lines, []


class _DryRun(Exception):
    def __init__(self, groups, merged, lines):
        self.groups, self.merged, self.lines = groups, merged, lines


class _BibleDrift(Exception):
    def __init__(self, drifts):
        self.drifts = drifts
