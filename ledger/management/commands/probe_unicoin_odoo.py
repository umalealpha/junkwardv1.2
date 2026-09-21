"""
Read-only probe of Odoo data for a single legal entity (default: Unicoin).

CFO directive 2026-05-18: 'explore an option for the entity Unicoin
only — pull all purchase orders, the trial balance, and the fixed
asset register from the Odoo API. Try it.'

This command does NOT write to alpha-finance. It hits the Odoo XML-RPC
API and prints what's available so the CFO can decide whether to
trigger the corresponding importers afterwards.

  python manage.py probe_unicoin_odoo
  python manage.py probe_unicoin_odoo --company GCX        # different entity
  python manage.py probe_unicoin_odoo --max-rows 10        # smaller samples

Output sections:
  1. Odoo company resolution (alpha-finance code → Odoo company_id)
  2. Purchase orders          (purchase.order + .line)
  3. Trial balance            (account.move.line by account.account)
  4. Fixed asset register     (account.asset or account.asset.asset)
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.runner import DEFAULT_COMPANY_MAP_BY_NAME


class Command(BaseCommand):
    help = 'Read-only probe of Odoo POs / TB / Fixed Assets for one entity.'

    def add_arguments(self, parser):
        parser.add_argument('--company', default='UNI',
                            help='alpha-finance Company.code (default UNI).')
        parser.add_argument('--max-rows', type=int, default=5,
                            help='Sample rows to print per section (default 5).')
        parser.add_argument('--from-date', default='2024-07-01',
                            help='TB lower bound (ISO date; default 2024-07-01).')
        parser.add_argument('--to-date',   default='2026-03-31',
                            help='TB upper bound (ISO date; default 2026-03-31).')

    def handle(self, *args, **opts):
        code      = opts['company'].upper()
        max_rows  = opts['max_rows']
        from_date = opts['from_date']
        to_date   = opts['to_date']

        client = OdooClient()
        client.authenticate()

        # ── 1. Resolve company ────────────────────────────────────────────
        companies = list(client.search_read('res.company', [], ['id', 'name']))
        odoo_id   = None
        for rec in companies:
            nm = (rec.get('name') or '').strip().lower()
            mapped = DEFAULT_COMPANY_MAP_BY_NAME.get(nm)
            if mapped and mapped.upper() == code:
                odoo_id = int(rec['id'])
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f'\nResolved {code} → Odoo company_id={odoo_id} "{rec["name"]}"'
                ))
                break
        if odoo_id is None:
            raise CommandError(
                f'No Odoo company maps to alpha-finance code {code!r}. '
                f'Update DEFAULT_COMPANY_MAP_BY_NAME in ops/migrations/odoo/runner.py.'
            )

        # ── 2. Purchase Orders ────────────────────────────────────────────
        self._probe_pos(client, code, odoo_id, max_rows)

        # ── 3. Trial Balance ──────────────────────────────────────────────
        self._probe_tb(client, code, odoo_id, max_rows, from_date, to_date)

        # ── 4. Fixed Assets ───────────────────────────────────────────────
        self._probe_assets(client, code, odoo_id, max_rows)

    # ------------------------------------------------------------------
    def _probe_pos(self, client, code, odoo_id, max_rows):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n── {code} PURCHASE ORDERS ' + '─' * 40
        ))
        try:
            pos = list(client.search_read(
                'purchase.order',
                [('company_id', '=', odoo_id)],
                ['id', 'name', 'partner_id', 'date_order', 'state',
                 'amount_untaxed', 'amount_total', 'currency_id'],
            ))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f'  purchase.order fetch failed: {e}'))
            return

        by_state: dict[str, int] = defaultdict(int)
        total = Decimal('0')
        for p in pos:
            by_state[p.get('state') or '?'] += 1
            total += Decimal(str(p.get('amount_total') or 0))

        self.stdout.write(f'  count: {len(pos)}')
        self.stdout.write(f'  total amount: {total:,.2f}')
        for st, n in sorted(by_state.items(), key=lambda x: -x[1]):
            self.stdout.write(f'    state={st:<12} {n}')
        for p in pos[:max_rows]:
            partner = (p.get('partner_id') or [None, '?'])[1]
            self.stdout.write(
                f"    sample: {p['name']:<14} {p.get('date_order','')[:10]} "
                f"{partner[:38]:<38} {Decimal(str(p.get('amount_total') or 0)):>12,.2f} "
                f"({p.get('state')})"
            )

    # ------------------------------------------------------------------
    def _probe_tb(self, client, code, odoo_id, max_rows, from_date, to_date):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n── {code} TRIAL BALANCE  ({from_date} → {to_date}) ' + '─' * 16
        ))
        # Aggregate move lines per account
        try:
            lines = list(client.search_read(
                'account.move.line',
                [
                    ('company_id', '=', odoo_id),
                    ('parent_state', '=', 'posted'),
                    ('date', '>=', from_date),
                    ('date', '<=', to_date),
                ],
                ['account_id', 'debit', 'credit', 'date'],
            ))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f'  account.move.line fetch failed: {e}'))
            return

        by_account: dict[tuple, dict] = {}
        for ln in lines:
            aid_pair = ln.get('account_id')
            if not isinstance(aid_pair, (list, tuple)) or len(aid_pair) < 2:
                continue
            aid, aname = int(aid_pair[0]), str(aid_pair[1])
            row = by_account.setdefault((aid, aname), {'d': Decimal('0'), 'c': Decimal('0')})
            row['d'] += Decimal(str(ln.get('debit') or 0))
            row['c'] += Decimal(str(ln.get('credit') or 0))

        total_d = sum((r['d'] for r in by_account.values()), Decimal('0'))
        total_c = sum((r['c'] for r in by_account.values()), Decimal('0'))
        self.stdout.write(f'  lines: {len(lines)}')
        self.stdout.write(f'  unique accounts: {len(by_account)}')
        self.stdout.write(f'  total DR: {total_d:>16,.2f}')
        self.stdout.write(f'  total CR: {total_c:>16,.2f}')
        self.stdout.write(f'  TB diff:  {total_d - total_c:>16,.2f}  (should be 0.00 for posted-only)')

        # Top accounts by net activity
        top = sorted(
            by_account.items(),
            key=lambda kv: -abs(kv[1]['d'] - kv[1]['c']),
        )[:max_rows]
        for (aid, aname), r in top:
            net = r['d'] - r['c']
            self.stdout.write(
                f"    {aname[:50]:<50} DR={r['d']:>14,.2f}  CR={r['c']:>14,.2f}  "
                f"NET={net:>14,.2f}"
            )

    # ------------------------------------------------------------------
    def _probe_assets(self, client, code, odoo_id, max_rows):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n── {code} FIXED ASSETS ' + '─' * 42
        ))
        # Odoo enterprise uses `account.asset`; community fork uses
        # `account.asset.asset`. Try both, report whichever returns.
        for model in ('account.asset', 'account.asset.asset'):
            try:
                assets = list(client.search_read(
                    model,
                    [('company_id', '=', odoo_id)],
                    ['id', 'name', 'state', 'original_value', 'salvage_value',
                     'date', 'method_number', 'method_period'],
                ))
            except Exception as e:  # noqa: BLE001
                # Field set may differ — try a minimal field set
                try:
                    assets = list(client.search_read(
                        model,
                        [('company_id', '=', odoo_id)],
                        ['id', 'name', 'state'],
                    ))
                except Exception:
                    continue   # try the other model name
            self.stdout.write(f'  model: {model}')
            self.stdout.write(f'  count: {len(assets)}')
            if not assets:
                continue
            by_state: dict[str, int] = defaultdict(int)
            value_total = Decimal('0')
            for a in assets:
                by_state[a.get('state') or '?'] += 1
                if a.get('original_value'):
                    value_total += Decimal(str(a['original_value']))
            self.stdout.write(f'  total original value: {value_total:,.2f}')
            for st, n in sorted(by_state.items(), key=lambda x: -x[1]):
                self.stdout.write(f'    state={st:<10} {n}')
            for a in assets[:max_rows]:
                self.stdout.write(
                    f"    sample: {a.get('name', '')[:50]:<50} "
                    f"{Decimal(str(a.get('original_value') or 0)):>14,.2f} "
                    f"({a.get('state')})"
                )
            return  # found a working model — stop trying

        self.stdout.write(self.style.WARNING(
            '  no asset records found under account.asset or account.asset.asset '
            f'for company_id={odoo_id}. Either no FAR in Odoo for {code}, or the '
            'asset module label differs on this Odoo install.'
        ))
