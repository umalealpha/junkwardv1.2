"""Money Lens — the four business numbers at the top of the CEO brief.

The brief used to read only the CEO's inbox; it never said how the business
actually did. These four tiles answer that in one glance: money in, business
won, business lost, claims coming.

Sourced from the Graphite read-only replica via aware.engine (the same masked,
SELECT-only bridge the Sunday board read uses). Business rules live here, in
the app, where they are tested — NOT in infra/ceo-monitor/ (see that README).

WHY EVERY TILE CARRIES ITS OWN "unavailable" STATE
--------------------------------------------------
CFO standing rule: never quote a figure out of Omni as fact. A failed or empty
sub-query must NOT become a conclusion — a tile with no data reads "no fresh
data", never "P0". A silent zero on the premium tile would tell the CEO
collections had collapsed when in truth the query broke.

WHAT IS DELIBERATELY NOT A TILE
-------------------------------
* Claim RESERVES — excluded outright (CFO 2026-09-10: "actual claims payments
  only"). `claim_reserves` has no amount column at all and
  `new_claims.reserve_amount` sums to zero on live data, so a reserve figure
  would be both wrong and the wrong question. Claims paid is measured from the
  money that actually left instead — see _claims_paid.
* Cash at bank — the bank-statement imports carry known duplicates, so a
  balance from Omni would be wrong.
* Debtor balance — the true balance is invoices - (payments - reversals) per
  policy_ledger; the pre-aggregated `summary_age_*` table the schema guide
  suggests is the one the CFO has ruled out. Too heavy for a 04:30 email and
  not worth a wrong number.
"""
import datetime
import logging
from django.utils import timezone

log = logging.getLogger(__name__)


def _one(sql):
    """Run a single-row aggregate. Returns the row dict, or None if anything
    went wrong. None means 'unknown' and is never coerced to zero."""
    try:
        from aware.engine import run_select
        _cols, rows = run_select(sql)
    except Exception:  # noqa: BLE001 - a dead replica must not kill the brief
        log.exception("ceo_money_lens: query failed")
        return None
    return rows[0] if rows else None


def _num(row, key):
    if not row or row.get(key) is None:
        return None
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return None


def _pula(v):
    return "P{:,.0f}".format(v)


def _delta(now, before):
    """Percent move vs the same weekday last week.

    Returns None when it cannot be stated honestly: either side unknown, or a
    zero baseline (a rise from 0 is not '+100%', it has no percentage).
    """
    if now is None or before is None or before == 0:
        return None
    return (now - before) / before * 100.0


def _tile(label, value, sub, delta, note=""):
    return {"label": label, "value": value, "sub": sub, "delta": delta,
            "note": note, "ok": value is not None}


def _collected(start, end, prev_start, prev_end):
    """Premium banked in the window — v_policy_payments. `amount` is a varchar
    on the replica, so it is CAST before summing."""
    sql = ("SELECT COUNT(*) n, SUM(CAST(amount AS DECIMAL(14,2))) t "
           "FROM v_policy_payments WHERE DATE(payment_date) BETWEEN '%s' AND '%s'")
    row, prev = _one(sql % (start, end)), _one(sql % (prev_start, prev_end))
    total, n = _num(row, "t"), _num(row, "n")
    return _tile("Premium collected",
                 _pula(total) if total is not None else None,
                 "%d receipts" % int(n) if n is not None else "no fresh data",
                 _delta(total, _num(prev, "t")))


def _new_business(start, end, prev_start, prev_end):
    """Premium written in the window. Only ISSUED NEWBUSINESS rows: policy_actions
    keeps superseded re-rate rows and negative CANCEL rows, both of which would
    corrupt a naive sum."""
    sql = ("SELECT COUNT(*) c, SUM(premium) p FROM policy_actions "
           "WHERE transaction_type='NEWBUSINESS' AND status='ISSUED' "
           "AND DATE(transaction_date) BETWEEN '%s' AND '%s'")
    row, prev = _one(sql % (start, end)), _one(sql % (prev_start, prev_end))
    prem, c = _num(row, "p"), _num(row, "c")
    return _tile("New business written",
                 _pula(prem) if prem is not None else None,
                 "%d policies" % int(c) if c is not None else "no fresh data",
                 _delta(prem, _num(prev, "p")))


def _cancellations(start, end, prev_start, prev_end):
    sql = ("SELECT COUNT(*) c FROM policy_actions "
           "WHERE transaction_type='CANCEL' AND status='ISSUED' "
           "AND DATE(transaction_date) BETWEEN '%s' AND '%s'")
    row, prev = _one(sql % (start, end)), _one(sql % (prev_start, prev_end))
    c = _num(row, "c")
    return _tile("Cancellations",
                 "%d" % int(c) if c is not None else None,
                 "policies lost", _delta(c, _num(prev, "c")))


def _claims_paid(start, end, prev_start, prev_end):
    """Claims money that ACTUALLY went out (CFO 2026-09-10: "actual claims
    payments only" — never reserves).

    Source is Omni's own PaymentRequest, not the Graphite replica: a claim is
    paid when Finance's claim payment request reaches `paid`. Dated by the
    linked task's completed_at — the authorisation click itself — because
    updated_at moves on any later edit to the record.
    """
    import datetime as _dt

    from django.db.models import Count, Sum

    from taskboard.models import PaymentRequest

    def one(s, e):
        try:
            return (PaymentRequest.objects
                    .filter(category=PaymentRequest.Category.CLAIM,
                            status=PaymentRequest.Status.PAID,
                            task__completed_at__date__gte=_dt.date.fromisoformat(s),
                            task__completed_at__date__lte=_dt.date.fromisoformat(e))
                    .aggregate(n=Count("id"), t=Sum("total")))
        except Exception:  # noqa: BLE001 - unknown, never zero
            log.exception("ceo_money_lens: claims paid query failed")
            return None

    row, prev = one(start, end), one(prev_start, prev_end)
    total, n = _num(row, "t"), _num(row, "n")
    if row is not None and total is None:
        total = 0.0
    if prev is not None and _num(prev, "t") is None:
        prev = {"t": 0.0}
    return _tile("Claims paid",
                 _pula(total) if total is not None else None,
                 "%d payment%s" % (int(n or 0), "" if int(n or 0) == 1 else "s")
                 if row is not None else "no fresh data",
                 _delta(total, _num(prev, "t")),
                 note="money out, not reserves")


CLAIM_REPORTING_FLOOR = 200_000


def _large_claims(days=10, floor=CLAIM_REPORTING_FLOOR):
    """Largest NEW claims over `floor` registered in Graphite in the last `days` days.

    The CEO is not told about a P15 key loss. Before 16-Sep-2026 the only filter
    was `reserve_amount > 0`, so on a quiet week the brief led with claims of
    P6,749, P6,000 and P15 (CFO). The floor is the reported RESERVE - what we
    expect the claim to cost us - not what has been paid so far.

    Pulls from `claims` (for claim_number, status, type_of_loss, created_at,
    incident_description) joined to `new_claims` (for reserve_amount) and
    `policies` (for policyNumber). NO claimant PII — never select names, Omang,
    phone, address or medical detail.
    """
    cutoff = (timezone.localdate() - datetime.timedelta(days=days)).isoformat()
    sql = (
        "SELECT c.claim_number, c.claim_type, c.type_of_loss, c.status, "
        "  c.incident_description, DATE(c.created_at) AS registered, "
        "  nc.reserve_amount, nc.paid_amount, p.policyNumber "
        "FROM claims c "
        "LEFT JOIN new_claims nc ON nc.claim_number = c.claim_number "
        "LEFT JOIN policies p ON c.policy_id = p.id "
        "WHERE DATE(c.created_at) >= '%s' "
        "  AND nc.reserve_amount IS NOT NULL AND nc.reserve_amount > %d "
        "ORDER BY nc.reserve_amount DESC "
        "LIMIT 10" % (cutoff, floor)
    )
    try:
        from aware.engine import run_select
        _cols, rows = run_select(sql)
    except Exception:  # noqa: BLE001
        # None, never [] - the caller prints "no claims above the floor" for an
        # empty list, and a broken feed must never assert that to the CEO. A
        # failure and a genuinely quiet week are different facts (Fable, L31).
        log.exception("ceo_money_lens: large claims query failed")
        return None
    result = []
    for r in rows:
        reserve = _num(r, "reserve_amount")
        paid = _num(r, "paid_amount")
        result.append({
            "claim": r.get("claim_number", "?"),
            "policy": r.get("policyNumber", "?"),
            "type": r.get("claim_type") or r.get("type_of_loss") or "—",
            "status": r.get("status") or "—",
            "description": (r.get("incident_description") or "—")[:120],
            "registered": str(r.get("registered") or "—"),
            "reserve": _pula(reserve) if reserve else "—",
            "paid": _pula(paid) if paid else "P0",
        })
    return result


def money_lens(today=None):
    """Business numbers for the CEO brief — trailing month-to-date window.

    e.g. on 11 Sep, the brief covers 1 Sep to 10 Sep. The comparison is the
    same-length window one month earlier (1 Aug to 10 Aug), so the CEO sees
    month-on-month movement, not day-on-day noise.

    Returns {"period", "basis", "tiles"[4], "claims_floor", "large_claims"}.
    large_claims is a list of up to 10 qualifying claims, or None if the
    Graphite query failed (which is NOT the same as none qualifying).
    Never raises: the brief must go out.
    """
    today = today or timezone.localdate()
    day = today - datetime.timedelta(days=1)

    # Trailing month-to-date: 1st of current month to yesterday
    start = day.replace(day=1)
    # Same window one month earlier for comparison
    if start.month == 1:
        prev_start = start.replace(year=start.year - 1, month=12)
    else:
        prev_start = start.replace(month=start.month - 1)
    prev_end = prev_start + (day - start)

    s = start.isoformat()
    e = day.isoformat()
    ps = prev_start.isoformat()
    pe = prev_end.isoformat()

    return {
        "period": "%s to %s" % (start.strftime("%d %b"), day.strftime("%d %b %Y")),
        "basis": "vs %s to %s" % (prev_start.strftime("%d %b"),
                                   prev_end.strftime("%d %b")),
        "tiles": [_collected(s, e, ps, pe), _new_business(s, e, ps, pe),
                  _cancellations(s, e, ps, pe), _claims_paid(s, e, ps, pe)],
        "claims_floor": CLAIM_REPORTING_FLOOR,
        "large_claims": _large_claims(days=10),
    }
