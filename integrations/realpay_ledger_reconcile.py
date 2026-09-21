"""
integrations/realpay_ledger_reconcile.py — does the money RealPay says it collected
actually reach Omni's payment ledger?

WHY THIS EXISTS. On 8 September 2026, while sizing the Instant Insurance brief, a
reconciliation of RealPay's own instalment feed against `payment_transactions` found
that roughly 22,500 successful collections from July and August had never landed:

    month     RealPay says    reached the ledger    missing
    2026-06         18,635                17,257      1,378
    2026-07         18,090                 3,742     14,348   (79%)
    2026-08         19,268                11,123      8,145   (42%)

RealPay was healthy throughout — 67,442 instalment rows in August, still writing that
same afternoon. The loss was on our side, between their feed and our ledger, and
NOTHING in Omni noticed for two months. That is the gap this module closes: not the
import itself (that is TheRiskCo's to mend) but the silence around it.

It is deliberately not a "collections dashboard". It answers one question a day, in
one number, and shouts when that number moves.

HOW IT COMPARES, AND WHY A COUNT-VS-COUNT SUBTRACTION WAS NOT GOOD ENOUGH
The first version of this module subtracted one month's totals from the other. Measured
against matched contracts on the live replica, that had a ~16% blind spot, because in a
CLEAN month the ledger holds receipts the feed table does not:

    month     feed    ledger   subtraction says   matched truth   ledger-only
    2026-04  19,927   23,344       -3,417 "ok"      41  ( 0.2%)         3,458
    2026-06  18,635   17,257        1,378 (7.4%)  3,474  (18.6%)         2,096
    2026-07  18,090    3,742       14,348 (79%)  15,680  (86.7%)         1,332
    2026-08  19,268   11,123        8,145 (42%)   8,954  (46.5%)           809

Those ledger-only receipts cancel real losses one for one, so a month quietly losing up
to ~16% of its collections would have reported "reconciles", and a 20% loss would have
shown as ~4% and passed under the 5% threshold. June's real figure was 18.6%, not 7.4%.
A false clean is the one failure this module must never produce.

So the comparison is per CONTRACT: the feed's successful instalments are grouped by
clientNumber, the ledger's by the policy number they belong to, and the shortfall is the
sum over contracts of what the feed has and the ledger does not. Receipts the LEDGER has
and the feed does not are reported separately as `ledger_only` — they are real and worth
explaining (roughly 3,400 in a clean month, most likely a second collection path) but
they can no longer hide a loss.

THREE TRAPS THIS CODE HAS TO RESPECT (all proven on the live data 8-Sep-2026)
  * `payment_transactions` is a payment LEDGER, not a receipts table: it carries
    future-dated scheduled instalments out to the year 2058. Every window is bounded
    at both ends or the comparison sums the future.
  * The success status has spelling variants — 'Success' (1,214,359 rows), 'Paid'
    (120) and 'SUCCESSFUL' (90). Matching the exact word 'Success' silently drops
    P29,269 of real receipts, so both sides compare on UPPER(TRIM(...)).
  * `realpay_contract_installments.InstalmentActionDate` is a **VARCHAR**, not a date,
    and carries rows dated as far ahead as 2124. The window comparisons work only
    because ISO-8601 strings sort lexically in the same order as the dates they
    represent; both ends are bounded, so the future rows are excluded rather than
    summed. Any freshness probe on this column must bound it at today as well.

READ ONLY. Every statement goes through integrations.graphite_ro, which refuses a
non-replica host, refuses anything that is not a read, and sets the session
read-only. Nothing here writes to Graphite, and nothing here moves money.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from django.conf import settings

from integrations import graphite_ro
from django.utils import timezone

log = logging.getLogger(__name__)

#: RealPay's own success code on an instalment, plus the long spellings seen in the
#: feed. Verified against the live status distribution on 8-Sep-2026.
FEED_SUCCESS = ('S', 'SUCCESS', 'SUCCESSFUL', 'PAID')

#: The ledger's success spellings. All three occur; a single-value filter loses rows.
LEDGER_SUCCESS = ('SUCCESS', 'SUCCESSFUL', 'PAID')

#: A month is a break when the feed knows of this many more collections than the
#: ledger, AND that is this share of the feed's own count. Both must be exceeded, so a
#: handful of stragglers mid-month is not an alarm.
DEFAULT_MIN_ROWS = 500
DEFAULT_MIN_PCT = 5.0

#: The newest month is still filling, so it is reported but never alarmed on.
SETTLING_MONTHS = 1

#: Past this day of the month, the current month having NO feed rows at all is an
#: outage rather than an early-month emptiness.
FEED_SILENT_AFTER_DAY = 3


def _thresholds() -> tuple[int, float]:
    return (int(getattr(settings, 'REALPAY_RECONCILE_MIN_ROWS', DEFAULT_MIN_ROWS)),
            float(getattr(settings, 'REALPAY_RECONCILE_MIN_PCT', DEFAULT_MIN_PCT)))


def _month_floor(d: datetime.date) -> datetime.date:
    return d.replace(day=1)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    y, m = divmod((d.year * 12 + d.month - 1) + n, 12)
    return datetime.date(y, m + 1, 1)


def _placeholders(values) -> str:
    return ', '.join(['%s'] * len(values))


def _month_counts(cur, start: datetime.date, end: datetime.date) -> tuple[dict, dict]:
    """Successful collections per contract, from each side, for one month.

    Keyed on the contract: the feed's clientNumber against the policy number the
    ledger receipt belongs to. Fable verified on the live replica that the two are the
    same identifier with no orphans; a handful of feed keys carry a trailing "/YYYY"
    and stripping it changed nothing, so they are left exactly as stored.
    """
    cur.execute(
        "SELECT TRIM(clientNumber) AS k, COUNT(*) AS n "
        "FROM realpay_contract_installments "
        "WHERE InstalmentActionDate >= %s AND InstalmentActionDate < %s "
        f"AND UPPER(TRIM(InstalmentStatus)) IN ({_placeholders(FEED_SUCCESS)}) "
        "GROUP BY k",
        [start.isoformat(), end.isoformat(), *FEED_SUCCESS])
    feed = {(r['k'] or ''): int(r['n']) for r in cur.fetchall()}

    cur.execute(
        "SELECT TRIM(pol.policyNumber) AS k, COUNT(*) AS n, "
        "       COALESCE(SUM(pt.amount), 0) AS amt "
        "FROM payment_transactions pt "
        "JOIN policies pol ON pol.id = pt.policy_id "
        "WHERE pt.new_payment_date >= %s AND pt.new_payment_date < %s "
        "AND pt.paymentMethod = 'RealPay' "
        f"AND UPPER(TRIM(pt.status)) IN ({_placeholders(LEDGER_SUCCESS)}) "
        "GROUP BY k",
        [start, end, *LEDGER_SUCCESS])
    ledger = {(r['k'] or ''): (int(r['n']), float(r['amt'] or 0))
              for r in cur.fetchall()}
    return feed, ledger


def compare(months: int = 6, asof: Optional[datetime.date] = None) -> Dict[str, Any]:
    """RealPay's successful collections vs the ledger's, matched contract by contract.

    Returns
      {'available': True, 'asof': 'YYYY-MM-DD', 'window_months': n,
       'rows': [{'month','feed','ledger','shortfall','shortfall_pct','ledger_only',
                 'estimated_value','settling','breach'}],
       'breaches': [...], 'worst': {...} | None, 'verdict': 'ok' | 'break',
       'thresholds': {'min_rows': n, 'min_pct': f}}

    Never raises: an unreachable or unconfigured replica returns
    {'available': False, 'reason': '<short cause>'} so a daily job degrades to a
    reported outage instead of a crash. An EMPTY feed is treated the same way — the
    feed importer can die exactly as the ledger importer did, and "the feed said
    nothing" must never read as "everything reconciles".
    """
    asof = asof or timezone.localdate()
    months = max(2, int(months))
    start = _add_months(_month_floor(asof), -(months - 1))
    min_rows, min_pct = _thresholds()
    newest = f'{asof.year:04d}-{asof.month:02d}'

    try:
        rows: List[Dict[str, Any]] = []
        with graphite_ro.connection() as cx:
            with cx.cursor() as cur:
                for i in range(months):
                    m_start = _add_months(start, i)
                    m_end = _add_months(m_start, 1)
                    key = f'{m_start.year:04d}-{m_start.month:02d}'
                    settling = key == newest

                    feed, ledger = _month_counts(cur, m_start, m_end)
                    feed_rows = sum(feed.values())
                    ledger_rows = sum(n for n, _ in ledger.values())
                    ledger_amt = sum(a for _, a in ledger.values())

                    # H25 fence: a silent feed is an outage, never a clean bill. The
                    # feed importer can die exactly as the ledger importer did, and a
                    # month with no feed rows would otherwise show a shortfall of zero
                    # and read "reconciles". Early days of the current month are
                    # genuinely empty, so the newest month is only fenced once it has
                    # had a few days to fill.
                    if feed_rows == 0 and (not settling or asof.day > FEED_SILENT_AFTER_DAY):
                        log.warning('realpay reconcile: feed empty for %s', key)
                        return {'available': False,
                                'reason': f'the RealPay feed has no rows for {key}'}

                    short = sum(max(0, n - ledger.get(k, (0, 0.0))[0])
                                for k, n in feed.items())
                    ledger_only = sum(max(0, n - feed.get(k, 0))
                                      for k, (n, _) in ledger.items())
                    pct = round(short / feed_rows * 100, 1) if feed_rows else 0.0
                    breach = (not settling) and short >= min_rows and pct >= min_pct
                    # Average value of the receipts that DID land, so the estimate of
                    # what the missing ones are worth is grounded rather than invented.
                    avg = (ledger_amt / ledger_rows) if ledger_rows else 0.0
                    rows.append({
                        'month': key,
                        'feed': feed_rows,
                        'ledger': ledger_rows,
                        'ledger_amount': round(ledger_amt, 2),
                        'contracts_feed': len(feed),
                        'contracts_ledger': len(ledger),
                        'shortfall': short,
                        'shortfall_pct': pct,
                        'ledger_only': ledger_only,
                        'estimated_value': round(short * avg, 2) if short > 0 else 0.0,
                        'settling': settling,
                        'breach': breach,
                    })
    except Exception as exc:                     # noqa: BLE001 — degrade, never crash a cron
        log.warning('realpay reconcile unavailable: %s', exc.__class__.__name__)
        return {'available': False, 'reason': exc.__class__.__name__}

    breaches = [r for r in rows if r['breach']]
    worst = max(breaches, key=lambda r: r['shortfall']) if breaches else None
    return {
        'available': True,
        'asof': asof.isoformat(),
        'window_months': months,
        'rows': rows,
        'breaches': breaches,
        'worst': worst,
        'verdict': 'break' if breaches else 'ok',
        'thresholds': {'min_rows': min_rows, 'min_pct': min_pct},
        'note': ('Matched contract by contract, not total against total. Receipts the '
                 'ledger holds that the feed does not are reported as "ledger only" '
                 'and never used to offset a shortfall — offsetting them was hiding '
                 'about 16% of any loss. The newest month is still settling and is '
                 'never alarmed.'),
    }


def summary_line(result: Dict[str, Any]) -> str:
    """One sentence for an email subject or a chat message."""
    if not result.get('available'):
        return f"RealPay reconciliation could not run ({result.get('reason')})."
    if result['verdict'] == 'ok':
        return 'RealPay collections reconcile to the payment ledger.'
    w = result['worst']
    total = sum(r['shortfall'] for r in result['breaches'])
    return (f"{total:,} RealPay collections are missing from the payment ledger "
            f"across {len(result['breaches'])} month(s); worst is {w['month']} at "
            f"{w['shortfall']:,} ({w['shortfall_pct']}%).")
