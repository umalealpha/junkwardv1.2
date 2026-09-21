"""
core/intel_summary.py — Omni's high-level intelligence feed (CFO directive
2026-07-25).

WHY: Alpha Brain (the Graphite bolt-on) already sends Omni a PII-free compliance
census — see core/compliance_brain.py, which PULLS from the brain. This is the
OTHER direction: a single read-only endpoint that lets the brain (or any
authorised machine consumer) ask Omni for the numbers only Omni knows — GWP for
a month from the posted GL, net claims incurred, the loss ratios — alongside the
policy census straight off the Graphite read replica.

Hard rules:
- AGGREGATES ONLY. Every value is a count or a total. No customer rows, no policy
  numbers, no names, no bank details. There is nothing here to mask because
  nothing row-level is selected.
- Shared bearer token (INTEL_SUMMARY_TOKEN), constant-time compare, fail CLOSED:
  no token configured = nobody gets in. Same pattern as REFUND_INBOUND_TOKEN.
- READ-ONLY. The GL is read through reporting.ma_pl (the MA-reconciled path, so
  the figure matches the CFO's management accounts); the policy census is a
  COUNT/GROUP BY on the Graphite read replica via aware.engine.run_select, which
  is SELECT-guarded and rejects PII columns.
- Each block soft-fails on its own. A dead replica must not blank the GL figures.

POLICY-COUNT DEFINITION (the reason this block reports three numbers, not one):
Graphite's `policies.status` is 1 = activated / in force, 0 = never activated,
2 = lapsed or otherwise inactive. Alpha Brain's census counts status IN (0,1)
and labels the result "active policies", so its figure includes policies that
were never activated. We publish all three counts so the two systems reconcile
arithmetically instead of arguing:
    brain "active"  ==  active_total + not_activated_total
"""
from __future__ import annotations

import hmac
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Graphite policy-number prefixes → the three product categories the board uses.
# Mirrors Alpha Brain's own bucketing (brain/lib/complianceCensus.js) so the two
# censuses are comparable line for line.
_CATEGORY_SQL = """
  SELECT CASE
           WHEN policyNumber LIKE 'MIS%' OR policyNumber LIKE 'ADH%'  THEN 'MIS'
           WHEN policyNumber LIKE 'DOMG%' OR policyNumber LIKE 'DOMD%' THEN 'DOM'
           WHEN policyNumber LIKE 'COMG%' OR policyNumber LIKE 'COMD%' THEN 'COM'
           ELSE 'OTHER' END AS category,
         status,
         COUNT(*)           AS n
  FROM policies
  GROUP BY category, status
  LIMIT 100
"""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def token_ok(request) -> bool:
    """Constant-time bearer check. Fails closed when no token is configured."""
    expected = (getattr(settings, 'INTEL_SUMMARY_TOKEN', '') or '').strip()
    if not expected:
        return False
    got = (request.headers.get('Authorization') or '').replace('Bearer ', '').strip()
    return bool(got) and hmac.compare_digest(got, expected)


def client_ip(request) -> str:
    """The real caller's address, as the house pattern resolves it.

    SECURITY: prod sits behind Cloudflare → ALB → Caddy → gunicorn, so
    REMOTE_ADDR is the PROXY HOP, not the caller. Cloudflare overwrites
    CF-Connecting-IP with the true client and the client cannot forge it; the
    left-most X-Forwarded-For value IS client-controlled and must never be
    trusted. Mirrors core/admin_ip_allowlist._client_ip.
    """
    cf = (request.META.get('HTTP_CF_CONNECTING_IP') or '').strip()
    if cf:
        return cf
    return (request.META.get('REMOTE_ADDR') or '').strip()


def caller_allowed(request) -> bool:
    """Optional second wall: restrict the feed to known caller addresses.

    Set INTEL_SUMMARY_ALLOWED_IPS to a comma-separated list of EXACT addresses
    (Alpha Brain calls from a fixed private address behind its internal load
    balancer, so exact values are what you want). A trailing-dot prefix such as
    '10.0.' is also accepted for a deliberately pinned narrow range — never use
    a prefix that spans the whole private range, because behind a proxy that
    matches every request and admits everyone while looking locked.

    Empty = no address restriction, so behaviour is unchanged until the brain's
    address is known and pinned. Raised by the DeepSeek review 2026-07-25: a
    shared token with no audience limit exposes every entity's GL aggregates if
    it ever leaks.
    """
    raw = (getattr(settings, 'INTEL_SUMMARY_ALLOWED_IPS', '') or '').strip()
    if not raw:
        return True
    ip = client_ip(request)
    if not ip:
        return False
    for entry in (e.strip() for e in raw.split(',')):
        if not entry:
            continue
        if entry.endswith('.') and ip.startswith(entry):
            return True
        if ip == entry:
            return True
    return False


# ---------------------------------------------------------------------------
# Period helpers — the financial year runs 1 Jul → 30 Jun.
# ---------------------------------------------------------------------------
def month_bounds(month: str | None, today: date | None = None) -> tuple[date, date, str]:
    """'YYYY-MM' → (first day, last day, label). Default = last COMPLETE month.

    Raises ValueError on a malformed month so the caller can 400 rather than
    silently reporting the wrong period.
    """
    today = today or timezone.localdate()
    if month:
        parts = str(month).strip().split('-')
        if len(parts) != 2:
            raise ValueError("month must be 'YYYY-MM'.")
        try:
            year, mon = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError("month must be 'YYYY-MM'.") from None
        if not (1 <= mon <= 12) or not (2000 <= year <= 2100):
            raise ValueError("month out of range.")
    else:
        year, mon = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    first = date(year, mon, 1)
    last = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    from datetime import timedelta
    return first, last - timedelta(days=1), f'{year:04d}-{mon:02d}'


def fy_start_for(d: date) -> date:
    """First day of the financial year containing `d` (FY = 1 Jul → 30 Jun)."""
    return date(d.year if d.month >= 7 else d.year - 1, 7, 1)


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------
def policy_census() -> dict:
    """Counts of Graphite policies by category and status. Aggregates only."""
    from aware.engine import run_select
    _, rows = run_select(_CATEGORY_SQL)
    active: dict[str, int] = {}
    not_activated: dict[str, int] = {}
    inactive: dict[str, int] = {}
    for r in rows:
        cat = str(r.get('category') or 'OTHER')
        n = int(r.get('n') or 0)
        bucket = {1: active, 0: not_activated}.get(r.get('status'), inactive)
        bucket[cat] = bucket.get(cat, 0) + n
    return {
        'definition': 'active = Graphite policies.status = 1 (activated, in force)',
        'active_total': sum(active.values()),
        'active_by_category': active,
        'not_activated_total': sum(not_activated.values()),
        'not_activated_by_category': not_activated,
        'lapsed_or_inactive_total': sum(inactive.values()),
        'reconciles_to_brain_active': sum(active.values()) + sum(not_activated.values()),
        'reconciliation_note': (
            "Alpha Brain's census counts status IN (0,1); that total equals "
            "active_total + not_activated_total."
        ),
        'source': 'graphite_read_replica',
    }


# The canonical code-or-UUID resolver now lives in core.mixins (the company
# scoping module) so every company-filtered view shares ONE implementation.
# Re-exported here because core.intel_summary.resolve_company is the name the
# CFO's 2026-07-28 standing rule points at.
from core.mixins import resolve_company    # noqa: E402  (re-export)


def _company_pk(code: str | None):
    """Company code (e.g. 'ADIC') or UUID id → primary key. None = rollup."""
    if not code:
        return None, None
    co = resolve_company(code)
    if not co:
        raise ValueError(f'Unknown company code: {code}')
    return co.id, co.code


def _dec(v) -> str:
    try:
        return str(Decimal(str(v or '0')).quantize(Decimal('0.01')))
    except (InvalidOperation, TypeError, ValueError):
        return '0.00'


def gl_block(from_date: date, to_date: date, company_pk) -> dict:
    """Posted-GL figures for the month and the financial year to date.

    Reads through reporting.ma_pl, the same path the management accounts and the
    dashboard use — so a figure served here cannot disagree with the MA pack.
    """
    from core.aria.tools import get_pl_summary
    month = get_pl_summary(from_date, to_date, company_id=company_pk)
    if 'error' in month:
        raise RuntimeError(month['error'])
    ytd = get_pl_summary(fy_start_for(from_date), to_date, company_id=company_pk)
    return {
        'currency': 'BWP',
        'month': {
            'gwp': _dec(month.get('gwp')),
            'net_earned_premium': _dec(month.get('nep')),
            'gross_profit': _dec(month.get('gp')),
            'pat': _dec(month.get('pat')),
            'gross_loss_ratio': str(month.get('gross_loss_ratio') or '0'),
            'net_loss_ratio': str(month.get('net_loss_ratio') or '0'),
        },
        'financial_year_to_date': {
            'from': fy_start_for(from_date).isoformat(),
            'gwp': _dec(ytd.get('gwp')) if 'error' not in ytd else None,
            'pat': _dec(ytd.get('pat')) if 'error' not in ytd else None,
        },
        'basis': 'posted journal entries only (status=posted), MA P&L mapping',
        'source': 'omni_general_ledger',
    }


def feed_company_code(requested: str | None) -> str | None:
    """The company Alpha Brain is allowed to see.

    CFO decision 2026-07-26: "Brain sees ADIC." INTEL_SUMMARY_COMPANY pins the
    scope for the token feed, so asking for another entity — or for the
    all-company rollup by omitting the parameter — cannot widen it. Set it to
    'ALL' to deliberately allow the rollup.
    """
    pinned = (getattr(settings, 'INTEL_SUMMARY_COMPANY', '') or '').strip()
    if not pinned or pinned.upper() == 'ALL':
        return requested
    return pinned


def month_is_posted(gl: dict | None) -> bool:
    """Did this month's revenue actually reach the ledger?

    ADIC has no posted revenue for May/June 2026 (0.00, -1.00) because the CFO
    is bringing those in as opening balances at 1 July 2026 rather than posting
    the months. A near-zero GWP is therefore 'not posted', NOT 'we earned
    nothing' — the page must say so instead of showing a misleading zero.
    """
    if not gl:
        return False
    try:
        return abs(Decimal(str(gl.get('month', {}).get('gwp') or '0'))) > Decimal('1000')
    except (InvalidOperation, TypeError, ValueError):
        return False


def build_summary(month: str | None = None, company_code: str | None = None,
                  today: date | None = None) -> dict:
    """The whole payload. Aggregates only. Each block soft-fails independently."""
    from django.utils import timezone
    first, last, label = month_bounds(month, today=today)
    company_pk, resolved_code = _company_pk(company_code)

    out: dict = {
        'generated_at': timezone.now().isoformat(),
        'period': {'month': label, 'from': first.isoformat(), 'to': last.isoformat()},
        'company': resolved_code or 'ALL',
        'pii': 'none — counts and totals only',
        'errors': {},
    }
    for key, fn in (('policies', policy_census),
                    ('premium', lambda: gl_block(first, last, company_pk))):
        try:
            out[key] = fn()
        except Exception as exc:      # noqa: BLE001 — one dead source must not void the rest
            # The full reason goes to OUR log; the caller is told only the
            # exception class. A DB error message can carry the replica hostname
            # and we do not hand infrastructure detail to an outside consumer.
            logger.warning('intel_summary %s block failed: %s', key, exc)
            out[key] = None
            out['errors'][key] = f'unavailable ({type(exc).__name__})'
    out['month_posted'] = month_is_posted(out.get('premium'))
    return out


# ---------------------------------------------------------------------------
# The staff view — same numbers, plus what Alpha Brain currently believes.
# ---------------------------------------------------------------------------
def _traffic_light(ours, theirs, tolerance: Decimal = Decimal('0.01')) -> str:
    """green = the two systems agree · amber = they drift · red = a side is down."""
    if ours is None or theirs is None:
        return 'red'
    try:
        a, b = Decimal(str(ours)), Decimal(str(theirs))
    except (InvalidOperation, TypeError, ValueError):
        return 'red'
    if a == b:
        return 'green'
    biggest = max(abs(a), abs(b))
    if biggest and abs(a - b) / biggest <= tolerance:
        return 'green'
    return 'amber'


def _brain_policy_total(kyc) -> int | None:
    """Alpha Brain's own policy count, out of whatever shape it sent.

    We store the brain's `kyc` block verbatim and nothing else in Omni reads
    inside it, so the exact key is not something this repo can guarantee — the
    brain is Node, where camelCase is at least as likely as snake_case. Accept
    either spelling rather than let a naming difference park the traffic light
    on red for ever. Returns None when there is nothing countable.
    """
    if not isinstance(kyc, dict):
        return None
    for key in ('by_category', 'byCategory'):
        by_cat = kyc.get(key)
        if isinstance(by_cat, dict) and by_cat:
            try:
                return sum(int(v or 0) for v in by_cat.values())
            except (TypeError, ValueError):
                return None
    return None


def build_comparison(month: str | None = None, company_code: str | None = None,
                     today: date | None = None) -> dict:
    """build_summary() plus the Alpha Brain side, so the two can be read together.

    The headline policy number is OUR active book (CFO 2026-07-26: "I want to see
    62,347 only"). Alpha Brain's figure is carried alongside purely so the gap is
    visible and settled on screen instead of in email — it is never the headline.
    """
    out = build_summary(month=month, company_code=company_code, today=today)
    from core.compliance_brain import latest_summary
    try:
        brain = latest_summary()
    except Exception as exc:      # noqa: BLE001 — the brain being down is not our failure
        logger.warning('intel comparison: brain summary unavailable: %s', exc)
        brain = {'activated': False, 'note': f'unavailable ({type(exc).__name__})'}

    ours = (out.get('policies') or {}).get('reconciles_to_brain_active')
    theirs = _brain_policy_total(brain.get('kyc'))

    # If the brain HAS reported but we could not find a census breakdown in its
    # payload, say so. Otherwise the light sits on red for ever while the page
    # also insists the brain is activated — a silent failure with no symptom.
    note = brain.get('note') or ''
    if brain.get('activated') and theirs is None:
        note = (note + ' · ' if note else '') + (
            'Alpha Brain reported, but its payload carried no policy-count '
            'breakdown to compare against.')

    out['brain'] = {
        'activated': bool(brain.get('activated')),
        'as_of': brain.get('as_of'),
        'note': note,
        'narrative': brain.get('ai_narrative') or '',
        'their_active_total': theirs,
        'our_same_basis_total': ours,
        'agree': _traffic_light(ours, theirs),
        'basis_note': (
            "Alpha Brain counts Graphite status 0 and 1 together and calls that "
            "active; status 0 was never activated. Compared here on that same "
            "basis so the two are like for like — the active book above is the "
            "number to use."
        ),
    }
    return out
