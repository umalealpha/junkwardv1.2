"""reinsurance/api_views.py — DRF endpoints."""

from decimal import Decimal, InvalidOperation
from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Currency
from . import services
from .models import (
    BordereauImport, Cession, Reinsurer,
    ReinsuranceRecovery, ReinsuranceTreaty,
)
from .serializers import (
    BordereauImportSerializer, CessionSerializer,
    ReinsurerSerializer, ReinsuranceRecoverySerializer,
    ReinsuranceTreatySerializer,
)
from .treaty_doc_parser import extract_text, parse_with_ai


def _val_to_resp(exc):
    if hasattr(exc, 'message_dict'):
        return Response(exc.message_dict, status=400)
    if hasattr(exc, 'messages'):
        return Response({'detail': exc.messages}, status=400)
    return Response({'detail': str(exc)}, status=400)


# ---------------------------------------------------------------------------
# Legacy reinsurer / treaty endpoints — READ-ONLY since 16-Sep-2026
# ---------------------------------------------------------------------------
# These two were plain ModelViewSets behind nothing but IsAuthenticated, so
# ANY signed-in user in the group could create, edit or DELETE a reinsurer or
# a treaty straight past the whole onboarding and approval chain in
# reinsurance/onboarding.py. The reinsurance control QC (bug-board
# 3885a3ec-5bc9-4880-87d0-fd01efcee2b5) logged that as a P0 "unsafe/broken
# side door", together with the HTTP 500 a create returned: ReinsurerSerializer
# still carries only the six original v1 fields, so a create wrote a row with
# no legal name, no domicile, no licence and approval_status=DRAFT, and the
# server then blew up in AuditableMixin — a half-made counterparty either way.
#
# CFO decision 16-Sep-2026, asked and answered: lock them to read-only now;
# the proper create/edit screens arrive with the onboarding batch, behind the
# four-person approval. Write access here is not fixed, it is REMOVED — the
# 500 cannot happen because the verb no longer exists (405 instead), and
# nothing in the frontend ever wrote through them (only two GETs, in
# reinsurance/treaties/page.tsx and lib/api.ts).
#
# READ is deliberately left on IsAuthenticated, exactly as it was. The QC also
# wants reading gated, but 're.view' is not granted to anybody yet — that role
# work is the next batch — so tightening read today would lock every user out
# of a page that works now, in order to close a hole that is about writes. The
# write verbs are the danger and the write verbs are what go.
class ReinsurerViewSet(mixins.ListModelMixin,
                       mixins.RetrieveModelMixin,
                       viewsets.GenericViewSet):
    queryset = Reinsurer.objects.all()
    serializer_class = ReinsurerSerializer
    permission_classes = [IsAuthenticated]


class ReinsuranceTreatyViewSet(mixins.ListModelMixin,
                               mixins.RetrieveModelMixin,
                               viewsets.GenericViewSet):
    queryset = ReinsuranceTreaty.objects.select_related('reinsurer', 'currency_code')
    serializer_class = ReinsuranceTreatySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        rein = self.request.query_params.get('reinsurer')
        if rein:
            qs = qs.filter(reinsurer_id=rein)
        return qs


class CessionViewSet(viewsets.ModelViewSet):
    queryset = Cession.objects.select_related(
        'treaty', 'treaty__reinsurer', 'journal_entry', 'posted_by',
    )
    serializer_class = CessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        treaty = self.request.query_params.get('treaty')
        if treaty:
            qs = qs.filter(treaty_id=treaty)
        return qs

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    @action(detail=True, methods=['post'])
    def post_cession(self, request, pk=None):
        cession = self.get_object()
        try:
            services.post_cession(cession, request.user)
        except DjangoValidationError as exc:
            return _val_to_resp(exc)
        return Response(self.get_serializer(cession).data)


class ReinsuranceRecoveryViewSet(viewsets.ModelViewSet):
    queryset = ReinsuranceRecovery.objects.select_related(
        'treaty', 'treaty__reinsurer', 'journal_entry', 'posted_by',
    )
    serializer_class = ReinsuranceRecoverySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        treaty = self.request.query_params.get('treaty')
        if treaty:
            qs = qs.filter(treaty_id=treaty)
        return qs

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    @action(detail=True, methods=['post'])
    def post_recovery(self, request, pk=None):
        recovery = self.get_object()
        try:
            services.post_recovery(recovery, request.user)
        except DjangoValidationError as exc:
            return _val_to_resp(exc)
        return Response(self.get_serializer(recovery).data)


class BordereauImportViewSet(viewsets.ModelViewSet):
    queryset = BordereauImport.objects.select_related('treaty', 'treaty__reinsurer')
    serializer_class = BordereauImportSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)


# ---------------------------------------------------------------------------
# Treaty document upload — AI-assisted parse → review → commit
# ---------------------------------------------------------------------------

def _resolve_reinsurer(code: str | None, name: str | None) -> Reinsurer | None:
    """Match an incoming candidate to an existing Reinsurer row.

    Strict — we never auto-create counterparties.
    """
    if code:
        r = Reinsurer.objects.filter(short_code__iexact=str(code).strip()).first()
        if r:
            return r
    if name:
        r = Reinsurer.objects.filter(name__icontains=str(name).strip()[:50]).first()
        if r:
            return r
    return None


class TreatyDocUploadParseView(APIView):
    """POST /api/v1/reinsurance/treaties/upload-parse/

    multipart/form-data with `file=<treaty doc>` (PDF/DOCX/XLSX/CSV).
    Returns AI-extracted treaty candidates the CFO can review + commit.
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        f = request.FILES.get('file')
        if not f:
            return Response({'detail': 'file is required.'}, status=400)
        if f.size > 25 * 1024 * 1024:
            return Response({'detail': 'File too large (limit 25 MB).'}, status=413)

        try:
            file_bytes = f.read()
        except Exception as e:  # noqa: BLE001
            return Response({'detail': f'Could not read file: {e}'}, status=400)

        text, kind = extract_text(f.name or '', file_bytes)
        if not text:
            return Response({
                'detail': 'Could not extract any text from the document. '
                          'Supported formats: .pdf, .docx, .xlsx, .csv',
                'doc_kind': kind,
            }, status=400)

        candidates, ai_used, _raw = parse_with_ai(text)

        # Annotate each candidate with a resolved reinsurer (if matchable)
        # so the UI can drop a confirmation pill.
        for c in candidates:
            r = _resolve_reinsurer(c.get('reinsurer_code'), c.get('reinsurer_name'))
            c['reinsurer_id']        = str(r.pk) if r else None
            c['resolved_reinsurer']  = r.name if r else None
            c['needs_reinsurer']     = r is None

        return Response({
            'candidates':        candidates,
            'doc_kind':          kind,
            'deepseek_used':     ai_used,
            'raw_text_preview':  text[:1500],
            'extracted_chars':   len(text),
        })


class TreatyDocUploadCommitView(APIView):
    """POST /api/v1/reinsurance/treaties/upload-commit/

    Body: { candidates: [ <edited candidate dict>, ... ] }
    Each candidate must have a resolved reinsurer_id by this point.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        candidates = (request.data or {}).get('candidates') or []
        if not isinstance(candidates, list) or not candidates:
            return Response({'detail': 'candidates list is required.'}, status=400)

        results: list[dict] = []
        errors:  list[dict] = []

        with transaction.atomic():
            for idx, c in enumerate(candidates):
                try:
                    treaty = self._upsert_one(c, request.user)
                    results.append({
                        'index': idx,
                        'id': str(treaty.pk),
                        'treaty_number': treaty.treaty_number,
                        'created': getattr(treaty, '_was_created', False),
                    })
                except Exception as e:  # noqa: BLE001
                    errors.append({
                        'index': idx,
                        'treaty_number': c.get('treaty_number'),
                        'error': str(e),
                    })

        return Response({
            'committed': results,
            'errors':    errors,
        })

    def _upsert_one(self, c: dict, user) -> ReinsuranceTreaty:
        treaty_number = (c.get('treaty_number') or '').strip()
        if not treaty_number:
            raise ValueError('treaty_number is required.')

        reinsurer_id = c.get('reinsurer_id')
        if not reinsurer_id:
            raise ValueError('reinsurer_id is required (resolve the reinsurer first).')
        try:
            reinsurer = Reinsurer.objects.get(pk=reinsurer_id)
        except Reinsurer.DoesNotExist:
            raise ValueError(f'Reinsurer {reinsurer_id} not found.')

        currency_code = (c.get('currency_code') or 'BWP').upper().strip()
        currency, _ = Currency.objects.get_or_create(
            code=currency_code,
            defaults={'name': currency_code, 'symbol': currency_code},
        )

        def _d(v):
            if v in (None, '', 'null'): return None
            try: return Decimal(str(v))
            except InvalidOperation: return None

        def _date(v):
            if not v: return None
            if isinstance(v, str):
                try: return datetime.strptime(v[:10], '%Y-%m-%d').date()
                except ValueError: return None
            return v

        treaty_type = (c.get('treaty_type') or '').strip()
        if treaty_type not in {tc.value for tc in ReinsuranceTreaty.TreatyType}:
            raise ValueError(f"treaty_type {treaty_type!r} is not one of "
                             f"quota_share|surplus|xl|stop_loss|facultative.")

        defaults = {
            'description':       (c.get('description') or '')[:200],
            'reinsurer':         reinsurer,
            'treaty_type':       treaty_type,
            'line_of_business':  (c.get('line_of_business') or '')[:80],
            'inception_date':    _date(c.get('inception_date')),
            'expiry_date':       _date(c.get('expiry_date')),
            'currency_code':     currency,
            'cession_share_percent': _d(c.get('cession_share_percent')),
            'commission_percent':    _d(c.get('commission_percent')),
            'retention_amount':      _d(c.get('retention_amount')),
            'limit_amount':          _d(c.get('limit_amount')),
            'notes':                 c.get('notes') or '',
            'status':                c.get('status') or 'draft',
        }
        if not defaults['inception_date'] or not defaults['expiry_date']:
            raise ValueError('inception_date and expiry_date are required.')

        treaty, was_created = ReinsuranceTreaty.objects.update_or_create(
            treaty_number=treaty_number, defaults=defaults,
        )
        treaty._was_created = was_created
        return treaty
