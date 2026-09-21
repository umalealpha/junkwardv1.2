"""realpay/graphite_feed.py — LIVE RealPay collections read straight from Graphite.

Why this exists: Omni's own RealPay API pull (realpay/client.py) depends on a
vendor credential that RealPay had not activated for production, so the monthly
reports went stale. Graphite — the core policy system — already collects every
debit order through RealPay and stores each instalment in
``realpay_contract_installments``. That table is the source of truth for what
actually collected, and Omni already has a read-only window onto it via
``integrations.graphite_ro`` (SELECT-only, replica-only, guard-tested).

So this module reads the collection totals live on every request: no vendor key,
no copied credential, no stored snapshot to go stale, and — crucially — no mixing
into Omni's uploaded ``RealPayTransaction`` rows (that would double-count the
months already loaded from the RealPay portal Excel). It is a separate, clearly
labelled "live from Graphite" view that reconciles to source by construction.

Status codes are Graphite's own (confirmed from Graphite source, not guessed):
  S=Success(collected) · F=Failed · W=Processing · R=Retry · A=Scheduled ·
  I=Cancelled · E=Error.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

from integrations import graphite_ro
from django.utils import timezone

log = logging.getLogger(__name__)

# Bounds so a mistyped query param can never scan the full 6.6M-row table blindly.
_MIN_MONTHS = 1
_MAX_MONTHS = 36


def _f(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def monthly_collections(months: int = 6) -> Dict[str, Any]:
    """Per-month collection totals from Graphite, newest month first.

    Returns a JSON-safe dict:
      { configured, months, rows: [{ym, collected_count, collected_amount,
        failed_count, failed_amount, processing_count}], totals: {...},
        last30: {collected_count, collected_amount, failed_count} }
    ``configured=False`` when the Graphite read-only bridge is not wired (dev),
    so the caller renders "not available" instead of erroring.
    """
    if not graphite_ro.is_configured():
        return {'configured': False, 'months': months, 'rows': [], 'totals': {},
                'last30': {}}

    months = max(_MIN_MONTHS, min(_MAX_MONTHS, int(months)))

    # Only rows with an action date in the window and not in the future — future
    # rows are Scheduled mandates (status 'A'), not collections.
    monthly_sql = """
        SELECT DATE_FORMAT(created_at, %s) AS ym,
               SUM(InstalmentStatus = %s)                                            AS collected_count,
               SUM(CASE WHEN InstalmentStatus = %s THEN InstalmentAmount ELSE 0 END) AS collected_amount,
               SUM(InstalmentStatus = %s)                                            AS failed_count,
               SUM(CASE WHEN InstalmentStatus = %s THEN InstalmentAmount ELSE 0 END) AS failed_amount,
               SUM(InstalmentStatus = %s)                                            AS processing_count
        FROM realpay_contract_installments
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)
          AND created_at <  DATE_ADD(CURDATE(), INTERVAL 1 DAY)
        GROUP BY ym
        ORDER BY ym DESC
    """
    rows_raw = graphite_ro.query(
        monthly_sql, ['%Y-%m', 'S', 'S', 'F', 'F', 'W', months])

    rows: List[Dict[str, Any]] = []
    tot_c_cnt = tot_c_amt = tot_f_cnt = tot_f_amt = 0
    for r in rows_raw:
        cc = int(r['collected_count'] or 0)
        ca = _f(r['collected_amount'])
        fc = int(r['failed_count'] or 0)
        fa = _f(r['failed_amount'])
        rows.append({
            'ym': r['ym'],
            'collected_count': cc,
            'collected_amount': round(ca, 2),
            'failed_count': fc,
            'failed_amount': round(fa, 2),
            'processing_count': int(r['processing_count'] or 0),
        })
        tot_c_cnt += cc; tot_c_amt += ca; tot_f_cnt += fc; tot_f_amt += fa

    last30_sql = """
        SELECT SUM(InstalmentStatus = %s)                                            AS collected_count,
               SUM(CASE WHEN InstalmentStatus = %s THEN InstalmentAmount ELSE 0 END) AS collected_amount,
               SUM(InstalmentStatus = %s)                                            AS failed_count
        FROM realpay_contract_installments
        WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
          AND created_at <  DATE_ADD(CURDATE(), INTERVAL 1 DAY)
    """
    l30 = graphite_ro.query(last30_sql, ['S', 'S', 'F'])
    l30row = l30[0] if l30 else {}

    return {
        'configured': True,
        'months': months,
        'rows': rows,
        'totals': {
            'collected_count': tot_c_cnt,
            'collected_amount': round(tot_c_amt, 2),
            'failed_count': tot_f_cnt,
            'failed_amount': round(tot_f_amt, 2),
        },
        'last30': {
            'collected_count': int((l30row.get('collected_count') or 0)),
            'collected_amount': round(_f(l30row.get('collected_amount')), 2),
            'failed_count': int((l30row.get('failed_count') or 0)),
        },
        'source': 'graphite.realpay_contract_installments (live, read-only)',
    }


# Statuses that represent an actual debit ATTEMPT (money was presented). Scheduled
# mandates ('A', future-dated) and cancelled mandates ('I') were never attempted,
# so they are excluded from "attempts" — this makes the live figures line up with
# what RealPay's own billing report treats as an attempt.
_ATTEMPT_STATUSES = ('S', 'F', 'W', 'R', 'E')
_COLLECTED = 'S'
#: A mandate row RAISED but never actioned. On a date already past this is not
#: "scheduled" — it is a debit whose result never came back.
_SCHEDULED = 'A'


# KNOWN CAVEAT (timezone): created_at is stored ISO in UTC, and the money windows
# below compare it against a plain 'YYYY-MM-DD' local date string. A collection
# written 00:00-02:00 CAT lands on the previous UTC day, so a handful of
# near-midnight rows can fall in the adjacent reporting month. Left as a plain
# string compare for now: it matches every other read in this module and the
# uploaded RealPay Client Billing report is the reconciled month-boundary source
# of record. Revisit with CONVERT_TZ if a month must tie to the second.
def _iso_start(d) -> str:
    """A date -> the string boundary that selects rows on/after that day.
    created_at (the real RealPay collection time) is stored ISO, so a plain
    'YYYY-MM-DD' compares correctly by string ordering."""
    return d.isoformat()


def collections_by_group(start=None, end=None) -> Dict[str, Any]:
    """LIVE collected totals grouped by the leading-letter run of clientNumber,
    read straight from Graphite. The caller maps each prefix to a product label
    with the SAME classifier the uploaded-file dashboard uses, so the split is
    identical — only the source (live Graphite vs stale Excel) changes.

    ``start``/``end`` are date objects (both inclusive); either may be None.
    Future-dated rows are always excluded (capped at today) so a mistyped mandate
    date can never inflate the total. Returns ``configured=False`` in dev where the
    read bridge is not wired, so the caller falls back to uploaded rows."""
    if not graphite_ro.is_configured():
        return {'configured': False}

    where = ['created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)']
    # NB: the SELECT placeholders in order — prefix regex, the five attempt
    # statuses, collected (lines), 'A' (raised but no result), collected (amount).
    params: List[Any] = ['^[A-Z]+', *_ATTEMPT_STATUSES, _COLLECTED, _SCHEDULED, _COLLECTED]
    # NB: the SELECT placeholders (prefix regex, attempt statuses, collected x2)
    # come first; the WHERE date params are appended after in query order below.
    date_params: List[Any] = []
    if start is not None:
        where.append('created_at >= %s')
        date_params.append(_iso_start(start))
    if end is not None:
        # end inclusive: strictly-before the day after `end`.
        import datetime as _dt
        where.append('created_at < %s')
        date_params.append(_iso_start(end + _dt.timedelta(days=1)))

    sql = f"""
        SELECT REGEXP_SUBSTR(UPPER(clientNumber), %s)                                 AS pref,
               SUM(InstalmentStatus IN (%s, %s, %s, %s, %s))                          AS attempts,
               SUM(InstalmentStatus = %s)                                             AS collected_lines,
               SUM(InstalmentStatus = %s)                                             AS awaiting_result,
               ROUND(SUM(CASE WHEN InstalmentStatus = %s
                              THEN CAST(InstalmentAmount AS DECIMAL(15,2)) ELSE 0 END), 2) AS collected_amount
        FROM realpay_contract_installments
        WHERE {' AND '.join(where)}
        GROUP BY pref
    """
    # GROUP BY the leading-letter run, so one row per client-number prefix —
    # five on real data (MIS/COM/COMG/DOM/DOMG). The explicit limit is the
    # same guard as collected_rows: graphite_ro.query defaults to 500, and a
    # silent cap on THIS query would understate a money figure Finance reads.
    # 1000 is far above any plausible prefix count, so it can only ever bite
    # on corrupt data — in which case the total is already untrustworthy.
    raw = graphite_ro.query(sql, params + date_params, limit=1000)

    out = []
    for r in raw:
        out.append({
            'pref': (r.get('pref') or '').strip().upper(),
            'attempts': int(r.get('attempts') or 0),
            'collected_lines': int(r.get('collected_lines') or 0),
            # Debits RAISED in the window that never came back with a result.
            # Reported so the caller can tell "nothing was collected" apart from
            # "we were never told what happened" — they are not the same fact and
            # a dashboard must never render them the same way.
            'awaiting_result': int(r.get('awaiting_result') or 0),
            'collected_amount': _f(r.get('collected_amount')),
        })
    return {'configured': True, 'rows': out}


def client_names(client_numbers, chunk: int = 1000) -> Dict[str, str]:
    """{clientNumber: display name} from Graphite, for the collections export.

    RealPay's clientNumber IS the Graphite policyNumber (see recon.py — getting
    that join backwards once made 95% of a real file look like debits against
    unknown policies). Personal policies carry a customer first/last name;
    corporate ones carry ``business_name`` — prefer the business name when set.

    PII (DPA No.18/2024 + AD-POL-AI-GOV-001): names are returned ONLY to the
    finance-permissioned CSV export the CFO authorised (2026-09-08). They must
    never reach an AI prompt, a log line, or a scheduled email.

    Looked up in chunks with an explicit row limit — ``graphite_ro.query``
    defaults to 500 rows, so an omitted limit silently truncates.
    """
    if not graphite_ro.is_configured():
        return {}
    wanted = sorted({(c or '').strip() for c in client_numbers if (c or '').strip()})
    out: Dict[str, str] = {}
    for i in range(0, len(wanted), chunk):
        part = wanted[i:i + chunk]
        ph = ','.join(['%s'] * len(part))
        sql = (
            "SELECT p.policyNumber AS pn, "
            "       TRIM(COALESCE(NULLIF(TRIM(p.business_name), ''), "
            "                     CONCAT_WS(' ', cu.firstName, cu.lastName))) AS nm "
            "FROM policies p LEFT JOIN customer cu ON p.customer_id = cu.id "
            f"WHERE p.policyNumber IN ({ph})"
        )
        for r in (graphite_ro.query(sql, tuple(part), limit=chunk) or []):
            pn = (r.get('pn') or '').strip()
            nm = (r.get('nm') or '').strip()
            if pn and nm and pn not in out:
                out[pn] = nm
    return out


def collected_rows(start=None, end=None, limit: int = 200000):
    """Per-instalment COLLECTED rows (clientNumber, action date, amount) for the
    CSV export, live from Graphite. Same date window and future-cap as
    ``collections_by_group``.
    Bounded by ``limit`` so an unfiltered export can never stream the full table.
    ``limit`` MUST be passed through to ``graphite_ro.query`` — its own default is
    500 rows, and omitting it silently truncated every export to 500 lines while
    the CSV footer still showed the full month total, so the file could never
    reconcile (Bokani, bug d4e6d54a, 2026-09-08)."""
    if not graphite_ro.is_configured():
        return None
    where = ["InstalmentStatus = %s",
             "created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"]
    params: List[Any] = [_COLLECTED]
    if start is not None:
        where.append('created_at >= %s')
        params.append(_iso_start(start))
    if end is not None:
        import datetime as _dt
        where.append('created_at < %s')
        params.append(_iso_start(end + _dt.timedelta(days=1)))
    sql = f"""
        SELECT clientNumber AS cn,
               InstalmentActionDate AS action_date,
               ROUND(CAST(InstalmentAmount AS DECIMAL(15,2)), 2) AS amt
        FROM realpay_contract_installments
        WHERE {' AND '.join(where)}
        ORDER BY created_at
        LIMIT {int(limit)}
    """
    return graphite_ro.query(sql, params, limit=limit)


# ─── Broker commission feed (CFO 2026-09-08) ─────────────────────────────────
# Brokers write COMMERCIAL and DOMESTIC business only, never Instant (CFO rule,
# 2026-09-08). Confirmed on live data: everything carrying a MIS policy number
# under an agency is a lead/direct-marketing channel (108 Media, MARKET SA,
# Riverwalk), not a broker we pay commission to. Filtering on the policy-number
# prefix therefore excludes those channels with no hand-kept exclusion list.
_BROKER_PREFIXES = ('COM', 'COMG', 'DOM', 'DOMG')

#: Human labels for the instalment codes, plus the dirty values that are really
#: on the table: 343 rows say 'CANCELLED', 12 say 'SUCCESS', and there is one
#: each of 'SUCCESSFUL' and 'FAILED(FAILED-73)'. Normalising here keeps a broker
#: from being paid on a mis-read status.
def _today_iso() -> str:
    return timezone.localdate().isoformat()


_STATUS_LABEL = {
    'S': 'Successful', 'F': 'Failed', 'W': 'Processing', 'R': 'Retry',
    'A': 'Scheduled', 'I': 'Cancelled', 'E': 'Error',
}


def normalise_status(raw) -> tuple[str, str]:
    """(code, label) for an InstalmentStatus cell, however dirty.

    Returns ('?', 'Unknown') rather than guessing — an unrecognised status must
    read as unknown, never silently as Successful."""
    s = str(raw or '').strip().upper()
    if s in _STATUS_LABEL:
        return s, _STATUS_LABEL[s]
    if s.startswith('SUCCESS'):
        return 'S', 'Successful'
    if s.startswith('FAIL'):
        return 'F', 'Failed'
    if s.startswith('CANCEL'):
        return 'I', 'Cancelled'
    if s.startswith('ERROR'):
        return 'E', 'Error'
    return '?', 'Unknown'


def policy_number_of(client_number) -> str:
    """RealPay's clientNumber → the Graphite policyNumber.

    They are the same number EXCEPT that RealPay appends a renewal year on some
    rows — shaped 'DOMG<policy>/<year>'. 2,383 live rows carry that suffix, and
    Finance strips it by hand with LEFT(14) / LEFT(13) (see Rose Mokgware's
    'PAYMENTS BROKER REPORT' procedure). Deliberately no real policy number in
    this docstring: policy numbers are PII (AD-POL-AI-GOV-001) and must not sit
    in source.

    🔴 Do NOT reuse realpay.recon._norm_contract here: it strips punctuation, so
    the suffix is glued on instead of removed and the result matches no policy.
    """
    return str(client_number or '').strip().split('/')[0].strip()


def broker_book(active_only: bool = False, chunk: int = 1000) -> Dict[str, Any]:
    """Every broker in Graphite with their commercial + domestic policy counts.

    🔴 The broker link is ``policies.agency_id``, NOT ``policies.agent_id`` via
    ``users.agency_id``. The agent path returns ZERO for Spectrum, Botshabelo,
    Minet, Ultimate Care and Trinity, and 21,059 policies have the two fields
    disagreeing (verified on the replica 2026-09-08). See
    aware/modes/broker_analysis.py, which still uses the agent path.
    """
    if not graphite_ro.is_configured():
        return {'configured': False, 'rows': []}
    like = ' OR '.join(['UPPER(p.policyNumber) LIKE %s'] * len(_BROKER_PREFIXES))
    params: List[Any] = [f'{p}%' for p in _BROKER_PREFIXES]
    where = [f'({like})']
    if active_only:
        where.append('p.status = 1')
    sql = (
        "SELECT a.id AS agency_id, a.name AS agency, "
        "       COUNT(*) AS policies, SUM(p.status = 1) AS live_policies, "
        "       ROUND(SUM(COALESCE(p.annual_premium, 0)), 2) AS annual_premium "
        "FROM policies p JOIN agencies a ON p.agency_id = a.id "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY a.id, a.name ORDER BY policies DESC"
    )
    rows = graphite_ro.query(sql, params, limit=chunk) or []
    return {'configured': True, 'rows': [{
        'agency_id': str(r.get('agency_id') or ''),
        'agency': (r.get('agency') or '').strip(),
        'policies': int(r.get('policies') or 0),
        'live_policies': int(r.get('live_policies') or 0),
        'annual_premium': _f(r.get('annual_premium')),
    } for r in rows]}


def broker_policies(agency_names, active_only: bool = False, limit: int = 5000):
    """The commercial + domestic policies sitting under these Graphite agencies."""
    if not graphite_ro.is_configured():
        return None
    names = [n for n in (agency_names or []) if n]
    if not names:
        return []
    like = ' OR '.join(['UPPER(p.policyNumber) LIKE %s'] * len(_BROKER_PREFIXES))
    ph = ','.join(['%s'] * len(names))
    where = [f'({like})', f'a.name IN ({ph})']
    if active_only:
        where.append('p.status = 1')
    sql = (
        "SELECT p.policyNumber AS policy_number, a.name AS agency, p.status AS policy_status, "
        "       COALESCE(p.premium, 0) AS premium, COALESCE(p.annual_premium, 0) AS annual_premium, "
        "       p.term_start_date, p.term_end_date, "
        "       TRIM(COALESCE(NULLIF(TRIM(p.business_name), ''), "
        "                     CONCAT_WS(' ', cu.firstName, cu.lastName))) AS insured_name "
        "FROM policies p JOIN agencies a ON p.agency_id = a.id "
        "LEFT JOIN customer cu ON p.customer_id = cu.id "
        f"WHERE {' AND '.join(where)} ORDER BY p.policyNumber"
    )
    params = [f'{p}%' for p in _BROKER_PREFIXES] + names
    return graphite_ro.query(sql, params, limit=limit) or []


def policy_lookup(policy_number):
    """One policy, straight from the Graphite replica — for C3's Add Policy
    prefill. Returns the row, None when Graphite is not configured (caller must
    NOT read that as "does not exist"), or {} when Graphite genuinely has no
    such policy.

    The three answers are deliberately distinct: an unreachable Graphite and a
    missing policy look identical if both return falsey, and C3 blocks insertion
    on "missing" — so conflating them would block every insertion during an
    outage and read as a data problem.
    """
    if not graphite_ro.is_configured():
        return None
    pol = (policy_number or '').strip()
    if not pol:
        return {}
    sql = (
        "SELECT p.policyNumber AS policy_number, a.name AS agency, p.status AS policy_status, "
        "       COALESCE(p.premium, 0) AS premium, COALESCE(p.annual_premium, 0) AS annual_premium, "
        "       p.term_start_date, p.term_end_date, "
        "       TRIM(COALESCE(NULLIF(TRIM(p.business_name), ''), "
        "                     CONCAT_WS(' ', cu.firstName, cu.lastName))) AS insured_name "
        "FROM policies p JOIN agencies a ON p.agency_id = a.id "
        "LEFT JOIN customer cu ON p.customer_id = cu.id "
        "WHERE UPPER(p.policyNumber) = UPPER(%s) LIMIT 1"
    )
    # graphite_ro.query() NEVER returns None — it returns a list or RAISES. So a
    # real outage (connection refused, timeout, credentials rotated) arrives here
    # as an exception, and without this catch it would 500 the caller: the Add
    # Policy form would refuse every row during an outage, which is precisely the
    # behaviour the tri-state exists to prevent. Unreachable must reach the
    # caller as None, not as a stack trace.
    try:
        rows = graphite_ro.query(sql, [pol], limit=1)
    except Exception:                       # noqa: BLE001
        log.warning('policy_lookup: Graphite unreachable for %s', pol, exc_info=True)
        return None
    return rows[0] if rows else {}


def policy_statuses(policy_numbers, start=None, end=None, chunk: int = 500):
    """{policyNumber: the collection result for the window} straight from RealPay.

    This replaces the manual "download the transaction report and VLOOKUP the
    Current Status into every broker tab" step.

    TWO things this has to get right, both learned from real data:

    1. **Answer for a MONTH, not "latest ever".** A broker sheet pays commission
       on what was collected in the month being paid, which is exactly what
       Finance's ``Current Status`` column holds. Without a window the answer is
       meaningless: every policy carries a 24-instalment mandate schedule
       running into 2027.

    2. **Prefer a real ATTEMPT over a mandate row.** RealPay leaves rows at
       status 'A' (scheduled) on dates that have already passed, and those rows
       are often the newest by date. Ranking purely by date therefore reported
       "Scheduled" for policies that had in fact been debited successfully —
       one COMG policy carries 8 successes and a processing row underneath
       an 'A' dated later than all of them. So an attempt (S/F/W/R/E) always beats a non-attempt
       (A/I), and only then does the later date win.

    Returns {} when the bridge is not configured; the caller MUST treat that as
    'unknown', never as 'nothing collected' (realpay/recon.py learned that one
    the hard way: a partial read presented as a whole one invents alarm rows).
    """
    if not graphite_ro.is_configured():
        return {}
    wanted = sorted({policy_number_of(p) for p in (policy_numbers or []) if policy_number_of(p)})
    out: Dict[str, Any] = {}
    for i in range(0, len(wanted), chunk):
        part = wanted[i:i + chunk]
        ph = ','.join(['%s'] * len(part))
        # The renewal-year suffix means an equality join drops rows, so match the
        # policy number OR that number followed by '/…'.
        where = [f"(i.clientNumber IN ({ph}) OR SUBSTRING_INDEX(i.clientNumber, '/', 1) IN ({ph}))",
                 "i.InstalmentActionDate < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"]
        params: List[Any] = list(part) + list(part)
        if start is not None:
            where.append('i.InstalmentActionDate >= %s')
            params.append(_iso_start(start))
        if end is not None:
            import datetime as _dt
            where.append('i.InstalmentActionDate < %s')
            params.append(_iso_start(end + _dt.timedelta(days=1)))
        sql = (
            "SELECT SUBSTRING_INDEX(i.clientNumber, '/', 1) AS pn, "
            "       i.InstalmentStatus AS st, "
            "       ROUND(CAST(i.InstalmentAmount AS DECIMAL(15,2)), 2) AS amt, "
            "       i.InstalmentActionDate AS action_date, "
            "       i.instalmentResponse AS reason "
            "FROM realpay_contract_installments i "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY i.InstalmentActionDate"
        )
        for r in (graphite_ro.query(sql, tuple(params), limit=chunk * 60) or []):
            pn = (r.get('pn') or '').strip()
            if not pn:
                continue
            code, label = normalise_status(r.get('st'))
            # A mandate row still at 'A' on a date that has ALREADY PASSED is not
            # "scheduled" — it is a debit whose result never came back. Since June
            # 2026 that is the entire corporate/domestic book (the RealPay ->
            # Graphite outcome feed stopped; Instant is unaffected). Calling it
            # Scheduled hides the gap on the very screen that should show it.
            if code == 'A' and str(r.get('action_date') or '')[:10] < _today_iso():
                label = 'No outcome received'
            cand = {
                'status': code, 'status_label': label,
                'amount': _f(r.get('amt')),
                'action_date': str(r.get('action_date') or '')[:10],
                'reason': (r.get('reason') or '').strip()[:120],
                'attempt': code in _ATTEMPT_STATUSES,
            }
            held = out.get(pn)
            if held is None:
                out[pn] = cand
                continue
            # An attempt always beats a non-attempt; among equals, the later date.
            if cand['attempt'] and not held['attempt']:
                out[pn] = cand
            elif cand['attempt'] == held['attempt'] and cand['action_date'] >= held['action_date']:
                out[pn] = cand
    for v in out.values():
        v.pop('attempt', None)
    return out


def collected_in_window(policy_numbers, start=None, end=None, chunk: int = 500):
    """{policyNumber: total actually COLLECTED in the window}.

    Separate from policy_statuses on purpose: a policy can be debited twice in a
    month, and the money question ('how much came in') is not the same as the
    outcome question ('did the last attempt work')."""
    if not graphite_ro.is_configured():
        return {}
    wanted = sorted({policy_number_of(p) for p in (policy_numbers or []) if policy_number_of(p)})
    out: Dict[str, float] = {}
    for i in range(0, len(wanted), chunk):
        part = wanted[i:i + chunk]
        ph = ','.join(['%s'] * len(part))
        where = [f"(i.clientNumber IN ({ph}) OR SUBSTRING_INDEX(i.clientNumber, '/', 1) IN ({ph}))",
                 "i.InstalmentStatus = %s",
                 "i.created_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"]
        params: List[Any] = list(part) + list(part) + [_COLLECTED]
        if start is not None:
            where.append('i.created_at >= %s')
            params.append(_iso_start(start))
        if end is not None:
            import datetime as _dt
            where.append('i.created_at < %s')
            params.append(_iso_start(end + _dt.timedelta(days=1)))
        sql = ("SELECT SUBSTRING_INDEX(i.clientNumber, '/', 1) AS pn, "
               "       ROUND(SUM(CAST(i.InstalmentAmount AS DECIMAL(15,2))), 2) AS amt "
               "FROM realpay_contract_installments i "
               f"WHERE {' AND '.join(where)} GROUP BY pn")
        for r in (graphite_ro.query(sql, tuple(params), limit=chunk * 2) or []):
            pn = (r.get('pn') or '').strip()
            if pn:
                out[pn] = out.get(pn, 0.0) + _f(r.get('amt'))
    return out


# ─── Broker commission feed (C6/C7, 17-Sep-2026) ─────────────────────────────
# Two functions the commission summary needs. Both window on created_at, NOT
# created_at. PROVEN on live Graphite 17-Sep-2026: for September,
# DOM/COM collected rows are 13 (P21,361.76) on created_at and ZERO on
# created_at, because that column carries garbage future dates. Every
# other function in this module windows on created_at, so a commission
# screen built on those would have shown every broker P0.00 and called it a
# month's work. Money windows on created_at. The older screens are deliberately
# NOT changed here.
#
# MOTOR / NON-MOTOR (CFO 17-Sep-2026): decided per POLICY, not per broker and
# not by apportioning a premium. A policy is MOTOR if it has a vehicle attached
# in Graphite (the `vehicle` table, joined on policy_id, soft-deleted rows
# ignored) OR its product is a motor product (has_vehicle=1: Motor Comprehensive,
# Third Party Car). Everything else is non-motor. The whole of that policy's
# collected premium goes to the one bucket. "If a policy mentions motor it is
# motor; anything else is non-motor" — his words.

_DOMCOM_PREFIXES = ("COMG", "DOMG")


def _domcom_clause(alias: str = 'i') -> str:
    """SQL fragment limiting to Domestic/Commercial policy numbers.

    pymysql does %-substitution on the query string, so a literal percent in a
    LIKE has to be doubled or it is read as a placeholder and the query dies.
    """
    return ("(" + " OR ".join(
        f"{alias}.clientNumber LIKE '{p}%%'" for p in _DOMCOM_PREFIXES) + ")")


# A collected instalment is motor when the policy behind it has a live vehicle
# row, or its product is flagged has_vehicle. Written as a SQL CASE so the split
# happens in one pass over the join rather than a second query per policy.
_MOTOR_CASE = (
    "CASE WHEN pr.has_vehicle = 1 "
    "     OR EXISTS (SELECT 1 FROM vehicle v "
    "                WHERE v.policy_id = p.id AND v.deleted_at IS NULL) "
    "     THEN 1 ELSE 0 END"
)


def collected_by_agency(start, end):
    """{agency_name: {'motor': float, 'non_motor': float, 'amount': float,
                      'count': int}} collected in the window.

    'Collected' is InstalmentStatus='S' only — a raised debit with no result is
    not money. Windowed on created_at. Returns {} when the bridge is not
    configured; the caller must not read that as zero collected.
    """
    if not graphite_ro.is_configured():
        return {}
    sql = (
        "SELECT COALESCE(ab.name, '') AS agency, "
        f"       {_MOTOR_CASE} AS is_motor, "
        "       COUNT(*) AS n, "
        "       ROUND(SUM(CAST(i.InstalmentAmount AS DECIMAL(15,2))), 2) AS amt "
        "FROM realpay_contract_installments i "
        "JOIN policies p ON p.policyNumber = i.clientNumber "
        "LEFT JOIN products pr ON pr.id = p.product_id "
        "LEFT JOIN agencies ab ON ab.id = p.agency_id "
        "WHERE i.InstalmentStatus = %s "
        f"  AND {_domcom_clause('i')} "
        "  AND i.created_at >= %s AND i.created_at < %s "
        "GROUP BY agency, is_motor"
    )
    rows = graphite_ro.query(sql, (_COLLECTED, _iso_start(start), _iso_start(end)),
                             limit=2000) or []
    out = {}
    for r in rows:
        name = (r.get('agency') or '').strip()
        if not name:
            continue
        rec = out.setdefault(name, {'motor': 0.0, 'non_motor': 0.0,
                                    'amount': 0.0, 'count': 0})
        amt = _f(r.get('amt'))
        if int(r.get('is_motor') or 0) == 1:
            rec['motor'] += amt
        else:
            rec['non_motor'] += amt
        rec['amount'] += amt
        rec['count'] += int(r.get('n') or 0)
    return out


def outcome_feed_health(start, end):
    """Is the RealPay→Graphite OUTCOME feed actually reporting in this window?

    The failure this guards against is specific and has already happened: debits
    are raised, Graphite stores them at status 'A', and the result never comes
    back. Every 'money collected' query then returns a small number or zero —
    which reads as "the brokers collected nothing" when the truth is "we do not
    know what they collected". Zero and unknown must never render the same.

    Returns {'reporting': bool, 'resolved': int, 'awaiting': int, 'window_rows':
    int, 'reason': str}. `reporting` is False when the window holds debits but
    (nearly) none of them came back with an outcome.
    """
    if not graphite_ro.is_configured():
        return {'reporting': False, 'resolved': 0, 'awaiting': 0, 'window_rows': 0,
                'reason': 'Graphite bridge not configured'}
    sql = (
        "SELECT i.InstalmentStatus AS st, COUNT(*) AS n "
        "FROM realpay_contract_installments i "
        f"WHERE {_domcom_clause('i')} "
        "  AND i.created_at >= %s AND i.created_at < %s "
        "GROUP BY st"
    )
    rows = graphite_ro.query(sql, (_iso_start(start), _iso_start(end)), limit=50) or []
    counts = {(r.get('st') or '').strip().upper(): int(r.get('n') or 0) for r in rows}
    awaiting = counts.get(_SCHEDULED, 0)
    total = sum(counts.values())
    resolved = total - awaiting
    if total == 0:
        return {'reporting': False, 'resolved': 0, 'awaiting': 0, 'window_rows': 0,
                'reason': 'No debits were raised at all in this period'}
    # A book that raised debits but got results back on under a fifth of them is
    # not reporting. Deliberately generous: this flag exists to stop a confident
    # wrong number, not to grade the feed.
    if resolved * 5 < total:
        return {'reporting': False, 'resolved': resolved, 'awaiting': awaiting,
                'window_rows': total,
                'reason': (f'{awaiting} of {total} debits are still awaiting a result '
                           f'from RealPay, so collected premium is incomplete')}
    return {'reporting': True, 'resolved': resolved, 'awaiting': awaiting,
            'window_rows': total, 'reason': ''}


# ─── C4 EFT cross-check (19-Sep-2026) ────────────────────────────────────────
#: Graphite ledger statuses that mean the money arrived. Same set
#: integrations.realpay_ledger_reconcile uses for payment_transactions.
_LEDGER_PAID = ('SUCCESS', 'SUCCESSFUL', 'PAID')


def payments_in_window(policy_numbers, start, end, chunk: int = 500):
    """{policyNumber: {'count': int, 'amount': Decimal}} for every PAID Graphite
    payment/receipt (``payment_transactions``) dated in [start, end).

    Used only to turn a RealPay FAILED/ERROR into EFT SUCCESS: a client whose
    debit bounced but who paid by EFT in the same month did pay. Any payment
    counts — no amount threshold, by Finance's rule.

    Returns None when the bridge is not configured. A query failure RAISES — the
    caller must not read an unread ledger as "no payment".
    """
    if not graphite_ro.is_configured():
        return None
    wanted = sorted({policy_number_of(p) for p in (policy_numbers or []) if policy_number_of(p)})
    out: Dict[str, Any] = {}
    for i in range(0, len(wanted), chunk):
        part = wanted[i:i + chunk]
        ph = ','.join(['%s'] * len(part))
        lp = ','.join(['%s'] * len(_LEDGER_PAID))
        sql = ("SELECT TRIM(pol.policyNumber) AS pn, COUNT(*) AS n, "
               "       ROUND(SUM(COALESCE(pt.amount, 0)), 2) AS amt "
               "FROM payment_transactions pt "
               "JOIN policies pol ON pol.id = pt.policy_id "
               f"WHERE pol.policyNumber IN ({ph}) "
               "  AND pt.new_payment_date >= %s AND pt.new_payment_date < %s "
               f"  AND UPPER(TRIM(pt.status)) IN ({lp}) "
               "GROUP BY pn")
        params = list(part) + [_iso_start(start), _iso_start(end)] + list(_LEDGER_PAID)
        for r in (graphite_ro.query(sql, tuple(params), limit=chunk * 2) or []):
            pn = (r.get('pn') or '').strip()
            if pn and int(r.get('n') or 0) > 0:
                out[pn] = {'count': int(r.get('n') or 0),
                           'amount': Decimal(str(r.get('amt') or '0'))}
    return out
