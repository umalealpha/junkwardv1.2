"""Internal Audit API — engagements, findings, responses, follow-ups, dashboard.

Every write is stamped with the acting user via AuditableMixin (audit_user), so
the module carries its own tamper-evident trail. All endpoints sit behind
InternalAuditAccess (independence gate).
"""
from __future__ import annotations

from datetime import timedelta

from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

from .access import can_view, is_editor
from .models import Engagement, Finding, FollowUp, ManagementResponse
from .permissions import InternalAuditAccess
from .serializers import (
    EngagementSerializer, FindingSerializer, FollowUpSerializer,
    ManagementResponseSerializer,
)


def _ip(request):
    return request.META.get('REMOTE_ADDR')


class _AuditingViewSet(viewsets.ModelViewSet):
    """ModelViewSet that stamps AuditLog with the acting user on every write."""
    permission_classes = [InternalAuditAccess]

    def perform_create(self, serializer):
        instance = serializer.Meta.model(**serializer.validated_data)
        instance.save(audit_user=self.request.user, audit_ip=_ip(self.request))
        serializer.instance = instance

    def perform_update(self, serializer):
        instance = serializer.instance
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        instance.save(audit_user=self.request.user, audit_ip=_ip(self.request))

    def perform_destroy(self, instance):
        instance.delete(audit_user=self.request.user, audit_ip=_ip(self.request))


class EngagementViewSet(_AuditingViewSet):
    queryset = Engagement.objects.all().prefetch_related('findings')
    serializer_class = EngagementSerializer


class FindingViewSet(_AuditingViewSet):
    serializer_class = FindingSerializer

    def get_queryset(self):
        qs = (Finding.objects
              .select_related('engagement', 'response')
              .prefetch_related('followups')
              .all())
        engagement = self.request.query_params.get('engagement')
        if engagement:
            qs = qs.filter(engagement_id=engagement)
        status_f = self.request.query_params.get('status')
        if status_f:
            qs = qs.filter(status=status_f)
        return qs


class ManagementResponseViewSet(_AuditingViewSet):
    queryset = ManagementResponse.objects.select_related('finding').all()
    serializer_class = ManagementResponseSerializer


class FollowUpViewSet(_AuditingViewSet):
    serializer_class = FollowUpSerializer

    def get_queryset(self):
        qs = FollowUp.objects.select_related('finding', 'finding__response').all()
        finding = self.request.query_params.get('finding')
        if finding:
            qs = qs.filter(finding_id=finding)
        return qs


# ---------------------------------------------------------------------------
# Dashboard — live KPIs pulled from Modules 5 + 7 (never hand-entered).
# Each tile carries its source module in the payload.
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard(request):
    if not can_view(request.user):
        return Response({'detail': 'Not authorised for Internal Audit.'}, status=403)

    open_statuses = [Finding.Status.DRAFT, Finding.Status.AGREED, Finding.Status.ISSUED]
    open_findings = Finding.objects.filter(status__in=open_statuses)

    # Open findings by rating (Module 5)
    by_rating = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
    for f in open_findings.values_list('rating', flat=True):
        if f in by_rating:
            by_rating[f] += 1

    # Ageing buckets on open findings (Module 5 + 7)
    today = timezone.localdate()
    buckets = {'0-30': 0, '31-60': 0, '61-90': 0, '90+': 0}
    for created in open_findings.values_list('created_at', flat=True):
        age = (today - created.date()).days
        if age <= 30:
            buckets['0-30'] += 1
        elif age <= 60:
            buckets['31-60'] += 1
        elif age <= 90:
            buckets['61-90'] += 1
        else:
            buckets['90+'] += 1

    # Overdue follow-up % (Module 7) — overdue computed, not stored
    active_followups = (FollowUp.objects
                        .exclude(status__in=[FollowUp.Status.IMPLEMENTED, FollowUp.Status.RISK_ACCEPTED])
                        .select_related('finding', 'finding__response'))
    total_active = 0
    overdue = 0
    for fu in active_followups:
        total_active += 1
        if fu.is_overdue:
            overdue += 1
    overdue_pct = round((overdue / total_active) * 100, 1) if total_active else 0.0

    # Fraud heat (Module 8 preview — from the fraud flag on findings today)
    fraud_open = open_findings.filter(fraud_flag=True).count()

    # Regulatory / NBFIRA (Module 14 overlay)
    regulatory_open = open_findings.filter(regulatory_tag=True).count()

    # Engagement plan status (Module 2 preview)
    eng_total = Engagement.objects.count()
    eng_closed = Engagement.objects.filter(status=Engagement.Status.CLOSED).count()
    plan_completion_pct = round((eng_closed / eng_total) * 100, 1) if eng_total else 0.0

    return Response({
        'generated_at': timezone.now().isoformat(),
        'can_edit': is_editor(request.user),
        'tiles': {
            'open_by_rating':      {'source': 'Module 5', 'value': by_rating},
            'open_by_age':         {'source': 'Module 5 + 7', 'value': buckets},
            'overdue_followup_pct': {'source': 'Module 7', 'value': overdue_pct,
                                     'overdue': overdue, 'active': total_active},
            'fraud_open':          {'source': 'Module 8', 'value': fraud_open},
            'regulatory_open':     {'source': 'Module 14', 'value': regulatory_open},
            'plan_completion_pct': {'source': 'Module 2', 'value': plan_completion_pct,
                                    'closed': eng_closed, 'total': eng_total},
        },
    })
