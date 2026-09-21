"""rewards/customer_views.py — the customer app API (Nexus + Rewards + Thrive).

Public, customer-facing surface used by the customer app (route /m). Auth is
email OTP via rewards/customer_auth.py — NOT staff SSO. Every data endpoint
resolves the member from the login session (request.customer_member), so a
customer can only ever read/write their OWN data. No member id is ever taken
from the client.

DPA: a customer's email is PII — it is used only to send their own login code
(cc_cfo=False, never to EXCO) and is never sent to an AI or logged with a code.
"""
from __future__ import annotations

import logging

from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from . import customer_auth, device_auth, thrive_core, drive_score, health_points
logger = logging.getLogger(__name__)

from .models import (
    RewardMember, PointsTransaction, DrivingScore, CustomerDriveTrip, RewardProgram,
    CustomerActivity, CustomerFeedback, CustomerStepDay, CustomerWorkoutSession,
)

# Health-derived step points are REWARDS ONLY — never premium pricing (Google
# Play forbids health data → insurance pricing). See rewards/provisional_rates.py.
MAX_SYNCED_STEPS_PER_DAY = 50_000  # sanity cap before points conversion
# v11 workout sync — one Health Connect exercise session pays what the retired
# one-tap "Log a workout" paid (+2), at most two paid sessions a day.
WORKOUT_POINTS_PER_SESSION = 2
WORKOUT_PAID_SESSIONS_PER_DAY = 2
WORKOUT_MIN_MINUTES = 10            # anything shorter is noise, not a workout
WORKOUT_MAX_MINUTES = 6 * 60        # a "session" longer than 6h is a stuck timer
WORKOUT_BATCH_LIMIT = 25            # sessions per POST
SYNC_SOURCES = ('health_connect', 'apple_health')   # Android v10/v11, iPhone 1.1.0


def _sync_source(d) -> str:
    src = str((d or {}).get('source') or 'health_connect')
    return src if src in SYNC_SOURCES else 'health_connect'


def _manual_step_points_enabled() -> bool:
    """May a member SELF-REPORT activity — a typed step count (activity
    kind=steps, health-metrics) or a one-tap workout (kind=fitness)?

    OFF by default since 7-Sep-2026 (CFO): with monthly cash prizes riding on
    the leaderboard, anything a person can type is a fake. Steps now earn ONLY
    through the device-synced ledger (customer_steps_sync, Health Connect).
    NEXUS_MANUAL_STEP_POINTS_ENABLED=True re-opens the legacy paths.
    """
    from django.conf import settings
    return bool(getattr(settings, 'NEXUS_MANUAL_STEP_POINTS_ENABLED', False))


SELF_REPORT_REFUSED = ('Typed steps and one-tap workouts no longer earn points. '
                       'Tap "Sync steps" to count the real steps from your phone.')

NEXT_TIER = {'bronze': 'Silver', 'silver': 'Gold', 'gold': 'Platinum', 'platinum': 'Platinum'}


def premium_discount_pct(member) -> int:
    """The COMPLIANT premium-discount % for a member.

    HARD RULE (CFO 16-Aug-2026, Fable v10 review): health/wellness points —
    steps, pulse scan — must NEVER influence a premium discount. Google Play
    forbids using health data for insurance pricing, and the wellness TIER is
    fed by health points, so tier must not drive the premium discount.

    Until a CFO-signed, NON-health discount table exists
    (provisional_rates.PROVISIONAL flips to False) NO premium discount is shown —
    which also honours "no pula figure on a customer screen until signed". When
    live, it is driven ONLY by non-health factors (driving score, claims-free
    years, tenure) via provisional_rates — never the wellness tier.
    """
    from . import provisional_rates
    if getattr(provisional_rates, 'PROVISIONAL', True):
        return 0
    # TODO: wire non-health computation (driving score / claims-free / tenure,
    # capped at provisional_rates.MAX_TOTAL_DISCOUNT_PCT) when the signed table
    # lands. Health points are never an input here.
    return 0


class CustomerOtpThrottle(SimpleRateThrottle):
    """Per-IP throttle for the public customer OTP endpoints (rate =
    DEFAULT_THROTTLE_RATES['customer_otp']). These views are AllowAny, so
    without this a single IP could email-bomb customers, spam member rows, or
    brute-force the OTP. Fixed scope so it works on function-based views."""
    scope = 'customer_otp'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class TeamJoinThrottle(SimpleRateThrottle):
    """Per-IP throttle for guessing a team's join code.

    A 6-character code over a 32-letter alphabet is ~1.07e9 combinations, and a
    hit would put a stranger inside a family's numbers. Its OWN scope on
    purpose: sharing the customer_otp bucket would let joining a team drain the
    allowance that sign-in and device pairing depend on.
    """
    scope = 'team_join'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}

# Click & Drive anti-abuse guards. A real road trip cannot average faster than
# this; anything above is a GPS glitch or a spoof and is rejected, not scored.
DRIVE_MAX_PLAUSIBLE_KMH = 200.0
# Cap on Click & Drive points a member can earn per day (stops trip-spam farming).
DRIVE_DAILY_POINTS_CAP = 60
# Max feedback rows stored per member per day (bounds authenticated spam).
FEEDBACK_DAILY_CAP = 10
# Shortest distance that counts as a real trip (km). Below this = not stored or
# scored — kills 0-distance "perfect 100" trips (incl. NaN coerced to 0).
# Sourced from drive_score so the ingest gate, the stored quality flags and
# every read path use ONE parser and can never disagree.
DRIVE_MIN_TRIP_KM = drive_score.MIN_TRIP_KM


# ---------------------------------------------------------------------------
# Auth: request + verify email OTP
# ---------------------------------------------------------------------------

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([CustomerOtpThrottle])
def customer_request_otp(request):
    """POST /api/v1/rewards/customer/request-otp/  Body: {email, policyNumber?}

    Email-first sign-in for the Alpha Nexus customer app. Emails a 6-digit code
    to the address given. In the test app an unknown email SELF-REGISTERS a new
    member (CUSTOMER_SELF_REGISTER, default on) so testers can join with any
    Gmail. A policy number may also be supplied to link to an existing member.
    Always returns {ok:true} and never reveals whether the email already existed.
    """
    data = request.data or {}
    email_in = str(data.get('email') or '').strip().lower()
    policy = str(data.get('policyNumber') or '').strip()
    if not email_in and not policy:
        return Response({'detail': 'email is required.'}, status=status.HTTP_400_BAD_REQUEST)

    member = None
    if email_in:
        member = RewardMember.objects.filter(email__iexact=email_in, is_active=True).first()
    if member is None and policy:
        member = RewardMember.objects.filter(policy_number__iexact=policy, is_active=True).first()
    if member is None and email_in and _self_register_enabled():
        member = _register_member(email_in, str(data.get('referralCode') or '').strip())

    if member is not None:
        # SECURITY: never bind a caller-supplied email onto a member that has
        # none on file. The old code did, which let anyone who knows a policy
        # number (printed on schedules/cards — not a secret) attach their own
        # email to that policyholder and receive the OTP = silent account
        # takeover. A code is emailed ONLY to an address already on the member
        # (self-registered, or previously verified). Policy-only members with no
        # email must be linked through a verified/out-of-band flow, not here.
        if member.email and customer_auth.recent_code_count(member) < customer_auth.OTP_RATE_PER_HOUR:
            code = customer_auth.issue_code(member)
            _email_code(member, code)
    return Response({'ok': True, 'message': 'If that email can sign in, a code has been sent.'})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([CustomerOtpThrottle])
def customer_verify_otp(request):
    """POST /api/v1/rewards/customer/verify-otp/  Body: {policyNumber, code}
    On success returns {token, member:{...}}."""
    data = request.data or {}
    email_in = str(data.get('email') or '').strip().lower()
    policy = str(data.get('policyNumber') or '').strip()
    code = str(data.get('code') or '').strip()
    if not code or (not email_in and not policy):
        return Response({'detail': 'email and code are required.'}, status=status.HTTP_400_BAD_REQUEST)

    member = None
    if email_in:
        member = RewardMember.objects.filter(email__iexact=email_in, is_active=True).first()
    if member is None and policy:
        member = RewardMember.objects.filter(policy_number__iexact=policy, is_active=True).first()
    if member is None or not customer_auth.verify_code(member, code):
        return Response({'detail': 'That code is invalid or has expired.'}, status=status.HTTP_401_UNAUTHORIZED)

    token = customer_auth.start_session(member)
    return Response({'token': token, 'member': _member_brief(member)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_logout(request):
    member = customer_auth.resolve_member(request)
    customer_auth.revoke_session(request)
    if member is not None:
        device_auth.revoke_member_devices(member)  # a signed-out phone loses step-sync too
    return Response({'ok': True})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_feedback(request):
    """POST /api/v1/rewards/customer/feedback/  {rating 1-5, area?, comment?, wants?}

    In-app feedback / short survey so the team shapes Nexus around what members
    actually value (Medu 2026-07). No points — pure product signal.
    """
    d = request.data or {}
    try:
        rating = int(d.get('rating') or 0)
    except (TypeError, ValueError):
        rating = 0
    if not (1 <= rating <= 5):
        return Response({'detail': 'Please give a rating from 1 to 5.'}, status=status.HTTP_400_BAD_REQUEST)
    area = str(d.get('area') or 'overall').strip().lower()
    if area not in dict(CustomerFeedback.Area.choices):
        area = 'overall'
    # Per-member daily cap — bound row creation from a shared/looped token
    # (this endpoint is authenticated, so a per-IP throttle can't see the
    # token-resolved member). Over the cap we accept silently, no new row.
    from django.utils import timezone
    member = request.customer_member
    today_count = CustomerFeedback.objects.filter(
        member=member, created_at__date=timezone.localdate()).count()
    if today_count < FEEDBACK_DAILY_CAP:
        CustomerFeedback.objects.create(
            member=member, rating=rating, area=area,
            comment=str(d.get('comment') or '')[:2000],
            wants=str(d.get('wants') or '')[:2000],
        )
    return Response({'ok': True, 'detail': 'Thank you — your feedback helps us build what you want.'},
                    status=status.HTTP_201_CREATED)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_delete_account(request):
    """POST /api/v1/rewards/customer/delete-account/  Body: {confirm: "DELETE"}

    Permanently deletes the signed-in member and ALL their data — points,
    transactions, drive trips, activities, health metrics/consents, login
    codes and sessions (every member FK is on_delete=CASCADE). Required by
    Apple App Store Guideline 5.1.1(v): an app that lets users create
    accounts must offer full in-app account deletion. The confirm word stops
    a stray/accidental POST from wiping an account.
    """
    if str((request.data or {}).get('confirm') or '').strip().upper() != 'DELETE':
        return Response({'detail': 'Confirmation missing.'}, status=status.HTTP_400_BAD_REQUEST)
    member = request.customer_member
    member.delete()   # cascades to all rewards/thrive/drive/session rows
    return Response({'ok': True, 'detail': 'Your account and all its data have been deleted.'})


# ---------------------------------------------------------------------------
# Profile + Rewards + Drive (Nexus) — scoped to the logged-in member
# ---------------------------------------------------------------------------

@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_me(request):
    return Response(_member_brief(request.customer_member))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_rewards(request):
    member = request.customer_member
    txns = (PointsTransaction.objects.filter(member=member)
            .select_related('program', 'partner').order_by('-occurred_at')[:12])
    return Response({
        'member':   _member_brief(member),
        'discount': premium_discount_pct(member),
        'nextTier': NEXT_TIER.get(member.tier, 'Platinum'),
        'transactions': [{
            'kind':       t.kind,
            'points':     t.points,
            'detail':     t.detail,
            'program':    t.program.name if t.program_id else None,
            'occurredAt': t.occurred_at.isoformat() if t.occurred_at else None,
        } for t in txns],
    })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_drive(request):
    member = request.customer_member
    scores = DrivingScore.objects.filter(member=member).order_by('-period')[:12]
    rows = [{
        'period':        s.period,
        'score':         s.score,
        'distanceKm':    str(s.distance_km),
        'harshEvents':   s.harsh_events,
        'pointsAwarded': s.points_awarded,
        'vehicleReg':    s.vehicle_reg,
    } for s in scores]
    # Click & Drive trips recorded in the app (gradeable trips → profile;
    # latest 12 → list). Rows that were not real drives stay in the list,
    # labelled and greyed, so nothing is hidden — they are simply never graded.
    all_trips = list(CustomerDriveTrip.objects.filter(member=member).order_by('-started_at'))
    graded_trips = [t for t in all_trips if t.is_valid]
    trip_rows = [{
        'startedAt':     t.started_at.isoformat(),
        'score':         t.score,
        'distanceKm':    round(t.distance_km, 1),
        'durationMin':   round(t.duration_min, 1),
        'harshEvents':   t.harsh_events,
        'idleMinutes':   round(t.idle_minutes, 1),
        'maxSpeed':      round(t.max_speed) if t.speed_reliable else None,
        'pointsAwarded': t.points_awarded,
        'counted':       t.is_valid,
        'notCountedWhy': drive_score.REASON_TEXT.get(t.invalid_reason, '') if not t.is_valid else '',
    } for t in all_trips[:12]]
    # ALL rows go to driving_profile: it applies trip_quality itself (the one
    # parser) and reports how many it had to set aside, so the screen can say
    # so out loud instead of silently showing a smaller number.
    profile = drive_score.driving_profile([{
        'score': t.score, 'distance_km': t.distance_km, 'duration_min': t.duration_min,
        'idle_minutes': t.idle_minutes, 'harsh_events': t.harsh_events, 'max_speed': t.max_speed,
    } for t in all_trips])
    # Average score favours the in-app trips; falls back to monthly DrivingScore.
    if graded_trips:
        avg = round(sum(t.score for t in graded_trips) / len(graded_trips), 1)
    elif rows:
        avg = round(sum(r['score'] for r in rows) / len(rows), 1)
    else:
        avg = None
    return Response({'scores': rows, 'trips': trip_rows, 'profile': profile,
                     'avgScore': avg, 'count': len(rows) + len(graded_trips)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_drive_trip(request):
    """POST /api/v1/rewards/customer/drive/trip/

    Body (AGGREGATE numbers only — no GPS track):
      {startedAt?, distanceKm, durationMin, idleMinutes, harshEvents, maxSpeed}
    Server scores the trip (drive_score.py — client can't fake it), stores it,
    and awards safe-driving points. Returns the scored trip + new balance.
    """
    import math
    from django.db import transaction
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    d = request.data or {}

    def _num(key):
        try:
            v = float(d.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        # Reject NaN/Infinity — float('nan') passes `<= 0` checks (nan<=0 is
        # False) and would be scored as a perfect trip. Coerce to 0.
        if not math.isfinite(v):
            return 0.0
        return max(0.0, v)

    distance = _num('distanceKm')
    duration = _num('durationMin')
    idle = _num('idleMinutes')
    max_speed = _num('maxSpeed')
    try:
        harsh = max(0, int(d.get('harshEvents') or 0))
    except (TypeError, ValueError):
        harsh = 0

    # Idle time can never exceed the trip duration (client clamps too, but the
    # server must not trust the client).
    idle = min(idle, duration)

    # A real drive covers real ground and takes real time. trip_quality() is the
    # ONE parser — the same call judges history in the migration and every read
    # path — so a trip can never be rejected on the way in yet graded on the way
    # out. A "start, don't move, stop" would otherwise bank a free 100-score
    # trip, and a GPS lock-on spike would report a peak speed off no distance.
    quality = drive_score.trip_quality(distance_km=distance, duration_min=duration,
                                       max_speed=max_speed)
    if not quality['valid']:
        return Response({'detail': 'No trip was recorded — drive a little further and try again.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Plausibility gate: reject an average speed no car achieves instead of
    # scoring a fabricated payload as a real drive.
    implied_kmh = distance / (duration / 60.0)
    if implied_kmh > DRIVE_MAX_PLAUSIBLE_KMH:
        return Response({'detail': 'That trip did not look right and was not scored. Try again.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # An unsupported peak is UNKNOWN, not zero-risk: it must not earn a speed
    # penalty, and it must not be reported as the member's top speed either.
    res = drive_score.trip_score(distance_km=distance, duration_min=duration,
                                 idle_minutes=idle, harsh_events=harsh,
                                 max_speed=quality['max_speed'])
    provided_start = bool(str(d.get('startedAt') or '').strip())
    started = parse_datetime(str(d.get('startedAt') or '')) or timezone.now()

    from django.db.models import Sum
    member = request.customer_member
    duplicate = False
    with transaction.atomic():
        try:
            member = RewardMember.objects.select_for_update().get(pk=member.pk)
        except RewardMember.DoesNotExist:
            # Account was deleted between auth and here (e.g. delete-account in
            # a parallel tab) — 410 instead of an unhandled 500.
            return Response({'detail': 'Your account no longer exists.'}, status=status.HTTP_410_GONE)
        # Idempotency: a network-timeout retry resends the same startedAt — do
        # not create a second trip or award points twice for one drive.
        existing = (CustomerDriveTrip.objects.filter(member=member, started_at=started).first()
                    if provided_start else None)
        if existing is not None:
            duplicate = True
            award = 0
        else:
            # Daily anti-farming cap: only award up to the day's remaining points.
            earned_today = (CustomerDriveTrip.objects
                            .filter(member=member, started_at__date=timezone.localdate())
                            .aggregate(s=Sum('points_awarded'))['s'] or 0)
            award = max(0, min(res['points'], DRIVE_DAILY_POINTS_CAP - earned_today))
            CustomerDriveTrip.objects.create(
                member=member, started_at=started, distance_km=distance, duration_min=duration,
                idle_minutes=idle, harsh_events=harsh, max_speed=max_speed,
                score=res['score'], points_awarded=award,
                is_valid=quality['valid'], invalid_reason=quality['reason'],
                speed_reliable=quality['speed_reliable'],
            )
            if award > 0:
                program, _ = RewardProgram.objects.get_or_create(
                    code=RewardProgram.Code.DRIVING, defaults={'name': 'Driving Points', 'is_active': True})
                PointsTransaction.objects.create(
                    member=member, program=program, kind=PointsTransaction.Kind.EARN,
                    points=award, occurred_at=timezone.now(),
                    detail=f'Click & Drive: {distance:.1f} km, score {res["score"]}')
                member.points_balance = (member.points_balance or 0) + award
                member.tier = health_points.tier_for(member.points_balance)
                member.save(update_fields=['points_balance', 'tier', 'updated_at'])

    return Response({
        'trip': {
            'score': res['score'], 'band': res['band'], 'factors': res['factors'],
            'pointsAwarded': (existing.points_awarded if duplicate else award),
            'distanceKm': round(distance, 1),
            'durationMin': round(duration, 1), 'harshEvents': harsh,
            'idleMinutes': round(idle, 1),
            # An unreliable peak is UNKNOWN on EVERY response that carries it,
            # including this echo — otherwise the score card shows the raw spike
            # ("max 200 km/h") while the same payload's speed factor says the
            # reading was discarded.
            'maxSpeed': round(max_speed) if quality['speed_reliable'] else None,
        },
        'totalPoints': member.points_balance, 'tier': member.tier,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_activity(request):
    """POST /api/v1/rewards/customer/activity/  Body: {kind, detail?, steps?, imageData?}

    Log a wellness activity and earn Nexus points. ONE earning per kind per day.
    Anti-cheat weighting: healthy_eating needs a vision-verified food photo (up
    to +15); fitness one-tap +2; manual steps capped +3.
    """
    from django.db import transaction
    from django.utils import timezone

    d = request.data or {}
    kind = str(d.get('kind') or '')
    if kind not in dict(CustomerActivity.Kind.choices):
        return Response({'detail': 'Unknown activity.'}, status=status.HTTP_400_BAD_REQUEST)
    # Self-reported kinds are refused outright (no row, no points) unless the
    # legacy switch is on — see _manual_step_points_enabled.
    if (kind in (CustomerActivity.Kind.STEPS, CustomerActivity.Kind.FITNESS)
            and not _manual_step_points_enabled()):
        return Response({'detail': SELF_REPORT_REFUSED}, status=status.HTTP_400_BAD_REQUEST)
    detail = str(d.get('detail') or '')[:200]
    member = request.customer_member
    today = timezone.localdate()

    already = CustomerActivity.objects.filter(
        member=member, kind=kind, occurred_at__date=today, points_awarded__gt=0).exists()

    # --- Anti-cheat point weighting (tester feedback, CFO directive) -----------
    # Verified actions earn real points; self-reported actions earn little.
    steps = 0
    meal_score = None
    pts = 0
    if already:
        pts = 0
    elif kind == CustomerActivity.Kind.FITNESS:
        pts = 2  # one-tap self-report — easily faked → token points only
    elif kind == CustomerActivity.Kind.STEPS:
        try:
            steps = max(0, int(d.get('steps') or 0))
        except (TypeError, ValueError):
            steps = 0
        # Legacy typed entry (switch ON only) → capped low.
        pts = min(3, health_points.steps_to_points(steps))
    elif kind == CustomerActivity.Kind.HEALTHY_EATING:
        image = d.get('imageData') or d.get('photo') or ''
        if not image:
            return Response({'detail': 'Take a photo of your meal to earn points.'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Gemini vision confirms it is a real meal (anti-cheat) + scores healthiness.
        # Image is sent once and never stored; never falls back to Anthropic.
        from staff_rewards.api_views import _score_meal_image
        try:
            meal_score = _score_meal_image(image, str(member.id))
        except Exception:  # noqa: BLE001
            return Response({'detail': 'Could not check your photo right now — please try again.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        pts = 15 if meal_score >= 60 else 8 if meal_score >= 30 else 3

    with transaction.atomic():
        try:
            member = RewardMember.objects.select_for_update().get(pk=member.pk)
        except RewardMember.DoesNotExist:
            return Response({'detail': 'Your account no longer exists.'}, status=status.HTTP_410_GONE)
        if not already and CustomerActivity.objects.filter(
                member=member, kind=kind, occurred_at__date=today, points_awarded__gt=0).exists():
            already, pts = True, 0  # lost the race — already earned today
        CustomerActivity.objects.create(
            member=member, kind=kind, detail=detail, value=(steps or (meal_score or 0)),
            points_awarded=pts, occurred_at=timezone.now())
        if pts > 0:
            program, _ = RewardProgram.objects.get_or_create(
                code=RewardProgram.Code.HEALTH, defaults={'name': 'Health Points', 'is_active': True})
            label = dict(CustomerActivity.Kind.choices)[kind]
            PointsTransaction.objects.create(
                member=member, program=program, kind=PointsTransaction.Kind.EARN,
                points=pts, occurred_at=timezone.now(),
                detail=f'{label}{(": " + detail) if detail else ""}')
            member.points_balance = (member.points_balance or 0) + pts
            member.tier = health_points.tier_for(member.points_balance)
            member.save(update_fields=['points_balance', 'tier', 'updated_at'])

    return Response({'kind': kind, 'pointsAwarded': pts, 'totalPoints': member.points_balance,
                     'tier': member.tier, 'alreadyToday': already, 'mealScore': meal_score},
                    status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Thrive — scoped to the logged-in member (member NEVER from the client)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_thrive_scan(request):
    data = request.data or {}
    rr = data.get('rrIntervals') or []
    if not isinstance(rr, list):
        return Response({'detail': 'rrIntervals must be a list of milliseconds.'}, status=status.HTTP_400_BAD_REQUEST)
    result = thrive_core.run_scan(
        request.customer_member, rr,
        resting_hr_override=data.get('restingHr'),
        respiration_rate=data.get('respirationRate'),
        age=data.get('age'), gender=data.get('gender'),
        metric_date=parse_date(str(data.get('date') or '')) or None,
    )
    if result is None:
        return Response({'detail': 'Not enough clean beats — hold your fingertip still '
                                   'over the camera and try again.'},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return Response(result, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_thrive_score(request):
    age = request.query_params.get('age')
    return Response(thrive_core.alpha_for_member(request.customer_member, age=age))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_thrive_trend(request):
    return Response(thrive_core.trend_for_member(request.customer_member))


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_thrive_coach(request):
    age = (request.data or {}).get('age')
    return Response(thrive_core.coach_for_member(request.customer_member, age=age))


# ---------------------------------------------------------------------------
# Health Connect / HealthKit (the Android + iOS app) — member-scoped.
#
# The phone app reads step counts from the platform health store WITH CONSENT
# and posts them here. Identical logic to the staff endpoints in api_views.py,
# except the member comes from the login token, never from the payload — so the
# app never needs a member id and one signed-in customer can only ever write
# their own record.
# ---------------------------------------------------------------------------

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_health_consent(request):
    """POST /api/v1/rewards/customer/health-consent/  Body: {dataTypes: [], grantedAt?}"""
    from .api_views import apply_health_consent
    return apply_health_consent(request.customer_member, request.data or {})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_health_metrics(request):
    """POST /api/v1/rewards/customer/health-metrics/  Body: {date?, steps, sleepMinutes?}

    Idempotent per member+day: re-posting the same day never double-awards.
    """
    from .api_views import apply_health_metrics
    return apply_health_metrics(request.customer_member, request.data or {})


# ---------------------------------------------------------------------------
# Growth: quest / streak / weekly challenge / savings / leaderboard / policy
# ---------------------------------------------------------------------------

@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_growth(request):
    """GET /api/v1/rewards/customer/growth/

    Everything the home screen's engagement cards need in ONE call: the
    first-win quest (3 starter steps + one-time bonus), the day streak, the
    weekly drive challenge, the premium-savings figure for the member's tier,
    and the safe-driving leaderboard (anonymised names). Quest/challenge
    bonuses are awarded here idempotently — hitting this endpoint repeatedly
    can never pay twice (fixed detail-string dedupe under a row lock).
    """
    from . import nexus_growth
    member = request.customer_member
    quest = nexus_growth.quest_state(member)
    challenge = nexus_growth.weekly_challenge_state(member)
    return Response({
        'quest': quest,
        'streak': nexus_growth.streak_state(member),
        'challenge': challenge,
        'savings': nexus_growth.savings_state(member, premium_discount_pct(member)),
        'leaderboard': nexus_growth.leaderboard_state(member),
        # Fresh balance so the app can update the header when a bonus just paid.
        'totalPoints': member.points_balance,
        'tier': member.tier,
    })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_policy(request):
    """GET /api/v1/rewards/customer/policy/ — the member's OWN policy + claims
    card (claims from the synced Graphite mirror; premium best-effort/cached).
    Unlinked members get {linked: false} and the app hides the card."""
    from . import nexus_growth
    return Response(nexus_growth.policy_card(request.customer_member))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _self_register_enabled() -> bool:
    """Test-app mode: an unknown email may self-register a new member.
    Toggle with settings.CUSTOMER_SELF_REGISTER (default True)."""
    from django.conf import settings
    return bool(getattr(settings, 'CUSTOMER_SELF_REGISTER', True))


def _name_for_email(email: str) -> str:
    """Best display name for an email: the real name from the omni user
    directory if it's a known account, else a tidy version of the local part."""
    from django.contrib.auth import get_user_model
    u = get_user_model().objects.filter(email__iexact=email).first()
    if u and (u.first_name or u.last_name):
        return f'{u.first_name} {u.last_name}'.strip()
    # Company-email testers who never SSO'd into omni have no local User row.
    # The M365 directory (synced) is the source of truth — resolve from Graph.
    domain = (email.split('@')[-1] or '').lower()
    if domain in ('alphadirect.co.bw', 'insurance.co.bw', 'adrisk.co.bw'):
        try:
            from licensing.services.graph import directory_name_for_email
            real = directory_name_for_email(email)
            if real:
                return real
        except Exception:
            pass
    local = (email.split('@')[0] or 'Member').replace('.', ' ').replace('_', ' ').strip()
    return local.title() if local else 'Member'


# ---------------------------------------------------------------------------
# Family & friend teams · screening voucher · referrals
# (CFO 2026-09-08: "6 is good", "10 is good", "7 is good")
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_team(request):
    """GET my team; POST {name} to start one.

    Every read starts from the signed-in member, so there is no way to ask for
    somebody else's team - one family can never see another's numbers.
    """
    from . import nexus_engage
    member = request.customer_member
    if request.method == 'POST':
        team, err = nexus_engage.create_team(member, str((request.data or {}).get('name') or ''))
        if err:
            return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(nexus_engage.team_state(member), status=status.HTTP_201_CREATED)
    return Response(nexus_engage.team_state(member))


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([TeamJoinThrottle])
@customer_auth.customer_required
def customer_team_join(request):
    """POST {code} to join a team. Refuses a full team and a second team.

    Throttled: a 6-character code over a 32-letter alphabet is ~1.07e9
    combinations, and a hit would put a stranger inside a family's numbers —
    the exact failure mode this feature was told to avoid. Rate-limiting turns
    a feasible grind into centuries per IP.
    """
    from . import nexus_engage
    member = request.customer_member
    team, err = nexus_engage.join_team(member, str((request.data or {}).get('code') or ''))
    if err:
        return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
    return Response(nexus_engage.team_state(member))


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_team_leave(request):
    """POST to leave whatever team I am in. Idempotent."""
    from . import nexus_engage
    member = request.customer_member
    nexus_engage.leave_team(member)
    return Response(nexus_engage.team_state(member))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_screening(request):
    """How close I am to my free annual screening, and my voucher if issued."""
    from . import nexus_engage
    return Response(nexus_engage.screening_state_for(request.customer_member))


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_screening_claim(request):
    """Issue the voucher - only when the pulse checks are actually there.

    The check and the write happen under one lock, so two taps cannot mint two
    vouchers, and the once-a-year window is enforced server-side.
    """
    from . import nexus_engage
    voucher, err = nexus_engage.claim_screening(request.customer_member)
    if err:
        return Response({'detail': err}, status=status.HTTP_400_BAD_REQUEST)
    return Response(nexus_engage.screening_state_for(request.customer_member),
                    status=status.HTTP_201_CREATED)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_referral(request):
    """My referral code and who has joined on it.

    No money value is returned - the reward is stated in POINTS and only lands
    once a referred policy has stuck (provisional_rates).
    """
    from . import nexus_engage
    return Response(nexus_engage.referral_state(request.customer_member))


def _register_member(email: str, referral_code: str = ''):
    """Create a Bronze member for a self-registering tester. Uses their real
    name from omni when the email is a registered account; no policy attached.

    A referral code, if one was typed, is RECORDED here and never paid here:
    provisional_rates says the reward lands only once the referred policy has
    activated and stuck, so nothing is awarded at signup. A bad code is
    silently ignored - it must never block somebody joining, and it must never
    reveal whether another account exists.
    """
    from django.utils import timezone
    member = RewardMember.objects.create(
        customer_name=_name_for_email(email), email=email, tier='bronze',
        is_active=True, enrolled_at=timezone.localdate(),
    )
    from . import nexus_engage
    nexus_engage.code_for(member)          # issue this member their own code
    if referral_code:
        # Narrow on purpose: a database or value problem must not stop somebody
        # joining, but a programming error should still surface as a 500 rather
        # than vanish into a log line nobody reads.
        from django.db import DatabaseError
        try:
            nexus_engage.record_referral(member, referral_code)
        except (DatabaseError, ValueError, TypeError):
            logger.exception('referral not recorded for member %s', member.pk)
    return member


def _member_brief(member) -> dict:
    return {
        'id':       str(member.id),
        'name':     member.customer_name,
        'tier':     member.tier,
        'tierDisplay': member.get_tier_display(),
        'points':   member.points_balance,
        'hasEmail': bool(member.email),
    }


def _email_code(member, code: str) -> None:
    """Email the login code to the member only (never CC EXCO)."""
    from core.notifications import send_html_with_cfo_cc
    html = (
        '<div style="font-family:Georgia,serif;max-width:480px;margin:0 auto;">'
        '<div style="background:#0D1B2A;color:#fff;padding:20px 24px;border-radius:12px 12px 0 0;">'
        '<b style="font-size:18px;">Alpha Direct</b></div>'
        '<div style="background:#fff;border:1px solid #E5E7EB;border-top:none;'
        'padding:24px;border-radius:0 0 12px 12px;">'
        '<p>Your one-time login code:</p>'
        f'<p style="font-size:34px;font-weight:bold;letter-spacing:6px;color:#0D1B2A;'
        f'margin:8px 0;">{code}</p>'
        '<p style="font-size:13px;color:#6B7280;">It expires in 10 minutes. If you did not '
        'request it, ignore this email.</p></div></div>'
    )
    send_html_with_cfo_cc(
        'Your Alpha Direct login code',
        html,
        to=[member.email],
        text_fallback=f'Your Alpha Direct login code is {code}. It expires in 10 minutes.',
        cc_cfo=False,
    )


# ---------------------------------------------------------------------------
# v10 — verified STEP SYNC (Health Connect / Apple Health via a paired device)
# ---------------------------------------------------------------------------
# Two tiers of trust: the 30-day email-OTP session runs the app; a SEPARATE
# device token (device_auth.py) authorises ONLY posting real steps read by the
# phone. Points from synced steps are REWARDS ONLY — never premium (Google Play
# forbids health data → insurance pricing; rewards/provisional_rates.py).

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@customer_auth.customer_required
def customer_pair_start(request):
    """POST /rewards/customer/pair/start/  (web session)  → {code}

    The logged-in web page asks for a single-use pairing code. The native
    PairingActivity is launched with this code inside an intent:// link and
    swaps it for a device token via /pair/complete/. The backend binds the
    device to THIS member because it minted the code under this session.
    """
    member = request.customer_member
    if device_auth.recent_pair_code_count(member) >= device_auth.PAIR_RATE_PER_HOUR:
        return Response({'detail': 'Too many attempts — please try again later.'},
                        status=status.HTTP_429_TOO_MANY_REQUESTS)
    code = device_auth.issue_pairing_code(member)
    return Response({'code': code, 'expiresInSeconds': device_auth.PAIR_CODE_TTL_MINUTES * 60})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([CustomerOtpThrottle])
def customer_pair_complete(request):
    """POST /rewards/customer/pair/complete/  {code, deviceLabel?}  → {deviceToken}

    Called by the native PairingActivity. Swaps a valid pairing code for a
    device token scoped to step-sync only. Public (the app has no session yet),
    but a code is single-use, short-lived and attempt-limited.
    """
    d = request.data or {}
    code = str(d.get('code') or '')
    if not code:
        return Response({'detail': 'Missing pairing code.'}, status=status.HTTP_400_BAD_REQUEST)
    token = device_auth.complete_pairing(code, device_label=str(d.get('deviceLabel') or ''))
    if token is None:
        return Response({'detail': 'That pairing code is invalid or has expired.'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'deviceToken': token})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@device_auth.device_required
def customer_steps_sync(request):
    """POST /rewards/customer/steps/sync/  (Bearer device token)  {steps, day?}

    Record today's synced step total for the paired member and award points on
    the GROWTH of the total (delta), so re-syncs top up and exact replays earn 0.
    One row per member per day makes double-counting structurally impossible.
    A web session bearer CANNOT reach this endpoint (device_required checks the
    device-token store only).
    """
    from django.db import transaction
    from django.utils import timezone

    d = request.data or {}
    member = request.device_member
    try:
        steps = int(d.get('steps') or 0)
    except (TypeError, ValueError):
        steps = 0
    steps = max(0, min(steps, MAX_SYNCED_STEPS_PER_DAY))

    from datetime import timedelta
    today = timezone.localdate()
    day = parse_date(str(d.get('day') or '')) or today
    # Clamp to a tight window: a future date (clock-skew/spoof) becomes today; a
    # date older than 2 days is refused so a paired device cannot back-date-farm
    # points for every historical day (Fable v10 review).
    if day > today:
        day = today
    if day < today - timedelta(days=2):
        return Response({'detail': 'That date is too far in the past to sync.'},
                        status=status.HTTP_400_BAD_REQUEST)

    target_points = health_points.steps_to_points(steps)  # cumulative for this total (capped)

    with transaction.atomic():
        row, _created = (CustomerStepDay.objects
                         .select_for_update()
                         .get_or_create(member=member, day=day,
                                        defaults={'steps_total': 0, 'points_awarded': 0,
                                                  'source': _sync_source(d)}))
        delta_points = 0
        if steps > row.steps_total:
            row.steps_total = steps
            row.source = _sync_source(d)
        # Award only the growth in cumulative points for the day.
        if target_points > row.points_awarded:
            delta_points = target_points - row.points_awarded
            row.points_awarded = target_points
        row.save(update_fields=['steps_total', 'points_awarded', 'source', 'updated_at'])

        if delta_points > 0:
            member = RewardMember.objects.select_for_update().get(pk=member.pk)
            program, _ = RewardProgram.objects.get_or_create(
                code=RewardProgram.Code.HEALTH,
                defaults={'name': 'Health Points', 'is_active': True})
            PointsTransaction.objects.create(
                member=member, program=program, kind=PointsTransaction.Kind.EARN,
                points=delta_points, occurred_at=timezone.now(),
                detail=f'Synced steps: {steps:,}')
            member.points_balance = (member.points_balance or 0) + delta_points
            member.tier = health_points.tier_for(member.points_balance)
            member.save(update_fields=['points_balance', 'tier', 'updated_at'])

    return Response({
        'stepsToday': row.steps_total,
        'pointsToday': row.points_awarded,
        'pointsAdded': delta_points,
        'totalPoints': member.points_balance,
        'tier': member.tier,
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@device_auth.device_required
def customer_workouts_sync(request):
    """POST /rewards/customer/workouts/sync/  (Bearer device token)
    Body: {sessions: [{id, exerciseType?, start, end}, ...]}

    Record Health Connect exercise sessions for the paired member. The SERVER
    sets the points: +2 per real session (>= WORKOUT_MIN_MINUTES), at most
    WORKOUT_PAID_SESSIONS_PER_DAY paid sessions per local day. A session id
    already stored earns 0 (replay-safe); extra sessions past the daily cap are
    stored with 0 points so a later replay of them also earns 0. A web session
    bearer CANNOT reach this endpoint (device_required = device-token store only).
    """
    from datetime import timedelta
    from django.db import transaction
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    member = request.device_member
    d = request.data or {}
    raw = d.get('sessions')
    if not isinstance(raw, list):
        return Response({'detail': 'Send a list of sessions.'}, status=status.HTTP_400_BAD_REQUEST)
    raw = raw[:WORKOUT_BATCH_LIMIT]

    today = timezone.localdate()
    oldest = today - timedelta(days=2)
    clean = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get('id') or '').strip()[:128]
        start = parse_datetime(str(item.get('start') or ''))
        end = parse_datetime(str(item.get('end') or ''))
        if not sid or start is None or end is None or sid in clean:
            continue
        if timezone.is_naive(start) or timezone.is_naive(end):
            continue               # Health Connect gives zoned instants; a naive time is garbage
        minutes = int((end - start).total_seconds() // 60)
        if minutes < WORKOUT_MIN_MINUTES or minutes > WORKOUT_MAX_MINUTES:
            continue
        day = timezone.localdate(start)
        if day > today:
            day = today            # clock skew / spoof → today
        if day < oldest:
            continue               # back-date farming refused
        try:
            ex_type = int(item.get('exerciseType') or 0)
        except (TypeError, ValueError):
            ex_type = 0
        ex_type = max(0, min(ex_type, 999))  # Health Connect codes are small ints
        clean[sid] = (day, ex_type, start, end, minutes)

    added = 0
    with transaction.atomic():
        member = RewardMember.objects.select_for_update().get(pk=member.pk)
        for sid, (day, ex_type, start, end, minutes) in clean.items():
            if CustomerWorkoutSession.objects.filter(member=member, session_id=sid).exists():
                continue           # replay of a known session → 0
            paid_today = CustomerWorkoutSession.objects.filter(
                member=member, day=day, points_awarded__gt=0).count()
            pts = WORKOUT_POINTS_PER_SESSION if paid_today < WORKOUT_PAID_SESSIONS_PER_DAY else 0
            CustomerWorkoutSession.objects.create(
                member=member, session_id=sid, day=day, exercise_type=ex_type,
                started_at=start, ended_at=end, duration_minutes=minutes, points_awarded=pts,
                source=_sync_source(d))
            added += pts
        if added > 0:
            program, _ = RewardProgram.objects.get_or_create(
                code=RewardProgram.Code.HEALTH,
                defaults={'name': 'Health Points', 'is_active': True})
            PointsTransaction.objects.create(
                member=member, program=program, kind=PointsTransaction.Kind.EARN,
                points=added, occurred_at=timezone.now(),
                detail=f'Synced workouts: {added // WORKOUT_POINTS_PER_SESSION} session(s)')
            member.points_balance = (member.points_balance or 0) + added
            member.tier = health_points.tier_for(member.points_balance)
            member.save(update_fields=['points_balance', 'tier', 'updated_at'])

    sessions_today = CustomerWorkoutSession.objects.filter(member=member, day=today).count()
    return Response({
        'sessionsToday': sessions_today,
        'pointsAdded': added,
        'totalPoints': member.points_balance,
        'tier': member.tier,
    })
