"""
audit_categorisation — dump per (account_type, sub_type) totals + every
account-level balance for one company at one date.

Used to investigate residual gaps between omni's Balance Sheet tile and
the MA workbook line totals — without making any speculative
reclassification.  After running this, the operator can compare the
output against the MA workbook and decide which `PREFIX_TYPE_MAP` rows
need updating.

Usage:
    python manage.py audit_categorisation --company ADIC --as-of 2026-03-31
    python manage.py audit_categorisation --company ADIC --as-of 2025-06-30 --by-account
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

ZERO = Decimal('0.00')


def _q(v) -> Decimal:
    return (v or ZERO).quantize(Decimal('0.01'))


class Command(BaseCommand):
    help = (
        'Dump per (account_type, sub_type) totals + per-account balances '
        'for a company at a given date. Use to investigate Balance Sheet '
        'residual gaps vs the MA workbook.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True,
                            help='Company code, e.g. ADIC.')
        parser.add_argument('--as-of', required=True,
                            help='ISO date for as-of position, e.g. 2026-03-31.')
        parser.add_argument('--by-account', action='store_true',
                            help='Print every leaf account (not just sub_type totals).')

    def handle(self, *args, **opt):
        from core.models import Company
        from ledger.models import Account, JournalEntry, JournalEntryLine

        try:
            company = Company.objects.get(code__iexact=opt['company'])
        except Company.DoesNotExist as e:
            raise CommandError(f'Unknown company code {opt["company"]!r}') from e

        try:
            as_of = date_cls.fromisoformat(opt['as_of'])
        except ValueError as e:
            raise CommandError(f'Bad --as-of: {opt["as_of"]!r}; use YYYY-MM-DD') from e

        base = JournalEntryLine.objects.filter(
            journal_entry__company=company,
            journal_entry__status=JournalEntry.Status.POSTED,
            journal_entry__entry_date__lte=as_of,
        ).select_related('account')

        # Per-sub_type roll-up
        agg_by_sub = defaultdict(lambda: {'dr': ZERO, 'cr': ZERO, 'count': 0})
        per_account = []

        for line in base.values(
            'account__code', 'account__name',
            'account__account_type', 'account__sub_type',
        ).annotate(d=Sum('debit_bwp'), c=Sum('credit_bwp')):
            atype = line['account__account_type']
            stype = line['account__sub_type'] or '(none)'
            key = (atype, stype)
            dr, cr = (line['d'] or ZERO), (line['c'] or ZERO)
            agg_by_sub[key]['dr'] += dr
            agg_by_sub[key]['cr'] += cr
            agg_by_sub[key]['count'] += 1
            per_account.append({
                'code':  line['account__code'],
                'name':  line['account__name'][:50],
                'type':  atype,
                'sub':   stype,
                'dr':    _q(dr),
                'cr':    _q(cr),
                'bal':   _q(dr - cr),  # Dr-positive convention
            })

        # Sub-type summary
        bar = '─' * 90
        self.stdout.write(self.style.NOTICE(bar))
        self.stdout.write(self.style.NOTICE(
            f'  Categorisation audit · {company.code} · as of {as_of}'))
        self.stdout.write(self.style.NOTICE(bar))
        self.stdout.write(f'  {"account_type":15s} {"sub_type":30s} {"accts":>6s} '
                          f'{"debit":>16s} {"credit":>16s} {"balance":>16s}')
        self.stdout.write('  ' + '-' * 88)

        grand_dr = ZERO
        grand_cr = ZERO
        for (atype, stype), agg in sorted(agg_by_sub.items()):
            dr, cr, n = agg['dr'], agg['cr'], agg['count']
            bal = dr - cr
            grand_dr += dr
            grand_cr += cr
            self.stdout.write(
                f'  {atype:15s} {stype:30s} {n:>6d} '
                f'{_q(dr):>16,.2f} {_q(cr):>16,.2f} {_q(bal):>16,.2f}')

        self.stdout.write('  ' + '-' * 88)
        self.stdout.write(
            f'  {"TOTAL":15s} {"":30s} {len(per_account):>6d} '
            f'{_q(grand_dr):>16,.2f} {_q(grand_cr):>16,.2f} '
            f'{_q(grand_dr - grand_cr):>16,.2f}')
        self.stdout.write(self.style.NOTICE(bar))

        # Asset-focused roll-up (matches reports.build_balance_sheet logic)
        self.stdout.write('  Balance-Sheet asset bucketing (matches reporting.reports):')
        ca = ZERO
        fa = ZERO
        oa = ZERO
        for (atype, stype), agg in agg_by_sub.items():
            if atype != 'asset':
                continue
            bal = agg['dr'] - agg['cr']
            if stype in ('bank', 'current_asset'):
                ca += bal
            elif stype == 'fixed_asset':
                fa += bal
            else:
                oa += bal
        self.stdout.write(f'    Current Assets (bank + current_asset): {_q(ca):>16,.2f}')
        self.stdout.write(f'    Fixed Assets   (fixed_asset)          : {_q(fa):>16,.2f}')
        self.stdout.write(f'    Other Assets   (everything else)      : {_q(oa):>16,.2f}')
        self.stdout.write(f'    TOTAL ASSETS                          : {_q(ca + fa + oa):>16,.2f}')
        self.stdout.write(self.style.NOTICE(bar))

        if opt['by_account']:
            self.stdout.write('  Per-account detail (sub_type = "current_asset" only):')
            self.stdout.write(f'    {"code":10s} {"name":50s} {"balance":>16s}')
            for r in sorted(per_account, key=lambda x: x['code']):
                if r['sub'] != 'current_asset':
                    continue
                self.stdout.write(
                    f"    {r['code']:10s} {r['name']:50s} {r['bal']:>16,.2f}")
            self.stdout.write(self.style.NOTICE(bar))
