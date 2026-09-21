"""rewards/nexus_growth.py — Alpha Nexus growth features (CFO picks, 2026-08-11).

One module, no new tables:

  * FIRST-WIN QUEST  — 3 starter steps (log an activity, pulse scan, first
    drive). Completing all 3 pays a one-time bonus. Fixes "nobody passed
    Bronze fast enough to care".
  * STREAK           — consecutive days with any earning action. Display only.
  * WEEKLY CHALLENGE — record 3 drives Mon–Sun; pays a bonus once per week.
  * PREMIUM SAVINGS  — the tier discount expressed in Pula against the
    member's real premium (read-only Graphite lookup, cached), or the
    aspirational copy when no policy/premium is known.
  * LEADERBOARD      — the safe-driving board the home screen has promised
    since launch. Reuses nexus_standings.ranked_testers(); other members are
    shown as FIRST NAME + LAST INITIAL only (no emails, no ids — DPA).

Award integrity: bonuses are PointsTransaction rows with fixed detail strings
used as idempotency keys, written inside a select_for_update() transaction —
the same pattern customer_drive_trip uses. A member can never be paid a quest
or a given week's challenge twice, no matter how often the endpoint is hit.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import (
    RewardMember, PointsTransaction, RewardProgram, CustomerActivity,
    CustomerDriveTrip, CustomerStepDay,
)
from . import health_points

log = logging.getLogger(__name__)

QUEST_BONUS = 40
QUEST_DETAIL = 'First-win quest complete'
WEEKLY_TARGET_TRIPS = 3
WEEKLY_BONUS = 15
STREAK_LOOKBACK_DAYS = 60   # bound the day-set query; nobody streaks past this


def _weekly_detail(day) -> str:
    y, w, _ = day.isocalendar()
    return f'Weekly challenge won · {y}-W{w:02d}'


def _award(member: RewardMember, points: int, detail: str, program_code) -> bool:
    """Pay `points` once per unique `detail` for this member. True if paid now."""
    with transaction.atomic():
        try:
            m = RewardMember.objects.select_for_update().get(pk=member.pk)
        except RewardMember.DoesNotExist:
            return False
        if PointsTransaction.objects.filter(member=m, detail=detail).exists():
            return False
        program, _ = RewardProgram.objects.get_or_create(
            code=program_code, defaults={'name': 'Health & Wellness Points', 'is_active': True})
        PointsTransaction.objects.create(
            member=m, program=program, kind=PointsTransaction.Kind.EARN,
            points=points, occurred_at=timezone.now(), detail=detail)
        m.points_balance = (m.points_balance or 0) + points
        m.tier = health_points.tier_for(m.points_balance)
        m.save(update_fields=['points_balance', 'tier', 'updated_at'])
        member.points_balance = m.points_balance
        member.tier = m.tier
    return True


# ---------------------------------------------------------------------------
# Quest / streak / challenge
# ---------------------------------------------------------------------------

def quest_state(member: RewardMember) -> dict:
    """3 starter steps + one-time completion bonus (awarded here, idempotent)."""
    # Real activity = a paid activity row OR a paid day of device-synced steps
    # (typed steps/workouts no longer exist, so synced steps must count here).
    did_activity = (CustomerActivity.objects.filter(member=member, points_awarded__gt=0).exists()
                    or CustomerStepDay.objects.filter(member=member, points_awarded__gt=0).exists())
    did_scan = member.health_metrics.filter(resting_hr__isnull=False).exists()
    # is_valid only — a 0 km recording must not complete the quest, hold a
    # streak alive, or tick the weekly challenge.
    did_drive = CustomerDriveTrip.objects.filter(member=member, is_valid=True).exists()
    steps = [
        {'key': 'activity', 'label': 'Log any activity', 'done': did_activity},
        {'key': 'scan',     'label': 'Do a 30-second pulse check', 'done': did_scan},
        {'key': 'drive',    'label': 'Record your first drive', 'done': did_drive},
    ]
    complete = all(s['done'] for s in steps)
    already_paid = PointsTransaction.objects.filter(member=member, detail=QUEST_DETAIL).exists()
    bonus_paid_now = False
    if complete and not already_paid:
        bonus_paid_now = _award(member, QUEST_BONUS, QUEST_DETAIL, RewardProgram.Code.HEALTH)
    return {
        'steps': steps,
        'complete': complete,
        'bonus': QUEST_BONUS,
        'bonusPaid': complete and (already_paid or bonus_paid_now),
        'bonusPaidNow': bonus_paid_now,
    }


def _active_days(member: RewardMember) -> set:
    """Distinct local dates (recent window) on which the member did anything real."""
    since = timezone.localdate() - timedelta(days=STREAK_LOOKBACK_DAYS)
    days = set(CustomerActivity.objects.filter(
        member=member, points_awarded__gt=0, occurred_at__date__gte=since)
        .values_list('occurred_at__date', flat=True))
    days |= set(CustomerDriveTrip.objects.filter(
        member=member, is_valid=True, started_at__date__gte=since)
        .values_list('started_at__date', flat=True))
    days |= set(CustomerStepDay.objects.filter(
        member=member, points_awarded__gt=0, day__gte=since)
        .values_list('day', flat=True))
    days |= set(member.health_metrics.filter(
        resting_hr__isnull=False, date__gte=since).values_list('date', flat=True))
    return days


def streak_state(member: RewardMember) -> dict:
    """Consecutive active days ending today (or yesterday — today still open)."""
    days = _active_days(member)
    today = timezone.localdate()
    anchor = today if today in days else today - timedelta(days=1)
    n = 0
    d = anchor
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return {'days': n, 'activeToday': today in days}


def weekly_challenge_state(member: RewardMember) -> dict:
    """Record WEEKLY_TARGET_TRIPS drives Mon–Sun → one-time weekly bonus."""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    trips = CustomerDriveTrip.objects.filter(
        member=member, is_valid=True,
        started_at__date__gte=monday, started_at__date__lte=today).count()
    detail = _weekly_detail(today)
    already_paid = PointsTransaction.objects.filter(member=member, detail=detail).exists()
    paid_now = False
    if trips >= WEEKLY_TARGET_TRIPS and not already_paid:
        paid_now = _award(member, WEEKLY_BONUS, detail, RewardProgram.Code.DRIVING)
    return {
        'label': f'Record {WEEKLY_TARGET_TRIPS} drives this week',
        'progress': min(trips, WEEKLY_TARGET_TRIPS),
        'target': WEEKLY_TARGET_TRIPS,
        'bonus': WEEKLY_BONUS,
        'done': trips >= WEEKLY_TARGET_TRIPS,
        'bonusPaid': already_paid or paid_now,
    }


# ---------------------------------------------------------------------------
# Premium savings (tier discount x real premium when we can know it)
# ---------------------------------------------------------------------------

def _graphite_premium(policy_number: str):
    """Best-effort monthly premium for a policy via the read-only Graphite door.
    Cached; any failure (no DSN locally, schema drift, timeout) returns None —
    the card then shows the aspirational copy instead of an error."""
    if not policy_number:
        return None
    key = f'nexus:premium:{policy_number}'
    hit = cache.get(key)
    if hit is not None:
        return hit if hit != 'none' else None
    value = None
    try:
        from integrations.graphite_ro import query
        # Schema probed live 2026-08-11: table `policies`, columns `policyNumber`
        # (camelCase) + `premium` (monthly) / `annual_premium`.
        rows = query(
            "SELECT premium FROM policies WHERE policyNumber = %s "
            "ORDER BY id DESC LIMIT 1", [policy_number])
        if rows:
            raw = list(rows[0].values())[0] if isinstance(rows[0], dict) else rows[0][0]
            if raw is not None:
                value = float(raw)
    except Exception as exc:  # noqa: BLE001 — read-only nicety, never breaks home
        log.info('nexus premium lookup skipped for %s: %s', policy_number, exc)
    cache.set(key, value if value is not None else 'none', 6 * 3600)
    return value


def savings_state(member: RewardMember, discount_pct: int) -> dict:
    """Premium-savings card. `discount_pct` is the COMPLIANT premium discount
    (customer_views.premium_discount_pct) — driven by NON-health factors only,
    and 0 until a CFO-signed table exists. The wellness tier must NOT appear
    here, and there is deliberately no "reach the next tier for a bigger premium
    discount" ladder — that would tie health points to insurance pricing, which
    Google Play forbids (Fable v10 review)."""
    pct = int(discount_pct or 0)
    premium = _graphite_premium(member.policy_number)
    monthly = round(premium * pct / 100.0, 2) if (premium and pct) else None
    return {
        'discountPct': pct,
        'monthlyPremium': premium,
        'monthlySaving': monthly,
        'yearlySaving': round(monthly * 12, 2) if monthly else None,
        'nextTier': None,
        'nextTierPct': None,
        'policyLinked': bool(member.policy_number),
    }


# ---------------------------------------------------------------------------
# Leaderboard (anonymised) + policy & claims card
# ---------------------------------------------------------------------------

def _public_name(name: str) -> str:
    parts = [p for p in (name or '').strip().split(' ') if p]
    if not parts:
        return 'Nexus member'
    if len(parts) == 1:
        return parts[0]
    return f'{parts[0]} {parts[-1][0]}.'


def leaderboard_state(member: RewardMember, top_n: int = 5) -> dict:
    """Top drivers by Nexus Score + the caller's own rank. Cached 5 min —
    ranked_testers() walks every member and this sits on the home screen."""
    from .nexus_standings import ranked_testers
    rows = cache.get('nexus:leaderboard:rows')
    if rows is None:
        rows = ranked_testers()
        cache.set('nexus:leaderboard:rows', rows, 300)
    me_id = str(member.id)
    my_rank = None
    my_score = 0
    for i, r in enumerate(rows):
        if r['memberId'] == me_id:
            my_rank, my_score = i + 1, r['nexusScore']
            break
    return {
        'top': [{
            'rank': i + 1,
            'name': 'You' if r['memberId'] == me_id else _public_name(r['name']),
            'isMe': r['memberId'] == me_id,
            'nexusScore': r['nexusScore'],
            'trips': r['trips'],
        } for i, r in enumerate(rows[:top_n])],
        'me': {'rank': my_rank, 'total': len(rows), 'nexusScore': my_score},
    }


def policy_card(member: RewardMember) -> dict:
    """The member's OWN policy + claims, from data Omni already holds:
    claims from the synced GraphiteClaim mirror; premium via the cached
    read-only lookup. Members without a linked policy get {linked: False}."""
    pol = (member.policy_number or '').strip()
    if not pol:
        return {'linked': False}
    premium = _graphite_premium(pol)
    claims = []
    try:
        from integrations.models import GraphiteClaim
        for c in (GraphiteClaim.objects.filter(policy_number__iexact=pol)
                  .order_by('-registered_date')[:3]):
            claims.append({
                'claimNumber': c.claim_number,
                'type': c.claim_type,
                'status': c.status,
                'registered': c.registered_date.isoformat() if c.registered_date else None,
                'paid': float(c.total_payment or 0),
            })
    except Exception as exc:  # noqa: BLE001
        log.info('nexus policy card claims skipped: %s', exc)
    return {
        'linked': True,
        'policyNumber': pol,
        'monthlyPremium': premium,
        'claims': claims,
    }
