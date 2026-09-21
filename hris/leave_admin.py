"""
hris/leave_admin.py — HR leave-administration surfaces (Unami Butale, HR,
2026-07-27, forwarded by the CFO).

Adds the HR-oversight half of the leave module that the manager/employee
screens didn't cover:

  * leave_certificate   — GATED download of a sick-leave medical certificate
                          (Unami "I am unable to view the sick leave attached" —
                          the cert was never actually servable; media is not
                          proxied to Django in prod). Mirrors the disciplinary
                          evidence-download gate.
  * leave_admin_all     — every employee's leave, filterable by status +
                          department (asks #1 approved / #2 pending).
  * leave_admin_analytics — per-department leave dashboard (#3): on leave today,
                          going on leave in the next 14 days, per-department and
                          per-type rollups for the current leave year.
  * leave_hr_queue      — HR verification queue (#5 dual approval): manager-
                          approved sick leave awaiting HR authentication.
  * hr_verify_leave     — HR verifies or flags a request (fraud control).

All HR surfaces gate on `manage_leave_admin` (hr / hris / admin) and clamp to
the caller's entity scope. The certificate download gates on the lighter
`approve_team_leave` so a manager can view the cert for a request they decide.
"""
from __future__ import annotations
from hris.departments import fold_legacy

import datetime as _dt
import os

from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import ROLE_CAPABILITIES, hris_role, user_can_access_hris
from core.mixins import apply_company_scope
from hris.feature_views import _gate
from hris.models import HRISProfile, LeaveRequest
from hris.leave_balance import days_out

# Medical certificates may only be PDFs or images. Mapped to an explicit
# content-type so the serve path never guesses a scriptable type (text/html,
# image/svg+xml). Shared with the upload validator in feature_views.apply_leave.
CERT_CONTENT_TYPES = {
    '.pdf':  'application/pdf',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
}


def _may_view_certificate(user, lr) -> bool:
    """A medical certificate is sensitive PII, so viewing it is NOT open to every
    manager in the company. Allow: HR (manage_leave_admin), the person who was
    asked to / did decide this request, or the employee's own line manager."""
    caps = ROLE_CAPABILITIES.get(hris_role(user), set())
    # The blanket "HR may see any certificate" branch must ALSO require real
    # HRIS clearance. hris_role() maps the finance titles (CFO, Finance Manager,
    # Financial Controller, HR Manager) to role 'hr', which holds
    # manage_leave_admin — so once approve_team_leave gained the lighter
    # TEAM_CAPS tier, a finance-titled account that is not on the HRIS whitelist
    # could have pulled ANY employee's medical certificate. Staff medical data
    # stays behind the whitelist (Fable review 2026-08-07).
    if 'manage_leave_admin' in caps and user_can_access_hris(user):
        return True
    if lr.requested_approver_id == user.id or lr.approver_id == user.id:
        return True
    mgr = getattr(lr.profile, 'manager', None)
    return bool(mgr and getattr(mgr, 'user_id', None) == user.id)


# ── shared helpers ──────────────────────────────────────────────────────────

def tasks_in_window_count(lr) -> int:
    """How many of the employee's OPEN dashboard tasks fall inside the leave
    window — surfaced on the approval queue so a manager can decline a request
    that would drop live work (ask #7). Best-effort: never raise into a queue."""
    try:
        from core.models import OmniTask
        user_id = getattr(lr.profile.employee, 'user_id', None)
        if not user_id:
            return 0
        open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS,
                       OmniTask.Status.BLOCKED]
        return (OmniTask.objects
                .filter(assignee_id=user_id, status__in=open_states,
                        due_at__date__gte=lr.start_date,
                        due_at__date__lte=lr.end_date)
                .count())
    except Exception:        # noqa: BLE001
        return 0


def _cert_url(lr) -> str | None:
    has = bool(getattr(lr, 'medical_certificate', None) and lr.medical_certificate.name)
    return f'/hris/api/leave-requests/{lr.id}/certificate/' if has else None


def serialize_row(lr) -> dict:
    """One leave row for the HR admin lists / queues."""
    emp = lr.profile.employee
    return {
        'id':              str(lr.id),
        'employee':        emp.full_name,
        'department':      emp.department or '',
        'leave_type':      lr.leave_type.name if lr.leave_type_id else '',
        'leave_code':      lr.leave_type.code if lr.leave_type_id else '',
        'start_date':      str(lr.start_date),
        'end_date':        str(lr.end_date),
        'days':            float(lr.days or 0),
        'day_breakdown':   lr.day_breakdown(),
        'status':          lr.status,
        'status_label':    lr.get_status_display(),
        'reason':          lr.reason or '',
        'reason_category': lr.get_reason_category_display() if lr.reason_category else '',
        'has_certificate': _cert_url(lr) is not None,
        'certificate_url': _cert_url(lr),
        'hr_review_state': lr.hr_review_state,
        'hr_review_label': lr.get_hr_review_state_display(),
        'hr_review_notes': lr.hr_review_notes or '',
        'approver':        ((lr.approver.get_full_name() or lr.approver.username)
                            if lr.approver_id else ''),
        'decided_at':      lr.decided_at.isoformat() if lr.decided_at else None,
        'created_at':      lr.created_at.isoformat(),
    }


# ── ask #4 — view the sick-leave certificate ────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_certificate(request, leave_id):
    """GET /hris/api/leave-requests/<uuid:leave_id>/certificate/
    Stream the medical certificate behind the approver gate. Media is never
    served directly (Caddy does not proxy /media/ to Django in prod), so this
    is the only way to view it. Opens inline so HR can read it in the browser."""
    gate = _gate(request, capability='approve_team_leave')
    if gate is not None:
        return gate
    lr = (apply_company_scope(request,
                              LeaveRequest.objects.filter(pk=leave_id),
                              'profile__employee__company_id')
          .select_related('profile__employee', 'profile__manager').first())
    if lr is None:
        return Response({'detail': 'Leave request not found.'}, status=404)
    if not _may_view_certificate(request.user, lr):
        return Response({'detail': 'You can only view certificates for leave you review.'},
                        status=403)
    if not (lr.medical_certificate and lr.medical_certificate.name):
        return Response({'detail': 'No certificate attached to this leave request.'},
                        status=404)
    # Serve with an EXPLICIT content-type from an extension whitelist — never let
    # mimetype-guessing pick text/html or image/svg+xml. The frontend opens the
    # blob same-origin, so an employee-uploaded .html/.svg would otherwise run
    # script on omni when HR views it (stored XSS). Only the safe, inline-render
    # types open inline; anything else is forced to download.
    fname = lr.medical_certificate.name.split('/')[-1] or 'certificate'
    ext = os.path.splitext(fname)[1].lower()
    content_type = CERT_CONTENT_TYPES.get(ext)
    return FileResponse(lr.medical_certificate.open('rb'),
                        as_attachment=content_type is None,
                        content_type=content_type or 'application/octet-stream',
                        filename=fname)


# ── asks #1 / #2 — all approved / all pending leave ─────────────────────────

_STATUS_MAP = {
    'pending':   LeaveRequest.Status.PENDING,
    'approved':  LeaveRequest.Status.APPROVED,
    'refused':   LeaveRequest.Status.REFUSED,
    'cancelled': LeaveRequest.Status.CANCELLED,
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_admin_all(request):
    """GET /hris/api/leave-admin/all/?status=pending|approved|...|all&department=
    Every employee's leave for HR oversight (Unami wants to see ALL approved and
    ALL pending leave, not only what routed to her)."""
    gate = _gate(request, capability='manage_leave_admin')
    if gate is not None:
        return gate
    status_param = (request.query_params.get('status') or 'pending').lower().strip()
    if status_param not in _STATUS_MAP and status_param != 'all':
        return Response(
            {'detail': 'status must be pending|approved|refused|cancelled|all.'},
            status=400)
    dept = (request.query_params.get('department') or '').strip()

    qs = (LeaveRequest.objects
          .select_related('profile__employee', 'leave_type', 'approver'))
    qs = apply_company_scope(request, qs, 'profile__employee__company_id')
    if status_param in _STATUS_MAP:
        qs = qs.filter(status=_STATUS_MAP[status_param])
    if dept:
        qs = qs.filter(profile__employee__department__iexact=fold_legacy(dept) or dept)
    qs = qs.order_by('-start_date')[:500]
    rows = [serialize_row(lr) for lr in qs]
    return Response({'count': len(rows), 'status': status_param, 'rows': rows})


# ── ask #3 — department leave analytics ─────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_admin_analytics(request):
    """GET /hris/api/leave-admin/analytics/
    Department dashboard: who is on leave today, who is off in the next 14 days,
    and per-department / per-type approved-day rollups for the current year."""
    gate = _gate(request, capability='manage_leave_admin')
    if gate is not None:
        return gate

    today = timezone.localdate()
    horizon = today + _dt.timedelta(days=14)
    year_start = _dt.date(today.year, 1, 1)

    base = apply_company_scope(
        request,
        LeaveRequest.objects.select_related('profile__employee', 'leave_type'),
        'profile__employee__company_id')

    # On leave TODAY (approved, spanning today).
    on_today = (base.filter(status=LeaveRequest.Status.APPROVED,
                            start_date__lte=today, end_date__gte=today)
                .order_by('profile__employee__full_name'))
    # Going on leave in the next 14 days (approved, starting after today).
    upcoming = (base.filter(status=LeaveRequest.Status.APPROVED,
                            start_date__gt=today, start_date__lte=horizon)
                .order_by('start_date'))

    # Year-to-date rollups (approved only) for department + leave-type dashboards.
    ytd = (base.filter(status=LeaveRequest.Status.APPROVED,
                       start_date__gte=year_start, start_date__lte=today))

    by_dept: dict[str, dict] = {}
    by_type: dict[str, float] = {}
    for lr in ytd:
        dept = (lr.profile.employee.department or 'Unassigned')
        d = by_dept.setdefault(dept, {'department': dept, 'approved_days': 0.0,
                                      'on_leave_today': 0, 'requests': 0})
        d['approved_days'] += float(lr.days or 0)
        d['requests'] += 1
        tname = lr.leave_type.name if lr.leave_type_id else 'Other'
        by_type[tname] = by_type.get(tname, 0.0) + float(lr.days or 0)

    for lr in on_today:
        dept = (lr.profile.employee.department or 'Unassigned')
        d = by_dept.setdefault(dept, {'department': dept, 'approved_days': 0.0,
                                      'on_leave_today': 0, 'requests': 0})
        d['on_leave_today'] += 1

    # Pending count per department (for the "needs attention" column).
    pending = (base.filter(status=LeaveRequest.Status.PENDING))
    for lr in pending:
        dept = (lr.profile.employee.department or 'Unassigned')
        d = by_dept.setdefault(dept, {'department': dept, 'approved_days': 0.0,
                                      'on_leave_today': 0, 'requests': 0})
        d['pending'] = d.get('pending', 0) + 1

    dept_rows = sorted(by_dept.values(),
                       key=lambda r: (-r.get('on_leave_today', 0), -r['approved_days']))
    for r in dept_rows:
        r.setdefault('pending', 0)
        r['approved_days'] = days_out(r['approved_days'])
    type_rows = [{'leave_type': k, 'approved_days': days_out(v)}
                 for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])]

    return Response({
        'today':          str(today),
        'on_leave_today': [serialize_row(lr) for lr in on_today],
        'upcoming_14d':   [serialize_row(lr) for lr in upcoming],
        'by_department':  dept_rows,
        'by_type':        type_rows,
    })


# ── ask #5 — HR dual-approval / verification queue ──────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_hr_queue(request):
    """GET /hris/api/leave-admin/hr-queue/
    Manager-approved sick leave awaiting HR authentication (dates + certificate).
    """
    gate = _gate(request, capability='manage_leave_admin')
    if gate is not None:
        return gate
    qs = (LeaveRequest.objects
          .filter(hr_review_state=LeaveRequest.HRReviewState.PENDING)
          .select_related('profile__employee', 'leave_type', 'approver'))
    qs = apply_company_scope(request, qs, 'profile__employee__company_id')
    qs = qs.order_by('-start_date')[:200]
    rows = [serialize_row(lr) for lr in qs]
    return Response({'count': len(rows), 'rows': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hr_verify_leave(request, leave_id):
    """POST /hris/api/leave-admin/<uuid:leave_id>/hr-verify/
    Body: {"decision": "verify"|"flag", "notes": "..."} — HR authenticates the
    manager-approved leave (fraud control). Flagging records the concern without
    reversing the manager's decision; HR follows up off-system / via disciplinary.
    """
    gate = _gate(request, capability='manage_leave_admin')
    if gate is not None:
        return gate
    decision = (request.data.get('decision') or '').strip().lower()
    notes = (request.data.get('notes') or '').strip()
    if decision not in ('verify', 'flag'):
        return Response({'detail': 'decision must be verify|flag.'}, status=400)
    if decision == 'flag' and not notes:
        return Response({'detail': 'A flag needs a short note on the concern.'},
                        status=400)

    lr = (apply_company_scope(request,
                              LeaveRequest.objects.filter(pk=leave_id),
                              'profile__employee__company_id')
          .select_related('profile__employee').first())
    if lr is None:
        return Response({'detail': 'Leave request not found.'}, status=404)
    # Segregation of duties: HR cannot authenticate their own leave (mirrors
    # decide_leave blocking self-approval) — the whole point is an independent
    # fraud check.
    if lr.profile.employee.user_id and lr.profile.employee.user_id == request.user.id:
        return Response({'detail': 'You cannot HR-verify your own leave.'}, status=403)
    if lr.hr_review_state not in (LeaveRequest.HRReviewState.PENDING,
                                  LeaveRequest.HRReviewState.VERIFIED,
                                  LeaveRequest.HRReviewState.FLAGGED):
        return Response({'detail': 'This request is not in HR review.'}, status=409)

    lr.hr_review_state = (LeaveRequest.HRReviewState.VERIFIED if decision == 'verify'
                          else LeaveRequest.HRReviewState.FLAGGED)
    lr.hr_reviewer = request.user
    lr.hr_reviewed_at = timezone.now()
    if notes:
        lr.hr_review_notes = notes
    lr.save(update_fields=['hr_review_state', 'hr_reviewer', 'hr_reviewed_at',
                           'hr_review_notes', 'updated_at'])
    return Response(serialize_row(lr))
