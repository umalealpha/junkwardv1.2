"""
READ-ONLY: what can omni actually see inside Graphite today?

WHY (CFO directive 16-Aug-2026): "Omni should have access to everything Graphite
has, every single thing." Before building a new API we must know what access
already exists — omni already holds a SELECT-only connection to the Graphite
read replica (used by the premium-debtors aging report), and that may already be
unrestricted across the whole schema.

This command answers, with evidence, not assumption:
  * which server/database we land on (the -rpro read replica, writes refused)
  * how many tables the read user can actually see
  * which of the tables that matter are reachable (policies, claims, customers,
    payments, KYC)
  * the read user's granted privileges, verbatim

SAFETY: SELECT + SHOW GRANTS only. No data rows are printed — table names and
counts only, so no customer information is exposed by running it.

Usage:  python manage.py graphite_access_probe
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Tables the Nexus/customer work will need. Presence check only.
WANTED = [
    'policies', 'policy_actions', 'customers', 'customer_kyc',
    'new_claims', 'claims', 'claim_reserves',
    'payment_transactions', 'realpay_contract_installments',
    'vehicles', 'agents',
]


def _connect():
    try:
        import pymysql
    except ImportError as exc:                       # pragma: no cover
        raise CommandError('pymysql is not installed in this image') from exc
    host = getattr(settings, 'GRAPHITE_RO_DB_HOST', '')
    if not host:
        raise CommandError('GRAPHITE_RO_DB_HOST is not set — read replica not configured')
    return pymysql.connect(
        host=host,
        port=int(getattr(settings, 'GRAPHITE_RO_DB_PORT', 3306) or 3306),
        user=getattr(settings, 'GRAPHITE_RO_DB_USER', ''),
        password=getattr(settings, 'GRAPHITE_RO_DB_PASSWORD', ''),
        database=getattr(settings, 'GRAPHITE_RO_DB_NAME', 'Graphite_live'),
        connect_timeout=int(getattr(settings, 'GRAPHITE_RO_DB_TIMEOUT', 20) or 20),
    )


class Command(BaseCommand):
    help = 'Read-only probe: what can omni see in Graphite right now?'

    def handle(self, *args, **opts):
        cn = _connect()
        try:
            cur = cn.cursor()

            cur.execute('SELECT @@hostname, DATABASE(), @@read_only, CURRENT_USER()')
            hostname, dbname, read_only, who = cur.fetchone()
            self.stdout.write('── WHERE AM I ─────────────────────────────')
            self.stdout.write(f'server    : {hostname}')
            self.stdout.write(f'database  : {dbname}')
            self.stdout.write(f'read_only : {read_only}   (1 = replica, writes refused server-side)')
            self.stdout.write(f'connected : {who}')
            self.stdout.write('')

            cur.execute(
                'SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()')
            visible = cur.fetchone()[0]
            self.stdout.write('── HOW MUCH CAN I SEE ─────────────────────')
            self.stdout.write(f'tables visible to this user: {visible:,}')
            self.stdout.write('')

            self.stdout.write('── THE TABLES THAT MATTER ─────────────────')
            for name in WANTED:
                cur.execute(
                    'SELECT COUNT(*) FROM information_schema.TABLES '
                    'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (name,))
                if not cur.fetchone()[0]:
                    self.stdout.write(f'  {name:<32} NOT VISIBLE')
                    continue
                try:
                    cur.execute(f'SELECT COUNT(*) FROM `{name}`')
                    self.stdout.write(f'  {name:<32} readable  ({cur.fetchone()[0]:,} rows)')
                except Exception as exc:                       # noqa: BLE001
                    self.stdout.write(f'  {name:<32} VISIBLE BUT BLOCKED ({type(exc).__name__})')
            self.stdout.write('')

            self.stdout.write('── WHAT THIS USER IS ALLOWED ──────────────')
            try:
                cur.execute('SHOW GRANTS FOR CURRENT_USER()')
                for (grant,) in cur.fetchall():
                    self.stdout.write(f'  {grant}')
            except Exception as exc:                           # noqa: BLE001
                self.stdout.write(f'  could not read grants: {exc}')
        finally:
            cn.close()
