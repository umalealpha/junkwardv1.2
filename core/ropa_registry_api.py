"""
core/ropa_registry_api.py — ROPA module REST API (Developer Build Brief).

  GET  /api/v1/ropa/queue/            review queue (+ summary); dept-heads see own dept
  POST /api/v1/ropa/scan/             DPO: detect fields -> draft entries
  GET  /api/v1/ropa/entries/<id>/     one entry (auto + suggested + human fields)
  PATCH/api/v1/ropa/entries/<id>/     edit human fields; confirm lawful basis = DPO only
  GET  /api/v1/ropa/vendors/          vendor register (with suggested risk)
  POST /api/v1/ropa/vendors/          DPO: add a vendor
  PATCH/api/v1/ropa/vendors/<id>/     edit; confirm risk level = DPO only
  GET  /api/v1/ropa/rulebook/         rulebook + legal-basis rules (reference)

Suggestions never self-finalise: confirmed_lawful_basis and confirmed_risk_level
are DPO-only actions.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from iso_compliance.models import (LegalBasisRule, RopaEntry, RulebookRequirement,
                                   VendorRegister)
from core.ropa_registry import (compute_vendor_risk, detect_and_create_entries,
                                evaluate_flags, suggest_legal_basis)

FLAG_LABELS = dict(RopaEntry.FLAG_CHOICES)
BASIS_LABELS = dict(LegalBasisRule.BASIS_CHOICES)
# human fields a department head may edit (NOT the confirm fields)
HUMAN_FIELDS = [
    'department', 'processing_activity', 'purpose_of_processing', 'data_subject_categories',
    'recipients_third_parties', 'cross_border_transfer', 'cross_border_details',
    'retention_period', 'security_measures', 'description_of_risks', 'dpia_required', 'dpia_status',
]


def _is_dpo(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    if user.groups.filter(name__in=['DPO', 'Data Protection Officer']).exists():
        return True
    prof = getattr(user, 'profile', None)
    return bool(getattr(prof, 'is_dpo', False))


def _user_department(user) -> str:
    prof = getattr(user, 'profile', None)
    return (getattr(prof, 'department', '') or '') if prof else ''


def _entry_row(e: RopaEntry) -> dict:
    return {
        'entry_id': str(e.entry_id),
        'source_table': e.source_table, 'source_field': e.source_field,
        'detected_data_category': e.detected_data_category,
        'flag_status': e.flag_status, 'flag_status_label': FLAG_LABELS.get(e.flag_status, e.flag_status),
        'flag_reasons': e.flag_reasons or [],
        'suggested_lawful_basis': e.suggested_lawful_basis,
        'suggested_lawful_basis_label': BASIS_LABELS.get(e.suggested_lawful_basis, ''),
        'suggested_lawful_basis_reason': e.suggested_lawful_basis_reason,
        'department': e.department, 'processing_activity': e.processing_activity,
        'purpose_of_processing': e.purpose_of_processing, 'data_subject_categories': e.data_subject_categories,
        'confirmed_lawful_basis': e.confirmed_lawful_basis,
        'confirmed_lawful_basis_label': BASIS_LABELS.get(e.confirmed_lawful_basis, ''),
        'processor_vendor': str(e.processor_vendor_id) if e.processor_vendor_id else None,
        'processor_vendor_name': e.processor_vendor.vendor_name if e.processor_vendor_id else '',
        'recipients_third_parties': e.recipients_third_parties,
        'cross_border_transfer': e.cross_border_transfer, 'cross_border_details': e.cross_border_details,
        'retention_period': e.retention_period, 'security_measures': e.security_measures,
        'description_of_risks': e.description_of_risks,
        'dpia_required': e.dpia_required, 'dpia_status': e.dpia_status,
        'date_last_reviewed': e.date_last_reviewed.isoformat() if e.date_last_reviewed else None,
    }


class RopaQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        is_dpo = _is_dpo(request.user)
        qs = RopaEntry.objects.select_related('processor_vendor').all()
        if not is_dpo:
            dept = _user_department(request.user)
            qs = qs.filter(department__iexact=dept) if dept else qs.none()
        fs = request.query_params.get('flag_status')
        if fs:
            qs = qs.filter(flag_status=fs)
        entries = list(qs)
        counts = {k: 0 for k, _ in RopaEntry.FLAG_CHOICES}
        for e in RopaEntry.objects.values_list('flag_status', flat=True):
            counts[e] = counts.get(e, 0) + 1
        return Response({
            'is_dpo': is_dpo,
            'summary': {'total': RopaEntry.objects.count(), 'by_status': counts,
                        'labels': FLAG_LABELS},
            'entries': [_entry_row(e) for e in entries],
        })


class RopaScanView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _is_dpo(request.user):
            return Response({'detail': 'DPO only.'}, status=403)
        created, skipped = detect_and_create_entries()
        return Response({'created': created, 'skipped': skipped,
                         'total': RopaEntry.objects.count()})


class RopaEntryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        e = get_object_or_404(RopaEntry, pk=pk)
        return Response(_entry_row(e))

    def patch(self, request, pk):
        e = get_object_or_404(RopaEntry.objects.select_related('processor_vendor'), pk=pk)
        is_dpo = _is_dpo(request.user)
        if not is_dpo:
            dept = _user_department(request.user)
            if not dept or e.department.lower() != dept.lower():
                return Response({'detail': "You can only edit your own department's entries."}, status=403)
        d = request.data
        for f in HUMAN_FIELDS:
            if f in d:
                setattr(e, f, d[f])
        if 'processor_vendor' in d:
            e.processor_vendor_id = d['processor_vendor'] or None
        # confirming lawful basis is a DPO-only action
        if 'confirmed_lawful_basis' in d:
            if not is_dpo:
                return Response({'detail': 'Only the DPO can confirm the lawful basis.'}, status=403)
            val = (d['confirmed_lawful_basis'] or '').strip()
            valid = {v for v, _ in LegalBasisRule.BASIS_CHOICES}
            if val and val not in valid:
                return Response({'detail': f'lawful basis must be one of {sorted(valid)}.'}, status=400)
            e.confirmed_lawful_basis = val
            e.lawful_basis_confirmed_by = request.user if val else None
            e.lawful_basis_confirmed_at = timezone.now() if val else None
        e.save()
        suggest_legal_basis(e)
        e.save(update_fields=['suggested_lawful_basis', 'suggested_lawful_basis_reason'])
        evaluate_flags(e)
        return Response(_entry_row(e))


def _vendor_row(v: VendorRegister) -> dict:
    return {
        'vendor_id': str(v.vendor_id), 'vendor_name': v.vendor_name,
        'dpa_contract_signed': v.dpa_contract_signed, 'sub_processors_used': v.sub_processors_used,
        'sub_processor_names': v.sub_processor_names,
        'last_review_date': v.last_review_date.isoformat() if v.last_review_date else None,
        'safeguards_on_file': v.safeguards_on_file,
        'suggested_risk_score': v.suggested_risk_score, 'suggested_risk_level': v.suggested_risk_level,
        'suggested_risk_reason': v.suggested_risk_reason or [],
        'confirmed_risk_level': v.confirmed_risk_level,
        'linked_activities': v.ropa_entries.count(),
    }


class VendorListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'vendors': [_vendor_row(v) for v in VendorRegister.objects.all()]})

    def post(self, request):
        if not _is_dpo(request.user):
            return Response({'detail': 'DPO only.'}, status=403)
        name = (request.data.get('vendor_name') or '').strip()
        if not name:
            return Response({'detail': 'vendor_name required.'}, status=400)
        v, _ = VendorRegister.objects.get_or_create(vendor_name=name)
        compute_vendor_risk(v)
        v.save()
        return Response(_vendor_row(v), status=201)


class VendorView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if not _is_dpo(request.user):
            return Response({'detail': 'DPO only.'}, status=403)
        v = get_object_or_404(VendorRegister, pk=pk)
        for f in ['dpa_contract_signed', 'sub_processors_used', 'sub_processor_names',
                  'last_review_date', 'safeguards_on_file']:
            if f in request.data:
                setattr(v, f, request.data[f] or (False if 'signed' in f or 'used' in f else ''))
        if 'confirmed_risk_level' in request.data:
            val = (request.data['confirmed_risk_level'] or '').strip()
            if val and val not in {'Low', 'Medium', 'High'}:
                return Response({'detail': 'risk level must be Low/Medium/High.'}, status=400)
            v.confirmed_risk_level = val
            v.risk_confirmed_by = request.user if val else None
            v.risk_confirmed_at = timezone.now() if val else None
        compute_vendor_risk(v)
        v.save()
        return Response(_vendor_row(v))


class RulebookView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'rulebook': [
                {'requirement_id': r.requirement_id, 'source': r.source, 'description': r.description,
                 'trigger_field': r.trigger_field, 'trigger_value': r.trigger_value,
                 'satisfying_evidence': r.satisfying_evidence,
                 'framework_requirement': r.framework_requirement_id}
                for r in RulebookRequirement.objects.all()
            ],
            'legal_basis_rules': [
                {'rule_id': r.rule_id, 'match_department': r.match_department,
                 'match_purpose_keywords': r.match_purpose_keywords,
                 'suggested_basis': r.suggested_basis, 'reason_template': r.reason_template,
                 'framework_requirement': r.framework_requirement_id}
                for r in LegalBasisRule.objects.all()
            ],
        })
