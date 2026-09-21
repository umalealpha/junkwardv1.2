"""watchdog/severity.py — decide whether a failing check is a DANGER (flag only,
a human decides) or a safe WARN (eligible for the switched-off auto-fixer).

The danger list is the CFO's own "What won't be auto-fixed" list from the
2026-08-21 plan. A check tags itself with one or more `domains`; if ANY of them is
dangerous the finding is flagged and can never be auto-fixed, whatever the
WATCHDOG_AUTOFIX_ENABLED switch says.
"""
from __future__ import annotations

# CFO 2026-08-21 — "What won't be auto-fixed".
DANGER_DOMAINS: frozenset[str] = frozenset({
    "finance",       # financial posting logic (GL accounts, journal entries)
    "gl",
    "journal",
    "posting",
    "permissions",   # user permissions, roles, access controls
    "access",
    "frozen",        # frozen numbers (GWP, revenue mapping, MA format)
    "schema",        # database schema changes
    "payment",       # payment processing
    "bank",          # bank integration
    "pii",           # anything touching personal data
})


def classify(domains) -> tuple[bool, str]:
    """Return (flagged, reason). flagged=True means DANGER: report only."""
    hit = sorted(set(d for d in (domains or ()) if d in DANGER_DOMAINS))
    if hit:
        return True, "touches " + ", ".join(hit)
    return False, ""
