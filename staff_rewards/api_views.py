"""staff_rewards/api_views.py — Alpha Staff Rewards API (DRF).

Endpoints (wired under 'staff-rewards/...' in alpha_finance/api_router.py):
  POST staff-rewards/submit/                  — log an activity (any staff)
  GET  staff-rewards/dashboard/               — caller's pillars + tiers
  GET  staff-rewards/pending/                 — approver queue (hr/admin only)
  POST staff-rewards/submissions/<id>/approve/
  POST staff-rewards/submissions/<id>/reject/

Identity: the caller's payroll.Employee is resolved by the linked Django
user first (Employee.user), then by email. No body-supplied employee id is
trusted for the caller's own submission.

DPA: payloads carry Member ID + references only; service.submit_staff
strips any name/identity/location keys before persisting.
"""
from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import hris_role
from payroll.models import Employee

from .models import (
    FEATURE_PILLAR, PILLAR_FIELD, Pillar, StaffPointsAccount, StaffSubmission,
)
from .service import (
    FeatureDisabled, UnknownFeature, approve_submission, reject_submission,
    submit_staff,
)

# Approver tiers — gate via core.hris_access.hris_role (spec: approver only).
_APPROVER_ROLES = {'hr', 'hris', 'admin', 'superadmin'}


def _resolve_employee(request):
    """Resolve the caller's Employee via linked user, then email."""
    emp = getattr(request.user, 'employee_record', None)
    if emp is not None:
        return emp
    email = (getattr(request.user, 'email', '') or '').strip()
    if email:
        return Employee.objects.filter(email__iexact=email).first()
    return None


def _account_for(employee) -> StaffPointsAccount:
    account, _ = StaffPointsAccount.objects.get_or_create(employee=employee)
    return account


def _recent(account, pillar, limit=5):
    """Most-recent transactions feeding a pillar — submission-backed rows (via
    the submission's feature) plus submission-less rows tagged with the pillar
    directly (e.g. task-performance adjustments, CFO 2026-07-13)."""
    feature_codes = [fc for fc, p in FEATURE_PILLAR.items() if p == pillar]
    rows = (account.transactions
            .filter(Q(submission__feature_code__in=feature_codes)
                    | Q(pillar=pillar))
            .select_related('submission')
            .order_by('-occurred_at')[:limit])
    out = []
    for t in rows:
        out.append({
            'points':      t.points,
            'detail':      t.detail,
            'feature':     (t.submission.feature_code if t.submission
                            else 'task_performance'),
            'occurredAt':  t.occurred_at.isoformat(),
        })
    return out


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit(request):
    """POST staff-rewards/submit/  Body: {feature_code, payload}"""
    employee = _resolve_employee(request)
    if employee is None:
        return Response(
            {'detail': 'No staff (payroll) record is linked to your account.'},
            status=status.HTTP_403_FORBIDDEN)

    data = request.data or {}
    feature_code = data.get('feature_code') or data.get('featureCode')
    payload = data.get('payload') or {}
    if not feature_code:
        return Response({'detail': 'feature_code is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        sub = submit_staff(
            employee, feature_code, payload,
            maker_email=(getattr(request.user, 'email', '') or ''))
    except UnknownFeature as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except FeatureDisabled as exc:
        return Response({'detail': str(exc)},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    return Response({
        'id':          str(sub.id),
        'pillar':      sub.pillar,
        'featureCode': sub.feature_code,
        'status':      sub.status,
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard(request):
    """GET staff-rewards/dashboard/ — caller's pillars + overall tier."""
    employee = _resolve_employee(request)
    if employee is None:
        return Response(
            {'detail': 'No staff (payroll) record is linked to your account.'},
            status=status.HTTP_403_FORBIDDEN)

    account = _account_for(employee)
    from .tiers import progress_to_next, tier_for

    pillars = []
    for key in (Pillar.INNOVATION, Pillar.BUSINESS_IMPACT, Pillar.HEALTH_WELLNESS):
        total = account.pillar_total(key)
        pillars.append({
            'key':      key,
            'label':    Pillar(key).label,
            'total':    total,
            'tier':     tier_for(total),
            'progress': progress_to_next(total),
            'recent':   _recent(account, key),
        })

    return Response({
        'pillars':       pillars,
        'overallPoints': account.points_balance,
        'overallTier':   account.tier,
        'overallProgress': account.progress(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending(request):
    """GET staff-rewards/pending/ — approver-only queue."""
    if hris_role(request.user) not in _APPROVER_ROLES:
        return Response({'detail': 'Approver access required.'},
                        status=status.HTTP_403_FORBIDDEN)

    rows = (StaffSubmission.objects
            .filter(status=StaffSubmission.Status.PENDING)
            .select_related('employee')
            .order_by('created_at'))
    out = [{
        'id':          str(s.id),
        'employee':    s.employee.full_name,
        'department':  s.employee.department,
        'pillar':      s.pillar,
        'featureCode': s.feature_code,
        'payload':     s.payload,
        'makerEmail':  s.maker_email,
        'createdAt':   s.created_at.isoformat(),
    } for s in rows]
    return Response({'pending': out, 'count': len(out)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve(request, pk):
    """POST staff-rewards/submissions/<id>/approve/  Body: {reason?}"""
    if hris_role(request.user) not in _APPROVER_ROLES:
        return Response({'detail': 'Approver access required.'},
                        status=status.HTTP_403_FORBIDDEN)

    sub = get_object_or_404(StaffSubmission, pk=pk)
    if sub.status == StaffSubmission.Status.REJECTED:
        return Response({'detail': 'Submission was rejected; cannot approve.'},
                        status=status.HTTP_409_CONFLICT)

    sub = approve_submission(sub, request.user,
                             reason=(request.data or {}).get('reason', ''))
    return Response({
        'id':            str(sub.id),
        'status':        sub.status,
        'pointsAwarded': sub.points_awarded,
        'pillar':        sub.pillar,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject(request, pk):
    """POST staff-rewards/submissions/<id>/reject/  Body: {reason?}"""
    if hris_role(request.user) not in _APPROVER_ROLES:
        return Response({'detail': 'Approver access required.'},
                        status=status.HTTP_403_FORBIDDEN)

    sub = get_object_or_404(StaffSubmission, pk=pk)
    if sub.status == StaffSubmission.Status.APPROVED:
        return Response({'detail': 'Submission already approved; cannot reject.'},
                        status=status.HTTP_409_CONFLICT)

    sub = reject_submission(sub, request.user,
                            reason=(request.data or {}).get('reason', ''))
    return Response({'id': str(sub.id), 'status': sub.status})

# ---------------------------------------------------------------------------
# Healthy Eating — server-side meal-photo scoring (CFO-approved 2026-06-26).
#
# DPA-safe by construction:
#   * opt-in consent REQUIRED (consent=true in the body) — no silent capture.
#   * the image is scored in-memory and DISCARDED — never persisted, never logged.
#   * only the numeric score is stored, keyed to the employee's Member ID.
#   * the AI prompt carries NO name — Member ID only.
#   * scoring is SERVER-SIDE so the client cannot forge the score (anti-tamper).
#   * vision model = Gemini (never Anthropic, per CFO standing rule); if Gemini
#     is unavailable we 503 rather than fall back to a banned provider.
# ---------------------------------------------------------------------------
import base64 as _b64
import re as _re

_MEAL_MIN_SCORE = 60   # TODO(human): qualifying threshold — HR + CFO sign-off


def _score_meal_image(data_url_or_b64: str, member_id: str) -> int:
    """Return a 0-100 healthiness score for a meal photo.

    Scored by core.ai_assist.vision_complete, which fails over across
    vision-capable engines (Gemini 2.5-flash → Gemini 2.0-flash → Grok vision)
    so one model's outage/overload no longer 503s the scan. DeepSeek is NOT in
    the chain — its API can't see images. The image is sent once and never
    stored. Raises (→ caller 503) only if every configured vision engine fails;
    never falls back to a banned provider."""
    b64 = data_url_or_b64.split(',', 1)[1] if data_url_or_b64.startswith('data:') else data_url_or_b64
    # validate it decodes (and bound size ~6MB) — then build a clean data URL
    raw = _b64.b64decode(b64, validate=False)
    if len(raw) > 6 * 1024 * 1024:
        raise ValueError('image too large')
    data_url = 'data:image/jpeg;base64,' + _b64.b64encode(raw).decode()

    prompt = (f'You are a nutrition scorer for staff member {member_id} (no name supplied). '
              'Rate how healthy this meal looks from 0 to 100 (100 = very healthy whole foods, '
              'low = fried/sugary/processed). Reply ONLY compact JSON: {"score": <int 0-100>}.')
    # Vision with automatic failover: Gemini 2.5-flash → Gemini 2.0-flash → Grok
    # vision (whichever keys are configured). NOT DeepSeek — its API is text-only
    # and cannot see the photo. vision_complete disables gemini-2.5-flash's hidden
    # "thinking" (which truncated the score to `{"` and 503'd the scan) and raises
    # VisionUnavailable only when EVERY configured vision engine fails.
    from core.ai_assist import vision_complete
    txt = vision_complete(prompt, data_url, timeout=30)
    m = _re.search(r'"?score"?\s*:?\s*(\d{1,3})', txt) or _re.search(r'\b(\d{1,3})\b', txt)
    if not m:
        raise ValueError(f'could not parse score from: {txt[:120]}')
    return max(0, min(100, int(m.group(1))))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def score_meal(request):
    """POST staff-rewards/score-meal/  Body: {imageData (base64/dataURL), consent, mealsTodayCount?}

    Scores the meal server-side, discards the image, and submits a 'meal'
    activity with the AI score. Opt-in consent is mandatory."""
    employee = _resolve_employee(request)
    if employee is None:
        return Response({'detail': 'No staff (payroll) record is linked to your account.'},
                        status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    if not (data.get('consent') in (True, 'true', 'True', 1, '1')):
        return Response({'detail': 'Opt-in consent is required to use meal scoring.'},
                        status=status.HTTP_403_FORBIDDEN)
    image = data.get('imageData') or data.get('photo') or ''
    if not image:
        return Response({'detail': 'A camera-captured image is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    member_id = str(employee.id)
    try:
        score = _score_meal_image(image, member_id)
    except Exception as exc:        # noqa: BLE001 — never leak the image; never fall back to a banned provider
        return Response({'detail': f'Meal scoring is temporarily unavailable ({type(exc).__name__}).'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    qualifying = score >= _MEAL_MIN_SCORE
    # image is now out of scope and discarded — only the numeric score is kept.
    try:
        meals_today = int(data.get('mealsTodayCount') or 1)
    except (TypeError, ValueError):
        meals_today = 1
    try:
        sub = submit_staff(employee, 'meal',
                           {'memberId': member_id, 'aiScore': score,
                            'qualifying': qualifying, 'mealsTodayCount': meals_today},
                           maker_email=(getattr(request.user, 'email', '') or ''))
    except (FeatureDisabled, UnknownFeature) as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return Response({'aiScore': score, 'qualifying': qualifying,
                     'submissionId': str(sub.id), 'status': sub.status},
                    status=status.HTTP_201_CREATED)
