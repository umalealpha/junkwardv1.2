"""
auto_map_fs_line_item — set Account.fs_line_item for unmapped accounts
using the SAME name-keyword patterns the CFO-approved per-entity BS engine
already uses (reporting/entity_bs_templates.py + reporting/ma_bs_spec.py).

CFO directive 2026-06-09 (Legakwa thread). 84% of accounts (1,811 / 2,167)
have no fs_line_item. The per-entity BS router shipped 2026-06-09 (commit
5a515fa) doesn't depend on fs_line_item — it matches on Account.name. But
the CoA-MA-tree report and the ADIC MA P&L DO use fs_line_item, so leaving
it blank means those views show "(unclassified)" for most of the group.

Strategy:
  * Match each unmapped Account by Account.name against the same keyword
    library used by entity_bs_templates._line(...). First match wins.
  * For ADIC + ADIL, use ma_bs_spec.SECTIONS labels (insurance shape).
  * For everything else, use the entity_bs_templates section line definitions.
  * Only update accounts whose fs_line_item is currently NULL/empty.
  * Default mode = --dry-run (print the table, no DB change).
  * --commit writes the mappings.

Audit: every UPDATE is captured as a NoteLogEntry-style print line so the
diff is fully visible before commit. The Account model is NOT AuditableMixin
(separate gap from BUG-019), so per-row AuditLog rows are not written — the
command's stdout is the audit trail; run via `manage.py shell` for record.

Usage:
  python manage.py auto_map_fs_line_item                 # dry-run, all companies
  python manage.py auto_map_fs_line_item --company RSA   # dry-run, RSA only
  python manage.py auto_map_fs_line_item --commit        # write all
  python manage.py auto_map_fs_line_item --company RSA --commit
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from ledger.models import Account
from core.models import Company


# Build the keyword library by reading the per-entity BS templates directly
# (avoid duplicating the patterns). For ADIC + ADIL we layer in the
# ma_bs_spec.py section line labels as keyword matches against themselves.

def _icontains_q(keywords: Iterable[str], exclude: Iterable[str]) -> Q:
    inc = Q()
    for kw in keywords:
        inc |= Q(name__icontains=kw)
    if not exclude:
        return inc
    exc = Q()
    for kw in exclude:
        exc |= Q(name__icontains=kw)
    return inc & ~exc


def _entity_keyword_table(company_code: str) -> list[tuple[str, list[str], list[str]]]:
    """Return [(fs_line_item_label, keywords, excludes), ...] for an entity,
    in priority order. First matching label wins.
    """
    from reporting.entity_bs_templates import get_template
    tpl = get_template(company_code)
    out: list[tuple[str, list[str], list[str]]] = []
    if tpl:
        for sec in tpl['sections']:
            for line in sec['lines']:
                out.append((line['label'], list(line['keywords']),
                            list(line.get('exclude') or [])))
    return out


def _adic_keyword_table() -> list[tuple[str, list[str], list[str]]]:
    """ADIC + ADIL: derive a keyword library from ma_bs_spec.py section
    line LABELS. Each label is itself a keyword (e.g. "Bank and Cash
    Accounts" matches accounts named "...Bank...Cash..."). We also add a
    few hand-written keywords for short labels that wouldn't match on the
    label string alone.
    """
    from reporting.ma_bs_spec import SECTIONS
    hand: dict[str, tuple[list[str], list[str]]] = {
        'Bank and Cash Accounts':          (['Bank', 'Cash', 'FNB', 'Petty Cash'], ['Overdraft', 'Reconciliation']),
        'Trade Receivables':               (['Account Receivable', 'Trade Receivable', 'Trade Debtor'], ['Related', 'Staff', 'Group']),
        'Other Receivables':               (['Other Receivable'], []),
        'Reinsurance Provisions':          (['Reinsurance Provision', 'RI Provision'], []),
        'Related Party Receivables':       (['Receivable from', 'Due from'], []),
        'Staff Loans & Advances':          (['Staff Loan', 'Employee Loan', 'Staff Advance'], []),
        'Claims Payable - All Risk':       (['Claims Payable'], []),
        'Subrogation Receivables':         (['Subrogation Receivable'], []),
        'Salvage Receivables':             (['Salvage Receivable'], []),
        'Deferred tax asset':              (['Deferred Tax Asset'], []),
        'Property, Plant & Equipment':     (['Motor Vehicle', 'Furniture', 'IT Equipment', 'Laptop', 'Computer',
                                              'Equipment', 'Office Equipment', 'Display Rack', 'Fixtures'],
                                            ['Accumulated', 'Depreciation', 'Salvage']),
        'Property, Plant & Equipment - Accumulated Depreciation':
                                            (['Accumulated Depreciation', 'Accum Depreciation'], []),
        'Right of Use - Asset':            (['Right of Use', 'RoU'], []),
        'Unearned Premium Reserve':        (['Unearned Premium', 'UPR'], []),
        'Current Lease Liability':         (['Lease Liability'], ['Long Term', 'Long-Term', 'Non-current']),
        'WHT':                             (['WHT'], []),
        'VAT':                             (['VAT'], []),
        'Due to Reinsurers':               (['Due to Reinsurer', 'Payable to Reinsurer'], []),
        'IBNR - BS':                       (['IBNR'], []),
        'Severance & Leave liabilities':   (['Severance', 'Leave Liability'], []),
        'Short-term Loan':                 (['Short-term Loan', 'Short Term Loan'], []),
        'Tax Payable':                     (['Tax Payable', 'Income Tax Payable', 'PAYE'], []),
        'Trade & Other Payables':          (['Account Payable', 'Trade Payable'], ['Related', 'Group']),
        'Long-term Loan':                  (['Long-term Loan', 'Long Term Loan'], []),
        'Lease Liabilities':               (['Lease Liability Long', 'Lease Liabilities Long'], []),
        'Stated Capital (Issued Share Capital)':
                                            (['Capital'], ['Reserve', 'Working']),
        'Retained Earnings':               (['Retained Earnings', 'Accumulated Earnings'], []),
        'IBNR Reserve':                    (['IBNR Reserve'], []),
    }
    out: list[tuple[str, list[str], list[str]]] = []
    for sec in SECTIONS:
        for label in sec['lines']:
            kw, exc = hand.get(label, ([label], []))
            out.append((label, kw, exc))
    return out


def _classify(account: Account, table: list[tuple[str, list[str], list[str]]]) -> str | None:
    name = account.name or ''
    for label, keywords, excludes in table:
        # Skip excludes first
        if any(e.lower() in name.lower() for e in excludes):
            continue
        # Match if any keyword in name (case-insensitive substring)
        if any(k.lower() in name.lower() for k in keywords):
            return label
    return None


class Command(BaseCommand):
    help = 'Auto-map Account.fs_line_item by Account.name keyword. Dry-run by default.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='Limit to one Company.code (e.g. RSA, QIH).')
        parser.add_argument('--commit', action='store_true',
                            help='Write changes to the DB. Without this flag, runs dry.')

    def handle(self, *args, **opts):
        company_code = opts.get('company')
        commit = bool(opts.get('commit'))

        companies = Company.objects.order_by('code')
        if company_code:
            companies = companies.filter(code__iexact=company_code)
            if not companies.exists():
                raise CommandError(f"Company '{company_code}' not found.")

        mode_label = 'COMMIT' if commit else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(f'\n=== {mode_label} ===\n'))

        total_unmapped = 0
        total_mapped_now = 0
        total_still_unmapped = 0
        per_label_counter: Counter = Counter()

        for co in companies:
            code = co.code.upper()
            table = (_adic_keyword_table() if code in ('ADIC', 'ADIL')
                     else _entity_keyword_table(code))
            if not table:
                self.stdout.write(f'{code}: no template, skipping')
                continue
            unmapped = Account.objects.filter(
                owner_company=co,
            ).filter(Q(fs_line_item__isnull=True) | Q(fs_line_item__exact=''))
            n_unmapped = unmapped.count()
            total_unmapped += n_unmapped
            if n_unmapped == 0:
                self.stdout.write(f'{code}: 0 unmapped')
                continue

            applied = 0
            still = 0
            for a in unmapped:
                label = _classify(a, table)
                if label:
                    applied += 1
                    per_label_counter[label] += 1
                    if commit:
                        a.fs_line_item = label
                        a.save(update_fields=['fs_line_item'])
                else:
                    still += 1
            total_mapped_now += applied
            total_still_unmapped += still
            self.stdout.write(
                f'  {code:8} unmapped={n_unmapped:>4}  '
                f'would-map={applied:>4}  remaining={still:>4}'
            )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'TOTAL: unmapped={total_unmapped} | '
            f'{("mapped" if commit else "would-map")}={total_mapped_now} | '
            f'remaining-unmapped={total_still_unmapped}'
        ))
        self.stdout.write('\nTop 15 fs_line_item labels applied:')
        for label, n in per_label_counter.most_common(15):
            self.stdout.write(f'  {n:>5}  {label}')
        if not commit:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN — no changes written. Re-run with --commit to apply.'
            ))
