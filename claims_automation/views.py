"""claims_automation/views.py

Two doors, kept apart on purpose:

  Graphite (server-to-server, ApiKey with the `claims-events` scope ONLY)
    POST /api/v1/claims-automation/graphite/events/    one claim event
    GET  /api/v1/claims-automation/graphite/insight/?claim_ref=
         what Omni read about a claim — words, triage, letter states. No PII.

  Staff (signed in; claims team, or CFO / superuser to read)
    GET  /api/v1/claims-automation/cases/              list, ?stage= ?triage= ?q=
    GET  /api/v1/claims-automation/cases/<ref>/        one case, events, letters
    GET  /api/v1/claims-automation/letters/<id>/html/  the letter as rendered
    GET  /api/v1/claims-automation/letters/<id>/pdf/   the letter PDF
    POST /api/v1/claims-automation/letters/<id>/approve/
    POST /api/v1/claims-automation/letters/<id>/decline/   {note}
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api_key_auth import ApiKeyAuthentication

from . import processor, veritas
from .models import ClaimCase, ClaimLetter, SalvageHandover

log = logging.getLogger(__name__)

SCOPE = "claims-events"
_READ_TITLES = set(processor.CLAIMS_SENIOR_TITLES) | {
    "junior_claims_associate",
    "claims_intern",
    "cfo",
}


# ── Graphite door ───────────────────────────────────────────────────────────


def _key_ok(request) -> bool:
    key = getattr(request, "auth", None)
    scopes = list(getattr(key, "allowed_scopes", None) or [])
    return SCOPE in scopes or "admin" in scopes


@api_view(["POST"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def graphite_event(request):
    if not _key_ok(request):
        return Response({"detail": "This key may not send claim events."}, status=403)
    try:
        event, created = processor.receive(
            request.data if isinstance(request.data, dict) else {},
            received_via=str(
                getattr(getattr(request, "auth", None), "label", "") or ""
            ),
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(
        {"id": str(event.id), "status": event.status, "actions": event.actions},
        status=201 if created else 200,
    )


def _insight(case: ClaimCase) -> dict:
    return {
        "claim_ref": case.claim_ref,
        "stage": case.get_stage_display(),
        "triage": case.triage,
        "triage_reasons": case.triage_reasons,
        "ai_summary": case.ai_summary,
        "ai_next_step": case.ai_next_step,
        "premium_light": case.premium_light,
        "letters": [
            {
                "kind": l.get_kind_display(),
                "status": l.get_status_display(),
                "prepared_at": l.created_at.isoformat(),
            }
            for l in case.letters.all()
        ],
        "purchase_orders_drafted": bool(case.po_assessment_id),
        "deep_link": processor.case_link(case),
        "updated_at": case.updated_at.isoformat(),
    }


@api_view(["GET"])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def graphite_insight(request):
    if not _key_ok(request):
        return Response({"detail": "This key may not read claim insight."}, status=403)
    ref = (request.GET.get("claim_ref") or "").strip().upper()
    if not ref:
        return Response({"detail": "claim_ref is required."}, status=400)
    case = ClaimCase.objects.filter(claim_ref=ref).first()
    return Response(
        {
            "claim_ref": ref,
            "found": bool(case),
            "insight": _insight(case) if case else None,
        }
    )


# ── Staff door ──────────────────────────────────────────────────────────────


def _can_read(user) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    from core.models import get_user_profile

    p = get_user_profile(user)
    return bool(p and p.is_active and p.title in _READ_TITLES)


def _letter_json(l: ClaimLetter, user) -> dict:
    return {
        "id": str(l.id),
        "kind": l.kind,
        "kind_label": l.get_kind_display(),
        "status": l.status,
        "status_label": l.get_status_display(),
        "figures": l.figures,
        "reasons": l.reasons,
        "insured_name": (l.context or {}).get("insured_name", ""),
        "has_email": bool((l.context or {}).get("insured_email")),
        "decided_by": (l.decided_by.get_full_name() or l.decided_by.username)
        if l.decided_by_id
        else "",
        "decided_at": l.decided_at.isoformat() if l.decided_at else None,
        "decision_note": l.decision_note,
        "sent_to": l.sent_to,
        "created_at": l.created_at.isoformat(),
        "can_decide": l.status == ClaimLetter.Status.AWAITING
        and processor.can_decide(user, l),
        "wording_approved": processor.wording_approved(),
    }


def _case_json(c: ClaimCase, user, detail=False) -> dict:
    f = c.facts or {}
    out = {
        "claim_ref": c.claim_ref,
        "claim_type": c.claim_type,
        "stage": c.stage,
        "stage_label": c.get_stage_display(),
        "triage": c.triage,
        "triage_reasons": c.triage_reasons,
        "premium_light": c.premium_light,
        "insured_name": (f.get("insured") or {}).get("name", ""),
        "ai_summary": c.ai_summary,
        "ai_next_step": c.ai_next_step,
        "flags": c.flags,
        "updated_at": c.updated_at.isoformat(),
        "po_assessment_id": str(c.po_assessment_id) if c.po_assessment_id else None,
        "letters": [_letter_json(l, user) for l in c.letters.all()],
        "handover": _handover_json(c),
    }
    if detail:
        out["events"] = [
            {
                "type": e.get_event_type_display(),
                "status": e.status,
                "actions": e.actions,
                "error": e.error,
                "at": e.created_at.isoformat(),
            }
            for e in c.events.all()[:50]
        ]
        out["facts"] = {k: f.get(k) for k in ("claim", "policy", "vehicle", "premium")}
    return out


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def cases(request):
    if not _can_read(request.user):
        return Response({"detail": "Claims team only."}, status=403)
    qs = ClaimCase.objects.prefetch_related("letters").all()
    if request.GET.get("stage"):
        qs = qs.filter(stage=request.GET["stage"])
    if request.GET.get("triage"):
        qs = qs.filter(triage=request.GET["triage"])
    if request.GET.get("awaiting") == "1":
        qs = qs.filter(letters__status=ClaimLetter.Status.AWAITING).distinct()
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(claim_ref__icontains=q) | Q(claim_type__icontains=q))
    rows = [_case_json(c, request.user) for c in qs[:200]]
    awaiting = ClaimLetter.objects.filter(status=ClaimLetter.Status.AWAITING).count()
    return Response(
        {
            "results": rows,
            "awaiting_letters": awaiting,
            # Counted on the server over ALL cases — the list above is capped at 200.
            "counts": {
                "total": ClaimCase.objects.count(),
                "straight_through": ClaimCase.objects.filter(triage="straight_through").count(),
                "exception": ClaimCase.objects.filter(triage="exception").count(),
            },
            "wording_approved": processor.wording_approved(),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_detail(request, ref):
    if not _can_read(request.user):
        return Response({"detail": "Claims team only."}, status=403)
    c = get_object_or_404(ClaimCase, claim_ref=ref.strip().upper())
    return Response(_case_json(c, request.user, detail=True))


def _letter_for(request, pk):
    if not _can_read(request.user):
        return None
    return get_object_or_404(ClaimLetter.objects.select_related("case"), pk=pk)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def letter_html(request, pk):
    l = _letter_for(request, pk)
    if l is None:
        return Response({"detail": "Claims team only."}, status=403)
    from .letters import render_html

    try:
        body = render_html(l.kind, processor.render_context(l))
    except ValueError as exc:
        # The letter refuses rather than printing a figure it cannot stand
        # behind (B9). Say why in plain words instead of a server error, and
        # log it — a refused letter is a thing somebody has to act on.
        log.warning("letter %s not rendered (html): %s", pk, exc)
        return Response({"detail": str(exc)}, status=409)
    return HttpResponse(body, content_type="text/html")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def letter_pdf(request, pk):
    l = _letter_for(request, pk)
    if l is None:
        return Response({"detail": "Claims team only."}, status=403)
    from .letters import render_pdf

    try:
        pdf = render_pdf(l.kind, processor.render_context(l))
    except ValueError as exc:
        log.warning("letter %s not rendered (pdf): %s", pk, exc)
        return Response({"detail": str(exc)}, status=409)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{l.kind}-{l.case.claim_ref}.pdf"'
    return resp


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def letter_approve(request, pk):
    l = _letter_for(request, pk)
    if l is None or not processor.can_decide(request.user, l):
        who = (
            "the Claims Manager"
            if l is not None and l.kind == "repudiation"
            else "claims seniors"
        )
        return Response({"detail": f"Only {who} may approve this letter."}, status=403)
    try:
        outcome = processor.approve(
            l, request.user, str((request.data or {}).get("override_reason") or "")
        )
    except processor.PossessionMissing as exc:
        # The yard has not confirmed they hold the wreck. A claims manager may
        # still authorise it by sending a written reason (CFO 19-Sep-2026).
        return Response({"detail": str(exc), "needs_override": True}, status=409)
    except PermissionError as exc:
        return Response({"detail": str(exc)}, status=403)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=409)
    return Response({"outcome": outcome, "letter": _letter_json(l, request.user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def letter_decline(request, pk):
    l = _letter_for(request, pk)
    if l is None or not processor.can_decide(request.user, l):
        return Response({"detail": "You may not decide this letter."}, status=403)
    try:
        outcome = processor.decline(
            l, request.user, str((request.data or {}).get("note") or "")
        )
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"outcome": outcome, "letter": _letter_json(l, request.user)})


# ── the salvage handover (Veritas) ──────────────────────────────────────────


def _can_declare(user) -> bool:
    """The yard declares what it holds: Veritas (VCM) / ADIC salvage users, or a
    claims senior recording it on their behalf."""
    if _yard_user(user):
        return True
    return _can_read(user)


def _yard_user(user) -> bool:
    """Veritas (VCM) only. The salvage app also admits ADIC staff, which is right
    for the yard's own inventory but too wide for declaring possession on a claim
    (Fable review, 20-Sep-2026) — so check the company here."""
    try:
        from payroll.models import Employee
    except ImportError:
        return False
    emp = Employee.objects.filter(user=user).select_related('company').first()
    return bool(emp and emp.company and getattr(emp.company, 'code', '') == 'VCM')


def _handover_json(case: ClaimCase) -> dict:
    h = getattr(case, 'handover', None)
    ok, why = veritas.possession_ok(case)
    charge = case.veritas_charges.first()
    return {
        'items': [{'key': k, 'label': lbl, 'answer': ((h.checklist or {}).get(k) if h else None)}
                  for k, lbl in SalvageHandover.ITEMS],
        'complete': bool(h and h.complete),
        'overridden': bool(h and h.overridden),
        'override_reason': (h.override_reason if h else ''),
        'note': (h.note if h else ''),
        'yard_reference': (h.yard_reference if h else ''),
        'declared_by': ((h.declared_by.get_full_name() or h.declared_by.username)
                        if h and h.declared_by_id else ''),
        'declared_at': (h.declared_at.isoformat() if h and h.declared_at else None),
        'may_settle': ok,
        'blocked_because': why,
        'veritas_charge': ({'amount': str(charge.amount), 'settlement': str(charge.settlement),
                            'status': charge.get_status_display(), 'note': charge.note,
                            'invoice': (charge.invoice.invoice_number if charge.invoice_id else None)}
                           if charge else None),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def handover(request, ref):
    """GET: what the yard has declared. POST: declare it.
    The yard sees the claim reference and the vehicle — never the insured's details."""
    if not _can_declare(request.user):
        return Response({'detail': 'Salvage yard or claims team only.'}, status=403)
    case = get_object_or_404(ClaimCase, claim_ref=ref.strip().upper())
    if request.method == 'GET':
        vehicle = (case.facts or {}).get('vehicle') or {}
        return Response({'claim_ref': case.claim_ref, 'vehicle': vehicle,
                         'handover': _handover_json(case)})

    answers = (request.data or {}).get('checklist') or {}
    keys = {k for k, _ in SalvageHandover.ITEMS}
    clean = {k: str(v).lower() for k, v in answers.items()
             if k in keys and str(v).lower() in SalvageHandover.ANSWERS}
    if not clean:
        return Response({'detail': 'Answer each item yes, no or not applicable.'}, status=400)
    h, _ = SalvageHandover.objects.get_or_create(case=case)
    h.checklist = {**(h.checklist or {}), **clean}
    data = request.data or {}
    if 'note' in data:                       # an empty note CLEARS the old one
        h.note = str(data.get('note') or '')[:4000]
    if 'yard_reference' in data:
        h.yard_reference = str(data.get('yard_reference') or '')[:64]
    h.declared_by, h.declared_at = request.user, timezone.now()
    h.save()
    raised = veritas.maybe_raise(case, request.user) if h.complete else ''
    return Response({'claim_ref': case.claim_ref, 'handover': _handover_json(case),
                     'outcome': raised or ('Recorded.' if h.complete else
                                           'Recorded — still waiting on: ' + ', '.join(h.missing))})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def handovers_waiting(request):
    """The yard's worklist: write-offs where possession is not yet confirmed.
    Claim reference and vehicle only."""
    if not _can_declare(request.user):
        return Response({'detail': 'Salvage yard or claims team only.'}, status=403)
    rows = []
    qs = (ClaimCase.objects
          .filter(letters__kind=ClaimLetter.Kind.AOL)
          .exclude(letters__kind=ClaimLetter.Kind.AOL,
                   letters__status=ClaimLetter.Status.DECLINED)
          .distinct().prefetch_related('letters', 'veritas_charges')[:200])
    for case in qs:
        h = getattr(case, 'handover', None)
        if h and (h.complete or h.overridden) and case.veritas_charges.exists():
            continue
        vehicle = (case.facts or {}).get('vehicle') or {}
        letter = case.letters.filter(kind=ClaimLetter.Kind.AOL).first()
        rows.append({'claim_ref': case.claim_ref, 'vehicle': vehicle,
                     'agreement_status': letter.get_status_display() if letter else '',
                     'handover': _handover_json(case)})
    return Response({'results': rows, 'showing': len(rows),
                     'more_than_shown': len(rows) >= 200})
