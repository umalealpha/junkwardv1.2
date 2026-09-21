"""
integrations/graphite_age.py

READ-ONLY reader for the Graphite V2 age-analysis (premium debtors aging).

omni-only integration (CFO directive 2026-06-16): omni opens a SELECT-only
connection to the Graphite BW read replica and reads the pre-aggregated aging
table that Graphite's nightly cron populates. Nothing is changed on Graphite.

Source (all via env, values in /etc/alpha-finance/.env — never in code):
  GRAPHITE_RO_DB_HOST   graphite-v2-prod-rpro.<...>.af-south-1.rds.amazonaws.com  (read replica)
  GRAPHITE_RO_DB_PORT   3306
  GRAPHITE_RO_DB_NAME   Graphite_live
  GRAPHITE_RO_DB_USER   (read user)
  GRAPHITE_RO_DB_PASSWORD
  GRAPHITE_AGE_TABLE    summary_age_analyst_report_dom_com_2025  (year-suffixed; override yearly)

The connection is opened read-only and only ever issues SELECT. The replica
endpoint (-rpro) rejects writes at the server anyway. Verified 2026-06-16:
table has ~3,820 rows.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.conf import settings

log = logging.getLogger(__name__)


class GraphiteAgeNotConfigured(Exception):
    """Raised when the Graphite read-replica env vars aren't set."""


# Columns as they exist in the Graphite aging table → the names we expose.
# Mirrors the live Graphite reporting query (do not rename source columns).
_SELECT = """
    SELECT
        policyNumber                     AS policy_number,
        client_name                      AS client_name,
        agent_name                       AS agent_name,
        invoice_total                    AS invoice_total,
        payment_total                    AS payment_total,
        refund_total                     AS refund_total,
        balance_outstanding              AS balance_outstanding,
        `30_days`                        AS days_30,
        `60_days`                        AS days_60,
        `90_days`                        AS days_90,
        `120_days_and_above`             AS days_120_plus,
        premium_freq                     AS frequency,
        product_name                     AS product_name,
        policy_status                    AS policy_status,
        policy_created_at                AS policy_created_at
    FROM {table}
"""


def _config() -> Dict[str, Any]:
    host = getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or ''
    if not host:
        raise GraphiteAgeNotConfigured(
            'Graphite read-replica not configured. Set GRAPHITE_RO_DB_HOST '
            'and the GRAPHITE_RO_DB_* credentials in the env.'
        )
    return {
        'host':     host,
        'port':     int(getattr(settings, 'GRAPHITE_RO_DB_PORT', 3306) or 3306),
        'user':     getattr(settings, 'GRAPHITE_RO_DB_USER', '') or '',
        'password': getattr(settings, 'GRAPHITE_RO_DB_PASSWORD', '') or '',
        'database': getattr(settings, 'GRAPHITE_RO_DB_NAME', 'Graphite_live') or 'Graphite_live',
        'table':    getattr(settings, 'GRAPHITE_AGE_TABLE',
                            'summary_age_analyst_report_dom_com_2025'),
        'timeout':  int(getattr(settings, 'GRAPHITE_RO_DB_TIMEOUT', 20) or 20),
    }


def is_configured() -> bool:
    return bool(getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or '')


def fetch_age_analysis(
    *,
    search: str = '',
    status: str = '',
    limit: int = 20000,
) -> List[Dict[str, Any]]:
    """SELECT the aging rows from the Graphite replica (read-only).

    Optional server-side filters keep the payload small:
      search  — LIKE on policyNumber / client_name
      status  — exact policy_status
    """
    import pymysql

    cfg = _config()
    where: List[str] = []
    params: List[Any] = []
    if search:
        where.append('(policyNumber LIKE %s OR client_name LIKE %s)')
        params += [f'%{search}%', f'%{search}%']
    if status:
        where.append('policy_status = %s')
        params.append(status)

    sql = _SELECT.format(table=cfg['table'])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY balance_outstanding DESC LIMIT %s'
    params.append(int(limit))

    conn = pymysql.connect(
        host=cfg['host'], port=cfg['port'], user=cfg['user'],
        password=cfg['password'], database=cfg['database'],
        connect_timeout=cfg['timeout'], read_timeout=cfg['timeout'],
        cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4',
        # Defensive: session is read-only; the -rpro replica rejects writes anyway.
        init_command='SET SESSION TRANSACTION READ ONLY',
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()
