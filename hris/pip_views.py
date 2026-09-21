"""hris/pip_views.py — CFO-directed Performance Improvement Plans.

A management-raised PIP for a specific documented issue, shown on the employee's
dashboard and to a NAMED audience, with a box for the employee to explain the
issue (the ELRA "opportunity to respond"). CFO directive 2026-07-15 (Legakwa
Ntabeni — delayed commission-payment processing).

Deliberately INDEPENDENT of the dormant ELRA monthly-check-in module
(`ELRA_PERF_ENABLED`): this is a single, per-record, tightly-scoped PIP raised by
management with authority, not the bulk performance-data collection that gate
protects. Visibility is per-record:
  • the subject employee — view + fill their explanation (only they can),
  • named viewer_emails — read-only,
  • the person who opened it + the HRIS exec/HR whitelist + superusers — read-only.

    GET   /hris/api/pips/directed/                 PIPs the caller may see
    PATCH /hris/api/pips/directed/<uuid>/explain/  subject submits their explanation
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from hris.performance_feedback_models import PerformanceImprovementPlan
from hris.performance_views import _resolve_employee


def _norm(email) -> str:
    return (email or '').strip().lower()


def _is_subject(user, pip) -> bool:
    emp = pip.profile.employee if pip.profile_id else None
    if emp is None:
        return False
    if emp.user_id and emp.user_id == getattr(user, 'id', None):
        return True
    # Fall back to email match (staff linked by email, not user FK).
    return bool(_norm(getattr(emp, 'email', '')) and
                _norm(emp.email) == _norm(getattr(user, 'email', '')))


def _can_view(user, pip) -> bool:
    if _is_subject(user, pip):
        return True
    if getattr(user, 'is_superuser', False):
        return True
    if pip.opened_by_id and pip.opened_by_id == getattr(user, 'id', None):
        return True
    if _norm(getattr(user, 'email', '')) in {_norm(e) for e in (pip.viewer_emails or [])}:
        return True
    try:
        from core.hris_access import user_can_access_hris
        if user_can_access_hris(user):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _serialize(pip, user) -> dict:
    return {
        'id':            str(pip.id),
        'title':         pip.title or 'Performance Improvement Plan',
        'employee_name': pip.profile.employee.full_name if pip.profile_id else '',
        'reason':        pip.reason,
        'objectives':    pip.objectives,
        'support_plan':  pip.support_plan,
        'start_date':    str(pip.start_date) if pip.start_date else '',
        'review_date':   str(pip.review_date) if pip.review_date else '',
        'end_date':      str(pip.end_date) if pip.end_date else '',
        'status':        pip.status,
        'status_label':  pip.get_status_display(),
        'outcome':       pip.outcome,
        'opened_by':     (pip.opened_by.get_full_name() or pip.opened_by.username)
                         if pip.opened_by_id else 'Management',
        'employee_explanation':    pip.employee_explanation,
        'employee_explanation_at': pip.employee_explanation_at.isoformat()
                                   if pip.employee_explanation_at else None,
        'is_subject':    _is_subject(user, pip),
        'can_explain':   _is_subject(user, pip),
        'viewers':       list(pip.viewer_emails or []),
        'created_at':    pip.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def directed_pips(request):
    """GET — the directed PIPs the caller is allowed to see."""
    qs = (PerformanceImprovementPlan.objects
          .filter(directed=True)
          .select_related('profile', 'profile__employee', 'opened_by')
          .order_by('-start_date', '-created_at'))
    rows = [_serialize(p, request.user) for p in qs if _can_view(request.user, p)]
    return Response({'count': len(rows), 'pips': rows})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def pip_explain(request, pk):
    """PATCH — the SUBJECT records their explanation. Only the subject; the time
    is stamped server-side so it cannot be forged or back-dated."""
    pip = (PerformanceImprovementPlan.objects
           .filter(pk=pk, directed=True)
           .select_related('profile', 'profile__employee').first())
    if pip is None:
        return Response({'detail': 'PIP not found.'}, status=404)
    if not _is_subject(request.user, pip):
        return Response(
            {'detail': 'Only the employee this plan is about can add the explanation.'},
            status=403)
    text = (request.data.get('explanation') or '').strip()[:8000]
    if not text:
        return Response({'detail': 'Please write your explanation.'}, status=400)
    pip.employee_explanation = text
    pip.employee_explanation_at = timezone.now()
    if pip.status == PerformanceImprovementPlan.Status.OPEN:
        pip.status = PerformanceImprovementPlan.Status.IN_PROGRESS
    pip.save(update_fields=['employee_explanation', 'employee_explanation_at',
                            'status', 'updated_at'])
    return Response(_serialize(pip, request.user))
