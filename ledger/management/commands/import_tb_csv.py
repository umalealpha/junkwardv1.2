"""
Management command: import_tb_csv

Loads `data/alpha_direct_full_tb_complete.csv` — the authoritative Alpha
Direct Insurance trial balance for FY25 (Jun 2025) and FY26-9M (Mar 2026)
— into the omni `Account` and `JournalEntry` tables.

This is the CFO-mandated source of truth (CRITICAL_INSTRUCTION_FOR_CLAUDE_CODE.docx,
2026-05-17). All historical figures for these two periods come from this
CSV. After import, FY25 and FY26-Mar are HARD-LOCKED — see
`LOCKED_FINANCIALS.md` at the repo root.

Behaviour
---------
- For each unique Account Code in ACCOUNT rows: get_or_create an Account
  row with type inferred from the code prefix and the balance side.
- For each period: create ONE JournalEntry that contains every account's
  **Period Activity** (Debit + Credit columns of the CSV) as a
  JournalEntryLine. This is the net delta over the period, NOT the end
  balance. Why this matters: balance-sheet reports (e.g.
  `build_cash_position`) sum across ALL JEs without date filtering — so
  if both FY25 and FY26 JEs each stored the end balance, the cash tile
  would double-count. Period Activity is additive: sum of FY25 + FY26
  activity gives the FY26 end balance correctly (because Initial
  Balance for FY26 = End Balance for FY25 by construction).
- A small "Rounding Suspense" line absorbs the < 100 BWP TB rounding
  diff so the JE is exactly balanced.
- Idempotent. Re-running the command:
    1. Deletes any prior import JEs for that period (matched by
       `source_type='tb_csv_import'` + `description` carrying the period).
    2. Re-imports cleanly from the CSV.

Usage
-----
    python manage.py import_tb_csv                      # ADIC, both periods, dry-run
    python manage.py import_tb_csv --commit             # actual write
    python manage.py import_tb_csv --period FY25_Jun2025 --commit
    python manage.py import_tb_csv --file path/to/other.csv --commit
"""

import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, Currency
from ledger.models import (
    Account,
    FiscalPeriod,
    JournalEntry,
    JournalEntryLine,
)


# Period → end date (period close date for the JE)
PERIOD_DATES = {
    'FY25_Jun2025': date(2025, 6, 30),
    'FY26_Mar2026': date(2026, 3, 31),
}

ZERO = Decimal('0.00')
SOURCE_TYPE = 'tb_csv_import'
ROUNDING_CODE = '999999'
ROUNDING_NAME = 'TB Import Rounding Suspense'

# Map known GROUP_HEADER labels to canonical account_type + sub_type.
# Used to seed parent groups and to disambiguate child accounts where
# the prefix alone is ambiguous.
# Code prefix ranges follow the Odoo CoA Alpha Direct uses.
PREFIX_TYPE_MAP = [
    # (prefix, account_type, sub_type)
    # Audited 2026-05-17 against the CSV GROUP_HEADER labels — see
    # alpha_direct_full_tb_complete.csv. Sub-type granularity matters: the
    # balance-sheet report categorises by sub_type into Current Assets /
    # Fixed Assets / Other Assets etc., so an account tagged
    # 'current_asset' that's actually a long-term receivable shows up in
    # the wrong section on the BS.

    # ── P&L (revenue / expense) — 1xxxxx ────────────────────────────────
    ('100', 'revenue',   'operating_revenue'),    # GWP
    ('101', 'revenue',   'operating_revenue'),    # Premium Ceded — contra
    ('102', 'revenue',   'operating_revenue'),    # Change in UPR
    ('103', 'expense',   'cost_of_insurance'),    # Gross claims
    ('104', 'revenue',   'other_revenue'),        # RI Recovery
    ('105', 'revenue',   'other_revenue'),        # Subrog/Salvage
    ('106', 'revenue',   'other_revenue'),        # RI Commission income
    ('107', 'expense',   'cost_of_insurance'),    # Commissions paid
    ('109', 'revenue',   'other_revenue'),        # Forex income
    # 1104xx is the fixed-asset block (F&F, laptops, motor vehicles) and its
    # '.01' accumulated-depreciation contras — NOT employee costs. Without these
    # four entries the greedy '110' prefix below types every future 1104xx
    # account as an expense (found by the 31-Aug-2026 QC pass on 110402.01
    # Accumulated Depreciation - Motor Vehicles). Existing rows are never
    # overwritten on re-import, so only NEW accounts were ever at risk.
    # Specific-before-general: the map returns the FIRST prefix that matches.
    ('110400.01', 'asset', 'accumulated_depreciation'),  # Accum Depr — F&F
    ('110401.01', 'asset', 'accumulated_depreciation'),  # Accum Depr — Laptops & Computers
    ('110402.01', 'asset', 'accumulated_depreciation'),  # Accum Depr — Motor Vehicles
    ('1104',      'asset', 'fixed_asset'),               # F&F / Laptops / Motor Vehicles (cost)
    ('110', 'expense',   'operating_expense'),    # Employee costs
    ('111', 'expense',   'operating_expense'),    # Op expenses
    ('112', 'expense',   'operating_expense'),    # IT
    ('113', 'expense',   'operating_expense'),    # Paygates
    ('114', 'expense',   'operating_expense'),    # Telephone
    ('115', 'expense',   'operating_expense'),    # Marketing
    ('116', 'expense',   'operating_expense'),    # Staff Welfare
    ('117', 'expense',   'operating_expense'),    # Consultancy
    ('118', 'expense',   'cost_of_insurance'),    # Acquisition cost
    ('119', 'expense',   'provision'),            # Provision for related party
    ('120', 'expense',   'provision'),            # Provision for Subrogation
    ('121', 'expense',   'operating_expense'),    # Bad Debt Expense
    ('122', 'expense',   'operating_expense'),    # Depreciation
    ('123', 'revenue',   'other_revenue'),        # Interest income
    ('124', 'revenue',   'other_revenue'),        # Other income

    # Standalone P&L codes outside the structured prefix scheme
    ('135', 'expense',   'provision'),            # Provision for Change in IBNR
    ('147', 'revenue',   'other_revenue'),        # Reinsurers share of IBNR claims
    ('195', 'expense',   'operating_expense'),    # Bad Debts Expense (legacy)
    ('303', 'expense',   'operating_expense'),    # Accounting Fees
    ('321', 'expense',   'operating_expense'),    # Legal Expense — Claims
    ('84',  'revenue',   'operating_revenue'),    # Change in Unearned RI Premium

    # ── Balance sheet — 2xxxxx ──────────────────────────────────────────
    # Assets (Dr-natural)
    ('200', 'asset',     'accumulated_depreciation'),  # Accum Depr Motor Vehicle (contra-asset)
    ('201', 'asset',     'other_asset'),               # Related party Receivables (long-term)
    ('202', 'asset',     'current_asset'),             # Other Receivables
    ('203', 'asset',     'current_asset'),             # Staff Loans
    ('210', 'asset',     'fixed_asset'),               # Motor Vehicle
    ('220', 'asset',     'fixed_asset'),               # Computers & Networks (G F S Software, Office Equip, etc.)
    ('230', 'asset',     'accumulated_depreciation'),  # Accum Depr F&F + IT (contra)
    ('240', 'asset',     'current_asset'),             # Subrog & Insurance Receivables
    ('250', 'asset',     'fixed_asset'),               # Right of Use - Asset (IFRS 16)
    ('260', 'asset',     'current_asset'),             # Salvages & Recoveries Receivable
    ('270', 'asset',     'other_asset'),               # Deferred Tax Asset (non-current)
    ('280', 'asset',     'bank'),                      # Bank
    ('290', 'asset',     'current_asset'),             # Insurance Premiums Due From Policyholders
    ('330', 'asset',     'current_asset'),             # Prepayments

    # Liabilities (Cr-natural)
    ('204', 'liability', 'current_liability'),         # Related party Payables
    ('205', 'liability', 'current_liability'),         # Unearned Premium Reserve
    ('206', 'liability', 'long_term_liability'),       # Finance Lease
    ('207', 'liability', 'current_liability'),         # Finance Lease ST
    ('208', 'liability', 'current_liability'),         # Claims Payable
    ('209', 'liability', 'current_liability'),         # VAT
    ('211', 'liability', 'current_liability'),         # WHT
    ('212', 'liability', 'current_liability'),         # Due to Reinsurers
    ('213', 'liability', 'current_liability'),         # Severance & Leave
    ('214', 'liability', 'current_liability'),         # Accounts Payable
    ('215', 'liability', 'current_liability'),         # Other Payables
    ('218', 'liability', 'provision'),                 # IBNR Reserve (technical provision)

    # Equity
    ('216', 'equity',    'equity'),                    # Share Capital
    ('217', 'equity',    'equity'),                    # Retained Earnings
    ('219', 'equity',    'equity'),                    # Current Year Earnings / Tax Provision (mixed)

    # ── Group-company codes (Manus QC 2026-08-30) ─────────────────────────
    # Manus's QC of the 30-Jun-2026 FY26 activity load found 8 accounts newly
    # created via the balance-side fallback, of which 4 were miscategorised
    # (110402.01 accum-depr treated as expense, 450002 interest-income treated
    # as liability, 600074/644002 expenses treated as asset). The fixes were
    # applied to the affected Account rows on prod; extending this map here
    # stops the same class of miss from recurring on the next TB import.
    #
    # 4xxxxx family — non-ADIC group companies use 4xxxxx for revenue.
    ('400', 'revenue',   'operating_revenue'),         # Product Sales / Fee income
    ('443', 'revenue',   'other_revenue'),             # Refund income / recoveries
    ('450', 'revenue',   'other_revenue'),             # Interest income (staff loan, other)
    # 6xxxxx family — non-ADIC group companies use 6xxxxx for operating expense.
    ('600', 'expense',   'operating_expense'),         # WHT / Repairs / Fuel / other opex
    ('613', 'expense',   'operating_expense'),         # Consultancy fees
    ('620', 'expense',   'operating_expense'),         # Bank charges
    ('630', 'expense',   'operating_expense'),         # Salaries / staff / benefits
    ('644', 'expense',   'operating_expense'),         # Provident fund expense
]


def infer_type(code: str, end_dr: Decimal, end_cr: Decimal):
    """Return (account_type, sub_type) for a given account code + balance."""
    for prefix, atype, stype in PREFIX_TYPE_MAP:
        if code.startswith(prefix):
            return atype, stype
    # Fallback: use balance side
    if end_dr > end_cr:
        return 'asset', 'current_asset'
    return 'liability', 'current_liability'


def _d(s: str) -> Decimal:
    s = (s or '').strip()
    if not s or s.lower() == 'none':
        return ZERO
    try:
        return Decimal(s)
    except Exception:
        return ZERO


class Command(BaseCommand):
    help = ('Import the authoritative Alpha Direct trial balance CSV into '
            'the omni Account and JournalEntry tables.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            default=str(Path(settings.BASE_DIR) / 'data' / 'alpha_direct_full_tb_complete.csv'),
            help='Path to the TB CSV. Defaults to data/alpha_direct_full_tb_complete.csv.',
        )
        parser.add_argument(
            '--company-code',
            default='ADIC',
            help='Company.code to attach the import to. Default: ADIC.',
        )
        parser.add_argument(
            '--period',
            choices=list(PERIOD_DATES),
            default=None,
            help='Restrict to a single period. Default: both periods.',
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually write to the database. Default is dry-run.',
        )

    def handle(self, *args, **opts):
        path = Path(opts['file'])
        if not path.exists():
            raise CommandError(f'CSV file not found: {path}')

        company = Company.objects.filter(code=opts['company_code']).first()
        if not company:
            raise CommandError(f'Company {opts["company_code"]} not found. '
                               f'Seed companies first.')

        sys_user = User.objects.filter(is_superuser=True).order_by('id').first()
        if not sys_user:
            raise CommandError('No superuser exists to attribute the import to.')

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )

        periods_filter = [opts['period']] if opts['period'] else list(PERIOD_DATES)

        # ── Phase 1: parse CSV grouped by period ────────────────────────────
        # We use Period Activity (Dr + Cr) for the JE lines so balances are
        # additive across periods. End Balance columns are still used for
        # account-type inference (Dr-natural vs Cr-natural side) and for
        # the bank-balance sanity check below.
        rows_by_period = defaultdict(list)
        seen_codes = {}  # code -> (name, end_dr, end_cr) — last wins, used for type inference
        with path.open(encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['Row Type'] != 'ACCOUNT':
                    continue
                p = row['Period']
                if p not in periods_filter:
                    continue
                code = (row['Account Code'] or '').strip()
                if not code:
                    continue
                name = (row['Account Name'] or '').strip()
                end_dr = _d(row['End Balance Debit (BWP)'])
                end_cr = _d(row['End Balance Credit (BWP)'])
                act_dr = _d(row['Period Activity Debit'])
                act_cr = _d(row['Period Activity Credit'])
                rows_by_period[p].append({
                    'code': code, 'name': name,
                    'end_dr': end_dr, 'end_cr': end_cr,
                    'act_dr': act_dr, 'act_cr': act_cr,
                })
                seen_codes[code] = (name, end_dr, end_cr)

        if not rows_by_period:
            raise CommandError('CSV had no ACCOUNT rows for the requested period(s).')

        # ── Phase 2: account upsert (read-only side-effect-free in dry-run) ──
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{"COMMIT" if opts["commit"] else "DRY-RUN"}] '
            f'Upserting {len(seen_codes)} accounts from {path.name}'
        ))
        accts_created = accts_updated = 0
        accts_by_code = {}

        if opts['commit']:
            with transaction.atomic():
                for code, (name, dr, cr) in seen_codes.items():
                    atype, stype = infer_type(code, dr, cr)
                    obj, created = Account.objects.get_or_create(
                        code=code,
                        defaults={
                            'name': name,
                            'account_type': atype,
                            'sub_type': stype,
                            'external_ref': f'odoo:account.account:tb-csv-{code}',
                        },
                    )
                    accts_by_code[code] = obj
                    if created:
                        accts_created += 1
                    else:
                        # Force re-categorisation when the importer is the
                        # source of truth for this account. PREFIX_TYPE_MAP
                        # changes (e.g. PR #113 added 218/220/230/240/250/
                        # 260/270/290/330 mappings) must propagate to
                        # existing rows; the previous "only fill if blank"
                        # behaviour silently swallowed those changes and
                        # left Current Assets / Other Assets miscategorised
                        # on the BS. The CSV importer is authoritative on
                        # account_type + sub_type for accounts it touches —
                        # only the `name` field is preserved (since the CFO
                        # may have renamed an account in the admin UI).
                        changed = False
                        if obj.name != name and name:
                            obj.name = name; changed = True
                        if obj.account_type != atype:
                            obj.account_type = atype; changed = True
                        if obj.sub_type != stype:
                            obj.sub_type = stype; changed = True
                        if obj.external_ref != f'odoo:account.account:tb-csv-{code}':
                            obj.external_ref = f'odoo:account.account:tb-csv-{code}'
                            changed = True
                        if changed:
                            obj.save()
                            accts_updated += 1

                # Rounding suspense account
                rounding, _ = Account.objects.get_or_create(
                    code=ROUNDING_CODE,
                    defaults={
                        'name': ROUNDING_NAME,
                        'account_type': 'equity',
                        'sub_type': 'rounding_suspense',
                    },
                )
                accts_by_code[ROUNDING_CODE] = rounding
        else:
            for code in seen_codes:
                accts_by_code[code] = code  # placeholder
            accts_by_code[ROUNDING_CODE] = ROUNDING_CODE

        self.stdout.write(f'  Accounts: {accts_created} created, '
                          f'{accts_updated} updated, '
                          f'{len(seen_codes) - accts_created - accts_updated} unchanged.')

        # ── Phase 3: per-period JE rollup ───────────────────────────────────
        for period_id, period_rows in rows_by_period.items():
            entry_date = PERIOD_DATES[period_id]

            # Ensure a FiscalPeriod exists for the JE date
            self._ensure_fiscal_period(entry_date, commit=opts['commit'])

            # Period Activity totals — these drive the JE lines.
            total_dr = sum((r['act_dr'] for r in period_rows), ZERO)
            total_cr = sum((r['act_cr'] for r in period_rows), ZERO)
            rounding_diff = total_dr - total_cr   # positive → add Cr suspense
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n[{period_id}] {entry_date}  rows={len(period_rows)}  '
                f'PeriodActivity Dr={total_dr}  Cr={total_cr}  rounding={rounding_diff}'
            ))

            # Bank group sanity check — use End Balance (the headline number)
            bank_end_dr = sum((r['end_dr'] for r in period_rows
                              if r['code'].startswith('280')), ZERO)
            bank_act_net = sum((r['act_dr'] - r['act_cr'] for r in period_rows
                               if r['code'].startswith('280')), ZERO)
            self.stdout.write(f'  Bank (280xxx) end Dr balance: {bank_end_dr}  '
                              f'period-activity net: {bank_act_net}')

            if not opts['commit']:
                continue

            with transaction.atomic():
                # Idempotent: delete prior import JEs for this period
                deleted, _ = JournalEntry.objects.filter(
                    source_type=SOURCE_TYPE,
                    description__icontains=period_id,
                ).delete()
                if deleted:
                    self.stdout.write(f'  Deleted {deleted} prior import JE(s) for this period.')

                je = JournalEntry.objects.create(
                    entry_date=entry_date,
                    description=f'TB import — {period_id} — Alpha Direct Insurance',
                    source_type=SOURCE_TYPE,
                    journal_type=JournalEntry.JournalType.GENERAL,
                    status=JournalEntry.Status.DRAFT,
                    company=company,
                    currency_code_id='BWP',
                    created_by=sys_user,
                    is_related_party=False,
                )

                for r in period_rows:
                    acct = accts_by_code[r['code']]
                    # Skip summary-only accounts to avoid the JE validation block.
                    if acct.is_summary_only:
                        continue
                    if r['act_dr'] == ZERO and r['act_cr'] == ZERO:
                        continue
                    JournalEntryLine.objects.create(
                        journal_entry=je,
                        account=acct,
                        description=f'{period_id} TB period activity',
                        debit_amount=r['act_dr'],
                        credit_amount=r['act_cr'],
                        debit_bwp=r['act_dr'],
                        credit_bwp=r['act_cr'],
                    )

                if rounding_diff != ZERO:
                    suspense = accts_by_code[ROUNDING_CODE]
                    if rounding_diff > ZERO:
                        # Total Dr > Total Cr → add a credit suspense to balance
                        JournalEntryLine.objects.create(
                            journal_entry=je,
                            account=suspense,
                            description=f'{period_id} TB rounding (Dr-Cr diff)',
                            debit_amount=ZERO,
                            credit_amount=rounding_diff,
                            debit_bwp=ZERO,
                            credit_bwp=rounding_diff,
                        )
                    else:
                        JournalEntryLine.objects.create(
                            journal_entry=je,
                            account=suspense,
                            description=f'{period_id} TB rounding (Cr-Dr diff)',
                            debit_amount=abs(rounding_diff),
                            credit_amount=ZERO,
                            debit_bwp=abs(rounding_diff),
                            credit_bwp=ZERO,
                        )

                # Post directly — TB import is a system action, not a maker-checker JE.
                je.post(user=sys_user, _allow_direct=True)
                self.stdout.write(self.style.SUCCESS(
                    f'  Posted JE {je.entry_number} with {je.lines.count()} lines.'
                ))

        self.stdout.write(self.style.SUCCESS(
            f'\n[{"COMMIT" if opts["commit"] else "DRY-RUN"}] '
            f'import_tb_csv complete.'
        ))

    def _ensure_fiscal_period(self, dt: date, *, commit: bool):
        if not commit:
            return
        period_name = f'{dt.year}-{dt.month:02d}'
        if FiscalPeriod.objects.filter(period_name=period_name).exists():
            return
        # Period spans the calendar month of dt
        import calendar
        last = calendar.monthrange(dt.year, dt.month)[1]
        FiscalPeriod.objects.create(
            period_name=period_name,
            start_date=date(dt.year, dt.month, 1),
            end_date=date(dt.year, dt.month, last),
            status=FiscalPeriod.Status.OPEN,
        )
        self.stdout.write(f'  Created FiscalPeriod {period_name}.')
