"""
claims/api_views.py

REST API for Subrogation, Salvage, and the recovery-import workflow.

Imports are restricted to users with approval-eligible titles
(CFO / Finance Manager / Financial Controller). Non-approvers can read
the modules but cannot upload bulk data.
"""

from datetime import date, datetime
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import filters, status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Count, Q, Sum

from core.mixins import CompanyScopedViewSetMixin
from core.models import Company, get_user_profile, allowed_company_ids

from .loss_ratio import build_client_loss_ratio, build_large_loss_clients
from .models import (
    RecoveryImportBatch, Salvage, Subrogation,
    SubrogationPanel, SubrogationReceipt, SubrogationGLConfig,
)
from .realpay_import import import_realpay_rows, DEFAULT_MERCHANT
from .serializers import (
    RecoveryImportBatchSerializer, SalvageSerializer, SubrogationSerializer,
    SubrogationPanelSerializer, SubrogationReceiptSerializer,
)
from .services import (
    commit_salvage_import, commit_subrogation_import,
    parse_salvage_file, parse_subrogation_file,
    validate_salvage_rows, validate_subrogation_rows,
)


def _require_approver(request):
    profile = get_user_profile(request.user)
    is_su = bool(getattr(request.user, 'is_superuser', False))
    if is_su:
        return
    if profile is None or not profile.can_approve_journal_entries:
        raise PermissionDenied(
            'Bulk imports are restricted to CFO, Finance Manager, or Financial Controller.'
        )


class SubrogationViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Subrogation.objects.select_related(
        'company', 'third_party_contact', 'appointed_to', 'created_by'
    ).order_by('-created_at')
    serializer_class   = SubrogationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['claim_reference', 'third_party_name', 'third_party_insurer', 'notes']
    ordering_fields    = ['claim_reference', 'incident_date', 'last_recovery_date', 'created_at']

    def get_queryset(self):
        # CompanyScopedViewSetMixin handles entity isolation: with ?company=
        # it scopes to that company (403→none if not allowed); WITHOUT it,
        # it scopes to the caller's allowed_company_ids instead of leaking
        # all 12 entities (audit 2026-06-11 — was unfiltered when no param).
        # Annotate the receipts total once per row so the serializer's money
        # properties (amount_recovered / outstanding / recovery_pct) don't each
        # fire their own aggregate — was ~4 queries per row on the list (K5).
        qs = super().get_queryset().annotate(_receipts_total=Sum('receipts__amount'))
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    # ── B9: the demand letter ────────────────────────────────────────────
    @action(detail=True, methods=['get'], url_path='demand-letter')
    def demand_letter(self, request, pk=None):
        """Preview / download the letter of demand as a PDF. Read-only."""
        from django.http import HttpResponse
        from .subrogation_demand_pdf import build_demand_letter_pdf

        sub = self.get_object()          # get_queryset() scopes the company
        pdf = build_demand_letter_pdf(sub)
        name = f'demand-letter-{sub.claim_reference}.pdf'.replace('/', '-')
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{name}"'
        return resp

    @action(detail=True, methods=['post'], url_path='send-demand-letter')
    def send_demand_letter_action(self, request, pk=None):
        """Dispatch the letter to the third party — the action that clears
        Lindani's alert (Pako's gap 3: Omni sends it, she does not tick a box).

        The recipient address must be supplied or already on the case's contact;
        we never guess an address for outbound customer mail.
        """
        from .subrogation_alert import send_demand_letter
        from .subrogation_demand_pdf import build_demand_letter_pdf

        sub = self.get_object()
        to_email = (request.data.get('to_email') or '').strip()
        if not to_email and sub.third_party_contact_id:
            to_email = (sub.third_party_contact.email or '').strip()
        if not to_email:
            return Response(
                {'detail': 'An email address for the third party is required.'},
                status=drf_status.HTTP_400_BAD_REQUEST)
        if not sub.third_party_name:
            return Response(
                {'detail': 'Complete the third party name before sending the letter.'},
                status=drf_status.HTTP_400_BAD_REQUEST)

        send_demand_letter(sub, to_email=to_email,
                           pdf_bytes=build_demand_letter_pdf(sub))
        return Response(self.get_serializer(sub).data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Recoveries dashboard KPIs, company-scoped like the list.

        One number for the board (open / recovered / rate), plus the two piles
        that actually move money: the unassigned queue (cases nobody is chasing)
        and the prescription-at-risk pile (cases the 3-year clock is eating).
        Ageing and the counterparty scorecard round it out. Ordinary reads only
        — no writes, no GL.
        """
        ZERO = Decimal('0.00')
        qs = self.get_queryset()             # scoped + _receipts_total annotated
        today = timezone.localdate()
        fy_start = date(today.year if today.month >= 7 else today.year - 1, 7, 1)  # ADIC FY = Jul–Jun

        AGE_ORDER = ['0-90 days', '91-180 days', '181-365 days', '1-2 years',
                     'Over 2 years', 'No date appointed']
        ageing = {k: {'count': 0, 'balance': ZERO} for k in AGE_ORDER}
        presc = {}
        counterparties = {}
        total_recoverable = recovered_total = open_recoverable = ZERO
        open_count = unassigned_count = 0
        unassigned_balance = ZERO
        at_risk_count = 0
        at_risk_balance = ZERO

        for s in qs:
            total_recoverable += s.total_recoverable
            recovered_total += s.amount_recovered
            out = s.outstanding_balance
            if out <= Decimal('0.005'):
                continue
            open_count += 1
            open_recoverable += out
            lbl = s.age_bucket_label
            ageing.setdefault(lbl, {'count': 0, 'balance': ZERO})
            ageing[lbl]['count'] += 1
            ageing[lbl]['balance'] += out
            risk = s.prescription.risk.name
            p = presc.setdefault(risk, {'count': 0, 'balance': ZERO})
            p['count'] += 1
            p['balance'] += out
            if risk in ('EXPIRED', 'CRITICAL', 'URGENT'):
                at_risk_count += 1
                at_risk_balance += out
            if s.appointed_to_id is None:
                unassigned_count += 1
                unassigned_balance += out
            name = (s.appointed_to.name if s.appointed_to_id else None) or 'Unassigned'
            c = counterparties.setdefault(name, {'count': 0, 'balance': ZERO})
            c['count'] += 1
            c['balance'] += out

        # Recovered MTD / fiscal-YTD from the receipt records (scoped via parent).
        rec_qs = SubrogationReceipt.objects.filter(subrogation__in=qs)
        mtd = rec_qs.filter(received_date__gte=today.replace(day=1)).aggregate(t=Sum('amount'))['t'] or ZERO
        ytd = rec_qs.filter(received_date__gte=fy_start).aggregate(t=Sum('amount'))['t'] or ZERO

        rate = (recovered_total / total_recoverable * 100) if total_recoverable else ZERO

        top = sorted(counterparties.items(), key=lambda kv: kv[1]['balance'], reverse=True)[:5]

        def money(d):
            return str(d.quantize(Decimal('0.01')))

        return Response({
            'open_count': open_count,
            'open_recoverable': money(open_recoverable),
            'recovered_total': money(recovered_total),
            'recovered_mtd': money(Decimal(mtd)),
            'recovered_ytd': money(Decimal(ytd)),
            'recovery_rate': str(rate.quantize(Decimal('0.1'))),
            'unassigned': {'count': unassigned_count, 'balance': money(unassigned_balance)},
            'at_risk': {'count': at_risk_count, 'balance': money(at_risk_balance)},
            'ageing': [{'bucket': k, 'count': ageing[k]['count'], 'balance': money(ageing[k]['balance'])}
                       for k in AGE_ORDER if k in ageing],
            'prescription': {k: {'count': v['count'], 'balance': money(v['balance'])}
                             for k, v in presc.items()},
            'top_counterparties': [{'name': n, 'count': v['count'], 'balance': money(v['balance'])}
                                   for n, v in top],
        })


class SubrogationPanelViewSet(viewsets.ModelViewSet):
    """The approved who-collects register — shared across entities (a law firm
    is a firm regardless of which company's case it works)."""
    queryset           = SubrogationPanel.objects.all().order_by('name')
    serializer_class   = SubrogationPanelSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['name', 'contact_name', 'contact_email']
    ordering_fields    = ['name', 'kind', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            open_case_count=Count('subrogations', filter=~Q(
                subrogations__status=Subrogation.Status.FULLY_RECOVERED,
            )),
        )
        kind = self.request.query_params.get('kind')
        if kind:
            qs = qs.filter(kind=kind)
        active = self.request.query_params.get('active')
        if active in ('1', 'true', 'True'):
            qs = qs.filter(is_active=True)
        return qs

    # The panel is a CONTROL (the approved-collectors register), so reads are
    # open but changes are restricted to the same approvers as bulk imports —
    # otherwise any authenticated user could add/rename/deactivate a collector.
    def perform_create(self, serializer):
        _require_approver(self.request)
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_destroy(self, instance):
        _require_approver(self.request)
        instance.delete()


class SubrogationReceiptViewSet(viewsets.ModelViewSet):
    """Recording money collected against a subrogation — the CFO's explicit ask.

    Entity-isolated through the parent case's company, so a user only sees (and
    can only bank against) receipts for cases in a company they may access.
    """
    queryset           = SubrogationReceipt.objects.select_related(
        'subrogation', 'subrogation__company', 'created_by'
    ).order_by('-received_date', '-created_at')
    serializer_class   = SubrogationReceiptSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['subrogation__claim_reference', 'reference', 'realpay_txn_id']
    ordering_fields    = ['received_date', 'amount', 'created_at']

    def get_queryset(self):
        # Scope through the parent case's company, matching SubrogationViewSet's
        # STRICT isolation (a user with no grant sees nothing). Deliberately NOT
        # apply_company_scope() — that helper keeps a consolidated-HRIS leniency
        # the subrogation list does not, so the two would disagree about the
        # same user on the same case (the SEC-02 class of bug).
        qs = super().get_queryset()
        allowed = allowed_company_ids(self.request.user)
        if allowed != {'*'}:
            qs = qs.filter(subrogation__company_id__in=allowed)  # empty grant → empty qs
        sub = self.request.query_params.get('subrogation')
        if sub:
            qs = qs.filter(subrogation_id=sub)
        return qs

    def perform_create(self, serializer):
        # IDOR guard: the target case's company must be one the caller may access.
        # Reads are scoped in get_queryset, but `subrogation` is a writable FK, so
        # without this a scoped user could bank a receipt against another entity's
        # case by posting its id.
        sub = serializer.validated_data.get('subrogation')
        allowed = allowed_company_ids(self.request.user)
        if allowed != {'*'}:
            cid = str(sub.company_id) if sub and sub.company_id else None
            if cid is None or cid not in {str(a) for a in allowed}:
                raise PermissionDenied(
                    'You cannot record a payment against a case in another company.'
                )
        serializer.save(created_by=self.request.user)


class SubrogationGLConfigView(APIView):
    """The place to assign, by hand, the GL account a subrogation recovery
    credits. Read-open; writing restricted to the same approvers as imports.
    Saving the choice posts NO journal — GL posting is a separate, later step.
    """
    permission_classes = [IsAuthenticated]

    def _accounts(self):
        from ledger.models import Account
        return Account.objects.filter(
            account_type=Account.AccountType.REVENUE, is_active=True,
        ).order_by('code').values('id', 'code', 'name')

    def get(self, request):
        cfg = SubrogationGLConfig.get_solo()
        acct = cfg.recovery_income_account
        return Response({
            'recovery_income_account': str(acct.id) if acct else None,
            'recovery_income_account_code': acct.code if acct else None,
            'recovery_income_account_name': acct.name if acct else None,
            'revenue_accounts': list(self._accounts()),
        })

    def put(self, request):
        _require_approver(request)
        from ledger.models import Account
        cfg = SubrogationGLConfig.get_solo()
        acct_id = request.data.get('recovery_income_account')
        if acct_id in (None, '', 'null'):
            cfg.recovery_income_account = None
        else:
            acct = Account.objects.filter(
                id=acct_id, account_type=Account.AccountType.REVENUE, is_active=True,
            ).first()
            if acct is None:
                return Response({'error': 'Pick an active revenue account.'},
                                status=drf_status.HTTP_400_BAD_REQUEST)
            cfg.recovery_income_account = acct
        cfg.updated_by = request.user
        cfg.save()
        return self.get(request)


class SubrogationRealPayImportView(APIView):
    """The RealPay import space. POST a RealPay collections CSV; by default it
    PREVIEWS (dry run) and returns the counts. Pass commit=true to post.
    Posting is restricted to approvers. Only the Alpha Direct Third Parties
    merchant is imported; matching is ClientNumber → claim reference.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        import csv as _csv
        import io as _io
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': 'Attach the RealPay CSV as "file".'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        commit = str(request.data.get('commit', '')).lower() in ('1', 'true', 'yes')
        if commit:
            _require_approver(request)
        try:
            text = upload.read().decode('utf-8-sig', errors='replace')
            rows = list(_csv.DictReader(_io.StringIO(text)))
        except Exception:
            return Response({'error': 'Could not read that file as a CSV.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({'error': 'The CSV has no data rows.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        merchant = (request.data.get('merchant') or DEFAULT_MERCHANT)
        stats, posted_amount, unmatched = import_realpay_rows(
            rows, merchant=merchant, creator=request.user, commit=commit)
        return Response({
            'committed': commit,
            'merchant': merchant,
            'stats': stats,
            'posted_amount': str(posted_amount.quantize(Decimal('0.01'))),
            'unmatched_client_numbers': unmatched[:50],
            'unmatched_total': len(unmatched),
        })


class SalvageViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset           = Salvage.objects.select_related(
        'company', 'buyer_contact', 'created_by'
    ).order_by('-created_at')
    serializer_class   = SalvageSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['claim_reference', 'asset_description', 'buyer_name', 'notes']
    ordering_fields    = ['claim_reference', 'sale_date', 'incident_date', 'created_at']

    def get_queryset(self):
        # Entity isolation via CompanyScopedViewSetMixin — see Subrogation note.
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class RecoveryImportBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RecoveryImportBatch.objects.select_related('company', 'created_by').order_by('-created_at')
    serializer_class   = RecoveryImportBatchSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['file_name']

    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get('kind')
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    @action(
        detail=False, methods=['post'], url_path=r'preview/(?P<kind>subrogation|salvage)',
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def preview(self, request, kind=None):
        """
        POST /api/v1/recovery-imports/preview/subrogation/  multipart: file, company?
        POST /api/v1/recovery-imports/preview/salvage/      multipart: file, company?
        """
        _require_approver(request)
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': 'file is required'}, status=drf_status.HTTP_400_BAD_REQUEST)
        company = None
        company_id = request.data.get('company')
        if company_id:
            company = get_object_or_404(Company, pk=company_id)

        file_name = getattr(upload, 'name', '') or ''
        if kind == 'subrogation':
            rows, parse_errors = parse_subrogation_file(upload, file_name=file_name)
            validation_errors  = validate_subrogation_rows(rows)
        else:
            rows, parse_errors = parse_salvage_file(upload, file_name=file_name)
            validation_errors  = validate_salvage_rows(rows)
        all_errors = parse_errors + validation_errors

        bad_rows = {e['row_index'] for e in validation_errors if e['row_index'] != -1}
        rows_invalid = len(bad_rows)
        rows_valid   = max(0, len(rows) - rows_invalid)

        batch = RecoveryImportBatch.objects.create(
            kind=kind,
            file_name=file_name,
            rows_total=len(rows),
            rows_valid=rows_valid,
            rows_invalid=rows_invalid,
            parsed_rows=rows,
            validation_errors=all_errors,
            status=RecoveryImportBatch.Status.DRAFT,
            company=company,
            created_by=request.user,
        )
        batch.save(audit_user=request.user, audit_description=f'Previewed {len(rows)} rows ({kind})')
        return Response(RecoveryImportBatchSerializer(batch).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """POST /api/v1/recovery-imports/{id}/approve/

        Dual-authorisation: first call from one approver moves the batch
        to partially_approved; second call from a DIFFERENT approver moves
        it to approved (which then unlocks commit).
        """
        from core.dual_auth import record_approval
        batch = self.get_object()
        try:
            record_approval(
                batch, request.user,
                partial_status=RecoveryImportBatch.Status.PARTIALLY_APPROVED,
                approved_status=RecoveryImportBatch.Status.APPROVED,
            )
            batch.save(audit_user=request.user, audit_description='Approved recovery import')
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(RecoveryImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """POST /api/v1/recovery-imports/{id}/reject/   {reason: str}"""
        from core.dual_auth import record_rejection
        batch = self.get_object()
        reason = request.data.get('reason', '')
        try:
            record_rejection(
                batch, request.user, reason,
                rejected_status=RecoveryImportBatch.Status.REJECTED,
            )
            batch.save(audit_user=request.user, audit_description=f'Rejected: {reason}')
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(RecoveryImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def commit(self, request, pk=None):
        """POST /api/v1/recovery-imports/{id}/commit/

        Requires the batch to be APPROVED — both approvers have signed off.
        """
        from core.dual_auth import assert_ready_to_commit
        _require_approver(request)
        batch = self.get_object()
        try:
            assert_ready_to_commit(batch)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        if batch.rows_invalid > 0:
            return Response({
                'error': f'{batch.rows_invalid} row(s) have validation errors. '
                         'Fix the source file and re-preview before committing.',
            }, status=drf_status.HTTP_400_BAD_REQUEST)

        if batch.kind == RecoveryImportBatch.Kind.SUBROGATION:
            created, skipped, errors = commit_subrogation_import(batch, request.user)
        else:
            created, skipped, errors = commit_salvage_import(batch, request.user)

        return Response({
            'created':            created,
            'skipped_duplicates': skipped,
            'errors':             errors,
            'batch':              RecoveryImportBatchSerializer(batch).data,
        })


# ---------------------------------------------------------------------------
# Loss-ratio reports (CFO directive 2026-06-15)
# ---------------------------------------------------------------------------

def _qs_date(request, param):
    """Parse ?param=YYYY-MM-DD → date or None (silently ignores bad input)."""
    raw = (request.query_params.get(param) or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class ClientLossRatioView(APIView):
    """
    GET /api/v1/claims/loss-ratio/client/
        ?policy_number=...&customer=...&from=YYYY-MM-DD&to=YYYY-MM-DD

    Individual client / policy loss history: per-policy premium, incurred
    (paid + reserves) and loss ratio, plus the client's overall ratio.

    NOTE: premium is live from the Graphite payment feed; the incurred-loss
    side needs the Graphite claims feed — until then rows carry
    `claims_status='claims_feed_pending'` and incurred shows 0.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_client_loss_ratio(
            date_from=_qs_date(request, 'from'),
            date_to=_qs_date(request, 'to'),
            policy_number=(request.query_params.get('policy_number') or '').strip(),
            customer=(request.query_params.get('customer') or '').strip(),
        ))


class LargeLossClientsView(APIView):
    """
    GET /api/v1/claims/loss-ratio/large/
        ?threshold=70&min_premium=0&from=YYYY-MM-DD&to=YYYY-MM-DD

    Bad-risk watchlist: clients whose loss ratio ≥ threshold, worst first.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        def _f(p, d):
            try:
                return float(request.query_params.get(p, d))
            except (TypeError, ValueError):
                return d
        return Response(build_large_loss_clients(
            date_from=_qs_date(request, 'from'),
            date_to=_qs_date(request, 'to'),
            threshold_pct=_f('threshold', 70.0),
            min_premium=_f('min_premium', 0.0),
        ))
