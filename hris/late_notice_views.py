"""
hris/late_notice_views.py — the "I'm running late" button (CFO 2026-09-09).

Rule 1b of the review engine. Before this existed the only way to explain a
morning was the NEXT day's shortfall page, so the honest answer and the
after-the-fact excuse looked identical on the record.

  GET  /hris/api/late-notice/    what today's notice says, and whether the
                                 window is still open
  POST /hris/api/late-notice/    file it — today only, before 09:00 Botswana

Both limits are enforced HERE, on the server. A front-end that hides the button
after 09:00 is a courtesy; a person with the API call is not stopped by it.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.feature_views import _profile_for
from hris.late_notice_models import NOTICE_CUTOFF, LateNotice, local_now

MAX_REASON = 500


def _today_and_open():
    """(Botswana date now, is the window still open)."""
    now_local = local_now()
    return now_local.date(), now_local.time() < NOTICE_CUTOFF


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def late_notice(request):
    profile = _profile_for(request.user)
    if profile is None:
        # No staff record behind the login. Never guess by name — a wrong match
        # here forgives the wrong person's morning (f-never-namematch-td).
        return Response({'detail': 'No staff record is linked to this login.'},
                        status=status.HTTP_403_FORBIDDEN)

    today, window_open = _today_and_open()
    existing = LateNotice.objects.filter(profile=profile, notice_date=today).first()

    if request.method == 'GET':
        return Response({
            'date': today,
            'window_open': window_open,
            'cutoff': NOTICE_CUTOFF.strftime('%H:%M'),
            'notice': ({'kind': existing.kind, 'reason': existing.reason,
                        'in_time': existing.in_time,
                        'filed_at': existing.filed_local_time}
                       if existing else None),
        })

    if existing:
        # One per morning. Re-filing would let somebody replace a rejected
        # out-of-time notice with a fresh one the next day.
        return Response({'detail': 'You have already told us about this morning.'},
                        status=status.HTTP_409_CONFLICT)

    kind = (request.data.get('kind') or LateNotice.Kind.LATE).strip()
    if kind not in {c for c, _ in LateNotice.Kind.choices}:
        return Response({'detail': 'kind must be one of late / sick / client / other.'},
                        status=status.HTTP_400_BAD_REQUEST)
    reason = str(request.data.get('reason') or '').strip()[:MAX_REASON]

    now_local = local_now()
    notice = LateNotice.objects.create(
        profile=profile, notice_date=today, kind=kind, reason=reason,
        filed_local_time=now_local.time().replace(microsecond=0),
        in_time=window_open,
    )
    return Response({
        'ok': True,
        'in_time': notice.in_time,
        # Say plainly whether it counted. A silent "saved" on a 09:40 notice is
        # how somebody finds out a month later that it never forgave anything.
        'detail': ('Noted — your manager can see it and this morning will not '
                   'count against you.'
                   if notice.in_time else
                   'Recorded, but filed after 09:00 — this morning still counts. '
                   'Tell us before 09:00 next time.'),
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def late_notice_pattern(request):
    """GET /hris/api/late-notice/pattern/?months=3 — the forgiveness dashboard.

    Management-only, behind the same gate the rest of the HR reporting uses.
    A watching report: it deducts nothing and decides nothing, it just makes the
    pattern visible before November (CFO 2026-09-09).
    """
    from hris.document_access import is_hr_doc_admin
    if not is_hr_doc_admin(request.user):
        return Response({'detail': 'Attendance patterns are management-only.'},
                        status=status.HTTP_403_FORBIDDEN)
    try:
        months = max(1, min(int(request.query_params.get('months') or 3), 12))
    except (TypeError, ValueError):        # junk ?months= falls back, never 500s
        months = 3

    from hris.late_notice_report import collect, narrative
    report = collect(months=months)
    # ?ai=0 renders the table without waiting on a model.
    if (request.query_params.get('ai') or '1') != '0':
        report['narrative'] = narrative(report)
    return Response(report)
