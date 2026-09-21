"""Large Payment Authorisation — REST endpoints (/api/v1/large-payments/...).

Piece 1 of the CFO's 2026-09-11 design: the button, the selection box, the live
progress while Graphite is read, and the request landing on the CFO as a task.
The CEO email and its question / reject / approve-all buttons are Piece 2 and are
deliberately not here — the part where money is released on a link click gets its
own build and its own test rather than riding along with this one.

Nothing in this file writes to a payment. The only models it creates are this
app's own.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from taskboard.models import PaymentRequest

from .enrich import enrich_in_background
from .models import LargePaymentLine, LargePaymentRequest, default_threshold
from .permissions import can_decide, can_raise
from .selection import candidates, claim_number_for, payments_already_requested

log = logging.getLogger(__name__)

#: How long a request may sit at the same percentage before it counts as stopped
#: rather than working. Below this a retry is refused (it would start a second
#: thread beside a live one); above it the screen offers a way out instead of
#: spinning for ever — the two halves of the same rule, in one number.
STALL_SECONDS = 60


def _close_cfo_task(req) -> None:
    """Close the CFO's task once he has decided. Best effort, never fatal."""
    from core.models import OmniTask
    task = req.cfo_task
    if not task or task.status in (OmniTask.Status.DONE, OmniTask.Status.CANCELLED):
        return
    try:
        task.status = OmniTask.Status.DONE
        task.completed_at = timezone.now()
        task.save(update_fields=['status', 'completed_at', 'updated_at'])
    except Exception:                                     # noqa: BLE001
        log.exception('large_payments: could not close the CFO task on %s', req.ref)


def _deny_raise():
    return Response(
        {'detail': 'Only the Finance approvers and the CFO can raise a large '
                   'payment request.'},
        status=status.HTTP_403_FORBIDDEN)


def _deny_decide():
    return Response({'detail': 'Only the CFO can approve or reject this request.'},
                    status=status.HTTP_403_FORBIDDEN)


def _next_ref() -> str:
    """LPR/2026/09/11/0001 — readable, sortable, unique per day."""
    today = timezone.localtime().date()
    prefix = f'LPR/{today:%Y/%m/%d}/'
    n = LargePaymentRequest.objects.filter(ref__startswith=prefix).count() + 1
    # Count can collide if two are raised at the same instant; the unique
    # constraint is the real guard and the caller retries.
    return f'{prefix}{n:04d}'


def _line_json(ln: LargePaymentLine) -> dict:
    return {
        'id':               ln.id,
        'payment_request_id': str(ln.payment_request_id),
        'payment_ref':      ln.payment_ref,
        'claim_number':     ln.claim_number,
        'payee':            ln.payee,
        'amount':           str(ln.amount),
        'enrich_status':    ln.enrich_status,
        'insured_name':     ln.insured_name,
        'policy_number':    ln.policy_number,
        'claim_status':     ln.claim_status,
        'claim_sub_status': ln.claim_sub_status,
        'date_of_loss':     ln.date_of_loss.isoformat() if ln.date_of_loss else None,
        'loss_description': ln.loss_description,
        'reserve_amount':   str(ln.reserve_amount) if ln.reserve_amount is not None else None,
        'paid_amount':      str(ln.paid_amount) if ln.paid_amount is not None else None,
        'flags':            ln.flags or [],
    }


def _request_json(req: LargePaymentRequest, *, with_lines: bool = False) -> dict:
    # One walk over the prefetched lines, not a count() and a second walk.
    _lines = list(req.lines.all())
    out = {
        'id':            str(req.id),
        'ref':           req.ref,
        'title':         req.title,
        'status':        req.status,
        'status_label':  req.get_status_display(),
        'threshold':     str(req.threshold),
        'total':         str(req.total),
        'progress_pct':  req.progress_pct,
        'progress_note': req.progress_note,
        'enrich_error':  req.enrich_error,
        'line_count':    len(_lines),
        'flagged_count': sum(1 for ln in _lines if ln.flags),
        'raised_by':     req.raised_by.get_full_name() or req.raised_by.username,
        'created_at':    req.created_at.isoformat(),
        # So the screen can tell a run that is working from one that has stopped,
        # and offer Try again rather than spin for ever.
        'updated_at':    req.updated_at.isoformat(),
        'stalled':       (req.status == LargePaymentRequest.Status.ENRICHING
                          and (timezone.now() - req.updated_at).total_seconds()
                              >= STALL_SECONDS),
        'decided_at':    req.decided_at.isoformat() if req.decided_at else None,
        'decision_note': req.decision_note,
        'cfo_task_id':   str(req.cfo_task_id) if req.cfo_task_id else None,
        # Piece 2 — without these the screen can never say WHY a send failed or
        # WHO the CEO asked; api.ts declared them and the page read them, but the
        # server never sent them (Fable 5.1, 2026-09-12).
        'ceo_task_id':     str(req.ceo_task_id) if req.ceo_task_id else None,
        'sent_to_ceo_at':  req.sent_to_ceo_at.isoformat() if req.sent_to_ceo_at else None,
        'sent_recipients': list(req.sent_recipients or []),
        'send_error':      req.send_error,
        'ceo_decided_at':  req.ceo_decided_at.isoformat() if req.ceo_decided_at else None,
        'ceo_questions':   list(req.ceo_questions or []),
    }
    if with_lines:
        out['lines'] = [_line_json(ln) for ln in _lines]
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lp_candidates(request):
    """The claim payments that could go on a request, largest first.

    Everything eligible is returned; `over_threshold` says which ones are ticked
    by default. Smaller ones are shown but unticked so the CFO can add one by
    hand — hiding them would make his own instruction impossible to follow.
    """
    if not can_raise(request.user):
        return _deny_raise()

    raw = request.query_params.get('threshold')
    cut = None
    if raw:
        try:
            cut = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return Response({'detail': f'"{raw}" is not a valid amount.'},
                            status=status.HTTP_400_BAD_REQUEST)

    rows, used = candidates(cut)
    return Response({
        'threshold': str(used),
        'count':     len(rows),
        'ticked':    sum(1 for r in rows if r['over_threshold']),
        'rows':      rows,
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def lp_list_create(request):
    if request.method == 'GET':
        if not can_raise(request.user):
            return _deny_raise()
        # prefetch, not two queries per row: line_count and flagged_count each
        # walked the lines, so a hundred requests was a couple of hundred
        # queries for one page (Fable 5.1, 2026-09-11).
        qs = (LargePaymentRequest.objects
              .select_related('raised_by')
              .prefetch_related('lines')[:100])
        return Response({'results': [_request_json(r) for r in qs]})

    if not can_raise(request.user):
        return _deny_raise()

    ids = request.data.get('payment_request_ids') or []
    if not isinstance(ids, list) or not ids:
        return Response({'detail': 'Tick at least one payment first.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # De-duplicate what the client sent before anything is created. A double-tap
    # must not produce two lines for one payment.
    ids = list(dict.fromkeys(str(i) for i in ids))

    payments = list(PaymentRequest.objects.filter(id__in=ids))
    found = {str(p.id) for p in payments}
    missing = [i for i in ids if i not in found]
    if missing:
        return Response(
            {'detail': f'{len(missing)} of the payments you ticked no longer exist. '
                       'Refresh the list and try again.'},
            status=status.HTTP_400_BAD_REQUEST)

    # A payment already held on a live request cannot be put on a second one.
    # This is the whole reason the module exists, so it is enforced on the
    # server and not merely hidden in the list the client was shown.
    held = payments_already_requested()
    clash = [p for p in payments if p.id in held]
    if clash:
        return Response(
            {'detail': 'These payments are already on another large payment '
                       'request: ' + ', '.join(p.ref for p in clash[:5])
                       + ('…' if len(clash) > 5 else '')},
            status=status.HTTP_409_CONFLICT)

    total = sum((p.total for p in payments), Decimal('0.00'))
    title = str(request.data.get('title') or '').strip()[:200]
    if not title:
        # Built from the parts, not a strftime format: '%-d' (no leading zero) is
        # a glibc extension and raises on Windows, where this repo's tests run.
        now = timezone.localtime()
        title = f'Claims Payments {now.day} {now:%B %Y}'

    for attempt in range(5):
        try:
            with transaction.atomic():
                req = LargePaymentRequest.objects.create(
                    ref=_next_ref(),
                    title=title,
                    raised_by=request.user,
                    status=LargePaymentRequest.Status.ENRICHING,
                    threshold=default_threshold(),
                    total=total,
                    progress_pct=0,
                    progress_note='Starting…',
                )
                LargePaymentLine.objects.bulk_create([
                    LargePaymentLine(
                        request=req,
                        payment_request=p,
                        payment_ref=p.ref,
                        payee=(p.payee or '')[:200],
                        amount=p.total,
                        claim_number=claim_number_for(p)[:40],
                    ) for p in payments
                ])
            break
        except IntegrityError:
            if attempt == 4:
                raise
            continue

    enrich_in_background(req.id)
    return Response(_request_json(req), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lp_detail(request, pk):
    """The request, its lines, and — while it is still collecting — its progress.

    This is what the screen polls for the 10%/20%/60% the CFO asked for.
    """
    if not (can_raise(request.user) or can_decide(request.user)):
        return _deny_raise()
    try:
        req = LargePaymentRequest.objects.select_related('raised_by').get(pk=pk)
    except LargePaymentRequest.DoesNotExist:
        return Response({'detail': 'No such request.'},
                        status=status.HTTP_404_NOT_FOUND)
    return Response(_request_json(req, with_lines=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def lp_retry_enrich(request, pk):
    """Try the Graphite pull again after it failed or was interrupted."""
    if not can_raise(request.user):
        return _deny_raise()
    try:
        req = LargePaymentRequest.objects.get(pk=pk)
    except LargePaymentRequest.DoesNotExist:
        return Response({'detail': 'No such request.'},
                        status=status.HTTP_404_NOT_FOUND)

    if req.status not in (LargePaymentRequest.Status.ENRICH_FAILED,
                          LargePaymentRequest.Status.ENRICHING):
        return Response(
            {'detail': 'The claim details for this request have already been '
                       'collected.'},
            status=status.HTTP_400_BAD_REQUEST)

    # Retrying a run that is still moving starts a SECOND thread beside a live
    # one: both write the same lines, the percentage walks backwards, and both
    # try to raise the CFO's task (Fable 5.1, 2026-09-11). A retry is for a run
    # that has stopped, so a run that wrote something in the last minute is left
    # alone. STALL_SECONDS is what "stopped" means, in one place.
    if req.status == LargePaymentRequest.Status.ENRICHING:
        age = (timezone.now() - req.updated_at).total_seconds()
        if age < STALL_SECONDS:
            return Response(
                {'detail': 'This request is still collecting. Give it a moment — '
                           'the Try again button is for a run that has stopped.'},
                status=status.HTTP_409_CONFLICT)

    req.status = LargePaymentRequest.Status.ENRICHING
    req.enrich_error = ''
    req.progress_pct = 0
    req.progress_note = 'Starting again…'
    req.save(update_fields=['status', 'enrich_error', 'progress_pct',
                            'progress_note', 'updated_at'])
    enrich_in_background(req.id)
    return Response(_request_json(req))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def lp_decide(request, pk):
    """The CFO approves or rejects.

    Approving records HIS authorisation. It sends nothing to the CEO yet — that
    is Piece 2 — and the response says so plainly, so nobody approves here
    believing Arun has been emailed.
    """
    if not can_decide(request.user):
        return _deny_decide()
    try:
        req = LargePaymentRequest.objects.get(pk=pk)
    except LargePaymentRequest.DoesNotExist:
        return Response({'detail': 'No such request.'},
                        status=status.HTTP_404_NOT_FOUND)

    decision = str(request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'reject'):
        return Response({'detail': 'Say approve or reject.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if req.status != LargePaymentRequest.Status.PENDING_CFO:
        return Response(
            {'detail': f'This request is "{req.get_status_display()}" and is not '
                       'waiting for a decision.'},
            status=status.HTTP_409_CONFLICT)

    note = str(request.data.get('note') or '').strip()
    if decision == 'reject' and not note:
        return Response({'detail': 'Say why you are rejecting it.'},
                        status=status.HTTP_400_BAD_REQUEST)

    req.status = (LargePaymentRequest.Status.APPROVED if decision == 'approve'
                  else LargePaymentRequest.Status.REJECTED)
    req.decided_by = request.user
    req.decided_at = timezone.now()
    req.decision_note = note
    req.save(update_fields=['status', 'decided_by', 'decided_at',
                            'decision_note', 'updated_at'])

    # His task is finished the moment he decides, either way. Leaving it open
    # means Omni chases him daily for work he has done — the "phantom task
    # pending" pattern that already reached 14 managers (Fable 5.1, 2026-09-12).
    _close_cfo_task(req)

    if decision == 'reject':
        out = _request_json(req)
        out['note_to_user'] = ('Recorded as rejected. Nothing was sent and no '
                               'payment was changed.')
        return Response(out)

    # ── Piece 2: the CFO's approval SENDS it to the CEO (CFO 2026-09-12) ─────
    # The send is the point of approving, so a send that fails must NOT read as a
    # success: the request is left APPROVED-but-not-sent with the reason on the
    # record and a Send again button, rather than sitting in a state that claims
    # Arun has it. "Accepted" is not "delivered", and neither is "approved".
    from .ceo_email import notify_claims_queries, send_to_ceo
    from .tasks import raise_ceo_task

    # The CEO's email says a query is raised with Claims and Finance. This is
    # what makes that sentence true. Raised BEFORE the send so it cannot be
    # skipped by a send failure.
    notify_claims_queries(req)

    ok, message = send_to_ceo(req)
    if not ok:
        req.send_error = message
        req.save(update_fields=['send_error', 'updated_at'])
        out = _request_json(req)
        out['note_to_user'] = (f'Your approval is recorded, but the email to Mr '
                               f'Iyer did NOT go out: {message} Press Send again.')
        # `detail` as well as note_to_user: the frontend's error path reads
        # `detail`, and without it the toast shows the whole request as raw JSON.
        out['detail'] = out['note_to_user']
        out['send_failed'] = True
        return Response(out, status=status.HTTP_502_BAD_GATEWAY)

    raise_ceo_task(req)
    req.refresh_from_db()
    out = _request_json(req)
    out['note_to_user'] = message
    return Response(out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def lp_resend(request, pk):
    """Send the authorisation to the CEO again after a failed or lost send.

    Allowed on an APPROVED request whose send failed, and on one already SENT (he
    deleted it, or it went to junk). NOT allowed once he has decided — re-sending
    an authorisation he has already answered invites a second answer.
    """
    if not can_decide(request.user):
        return _deny_decide()
    try:
        req = LargePaymentRequest.objects.get(pk=pk)
    except LargePaymentRequest.DoesNotExist:
        return Response({'detail': 'No such request.'},
                        status=status.HTTP_404_NOT_FOUND)

    if req.status not in (LargePaymentRequest.Status.APPROVED,
                          LargePaymentRequest.Status.SENT_TO_CEO,
                          LargePaymentRequest.Status.QUESTION):
        return Response(
            {'detail': f'This request is "{req.get_status_display()}" and cannot '
                       'be sent to the CEO again.'},
            status=status.HTTP_409_CONFLICT)

    from .ceo_email import send_to_ceo
    from .tasks import raise_ceo_task

    ok, message = send_to_ceo(req)
    if not ok:
        req.send_error = message
        req.save(update_fields=['send_error', 'updated_at'])
        return Response({'detail': message},
                        status=status.HTTP_502_BAD_GATEWAY)

    raise_ceo_task(req)
    req.refresh_from_db()
    out = _request_json(req)
    out['note_to_user'] = message
    return Response(out)
