from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from hris.contract_followup import (
    classify_login,
    follow_through_renewal,
    orphan_logins,
    record_probation,
    renewal_letter_docx,
)
from hris.hr_settings import is_hr_head
from hris.models import ContractRenewalDecision
from payroll.models import EmploymentContract

User = get_user_model()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def contract_probation(request, contract_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "No HR access."}, status=403)

    contract = get_object_or_404(EmploymentContract, pk=contract_id)

    decision = request.data.get("decision")
    note = request.data.get("note", "")
    new_end_raw = request.data.get("new_end")

    new_end = None
    if new_end_raw:
        new_end = parse_date(new_end_raw)
        if not new_end:
            return Response({"detail": "Invalid new_end date."}, status=400)

    try:
        probation_decision = record_probation(
            contract,
            decision,
            request.user,
            new_end=new_end,
            note=note,
        )
    except ValidationError as exc:
        return Response({"detail": str(exc)}, status=400)

    return Response(
        {
            "decision": decision,
            "probation_end_date": (
                contract.probation_end_date.isoformat()
                if contract.probation_end_date
                else None
            ),
            "contract_type": contract.contract_type,
            "probation_decision_id": probation_decision.id,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def contract_follow_through(request, contract_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "No HR access."}, status=403)

    contract = get_object_or_404(EmploymentContract, pk=contract_id)

    decision = (
        ContractRenewalDecision.objects.filter(contract=contract)
        .order_by("-created_at")
        .first()
    )
    if not decision:
        return Response({"detail": "No renewal decision found."}, status=404)

    result = follow_through_renewal(decision, request.user)
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contract_letter(request, contract_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "No HR access."}, status=403)

    contract = get_object_or_404(EmploymentContract, pk=contract_id)
    docx_bytes = renewal_letter_docx(contract)

    employee = contract.employee
    filename = f"renewal-{employee.employee_number or employee.id}.docx"

    response = HttpResponse(
        docx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def logins_without_payroll(request):
    if not user_can_access_hris(request.user):
        return Response({"detail": "No HR access."}, status=403)

    return Response({"rows": orphan_logins()})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def classify_login_view(request, user_id):
    if not user_can_access_hris(request.user):
        return Response({"detail": "No HR access."}, status=403)

    if not is_hr_head(request.user):
        return Response({"detail": "Only HR Head may classify logins."}, status=403)

    user_obj = get_object_or_404(User, pk=user_id)

    kind = request.data.get("kind")
    note = request.data.get("note", "")
    from hris.models import LoginClassification
    if kind not in LoginClassification.Kind.values:
        return Response({"detail": "Choose staff, partner, service or close."}, status=400)

    classify_login(user_obj, kind, request.user, note=note)

    return Response({"status": "ok"})
