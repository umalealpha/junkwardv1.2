"""
bonu/member_views.py — the membership roll on screen.

Four things the BONU team needs: see who is on the roll, load the union's monthly list,
check one member before a claim is paid, and correct a single record by hand when the union
sends a correction by email instead of a new file.

Access is the standard BONU gate (`bonu.views._deny`), which already admits the BONU team
lead by name — Patience runs this and her title is `operations`, not finance.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bonu import members as roll
from bonu.models import BonuMember, BonuMemberListLoad
from bonu.views import _deny
from django.utils import timezone

logger = logging.getLogger(__name__)

PAGE = 100


def _row(m):
    return {
        'id': str(m.pk),
        'membership_no': m.membership_no,
        'full_name': m.full_name,
        'station': m.station,
        'district': m.district,
        'status': m.status,
        'status_display': m.get_status_display(),
        'monthly_premium': str(m.monthly_premium) if m.monthly_premium is not None else None,
        'paid_up_to': m.paid_up_to.isoformat() if m.paid_up_to else None,
        'joined_on': m.joined_on.isoformat() if m.joined_on else None,
        'left_on': m.left_on.isoformat() if m.left_on else None,
        'on_current_list': m.is_on_current_list,
        'last_seen_as_at': m.last_seen.as_at.isoformat() if m.last_seen_id else None,
        'note': m.note,
    }


def _date(raw, default=None):
    """YYYY-MM-DD, `default` when blank, or None meaning "given but unreadable".

    The None is not a swallow: every caller turns it into a 400 with the field
    named, so a mistyped date is refused rather than quietly ignored.
    """
    s = (raw or '').strip()
    if not s:
        return default
    try:
        return dt.datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError: return None    # "given but unreadable" — every caller 400s on this


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def member_list(request):
    """GET /api/v1/bonu/members/?q=&status=&district=&page= — the roll."""
    denied = _deny(request)
    if denied is not None:
        return denied

    qs = BonuMember.objects.select_related('last_seen').all()
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(membership_no__icontains=q) | Q(full_name__icontains=q)
                       | Q(station__icontains=q) | Q(district__icontains=q))
    st = (request.query_params.get('status') or '').strip()
    if st:
        qs = qs.filter(status=st)
    district = (request.query_params.get('district') or '').strip()
    if district:
        qs = qs.filter(district__iexact=district)
    if (request.query_params.get('on_current_list') or '') == 'no':
        lst = roll.current_list()
        qs = qs.exclude(last_seen=lst) if lst else qs

    total = qs.count()
    # A junk ?page= falls back to the first page rather than 500ing. Paging is a
    # view preference, not data, so there is nothing here worth failing over.
    raw_page = (request.query_params.get('page') or '1').strip()
    page = max(1, int(raw_page)) if raw_page.isdigit() else 1
    start = (page - 1) * PAGE
    return Response({
        'summary': roll.summary(),
        'total': total,
        'page': page,
        'page_size': PAGE,
        'rows': [_row(m) for m in qs[start:start + PAGE]],
        'loads': [{'as_at': l.as_at.isoformat(), 'source_name': l.source_name,
                   'rows_seen': l.rows_seen, 'members_loaded': l.members_loaded,
                   'is_current': l.is_current, 'loaded_by': l.loaded_by_email}
                  for l in BonuMemberListLoad.objects.all()[:12]],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def member_check(request):
    """GET /api/v1/bonu/members/check/?membership_no=&on_date= — may we pay?

    Returns allow / refuse / unknown with the reason. It ADVISES; it does not stop a
    payment. See the module note in bonu/members.py for why that is deliberate.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    on_date = _date(request.query_params.get('on_date'), timezone.localdate())
    if on_date is None:
        return Response({'detail': 'on_date must be YYYY-MM-DD.'},
                        status=status.HTTP_400_BAD_REQUEST)
    verdict = roll.check(request.query_params.get('membership_no') or '', on_date=on_date)
    member = verdict.pop('member', None)
    verdict['member'] = _row(member) if member is not None else None
    verdict['on_date'] = on_date.isoformat()
    if verdict['spend_this_year'] is not None:
        verdict['spend_this_year'] = str(verdict['spend_this_year'])
    return Response(verdict)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def member_upload(request):
    """POST /api/v1/bonu/members/upload/ — the union's monthly list.

    `as_at` is required and is NOT taken from the filename or from today's date: the
    whole register hangs off which list is current, and a guessed date would silently
    make an old list authoritative.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    upload = request.FILES.get('file')
    if upload is None:
        return Response({'detail': 'Attach the union\'s membership list.'},
                        status=status.HTTP_400_BAD_REQUEST)
    as_at = _date(request.data.get('as_at'))
    if as_at is None:
        return Response({'detail': 'Give as_at (YYYY-MM-DD) — the date the union\'s list '
                                   'speaks as of. It is not guessed.'},
                        status=status.HTTP_400_BAD_REQUEST)

    import os
    import tempfile
    suffix = os.path.splitext(upload.name)[1] or '.xlsx'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()
        load = roll.load_workbook(tmp.name, as_at=as_at, source_name=upload.name,
                                  user=request.user)
    # A bad column map or an empty sheet is the user's file, not a server fault —
    # say which column was missing so they can fix it themselves.
    except ValueError as e: return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    finally:
        try:
            os.unlink(tmp.name)
        # Cleanup only — never fail an accepted load over a leftover temp file,
        # but say so rather than dropping it.
        except OSError: logger.warning('could not remove temp upload %s', tmp.name)

    warnings = getattr(load, 'read_warnings', [])
    return Response({'as_at': load.as_at.isoformat(), 'rows_seen': load.rows_seen,
                     'members_loaded': load.members_loaded, 'is_current': load.is_current,
                     'unreadable_values': len(warnings), 'warnings': warnings[:20],
                     'summary': roll.summary()}, status=status.HTTP_201_CREATED)


@api_view(['POST', 'PATCH'])
@permission_classes([IsAuthenticated])
def member_edit(request, member_id=None):
    """Add or correct ONE member by hand, for the emailed correction.

    Deliberately does not touch `last_seen`: a hand edit is not the union publishing a
    list, and letting it pose as one would make a refusal unprovable.
    """
    denied = _deny(request)
    if denied is not None:
        return denied

    if member_id is None:
        ref = (request.data.get('membership_no') or '').strip()
        if not ref:
            return Response({'detail': 'membership_no is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if BonuMember.objects.filter(membership_no__iexact=ref).exists():
            return Response({'detail': f'{ref} is already on the roll.'},
                            status=status.HTTP_409_CONFLICT)
        member = BonuMember(membership_no=ref)
    else:
        member = BonuMember.objects.filter(pk=member_id).first()
        if member is None:
            return Response({'detail': 'No such member.'}, status=status.HTTP_404_NOT_FOUND)

    for field in ('full_name', 'station', 'district', 'note'):
        if field in request.data:
            setattr(member, field, (request.data.get(field) or '')[:200])
    if 'status' in request.data:
        want = (request.data.get('status') or '').strip()
        if want not in dict(BonuMember.Status.choices):
            return Response({'detail': f'Unknown status "{want}".'},
                            status=status.HTTP_400_BAD_REQUEST)
        member.status = want
    for field in ('paid_up_to', 'joined_on', 'left_on'):
        if field in request.data:
            raw = request.data.get(field)
            if raw in (None, ''):
                setattr(member, field, None)
            else:
                got = _date(str(raw))
                if got is None:
                    return Response({'detail': f'{field} must be YYYY-MM-DD.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                setattr(member, field, got)
    if 'monthly_premium' in request.data:
        member.monthly_premium = roll.read_money(str(request.data.get('monthly_premium') or ''))

    member.updated_by_email = (getattr(request.user, 'email', '') or '')[:200]
    member.save(audit_user=request.user)
    return Response(_row(member),
                    status=status.HTTP_201_CREATED if member_id is None else status.HTTP_200_OK)
