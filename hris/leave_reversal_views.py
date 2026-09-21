"""hris/leave_reversal_views.py — endpoints for reversing worked leave.

Thin on purpose: the rules live in leave_reversal_service so they hold however a
claim arrives, and so they can be tested without a request.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.feature_views import _gate, _profile_for
from hris.leave_reversal_models import LeaveReversal
from hris.leave_reversal_service import (
    approver_for,
    days_still_reversible,
    decide_reversal,
    evidence_vs_claim,
    request_reversal,
    timedoctor_evidence,
)
from hris.models import LeaveRequest

log = logging.getLogger(__name__)


def _row(rev: LeaveReversal) -> dict:
    """One reversal, with the ORIGINAL leave alongside it.

    The report asks for this explicitly — "approver sees the full original leave
    record alongside the reversal request" — because "reverse 2 days" is meaningless
    without the leave it came from.
    """
    lr = rev.leave_request
    return {
        'id':           str(rev.id),
        'status':       rev.status,
        'status_label': rev.get_status_display(),
        'days':         float(rev.days or 0),
        'reason':       rev.reason,
        'has_attachment': bool(rev.attachment),
        'requested_by': (rev.requested_by.get_full_name() or rev.requested_by.username)
                        if rev.requested_by_id else '',
        'requested_at': rev.created_at.isoformat(),
        'approver':     (rev.approver.get_full_name() or rev.approver.username)
                        if rev.approver_id else '',
        'decided_at':   rev.decided_at.isoformat() if rev.decided_at else None,
        'decision_notes': rev.decision_notes,
        'employee':     lr.profile.employee.full_name if lr.profile_id else '',
        # Time Doctor hours for the days this leave covered, so the manager decides
        # on evidence rather than on the claim alone (CFO 2026-08-10).
        'timedoctor':   timedoctor_evidence(lr),
        'evidence_note': evidence_vs_claim(lr, rev.days),
        'original_leave': {
            'id':         str(lr.id),
            'leave_type': lr.leave_type.name if lr.leave_type_id else '',
            'leave_code': lr.leave_type.code if lr.leave_type_id else '',
            # As the leave stood WHEN THE CLAIM WAS RAISED. Approving shortens the
            # live record, so showing only today's figure would quietly rewrite
            # history on an already-decided reversal.
            'start_date': str(rev.original_start_date or lr.start_date),
            'end_date':   str(rev.original_end_date or lr.end_date),
            'days_at_request':   float(rev.original_days or 0),
            'status_at_request': rev.original_status or lr.status,
            'days_now':   float(lr.days or 0),
            'status_now': lr.status,
        },
    }


def _has_hr_oversight(request) -> bool:
    """Whole-company view, exactly as pending_leave decides it.

    Copied deliberately rather than invented: an HR/finance-titled account that is
    NOT on the HRIS whitelist must fall back to its own team, or the lighter
    team-capability tier would hand it every employee's leave (Fable review
    2026-08-07, on the equivalent leave endpoint).
    """
    from core.hris_access import hris_role, user_can_access_hris
    from hris.amendment_service import UNAMI_EMAIL, _local
    return ((hris_role(request.user) in ('hr', 'hris')
             or _local(getattr(request.user, 'email', '')) == _local(UNAMI_EMAIL))
            and user_can_access_hris(request.user))


def _person_name(user) -> str:
    """A name a human recognises.

    Omni logins frequently have no first/last name set, so get_full_name() falls
    back to a username like `rev-user-101` — useless in a message telling somebody
    who to chase. The real name is on the linked payroll.Employee.
    """
    if user is None:
        return ''
    emp = getattr(user, 'employee_record', None)
    return ((getattr(emp, 'full_name', '') or '').strip()
            or (user.get_full_name() or '').strip()
            or user.username)


def _mine_to_decide(qs, user):
    """Reversals this user may see and decide.

    Whoever decided the ORIGINAL leave decides its reversal. That is already who
    `approver_for()` emails, but this predicate only ever asked "is this person one
    of your direct reports?" — so whenever the original approver was NOT the line
    manager (someone covering, or an EXCO member who signed the leave off), the
    person who received the email opened an EMPTY queue, was refused "this is not
    one of your team members" if they tried to decide, and could not open the
    attachment either; meanwhile the line manager, who was never told, could
    approve it. Route and authority now agree (report 2026-08-11, item 1).

    Where the original leave carries no approver there is nobody to route to, so
    the line manager stays the fallback rather than the claim stranding with no
    one able to act on it. The HR oversight tier is unchanged and sits outside
    this predicate.

    Manager link, same as pending_leave: HRISProfile.manager -> payroll.Employee
    -> user.
    """
    return qs.filter(
        Q(leave_request__approver=user)
        | Q(leave_request__approver__isnull=True,
            leave_request__profile__manager__user=user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_reversal(request, leave_id):
    """POST /hris/api/leave-requests/<id>/reversals/

    Multipart or JSON: days (0.5 upwards), reason, attachment (optional).
    """
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'detail': 'No employee profile is linked to your account.'},
                        status=404)
    lr = (LeaveRequest.objects.select_related('leave_type', 'profile__employee')
          .filter(pk=leave_id, profile=profile).first())
    if lr is None:
        return Response({'detail': 'Leave request not found.'}, status=404)

    att = request.FILES.get('attachment')
    if att is not None:
        from core.api_views import _validate_task_attachment
        err = _validate_task_attachment(att)
        if err:
            return Response({'detail': err}, status=400)

    try:
        rev = request_reversal(leave_request=lr, user=request.user,
                               days=request.data.get('days'),
                               reason=request.data.get('reason', ''),
                               attachment=att)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)

    try:
        from hris.leave_reversal_email import notify_reversal_raised
        notify_reversal_raised(rev)
    except Exception:   # noqa: BLE001 — a mail failure must not lose the claim
        # Logged, not dropped: if the manager is never told, the claim sits unseen
        # and the employee is waiting on somebody who does not know.
        log.exception('could not email the approver about reversal %s', rev.pk)

    approver = approver_for(lr)
    return Response({**_row(rev),
                     'approver_name': (approver.get_full_name() or approver.username)
                                      if approver else ''}, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_reversals(request):
    """GET /hris/api/leave-reversals/mine/ — the caller's own claims.

    Also returns, per approved leave request, how many days are still claimable, so
    the screen can offer a reversal without a second round-trip.
    """
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'count': 0, 'reversals': [], 'reversible_leave': []})

    qs = (LeaveReversal.objects.filter(profile=profile)
          .select_related('leave_request', 'leave_request__leave_type',
                          'leave_request__profile__employee',
                          'requested_by', 'approver'))
    reversible = []
    for lr in (LeaveRequest.objects
               .filter(profile=profile, status=LeaveRequest.Status.APPROVED)
               .select_related('leave_type').order_by('-start_date')[:40]):
        left = days_still_reversible(lr)
        if left > 0:
            reversible.append({
                'id':         str(lr.id),
                'leave_type': lr.leave_type.name if lr.leave_type_id else '',
                'start_date': str(lr.start_date),
                'end_date':   str(lr.end_date),
                'days':       float(lr.days or 0),
                'days_claimable': float(left),
            })
    rows = [_row(r) for r in qs]
    return Response({'count': len(rows), 'reversals': rows,
                     'reversible_leave': reversible})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending_reversals(request):
    """GET /hris/api/leave-reversals/pending/ — a manager's inbox."""
    denied = _gate(request, capability='approve_team_leave')
    if denied is not None:
        return denied
    qs = (LeaveReversal.objects.filter(status=LeaveReversal.Status.PENDING)
          .select_related('leave_request', 'leave_request__leave_type',
                          'leave_request__profile__employee',
                          'requested_by', 'approver'))
    if not _has_hr_oversight(request):
        qs = _mine_to_decide(qs, request.user)
    rows = [_row(r) for r in qs]
    return Response({'count': len(rows), 'reversals': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide(request, reversal_id):
    """POST /hris/api/leave-reversals/<id>/decide/  {decision: approve|decline, notes}

    Declining REQUIRES notes — the employee is shown the manager's own words.
    """
    denied = _gate(request, capability='approve_team_leave')
    if denied is not None:
        return denied
    rev = (LeaveReversal.objects
           .select_related('leave_request', 'leave_request__profile__employee')
           .filter(pk=reversal_id).first())
    if rev is None:
        return Response({'detail': 'Reversal not found.'}, status=404)

    # Nobody decides their own claim, however senior.
    if rev.requested_by_id == request.user.id:
        return Response({'detail': 'You cannot decide your own leave reversal.'},
                        status=403)
    if not _has_hr_oversight(request):
        if not _mine_to_decide(
                LeaveReversal.objects.filter(pk=rev.pk), request.user).exists():
            # Say WHO decides. "Not one of your team members" sent people back to
            # HR to ask, when the answer is on the original leave.
            orig = rev.leave_request.approver
            who = _person_name(orig) if orig else "the employee's line manager"
            return Response(
                {'detail': f'This reversal is for {who} to decide — they approved '
                           f'the original leave.'},
                status=403)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return Response({'detail': 'decision must be approve or decline.'}, status=400)

    try:
        rev = decide_reversal(reversal=rev, user=request.user,
                              approve=(decision == 'approve'),
                              notes=request.data.get('notes', ''))
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)

    try:
        from hris.leave_reversal_email import notify_reversal_decided
        notify_reversal_decided(rev)
    except Exception:   # noqa: BLE001 — the decision stands even if mail fails
        log.exception('could not email the employee the outcome of reversal %s',
                      rev.pk)
    return Response(_row(rev))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reversal_attachment(request, reversal_id):
    """GET /hris/api/leave-reversals/<id>/attachment/

    Streamed through an authenticated view, never a raw /media/ URL — /media/ is
    not served in production, so a bare file link 404s.
    """
    rev = LeaveReversal.objects.filter(pk=reversal_id).first()
    if rev is None or not rev.attachment:
        return Response({'detail': 'No attachment on this reversal.'}, status=404)
    mine = _profile_for(request.user)
    owns = mine is not None and rev.profile_id == mine.id
    decides = _mine_to_decide(
        LeaveReversal.objects.filter(pk=rev.pk), request.user).exists()
    if not (owns or decides or _has_hr_oversight(request)):
        return Response({'detail': 'Permission denied.'}, status=403)
    from django.http import FileResponse
    return FileResponse(rev.attachment.open('rb'), as_attachment=True,
                        filename=(rev.attachment.name or 'attachment').split('/')[-1])
