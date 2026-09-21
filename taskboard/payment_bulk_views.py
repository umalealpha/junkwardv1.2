"""Authorise every payment pack that is clean, in one action (CFO 2026-08-04).

The CFO had four packs waiting and asked to have them approved for him. That is
the one thing that cannot be delegated: completing a payment task IS the release
of the money, and the two-stage control only means something if a person with
authority performs the second stage. So instead of signing on his behalf, this
makes his own signature one press.

Nothing is loosened:

  * The authorisation is still the CFO's. This endpoint refuses everybody else.
  * Each pack goes through services.complete_task, exactly the path a single
    approval takes, so PAY-DUP-01 fires per pack. A pack that has gone dirty
    since the preview is skipped, never forced.
  * The client is not trusted for the list or for the money. The caller echoes
    back the total it displayed; if the server's own recount differs by a thebe
    the whole run is refused, so a pack that changed between reading and
    pressing can never be swept along unseen.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import OmniTask

from . import services
from .models import PaymentRequest
from .payment_duplicates import find_duplicates, hard_total
from .payment_views import _cfo_user

NOTE = ('Authorised by the CFO from the bulk approval screen. Every line was checked '
        'against the duplicate control at the moment of authorisation.')


def _is_cfo(user) -> bool:
    cfo = _cfo_user()
    return bool(getattr(user, 'is_superuser', False) or (cfo and user.id == cfo.id))


def _queue(categories=None):
    """(ready, blocked) for every pack awaiting CFO authorisation that still has
    a live task. A pack whose task is already done or cancelled is not payable
    from here, so it is left out of both lists rather than counted as ready.

    `categories` (optional list) scopes the queue to one category tab so the CFO
    can approve all the clean CLAIM packs (or supplier, or refunds) in one press
    (CFO 2026-08-31). The server still enumerates the packs itself — the client
    only names the category — so the 'client is not trusted for the list' rule
    holds and confirm_total still guards the money."""
    ready, blocked = [], []
    qs = (PaymentRequest.objects
          .filter(status=PaymentRequest.Status.PENDING_CFO)
          .select_related('task', 'first_approver')
          .order_by('created_at'))
    if categories:
        qs = qs.filter(category__in=list(categories))
    for p in qs:
        task = p.task
        if task is None or task.status in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
            continue
        dup = find_duplicates(p.line_items or [], currency=p.currency, exclude_pk=p.pk,
                              payee=p.payee or '')
        row = {
            'id': str(p.id),
            'ref': p.ref,
            'entity': p.entity,
            'subject': p.subject,
            'currency': p.currency,
            'total': str(p.total),
            'lines': len(p.line_items or []),
            'task_id': str(task.id),
            'signed_off_by': (p.first_approver.get_full_name() or p.first_approver.username)
                             if p.first_approver else '',
            'edited_after_signoff': bool((p.decision_notes or '').strip()),
        }
        if dup['hard']:
            row['clashes'] = len(dup['hard'])
            row['clash_total'] = str(hard_total(dup['hard']))
            row['clash_detail'] = [h.get('detail') for h in dup['hard'][:6]]
            blocked.append(row)
        else:
            ready.append(row)
    return ready, blocked


def _sum(rows) -> Decimal:
    return sum((Decimal(r['total']) for r in rows), Decimal('0'))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_bulk_preview(request):
    if not _is_cfo(request.user):
        return Response({'detail': 'Only the CFO can authorise payments.'},
                        status=http.HTTP_403_FORBIDDEN)
    cats = [c for c in (request.query_params.get('categories') or '').split(',') if c]
    ready, blocked = _queue(cats or None)
    return Response({
        'ready': ready,
        'blocked': blocked,
        'ready_total': str(_sum(ready)),
        'ready_count': len(ready),
        'blocked_count': len(blocked),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payment_bulk_approve(request):
    """POST {confirm_total} to authorise every clean pack.

    confirm_total is the figure the CFO was actually shown. It is not a
    formality: it is the only thing tying what he read to what he releases.
    """
    if not _is_cfo(request.user):
        return Response({'detail': 'Only the CFO can authorise payments.'},
                        status=http.HTTP_403_FORBIDDEN)

    body = request.data or {}
    cats = body.get('categories') or None
    if cats is not None and not isinstance(cats, list):
        cats = None
    ready, blocked = _queue(cats)
    if not ready:
        return Response({'detail': 'Nothing is ready to authorise.',
                         'blocked_count': len(blocked)},
                        status=http.HTTP_409_CONFLICT)

    server_total = _sum(ready)
    raw = (request.data or {}).get('confirm_total')
    if raw in (None, ''):
        return Response({'detail': 'confirm_total is required. Echo back the total you were shown.'},
                        status=http.HTTP_400_BAD_REQUEST)
    try:
        shown = Decimal(str(raw))
    except (TypeError, ValueError, ArithmeticError):
        return Response({'detail': 'confirm_total must be a number.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if shown != server_total:
        return Response({
            'detail': (f'The queue changed while you were reading it. You were shown '
                       f'{shown}, it is now {server_total}. Nothing was authorised. '
                       f'Open it again and check before you press.'),
            'ready_total': str(server_total),
            'ready_count': len(ready),
        }, status=http.HTTP_409_CONFLICT)

    paid, refused = [], []
    for row in ready:
        task = OmniTask.objects.filter(pk=row['task_id']).first()
        if task is None:
            refused.append({**row, 'reason': 'the task disappeared'})
            continue
        try:
            # The single-approval path, per pack. PAY-DUP-01 fires inside it.
            services.complete_task(task, request.user, NOTE, 0)
        except DjangoValidationError as e:
            msg = e.messages[0] if getattr(e, 'messages', None) else str(e)
            refused.append({**row, 'reason': msg})
            continue
        paid.append(row)

    return Response({
        'authorised': paid,
        'authorised_count': len(paid),
        'authorised_total': str(_sum(paid)),
        'refused': refused,
        'refused_count': len(refused),
        'still_blocked': blocked,
    })
