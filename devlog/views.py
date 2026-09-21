"""devlog/views.py — the build log API. The CFO's own account, nothing else."""
from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsTheCfo, is_the_cfo
from devlog.models import DevItem

MAX_TEXT = 4000


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTheCfo])
def build_log_day(request):
    """GET /api/v1/cfo/build-log/?date=YYYY-MM-DD — the end-of-day read."""
    import datetime as dt

    from devlog.day_report import day, today_local

    qp = request.query_params
    raw = (qp.get('date') or '').strip()
    try:
        d = dt.date.fromisoformat(raw) if raw else today_local()
    except ValueError:      # junk ?date= falls back to today rather than 500
        d = today_local()
    return Response(day(
        d,
        who=(qp.get('who') or '').strip()[:120],
        area=(qp.get('area') or '').strip()[:64],
        source=(qp.get('source') or '').strip()[:24],
        only_bugs=(qp.get('bugs') or '') in ('1', 'true', 'yes'),
        q=(qp.get('q') or '').strip()[:120],
        stage=(qp.get('stage') or '').strip()[:10],
    ))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def record_item(request):
    """POST /api/v1/cfo/build-log/item/ — a skill recording what was asked.

    NOT gated to the CFO's own login: the caller is /goal, /code, /lane-b or
    /fabe running under whichever account the machine is signed in as, and the
    row it writes is about the CFO's request rather than the caller. Reading
    the log stays his alone; writing to it is an authenticated staff action.

    Idempotent on `client_key` so the same ask logged twice — two skills in one
    session, or a retry — is one row, not two.
    """
    key = (request.data.get('client_key') or '').strip()[:120]
    asked = (request.data.get('asked_text') or '').strip()[:MAX_TEXT]
    if not asked:
        return Response({'detail': 'asked_text is required — his words, verbatim.'},
                        status=status.HTTP_400_BAD_REQUEST)

    defaults = {
        'asked_text': asked,
        'title': (request.data.get('title') or '').strip()[:140],
        'asked_at': timezone.now(),
        'machine': (request.data.get('machine') or '').strip()[:16],
        'source': (request.data.get('source') or '').strip()[:24],
        'session_ref': (request.data.get('session_ref') or '').strip()[:64],
        'area': (request.data.get('area') or '').strip()[:64],
        'success_criteria': (request.data.get('success_criteria') or '')[:MAX_TEXT],
        'requested_by_name': (request.data.get('requested_by_name') or '').strip()[:120],
    }
    # Link the requester to a real login when one is given, but NEVER match on a
    # name — a wrong match credits the request to the wrong person.
    who = (request.data.get('requested_by_email') or '').strip()
    if who:
        from django.contrib.auth.models import User
        u = User.objects.filter(email__iexact=who).first()
        if u is not None:
            defaults['requested_by'] = u
        elif not defaults['requested_by_name']:
            defaults['requested_by_name'] = who
    bug_ref = (request.data.get('bug_id') or '').strip()
    if bug_ref:
        from core.models import BugReport
        b = BugReport.objects.filter(id=bug_ref).first()
        if b is not None:
            defaults['bug'] = b
    if key:
        item, created = DevItem.objects.get_or_create(client_key=key, defaults=defaults)
    else:
        item, created = DevItem.objects.create(**defaults), True
    return Response({'id': str(item.id), 'created': created, 'status': item.status},
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def set_item_status(request):
    """POST /api/v1/cfo/build-log/status/ {client_key|id, status, notes}."""
    key = (request.data.get('client_key') or '').strip()
    ident = (request.data.get('id') or '').strip()
    new = (request.data.get('status') or '').strip()
    if new not in {c for c, _ in DevItem.Status.choices}:
        return Response({'detail': f'status must be one of '
                                   f'{[c for c, _ in DevItem.Status.choices]}'},
                        status=status.HTTP_400_BAD_REQUEST)
    item = (DevItem.objects.filter(client_key=key).first() if key
            else DevItem.objects.filter(id=ident).first() if ident else None)
    if item is None:
        return Response({'detail': 'No such build item.'},
                        status=status.HTTP_404_NOT_FOUND)

    item.status = new
    # Only the deploy record may stamp live_at — a skill saying "live" without a
    # deploy behind it is exactly the claim this dashboard exists to stop.
    if new == DevItem.Status.LIVE and item.live_at is None and item.deploy_id:
        item.live_at = timezone.now()
    if request.data.get('notes'):
        item.notes = str(request.data['notes'])[:MAX_TEXT]
    item.save(update_fields=['status', 'live_at', 'notes', 'updated_at'])
    return Response({'id': str(item.id), 'status': item.status,
                     'live_at': item.live_at})


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTheCfo])
def confirm_done(request):
    """POST /api/v1/cfo/build-log/confirm/ {id, answer: yes|no} — his answer
    to the looks-done check. Yes is the CFO's own word that it is live, so it
    stamps live_at now; no keeps it open and stops it being asked again."""
    item = DevItem.objects.filter(id=(request.data.get('id') or '').strip() or None).first()
    if item is None:
        return Response({'detail': 'No such build item.'}, status=status.HTTP_404_NOT_FOUND)
    answer = (request.data.get('answer') or '').strip().lower()
    if answer == 'yes':
        item.status = DevItem.Status.LIVE
        item.live_at = item.live_at or timezone.now()
        item.save(update_fields=['status', 'live_at', 'updated_at'])
    elif answer == 'no':
        item.confirm_declined_at = timezone.now()
        item.save(update_fields=['confirm_declined_at', 'updated_at'])
    else:
        return Response({'detail': 'answer must be yes or no'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'id': str(item.id), 'status': item.status, 'live_at': item.live_at})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def whoami_cfo(request):
    """GET /api/v1/cfo/whoami/ — what the menu needs to hide the CFO screens.

    is_the_cfo gates the build log and the job switches (his alone); the
    forgiveness watch also opens for the CEO and HR (CFO 2026-09-09).
    """
    from hris.document_access import is_hr_doc_admin
    return Response({
        'is_the_cfo': is_the_cfo(request.user),
        'can_see_forgiveness': is_hr_doc_admin(request.user),
    })
