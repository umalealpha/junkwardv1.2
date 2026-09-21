"""
hris/roster_flag_views.py — raise / action a roster flag (CFO 2026-07-26).

A manager raises; HR actions. The manager's roster hides the person straight away
so they are not asked to account for someone who is not theirs, but the org chart
does not move until HR says so.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.mixins import apply_company_scope
from hris.models import HRISProfile
from hris.performance_views import _feature_off, _resolve_employee
from hris.roster_flag_models import (
    FlagKind, FlagStatus, RosterFlag, can_decide, decider_user,
)


def _serialize(f: RosterFlag) -> dict:
    emp = f.profile.employee
    return {
        'id': str(f.id),
        'employee': emp.full_name,
        'employee_id': str(emp.id),
        'job_title': emp.job_title or '',
        'current_manager': f.profile.manager.full_name if f.profile.manager else None,
        'kind': f.kind,
        'kind_label': f.get_kind_display(),
        'note': f.note,
        'status': f.status,
        'status_label': f.get_status_display(),
        'raised_by': getattr(f.raised_by, 'email', None),
        'raised_at': f.created_at,
        'hr_note': f.hr_note,
        'actioned_at': f.actioned_at,
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def flags(request):
    """GET  /hris/api/roster-flags/   — flags I raised (managers) or all open (HR)
    POST /hris/api/roster-flags/    — raise one {profile_id|employee_id, kind, note}
    """
    off = _feature_off()
    if off:
        return off
    me = _resolve_employee(request.user)

    if request.method == 'GET':
        if can_decide(request.user) or user_can_access_hris(request.user):
            qs = apply_company_scope(
                request,
                RosterFlag.objects.filter(status=FlagStatus.OPEN),
                'profile__employee__company_id')
        else:
            qs = RosterFlag.objects.filter(raised_by=request.user)
        qs = qs.select_related('profile__employee', 'profile__manager', 'raised_by')
        return Response({'flags': [_serialize(f) for f in qs[:300]]})

    data = request.data or {}
    profile = None
    if data.get('profile_id'):
        profile = HRISProfile.objects.filter(pk=data['profile_id']).first()
    elif data.get('employee_id'):
        profile = HRISProfile.objects.filter(employee_id=data['employee_id']).first()
    if profile is None:
        return Response({'detail': 'Employee not found.'}, status=404)

    # Only the person's line manager, their co-reviewer, or HR may flag them.
    allowed = bool(me and (profile.manager_id == me.id or profile.co_manager_id == me.id))
    if not (allowed or user_can_access_hris(request.user)):
        return Response({'detail': 'You can only flag someone on your own team.'},
                        status=403)

    kind = (data.get('kind') or '').strip()
    if kind not in FlagKind.values:
        return Response({'detail': 'Choose a reason.',
                         'choices': [{'value': k, 'label': l}
                                     for k, l in FlagKind.choices]}, status=400)

    existing = RosterFlag.objects.filter(
        profile=profile, raised_by=request.user, kind=kind,
        status=FlagStatus.OPEN).first()
    if existing:
        return Response(_serialize(existing))       # idempotent: one click, one flag

    flag = RosterFlag(profile=profile, raised_by=request.user, kind=kind,
                      note=(data.get('note') or '').strip())
    try:
        flag.clean()
    except ValidationError as e:
        return Response({'detail': 'Could not save.',
                         'errors': e.message_dict if hasattr(e, 'message_dict') else e.messages},
                        status=400)
    flag.save()
    _notify_decider(flag, request.user)
    return Response(_serialize(flag), status=201)


def _notify_decider(flag: RosterFlag, raiser) -> None:
    """Put the decision on Unami's dashboard (CFO 2026-07-26: "then it goes to
    Unami to make a decision"). Never let a notification failure lose the flag —
    the record is already saved by the time we get here."""
    from django.db import transaction
    try:
        from core.models import OmniTask
        from django.contrib.auth.models import User as _User
        from django.db.models import Q as _Q
        _q = _Q()
        for _e in RosterFlag.DECIDER_EMAILS:
            _q |= _Q(email__iexact=_e)
        targets = list(_User.objects.filter(_q, is_active=True))
        if not targets:
            target = decider_user()
            if target is None:
                return
            targets = [target]
        emp = flag.profile.employee
        mgr = flag.profile.manager.full_name if flag.profile.manager else 'nobody'
        who = getattr(raiser, 'email', None) or 'A manager'
        lines = [
            f'{who} has flagged {emp.full_name} on their team.',
            '',
            f'Reason: {flag.get_kind_display()}',
            f'Their note: {flag.note or "(none)"}',
            f'Currently recorded as reporting to: {mgr}',
            f'Job title on record: {emp.job_title or "(blank)"}',
            '',
            f'{emp.full_name} STAYS on that roster until you decide — a manager '
            'cannot make someone vanish from their own accountability.',
            '',
            'Open Roster Flags in HRIS to accept or reject. Accepting records your '
            'decision; the reporting line or status is then changed through the '
            'normal dual-approved HR amendment, so it stays attributable.',
        ]
        # OmniTask.source is varchar(30): 'roster_flag:<uuid>' is 48 and silently
        # blew up here, so the flag saved and Unami was NEVER told. Keep it short.
        source = f'rflag:{flag.id.hex[:20]}'          # 26 chars
        assert len(source) <= 30
        # Own savepoint: a notification problem must not poison the request's
        # transaction and take the flag itself down with it.
        with transaction.atomic():
            for target in targets:
                OmniTask.objects.create(
                    assigner=raiser if getattr(raiser, 'pk', None) else target,
                    assignee=target,
                    title=f'Decide: {emp.full_name} — {flag.get_kind_display()}'[:200],
                    body='\n'.join(lines),
                    priority=OmniTask.Priority.HIGH,
                    status=OmniTask.Status.PENDING,
                    source=source)
    except Exception:          # noqa: BLE001 - notification must never lose a flag
        import logging
        logging.getLogger(__name__).exception('roster flag: could not notify decider')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide(request, pk):
    """POST /hris/api/roster-flags/<id>/decide/ {action: action|reject, hr_note}

    HR only. `action` records that HR has dealt with it — it does NOT itself move
    the reporting line or terminate anyone; HR makes that change through the normal
    dual-approved amendment flow, so the change is attributable and reversible.
    """
    off = _feature_off()
    if off:
        return off
    if not can_decide(request.user):
        return Response(
            {'detail': 'Only HR (Unami Butale or Dorothy Ikgopoleng) decides '
                       'roster flags.'}, status=403)

    flag = (RosterFlag.objects.filter(pk=pk)
            .select_related('profile__employee').first())
    if flag is None:
        return Response({'detail': 'Flag not found.'}, status=404)
    if not apply_company_scope(
            request, HRISProfile.objects.filter(pk=flag.profile_id),
            'employee__company_id').exists():
        return Response({'detail': 'Not available for your entity.'}, status=403)
    if flag.status != FlagStatus.OPEN:
        return Response({'detail': 'This flag has already been dealt with.'}, status=409)

    action = (request.data or {}).get('action') or 'action'
    note = ((request.data or {}).get('hr_note') or '').strip()
    if action == 'reject' and not note:
        return Response({'detail': 'Say why you are not accepting it.'}, status=400)

    flag.status = FlagStatus.ACTIONED if action == 'action' else FlagStatus.REJECTED
    flag.hr_note = note
    flag.actioned_by = request.user
    flag.actioned_at = timezone.now()
    flag.save(update_fields=['status', 'hr_note', 'actioned_by', 'actioned_at',
                             'updated_at'])
    return Response(_serialize(flag))
