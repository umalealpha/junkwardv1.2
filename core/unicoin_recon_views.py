"""API for UniCoin reconciliation exceptions (CFO direction 2026-09-01).

  POST /api/v1/unicoin/recon-exceptions/            raise/upsert a finding
  GET  /api/v1/unicoin/recon-exceptions/            the open register (staff)
  POST /api/v1/unicoin/recon-exceptions/<id>/clear/ close one a human actioned

The POST is what the detection flow (Power Automate / n8n) calls, authenticated
with its scoped `unicoin-recon` ApiKey — a WRITE scope locked to this one path in
core.api_key_auth. A finance human may also raise / read / clear from a browser.
Nothing here moves money or changes a policy; it only raises and closes tasks.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from core.models import UniCoinReconException
from core.unicoin_recon import raise_recon_exception, clear_exception


def _is_api_key(request) -> bool:
    return hasattr(getattr(request, "auth", None), "allowed_scopes")


def _has_finance(request) -> bool:
    return CanViewFinancials().has_permission(request, None)


def _exc_json(exc: UniCoinReconException) -> dict:
    return {
        "id": str(exc.id),
        "policy_number": exc.policy_number,
        "kind": exc.kind,
        "kind_label": exc.get_kind_display(),
        "period": exc.period,
        "amount": str(exc.amount),
        "currency": exc.currency,
        "reason": exc.reason,
        "recommended_action": exc.recommended_action,
        "source_ref": exc.source_ref,
        "status": exc.status,
        "owner": (exc.owner.get_full_name() or exc.owner.username) if exc.owner else None,
        "task_id": str(exc.task_id) if exc.task_id else None,
        "created_at": exc.created_at.isoformat(),
        "cleared_at": exc.cleared_at.isoformat() if exc.cleared_at else None,
        "cleared_note": exc.cleared_note,
    }


@api_view(["POST", "GET"])
@permission_classes([IsAuthenticated])
def recon_exceptions(request):
    # A scoped ApiKey is already locked to this path at the authentication layer;
    # a human must hold finance access.
    if not _is_api_key(request) and not _has_finance(request):
        return Response({"detail": "Restricted to finance / the UniCoin recon key."}, status=403)

    if request.method == "GET":
        qs = UniCoinReconException.objects.select_related("owner", "task").all()
        status_f = (request.query_params.get("status") or "open").strip()
        if status_f and status_f != "all":
            qs = qs.filter(status=status_f)
        kind_f = (request.query_params.get("kind") or "").strip()
        if kind_f:
            qs = qs.filter(kind=kind_f)
        period_f = (request.query_params.get("period") or "").strip()
        if period_f:
            qs = qs.filter(period=period_f)
        total = qs.count()
        rows = [_exc_json(e) for e in qs[:1000]]
        return Response({"count": total, "results": rows, "truncated": total > len(rows)})

    d = request.data or {}
    try:
        exc = raise_recon_exception(
            policy_number=d.get("policy_number"),
            kind=d.get("kind"),
            reason=d.get("reason"),
            amount=d.get("amount") or 0,
            currency=d.get("currency") or "BWP",
            period=d.get("period") or "",
            recommended_action=d.get("recommended_action") or "",
            source_ref=d.get("source_ref") or "",
            owner_email=d.get("owner_email") or "",
            raised_by=request.user if getattr(request.user, "is_authenticated", False) else None,
        )
    except ValidationError as e:
        return Response({"detail": "; ".join(e.messages)}, status=400)
    return Response(_exc_json(exc), status=201)


@api_view(["POST"])
@permission_classes([CanViewFinancials])
def clear_recon_exception(request, pk):
    # Clearing is a human decision — the detection key raises findings, it does
    # not get to close them. Keeps the human-in-the-loop the whole design rests on.
    if _is_api_key(request):
        return Response(
            {"detail": "Clearing an exception is a human decision — sign in with a "
                       "finance login; the recon key can only raise findings."},
            status=403)
    exc = get_object_or_404(UniCoinReconException, pk=pk)
    d = request.data or {}
    clear_exception(exc, by=request.user, note=d.get("note") or "",
                    dismiss=bool(d.get("dismiss")))
    return Response(_exc_json(exc))
