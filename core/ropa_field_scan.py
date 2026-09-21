"""
core/ropa_field_scan.py — field-level personal-data detector (DPO Oratile, 2026-09-08).

Introspects the Django model schema, classifies each field NAME into a personal-data
category by keyword (no data values are ever read — column metadata only), and
presents anything not yet reviewed as a queue for the DPO. The row-level companion
to the activity-level Self-updating ROPA (core.ropa_auto).

  GET  /api/v1/ropa-field-scan/                 the queue + summary
  POST /api/v1/ropa-field-scan/  {app_label, model_name, field_name, status, note}
                                                 record a DPO review decision

Read-only over data. Banking/identity/health matches with no confirmation are
surfaced as 'flagged'; other personal-looking fields as 'needs_review'.
"""
from __future__ import annotations

from django.apps import apps
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from iso_compliance.models import PiiFieldReview

# Personal-data keyword rules. SHORT keys (<5 chars) match a whole underscore
# TOKEN only (so \'tin\' does not fire inside \'underwriting\'); longer keys match as
# a substring of the field name. First category that matches wins.
RULES = [
    ('banking', ['bank', 'iban', 'swift', 'branch_code', 'account_number', 'acc_no',
                 'card_number', 'card_no', 'commission_bank', 'bank_ref', 'payout_account']),
    ('identity', ['omang', 'id_number', 'national_id', 'passport', 'nric', 'tin',
                  'tax_number', 'date_of_birth', 'dob', 'birth_date']),
    ('health', ['medical', 'diagnos', 'clinical', 'health', 'disease', 'disability',
                'illness', 'treatment', 'prescription', 'sick_note']),
    ('contact', ['next_of_kin', 'kin', 'phone', 'mobile', 'msisdn', 'email',
                 'address', 'residential', 'postal', 'emergency_contact', 'contact_person']),
    ('identity', ['first_name', 'last_name', 'surname', 'full_name', 'maiden_name',
                  'given_name', 'middle_name']),
]
AUTO_FLAG = {'banking', 'identity', 'health'}
# Field-NAME substrings that are never the personal datum itself (avoid false hits).
IGNORE_SUBSTR = ['bank_account_id', 'bankaccount', 'gl_account', 'account_type',
                 'account_code', 'chart_of_account', 'contact_type', 'address_type',
                 'email_template', 'email_log', 'company_email', 'from_email',
                 'email_sent', 'email_status', 'phone_verified', 'address_line_type']
# Suffixes that mark a flag/timestamp/id, not the data value.
IGNORE_SUFFIX = ('_id', '_at', '_on', '_until', '_type', '_count', '_url', '_flag',
                 '_verified', '_status', '_pct', '_code', '_ok', '_number_of')
IGNORE_PREFIX = ('is_', 'has_', 'can_', 'should_', 'was_', 'num_', 'total_')


def _classify(field_name: str):
    fn = (field_name or '').lower()
    if not fn or any(ig in fn for ig in IGNORE_SUBSTR):
        return None
    if fn.endswith(IGNORE_SUFFIX):
        return None
    if fn.startswith(IGNORE_PREFIX):
        return None
    tokens = set(fn.split('_'))
    for cat, kws in RULES:
        for k in kws:
            if '_' in k or len(k) > 5:      # multi-word or long → substring
                if k in fn:
                    return cat
            else:                            # short single word → whole-token only
                if k in tokens:
                    return cat
    return None


def _scan():
    """List {app_label, model_name, field_name, category} for every personal-looking
    CONCRETE column (excludes reverse relations and M2M). Metadata only — no values."""
    out = []
    for model in apps.get_models():
        al = model._meta.app_label
        if al in ('admin', 'auth', 'contenttypes', 'sessions', 'authtoken', 'axes'):
            continue
        mn = model.__name__
        for f in model._meta.concrete_fields:
            name = getattr(f, 'name', None)
            if not name or getattr(f, 'primary_key', False):
                continue
            cat = _classify(name)
            if cat:
                out.append({'app_label': al, 'model_name': mn, 'field_name': name, 'category': cat})
    return out


class RopaFieldScanView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        detected = _scan()
        reviews = {
            (r.app_label, r.model_name, r.field_name): r
            for r in PiiFieldReview.objects.all()
        }
        rows = []
        counts = {'needs_review': 0, 'flagged': 0, 'confirmed': 0, 'dismissed': 0}
        for d in detected:
            key = (d['app_label'], d['model_name'], d['field_name'])
            r = reviews.get(key)
            if r:
                status = r.status
                note = r.note
                reviewed_by = r.reviewed_by
            else:
                status = PiiFieldReview.STATUS_FLAGGED if d['category'] in AUTO_FLAG else PiiFieldReview.STATUS_NEEDS
                note = ''
                reviewed_by = ''
            counts[status] = counts.get(status, 0) + 1
            rows.append({**d, 'status': status, 'note': note, 'reviewed_by': reviewed_by,
                         'reviewed': r is not None})
        # order: flagged, needs_review, then the rest
        pri = {'flagged': 0, 'needs_review': 1, 'confirmed': 2, 'dismissed': 3}
        rows.sort(key=lambda x: (pri.get(x['status'], 9), x['app_label'], x['model_name'], x['field_name']))
        return Response({
            'as_of': timezone.localdate().isoformat(),
            'summary': {
                'total_detected': len(detected),
                'needs_review': counts.get('needs_review', 0),
                'flagged': counts.get('flagged', 0),
                'confirmed': counts.get('confirmed', 0),
                'dismissed': counts.get('dismissed', 0),
            },
            'fields': rows,
        })

    def post(self, request):
        d = request.data
        al = (d.get('app_label') or '').strip()
        mn = (d.get('model_name') or '').strip()
        fn = (d.get('field_name') or '').strip()
        new_status = (d.get('status') or '').strip()
        valid = {v for v, _ in PiiFieldReview.STATUS_CHOICES}
        if not (al and mn and fn):
            return Response({'detail': 'app_label, model_name and field_name are required.'}, status=400)
        if new_status not in valid:
            return Response({'detail': f'status must be one of {sorted(valid)}.'}, status=400)
        cat = _classify(fn) or ''
        obj, _created = PiiFieldReview.objects.update_or_create(
            app_label=al, model_name=mn, field_name=fn,
            defaults={
                'category': cat,
                'status': new_status,
                'note': d.get('note', '')[:2000],
                'reviewed_by': getattr(request.user, 'username', '') or getattr(request.user, 'email', ''),
                'reviewed_at': timezone.now(),
            },
        )
        return Response({'ok': True, 'status': obj.status,
                         'field': f'{al}.{mn}.{fn}'})
