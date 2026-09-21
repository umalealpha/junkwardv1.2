"""
healthcare/register_counts.py — COUNT-ONLY reader for the ADH group register.

Why a second reader when afa_members.py already reads the register: that one
SELECTs 29 columns per life (names, Omang, DOB, bank account) because the AFA
load file needs them. A dashboard needs none of it — only how many lives, in
which employer group, at which cover status. So this module selects counts and
group names and nothing else: no name, no Omang, no address, no bank detail
ever leaves the database (AD-POL-AI-GOV-001). It also keeps the page fast —
five aggregate rows instead of 293 wide rows plus 29 dependant rows.

Reuses afa_members._config() / AFA_ON_COVER_STATUSES so there is one replica
config and one definition of "on cover", not two — but opens the connection
itself, on dashboard timeouts (see CONNECT_TIMEOUT_SECONDS).

WHAT GRAPHITE DOES NOT HOLD (verified against the live schema 8-Sep-2026):
  - no cancellation REASON column anywhere on policies / employer_group_policy
    / ad_grouped_policy_beneficiary;
  - no cancellation DATE — the only dates on policies are billingStartDate,
    policyActivatedDate, expiry_date, trial_period, created_at, updated_at.
So reasons_available is False and in_month is None. The dashboard prints that
as "not captured in the source system" rather than inventing a breakdown.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from healthcare.afa_members import (  # noqa: F401 - re-exported for callers
    AFA_ON_COVER_STATUSES,
    STATUS_CANCELLED,
    GraphiteReplicaNotConfigured,
    _config,
    is_configured,
)

log = logging.getLogger("afa-loadfile")


# Principals per employer group per policy status. One row per (group, status).
_PRINCIPAL_COUNTS_SQL = """
    SELECT
        eg.employer_group_id AS employer_group_id,
        eg.name              AS group_name,
        p.status             AS policy_status,
        COUNT(*)             AS lives
    FROM employer_group_policy egp
    JOIN policies        p  ON p.id  = egp.policy_id
    JOIN employer_groups eg ON eg.employer_group_id = egp.employer_group_id
    GROUP BY eg.employer_group_id, eg.name, p.status
"""

# Dependants hang off the policy. Joined back through employer_group_policy so
# only ADH GROUP dependants are counted, never the retail book.
_DEPENDANT_COUNTS_SQL = """
    SELECT
        eg.employer_group_id AS employer_group_id,
        eg.name              AS group_name,
        p.status             AS policy_status,
        b.status             AS dependant_status,
        COUNT(*)             AS lives
    FROM ad_grouped_policy_beneficiary b
    JOIN employer_group_policy egp ON egp.policy_id = b.policy_id
    JOIN policies        p  ON p.id  = egp.policy_id
    JOIN employer_groups eg ON eg.employer_group_id = egp.employer_group_id
    GROUP BY eg.employer_group_id, eg.name, p.status, b.status
"""

_DEP_ACTIVE = "active"

# Beyond this the replica is reporting yesterday's membership as today's — the
# same trap the load-file engine already guards against. 15 minutes.
MAX_REPLICA_LAG_SECONDS = 15 * 60

# A dashboard waits seconds, not half a minute. afa_members._connect() allows 20s
# to connect and 120s to read, which is correct for the nightly load-file run and
# wrong for a screen: with the replica unreachable the page sat on "Loading…" for
# 20.1s before it could even say the lives were missing. These ceilings are the
# whole reason this module opens its own connection instead of reusing that one.
CONNECT_TIMEOUT_SECONDS = 4
READ_TIMEOUT_SECONDS = 8


def _connect_fast():
    """The replica, on dashboard timeouts. Same credentials, shorter patience."""
    import pymysql
    from pymysql.cursors import DictCursor

    cfg = _config()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        cursorclass=DictCursor,
    )


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _lag_on(cur) -> Optional[int]:
    """Seconds this replica is behind its primary, read on the open cursor.

    None when the replica will not say. Never raises: a lag it cannot report is
    not a reason to withhold the counts it just gave us.
    """
    try:
        cur.execute("SHOW SLAVE STATUS")
        row = cur.fetchone() or {}
        for key in ("Seconds_Behind_Master", "Seconds_Behind_Source"):
            if row.get(key) is not None:
                return int(row[key])
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        log.warning("replica lag unreadable: %s", exc.__class__.__name__)
    return None


def _classify(exc: Exception) -> str:
    """Connection trouble or our own bug? The caller degrades either way, but
    the reason must not read the same — a SQL typo reported as "replica
    unavailable" is a bug hidden behind an infrastructure excuse."""
    try:
        import pymysql
    except ImportError:
        return 'replica driver missing'
    if isinstance(exc, (pymysql.err.OperationalError, pymysql.err.InterfaceError)):
        return 'replica unreachable'
    if isinstance(exc, pymysql.err.ProgrammingError):
        log.error('register counts SQL is wrong: %s', exc)
        return 'internal error reading the register'
    log.error('register counts failed unexpectedly: %s', exc.__class__.__name__)
    return 'internal error reading the register'


def register_counts() -> Dict[str, Any]:
    """Lives on the ADH group register, counted on the replica.

    Returns
      {'available': True,
       'policyholders': n, 'dependants': n, 'active_lives': n,
       'cancelled_policyholders': n, 'cancelled_dependants': n,
       'cancelled_lives': n,
       'by_group': [{'group': name, 'lives': n, 'in_month': None,
                     'main_reason': None, 'cancelled_lives': n}],
       'reasons_available': False, 'groups': n}

    Never raises: a replica that is down or unconfigured returns
    {'available': False, 'reason': '<short cause>'} so the rest of the
    dashboard still renders (the brief's "Omni must keep working" rule).
    """
    if not is_configured():
        return {"available": False, "reason": "replica not configured"}

    try:
        with _connect_fast() as con:
            with con.cursor() as cur:
                cur.execute(_PRINCIPAL_COUNTS_SQL)
                principal_rows: List[Dict[str, Any]] = list(cur.fetchall())
                cur.execute(_DEPENDANT_COUNTS_SQL)
                dependant_rows: List[Dict[str, Any]] = list(cur.fetchall())
                lag = _lag_on(cur)
    except Exception as exc:  # noqa: BLE001 - degrade, never 500
        return {"available": False, "reason": _classify(exc)}

    # H25 fence: an empty or badly lagged replica must not be printed as fact.
    # The ADH book has never been empty, so no rows is not a real answer of
    # "zero lives" — it reads as unavailable.
    if not principal_rows:
        log.warning("register counts: replica returned no rows")
        return {"available": False, "reason": "register read came back empty"}
    if lag is not None and lag > MAX_REPLICA_LAG_SECONDS:
        log.warning("register counts: replica %ss behind", lag)
        return {
            "available": False,
            "reason": f"register copy is {lag}s behind - too stale to show",
            "replica_lag_seconds": lag,
        }

    on_cover = set(AFA_ON_COVER_STATUSES)
    groups: Dict[Any, Dict[str, Any]] = {}

    def bucket(row: Dict[str, Any]) -> Dict[str, Any]:
        gid = row.get("employer_group_id")
        g = groups.get(gid)
        if g is None:
            g = groups[gid] = {
                "group": (row.get("group_name") or "").strip() or f"Group {gid}",
                "policyholders": 0,
                "dependants": 0,
                "cancelled_policyholders": 0,
                "cancelled_dependants": 0,
            }
        return g

    for row in principal_rows:
        g = bucket(row)
        n, status = _int(row.get("lives")), row.get("policy_status")
        if status in on_cover:
            g["policyholders"] += n
        elif status == STATUS_CANCELLED:
            g["cancelled_policyholders"] += n

    for row in dependant_rows:
        g = bucket(row)
        n, status = _int(row.get("lives")), row.get("policy_status")
        dep_active = (row.get("dependant_status") or "").strip().lower() == _DEP_ACTIVE
        if status in on_cover and dep_active:
            g["dependants"] += n
        elif status == STATUS_CANCELLED:
            g["cancelled_dependants"] += n

    policyholders = sum(g["policyholders"] for g in groups.values())
    dependants = sum(g["dependants"] for g in groups.values())
    cancelled_ph = sum(g["cancelled_policyholders"] for g in groups.values())
    cancelled_dep = sum(g["cancelled_dependants"] for g in groups.values())

    by_group = [
        {
            "group": g["group"],
            "lives": g["cancelled_policyholders"] + g["cancelled_dependants"],
            # Graphite records no cancellation date, so "dated in the month"
            # cannot be derived. None renders as "—", never as 0.
            "in_month": None,
            "main_reason": None,
            "active_lives": g["policyholders"] + g["dependants"],
        }
        for g in groups.values()
    ]
    by_group = [g for g in by_group if g["lives"] or g["active_lives"]]
    by_group.sort(key=lambda g: (-g["lives"], -g["active_lives"], g["group"]))

    log.info(
        "register counts: %s policyholders, %s dependants, %s groups",
        policyholders,
        dependants,
        len(groups),
    )
    return {
        "available": True,
        "policyholders": policyholders,
        "dependants": dependants,
        "active_lives": policyholders + dependants,
        "cancelled_policyholders": cancelled_ph,
        "cancelled_dependants": cancelled_dep,
        "cancelled_lives": cancelled_ph + cancelled_dep,
        "by_group": by_group,
        "groups": len(groups),
        "replica_lag_seconds": lag,
        # No reason column exists upstream — see the module docstring.
        "reasons_available": False,
    }

