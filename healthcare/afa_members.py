"""
healthcare/afa_members.py — Phase 1 of the ADH → AFA load file.

READ-ONLY reader for the ADH group-health membership held in Graphite.

Omni does not hold the members; Graphite does. Rather than deploy anything to
Graphite (213k live policies, no isolated test database), this reads the same
SELECT-only replica connection `integrations/graphite_age.py` already uses in
production — `GRAPHITE_RO_DB_*`, pointed at the `-rpro` read replica, which
rejects writes at the server. Nothing here writes anywhere.

EVERY COLUMN NAME BELOW WAS READ OUT OF THE LIVE SCHEMA, not from the Laravel
models. They disagree: `EmployerGroupPolicy::$fillable` claims
`waiting_effective_from` while the actual column is `waitingeffectivefrom`.
Trusting the model would have silently produced empty waiting periods.

DATA PROTECTION (AD-POL-AI-GOV-001, not waivable): every row this returns is
policyholder data — names, Omang, DOB, bank accounts. It is assembled in
process, written to the load file, and sent to AFA (the scheme's own
administrator) and nowhere else. It is never logged, never printed, and never
sent to any external model. Log line counts, never row contents.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

from django.conf import settings

log = logging.getLogger('afa-loadfile')


class GraphiteReplicaNotConfigured(Exception):
    """The Graphite read-replica env vars aren't set."""


# Graphite policy.status codes, per Policy::statusCount() in the Graphite repo.
STATUS_DEACTIVATED = 0
STATUS_ACTIVATED   = 1
STATUS_CANCELLED   = 2
STATUS_EXPIRED     = 3

# Which statuses count as "on cover" for AFA purposes.
#
# UNRESOLVED — see the plan's Phase 0. On 7-Aug-2026 all 293 live ADH group
# policies sat at status 0 with a populated policyActivatedDate and ZERO at
# status 1, while the book as a whole runs 108,670 at 0 / 30,704 at 1 / 74,071
# at 2. So 0 cannot simply mean "dead" here, but nor is it proven to mean "on
# cover". Deliberately a setting, defaulting to the observed reality, so the
# answer is one env change and not a code hunt. Do NOT hardcode this.
AFA_ON_COVER_STATUSES = tuple(
    getattr(settings, 'AFA_ON_COVER_STATUSES', (STATUS_DEACTIVATED, STATUS_ACTIVATED))
)

# AFA's "Option" column. Verified against the live data 7-Aug-2026: the five
# product_plans carrying ADH group policies are named exactly 'AD Lite',
# 'AD Essential', 'AD Core', 'AD Premier', 'AD Status' — byte-identical to
# AFA's closed list, so the plan name passes straight through with no mapping.
AFA_PLAN_NAMES = {'AD Lite', 'AD Essential', 'AD Core', 'AD Premier', 'AD Status'}


_PRINCIPALS_SQL = """
    SELECT
        p.id                        AS policy_id,
        p.policyNumber              AS policy_number,
        p.status                    AS policy_status,
        p.policyActivatedDate       AS activated_date,
        eg.employer_group_id        AS employer_group_id,
        eg.name                     AS graphite_group_name,
        eg.status                   AS group_status,
        egp.employee_id             AS employee_number,
        egp.policystartdate         AS policy_start_date,
        egp.waitingeffectivefrom    AS waiting_from,
        egp.waitingeffectiveto      AS waiting_to,
        pp.name                     AS plan_name,
        c.firstName                 AS first_name,
        c.lastName                  AS surname,
        c.email                     AS email,
        c.cellphone                 AS cellphone,
        cp.gender                   AS gender,
        cp.dob                      AS dob,
        cp.omang                    AS id_number,
        cp.passport                 AS passport,
        cp.plot_number              AS address_1,
        cp.address                  AS address_2,
        cp.second_address           AS address_3,
        cp.city                     AS town,
        cb.billing                  AS account_holder,
        cb.bankName                 AS bank_name,
        cb.branchCode               AS branch_code,
        cb.accountNumber            AS account_number,
        cb.accountType              AS account_type
    FROM employer_group_policy egp
    JOIN policies        p  ON p.id  = egp.policy_id
    JOIN employer_groups eg ON eg.employer_group_id = egp.employer_group_id
    LEFT JOIN product_plans   pp ON pp.id = p.plan_id
    LEFT JOIN customer        c  ON c.id  = p.customer_id
    LEFT JOIN customer_profile cp ON cp.customer_id = p.customer_id
    LEFT JOIN customer_banking cb ON cb.policy_id   = p.id
    ORDER BY egp.employer_group_id, p.policyNumber, p.id
"""

# Dependants hang off the policy, not the customer. There is no dependant
# NUMBER in Graphite — only a row order — which is exactly why the load-file
# engine assigns numbers from its own snapshot and never from position here
# (checklist H20: a departure must never renumber the survivors).
_DEPENDANTS_SQL = """
    SELECT
        b.id            AS beneficiary_id,
        b.policy_id     AS policy_id,
        b.person_type   AS person_type,
        b.relation      AS relation,
        b.first_name    AS first_name,
        b.last_name     AS surname,
        b.dob           AS dob,
        b.gender        AS gender,
        b.omang         AS id_number,
        b.passport      AS passport,
        b.email         AS email,
        b.cellphone     AS cellphone,
        b.address       AS address_2,
        b.city          AS town,
        b.status        AS status
    FROM ad_grouped_policy_beneficiary b
    ORDER BY b.policy_id, b.id
"""


def _config() -> Dict[str, Any]:
    host = getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or ''
    if not host:
        raise GraphiteReplicaNotConfigured(
            'Graphite read replica not configured. Set GRAPHITE_RO_DB_HOST and '
            'the GRAPHITE_RO_DB_* credentials in /etc/alpha-finance/.env — and '
            'make sure they are also listed in docker-compose.yml (checklist '
            'H24: a var in .env but absent from compose arrives EMPTY).'
        )
    return {
        'host':     host,
        'port':     int(getattr(settings, 'GRAPHITE_RO_DB_PORT', 3306) or 3306),
        'user':     getattr(settings, 'GRAPHITE_RO_DB_USER', '') or '',
        'password': getattr(settings, 'GRAPHITE_RO_DB_PASSWORD', '') or '',
        'database': getattr(settings, 'GRAPHITE_RO_DB_NAME', 'Graphite_live') or 'Graphite_live',
        'timeout':  int(getattr(settings, 'GRAPHITE_RO_DB_TIMEOUT', 20) or 20),
    }


def is_configured() -> bool:
    return bool(getattr(settings, 'GRAPHITE_RO_DB_HOST', '') or '')


def _connect():
    import pymysql
    from pymysql.cursors import DictCursor

    cfg = _config()
    return pymysql.connect(
        host=cfg['host'], port=cfg['port'], user=cfg['user'],
        password=cfg['password'], database=cfg['database'],
        connect_timeout=cfg['timeout'], read_timeout=cfg['timeout'] * 6,
        cursorclass=DictCursor,
    )


def replica_lag_seconds() -> Optional[int]:
    """Seconds this replica is behind its primary, or None if unknown.

    Checked before a load-file run trusts the pull: a badly lagged replica
    reports yesterday's membership as today's, and a member cancelled this
    morning would still be sent as on cover.
    """
    try:
        with _connect() as con:
            with con.cursor() as cur:
                cur.execute('SHOW SLAVE STATUS')
                row = cur.fetchone()
                if not row:
                    return None
                for key in ('Seconds_Behind_Master', 'Seconds_Behind_Source'):
                    if key in row and row[key] is not None:
                        return int(row[key])
    except Exception as exc:                       # noqa: BLE001 - diagnostic only
        log.warning('replica lag check failed: %s', exc.__class__.__name__)
    return None


def fetch_membership() -> Dict[str, Any]:
    """Read the whole ADH group membership off the replica in two queries.

    Two queries, not one per policy — the dependants are fetched in a single
    pass and bucketed in memory (checklist K5: no N+1 against a 213k-policy
    database).

    Returns {'principals': [...], 'dependants_by_policy': {policy_id: [...]},
             'columns_seen': {...}} — raw rows, no AFA formatting. Shaping the
    35 columns is the load-file engine's job, not the reader's.
    """
    with _connect() as con:
        with con.cursor() as cur:
            cur.execute(_PRINCIPALS_SQL)
            principals: List[Dict[str, Any]] = list(cur.fetchall())
            cur.execute(_DEPENDANTS_SQL)
            dependant_rows: List[Dict[str, Any]] = list(cur.fetchall())

    by_policy: Dict[Any, List[Dict[str, Any]]] = {}
    for row in dependant_rows:
        by_policy.setdefault(row['policy_id'], []).append(row)

    log.info(
        'afa: replica read %s principals, %s dependants across %s policies',
        len(principals), len(dependant_rows), len(by_policy),
    )
    return {
        'principals': principals,
        'dependants_by_policy': by_policy,
        'columns_seen': set(principals[0].keys()) if principals else set(),
    }


def on_cover(row: Dict[str, Any]) -> bool:
    """Is this principal currently on cover, per the configured status set."""
    return row.get('policy_status') in AFA_ON_COVER_STATUSES
