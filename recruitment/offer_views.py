from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status as drf_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.models import AuditLog
from hris import onboarding_service
from hris.models import OfferLetter
from recruitment.models import AuthorityToRecruit

from .offer_letter import build_docx, company_for


def _parse_date(value):
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return value
    parsed = parse_date(str(value))
    if parsed is None:
        raise ValueError("Invalid date. Use ISO format (YYYY-MM-DD).")
    return parsed


def _offer_payload(authority, offer) -> dict:
    return {
        "id": str(offer.pk),
        "status": offer.status,
        "candidate_email": offer.candidate_email,
        "start_date": offer.start_date.isoformat() if offer.start_date else None,
        "decided_at": offer.decided_at.isoformat() if offer.decided_at else None,
        "onboarding_request_id": (
            str(offer.onboarding_request_id) if offer.onboarding_request_id else None
        ),
    }


def _authority_payload(authority) -> dict:
    offer = getattr(authority, "offer_letter", None)
    return {
        "authority_id": str(authority.pk),
        "reference": authority.reference,
        "person_name": authority.person_name,
        "position": authority.position,
        "department": authority.department,
        "entity": authority.entity,
        "effective_date": (
            authority.effective_date.isoformat() if authority.effective_date else None
        ),
        "offer": _offer_payload(authority, offer) if offer is not None else None,
    }


def _audit_offer(action: str, offer, user, description: str, old_values: dict | None = None):
    new_values = {
        "id": str(offer.pk),
        "authority_id": str(offer.authority_id),
        "status": offer.status,
        "candidate_email": offer.candidate_email,
        "start_date": offer.start_date.isoformat() if offer.start_date else None,
        "decided_at": offer.decided_at.isoformat() if offer.decided_at else None,
        "onboarding_request_id": (
            str(offer.onboarding_request_id) if offer.onboarding_request_id else None
        ),
    }
    AuditLog.objects.create(
        table_name="OfferLetter",
        record_id=str(offer.pk),
        action=action,
        old_values=old_values or {},
        new_values=new_values,
        user=user,
        description=description,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def offer_list(request):
    if not user_can_access_hris(request.user):
        return Response({"detail": "Forbidden"}, status=drf_status.HTTP_403_FORBIDDEN)

    authorities = (
        AuthorityToRecruit.objects.filter(status="approved", kind="recruit")
        .order_by("-created_at")[:100]
        .select_related("offer_letter")
    )
    data = [_authority_payload(a) for a in authorities]
    return Response(data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def offer_draft(request, authority_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "Forbidden"}, status=drf_status.HTTP_403_FORBIDDEN)

    authority = AuthorityToRecruit.objects.filter(pk=authority_id).first()
    if authority is None:
        return Response({"detail": "Not found"}, status=drf_status.HTTP_404_NOT_FOUND)

    if authority.status != "approved" or authority.kind != "recruit":
        return Response(
            {
                "detail": "Offer letters can only be drafted from approved "
                "recruit authorities."
            },
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    offer, created = OfferLetter.objects.get_or_create(authority=authority)

    old_values = {
        "status": offer.status,
        "candidate_email": offer.candidate_email,
        "start_date": offer.start_date.isoformat() if offer.start_date else None,
    }

    if "candidate_email" in request.data:
        offer.candidate_email = request.data.get("candidate_email") or ""

    if "start_date" in request.data:
        try:
            offer.start_date = _parse_date(request.data.get("start_date"))
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST
            )

    offer.save()

    _audit_offer(
        "create" if created else "update",
        offer,
        request.user,
        "Offer letter drafted"
        if created
        else "Offer letter draft updated",
        old_values,
    )
    return Response(_offer_payload(authority, offer))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def offer_letter_download(request, authority_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "Forbidden"}, status=drf_status.HTTP_403_FORBIDDEN)

    authority = AuthorityToRecruit.objects.filter(pk=authority_id).first()
    if authority is None:
        return Response({"detail": "Not found"}, status=drf_status.HTTP_404_NOT_FOUND)

    if authority.status != "approved" or authority.kind != "recruit":
        return Response(
            {"detail": "Only approved recruit authorities have offer letters."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    offer = getattr(authority, "offer_letter", None)
    if offer is None:
        return Response(
            {"detail": "Draft the offer first."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    docx_bytes = build_docx(authority, offer)

    AuditLog.objects.create(
        table_name="OfferLetter",
        record_id=str(offer.pk),
        action="download",
        old_values={},
        new_values={"authority_id": str(authority.pk), "reference": authority.reference},
        user=request.user,
        description="Offer letter downloaded",
    )

    response = HttpResponse(
        docx_bytes,
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    response["Content-Disposition"] = (
        f'attachment; filename="offer-{authority.reference}.docx"'
    )
    return response


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def offer_status(request, authority_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "Forbidden"}, status=drf_status.HTTP_403_FORBIDDEN)

    authority = AuthorityToRecruit.objects.filter(pk=authority_id).first()
    if authority is None:
        return Response({"detail": "Not found"}, status=drf_status.HTTP_404_NOT_FOUND)

    if authority.status != "approved" or authority.kind != "recruit":
        return Response(
            {"detail": "Only approved recruit authorities can progress an offer."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    offer = getattr(authority, "offer_letter", None)
    if offer is None:
        return Response(
            {"detail": "Draft the offer first."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    new_status = request.data.get("status")
    if new_status not in {"sent", "accepted", "declined"}:
        return Response(
            {"detail": "Invalid status."},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    old_values = {
        "status": offer.status,
        "candidate_email": offer.candidate_email,
        "start_date": offer.start_date.isoformat() if offer.start_date else None,
    }

    offer.status = new_status
    offer.decided_by = request.user
    offer.decided_at = timezone.now()

    if "candidate_email" in request.data:
        offer.candidate_email = request.data.get("candidate_email") or ""

    if "start_date" in request.data:
        try:
            offer.start_date = _parse_date(request.data.get("start_date"))
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST
            )

    if new_status == "accepted":
        if not offer.candidate_email or not offer.start_date:
            return Response(
                {
                    "detail": "Candidate email and start date are required "
                    "for acceptance."
                },
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

    offer.save()

    onboarding_error = None

    if new_status == "accepted":
        company = company_for(authority)
        if company is None:
            onboarding_error = f"No company record found for {authority.entity}."
        else:
            try:
                onboarding_request, _ = onboarding_service.submit_onboarding_request(
                    maker=request.user,
                    data={
                        "full_name": authority.person_name,
                        "email": offer.candidate_email,
                        "hire_date": offer.start_date.isoformat(),
                        "company_id": str(company.id),
                        "department": authority.department,
                        "job_title": authority.position,
                    },
                )
                offer.onboarding_request = onboarding_request
                offer.save(update_fields=["onboarding_request"])
            except ValidationError as exc:
                onboarding_error = str(exc)

    _audit_offer(
        "update",
        offer,
        request.user,
        f"Offer status changed to {new_status}",
        old_values,
    )

    payload = _offer_payload(authority, offer)
    if onboarding_error:
        payload["onboarding_error"] = onboarding_error
    return Response(payload)
