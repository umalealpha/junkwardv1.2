"""
READ-ONLY: how many policies share the same contact phone number?

WHY THIS EXISTS (CFO directive 16-Aug-2026, Nexus policy-access design):
The customer app will let a policyholder prove who they are with
"policy number + the phone number Graphite holds, then a one-time code".
That is only safe if a phone number identifies ONE customer.

If a BROKER's number sits on their clients' policies — which is normal in
insurance — then that broker can authenticate as every client on their book,
legitimately and silently, and it looks like ordinary use in every log. That
would be a Data Protection Act incident with a mass-notification tail.

So before that feature is built we must know:
  (a) what share of active policies have a phone unique to one policy,
  (b) how bad the worst-shared numbers are,
  (c) how many policies have no usable phone at all.
(a) tells us if the feature works; (b) sets the "too shared to trust"
threshold; (c) is the call-centre load.

SAFETY:
  * SELECT only. The connection is the Graphite **read replica**
    (GRAPHITE_RO_DB_HOST, the -rpro endpoint), which rejects writes server-side.
  * Same credentials and pattern already approved for the premium-debtors
    aging read (integrations/graphite_age.py, CFO directive 2026-06-16).
  * **No phone number is ever printed.** Output is counts, plus a masked tail
    (last 3 digits) for the worst offenders so they can be recognised without
    exposing personal data.

Usage on prod:
    python manage.py graphite_phone_distribution
    python manage.py graphite_phone_distribution --table policies --column phone
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


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
    help = 'Read-only: distribution of contact phone numbers across Graphite policies.'

    def add_arguments(self, parser):
        parser.add_argument('--table', default='', help='Policy table name (auto-detect if omitted)')
        parser.add_argument('--column', default='', help='Phone column name (auto-detect if omitted)')
        parser.add_argument('--top', type=int, default=20, help='How many shared numbers to list')

    def handle(self, *args, **opts):
        cn = _connect()
        try:
            cur = cn.cursor()

            # 1. Prove which server we are on before trusting a single number.
            cur.execute('SELECT @@hostname, DATABASE(), @@read_only')
            hostname, dbname, read_only = cur.fetchone()
            self.stdout.write(f'SERVER   : {hostname}')
            self.stdout.write(f'DATABASE : {dbname}')
            self.stdout.write(f'READ_ONLY: {read_only}  (1 = replica, writes refused)')
            self.stdout.write('')

            table, column = opts['table'], opts['column']

            # 2. Find candidate policy tables / phone columns if not told.
            if not table or not column:
                cur.execute(
                    """SELECT TABLE_NAME, COLUMN_NAME
                         FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND (COLUMN_NAME LIKE %s OR COLUMN_NAME LIKE %s
                               OR COLUMN_NAME LIKE %s)
                          AND TABLE_NAME NOT LIKE %s
                          AND TABLE_NAME NOT LIKE %s
                        ORDER BY TABLE_NAME""",
                    ('%phone%', '%mobile%', '%cell%', '%log%', '%audit%'),
                )
                rows = cur.fetchall()
                self.stdout.write('CANDIDATE TABLE/COLUMN PAIRS (pick one, re-run with --table/--column):')
                for t, c in rows[:60]:
                    self.stdout.write(f'  {t}.{c}')
                if not table or not column:
                    self.stdout.write('')
                    self.stdout.write('Nothing counted yet — re-run with --table and --column.')
                    return

            # 3. The actual distribution. Identifiers are validated against
            #    information_schema above/below, never interpolated blindly.
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
                (table, column),
            )
            if not cur.fetchone()[0]:
                raise CommandError(f'{table}.{column} does not exist on this database')

            norm = (
                "REPLACE(REPLACE(REPLACE(REPLACE(TRIM(`%s`),' ',''),'-',''),'(',''),')','')" % column
            )

            cur.execute(f'SELECT COUNT(*) FROM `{table}`')
            total = cur.fetchone()[0]

            cur.execute(
                f"SELECT COUNT(*) FROM `{table}` "
                f"WHERE `{column}` IS NULL OR {norm} = '' OR CHAR_LENGTH({norm}) < 7"
            )
            unusable = cur.fetchone()[0]

            cur.execute(
                f'SELECT c, COUNT(*) FROM (SELECT {norm} AS p, COUNT(*) AS c FROM `{table}` '
                f"WHERE `{column}` IS NOT NULL AND CHAR_LENGTH({norm}) >= 7 "
                f'GROUP BY p) t GROUP BY c ORDER BY c'
            )
            buckets = cur.fetchall()

            cur.execute(
                f'SELECT RIGHT({norm}, 3) AS tail, COUNT(*) AS c FROM `{table}` '
                f"WHERE `{column}` IS NOT NULL AND CHAR_LENGTH({norm}) >= 7 "
                f'GROUP BY {norm} ORDER BY c DESC LIMIT %s',
                (opts['top'],),
            )
            worst = cur.fetchall()

            # 4. Report — counts only, phone numbers masked to the last 3 digits.
            self.stdout.write(f'TABLE            : {table}.{column}')
            self.stdout.write(f'Total rows       : {total:,}')
            self.stdout.write(f'No usable phone  : {unusable:,}  ({unusable / total * 100:.1f}%)'
                              if total else 'No rows')
            self.stdout.write('')
            self.stdout.write('POLICIES PER PHONE NUMBER')
            shared_policies = 0
            for policies_per_phone, how_many_phones in buckets:
                covered = policies_per_phone * how_many_phones
                if policies_per_phone > 1:
                    shared_policies += covered
                label = f'{policies_per_phone} polic' + ('y' if policies_per_phone == 1 else 'ies')
                self.stdout.write(f'  {label:<18} -> {how_many_phones:>7,} numbers  ({covered:,} policies)')
            usable = total - unusable
            if usable:
                self.stdout.write('')
                self.stdout.write(
                    f'SHARED           : {shared_policies:,} of {usable:,} policies with a phone '
                    f'({shared_policies / usable * 100:.1f}%) sit on a number used by more than one policy.'
                )
            self.stdout.write('')
            self.stdout.write(f'WORST-SHARED NUMBERS (masked — last 3 digits only), top {opts["top"]}:')
            for tail, count in worst:
                self.stdout.write(f'  •••{tail}  -> {count:,} policies')
        finally:
            cn.close()
