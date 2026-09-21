"""Is a premium refund actually recorded in the Graphite refund platform?

CFO 2026-08-11: he built a refund module in Graphite last week so Finance could stop
recording premium refunds in Excel, then saw four refunds waiting for authorisation
and asked the obvious question — are these in the new platform, or did they bypass it?

That question recurs every time Finance files refunds, so this is a command rather
than a one-off query. It discovers the refund schema at runtime instead of hard-coding
column names, because the module is a week old and will keep moving.

    manage.py graphite_refund_lookup --discover
    manage.py graphite_refund_lookup --policy MIS2025206317 --policy MIS2024091450
    manage.py graphite_refund_lookup --since 2026-08-01

Read-only throughout — see integrations/graphite_ro.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from integrations import graphite_ro as gro

#: Columns worth searching for a policy / MIS reference, in preference order.
_REF_HINTS = ('policy_number', 'policy_no', 'policy', 'mis_number', 'mis',
              'reference_number', 'reference', 'ref', 'contract_number')

#: Columns worth showing when we find a row.
_SHOW_HINTS = ('id', 'graphite_ref', 'policy_number', 'reference', 'reference_number',
               'amount', 'refund_amount', 'currency', 'status', 'omni_status',
               'state', 'area', 'reason', 'reason_code', 'created_at', 'submitted_at',
               'approved_at', 'created_by', 'requested_by', 'reviewed_by',
               'approved_by', 'cfo_approved_by', 'customer_name')


class Command(BaseCommand):
    help = 'Look up premium refunds in the Graphite refund platform (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--policy', action='append', default=[],
                            help='Policy / MIS number to look for. Repeatable.')
        parser.add_argument('--since', default=None,
                            help='Only rows created on/after this date (YYYY-MM-DD).')
        parser.add_argument('--discover', action='store_true',
                            help='Print the refund tables and their columns, then stop.')
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--table', default=None,
                            help='Force a specific refund table instead of choosing.')

    # ------------------------------------------------------------------ helpers
    def _refund_tables(self) -> list[str]:
        return gro.tables_like('refund')

    def _pick_primary(self, tables: list[str]) -> tuple[str, list[str], list[str]]:
        """The refund table that actually holds policy numbers, by data not by name.

        Ranking on the name alone picked `refund_request_documents` — it contains
        "request", and it sorts before `refund_requests` alphabetically. So ask each
        table what columns it has and choose one that can answer the question,
        preferring the fullest.
        """
        best = None
        for t in tables:
            cols = gro.columns_of(t)
            refs = self._pick_ref_columns(cols)
            if not refs:
                continue
            n = gro.query(
                f'SELECT COUNT(*) AS n FROM {gro.safe_identifier(t)}')[0]['n']
            # A header table beats a child: it has more reference-bearing columns
            # and more rows than a documents/events side table.
            score = (len(refs), n)
            if best is None or score > best[0]:
                best = (score, t, cols, refs)
        if best is None:
            return '', [], []
        return best[1], best[2], best[3]

    def _pick_ref_columns(self, cols: list[str]) -> list[str]:
        low = {c.lower(): c for c in cols}
        picked = [low[h] for h in _REF_HINTS if h in low]
        # Anything else that smells like a reference, so a renamed column still hits.
        picked += [c for c in cols
                   if c not in picked
                   and any(k in c.lower() for k in ('policy', 'mis', 'reference', 'contract'))]
        return picked
    # ---------------------------------------------------------------------------

    def handle(self, *args, **opts):
        if not gro.is_configured():
            raise CommandError(
                'GRAPHITE_RO_DSN is not set on this host. It lives in SSM Parameter '
                'Store at /graphite/GRAPHITE_RO_DSN and belongs in '
                '/etc/alpha-finance/.env alongside the other credentials.')

        tables = self._refund_tables()
        if not tables:
            raise CommandError('No table in Graphite_live has "refund" in its name.')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Graphite refund tables ({len(tables)}):'))
        for t in tables:
            self.stdout.write(f'   {t}')

        if opts['discover']:
            for t in tables[:6]:
                cols = gro.columns_of(t)
                n = gro.query(f'SELECT COUNT(*) AS n FROM {gro.safe_identifier(t)}')
                self.stdout.write('')
                self.stdout.write(self.style.MIGRATE_LABEL(
                    f'{t} — {n[0]["n"]} row(s)'))
                self.stdout.write('   ' + ', '.join(cols))
            return

        policies = [p.strip() for p in opts['policy'] if p.strip()]
        if not policies and not opts['since']:
            raise CommandError('Give me --policy (repeatable) or --since, or --discover.')

        primary, cols, ref_cols = self._pick_primary(tables)
        if opts.get('table'):
            primary = opts['table']
            cols = gro.columns_of(primary)
            ref_cols = self._pick_ref_columns(cols)
        if not primary:
            raise CommandError('No refund table has a policy/reference column. '
                               'Run --discover and pass --table.')
        show = [c for c in cols if c.lower() in _SHOW_HINTS] or cols[:12]

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(f'Searching {primary}'))
        self.stdout.write(f'   reference columns searched: {", ".join(ref_cols) or "(none found)"}')
        self.stdout.write(f'   total rows in table       : '
                          f'{gro.query(f"SELECT COUNT(*) AS n FROM {gro.safe_identifier(primary)}")[0]["n"]}')

        if not ref_cols:
            self.stdout.write(self.style.WARNING(
                '   No policy/reference-looking column. Run --discover and tell me '
                'which column holds the policy number.'))
            return

        sel = ', '.join(gro.safe_identifier(c) for c in show)
        tbl = gro.safe_identifier(primary)

        for pol in policies:
            where = ' OR '.join(f'{gro.safe_identifier(c)} LIKE %s' for c in ref_cols)
            params = [f'%{pol}%'] * len(ref_cols)
            rows = gro.query(f'SELECT {sel} FROM {tbl} WHERE {where}',
                             params, limit=opts['limit'])
            self.stdout.write('')
            if rows:
                self.stdout.write(self.style.SUCCESS(
                    f'{pol}: RECORDED — {len(rows)} row(s) in the refund platform'))
                for r in rows:
                    self.stdout.write('   ' + ' | '.join(
                        f'{k}={r[k]}' for k in show if r.get(k) not in (None, '')))
            else:
                self.stdout.write(self.style.ERROR(
                    f'{pol}: NOT FOUND in {primary} — this refund was not recorded '
                    'in the platform'))

        if opts['since']:
            date_col = next((c for c in cols if c.lower() in ('created_at', 'created')), None)
            if not date_col:
                self.stdout.write(self.style.WARNING(
                    '   no created_at column, skipping --since'))
                return
            rows = gro.query(
                f'SELECT {sel} FROM {tbl} WHERE {gro.safe_identifier(date_col)} >= %s '
                f'ORDER BY {gro.safe_identifier(date_col)} DESC',
                [opts['since']], limit=opts['limit'])
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'Refunds recorded since {opts["since"]}: {len(rows)}'))
            for r in rows:
                self.stdout.write('   ' + ' | '.join(
                    f'{k}={r[k]}' for k in show if r.get(k) not in (None, '')))
