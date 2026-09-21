"""
integrations/graphite_feed_filters.py — shared, display-only shaping for the
Graphite analytics snapshots, used by BOTH the feed viewer
(graphite_feeds_views) and the page panels (graphite_panels_views) so the two
can never drift.

Nothing here writes or changes a stored snapshot — it only decides what a screen
shows and totals the columns worth totalling.
"""
from __future__ import annotations

# Instant Insurance policies carry the MIS policy-number prefix and AUTO-RENEW,
# so they never need a renewal action (CFO 2026-09-01: "instant insurance is
# automatically renewed thus we dont need it in that section"). They are dropped
# from the renewals feed only — every other feed is left exactly as Graphite
# sent it. Prefix, not product line: the prefix is the stable book marker
# (verified 2026-09-01: 1,049 of 1,266 renewal rows are MIS; the rest are DOMG /
# COMG, the annual policies that do need chasing).
AUTO_RENEW_POLICY_PREFIXES = ('MIS',)

# Datasets whose renewals are auto-renewing Instant Insurance and should be
# hidden from the "renewals due" view. Keyed by dataset so the rule is explicit.
_AUTO_RENEW_DATASETS = ('renewals_trigger',)


def _is_auto_renew(row: dict) -> bool:
    pol = str(row.get('policy_no') or row.get('policy_number') or '').strip().upper()
    return pol.startswith(AUTO_RENEW_POLICY_PREFIXES)


def visible_rows(dataset: str, rows: list) -> list:
    """The rows a screen should show for this dataset. Only the renewals feed is
    narrowed (auto-renewing Instant Insurance removed); all others pass through."""
    if dataset in _AUTO_RENEW_DATASETS:
        return [r for r in rows if isinstance(r, dict) and not _is_auto_renew(r)]
    return [r for r in rows if isinstance(r, dict)]


# ── Column totals ───────────────────────────────────────────────────────────
# A total row helps every feed (CFO 2026-09-01: "where is the total here"). Only
# ADDITIVE money/count columns are summed — summing a day-count, a date, an id,
# a percentage or a ratio is meaningless, so those are skipped by name.
import re

_NOT_ADDITIVE = re.compile(
    r'(date|days?_to|_pct$|pct_|percent|ratio|_no$|number|ref$|_id$|^id$|flag|repudiated)',
    re.I,
)


def _num(value) -> float | None:
    if value is None or value == '':
        return None
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def column_totals(rows: list) -> dict:
    """{column: sum} for every column that is genuinely additive across `rows`.

    A column is summed only if every non-empty value in it is numeric AND its
    name is not an id / date / day-count / percentage / ratio. Returns {} when
    nothing is summable (e.g. the renewals list) — the caller then shows just the
    row count.
    """
    if not rows:
        return {}
    cols = list(rows[0].keys()) if isinstance(rows[0], dict) else []
    totals: dict[str, float] = {}
    for c in cols:
        if _NOT_ADDITIVE.search(c):
            continue
        seen_number = False
        ok = True
        running = 0.0
        for r in rows:
            v = r.get(c)
            if v is None or v == '':
                continue
            n = _num(v)
            if n is None:
                ok = False
                break
            seen_number = True
            running += n
        if ok and seen_number:
            totals[c] = round(running, 2)
    return totals
