"""Reconciliation Hub — Phase 1 constants.

Scope decisions (CFO, 2026-07-03):
- Entity: ADIC INCLUDING Instant. In omni there is a single ``core.Company``
  with ``code='ADIC'``; Instant Insurance is a product line represented by
  dedicated GL accounts *inside* ADIC's chart (e.g. ``78`` Instant Insurance
  A/R, ``103000`` Claims Settlement — Instant). Scoping the run to company
  ADIC therefore already unions ADIC + Instant on the ledger side.
- Metrics: GWP, Premium Debtors (ageing), Claims, Policy count.
- Variance tolerance: 5% (green <= 5%, red > 5%).

Data-source limitation flagged for CFO / Arjun & Pramod (Phase 2):
The Graphite read-only replica ageing table is Dom-Com only
(``summary_age_analyst_report_dom_com_2025``). Instant-book ageing is NOT in
that table, so the automated ageing tie-out compares Dom-Com like-for-like.
Instant debtors ageing needs a source before it can be tied out automatically.
"""

from decimal import Decimal

ZERO = Decimal('0.00')

# Company codes that make up the ADIC reconciliation scope. Instant lives inside
# ADIC's ledger, so a single code suffices; override via settings.RECON_ENTITY_CODES.
DEFAULT_ENTITY_CODES = ['ADIC']

# Default variance tolerance (percent). Editable per-run.
DEFAULT_TOLERANCE_PCT = Decimal('5.00')


class MetricKey:
    GWP = 'gwp'
    PREMIUM_DEBTORS = 'premium_debtors'
    CLAIMS = 'claims'
    POLICY_COUNT = 'policy_count'


class FlowType:
    """Whether a GL metric is a period flow (P&L) or an as-of balance (BS)."""
    PERIOD = 'period'
    BALANCE = 'balance'
    NONE = 'none'  # no GL side (e.g. a pure count metric)

    CHOICES = [
        (PERIOD, 'Period flow (P&L)'),
        (BALANCE, 'As-of balance (BS)'),
        (NONE, 'No GL side'),
    ]


class Unit:
    BWP = 'bwp'
    COUNT = 'count'

    CHOICES = [(BWP, 'BWP'), (COUNT, 'Count')]


class SourceSystem:
    GRAPHITE_RDS = 'graphite_rds'   # read-only replica, already wired in omni
    REGISTER = 'register'           # manual expected-figures register (CFO/finance)
    REPORTING_PORTAL = 'reporting_portal'  # Phase 2 — adrisk pre-generated extracts

    CHOICES = [
        (GRAPHITE_RDS, 'Graphite (read-only replica)'),
        (REGISTER, 'Expected-figures register (manual)'),
        (REPORTING_PORTAL, 'Reporting portal extract'),
    ]


class LineStatus:
    MATCHED = 'matched'          # both sides present, |variance%| <= tolerance
    BREACH = 'breach'            # both sides present, |variance%| > tolerance
    NO_SOURCE = 'no_source'      # no source figure captured for the period
    NO_OMNI = 'no_omni_side'     # metric has no GL mapping (e.g. policy count)

    CHOICES = [
        (MATCHED, 'Matched'),
        (BREACH, 'Breach'),
        (NO_SOURCE, 'No source figure'),
        (NO_OMNI, 'No omni GL side'),
    ]


class RunStatus:
    COMPLETED = 'completed'
    PARTIAL = 'partial'   # some metrics missing a side
    FAILED = 'failed'

    CHOICES = [
        (COMPLETED, 'Completed'),
        (PARTIAL, 'Partial'),
        (FAILED, 'Failed'),
    ]


# Canonical 4-bucket ageing scale used for the tie-out panel. Both omni's 5-bucket
# scale and Graphite's buckets are folded into these for like-for-like comparison.
CANONICAL_BUCKETS = ['0-30', '31-60', '61-90', '90+']

# omni _AGING_BUCKETS label -> canonical bucket
OMNI_BUCKET_MAP = {
    'current': '0-30',
    'days_31_60': '31-60',
    'days_61_90': '61-90',
    'days_91_120': '90+',
    'over_120': '90+',
}

# Graphite build_graphite_age_analysis summary bucket key -> canonical bucket
GRAPHITE_BUCKET_MAP = {
    '0-30': '0-30',
    '31-60': '31-60',
    '61-90': '61-90',
    '120+': '90+',
}
