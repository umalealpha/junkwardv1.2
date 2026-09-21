"""budgets/spend_views.py — pre-spend approval requests (CFO directive 2026-07-13).

  GET  /api/v1/spend-requests/                list (own, or all for approvers)
  POST /api/v1/spend-requests/                create + DeepSeek-analyse the budget
  GET  /api/v1/spend-requests/<id>/           detail
  POST /api/v1/spend-requests/<id>/decide/    approve|reject (CFO/EXCO)
  GET  /api/v1/spend-requests/<id>/file/      download the attached budget
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.models import SpendRequest
from budgets.spend_ai import analyze_spend_request


def _can_approve_spend(user) -> bool:
    """Pre-spend approval goes to the CFO / EXCO — these requests are emailed to
    the CFO for written approval today, so mirror that. Superuser backstop."""
    if getattr(user, "is_superuser", False):
        return True
    from core.models import UserProfile, get_user_profile
    p = get_user_profile(user)
    return bool(p and p.is_active and p.title == UserProfile.Title.CFO)


def _dec(v) -> Decimal:
    # NaN/Infinity parse as valid Decimals but .quantize() on Infinity raises,
    # so keep everything inside the try and normalise non-finite → 0.00 (which
    # callers reject via `amount <= 0`). Always 2dp so the create response
    # matches the DB value.
    #
    # The amount box is a free-text field, so tolerate how people actually type
    # money — "15,000", "P 15 000", "BWP15,000.00" all mean 15000.00. Strip
    # everything but digits, a decimal point and a leading minus before parsing,
    # so a valid amount is never rejected as "enter a valid amount" (CFO
    # 2026-08-24: a staff spend request could not be submitted).
    try:
        if v in (None, ""):
            return Decimal("0.00")
        s = re.sub(r"[^0-9.\-]", "", str(v).strip())
        d = Decimal(s) if s not in ("", "-", ".", "-.") else Decimal("0.00")
        if not d.is_finite():
            return Decimal("0.00")
        return d.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _serialize(sr, detail=False) -> dict:
    d = {
        "id": str(sr.id),
        "request_type": sr.request_type,
        "request_type_display": sr.get_request_type_display(),
        "title": sr.title,
        "amount": str(sr.amount),
        "event_date": sr.event_date.isoformat() if sr.event_date else None,
        "within_budget": sr.within_budget,
        "status": sr.status,
        "status_display": sr.get_status_display(),
        "requester": sr.requester.get_full_name() or sr.requester.username,
        "ai_status": sr.ai_status,
        "ai_summary": sr.ai_summary,
        "ai_extracted_total": str(sr.ai_extracted_total) if sr.ai_extracted_total is not None else None,
        "ai_flags": sr.ai_flags,
        "has_attachment": bool(sr.attachment),
        # Training / BQA levy recovery (only meaningful for training requests).
        "training_provider": sr.training_provider,
        "bqa_accredited": sr.bqa_accredited,
        "bqa_recovery_status": sr.bqa_recovery_status,
        "bqa_recovery_display": sr.get_bqa_recovery_status_display(),
        "levy_rate_pct": "0.5",   # statutory training levy = 0.5% of revenue incl VAT
        # Actual-spend loop (#4): what it really cost + variance vs the request.
        "actual_spent": str(sr.actual_spent) if sr.actual_spent is not None else None,
        "actual_reference": sr.actual_reference,
        "variance": (str((sr.actual_spent - sr.amount).quantize(Decimal("0.01")))
                     if sr.actual_spent is not None else None),
        "created_at": sr.created_at.isoformat(),
    }
    if detail:
        d.update({
            "description": sr.description,
            "budget_note": sr.budget_note,
            "approver": (sr.approver.get_full_name() or sr.approver.username) if sr.approver_id else "",
            "decided_at": sr.decided_at.isoformat() if sr.decided_at else None,
            "decision_notes": sr.decision_notes,
        })
    return d


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def spend_requests(request):
    if request.method == "GET":
        qs = (SpendRequest.objects.select_related("requester", "approver")
              .order_by("-created_at"))
        if not _can_approve_spend(request.user):
            qs = qs.filter(requester=request.user)   # requesters see only their own
        return Response({
            "can_approve": _can_approve_spend(request.user),
            "requests": [_serialize(s) for s in qs[:300]],
        })

    # POST — create the request, then DeepSeek-analyse the attached budget.
    d = request.data
    rtype = (d.get("request_type") or "event").strip()
    if rtype not in {t.value for t in SpendRequest.Type}:
        rtype = "other"
    # A mangled/blank amount must be rejected, not silently created as 0.00 in
    # the CFO queue (CFO 2026-07-14).
    amount = _dec(d.get("amount"))
    if not amount.is_finite() or amount <= 0:
        return Response({"detail": "Enter a valid amount greater than zero."},
                        status=http.HTTP_400_BAD_REQUEST)
    # Attachment cap — petty-cash/spend photos come off a phone camera.
    f = request.FILES.get("attachment") or request.FILES.get("budget") or request.FILES.get("file")
    if f is not None:
        if f.size > 10 * 1024 * 1024:
            return Response({"detail": "The attachment must be 10 MB or less."},
                            status=http.HTTP_400_BAD_REQUEST)
        ctype = (getattr(f, "content_type", "") or "").lower()
        if ctype and not (ctype.startswith("image/") or ctype == "application/pdf"):
            return Response({"detail": "The attachment must be a photo or PDF."},
                            status=http.HTTP_400_BAD_REQUEST)
    sr = SpendRequest(
        request_type=rtype,
        title=((d.get("title") or "").strip()[:200] or "Untitled request"),
        description=(d.get("description") or "").strip(),
        amount=amount,
        within_budget=str(d.get("within_budget")).lower() in ("true", "1", "yes", "on"),
        budget_note=(d.get("budget_note") or "").strip(),
        requester=request.user,
        status=SpendRequest.Status.SUBMITTED,
    )
    ed = (d.get("event_date") or "").strip()
    if ed:
        try:
            sr.event_date = date.fromisoformat(ed)
        except ValueError:
            pass
    f = request.FILES.get("attachment") or request.FILES.get("budget") or request.FILES.get("file")
    if f:
        sr.attachment = f
    # Training request — BQA accreditation decides whether the levy is recoverable.
    if rtype == SpendRequest.Type.TRAINING:
        sr.training_provider = (d.get("training_provider") or "").strip()[:200]
        acc = d.get("bqa_accredited")
        if acc is None or str(acc).strip() == "":
            sr.bqa_accredited = None
            sr.bqa_recovery_status = SpendRequest.BQARecovery.NOT_APPLICABLE
        else:
            is_acc = str(acc).lower() in ("true", "1", "yes", "on")
            sr.bqa_accredited = is_acc
            sr.bqa_recovery_status = (SpendRequest.BQARecovery.RECOVERABLE if is_acc
                                      else SpendRequest.BQARecovery.NOT_RECOVERABLE)
    sr.save()
    analyze_spend_request(sr)   # never raises; sets ai_status
    sr.save(update_fields=["ai_status", "ai_summary", "ai_extracted_total", "ai_flags", "updated_at"])
    return Response(_serialize(sr, detail=True), status=http.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spend_request_detail(request, req_id):
    sr = (SpendRequest.objects.select_related("requester", "approver")
          .filter(pk=req_id).first())
    if not sr:
        return Response({"detail": "Not found."}, status=404)
    if sr.requester_id != request.user.id and not _can_approve_spend(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    return Response(_serialize(sr, detail=True))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def spend_request_decide(request, req_id):
    if not _can_approve_spend(request.user):
        return Response({"detail": "Only the CFO / EXCO may approve spend requests."}, status=403)
    sr = SpendRequest.objects.filter(pk=req_id).first()
    if not sr:
        return Response({"detail": "Not found."}, status=404)
    decision = (request.data.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject"):
        return Response({"detail": "decision must be approve|reject."}, status=400)
    if sr.status in (SpendRequest.Status.APPROVED, SpendRequest.Status.REJECTED):
        return Response({"detail": f"Already {sr.status}."}, status=409)
    sr.status = (SpendRequest.Status.APPROVED if decision == "approve"
                 else SpendRequest.Status.REJECTED)
    sr.approver = request.user
    sr.decided_at = timezone.now()
    sr.decision_notes = (request.data.get("notes") or "").strip()
    sr.save(update_fields=["status", "approver", "decided_at", "decision_notes", "updated_at"])
    return Response(_serialize(sr, detail=True))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spend_request_file(request, req_id):
    sr = SpendRequest.objects.filter(pk=req_id).first()
    if not sr or not sr.attachment:
        raise Http404
    if sr.requester_id != request.user.id and not _can_approve_spend(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    return FileResponse(sr.attachment.open("rb"), as_attachment=True,
                        filename=(sr.attachment.name or "budget").split("/")[-1])


def _current_fy_start():
    """Alpha Direct financial year = 1 Jul -> 30 Jun."""
    from django.utils import timezone
    today = timezone.localdate()
    return date(today.year if today.month >= 7 else today.year - 1, 7, 1)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spend_levy_tracker(request):
    """GET /api/v1/spend-requests/levy-tracker/ — training-levy recovery for the
    current FY: annual claimable ceiling vs BQA-recoverable training already
    approved (used), and what's still claimable. (#1, CFO 2026-07-13.)"""
    from django.conf import settings
    from django.db.models import Sum
    from budgets.models import TRAINING_LEVY_CEILING_BWP
    ceiling = Decimal(str(getattr(settings, "TRAINING_LEVY_CEILING_BWP", TRAINING_LEVY_CEILING_BWP)))
    fy_start = _current_fy_start()
    rec = SpendRequest.objects.filter(
        request_type=SpendRequest.Type.TRAINING,
        status=SpendRequest.Status.APPROVED,
        bqa_recovery_status__in=[SpendRequest.BQARecovery.RECOVERABLE,
                                 SpendRequest.BQARecovery.CLAIMED,
                                 SpendRequest.BQARecovery.RECOVERED],
        created_at__date__gte=fy_start)
    used = rec.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    recovered = (rec.filter(bqa_recovery_status=SpendRequest.BQARecovery.RECOVERED)
                 .aggregate(s=Sum("amount"))["s"] or Decimal("0.00"))
    remaining = ceiling - used
    return Response({
        "fy_start": fy_start.isoformat(),
        "ceiling": str(ceiling),
        "used_recoverable": str(used),
        "recovered": str(recovered),
        "remaining_claimable": str(remaining if remaining > 0 else Decimal("0.00")),
        "levy_rate_pct": "0.5",
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def spend_request_bqa_claim(request, req_id):
    """POST — move a recoverable training's BQA claim forward: recoverable ->
    claimed -> recovered. (#2, CFO 2026-07-13.)"""
    if not _can_approve_spend(request.user):
        return Response({"detail": "Only the CFO / EXCO may update BQA claims."}, status=403)
    sr = SpendRequest.objects.filter(pk=req_id).first()
    if not sr:
        return Response({"detail": "Not found."}, status=404)
    if (sr.request_type != SpendRequest.Type.TRAINING
            or sr.bqa_recovery_status == SpendRequest.BQARecovery.NOT_RECOVERABLE):
        return Response({"detail": "Only BQA-recoverable trainings can be claimed."}, status=400)
    mapping = {
        "recoverable": SpendRequest.BQARecovery.RECOVERABLE,
        "claimed": SpendRequest.BQARecovery.CLAIMED,
        "recovered": SpendRequest.BQARecovery.RECOVERED,
    }
    to = (request.data.get("status") or "").strip().lower()
    if to not in mapping:
        return Response({"detail": "status must be recoverable|claimed|recovered."}, status=400)
    sr.bqa_recovery_status = mapping[to]
    sr.save(update_fields=["bqa_recovery_status", "updated_at"])
    return Response(_serialize(sr, detail=True))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def spend_request_claim_pack(request, req_id):
    """GET — a structured BQA reimbursement claim summary for a recoverable
    training (the FE renders it as a printable claim). (#2.)"""
    sr = (SpendRequest.objects.select_related("requester", "approver").filter(pk=req_id).first())
    if not sr:
        return Response({"detail": "Not found."}, status=404)
    if sr.requester_id != request.user.id and not _can_approve_spend(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    if sr.request_type != SpendRequest.Type.TRAINING:
        return Response({"detail": "Claim packs are for training requests only."}, status=400)
    return Response({
        "claim_title": f"BQA training reimbursement claim — {sr.title}",
        "training_provider": sr.training_provider,
        "bqa_accredited": sr.bqa_accredited,
        "recoverable": sr.bqa_recovery_status in ("recoverable", "claimed", "recovered"),
        "recovery_status": sr.get_bqa_recovery_status_display(),
        "amount": str(sr.amount),
        "event_date": sr.event_date.isoformat() if sr.event_date else None,
        "requester": sr.requester.get_full_name() or sr.requester.username,
        "approver": (sr.approver.get_full_name() or sr.approver.username) if sr.approver_id else "",
        "approved_at": sr.decided_at.isoformat() if sr.decided_at else None,
        "description": sr.description,
        "has_budget_attachment": bool(sr.attachment),
        "checklist": [
            "Signed training invoice from the provider",
            "Proof of the provider's BQA accreditation",
            "Attendance register / certificate of completion",
            "Proof of payment",
        ],
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def spend_request_actual(request, req_id):
    """POST — record what an approved request ACTUALLY cost + the PO/payment ref,
    so budget-vs-actual variance is visible. (#4, CFO 2026-07-13.)"""
    if not _can_approve_spend(request.user):
        return Response({"detail": "Only the CFO / EXCO / finance may record actual spend."}, status=403)
    sr = SpendRequest.objects.filter(pk=req_id).first()
    if not sr:
        return Response({"detail": "Not found."}, status=404)
    sr.actual_spent = _dec(request.data.get("actual_spent"))
    sr.actual_reference = (request.data.get("actual_reference") or "").strip()[:120]
    sr.save(update_fields=["actual_spent", "actual_reference", "updated_at"])
    return Response(_serialize(sr, detail=True))
