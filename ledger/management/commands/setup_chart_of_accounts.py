"""
Management command: setup_chart_of_accounts

Populates:
  - Full chart of accounts for Alpha Direct Insurance (Botswana)
  - Fiscal periods for the current Botswana fiscal year (July 2025 – June 2026)

Two-scheme CoA note (CFO directive):
This seed creates a 4-digit scaffold CoA (4100, 5100, 6100 …) suitable for
greenfield testing. The PRODUCTION CoA used by the MA P&L
(reporting.ma_pl_spec) is the 6-digit Odoo scheme (100001, 101000, 103000
…) imported by `migrate_odoo --commit`. Both coexist in the same Account
table; Odoo-imported accounts carry `external_ref='odoo:account.account:<id>'`
while seed accounts have a blank external_ref. The MA P&L reports read
ONLY the 6-digit Odoo codes — they are the trial-balance source of truth.
Once Odoo backfill is complete in any environment, this seed becomes
vestigial except for fixtures used by the demo / test suites.
"""

import calendar
import datetime

from django.core.management.base import BaseCommand

from ledger.models import Account, FiscalPeriod


# Codes that represent CALCULATED SUBTOTALS in the MA P&L roll-up, not
# postable leaves. Marked is_summary_only=True so JE validation rejects
# any direct postings to them. They are kept in the seed for display
# completeness (the dashboard CoA tree can render them as section headers).
SUMMARY_ONLY_CODES = {'4300', '5300'}


# ---------------------------------------------------------------------------
# Chart of accounts data
# Format: (code, name, account_type, sub_type, is_bank_account, parent_code)
# ---------------------------------------------------------------------------

ACCOUNTS = [
    # ── Assets ──────────────────────────────────────────────────────────────
    ('1100', 'Cash and bank accounts',          'asset', 'bank',           False, None),
    ('1110', 'FNB BWP operating account',       'asset', 'bank',           True,  '1100'),
    ('1120', 'FNB USD account',                 'asset', 'bank',           True,  '1100'),
    ('1130', 'FNB ZAR account',                 'asset', 'bank',           True,  '1100'),
    ('1140', 'Stanbic BWP account',             'asset', 'bank',           True,  '1100'),
    ('1150', 'E-wallet / mobile money',         'asset', 'bank',           True,  '1100'),
    ('1160', 'Petty cash',                      'asset', 'bank',           True,  '1100'),

    ('1200', 'Receivables',                     'asset', 'current_asset',  False, None),
    ('1210', 'Premium receivable',              'asset', 'current_asset',  False, '1200'),
    ('1220', 'Broker receivable',               'asset', 'current_asset',  False, '1200'),
    ('1230', 'Reinsurance receivable',          'asset', 'current_asset',  False, '1200'),
    ('1240', 'Other receivable',                'asset', 'current_asset',  False, '1200'),
    ('1250', 'VAT input receivable',            'asset', 'current_asset',  False, '1200'),

    ('1300', 'Prepayments',                     'asset', 'current_asset',  False, None),
    ('1310', 'Prepaid reinsurance',             'asset', 'current_asset',  False, '1300'),
    ('1320', 'Prepaid expenses',                'asset', 'current_asset',  False, '1300'),

    ('1400', 'Fixed assets',                    'asset', 'fixed_asset',    False, None),
    ('1410', 'Office equipment',                'asset', 'fixed_asset',    False, '1400'),
    ('1420', 'IT equipment',                    'asset', 'fixed_asset',    False, '1400'),
    ('1430', 'Motor vehicles',                  'asset', 'fixed_asset',    False, '1400'),
    ('1440', 'Furniture and fittings',          'asset', 'fixed_asset',    False, '1400'),

    ('1450', 'Accumulated depreciation',        'asset', 'fixed_asset',    False, None),
    ('1451', 'Accum. depr. — office equipment', 'asset', 'fixed_asset',    False, '1450'),
    ('1452', 'Accum. depr. — IT equipment',     'asset', 'fixed_asset',    False, '1450'),
    ('1453', 'Accum. depr. — motor vehicles',   'asset', 'fixed_asset',    False, '1450'),
    ('1454', 'Accum. depr. — furniture',        'asset', 'fixed_asset',    False, '1450'),

    # ── Memorandum: PO commitment accounts (steering rule 4, issue #58) ─────
    # Paired with 2199 below — DR/CR equal-and-opposite for every open PO,
    # net to zero in aggregate. Surfaces outstanding commitments in the TB.
    ('1990', 'Encumbered Purchase Commitments', 'asset',     'commitment_reserve', False, None),

    # ── Liabilities ─────────────────────────────────────────────────────────
    ('2100', 'Payables',                        'liability', 'current_liability', False, None),
    ('2110', 'Claims payable',                  'liability', 'current_liability', False, '2100'),
    ('2120', 'Reinsurance premium payable',     'liability', 'current_liability', False, '2100'),
    ('2130', 'Broker commission payable',       'liability', 'current_liability', False, '2100'),
    ('2140', 'Vendor payable (trade)',          'liability', 'current_liability', False, '2100'),
    ('2145', 'Goods Received Not Invoiced',     'liability', 'current_liability', False, '2100'),
    ('2150', 'Salary payable',                  'liability', 'current_liability', False, '2100'),
    ('2160', 'PAYE payable',                    'liability', 'current_liability', False, '2100'),
    ('2165', 'Pension contributions payable',   'liability', 'current_liability', False, '2100'),
    ('2170', 'WHT payable',                     'liability', 'current_liability', False, '2100'),
    ('2180', 'VAT output payable',              'liability', 'current_liability', False, '2100'),
    # PO commitment reserve — pairs with 1990 (see comment above).
    ('2199', 'Reserve for Encumbered Commitments', 'liability', 'commitment_reserve', False, '2100'),

    ('2200', 'Provisions',                      'liability', 'provision',         False, None),
    ('2210', 'Outstanding claims reserve',      'liability', 'provision',         False, '2200'),
    ('2220', 'IBNR reserve',                    'liability', 'provision',         False, '2200'),

    ('2300', 'Unearned premium',                'liability', 'current_liability', False, None),
    ('2310', 'Unearned premium reserve',        'liability', 'current_liability', False, '2300'),

    # ── Equity ──────────────────────────────────────────────────────────────
    ('3100', 'Share capital',                         'equity', 'equity', False, None),
    ('3200', 'Retained earnings',                     'equity', 'equity', False, None),
    ('3300', 'Current year profit/loss',              'equity', 'equity', False, None),
    ('3400', 'Statutory reserve solvency account',    'equity', 'equity', False, None),

    # ── Revenue ─────────────────────────────────────────────────────────────
    ('4100', 'Gross written premium',          'revenue', 'operating_revenue', False, None),
    ('4200', 'Reinsurance premium ceded',      'revenue', 'operating_revenue', False, None),
    ('4300', 'Net earned premium',             'revenue', 'operating_revenue', False, None),
    ('4400', 'Commission income',              'revenue', 'other_revenue',     False, None),
    ('4500', 'Investment income',              'revenue', 'other_revenue',     False, None),
    ('4600', 'Other income',                   'revenue', 'other_revenue',     False, None),

    # ── Cost of insurance ───────────────────────────────────────────────────
    ('5100', 'Claims incurred — gross',                 'expense', 'cost_of_insurance', False, None),
    ('5200', 'Claims recovered from reinsurers',        'expense', 'cost_of_insurance', False, None),
    ('5300', 'Net claims incurred',                     'expense', 'cost_of_insurance', False, None),
    ('5400', 'Commission expense — broker',             'expense', 'cost_of_insurance', False, None),
    ('5500', 'Change in unearned premium reserve',      'expense', 'cost_of_insurance', False, None),
    ('5600', 'Change in outstanding claims reserve',    'expense', 'cost_of_insurance', False, None),

    # ── Operating expenses ──────────────────────────────────────────────────
    ('6100', 'Salaries and wages',          'expense', 'operating_expense', False, None),
    ('6110', 'Employee benefits',           'expense', 'operating_expense', False, None),
    ('6120', 'Staff training',              'expense', 'operating_expense', False, None),
    ('6200', 'Office rent',                 'expense', 'operating_expense', False, None),
    ('6210', 'Utilities',                   'expense', 'operating_expense', False, None),
    ('6220', 'Office supplies',             'expense', 'operating_expense', False, None),
    ('6300', 'IT and software',             'expense', 'operating_expense', False, None),
    ('6310', 'Internet and connectivity',   'expense', 'operating_expense', False, None),
    ('6400', 'Professional fees',           'expense', 'operating_expense', False, None),
    ('6410', 'Audit fees',                  'expense', 'operating_expense', False, None),
    ('6420', 'Legal fees',                  'expense', 'operating_expense', False, None),
    ('6500', 'Marketing and advertising',   'expense', 'operating_expense', False, None),
    ('6600', 'Depreciation',                'expense', 'operating_expense', False, None),
    ('6700', 'Travel and accommodation',    'expense', 'operating_expense', False, None),
    ('6800', 'Motor vehicle expenses',      'expense', 'operating_expense', False, None),
    ('6900', 'Bank charges',                'expense', 'operating_expense', False, None),
    ('6950', 'Foreign exchange gain/loss',  'expense', 'operating_expense', False, None),
    ('6990', 'Miscellaneous expenses',      'expense', 'operating_expense', False, None),
]


class Command(BaseCommand):
    help = 'Populate the chart of accounts and fiscal periods for Alpha Direct Insurance.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('Setting up chart of accounts…'))
        self._create_accounts()
        self._create_fiscal_periods()
        self.stdout.write(self.style.SUCCESS('\nChart of accounts setup complete.'))

    # ------------------------------------------------------------------
    # Accounts — two-pass: create all, then set parents
    # ------------------------------------------------------------------

    def _create_accounts(self):
        self.stdout.write('\n  Accounts:')
        created_count = updated_count = 0

        # Pass 1: create/update accounts without parent
        for code, name, acct_type, sub_type, is_bank, _ in ACCOUNTS:
            is_summary = code in SUMMARY_ONLY_CODES
            obj, created = Account.objects.get_or_create(
                code=code,
                defaults={
                    'name':            name,
                    'account_type':    acct_type,
                    'sub_type':        sub_type,
                    'is_bank_account': is_bank,
                    'is_summary_only': is_summary,
                },
            )
            if created:
                created_count += 1
            else:
                # Keep existing data intact — only update if something changed
                changed = False
                for field, value in [
                    ('name', name), ('account_type', acct_type),
                    ('sub_type', sub_type), ('is_bank_account', is_bank),
                    ('is_summary_only', is_summary),
                ]:
                    if getattr(obj, field) != value:
                        setattr(obj, field, value)
                        changed = True
                if changed:
                    obj.save()
                    updated_count += 1

        # Pass 2: assign parent FKs
        account_map = {a.code: a for a in Account.objects.all()}
        for code, _, _, _, _, parent_code in ACCOUNTS:
            if parent_code:
                obj = account_map.get(code)
                parent = account_map.get(parent_code)
                if obj and parent and obj.parent_id != parent.pk:
                    obj.parent = parent
                    obj.save(update_fields=['parent'])

        total = len(ACCOUNTS)
        self.stdout.write(
            f"    {total} accounts: "
            f"{self.style.SUCCESS(str(created_count))} created, "
            f"{updated_count} updated, "
            f"{total - created_count - updated_count} unchanged"
        )

    # ------------------------------------------------------------------
    # Fiscal periods — July 2025 → June 2026
    # ------------------------------------------------------------------

    def _create_fiscal_periods(self):
        self.stdout.write('\n  Fiscal periods (FY 2025/2026):')
        created_count = 0

        fy_months = [
            (2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3), (2026, 4),  (2026, 5),  (2026, 6),
        ]

        for year, month in fy_months:
            period_name = f"{year}-{month:02d}"
            last_day    = calendar.monthrange(year, month)[1]
            start_date  = datetime.date(year, month, 1)
            end_date    = datetime.date(year, month, last_day)

            # Scope to the legacy company=None ("global") row. Since the
            # 2026-05-20 Manus audit made FiscalPeriod per-company, a bare
            # period_name lookup matches the 12 per-company rows + the legacy
            # global one (13 total) and get_or_create raises MultipleObjectsReturned
            # on every startup. company=None matches the legacy partial-unique
            # index only, so this seeds/finds the global period without crashing.
            _, created = FiscalPeriod.objects.get_or_create(
                period_name=period_name,
                company=None,
                defaults={
                    'start_date': start_date,
                    'end_date':   end_date,
                    'status':     FiscalPeriod.Status.OPEN,
                },
            )
            status = self.style.SUCCESS('created') if created else 'already exists'
            self.stdout.write(f"    {period_name} ({start_date} to {end_date}): {status}")
            if created:
                created_count += 1
