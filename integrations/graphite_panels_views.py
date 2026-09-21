"""
integrations/graphite_panels_views.py — small, page-shaped reads over the
Graphite analytics snapshots.

`graphite_feeds_views.py` hands a whole feed back raw, which is right for the
Graphite Feeds viewer and wrong for a panel on a working screen: a page that
wants "the ten brokers running hot" should not pull 13,495 debtor rows and sort
them in the browser. These endpoints do the shaping server-side and return only
what a panel draws.

Read-only, additive. Nothing here writes, and nothing replaces an official
figure — each endpoint fills a place that is empty today. Frozen numbers (the
management-accounts P&L, the dashboard GWP tile) are deliberately untouched.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials

from .models import GraphiteSnapshot
from .graphite_feed_filters import visible_rows
from . import graphite_live_broker_lr
from .graphite_live_broker_lr import ESCALATION_MIN_BOOK

# The CFO's standing underwriting rule: a broker whose loss ratio runs over 70%
# is flagged to the CFO. The panel colours on the same number the rule uses, so
# the screen and the rule can never drift apart.
BROKER_LR_FLAG = 70.0

# Graphite's renewals_trigger feed is itself a 30-day trigger list: every row it
# has ever sent sits between 0 and 30 days to expiry. So this is the widest
# honest default, and the `days` parameter can only narrow it. Asking for 90
# days would show the same 1,337 rows and imply a longer horizon than we have.
RENEWAL_WINDOW_DAYS = 30

# Graphite sends ratios where a person expects percentages: lr_on_reserve
# arrives as 0.5066 for 50.66%, and kyc pct_complete as 0.2 for 20%. Confirmed
# against the live feed on 31-Aug-2026 (lr_on_reserve == |reserve| / premium_fy
# exactly). Converting once, here, keeps every screen off the guess.
RATIO_TO_PCT = 100.0

# Only these columns of the renewals feed reach the browser. The feed carries no
# personal data today; an allowlist means it still would not if Graphite added a
# client name or a phone number to the export tomorrow.
RENEWAL_FIELDS = ('policy_no', 'gfs_ref', 'product_line', 'expiry_date', 'days_to_expiry')


def _rows(dataset: str) -> tuple[list, str | None]:
    """The rows of one snapshot, plus when it arrived. Missing feed -> empty."""
    snap = GraphiteSnapshot.objects.filter(dataset=dataset).first()
    if snap is None:
        return [], None
    rows = (snap.payload or {}).get('rows') or []
    return [r for r in rows if isinstance(r, dict)], (
        snap.received_at.isoformat() if snap.received_at else None)


def _num(value) -> float:
    """Graphite sends numbers as numbers, but a blank cell arrives as '' or None
    and a stray thousands separator has been seen. Never raise on a display read."""
    if value is None or value == '':
        return 0.0
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return 0.0


def _envelope(dataset: str, received_at: str | None, **extra) -> dict:
    body = {
        'dataset': dataset,
        'received_at': received_at,
        'source': 'Graphite Alpha Brain (read-only, refreshed nightly)',
    }
    body.update(extra)
    return body


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def renewals_due(request):
    """Policies coming up for renewal — for the Premium Lapse early-warning page,
    which today shows failing debits only and says nothing about expiry."""
    rows, received = _rows('renewals_trigger')
    # Auto-renewing Instant Insurance (MIS) never needs a renewal action, so it
    # is dropped here too — the panel and the feed viewer stay in step
    # (CFO 2026-09-01).
    rows = visible_rows('renewals_trigger', rows)
    try:
        window = max(1, min(RENEWAL_WINDOW_DAYS,
                            int(request.query_params.get('days') or RENEWAL_WINDOW_DAYS)))
    except (TypeError, ValueError):
        window = RENEWAL_WINDOW_DAYS

    # A blank day count is not day zero. Reading it as 0 puts a phantom policy
    # at the top of the list, which is exactly where it will be believed.
    dated = [r for r in rows if str(r.get('days_to_expiry') or '').strip() != '']
    undated = len(rows) - len(dated)
    due = [r for r in dated if _num(r.get('days_to_expiry')) <= window]
    due.sort(key=lambda r: _num(r.get('days_to_expiry')))

    by_line: dict[str, int] = {}
    for r in due:
        line = str(r.get('product_line') or 'Unclassified')
        by_line[line] = by_line.get(line, 0) + 1

    # Overdue is a real state in this feed: days_to_expiry goes negative once a
    # policy is past its expiry date and has not been renewed.
    return Response(_envelope(
        'renewals_trigger', received,
        window_days=window,
        feed_window_days=RENEWAL_WINDOW_DAYS,
        total_in_feed=len(rows),
        undated_rows=undated,
        due_count=len(due),
        expired_count=sum(1 for r in due if _num(r.get('days_to_expiry')) < 0),
        by_product_line=[{'product_line': k, 'count': v}
                         for k, v in sorted(by_line.items(), key=lambda kv: -kv[1])],
        results=[{k: r.get(k) for k in RENEWAL_FIELDS} for r in due[:200]],
    ))


_LIVE_SOURCE = ('Computed live by Omni from the Graphite read replica. Broker taken '
                'from the policy; claims cost is paid plus the open reserve; measured '
                'against the annual premium of the book the broker holds today.')
_UNAVAILABLE_SOURCE = (
    'Unavailable — the Graphite replica could not be read just now. No broker '
    'figures are shown rather than the pushed ones: that push takes the broker '
    'from the selling agent and double-counts settled claims, so it escalates '
    'the wrong brokers. The raw push is still visible under Graphite Feeds.')


def _shape_broker_lr(rows: list) -> list:
    """One broker row -> the shape the panel draws, with the CFO's rule applied.

    `reserve` is the OPEN reserve — what is still to be paid. Incurred is paid
    plus that. It must never be paid plus the reserve MOVEMENTS: a reserve is
    what later gets paid, so adding both scores a settled claim twice (proved on
    the replica 2026-09-08 — a P2m claim read as P4m).
    """
    out = []
    for r in rows:
        premium = _num(r.get('premium_fy'))
        # The rule runs on the broker's ANNUAL BOOK, not the premium written in
        # the year so far (CFO 2026-09-08). Ten weeks of written premium put
        # Letsema at 11,683% and Spectrum at 811% — arithmetic, not information.
        book = _num(r.get('inforce_book')) or premium
        paid, reserve = _num(r.get('payment')), _num(r.get('reserve'))
        reserve_lr = _num(r.get('lr_on_reserve')) * RATIO_TO_PCT
        incurred = abs(paid) + abs(reserve)
        incurred_lr = (incurred / book * RATIO_TO_PCT) if book else 0.0
        # Too small to escalate is NOT the same as fine: the row still shows, and
        # still shows its ratio — it just does not reach the CFO on its own.
        too_small = book < ESCALATION_MIN_BOOK
        out.append({
            'broker': r.get('broker') or 'Unnamed',
            'premium_fy': premium,
            'inforce_book': book,
            'payment': paid,
            'reserve': reserve,
            'claim_count': int(_num(r.get('claim_count'))),
            'incurred_lr_pct': round(incurred_lr, 2),
            'reserve_lr_pct': round(reserve_lr, 2),
            'below_escalation_size': too_small,
            'flagged': incurred_lr > BROKER_LR_FLAG and not too_small,
        })
    out.sort(key=lambda r: -r['incurred_lr_pct'])
    return out


@api_view(['GET'])
@permission_classes([CanViewFinancials])
def broker_loss_ratios(request):
    """Broker loss ratios, worst first, with the CFO's 70% rule applied.

    The feed's own `lr_on_reserve` is exactly |reserve| / premium_fy — it leaves
    PAID claims out. A broker with a million in premium, 800k already paid and
    50k still reserved scores 5% on that measure and passes the 70% rule in
    green, when what the business has actually incurred is 85%. So the rule is
    applied to INCURRED — paid plus reserve — and the feed's reserve-only figure
    is carried alongside it under its own name rather than being relabelled as
    the loss ratio.

    The rows are computed live from the Graphite replica where that is reachable
    (broker taken from the policy, reserve read as the OPEN reserve), because the
    pushed feed took the broker from the selling agent and double-counted every
    settled claim. If the replica cannot be read the panel FAILS CLOSED — no
    broker rows and `computed_live: false` — rather than reverting to that push
    and escalating the wrong brokers.
    """
    # Fails CLOSED. The pushed snapshot is not a safe stand-in here: it escalates
    # a different, wrong set of brokers, and a rule that quietly reverts to the
    # wrong answer is worse than one that says it cannot answer (CFO 2026-09-08).
    live = graphite_live_broker_lr.build('broker_lr')
    computed_live = live is not None
    out = _shape_broker_lr(live) if computed_live else []
    source = _LIVE_SOURCE if computed_live else _UNAVAILABLE_SOURCE

    return Response(_envelope(
        'broker_lr', None,
        source=source,
        # Machine-readable, so a screen can show the fallback state rather than
        # relying on somebody reading the source sentence.
        computed_live=computed_live,
        threshold_pct=BROKER_LR_FLAG,
        min_book_to_escalate=ESCALATION_MIN_BOOK,
        broker_count=len(out),
        flagged_count=sum(1 for r in out if r['flagged']),
        total_premium_fy=round(sum(r['premium_fy'] for r in out), 2),
        results=out,
    ))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def kyc_completeness(request):
    """KYC completeness rolled up by branch, plus the agents furthest behind.

    The feed is per agent (803 rows). A compliance screen wants the branch
    picture first and the individual agents second, so both are returned.
    """
    rows, received = _rows('kyc_completeness')

    branches: dict[str, dict] = {}
    for r in rows:
        b = str(r.get('branch') or 'Unassigned')
        acc = branches.setdefault(b, {'branch': b, 'policies': 0, 'complete': 0, 'agents': 0})
        acc['policies'] += int(_num(r.get('policies')))
        acc['complete'] += int(_num(r.get('complete')))
        acc['agents'] += 1
    for acc in branches.values():
        acc['pct_complete'] = round(100.0 * acc['complete'] / acc['policies'], 1) if acc['policies'] else 0.0

    agents = [{
        'agent': r.get('agent') or 'Unnamed',
        'branch': r.get('branch') or 'Unassigned',
        'policies': int(_num(r.get('policies'))),
        'complete': int(_num(r.get('complete'))),
        'pct_complete': round(_num(r.get('pct_complete')) * RATIO_TO_PCT, 1),
    } for r in rows]
    # Worst first, but an agent with two policies at 0% is noise next to one with
    # four hundred — rank by the number of policies actually missing KYC.
    agents.sort(key=lambda a: -(a['policies'] - a['complete']))

    total_policies = sum(a['policies'] for a in agents)
    total_complete = sum(a['complete'] for a in agents)

    return Response(_envelope(
        'kyc_completeness', received,
        agent_count=len(agents),
        total_policies=total_policies,
        total_complete=total_complete,
        pct_complete=round(100.0 * total_complete / total_policies, 1) if total_policies else 0.0,
        by_branch=sorted(branches.values(), key=lambda b: b['pct_complete']),
        worst_agents=agents[:25],
    ))


@api_view(['GET'])
@permission_classes([CanViewFinancials])
def major_claims(request):
    """The large-loss list, biggest exposure first — paid plus reserve.

    Computed from omni's live claims mirror when available, NOT the pushed
    snapshot (CFO 2026-09-01) — otherwise this panel would show the stale/broken
    Alpha-Brain figures while the Graphite-Feeds card shows the live ones, the
    exact contradiction we are removing. Falls back to the snapshot if the mirror
    is empty."""
    from . import graphite_live_claims
    if graphite_live_claims.available():
        from django.db.models import F, Sum
        from .models import GraphiteClaim
        qs = (GraphiteClaim.objects
              .annotate(_exp=F('total_payment') + F('total_reserve'))
              .order_by('-_exp'))
        out = []
        for c in qs[:100]:
            paid, reserve = _num(c.total_payment), _num(c.total_reserve)
            out.append({
                'claim_no': c.claim_number or '—',
                'claim_type': c.claim_type or '—',
                'status': c.status or '—',
                'paid': paid,
                'reserve': reserve,
                'exposure': round(abs(paid) + abs(reserve), 2),
                'repudiated': 'repudiat' in (c.status or '').lower(),
            })
        agg = GraphiteClaim.objects.aggregate(p=Sum('total_payment'), r=Sum('total_reserve'))
        synced = graphite_live_claims.last_synced()
        return Response(_envelope(
            'major_claims', synced.isoformat() if synced else None,
            claim_count=GraphiteClaim.objects.count(),
            total_exposure=round(abs(_num(agg['p'])) + abs(_num(agg['r'])), 2),
            repudiated_count=GraphiteClaim.objects.filter(status__icontains='repudiat').count(),
            results=out,
        ))

    rows, received = _rows('major_claims')
    out = []
    for r in rows:
        paid, reserve = _num(r.get('paid')), _num(r.get('reserve'))
        repudiated = r.get('repudiated')
        out.append({
            'claim_no': r.get('claim_no') or '—',
            'claim_type': r.get('claim_type') or '—',
            'status': r.get('status') or '—',
            'paid': paid,
            'reserve': reserve,
            'exposure': round(abs(paid) + abs(reserve), 2),
            'repudiated': bool(repudiated) and str(repudiated).strip().lower() not in ('no', 'n', 'false', '0'),
        })
    out.sort(key=lambda r: -r['exposure'])

    return Response(_envelope(
        'major_claims', received,
        claim_count=len(out),
        total_exposure=round(sum(r['exposure'] for r in out), 2),
        repudiated_count=sum(1 for r in out if r['repudiated']),
        results=out[:100],
    ))
