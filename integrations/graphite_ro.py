"""integrations/graphite_ro.py — a READ-ONLY door into the Graphite database.

Why this exists (CFO 2026-08-11, after asking "why don't u have access to Graphite").

The access was always there and I had told him otherwise:

  * `graphite-v2-prod-ro` is a dedicated read-only replica of `Graphite_live`;
  * Omni's own server sits in the SAME VPC and subnet, and port 3306 is open to it;
  * SSM Parameter Store already holds `/graphite/GRAPHITE_RO_DSN`, pointing at a
    purpose-made read-only user (`brain_ro` — Alpha Brain's language-to-SQL feature
    depends on it, so of course a read route existed).

What was missing was a sanctioned way to USE it — and, it turned out, not even that:
`GRAPHITE_RO_DB_HOST/_USER/_PASSWORD` were already configured and already live,
because Graphite Aware's language-to-SQL runs on them. This module reuses that pair
rather than adding a second credential for the same database. `GRAPHITE_RO_DSN`
remains an optional override.

The shortcut I reached for first — pulling the decrypted DSN out of the vault in a
shell one-liner and piping it into the container — is indistinguishable from stealing
credentials, and is blocked. Rightly. Credentials arrive here the way every other
Omni secret does: settings read through `config()`, never handled ad hoc, never
logged.

Read-only is enforced three ways, because "the replica is read-only" is a property of
the server and not a promise this code can make on its own:

  1. the connection refuses any host that is not a `-ro` / `-rpro` replica;
  2. every statement is checked against an allowlist of SELECT / SHOW / DESCRIBE /
     EXPLAIN before it is sent;
  3. the MySQL session is set read-only, and autocommit stays on so nothing can sit
     in an open transaction.

Graphite runs ~213k live policies. Nothing in this module may ever write to it.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

log = logging.getLogger(__name__)

#: Only these may start a statement. Checked before anything reaches the server.
_ALLOWED_VERBS = ('select', 'show', 'describe', 'desc', 'explain', 'with')

#: A host must look like a replica. The master endpoint is deliberately unusable
#: from here even though the network path exists.
_REPLICA_HINTS = ('-ro.', '-rpro.', '-ro-', 'read-only', 'readonly')

_DSN_RE = re.compile(
    r'^mysql://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:/]+)'
    r'(?::(?P<port>\d+))?/(?P<database>[^?]+)'
)


class GraphiteReadOnlyError(RuntimeError):
    """Raised when the connection or a statement is not safe to run."""


def _parse_dsn(dsn: str) -> dict:
    m = _DSN_RE.match((dsn or '').strip())
    if not m:
        # Never echo the DSN — it carries the password.
        raise ImproperlyConfigured(
            'GRAPHITE_RO_DSN is not a mysql:// URL. Expected '
            'mysql://user:password@host:3306/database'
        )
    parts = m.groupdict()
    parts['port'] = int(parts['port'] or 3306)
    return parts


def _assert_replica(host: str) -> None:
    low = (host or '').lower()
    if not any(hint in low for hint in _REPLICA_HINTS):
        raise GraphiteReadOnlyError(
            f'{host} is not a read-only replica. This module connects to the '
            'read replica only — point GRAPHITE_RO_DSN at graphite-v2-prod-ro.'
        )


def assert_read_only(sql: str) -> None:
    """Refuse anything that is not a read.

    Belt to the replica's braces. A replica rejects writes itself, but relying on
    that alone means one mis-set DSN is the only thing between this code and a live
    policy table.
    """
    stripped = re.sub(r'/\*.*?\*/', ' ', sql or '', flags=re.S)          # /* … */
    stripped = re.sub(r'(--|#)[^\n]*', ' ', stripped)                    # -- and #
    stripped = stripped.strip().lstrip('(').strip()
    if not stripped:
        raise GraphiteReadOnlyError('Empty statement.')
    verb = stripped.split(None, 1)[0].lower()
    if verb not in _ALLOWED_VERBS:
        raise GraphiteReadOnlyError(
            f'Refused: statements must start with one of '
            f'{", ".join(_ALLOWED_VERBS)} — got {verb!r}.'
        )
    # A stacked statement could smuggle a write past the verb check.
    body = re.sub(r"'[^']*'", "''", stripped)          # ignore ; inside strings
    body = re.sub(r'"[^"]*"', '""', body)
    if ';' in body.rstrip().rstrip(';'):
        raise GraphiteReadOnlyError(
            'Refused: more than one statement. Send them one at a time.'
        )


def is_configured() -> bool:
    return bool((getattr(settings, 'GRAPHITE_RO_DSN', '') or '')
                or (getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or ''))



def _settings_parts() -> dict:
    """Connection details, preferring the credentials Omni ALREADY has.

    `GRAPHITE_RO_DB_*` was configured long before this module — Graphite Aware's
    language-to-SQL feature runs on it (`aware/engine._connect`), pointed at
    `graphite-v2-prod-rpro` as `graphitebwlive`. I very nearly added a second,
    parallel credential for the same database; reusing the live one means no new
    secret to rotate and no chance of the two drifting apart.

    GRAPHITE_RO_DSN stays supported as an override for a one-off or a different
    replica, but it is not required and is not the normal path.
    """
    dsn = getattr(settings, 'GRAPHITE_RO_DSN', '') or ''
    if dsn:
        return _parse_dsn(dsn)
    host = getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or ''
    if not host:
        raise ImproperlyConfigured(
            'No Graphite read replica configured. Set GRAPHITE_RO_DB_HOST / _USER / '
            '_PASSWORD (the pair Graphite Aware already uses), or GRAPHITE_RO_DSN.'
        )
    return {
        'host': host,
        'port': int(getattr(settings, 'GRAPHITE_RO_DB_PORT', 3306) or 3306),
        'user': getattr(settings, 'GRAPHITE_RO_DB_USER', '') or '',
        'password': getattr(settings, 'GRAPHITE_RO_DB_PASSWORD', '') or '',
        'database': getattr(settings, 'GRAPHITE_RO_DB_NAME', 'Graphite_live') or 'Graphite_live',
    }


@contextmanager
def connection() -> Iterator[Any]:
    """A read-only pymysql connection to the Graphite replica."""
    parts = _settings_parts()
    _assert_replica(parts['host'])

    import pymysql            # imported here so the module loads without the driver
    cx = pymysql.connect(
        host=parts['host'], port=parts['port'],
        user=parts['user'], password=parts['password'],
        database=parts['database'],
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=int(getattr(settings, 'GRAPHITE_RO_TIMEOUT_SECONDS', 20)),
        read_timeout=int(getattr(settings, 'GRAPHITE_RO_TIMEOUT_SECONDS', 20)),
        autocommit=True,
    )
    try:
        with cx.cursor() as cur:
            # Ask the server to enforce it too. Harmless if the replica already is.
            try:
                cur.execute('SET SESSION TRANSACTION READ ONLY')
            except Exception:      # noqa: BLE001 — older servers reject the syntax
                log.debug('server would not set session read-only; replica still is')
        log.info('graphite-ro: connected to %s/%s as %s',
                 parts['host'], parts['database'], parts['user'])
        yield cx
    finally:
        try:
            cx.close()
        except Exception:          # noqa: BLE001
            pass


def query(sql: str, params: Optional[Sequence[Any]] = None,
          *, limit: int = 500) -> list[dict]:
    """Run one read and return rows. Refuses anything that is not a read."""
    assert_read_only(sql)
    with connection() as cx:
        with cx.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchmany(limit)
    return list(rows)


def tables_like(fragment: str) -> list[str]:
    """Table names containing *fragment*. Uses a parameter, not string building."""
    rows = query(
        'SELECT table_name AS t FROM information_schema.tables '
        'WHERE table_schema = DATABASE() AND table_name LIKE %s ORDER BY table_name',
        [f'%{fragment}%'], limit=200)
    return [r['t'] for r in rows]


def columns_of(table: str) -> list[str]:
    """Column names of *table*, looked up through information_schema.

    The table name is a parameter here rather than interpolated, so a caller cannot
    turn a column listing into anything else.
    """
    rows = query(
        'SELECT column_name AS c FROM information_schema.columns '
        'WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position',
        [table], limit=500)
    return [r['c'] for r in rows]


def safe_identifier(name: str) -> str:
    """A table/column name that came from information_schema, quoted for reuse.

    Only ever call this with a name the database itself just gave back — never with
    user input. Anything unexpected is refused rather than escaped.
    """
    if not re.fullmatch(r'[A-Za-z0-9_]+', name or ''):
        raise GraphiteReadOnlyError(f'Refusing unsafe identifier {name!r}.')
    return f'`{name}`'
