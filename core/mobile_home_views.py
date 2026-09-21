"""core/mobile_home_views.py — one permission-filtered Home payload (Omni Mobile B).

The mobile Home used to fire several separate requests on every open (approvals,
tasks, name). This returns them in ONE call, alongside the server-truth capability
manifest, so the phone renders Home from a single source and a transient failure on
one probe can't be misread as "no access". Read-only; it reuses the existing
approvals + task engines rather than recomputing them.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def mobile_home(request):
    user = request.user
    from core.approvals_views import pending_approvals_for
    from core.mobile_capabilities import build_mobile_capabilities
    from core.models import OmniTask
    from taskboard.payment_bulk_views import _is_cfo

    # Approvals — SAME engine the /my-approvals/ screen uses (streams + total).
    streams = pending_approvals_for(user)
    approvals_total = sum(s['count'] for s in streams)

    # Open tasks — mirror MyTasksView exactly (exclude done/cancelled; the CFO's
    # payment tasks live on the Payments screen, not the task inbox).
    tasks_qs = (OmniTask.objects
                .filter(assignee=user)
                .exclude(status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED]))
    if _is_cfo(user):
        tasks_qs = tasks_qs.exclude(payment_request__isnull=False)
    my_tasks_open = tasks_qs.count()

    return Response({
        'as_of': timezone.now().isoformat(),
        'first_name': (getattr(user, 'first_name', '') or getattr(user, 'username', '') or ''),
        'capabilities': build_mobile_capabilities(user),
        'approvals': {'streams': streams, 'total': approvals_total},
        'my_tasks_open': my_tasks_open,
    })
