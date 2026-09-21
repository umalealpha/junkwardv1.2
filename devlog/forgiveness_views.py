"""
devlog/forgiveness_views.py — the forgiveness dashboard, CFO's eyes only.

Rule 1b lets a person be forgiven a late morning if they tell Omni before 09:00,
three times a month. The CFO's concern (2026-09-09): "keep a track of them,
create a dashboard for people who is always asking forgiveness so we nail them
on Nov performence feedback."

The heavy lifting — who asked, how often, how close to the cutoff, how many were
too late — is already done by hris.late_notice_report.collect(). This is the
his-eyes-only front door on top of it, with a month filter, and it reuses the
same is_the_cfo gate as the build log so there is one lock, not two.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from rest_framework.permissions import BasePermission


class CanSeeForgiveness(BasePermission):
    """CFO + CEO/COO + the HR team (CFO 2026-09-09: "arun and hr team also").

    is_hr_doc_admin already means exactly the group C-suite plus HR — the same
    gate every other personal HR document uses — so forgiveness rides on it
    rather than inventing a second HR list to drift out of sync. The build log
    and the job switches stay the CFO's alone; only this watch screen widens.
    """
    message = "This screen is for the CFO, the CEO and the HR team."

    def has_permission(self, request, view):
        from hris.document_access import is_hr_doc_admin
        return is_hr_doc_admin(request.user)


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanSeeForgiveness])
def forgiveness(request):
    """GET /api/v1/cfo/forgiveness/?months=3&ai=1 — who keeps asking."""
    from hris.late_notice_report import collect, narrative

    try:
        months = max(1, min(int(request.query_params.get('months') or 3), 12))
    except (TypeError, ValueError):
        months = 3

    report = collect(months=months)
    if (request.query_params.get('ai') or '1') != '0':
        report['narrative'] = narrative(report)
    return Response(report)
