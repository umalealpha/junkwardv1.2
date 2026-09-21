"""
procurement/claims_api.py — Claims PO backend API (PHASE 5 of the port).

CFO 2026-07-06 claims-PO port: the DRF layer that glues the three earlier
phases together —

  Phase 1  claims_engine.py   (pure money math: split / allocations / excess)
  Phase 2  claims_models.py   (ClaimsAssessment persistence)
  Phase 3  claims_parser.py   (assessment-PDF -> report_dict)

Endpoints (registered as `claims-po` in alpha_finance/api_router.py):

  POST  /api/v1/claims-po/                  upload an assessment PDF; parses
                                            synchronously; returns id + status
  GET   /api/v1/claims-po/{id}/plan/        allocation plan + 2-PO split +
                                            saved inputs + claim meta
  PATCH /api/v1/claims-po/{id}/             save allocations / excess / markup /
                                            supplier settings / claim meta
  POST  /api/v1/claims-po/{id}/create-pos/  generate the DRAFT purchase orders

Hard rules (per CFO):
  * NO Odoo, NO FX — every PO is BWP. A per-supplier currency *choice* from
    supplier_settings / sa_supplier_default is recorded in po_results for the
    record, but nothing is ever converted and the PO currency is always BWP.
  * POs are created as DRAFT only. Submission/approval is the existing
    maker-checker workflow (procurement.services) — no money moves here.
  * Vendors are NEVER auto-created. A row resolves via its explicit
    vendor_id (a Contact PK picked on the review screen) first, else by
    label name-matching; a row that does not land on exactly one existing
    active billing.Contact (contact_type='vendor') comes back as an
    "unresolved" error row so the user picks a vendor on the review screen.
  * The excess is a NEGATIVE, no-VAT line on the repairer's PO. If the
    repairer label is unresolved the excess is NOT silently dropped — an
    explicit error row tells the user to assign the repairer.
"""

from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import CompanyScopedViewSetMixin, resolve_company_id_param
from .claims_engine import (
    _name_norm,
    _resolve_supplier_setting,
    apply_allocations,
    build_allocation_plan,
    claims_split,
    compute_excess,
    sa_supplier_default,
)
from .claims_models import ClaimsAssessment
from .serializers import PurchaseOrderCreateSerializer


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background pipeline (CFO 2026-07-08): the upload request returns instantly;
# the heavy parse + PO-generation runs in a daemon thread so the user gets a
# live progress bar instead of a frozen spinner. No Celery on this box — a
# thread is enough for a single-file, ~20s job, and each thread gets its own
# DB connection (closed at both ends).
# ---------------------------------------------------------------------------

class _BgReq:
    """Minimal request stand-in for the thread — create_pos only reads .user."""
    def __init__(self, user):
        self.user = user


def _set_stage(assessment, *, stage, pct, user, status_val=None, save_desc=''):
    assessment.progress_stage = stage
    assessment.progress_pct   = pct
    if status_val is not None:
        assessment.status = status_val
    assessment.save(audit_user=user, audit_description=save_desc or f'claims stage: {stage}')


def _run_claims_pipeline(assessment_id, user_id):
    """Parse the PDF, then auto-generate the two draft POs — updating
    progress_stage/pct + timing as it goes. Runs in its own thread."""
    from django.db import close_old_connections
    from django.contrib.auth import get_user_model
    close_old_connections()
    try:
        user = get_user_model().objects.filter(pk=user_id).first()
        a = ClaimsAssessment.objects.filter(pk=assessment_id).first()
        if a is None:
            return
        t0 = time.monotonic()

        # ── Parse ────────────────────────────────────────────────────────
        _set_stage(a, stage=ClaimsAssessment.Stage.PARSING, pct=30, user=user,
                   status_val=ClaimsAssessment.Status.PARSING, save_desc='parsing PDF')
        from . import claims_parser
        p0 = time.monotonic()
        try:
            report = claims_parser.parse(a.assessment_file.path)
        except Exception as exc:  # noqa: BLE001 — any parse failure is user-visible
            a.parse_error = str(exc)
            a.parse_ms = int((time.monotonic() - p0) * 1000)
            a.processing_finished_at = timezone.now()
            _set_stage(a, stage=ClaimsAssessment.Stage.FAILED, pct=100, user=user,
                       status_val=ClaimsAssessment.Status.PARSE_FAILED,
                       save_desc='parse failed')
            return
        a.parse_ms = int((time.monotonic() - p0) * 1000)
        a.report_json = report
        if not report.get('groups'):
            a.parse_error = ('No parts or labour lines could be extracted — this '
                             'does not look like a vehicle-assessment report.')
            a.processing_finished_at = timezone.now()
            _set_stage(a, stage=ClaimsAssessment.Stage.FAILED, pct=100, user=user,
                       status_val=ClaimsAssessment.Status.PARSE_FAILED,
                       save_desc='parse yielded no lines')
            return
        _autofill_meta(a, report)
        _set_stage(a, stage=ClaimsAssessment.Stage.GENERATING, pct=75, user=user,
                   status_val=ClaimsAssessment.Status.READY_FOR_REVIEW,
                   save_desc='parsed — generating POs')

        # ── Auto-generate the two draft POs ──────────────────────────────
        g0 = time.monotonic()
        try:
            ClaimsAssessmentViewSet().create_pos(_BgReq(user), _assessment=a)
            a.refresh_from_db()
        except Exception as exc:  # noqa: BLE001 — upload already succeeded
            log.warning('auto-generate POs failed for %s: %s', assessment_id, exc)
        a.generate_ms = int((time.monotonic() - g0) * 1000)
        a.total_ms = int((time.monotonic() - t0) * 1000)
        a.processing_finished_at = timezone.now()
        # status is whatever create_pos left (pos_created, or ready_for_review
        # if it flagged something) — the bar just says "done" either way.
        _set_stage(a, stage=ClaimsAssessment.Stage.DONE, pct=100, user=user,
                   save_desc='claims pipeline complete')
    except Exception:  # noqa: BLE001 — never let a thread die silently
        log.exception('claims pipeline crashed for %s', assessment_id)
        # Mark the row FAILED so the FE stops polling + offers a retry, instead
        # of leaving it stuck at 'parsing' forever (Fable audit 2026-07-08).
        try:
            a = ClaimsAssessment.objects.filter(pk=assessment_id).first()
            if a and a.progress_stage not in (
                    ClaimsAssessment.Stage.DONE, ClaimsAssessment.Stage.FAILED):
                a.parse_error = a.parse_error or 'Something went wrong while processing this file. Please try uploading it again.'
                a.status = ClaimsAssessment.Status.PARSE_FAILED
                a.progress_stage = ClaimsAssessment.Stage.FAILED
                a.progress_pct = 100
                a.processing_finished_at = timezone.now()
                a.save(audit_user=None, audit_description='claims pipeline crashed')
        except Exception:  # noqa: BLE001
            log.exception('failed to mark claims assessment %s as failed', assessment_id)
    finally:
        close_old_connections()

TWO_PLACES = Decimal('0.01')


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _f(value):
    """Decimal-or-None model field -> float-or-None for the pure engine."""
    return None if value is None else float(value)


def _dec(value) -> Decimal:
    """Engine float -> 2dp Decimal for PO line fields."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _err(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': detail}, status=code)


# Report-key -> ClaimsAssessment-field auto-fill map (upload step).
_REPORT_META_MAP = (
    ('claim_no',       'claim_number'),
    ('policy_no',      'policy_number'),
    ('assessment_no',  'assessment_number'),
    ('client_name',    'client_name'),
    ('vehicle',        'vehicle'),
    ('vehicle_reg',    'registration'),
    ('client_contact', 'contact_details'),
)

# ClaimsAssessment fields the review screen may PATCH.
_EDITABLE_META_FIELDS = [
    'claims_type', 'policy_number', 'claim_number', 'assessment_number',
    'claim_description', 'ad_note', 'client_name', 'registration',
    'vehicle', 'contact_details',
]
_EDITABLE_CONTROL_FIELDS = [
    'excess_pct', 'excess_min', 'excess_amount', 'markup_pct',
    'line_allocations', 'supplier_settings',
]


def _autofill_meta(assessment: ClaimsAssessment, report: dict) -> None:
    """Copy claim meta out of the parsed report onto the assessment row.

    Values are truncated to the target field's max_length — an over-long
    parsed value must degrade, not blow up the (post-parse) save."""
    for report_key, field in _REPORT_META_MAP:
        value = report.get(report_key)
        if value:
            value = str(value).strip()
            max_len = assessment._meta.get_field(field).max_length
            setattr(assessment, field, value[:max_len] if max_len else value)


def _meta_dict(assessment: ClaimsAssessment) -> dict:
    return {f: getattr(assessment, f) for f in _EDITABLE_META_FIELDS}


def _saved_inputs(assessment: ClaimsAssessment) -> dict:
    return {
        'line_allocations':  assessment.line_allocations,
        'supplier_settings': assessment.supplier_settings,
        'excess_pct':        _f(assessment.excess_pct),
        'excess_min':        _f(assessment.excess_min),
        'excess_amount':     _f(assessment.excess_amount),
        'markup_pct':        _f(assessment.markup_pct),
    }


def _standard_vat_taxrate():
    """The active 14% VAT TaxRate PO lines reference.

    Prefers the seeded VAT_STD code (core setup_initial_data: 'Standard Rate
    14%'); falls back to any active rate of exactly 14.00.
    """
    from core.models import TaxRate
    return (
        TaxRate.objects.filter(tax_code='VAT_STD', is_active=True).first()
        or TaxRate.objects
                  .filter(rate=Decimal('14.00'), is_active=True)
                  .order_by('tax_code')
                  .first()
    )


def _vendor_contact_pool(company):
    """Active vendor contacts in an assessment's company scope, with
    normalised names: active contact_type='vendor' contacts belonging to the
    assessment's company OR to no company (shared); with no company on the
    assessment, all active vendors are candidates. Sorted by name."""
    from django.db.models import Q
    from billing.models import Contact

    qs = Contact.objects.filter(contact_type='vendor', is_active=True)
    if company is not None:
        qs = qs.filter(Q(company=company) | Q(company__isnull=True))
    return [(_name_norm(c.name), c) for c in qs.order_by('name')]


def _canonical_contact(contacts):
    """Collapse same-name DUPLICATE vendor contacts to one deterministic pick.

    The vendor master carries many exact duplicates (e.g. 'MOTOR CENTRE' ×11).
    They are the same payee, so auto-generation must not stall on them (CFO
    directive 2026-07-08 — auto-generate, no exceptions). Prefer a duplicate
    that has an ACTIVE bank account (so the PO is actually payable), then the
    earliest-created, then lowest pk — stable across runs. Callers pass only
    contacts that share the same normalised name.
    """
    if len(contacts) == 1:
        return contacts[0]
    try:
        from procurement.models import VendorBankAccount
    except Exception:  # noqa: BLE001
        VendorBankAccount = None

    def _rank(c):
        has_bank = False
        if VendorBankAccount is not None:
            try:
                has_bank = VendorBankAccount.objects.filter(
                    contact=c, status='active').exists()
            except Exception:  # noqa: BLE001 — never block matching on this
                has_bank = False
        return (0 if has_bank else 1, str(getattr(c, 'created_at', '') or ''), str(c.pk))

    return sorted(contacts, key=_rank)[0]


def _match_vendor_label(label, pool):
    """One vendor label -> (Contact | None, status) against a
    _vendor_contact_pool, using claims_engine._name_norm on both sides:
      1. exact normalised match — same-name duplicates collapse to one
         canonical contact, so 'matched';
      2. else substring containment — if the matches are all the SAME
         normalised name (duplicates), collapse to one, 'matched';
      3. genuinely different candidates -> (None, 'ambiguous');
         none -> (None, 'unmatched').
    """
    norm = _name_norm(label)
    if not norm:
        return None, 'unmatched'
    exact = [c for n, c in pool if n == norm]
    if exact:
        # All share the same normalised name → duplicates of one vendor.
        return _canonical_contact(exact), 'matched'
    subs = [(n, c) for n, c in pool if n and (norm in n or n in norm)]
    if subs:
        distinct_names = {n for n, _ in subs}
        if len(distinct_names) == 1:
            return _canonical_contact([c for _, c in subs]), 'matched'
        return None, 'ambiguous'        # genuinely different vendors — flag
    return None, 'unmatched'


def _resolve_vendor_contacts(labels, company):
    """Map each vendor label -> exactly one active billing.Contact, or None
    (unresolved; user reassigns). Vendors are never auto-created. See
    _match_vendor_label for the matching rules and _vendor_contact_pool for
    the candidate scope."""
    pool = _vendor_contact_pool(company)
    return {label: _match_vendor_label(label, pool)[0] for label in labels}


def _row_vendor_contact(row, contact_map, company):
    """Resolve one allocation row to a vendor Contact for create-pos.

    An explicit ``vendor_id`` (a Contact PK picked on the review screen)
    wins when it maps to exactly one active vendor contact within the
    company scope — bypassing name matching, which is how an ambiguous
    label (two 'Rolling Wheels' vendor records — B915BEL) gets resolved.
    Otherwise fall back to the label name-matching. Never auto-creates.
    """
    vendor_id = row.get('vendor_id')
    if vendor_id:
        from django.core.exceptions import ValidationError
        from django.db.models import Q
        from billing.models import Contact

        try:
            # NB: an invalid-UUID pk raises ValidationError at .filter()
            # BUILD time (not .first()), so the query must be built inside
            # the try — a malformed vendor_id then falls back to the label.
            qs = Contact.objects.filter(
                pk=vendor_id, contact_type='vendor', is_active=True)
            if company is not None:
                qs = qs.filter(Q(company=company) | Q(company__isnull=True))
            contact = qs.first()
        except (ValueError, ValidationError):
            contact = None              # malformed PK — fall back to the label
        if contact is not None:
            return contact
    return contact_map.get(row['vendor'])


def _claim_justification(assessment: ClaimsAssessment) -> str:
    """The claim-metadata block stamped on every generated PO."""
    parts = [
        'Claims PO — auto-generated from vehicle assessment '
        f'{assessment.assessment_number or assessment.pk}.',
        f'Policy: {assessment.policy_number or "-"} | '
        f'Claim: {assessment.claim_number or "-"} | '
        f'Assessment: {assessment.assessment_number or "-"}',
        f'Client: {assessment.client_name or "-"} | '
        f'Registration: {assessment.registration or "-"} | '
        f'Vehicle: {assessment.vehicle or "-"}',
        f'Contact: {assessment.contact_details or "-"}',
    ]
    if assessment.claim_description:
        parts.append(f'Description: {assessment.claim_description}')
    if assessment.ad_note:
        parts.append(f'AD note: {assessment.ad_note}')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Serializer (detail / PATCH)
# ---------------------------------------------------------------------------

class ClaimsAssessmentSerializer(serializers.ModelSerializer):
    """Review-screen serializer. Writable = user inputs + claim meta only;
    pipeline state (status / report_json / po_results / file) is read-only."""

    status_display = serializers.CharField(source='get_status_display', read_only=True)
    stage_display  = serializers.CharField(source='get_progress_stage_display', read_only=True)
    eta_ms         = serializers.SerializerMethodField()
    progress_stage = serializers.SerializerMethodField()
    parse_error    = serializers.SerializerMethodField()

    # A run whose background worker died (deploy / recycle / crash) would sit at
    # 'parsing' forever and the FE would poll endlessly. Treat a still-processing
    # row older than this as failed so the FE stops + offers a retry (Fable audit).
    STALE_SECS = 300

    def _is_stale(self, obj):
        if not obj.is_processing or not obj.processing_started_at:
            return False
        from django.utils import timezone as _tz
        return (_tz.now() - obj.processing_started_at).total_seconds() > self.STALE_SECS

    def get_progress_stage(self, obj):
        return 'failed' if self._is_stale(obj) else obj.progress_stage

    def get_parse_error(self, obj):
        if self._is_stale(obj):
            return ('This is taking longer than expected — the file may not have '
                    'gone through. Please upload it again.')
        return obj.parse_error or None

    def get_eta_ms(self, obj):
        # Learned average duration — what the progress bar counts down against.
        return ClaimsAssessment.learned_eta_ms(company_id=obj.company_id)

    class Meta:
        model  = ClaimsAssessment
        fields = [
            'id', 'status', 'status_display', 'parse_error',
            'assessment_file', 'company', 'report_json', 'po_results',
            'progress_stage', 'stage_display', 'progress_pct', 'eta_ms',
            'parse_ms', 'generate_ms', 'total_ms',
            'processing_started_at', 'processing_finished_at',
            *_EDITABLE_META_FIELDS,
            *_EDITABLE_CONTROL_FIELDS,
            'created_at', 'updated_at',
        ]
        # NB: progress_stage + parse_error are declared SerializerMethodFields
        # above (stale-run override) — they are inherently read-only and must
        # NOT appear here or DRF raises at startup.
        read_only_fields = [
            'id', 'status', 'status_display',
            'assessment_file', 'company', 'report_json', 'po_results',
            'stage_display', 'progress_pct', 'eta_ms',
            'parse_ms', 'generate_ms', 'total_ms',
            'processing_started_at', 'processing_finished_at',
            'created_at', 'updated_at',
        ]


# ---------------------------------------------------------------------------
# ViewSet
# ---------------------------------------------------------------------------

class ClaimsAssessmentViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    """Claims PO pipeline: upload -> review -> two DRAFT purchase orders.

    Company-scoped and permission-gated exactly like PurchaseOrderViewSet
    (CompanyScopedViewSetMixin + IsAuthenticated)."""

    queryset = ClaimsAssessment.objects.select_related('company', 'created_by')
    serializer_class   = ClaimsAssessmentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]
    # No PUT (partial updates only) and no DELETE (audit trail stays).
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']

    # ------------------------------------------------------------------
    # 1. POST /claims-po/ — upload + synchronous parse
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        upload = request.FILES.get('file') or request.FILES.get('assessment_file')
        if upload is None:
            return _err("Attach the assessment PDF as multipart field 'file'.")

        # Company/entity (Fable audit 2026-07-08): the FE now sends the topbar
        # company on the upload. Prefer the explicit body value, then the
        # query/header/profile resolver. NEVER persist company=None — a
        # NULL-company assessment 404s the poll (the poll GET is topbar-scoped)
        # so the bar hangs forever, and re-uploading mints duplicate POs; and a
        # NULL-company PO is invisible to scoped approvers.
        from core.models import Company
        company = None
        raw = str(request.data.get('company') or '').strip()
        if raw:
            company = (Company.objects.filter(pk=raw).first()
                       if raw.count('-') >= 4 else
                       Company.objects.filter(code__iexact=raw).first())
        if company is None:
            cid = resolve_company_id_param(request)
            if cid:
                company = Company.objects.filter(pk=cid).first()
        if company is None:
            return _err("Pick a company/entity (top-right) before uploading the "
                        "assessment.")

        assessment = ClaimsAssessment(
            company=company,
            created_by=request.user,
            assessment_file=upload,
            status=ClaimsAssessment.Status.PARSING,
            progress_stage=ClaimsAssessment.Stage.UPLOADED,
            progress_pct=8,
            processing_started_at=timezone.now(),
        )
        assessment.save(audit_user=request.user,
                        audit_description='Claims assessment uploaded')

        # CFO 2026-07-08 (option B): DO NOT parse+generate in this request —
        # it can take ~20s and freezes the screen. Return immediately with an
        # ETA (learned from past runs); the parse + PO auto-generation run in
        # a background thread and the review screen shows a live progress bar
        # that polls this assessment until progress_stage == 'done'/'failed'.
        threading.Thread(
            target=_run_claims_pipeline,
            args=(assessment.pk, request.user.pk),
            daemon=True,
        ).start()

        return Response(
            {
                'id':             str(assessment.pk),
                'status':         assessment.status,
                'progress_stage': assessment.progress_stage,
                'progress_pct':   assessment.progress_pct,
                'eta_ms':         ClaimsAssessment.learned_eta_ms(
                                      company_id=assessment.company_id),
                'parse_error':    None,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------
    # 2. GET /claims-po/{id}/plan/ — everything the review screen renders
    # ------------------------------------------------------------------

    @action(detail=True, methods=['get'])
    def plan(self, request, pk=None):
        assessment = self.get_object()
        report = assessment.report_json
        if not report:
            return _err(
                'No parsed report on this assessment — upload succeeded but '
                f'parsing did not (status: {assessment.status}).',
                code=status.HTTP_409_CONFLICT,
            )

        markup_pct = _f(assessment.markup_pct)
        allocation_plan = build_allocation_plan(report, markup_pct)
        # claims_split takes excess_pct as a FRACTION (0.05); the model stores
        # a percent (5.00) — divide. compute_excess (below) takes the percent.
        split = claims_split(
            report,
            excess_pct=(None if assessment.excess_pct is None
                        else float(assessment.excess_pct) / 100.0),
            excess_min=_f(assessment.excess_min),
            markup_pct=markup_pct,
            excess_amount=_f(assessment.excess_amount),
        )
        # PARITY RULE: the excess the user SEES here must equal the excess
        # line create-pos will attach — so recompute it with the SAME function
        # and the SAME allocated rows create-pos uses, and override the
        # split's own figure when they differ (e.g. %-path with reassigned
        # rows, or no repairer row at all -> no excess anywhere).
        allocated = apply_allocations(allocation_plan, assessment.line_allocations)
        ex = compute_excess(
            report, allocated,
            _f(assessment.excess_pct),        # percent, e.g. 5.0
            _f(assessment.excess_min),
            _f(assessment.excess_amount),
        )
        actual_excess = round(ex['amount_incl'], 2) if ex else 0.0
        spec = split['specialised']
        if actual_excess != spec['excess']:
            spec['excess'] = actual_excess
            spec['excl']   = round(spec['gross_excl'] - actual_excess, 2)
            spec['incl']   = round(spec['gross_excl'] + spec['vat'] - actual_excess, 2)
            split['grand']['excl'] = round(spec['excl'] + split['motor_centre']['excl'], 2)
            split['grand']['incl'] = round(spec['incl'] + split['motor_centre']['incl'], 2)

        # Vendor picking (real contacts, not assessment labels): the full
        # in-scope vendor list + a per-row auto-match so the review screen
        # can pre-select rows and flag the ambiguous/unmatched ones. Uses
        # the SAME matching create-pos falls back to (_match_vendor_label).
        pool = _vendor_contact_pool(assessment.company)
        auto_cache: dict[str, tuple] = {}
        for plan_row, alloc_row in zip(allocation_plan['rows'], allocated):
            label = alloc_row['vendor']
            if label not in auto_cache:
                auto_cache[label] = _match_vendor_label(label, pool)
            contact, match_status = auto_cache[label]
            plan_row['auto_vendor_id'] = str(contact.pk) if contact else None
            plan_row['auto_status']    = match_status

        return Response({
            'id':     str(assessment.pk),
            'status': assessment.status,
            'plan':   allocation_plan,
            'split':  split,
            'saved':  _saved_inputs(assessment),
            'meta':   _meta_dict(assessment),
            'vendor_contacts': [
                {'id': str(c.pk), 'name': c.name} for _n, c in pool
            ],
        })

    # ------------------------------------------------------------------
    # 3. PATCH /claims-po/{id}/ — handled by ClaimsAssessmentSerializer
    # ------------------------------------------------------------------
    # (partial_update comes from ModelViewSet; writable fields are the
    #  user inputs + claim meta listed on the serializer.)

    # ------------------------------------------------------------------
    # 4. POST /claims-po/{id}/create-pos/ — THE generation (DRAFT only)
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='create-pos')
    def create_pos(self, request, pk=None, _assessment=None):
        # _assessment lets the upload step (create) auto-generate the POs
        # inline without re-fetching through get_object (CFO directive
        # 2026-07-08 — upload must create the two POs with no extra click).
        assessment = _assessment if _assessment is not None else self.get_object()
        if assessment.status == ClaimsAssessment.Status.POS_CREATED:
            return Response(
                {'detail': 'Purchase orders were already created for this assessment.',
                 'po_results': assessment.po_results},
                status=status.HTTP_409_CONFLICT,
            )
        report = assessment.report_json
        if not report:
            return _err('No parsed report on this assessment — cannot create POs.',
                        code=status.HTTP_409_CONFLICT)

        # a+b — pure engine math on the saved inputs.
        markup_pct = _f(assessment.markup_pct)
        allocated  = apply_allocations(
            build_allocation_plan(report, markup_pct),
            assessment.line_allocations,
        )
        if not allocated:
            return _err('The parsed report contains no allocatable lines — '
                        're-upload a readable assessment PDF.',
                        code=status.HTTP_409_CONFLICT)
        excess = compute_excess(
            report, allocated,
            _f(assessment.excess_pct),        # percent, e.g. 5.0
            _f(assessment.excess_min),
            _f(assessment.excess_amount),
        )

        # c — resolve each row to a Contact. A row's explicit vendor_id
        #     (picked on the review screen) wins; otherwise its vendor label
        #     is name-matched. Vendors are still NEVER auto-created.
        labels_in_order: list[str] = []
        for row in allocated:
            if row['vendor'] not in labels_in_order:
                labels_in_order.append(row['vendor'])
        contact_map = _resolve_vendor_contacts(labels_in_order, assessment.company)
        row_contacts = [
            (row, _row_vendor_contact(row, contact_map, assessment.company))
            for row in allocated
        ]

        results: list[dict] = []
        errors:  list[dict] = []

        # POs already created on a previous, partially-failed run: carry them
        # forward and NEVER raise a duplicate for the same supplier contact.
        prior_success = [r for r in (assessment.po_results or []) if r.get('id')]
        done_supplier_pks = {r['supplier_id'] for r in prior_success
                             if r.get('supplier_id')}

        # An excess with no repairer/labour row to carry it is NEVER silently
        # dropped (a typed amount or the assessment's own Excess figure).
        if excess is None:
            wanted = _f(assessment.excess_amount) or float(
                (report.get('summary') or {}).get('Excess') or 0)
            if wanted and wanted > 0:
                errors.append({
                    'error': (
                        f'This assessment carries an excess of P{wanted:,.2f} but has '
                        'no repairer/labour line to deduct it from — no PO would carry '
                        'the excess. Assign a repairer (labour row) or clear the excess, '
                        'then re-run.'
                    ),
                    'vendor': None,
                    'kind':   'excess',
                })

        # The repairer for the excess is the contact the LABOUR row resolves
        # to (vendor_id first, then label) — same per-row rule as the POs.
        repairer_label   = excess['vendor'] if excess else None
        repairer_contact = None
        if excess:
            repairer_contact = next(
                (c for row, c in row_contacts if row['id'] == 'labour'), None)
        if excess and repairer_contact is None:
            # NEVER silently drop the excess.
            errors.append({
                'error': (
                    f"Repairer '{repairer_label}' does not match a vendor contact, so "
                    f"the excess of P{excess['amount_incl']:,.2f} cannot be attached. "
                    "Assign the repairer to an existing vendor on the review screen "
                    "and re-run."
                ),
                'vendor': repairer_label,
                'kind':   'excess',
            })

        # Same-Contact rows merge into ONE PO bucket; unresolved rows flag
        # their label once (bad/ambiguous either way -> error row, no PO).
        buckets: dict = {}
        unresolved_labels: list[str] = []
        for row, contact in row_contacts:
            if contact is None:
                if row['vendor'] not in unresolved_labels:
                    unresolved_labels.append(row['vendor'])
                continue
            bucket = buckets.setdefault(
                contact.pk, {'contact': contact, 'labels': [], 'rows': []},
            )
            if row['vendor'] not in bucket['labels']:
                bucket['labels'].append(row['vendor'])
            bucket['rows'].append(row)
        for label in unresolved_labels:
            errors.append({
                'error': (
                    f"No single matching vendor contact for '{label}' — "
                    "assign this supplier on the review screen."
                ),
                'vendor': label,
            })

        vat_taxrate   = _standard_vat_taxrate()
        justification = _claim_justification(assessment)
        issue_date    = timezone.localdate().isoformat()

        # d — one DRAFT PO per bucket via PurchaseOrderCreateSerializer
        #     (inherits po_number generation, totals recalc, audit).
        for bucket in buckets.values():
            contact = bucket['contact']
            if str(contact.pk) in done_supplier_pks:
                # Draft PO for this supplier already exists from a previous
                # run of this assessment — never duplicate it. But if it is
                # the repairer PO and it was created WITHOUT the (since
                # typed) excess, that excess must not vanish silently.
                if excess and repairer_contact == contact and not any(
                    r.get('supplier_id') == str(contact.pk) and r.get('excess_attached')
                    for r in prior_success
                ):
                    errors.append({
                        'error': (
                            f"A draft PO for '{contact.name}' already exists from a "
                            f"previous run but does NOT carry the excess of "
                            f"P{excess['amount_incl']:,.2f} — add the excess line to "
                            'that PO manually before approval.'
                        ),
                        'vendor': contact.name,
                        'kind':   'excess',
                    })
                continue
            labels  = bucket['labels']
            kind    = ('repairer'
                       if any(r['id'] == 'labour' for r in bucket['rows'])
                       else 'parts')

            # Per-supplier VAT flag (settings -> SA-supplier default). The
            # currency CHOICE is recorded only — the PO stays BWP (no FX).
            default = sa_supplier_default(labels[0])
            vat_on, currency_choice = _resolve_supplier_setting(
                labels, assessment.supplier_settings,
                default['vat'], default['currency'],
            )
            if vat_on and vat_taxrate is None:
                errors.append({
                    'error': ('No active 14% VAT TaxRate (VAT_STD) is configured — '
                              f"cannot apply VAT on the PO for '{contact.name}'."),
                    'vendor': contact.name,
                    'kind':   kind,
                })
                continue

            lines = []
            for row in bucket['rows']:
                for ln in row['lines']:
                    lines.append({
                        'description': str(ln['description'])[:500],
                        'quantity':    _dec(ln.get('qty') or 1),
                        'unit_price':  _dec(ln['unit_price']),
                        'tax_code':    vat_taxrate.pk if vat_on else None,
                    })
            # Excess: flat NEGATIVE line, NO VAT, on the repairer's bucket.
            if excess and repairer_contact == contact:
                ex_line = excess['line']
                lines.append({
                    'description': str(ex_line['description'])[:500],
                    'quantity':    _dec(ex_line.get('qty') or 1),
                    'unit_price':  _dec(ex_line['unit_price']),
                    'tax_code':    None,
                })
                # Money guard: the excess may never turn the PO negative —
                # a negative draft payout is flagged, not created.
                total_incl = Decimal('0')
                for ln in lines:
                    lt = (ln['quantity'] * ln['unit_price']).quantize(
                        TWO_PLACES, rounding=ROUND_HALF_UP)
                    total_incl += lt
                    if ln['tax_code'] is not None:
                        total_incl += (lt * vat_taxrate.rate / Decimal('100')).quantize(
                            TWO_PLACES, rounding=ROUND_HALF_UP)
                if total_incl < 0:
                    errors.append({
                        'error': (
                            f"Excess P{excess['amount_incl']:,.2f} exceeds the value of "
                            f"the repairer PO for '{contact.name}' — the PO total would "
                            f"be negative (P{total_incl}). Reduce the excess or type "
                            'the correct amount, then re-run.'
                        ),
                        'vendor': contact.name,
                        'kind':   'excess',
                    })
                    continue

            po_serializer = PurchaseOrderCreateSerializer(
                data={
                    'department':              'claims',
                    'supplier':                contact.pk,
                    'company':                 assessment.company_id,
                    'issue_date':              issue_date,
                    'currency_code':           'BWP',   # no-FX rule — always BWP
                    'related_claim_reference': (assessment.claim_number or '')[:100],
                    'justification':           justification,
                    'lines':                   lines,
                },
                context={'request': request},
            )
            if not po_serializer.is_valid():
                errors.append({
                    'error':  f'PO validation failed: {po_serializer.errors}',
                    'vendor': contact.name,
                    'kind':   kind,
                })
                continue
            try:
                with transaction.atomic():
                    po = po_serializer.save()   # DRAFT by default — never submitted
            except Exception as exc:  # noqa: BLE001 — record, don't 500 the batch
                errors.append({
                    'error':  f'PO creation failed: {exc}',
                    'vendor': contact.name,
                    'kind':   kind,
                })
                continue

            results.append({
                'id':                str(po.pk),
                'po_number':         po.po_number,
                'supplier':          contact.name,
                'supplier_id':       str(contact.pk),   # re-run dedupe key
                'excess_attached':   bool(excess and repairer_contact == contact),
                'kind':              kind,
                'total':             str(po.total_amount),
                'currency':          'BWP',
                'supplier_currency_choice': currency_choice,  # recorded, never converted
                'status':            po.status,               # 'draft'
            })

        # e — persist the outcome. Any error leaves the assessment in
        # READY_FOR_REVIEW so the user can fix + re-run. POs created on an
        # earlier partial run stay on record (and are never re-raised).
        assessment.po_results = prior_success + results + errors
        if (results or prior_success) and not errors:
            assessment.status = ClaimsAssessment.Status.POS_CREATED
        assessment.save(
            audit_user=request.user,
            audit_description=(
                f'Claims PO generation: {len(results)} PO(s), {len(errors)} error(s)'
            ),
        )

        return Response(
            {
                'id':      str(assessment.pk),
                'status':  assessment.status,
                'results': results,
                'errors':  errors,
            },
            status=(status.HTTP_201_CREATED if results and not errors
                    else status.HTTP_200_OK),
        )
