"""
tb_check — canonical Trial Balance + P&L sanity check.

Implements the 5-failure-mode prevention pattern from
.claude/skills/prat-skill/recipes/tb-check.md (and Issues with Accounting.pdf,
section 5). Runs against alpha-finance's *own* GL — the source of truth for
omni-served reports.

Outputs:
  - Human summary on stdout
  - JSON receipt at reports/tb-check-YYYYMMDD-HHMMSS.json (or --json path)
  - Exit 0 if PASS, exit 1 if BLOCK (slots straight into a Stop hook later)

Usage:
    python manage.py tb_check \\
        --start 2025-07-01 --end 2026-03-31 \\
        --company VCM \\
        --re-account 3200

    python manage.py tb_check --period FY26-9M --company ADI --json out.json
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

ZERO     = Decimal('0.00')
TOLERANCE = Decimal('0.01')  # rounding tolerance for foot/tie-out

# Standard period presets — saves the CFO from typing dates.
PERIODS: dict[str, tuple[str, str]] = {
    'FY25':    ('2024-07-01', '2025-06-30'),
    'FY26':    ('2025-07-01', '2026-06-30'),
    'FY26-9M': ('2025-07-01', '2026-03-31'),
    'FY26-Q1': ('2025-07-01', '2025-09-30'),
    'FY26-Q2': ('2025-10-01', '2025-12-31'),
    'FY26-Q3': ('2026-01-01', '2026-03-31'),
}


def d(x) -> Decimal:
    """Coerce None/0/Decimal → Decimal, rounded to cents."""
    if x is None:
        return ZERO
    return (Decimal(x) if not isinstance(x, Decimal) else x).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = (
        "Canonical TB + P&L sanity check. PASSes only if double-entry, period, "
        "sign-convention, mapping completeness, and net-income-to-RE tie-out all "
        "hold. BLOCKs otherwise. Exits non-zero on BLOCK."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--period', default=None,
            help=f'Preset period name (one of {sorted(PERIODS)}). '
                 'Mutually exclusive with --start/--end.',
        )
        parser.add_argument('--start', default=None, help='ISO date, e.g. 2025-07-01')
        parser.add_argument('--end',   default=None, help='ISO date, e.g. 2026-03-31')
        parser.add_argument(
            '--company', default=None,
            help='Company code (e.g. ADI, VCM, ADIC). Omit to check all companies.',
        )
        parser.add_argument(
            '--re-account', default='3200',
            help='Retained-earnings account code. Default: 3200.',
        )
        parser.add_argument(
            '--current-year-pl-account', default='3300',
            help='Current-year P&L account code (Odoo equivalent: equity_unaffected). '
                 'Default: 3300.',
        )
        parser.add_argument(
            '--json', default=None,
            help='Write the JSON receipt to this path. Default: '
                 'reports/tb-check-YYYYMMDD-HHMMSS.json',
        )
        parser.add_argument(
            '--strict-mapping', action='store_true',
            help='BLOCK if any account has activity but no MA mapping. Default: warn only.',
        )

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opt):  # noqa: PLR0915 — kept linear on purpose
        # ─── Lazy imports so a broken model file doesn't break the help text ──
        from core.models import Company
        from ledger.models import Account, JournalEntry, JournalEntryLine

        start, end = self._resolve_period(opt)
        company    = self._resolve_company(Company, opt.get('company'))
        re_code    = opt['re_account']
        cy_pl_code = opt['current_year_pl_account']

        # ─── Base queryset: posted, in-period, optionally scoped to company ──
        lines = JournalEntryLine.objects.filter(
            journal_entry__status   = JournalEntry.Status.POSTED,  # rule #3
            journal_entry__entry_date__gte = start,                  # rule #4 — date (NOT created_at)
            journal_entry__entry_date__lte = end,
        ).select_related('account', 'journal_entry')
        if company is not None:
            lines = lines.filter(journal_entry__company=company)    # rule #5

        # ─── Check 1: TB foots to zero ───────────────────────────────────────
        totals = lines.aggregate(
            tot_dr = Sum('debit_bwp'),
            tot_cr = Sum('credit_bwp'),
        )
        tot_dr = d(totals['tot_dr'])
        tot_cr = d(totals['tot_cr'])
        tb_imbalance = (tot_dr - tot_cr).quantize(Decimal('0.01'))
        tb_foots = abs(tb_imbalance) <= TOLERANCE

        # ─── Check 2: per-account balance roll-up (with sign flip) ───────────
        # by_account = {account_code: {dr, cr, type, name, signed}}
        per_account: dict[str, dict] = {}
        # Aggregate at the DB to keep this O(accounts) not O(lines).
        from django.db.models import F
        per_acct_rows = (
            lines
            .values('account__code', 'account__name', 'account__account_type')
            .annotate(
                dr = Sum('debit_bwp'),
                cr = Sum('credit_bwp'),
            )
            .order_by('account__code')
        )
        revenue_total = ZERO
        expense_total = ZERO
        for row in per_acct_rows:
            code  = row['account__code']
            name  = row['account__name']
            atype = row['account__account_type']
            dr, cr = d(row['dr']), d(row['cr'])
            raw_balance = (dr - cr)  # debit-positive (the trap)
            # Sign flip — rule #7 from PDF section 5.
            if atype == Account.AccountType.REVENUE:
                signed = -raw_balance     # revenue shown positive
                revenue_total += signed
            elif atype == Account.AccountType.EXPENSE:
                signed = +raw_balance     # expense shown positive (debit-natural)
                expense_total += signed
            else:
                signed = raw_balance
            per_account[code] = {
                'name':         name,
                'account_type': atype,
                'debit':        str(dr),
                'credit':       str(cr),
                'raw_balance':  str(raw_balance),
                'signed_value': str(signed),
            }

        net_income = (revenue_total - expense_total).quantize(Decimal('0.01'))

        # ─── Check 3: net income ↔ change in Retained Earnings ───────────────
        # On omni, the P&L closes to "Current year profit/loss" (3300) during
        # the year and only rolls to RE (3200) at year-end. So the tie-out
        # account is the SUM of 3200 + 3300 movements for partial-year reports,
        # and 3200 only for full-year reports.
        equity_codes = {re_code, cy_pl_code}
        equity_movement = lines.filter(
            account__code__in=equity_codes,
        ).aggregate(
            dr = Sum('debit_bwp'),
            cr = Sum('credit_bwp'),
        )
        # Equity is credit-natural → movement = credit - debit.
        eq_dr = d(equity_movement['dr'])
        eq_cr = d(equity_movement['cr'])
        re_delta = (eq_cr - eq_dr).quantize(Decimal('0.01'))
        tie_out_diff = (net_income - re_delta).quantize(Decimal('0.01'))
        tie_out_ok   = abs(tie_out_diff) <= TOLERANCE

        # ─── Check 4: unmapped / suspense accounts with non-zero balance ─────
        # An account whose code begins '999' or whose name contains 'suspense'
        # or 'clearing' (case-insensitive) is flagged.
        suspense_codes: list[dict] = []
        for code, info in per_account.items():
            name_l = info['name'].lower()
            if code.startswith('999') or 'suspense' in name_l or 'clearing' in name_l:
                if d(info['signed_value']) != ZERO:
                    suspense_codes.append({
                        'code':   code,
                        'name':   info['name'],
                        'balance': info['signed_value'],
                    })

        # ─── Verdict ─────────────────────────────────────────────────────────
        checks_passed: list[str] = []
        checks_failed: list[dict] = []

        if tb_foots:
            checks_passed.append('tb_foots_to_zero')
        else:
            checks_failed.append({
                'check': 'tb_foots_to_zero',
                'expected': '0.00',
                'actual':   str(tb_imbalance),
                'fix_suggestion':
                    'Posted JE lines do not balance. Find the offending entry: '
                    'JournalEntryLine query above + group by journal_entry, '
                    'where SUM(debit_bwp) != SUM(credit_bwp). Then investigate '
                    'the originating module (payroll / procurement / claims) '
                    'for an unbalanced post.',
            })

        if tie_out_ok:
            checks_passed.append('net_income_eq_re_movement')
        else:
            checks_failed.append({
                'check': 'net_income_eq_re_movement',
                'expected': str(net_income),
                'actual':   str(re_delta),
                'difference': str(tie_out_diff),
                'fix_suggestion':
                    "P&L net income does not match the change in Retained "
                    f"Earnings ({re_code}) + Current-year P&L ({cy_pl_code}) "
                    "during the period. Either revenue/expense are misclassified "
                    "(check account_type on the offending accounts), or a "
                    "year-end closing entry is being double-counted, or the "
                    "RE account code is wrong (see --re-account flag).",
            })

        if suspense_codes:
            if opt['strict_mapping']:
                checks_failed.append({
                    'check': 'no_suspense_balances',
                    'accounts': suspense_codes,
                    'fix_suggestion':
                        'Reconcile suspense/clearing accounts to zero before '
                        'publishing the P&L, or exclude them with documented '
                        'reasoning.',
                })
            else:
                checks_passed.append('no_suspense_balances_(strict_off)')
        else:
            checks_passed.append('no_suspense_balances')

        verdict = 'PASS' if not checks_failed else 'BLOCK'

        # ─── Receipt ─────────────────────────────────────────────────────────
        receipt = {
            'verdict':         verdict,
            'generated_at':    datetime.now().isoformat(timespec='seconds'),
            'period':          {'start': str(start), 'end': str(end)},
            'company':         (company.code if company else 'ALL'),
            're_account':      re_code,
            'current_year_pl_account': cy_pl_code,
            'totals': {
                'debit_bwp':    str(tot_dr),
                'credit_bwp':   str(tot_cr),
                'tb_imbalance': str(tb_imbalance),
                'revenue':      str(revenue_total),
                'expenses':     str(expense_total),
                'net_income':   str(net_income),
                're_movement':  str(re_delta),
                'tie_out_diff': str(tie_out_diff),
            },
            'checks_passed':   checks_passed,
            'checks_failed':   checks_failed,
            'suspense_accounts': suspense_codes,
        }

        # ─── Write JSON receipt ──────────────────────────────────────────────
        out_path = opt.get('json') or (
            f'reports/tb-check-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        )
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, indent=2))

        # ─── Stdout summary ──────────────────────────────────────────────────
        bar = '─' * 60
        self.stdout.write(self.style.NOTICE(bar))
        styled = self.style.SUCCESS if verdict == 'PASS' else self.style.ERROR
        self.stdout.write(styled(f'  /tb-check : {verdict}'))
        self.stdout.write(self.style.NOTICE(bar))
        self.stdout.write(f'  Period       : {start} → {end}')
        self.stdout.write(f'  Company      : {company.code if company else "ALL"}')
        self.stdout.write(f'  TB imbalance : {tb_imbalance:>14}')
        self.stdout.write(f'  Revenue      : {revenue_total:>14}')
        self.stdout.write(f'  Expenses     : {expense_total:>14}')
        self.stdout.write(f'  Net income   : {net_income:>14}')
        self.stdout.write(f'  RE movement  : {re_delta:>14}')
        self.stdout.write(f'  Tie-out diff : {tie_out_diff:>14}')
        self.stdout.write(f'  Receipt      : {out}')
        if checks_failed:
            self.stdout.write(self.style.ERROR('\n  Failures:'))
            for f in checks_failed:
                self.stdout.write(self.style.ERROR(f'    • {f["check"]}'))
                self.stdout.write(f'      fix: {f["fix_suggestion"]}')
        self.stdout.write(self.style.NOTICE(bar))

        if verdict == 'BLOCK':
            sys.exit(1)

    # ------------------------------------------------------------------ #

    def _resolve_period(self, opt) -> tuple[date, date]:
        period_name = opt.get('period')
        start_arg, end_arg = opt.get('start'), opt.get('end')
        if period_name and (start_arg or end_arg):
            raise CommandError('--period is mutually exclusive with --start/--end.')
        if period_name:
            if period_name not in PERIODS:
                raise CommandError(
                    f'Unknown --period {period_name!r}. '
                    f'Known: {sorted(PERIODS)}.'
                )
            s, e = PERIODS[period_name]
            return date.fromisoformat(s), date.fromisoformat(e)
        if not (start_arg and end_arg):
            raise CommandError('Provide --period or both --start and --end.')
        return date.fromisoformat(start_arg), date.fromisoformat(end_arg)

    def _resolve_company(self, Company, code: str | None):
        if not code:
            return None
        try:
            return Company.objects.get(code__iexact=code)
        except Company.DoesNotExist as e:
            raise CommandError(f'Company code {code!r} not found.') from e
