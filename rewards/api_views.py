"""rewards/api_views.py — Alpha Rewards (Project Nexus) API."""
from __future__ import annotations

from django.db import transaction
from django.db.models import Sum, Avg, Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import viewsets, filters, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import CompanyScopedViewSetMixin
from . import health_points, hrv
from .alpha_score import alpha_score
from .models import (
    RewardPartner, RewardProgram, RewardMember, PointsTransaction, DrivingScore,
    HealthConsent, HealthMetric,
)
from .serializers import (
    RewardPartnerSerializer, RewardProgramSerializer, RewardMemberSerializer,
    PointsTransactionSerializer, DrivingScoreSerializer,
)


class RewardProgramViewSet(viewsets.ModelViewSet):
    queryset = RewardProgram.objects.all()
    serializer_class = RewardProgramSerializer
    permission_classes = [IsAuthenticated]


class RewardPartnerViewSet(viewsets.ModelViewSet):
    queryset = RewardPartner.objects.all()
    serializer_class = RewardPartnerSerializer
    permission_classes = [IsAuthenticated]


class RewardMemberViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = RewardMember.objects.select_related('company').all()
    serializer_class = RewardMemberSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['customer_name', 'policy_number']
    ordering_fields = ['points_balance', 'customer_name', 'tier']

    def retrieve(self, request, *args, **kwargs):
        # DPA L-5 (2026-07-19): log reads of a customer identity record.
        resp = super().retrieve(request, *args, **kwargs)
        from core.audit_reads import log_read
        log_read(request.user, 'rewards.RewardMember', kwargs.get('pk'),
                 description=f'Viewed customer reward member {kwargs.get("pk")}', request=request)
        return resp


class PointsTransactionViewSet(viewsets.ModelViewSet):
    queryset = PointsTransaction.objects.select_related('member', 'program', 'partner').all()
    serializer_class = PointsTransactionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['member__customer_name', 'detail']

    def get_queryset(self):
        qs = super().get_queryset()
        member = self.request.query_params.get('member')
        kind = self.request.query_params.get('kind')
        if member:
            qs = qs.filter(member_id=member)
        if kind:
            qs = qs.filter(kind=kind)
        return qs


class DrivingScoreViewSet(viewsets.ModelViewSet):
    queryset = DrivingScore.objects.select_related('member').all()
    serializer_class = DrivingScoreSerializer
    permission_classes = [IsAuthenticated]


class RewardsSummaryView(APIView):
    """GET /api/v1/rewards/summary/ — KPI payload for the Alpha Rewards tab."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        members = RewardMember.objects.all()
        txns = PointsTransaction.objects.all()
        earned = txns.filter(kind=PointsTransaction.Kind.EARN).aggregate(t=Sum('points'))['t'] or 0
        redeemed = txns.filter(kind=PointsTransaction.Kind.REDEEM).aggregate(t=Sum('points'))['t'] or 0
        avg_score = DrivingScore.objects.aggregate(a=Avg('score'))['a']
        tiers = list(members.values('tier').annotate(n=Count('id')).order_by('tier'))
        return Response({
            'members_total':    members.count(),
            'members_active':   members.filter(is_active=True).count(),
            'points_balance':   members.aggregate(t=Sum('points_balance'))['t'] or 0,
            'points_earned':    int(earned),
            'points_redeemed':  abs(int(redeemed)),
            'avg_driving_score': round(float(avg_score), 1) if avg_score is not None else None,
            'programs_active':  RewardProgram.objects.filter(is_active=True).count(),
            'partners_total':   RewardPartner.objects.count(),
            'tiers':            {t['tier']: t['n'] for t in tiers},
        })


# ---------------------------------------------------------------------------
# Health Points (Alpha Rewards Health App) — CFO directive.
#
# Mirrors the DrivingScore→points precedent: consented daily activity (steps)
# is converted server-side into reward points. Scoring is NEVER computed on the
# device (rewardsApi.ts sends raw metrics only); see rewards/health_points.py.
#
# Namespacing: these live under 'rewards/health-*' on purpose. The '/health/...'
# prefix is already owned by the healthcare INSURANCE quotes module — do NOT
# collide.
#
# DPA boundary: health activity is SPECIAL-CATEGORY data, used for REWARDS ONLY
# — never underwriting, pricing, or claims. We resolve a RewardMember by id and
# never store or log a customer name in the metric payload.
# ---------------------------------------------------------------------------

# The RewardProgram these points post under. Code matches RewardProgram.Code.HEALTH.
_HEALTH_PROGRAM_CODE = RewardProgram.Code.HEALTH


def _get_health_program():
    """Fetch (or lazily create) the Health Points RewardProgram row."""
    program, _ = RewardProgram.objects.get_or_create(
        code=_HEALTH_PROGRAM_CODE,
        defaults={'name': 'Health Points', 'is_active': True},
    )
    return program


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def health_consent(request):
    """POST rewards/health-consent/

    Body: {memberId, dataTypes: [], grantedAt}
    Upserts the member's granular health-data consent. 201 {ok: true}.
    Consent can be revoked by re-posting an empty dataTypes list (we then mark
    the latest consent inactive).
    """
    data = request.data or {}
    member_id = data.get('memberId')
    if not member_id:
        return Response({'detail': 'memberId is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # DPA: resolve by id; never trust/echo a name from the payload.
    member = get_object_or_404(RewardMember, pk=member_id)
    return apply_health_consent(member, data)


def apply_health_consent(member, data):
    """Upsert one member's health-data consent. Shared by the staff endpoint
    above and the member-scoped customer endpoint (rewards/customer_views.py),
    which resolves the member from the login token instead of the payload."""
    data_types = data.get('dataTypes') or []
    if not isinstance(data_types, list):
        return Response({'detail': 'dataTypes must be a list.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # Keep only string category names; ignore anything else.
    data_types = [str(t) for t in data_types if isinstance(t, (str, int))]

    granted_at = parse_datetime(data.get('grantedAt') or '') or timezone.now()

    # Upsert: one current consent row per member, refreshed on each post.
    consent, _ = HealthConsent.objects.update_or_create(
        member=member,
        defaults={
            'data_types': data_types,
            'granted_at': granted_at,
            'is_active': bool(data_types),
        },
    )
    return Response({'ok': True, 'consentId': str(consent.id)},
                    status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def health_metrics(request):
    """POST rewards/health-metrics/

    Body: {memberId, date, steps, sleepMinutes?}
    Idempotent upsert of one HealthMetric per member+date. Awards points via a
    PointsTransaction under the 'health' RewardProgram, bumps the member's
    balance by the DELTA only (re-posting the same day never double-awards),
    and recomputes the tier.

    Returns: {pointsAwarded, totalPoints, tier, streakDays}
    """
    data = request.data or {}
    member_id = data.get('memberId')
    if not member_id:
        return Response({'detail': 'memberId is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    member = get_object_or_404(RewardMember, pk=member_id)
    return apply_health_metrics(member, data)


def apply_health_metrics(member, data):
    """Record one member+day of health metrics and award the points delta.
    Shared by the staff endpoint above and the member-scoped customer endpoint
    (rewards/customer_views.py), which resolves the member from the token."""
    metric_date = parse_date(str(data.get('date') or '')) or timezone.localdate()
    try:
        steps = int(data.get('steps') or 0)
    except (TypeError, ValueError):
        return Response({'detail': 'steps must be an integer.'},
                        status=status.HTTP_400_BAD_REQUEST)
    sleep_minutes = data.get('sleepMinutes')
    if sleep_minutes is not None:
        try:
            sleep_minutes = int(sleep_minutes)
        except (TypeError, ValueError):
            return Response({'detail': 'sleepMinutes must be an integer.'},
                            status=status.HTTP_400_BAD_REQUEST)

    # Typed/self-reported steps award points only while the manual path is on.
    # v10 device-sync becomes the truth; flip NEXUS_MANUAL_STEP_POINTS_ENABLED
    # off in that release so a day's steps pay once, via the synced ledger only
    # (Fable v10 review — closes the fakeable web-session step-points path).
    from django.conf import settings
    _manual_on = bool(getattr(settings, 'NEXUS_MANUAL_STEP_POINTS_ENABLED', True))
    new_points = health_points.steps_to_points(steps) if _manual_on else 0
    program = _get_health_program()

    with transaction.atomic():
        # Lock the member row so concurrent posts for the same day serialise.
        member = RewardMember.objects.select_for_update().get(pk=member.pk)

        metric, created = HealthMetric.objects.select_for_update().get_or_create(
            member=member, date=metric_date,
            defaults={'steps': steps, 'sleep_minutes': sleep_minutes,
                      'points_awarded': new_points},
        )

        # Delta against whatever this day previously awarded — idempotent.
        prior_points = 0 if created else metric.points_awarded
        delta = new_points - prior_points

        if not created:
            metric.steps = steps
            metric.sleep_minutes = sleep_minutes
            metric.points_awarded = new_points
            metric.save(update_fields=['steps', 'sleep_minutes',
                                       'points_awarded', 'updated_at'])

        # One EARN transaction per member+day, refreshed to the current award
        # so the ledger total stays consistent with points_awarded.
        detail = f'Health Points: {steps} steps on {metric_date.isoformat()}'
        txn, txn_created = PointsTransaction.objects.get_or_create(
            member=member, program=program, kind=PointsTransaction.Kind.EARN,
            occurred_at__date=metric_date,
            defaults={'points': new_points, 'detail': detail,
                      'occurred_at': timezone.now()},
        )
        if not txn_created:
            txn.points = new_points
            txn.detail = detail
            txn.save(update_fields=['points', 'detail', 'updated_at'])

        # Bump balance by the delta only, then recompute tier.
        member.points_balance = (member.points_balance or 0) + delta
        member.tier = health_points.tier_for(member.points_balance)
        member.save(update_fields=['points_balance', 'tier', 'updated_at'])

    return Response({
        'pointsAwarded': new_points,
        'totalPoints':   member.points_balance,
        'tier':          member.tier,
        'streakDays':    health_points.streak_days(member),
    })


# ---------------------------------------------------------------------------
# Alpha Thrive — finger-PPG vitals, wellness score, trend, coach (2026-06-26).
#
# WELLNESS ONLY, NEVER DIAGNOSIS. Vitals = HR + HRV-stress + respiration.
# NO blood pressure. The device sends only DERIVED NUMBERS (RR intervals,
# HR, respiration) — raw camera frames never reach the server, and we never
# store or log a customer name in a vitals payload. The coach sees only
# ANONYMISED BUCKETS and runs on DeepSeek→Gemini (NEVER Anthropic).
# ---------------------------------------------------------------------------


def _coach_complete(system_prompt: str, user_prompt: str) -> tuple[str, str]:
    """Run the wellness coach on non-Anthropic engines only.

    Order: DeepSeek → Gemini. Anthropic is deliberately NOT in this chain
    (Thrive rule: never Anthropic for health coaching). Returns (text, engine).
    Raises nothing — on total failure returns ('', 'none') so the caller can
    fall back to a safe static nudge.
    """
    from core.ai_assist import (deepseek_complete, DeepSeekUnavailable,
                                gemini_complete, GeminiUnavailable)
    chain = [('DeepSeek', deepseek_complete, DeepSeekUnavailable),
             ('Gemini',   gemini_complete,   GeminiUnavailable)]
    for name, fn, exc_cls in chain:
        try:
            text = fn(user_prompt, system_prompt=system_prompt)
            if text and text.strip():
                return text.strip(), name
        except exc_cls:
            continue
        except Exception:  # noqa: BLE001 — never let a coach call 500 the scan flow
            continue
    return '', 'none'


def _age_band(age) -> str:
    try:
        a = int(age)
    except (TypeError, ValueError):
        return 'unknown'
    if a < 30:
        return 'under-30'
    if a < 45:
        return '30-44'
    if a < 60:
        return '45-59'
    return '60-plus'


def _activity_bucket(steps) -> str:
    try:
        s = int(steps or 0)
    except (TypeError, ValueError):
        return 'unknown'
    if s >= 8000:
        return 'active'
    if s >= 4000:
        return 'moderate'
    return 'low'


def _metrics_for_score(member, metric, *, age=None, gender=None) -> dict:
    """Assemble the alpha_score input dict from a member + a HealthMetric row.

    No identifiers — only numbers. age/gender are optional, self-reported, and
    NOT persisted (RewardMember stores neither); they only sharpen the baseline
    for this computation.
    """
    if metric is None:
        return {'age': age, 'gender': gender}
    return {
        'age':              age,
        'gender':           gender,
        'resting_hr':       metric.resting_hr,
        'stress_band':      metric.stress_band or '',
        'respiration_rate': metric.respiration_rate,
        'steps':            metric.steps,
        'sleep_minutes':    metric.sleep_minutes,
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def thrive_scan(request):
    """POST rewards/thrive/scan/

    Body: {memberId, rrIntervals:[ms,...], restingHr?, respirationRate?,
           age?, gender?, date?}
    Computes HRV (SDNN/RMSSD/pNN50 + stress band) from the RR intervals,
    upserts them onto the member's HealthMetric for the day (idempotent —
    a re-scan overwrites the day's VITALS only and never touches steps or
    points), and returns the recomputed Alpha Thrive score.

    DPA: rrIntervals are derived numbers. No frames, no identifiers stored.
    """
    data = request.data or {}
    member_id = data.get('memberId')
    if not member_id:
        return Response({'detail': 'memberId is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    member = get_object_or_404(RewardMember, pk=member_id)

    rr_intervals = data.get('rrIntervals') or []
    if not isinstance(rr_intervals, list):
        return Response({'detail': 'rrIntervals must be a list of milliseconds.'},
                        status=status.HTTP_400_BAD_REQUEST)

    summary = hrv.analyze(rr_intervals)
    if summary['beats'] < 2:
        return Response({'detail': 'Not enough clean beats in the scan — hold '
                                   'your fingertip still over the camera and try again.'},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Optional client-supplied overrides (the PPG app may measure these too).
    resting_hr = summary['resting_hr']
    if data.get('restingHr') is not None:
        try:
            resting_hr = int(data.get('restingHr'))
        except (TypeError, ValueError):
            pass
    respiration_rate = None
    if data.get('respirationRate') is not None:
        try:
            respiration_rate = float(data.get('respirationRate'))
        except (TypeError, ValueError):
            respiration_rate = None

    metric_date = parse_date(str(data.get('date') or '')) or timezone.localdate()

    with transaction.atomic():
        member = RewardMember.objects.select_for_update().get(pk=member.pk)
        # Vitals-only upsert. Never create points or touch steps here — the
        # steps→points economics stay owned by rewards/health-metrics/.
        metric, _ = HealthMetric.objects.get_or_create(
            member=member, date=metric_date,
            defaults={'steps': 0, 'points_awarded': 0, 'source': 'Alpha Thrive (PPG)'},
        )
        metric.resting_hr       = resting_hr
        metric.hrv_sdnn         = summary['hrv_sdnn']
        metric.hrv_rmssd        = summary['hrv_rmssd']
        metric.hrv_pnn50        = summary['hrv_pnn50']
        metric.respiration_rate = respiration_rate
        metric.stress_band      = summary['stress_band']
        metric.scan_confidence  = summary['scan_confidence']
        metric.save(update_fields=['resting_hr', 'hrv_sdnn', 'hrv_rmssd',
                                   'hrv_pnn50', 'respiration_rate', 'stress_band',
                                   'scan_confidence', 'updated_at'])

    score = alpha_score(_metrics_for_score(
        member, metric, age=data.get('age'), gender=data.get('gender')))

    return Response({
        'vitals': {
            'restingHr':       resting_hr,
            'hrvSdnn':         summary['hrv_sdnn'],
            'hrvRmssd':        summary['hrv_rmssd'],
            'hrvPnn50':        summary['hrv_pnn50'],
            'respirationRate': respiration_rate,
            'stressBand':      summary['stress_band'],
            'beats':           summary['beats'],
            'scanConfidence':  summary['scan_confidence'],
        },
        'alphaScore': score,
        'date':       metric_date.isoformat(),
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def thrive_alpha_score(request):
    """GET rewards/thrive/alpha-score/?member=<id>[&age=&gender=]

    Returns the Alpha Thrive score from the member's most recent HealthMetric.
    """
    member_id = request.query_params.get('member')
    if not member_id:
        return Response({'detail': 'member query param is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    member = get_object_or_404(RewardMember, pk=member_id)
    metric = member.health_metrics.order_by('-date').first()
    score = alpha_score(_metrics_for_score(
        member, metric,
        age=request.query_params.get('age'),
        gender=request.query_params.get('gender')))
    return Response({
        'score':     score,
        'tier':      member.tier,
        'points':    member.points_balance,
        'asOf':      metric.date.isoformat() if metric else None,
        'hasVitals': bool(metric and metric.resting_hr is not None),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def thrive_risk_trend(request):
    """GET rewards/thrive/risk-trend/?member=<id>

    Linear trend stub: scores the member's last (up to) 14 days of metrics and
    fits a simple least-squares slope so the UI can show direction. This is a
    wellness trend, not a clinical risk prediction.
    """
    member_id = request.query_params.get('member')
    if not member_id:
        return Response({'detail': 'member query param is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    member = get_object_or_404(RewardMember, pk=member_id)

    rows = list(member.health_metrics.order_by('-date')[:14])
    rows.reverse()  # oldest → newest for a left-to-right chart
    points = []
    for m in rows:
        s = alpha_score(_metrics_for_score(member, m))['score']
        points.append({'date': m.date.isoformat(), 'score': s})

    # Least-squares slope of score vs index (points/day-step). Stub linear fit.
    slope = 0.0
    n = len(points)
    if n >= 2:
        xs = list(range(n))
        ys = [p['score'] for p in points]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom:
            slope = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / denom
    direction = 'flat'
    if slope > 0.5:
        direction = 'improving'
    elif slope < -0.5:
        direction = 'declining'

    return Response({'points': points, 'slope': round(slope, 2),
                     'direction': direction})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def thrive_coach(request):
    """POST rewards/thrive/coach/  Body: {memberId, age?}

    Builds ANONYMISED buckets (age band, stress band, activity bucket, score
    band — no name, no raw vitals) and asks a non-Anthropic engine for ONE
    short wellness nudge. Falls back to a safe static nudge if no engine
    answers. Never diagnoses.
    """
    from core.ai_assist import is_safe_for_ai

    data = request.data or {}
    member_id = data.get('memberId')
    if not member_id:
        return Response({'detail': 'memberId is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    member = get_object_or_404(RewardMember, pk=member_id)
    metric = member.health_metrics.order_by('-date').first()
    score = alpha_score(_metrics_for_score(member, metric, age=data.get('age')))

    stress = (metric.stress_band if metric else '') or 'unknown'
    buckets = {
        'age_band':   _age_band(data.get('age')),
        'stress':     stress,
        'activity':   _activity_bucket(metric.steps if metric else None),
        'score_band': score['band'],
    }

    static_nudges = {
        'stressed': 'Your readings suggest some stress. Try five slow breaths, '
                    'a short walk, and water — small resets add up. 💧',
        'moderate': 'Steady going. A 10-minute walk and a glass of water now '
                    'will lift your score by tomorrow. 🚶',
        'calm':     'Lovely — you are in a calm zone. Keep the rhythm: move a '
                    'little, hydrate, and rest well tonight. 🌙',
    }
    fallback = static_nudges.get(stress,
        'Keep it simple today: move a little, drink water, and breathe slowly. '
        'Scan again tomorrow to watch your Alpha Thrive score grow. 🌱')

    system_prompt = (
        'You are Alpha Thrive, a friendly Botswana wellness coach for an '
        'insurance rewards app. Give exactly ONE short, warm, encouraging '
        'nudge (under 220 characters). WELLNESS ONLY — never diagnose, never '
        'mention illness, medication, or medical advice. You may use one emoji. '
        'You are given only anonymised wellness buckets, never a person.')
    user_prompt = (
        f'Member buckets: age band {buckets["age_band"]}, stress {buckets["stress"]}, '
        f'activity {buckets["activity"]}, wellness score band {buckets["score_band"]}. '
        f'Write one encouraging nudge.')

    nudge, engine = fallback, 'static'
    if is_safe_for_ai(user_prompt).safe:
        text, used = _coach_complete(system_prompt, user_prompt)
        if text:
            nudge, engine = text, used

    return Response({'nudge': nudge, 'engine': engine, 'buckets': buckets,
                     'scoreBand': score['band']})


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Nexus tester competition (STAFF / CFO — SSO). The ranking + the standings
# email live in rewards/nexus_standings.py (single source of truth).
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def nexus_leaderboard(request):
    """Ranked list of all Alpha Nexus testers (by Nexus Score). CFO picks the winner."""
    from .nexus_standings import ranked_testers, COMPETITION_START, start_label
    rows = ranked_testers()
    return Response({'members': rows, 'count': len(rows),
                     'competitionStart': COMPETITION_START.isoformat(),
                     'competitionStartLabel': start_label()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def nexus_standings_send(request):
    """Email every tester their personalised standings + prize push."""
    from .nexus_standings import send_standings_to_all
    return Response(send_standings_to_all())
