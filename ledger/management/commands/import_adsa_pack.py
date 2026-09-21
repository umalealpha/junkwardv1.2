"""
Management command: import_adsa_pack

Loads the ADSA CFO pack into omni for the **Alpha Direct South Africa**
entity ONLY. Refuses to write anything outside ADSA.

Source: CSVs at data/adsa_pack_20260518/ (extracted from
ADSA_CFO_Pack_20260518.xlsx, CFO-supplied 2026-05-18).

Scope (Stage 1):
  - ZAR Currency row (get-or-create)
  - ADSA.base_currency = ZAR
  - 78 Chart-of-Accounts rows (explicit account_type / sub_type from sheet)
  - FY24 TB rollup -> 1 posted JE at 2024-06-30
  - FY25 GL detail -> one posted JE per entry_ref (~613 JEs)
  - Customers + Suppliers as billing.Contact rows scoped to ADSA

Deferred (Stage 2):
  - PP&E real asset register (xlsx sheet is TB-derived, not per-asset)
  - HRIS employees + payroll (HRIS coupling, separate ingestion path)
  - AP Invoice records (financial impact already in GL; AP aging deferred)

Currency
--------
ADSA's functional currency is ZAR. JournalEntry.currency_code is set to ZAR
on every imported JE. The JournalEntryLine.debit_bwp / credit_bwp columns
are legacy-named but used to store the entry's functional-currency amount
(per the BWP-balance-check in JE._validate_for_posting).

Idempotency
-----------
Re-running the command:
  1. Deletes ALL prior JEs with source_type='adsa_pack_import' and
     company=ADSA (FY24 rollup + every GL-detail JE, whatever year they
     fall in), then
  2. Re-imports cleanly.

NOTE (bug fix 2026-06-03): the GL-detail delete used to be bounded to the
FY25 window (2024-07-01..2025-06-30). The real gl.csv detail spans multiple
years (observed 2019-2023), so the bounded delete never cleared the
out-of-window history and each --commit ADDED a full copy — ADSA ended
triple-loaded. The delete is now by (company, source_type) only.

Hard refusal: any code or row not starting with ADSA_ aborts the run.

Usage
-----
    python manage.py import_adsa_pack            # dry-run
    python manage.py import_adsa_pack --commit   # actual write
"""

import csv
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import Contact
from core.models import Company, Currency
from ledger.models import (
    Account,
    FiscalPeriod,
    JournalEntry,
    JournalEntryLine,
)


ZERO = Decimal('0.00')
SOURCE_TYPE = 'adsa_pack_import'
COMPANY_CODE = 'ADSA'
CURRENCY_CODE = 'ZAR'
PACK_DIR_DEFAULT = Path(settings.BASE_DIR) / 'data' / 'adsa_pack_20260518'

# Map xlsx Odoo-style account_type to omni AccountType choices.
ODOO_TO_OMNI_TYPE = {
    'asset_current':         'asset',
    'asset_cash':            'asset',
    'asset_receivable':      'asset',
    'asset_fixed':           'asset',
    'asset_prepayments':     'asset',
    'asset_non_current':     'asset',
    'liability_current':     'liability',
    'liability_non_current': 'liability',
    'liability_payable':     'liability',
    'equity':                'equity',
    'equity_unaffected':     'equity',
    'income':                'revenue',
    'income_other':          'revenue',
    'expense':               'expense',
    'expense_direct_cost':   'expense',
    'expense_depreciation':  'expense',
}

# Translate xlsx sub_types to omni convention (used by Balance Sheet
# grouping). Anything not listed passes through unchanged.
ODOO_TO_OMNI_SUBTYPE = {
    'receivable':           'current_asset',
    'payable':              'current_liability',
    'depreciation':         'accumulated_depreciation',
    'non_current_asset':    'other_asset',
    'prepayments':          'other_asset',
    'non_current_liability':'long_term_liability',
    'unaffected':           'equity',
    'income':               'operating_revenue',
    'other_income':         'other_revenue',
    'expense':              'operating_expense',
    'direct_cost':          'direct_cost',
}


def _d(v) -> Decimal:
    if v is None or v == '':
        return ZERO
    return Decimal(str(v)).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = 'Import the ADSA CFO pack into Alpha Direct South Africa.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pack-dir',
            type=str,
            default=str(PACK_DIR_DEFAULT),
            help='Directory containing the extracted CSVs.',
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually write to the database. Default is dry-run.',
        )

    # ------------------------------------------------------------------
    # entrypoint
    # ------------------------------------------------------------------

    def handle(self, *args, **opts):
        pack_dir = Path(opts['pack_dir'])
        if not pack_dir.exists():
            raise CommandError(f'Pack directory not found: {pack_dir}')

        commit = opts['commit']
        tag = 'COMMIT' if commit else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n[{tag}] Importing ADSA pack from {pack_dir}'
        ))

        sys_user = User.objects.filter(is_superuser=True).order_by('id').first()
        if not sys_user:
            raise CommandError('No superuser exists to attribute the import to.')

        company = self._step_company_and_currency(commit)
        accounts = self._step_coa(pack_dir, company, commit)
        self._step_contacts(pack_dir, company, commit)
        self._step_fy24_tb_rollup(pack_dir, company, accounts, sys_user, commit)
        self._step_fy25_gl_detail(pack_dir, company, accounts, sys_user, commit)

        self.stdout.write(self.style.SUCCESS(
            f'\n[{tag}] ADSA pack import done.'
        ))

    # ------------------------------------------------------------------
    # ADSA-only guard
    # ------------------------------------------------------------------

    def _assert_adsa_code(self, code: str, ctx: str):
        if not code or not code.startswith('ADSA_'):
            raise CommandError(
                f'Refusing to write non-ADSA code "{code}" in {ctx}. '
                f'This importer is ADSA-only.'
            )

    # ------------------------------------------------------------------
    # 1. Currency + Company
    # ------------------------------------------------------------------

    def _step_company_and_currency(self, commit: bool) -> Company:
        self.stdout.write('\n--- Step 1: ZAR currency + ADSA.base_currency ---')

        # Locate ADSA. Don't create — must already exist.
        company = Company.objects.filter(code=COMPANY_CODE).first()
        if not company:
            raise CommandError(
                f'Company {COMPANY_CODE} not found. Seed companies first.'
            )

        if commit:
            zar, created = Currency.objects.get_or_create(
                code='ZAR',
                defaults={'name': 'South African Rand', 'symbol': 'R'},
            )
            self.stdout.write(
                f'  ZAR currency: {"created" if created else "exists"}'
            )

            if company.base_currency_id != 'ZAR':
                company.base_currency = zar
                company.save(update_fields=['base_currency'])
                self.stdout.write(f'  ADSA.base_currency -> ZAR')
            else:
                self.stdout.write(f'  ADSA.base_currency already ZAR')
        else:
            self.stdout.write('  [dry-run] would create ZAR + set ADSA.base_currency')

        return company

    # ------------------------------------------------------------------
    # 2. Chart of Accounts
    # ------------------------------------------------------------------

    def _step_coa(self, pack_dir: Path, company: Company, commit: bool) -> dict:
        self.stdout.write('\n--- Step 2: Chart of Accounts ---')
        rows = list(csv.DictReader(open(pack_dir / 'coa.csv', encoding='utf-8-sig')))
        self.stdout.write(f'  CoA rows in CSV: {len(rows)}')

        # Validate ADSA-only
        for r in rows:
            self._assert_adsa_code(r['code'], 'coa.csv')

        if not commit:
            self.stdout.write('  [dry-run] would upsert 78 Account rows')
            return {r['code']: r['code'] for r in rows}

        accounts = {}
        created = updated = unchanged = 0
        for r in rows:
            atype = ODOO_TO_OMNI_TYPE.get(r['account_type'], 'asset')
            stype = ODOO_TO_OMNI_SUBTYPE.get(r['sub_type'], r['sub_type'])
            is_bank = (stype == 'bank')

            obj, was_created = Account.objects.get_or_create(
                code=r['code'],
                defaults={
                    'name': r['name'],
                    'account_type': atype,
                    'sub_type': stype,
                    'is_bank_account': is_bank,
                    'currency_code_id': CURRENCY_CODE,
                    'owner_company': company,
                    'external_ref': f'adsa-pack:account:{r["code"]}',
                },
            )
            if was_created:
                created += 1
            else:
                changed = False
                if obj.name != r['name'] and r['name']:
                    obj.name = r['name']; changed = True
                if obj.account_type != atype:
                    obj.account_type = atype; changed = True
                if obj.sub_type != stype:
                    obj.sub_type = stype; changed = True
                if obj.is_bank_account != is_bank:
                    obj.is_bank_account = is_bank; changed = True
                if obj.currency_code_id != CURRENCY_CODE:
                    obj.currency_code_id = CURRENCY_CODE; changed = True
                if obj.owner_company_id != company.id:
                    obj.owner_company = company; changed = True
                ref = f'adsa-pack:account:{r["code"]}'
                if obj.external_ref != ref:
                    obj.external_ref = ref; changed = True
                if changed:
                    obj.save()
                    updated += 1
                else:
                    unchanged += 1
            accounts[r['code']] = obj

        self.stdout.write(
            f'  Accounts: created={created} updated={updated} unchanged={unchanged}'
        )
        return accounts

    # ------------------------------------------------------------------
    # 3. Contacts (customers + suppliers)
    # ------------------------------------------------------------------

    def _step_contacts(self, pack_dir: Path, company: Company, commit: bool):
        self.stdout.write('\n--- Step 3: Contacts (customers + suppliers) ---')

        customers = list(csv.DictReader(
            open(pack_dir / 'customers.csv', encoding='utf-8-sig'),
        ))
        suppliers = list(csv.DictReader(
            open(pack_dir / 'suppliers.csv', encoding='utf-8-sig'),
        ))
        self.stdout.write(f'  customers={len(customers)} suppliers={len(suppliers)}')

        if not commit:
            self.stdout.write('  [dry-run] would upsert Contact rows')
            return

        def upsert(rows, kind: str, code_col: str, name_col: str):
            created = updated = 0
            for r in rows:
                ref = f'adsa-pack:contact:{kind}:{r[code_col]}'
                obj, was = Contact.objects.get_or_create(
                    external_ref=ref,
                    defaults={
                        'contact_type':  kind,
                        'name':          r[name_col],
                        'tax_id':        r.get('vat_number') or '',
                        'currency_code_id': CURRENCY_CODE,
                        'company':       company,
                        'is_active':     (r.get('is_active', 'Y').upper() == 'Y'),
                    },
                )
                if was:
                    created += 1
                else:
                    changed = False
                    if obj.name != r[name_col]:
                        obj.name = r[name_col]; changed = True
                    if obj.company_id != company.id:
                        obj.company = company; changed = True
                    if obj.currency_code_id != CURRENCY_CODE:
                        obj.currency_code_id = CURRENCY_CODE; changed = True
                    if changed:
                        obj.save()
                        updated += 1
            self.stdout.write(
                f'  {kind:10s}: created={created} updated={updated}'
            )

        upsert(customers, 'customer', 'customer_code', 'customer_name')
        upsert(suppliers, 'vendor',   'supplier_code', 'supplier_name')

    # ------------------------------------------------------------------
    # 4. FY24 TB rollup -> 1 JE
    # ------------------------------------------------------------------

    def _step_fy24_tb_rollup(self, pack_dir: Path, company: Company,
                             accounts: dict, sys_user, commit: bool):
        self.stdout.write('\n--- Step 4: FY24 TB rollup ---')
        rows = list(csv.DictReader(
            open(pack_dir / 'tb_fy24.csv', encoding='utf-8-sig'),
        ))
        for r in rows:
            self._assert_adsa_code(r['account_code'], 'tb_fy24.csv')

        entry_date = date(2024, 6, 30)
        total_dr = sum(_d(r['debit_zar']) for r in rows)
        total_cr = sum(_d(r['credit_zar']) for r in rows)
        self.stdout.write(
            f'  rows={len(rows)} Dr={total_dr} Cr={total_cr} diff={total_dr - total_cr}'
        )
        if total_dr != total_cr:
            raise CommandError(f'TB FY24 does not balance: Dr {total_dr} vs Cr {total_cr}')

        if not commit:
            self.stdout.write('  [dry-run] would post 1 JE at 2024-06-30')
            return

        self._ensure_fiscal_period(entry_date)

        # Idempotent delete
        prior = JournalEntry.objects.filter(
            company=company,
            source_type=SOURCE_TYPE,
            description__icontains='FY24',
        )
        n_prior = prior.count()
        if n_prior:
            prior.delete()
            self.stdout.write(f'  Deleted {n_prior} prior FY24 JE(s)')

        with transaction.atomic():
            je = JournalEntry.objects.create(
                entry_date=entry_date,
                description='ADSA TB import — FY24 (Jul 2023 – Jun 2024)',
                source_type=SOURCE_TYPE,
                journal_type=JournalEntry.JournalType.GENERAL,
                status=JournalEntry.Status.DRAFT,
                company=company,
                currency_code_id=CURRENCY_CODE,
                created_by=sys_user,
                is_related_party=False,
            )

            for r in rows:
                acct = accounts[r['account_code']]
                dr = _d(r['debit_zar'])
                cr = _d(r['credit_zar'])
                if dr == ZERO and cr == ZERO:
                    continue
                if acct.is_summary_only:
                    continue
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=acct,
                    description=f'FY24 TB rollup: {r["account_name"]}',
                    debit_amount=dr,
                    credit_amount=cr,
                    debit_bwp=dr,
                    credit_bwp=cr,
                )

            je.post(user=sys_user, _allow_direct=True)
            self.stdout.write(self.style.SUCCESS(
                f'  Posted {je.entry_number} with {je.lines.count()} lines'
            ))

    # ------------------------------------------------------------------
    # 5. FY25 GL detail -> one JE per entry_ref
    # ------------------------------------------------------------------

    def _step_fy25_gl_detail(self, pack_dir: Path, company: Company,
                             accounts: dict, sys_user, commit: bool):
        self.stdout.write('\n--- Step 5: FY25 GL detail ---')
        rows = list(csv.DictReader(
            open(pack_dir / 'gl.csv', encoding='utf-8-sig'),
        ))
        for r in rows:
            self._assert_adsa_code(r['account_code'], 'gl.csv')

        # Group lines by entry_ref
        groups = defaultdict(list)
        for r in rows:
            groups[r['entry_ref']].append(r)

        self.stdout.write(f'  GL lines: {len(rows)}  JEs: {len(groups)}')

        # Pre-validate every group balances
        bad = []
        for ref, lines in groups.items():
            td = sum(_d(l['debit_zar']) for l in lines)
            tc = sum(_d(l['credit_zar']) for l in lines)
            if td != tc:
                bad.append((ref, td, tc))
        if bad:
            raise CommandError(
                f'{len(bad)} GL entry_refs do not balance. First few: {bad[:3]}'
            )

        if not commit:
            self.stdout.write(f'  [dry-run] would post {len(groups)} JEs')
            return

        # Ensure fiscal periods covering FY25 (monthly)
        for m_start, m_end in self._monthly_buckets(date(2024, 7, 1), date(2025, 6, 30)):
            self._ensure_fiscal_period(m_start)

        # Idempotent delete of ALL prior adsa-pack GL JEs (everything except
        # the FY24 rollup posted in step 4). BUG FIX 2026-06-03: this was
        # previously bounded to the FY25 window (2024-07-01..2025-06-30), but
        # gl.csv detail spans multiple years (observed 2019-2023). The bounded
        # delete left out-of-window copies behind, so each --commit ADDED a
        # full copy → ADSA triple-loaded (1035 JEs = ~345 real x 3). Deleting
        # by (company, source_type) only — never date-bounded — makes re-runs
        # truly idempotent regardless of the GL's actual date span. Scope stays
        # strictly this importer's own JEs (source_type='adsa_pack_import'),
        # so no manual or other-source entries are ever touched.
        prior = JournalEntry.objects.filter(
            company=company,
            source_type=SOURCE_TYPE,
        ).exclude(description__icontains='FY24')
        n_prior = prior.count()
        if n_prior:
            prior.delete()
            self.stdout.write(f'  Deleted {n_prior} prior FY25 GL JE(s)')

        posted = 0
        for ref, lines in groups.items():
            lines_sorted = sorted(lines, key=lambda x: int(x['line_no']))
            head = lines_sorted[0]
            entry_date = datetime.strptime(head['entry_date'], '%Y-%m-%d').date()

            with transaction.atomic():
                je = JournalEntry.objects.create(
                    entry_date=entry_date,
                    description=f'ADSA GL {ref} — {head["description"][:120]}',
                    source_type=SOURCE_TYPE,
                    journal_type=JournalEntry.JournalType.GENERAL,
                    status=JournalEntry.Status.DRAFT,
                    company=company,
                    currency_code_id=CURRENCY_CODE,
                    created_by=sys_user,
                    is_related_party=False,
                )

                for l in lines_sorted:
                    acct = accounts[l['account_code']]
                    dr = _d(l['debit_zar'])
                    cr = _d(l['credit_zar'])
                    if dr == ZERO and cr == ZERO:
                        continue
                    if acct.is_summary_only:
                        continue
                    JournalEntryLine.objects.create(
                        journal_entry=je,
                        account=acct,
                        description=l.get('memo') or l['description'],
                        debit_amount=dr,
                        credit_amount=cr,
                        debit_bwp=dr,
                        credit_bwp=cr,
                    )

                je.post(user=sys_user, _allow_direct=True)
                posted += 1
                if posted % 100 == 0:
                    self.stdout.write(f'    ... posted {posted}/{len(groups)}')

        self.stdout.write(self.style.SUCCESS(
            f'  Posted {posted} GL JEs covering FY25'
        ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_fiscal_period(self, d: date):
        period_name = d.strftime('%Y-%m')
        period_start = date(d.year, d.month, 1)
        if d.month == 12:
            next_month_start = date(d.year + 1, 1, 1)
        else:
            next_month_start = date(d.year, d.month + 1, 1)
        period_end = date.fromordinal(next_month_start.toordinal() - 1)
        FiscalPeriod.objects.get_or_create(
            period_name=period_name,
            defaults={
                'start_date': period_start,
                'end_date':   period_end,
                'status':     FiscalPeriod.Status.OPEN,
            },
        )

    def _monthly_buckets(self, start: date, end: date):
        cur = date(start.year, start.month, 1)
        while cur <= end:
            if cur.month == 12:
                nxt = date(cur.year + 1, 1, 1)
            else:
                nxt = date(cur.year, cur.month + 1, 1)
            month_end = date.fromordinal(nxt.toordinal() - 1)
            yield cur, month_end
            cur = nxt
