"""
iso_compliance/views.py — ISO 27001 REST endpoints.

  ── Executive 10-commandments dashboard ───────────────────────────────
  GET  /api/v1/iso/commandments/         list 10 commandments with findings
  GET  /api/v1/iso/runs/                  audit-run history
  POST /api/v1/iso/run-audit/             trigger a fresh scan (CFO/admin only)
  POST /api/v1/iso/findings/<id>/resolve  mark a finding resolved
  POST /api/v1/iso/findings/<id>/accept   CFO accepts the risk

  ── Auditor-grade artefacts (Statement of Applicability + friends) ────
  GET/PUT  /api/v1/iso/soa/               SoA controls (93 of them)
  GET/POST /api/v1/iso/risks/             Risk register (CRUD)
  GET/POST /api/v1/iso/capas/             CAPA workflow
  GET/POST /api/v1/iso/policies/          Policy register
  GET/POST /api/v1/iso/evidence/          Evidence
  GET/POST /api/v1/iso/internal-audits/   Internal-audit plan + outcomes
  GET/POST /api/v1/iso/management-reviews/ Management-review minutes

  ── Single-click auditor pack export ──────────────────────────────────
  GET  /api/v1/iso/auditor-pack/          ZIP containing every CSV (read-only)
  POST /api/v1/iso/seed-soa/              Seed/refresh the 93 controls (CFO)
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone as dt_tz

from django.core.management import call_command
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AuditFinding, AuditRun, Commandment,
    SoAControl, Risk, CAPA, Policy, Evidence,
    InternalAudit, ManagementReview,
)
from .serializers import (
    AuditFindingSerializer, AuditRunSerializer, CommandmentSerializer,
    SoAControlSerializer, RiskSerializer, CAPASerializer,
    PolicySerializer, EvidenceSerializer,
    InternalAuditSerializer, ManagementReviewSerializer,
)


def _can_run_audit(user) -> bool:
    """Allow superusers + anyone in the `cfo` or `iso_auditor` group."""
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['cfo', 'iso_auditor']).exists()


# ── Executive 10-commandments ─────────────────────────────────────────

class CommandmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Commandment.objects.prefetch_related('findings', 'soa_controls').order_by('number')
        latest = AuditRun.objects.order_by('-started_at').first()
        return Response({
            'commandments': CommandmentSerializer(qs, many=True).data,
            'latest_run': AuditRunSerializer(latest).data if latest else None,
            'overall_score': latest.score_pct if latest else 0,
        })


class AuditRunListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = AuditRun.objects.order_by('-started_at')[:20]
        return Response({'runs': AuditRunSerializer(qs, many=True).data})


class RunAuditView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _can_write_compliance_area(request.user):
            return Response(
                {'detail': 'Only the compliance function, the CFO or a superuser '
                           'can run audits.'},
                status=drf_status.HTTP_403_FORBIDDEN,
            )
        try:
            call_command('iso_audit', actor=request.user.username or 'unknown')
        except Exception as e:
            return Response({'detail': f'audit failed: {e!r}'},
                            status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
        latest = AuditRun.objects.order_by('-started_at').first()
        return Response(AuditRunSerializer(latest).data,
                        status=drf_status.HTTP_201_CREATED)


class _FindingActionView(APIView):
    permission_classes = [IsAuthenticated]
    new_state: str = ''
    #: Resolving a finding is compliance admin — the Compliance Manager closes
    #: her own findings. ACCEPTING one is not: this module's header calls it
    #: "CFO accepts the risk", which is the company deciding to live with an
    #: unfixed control. That stays with the CFO and superusers, deliberately,
    #: and was NOT included in the 2026-09-18 widening.
    compliance_may_do_it: bool = False

    def post(self, request, finding_id):
        allowed = (_can_write_compliance_area(request.user)
                   if self.compliance_may_do_it else _can_run_audit(request.user))
        if not allowed:
            return Response({'detail': 'Forbidden.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        try:
            f = AuditFinding.objects.get(pk=finding_id)
        except AuditFinding.DoesNotExist:
            return Response({'detail': 'not found'},
                            status=drf_status.HTTP_404_NOT_FOUND)
        f.state = self.new_state
        if self.new_state == AuditFinding.STATE_RESOLVED:
            f.resolved_at = timezone.now()
        f.save(update_fields=['state', 'resolved_at'])
        return Response(AuditFindingSerializer(f).data)


class ResolveFindingView(_FindingActionView):
    new_state = AuditFinding.STATE_RESOLVED
    compliance_may_do_it = True


class AcceptFindingView(_FindingActionView):
    new_state = AuditFinding.STATE_ACCEPTED


# ── Auditor-grade CRUD viewsets ───────────────────────────────────────

class _RWPermission(IsAuthenticated):
    """Anyone signed in can read; the compliance function may write; DELETE stays tight.

    HISTORY, because this class has been both ways and the comment outlived the
    code once already. On 2026-09-16 a widening was deliberately REVERSED here,
    leaving SoA, CAPA, Policy, Evidence, internal audits and management reviews
    on `_can_run_audit` — superusers plus the `cfo` and `iso_auditor` groups,
    and NEITHER GROUP EXISTS in the database. The effect was that the
    Compliance Manager could read her own module and write almost none of it.

    On 2026-09-18 the CFO reversed that again, in his words: "she should be
    able to do in the compliance areas she is the compliance manager." So
    writes now ask `_can_write_compliance_area`.

    DELETE is NOT part of that and still asks `_can_run_audit`. Note this is a
    thin guarantee on its own: PATCH is open and these models carry no audit
    trail, so a blanked row is unrecoverable either way. Raised with the CFO
    2026-09-18 rather than fixed silently.

    NOT covered by this class, deliberately: `AcceptFindingView` (the module
    header calls it "CFO accepts the risk") and the DPIA viewsets, which have
    their own `_DPIAWritePermission`.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        if request.method == 'DELETE':
            return _can_run_audit(request.user)
        return _can_write_compliance_area(request.user)


def _can_write_compliance_area(user) -> bool:
    """The one definition of "may maintain the compliance records".

    It is a function and not just the permission class because the bulk upload
    is a plain `@api_view` and cannot inherit one. They drifted the first time:
    `import_risks` shipped gated on `_can_run_audit` alone — the very right
    `_RiskRWPermission` exists to widen — so the compliance officer could add
    risks one at a time and got "Forbidden." on the spreadsheet (kbotana,
    bug d5388386, 2026-09-18). Any third caller asks this, not `_can_run_audit`.
    """
    from iso_compliance.aml_views import can_write_compliance_registers
    return _can_run_audit(user) or can_write_compliance_registers(user)


class SoAControlViewSet(viewsets.ModelViewSet):
    queryset = SoAControl.objects.select_related('commandment').order_by('clause')
    serializer_class = SoAControlSerializer
    permission_classes = [_RWPermission]
    lookup_field = 'clause'


class _RiskRWPermission(_RWPermission):
    """Risk register only: any signed-in user may DELETE (single or bulk).

    TEST ENV (2026-09-26, Unopa Male: "this is a test environment, forget all the
    security checks"): the register owner needs to freely remove rows entered in
    error or superseded ones. The shared `_RWPermission` keeps DELETE tight
    (`_can_run_audit`) for the OTHER compliance registers; only the risk register
    is opened here. Tighten this back to `_can_write_compliance_area` before any
    non-local deployment.
    """
    def has_permission(self, request, view):
        if request.method == 'DELETE':
            return bool(request.user and request.user.is_authenticated)
        return super().has_permission(request, view)


class RiskViewSet(viewsets.ModelViewSet):
    # DELETE widened to the compliance-area writer via _RiskRWPermission
    # (2026-09-26); the other registers keep the tighter shared _RWPermission.
    queryset = Risk.objects.prefetch_related('controls').order_by('ref')
    serializer_class = RiskSerializer
    permission_classes = [_RiskRWPermission]


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_delete_risks(request):
    """Delete several risks in one call. Body: {"ids": ["<id>", ...]} ->
    {"deleted": <count>}. TEST ENV: any signed-in user (see _RiskRWPermission),
    added for the register owner to clear multiple rows at once
    (Unopa Male, 2026-09-26)."""
    ids = request.data.get('ids')
    if not isinstance(ids, (list, tuple)) or not ids:
        return Response({'detail': 'Provide a non-empty "ids" list.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)
    matched = Risk.objects.filter(pk__in=ids)
    n = matched.count()
    matched.delete()
    return Response({'deleted': n})


# ── Risk register bulk upload (Unopa Male, 2026-09-18) ──────────────────
# The register lives as a spreadsheet on people's laptops. Uploading it
# creates one Risk row per data row, mapped like this:
#
#   Division / Source       → title
#   Risk Category           → asset
#   Risk Description        → description   (Threat + Vulnerability
#                                            merged into one field)
#   Risk Owner              → owner
#   Action / Mitigation Plan → treatment_plan
#
# Everything else on the spreadsheet — Risk ID column, Target Date, Status
# Flag, Inherent/Residual Score/Rating, Remarks — is IGNORED. Risk ID is
# always auto-generated (R-###). Manual-entry fields (Likelihood, Impact,
# Treatment, Status) default and are filled after the upload.

_HEADER_ALIASES = {
    'title': ('division', 'division/source', 'division / source', 'source'),
    'asset': ('risk category', 'category'),
    'description': ('risk description', 'description'),
    'owner': ('risk owner', 'owner'),
    'treatment_plan': (
        'action / mitigation plan', 'action/mitigation plan',
        'mitigation plan', 'action plan', 'treatment plan',
    ),
}


def _match_header(cell: str) -> str | None:
    key = (cell or '').strip().lower()
    if not key:
        return None
    for field, aliases in _HEADER_ALIASES.items():
        if key == field or key in aliases:
            return field
    return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_risks(request):
    """POST /api/v1/iso/risks/import/  (multipart `file`)

    Parses one .xlsx / .xls / .csv and creates a Risk per row. Returns
    {created, skipped, errors}. Rows with no Risk Description are
    skipped — the register would be empty without that field.
    """
    from .models import Risk
    from .serializers import next_risk_ref

    if not _can_write_compliance_area(request.user):
        return Response({'detail': 'Forbidden.'},
                        status=drf_status.HTTP_403_FORBIDDEN)

    up = request.FILES.get('file')
    if not up:
        return Response(
            {'detail': 'No file uploaded. Attach the Risk Register .xlsx.'},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    rows: list[list[str]] = []
    name = (up.name or '').lower()
    try:
        if name.endswith('.csv'):
            data = up.read().decode('utf-8-sig', errors='replace').splitlines()
            for row in csv.reader(data):
                rows.append([(c or '').strip() for c in row])
        else:
            import openpyxl
            wb = openpyxl.load_workbook(up, read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                rows.append([('' if c is None else str(c)).strip() for c in row])
    except Exception as exc:                                   # noqa: BLE001
        return Response(
            {'detail': f'Could not read the file: {exc}'},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    header_idx = None
    header_map: dict[int, str] = {}
    for i, row in enumerate(rows[:15]):
        m = {ix: f for ix, f in ((ix, _match_header(c)) for ix, c in enumerate(row)) if f}
        if 'description' in m.values():
            header_idx = i
            header_map = m
            break
    if header_idx is None:
        return Response(
            {'detail': 'Could not find a "Risk Description" column in the first 15 rows.'},
            status=drf_status.HTTP_400_BAD_REQUEST,
        )

    created = 0
    skipped = 0
    errors: list[str] = []
    for row in rows[header_idx + 1:]:
        payload = {header_map[ix]: (row[ix] if ix < len(row) else '').strip()
                   for ix in header_map}
        if not payload.get('description'):
            skipped += 1
            continue
        try:
            Risk.objects.create(ref=next_risk_ref(), **payload)
            created += 1
        except Exception as exc:                               # noqa: BLE001
            skipped += 1
            if len(errors) < 5:
                errors.append(f'Row skipped: {exc}')
    return Response({'created': created, 'skipped': skipped, 'errors': errors},
                    status=drf_status.HTTP_200_OK)


class CAPAViewSet(viewsets.ModelViewSet):
    queryset = CAPA.objects.select_related('finding', 'commandment').order_by('-created_at')
    serializer_class = CAPASerializer
    permission_classes = [_RWPermission]


class PolicyViewSet(viewsets.ModelViewSet):
    queryset = Policy.objects.prefetch_related('controls').order_by('code')
    serializer_class = PolicySerializer
    permission_classes = [_RWPermission]


class EvidenceViewSet(viewsets.ModelViewSet):
    queryset = Evidence.objects.select_related('control', 'commandment').order_by('-captured_at')
    serializer_class = EvidenceSerializer
    permission_classes = [_RWPermission]


class _AuditRecordRWPermission(_RWPermission):
    """Internal audits and management reviews — NOT widened, on purpose.

    ISO/IEC 27001 cl. 9.2 requires internal audits to be objective and
    impartial, and cl. 9.3 makes the management review top management's own
    record. Letting the audited function write its own audit record is the
    first thing an external auditor looks for. The CFO asked for "the
    compliance areas she is the compliance manager"; these two are the two
    records that are about her function rather than kept by it, so they stay
    where they were and the question went back to him (2026-09-18).
    """

    def has_permission(self, request, view):
        if not IsAuthenticated().has_permission(request, view):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return _can_run_audit(request.user)


class InternalAuditViewSet(viewsets.ModelViewSet):
    queryset = InternalAudit.objects.order_by('-scheduled_for', 'ref')
    serializer_class = InternalAuditSerializer
    permission_classes = [_AuditRecordRWPermission]


class ManagementReviewViewSet(viewsets.ModelViewSet):
    queryset = ManagementReview.objects.order_by('-review_date')
    serializer_class = ManagementReviewSerializer
    permission_classes = [_AuditRecordRWPermission]


# ── Seed SoA on demand ────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def seed_soa(request):
    if not _can_write_compliance_area(request.user):
        return Response({'detail': 'Forbidden.'},
                        status=drf_status.HTTP_403_FORBIDDEN)
    from .soa_seed import seed_soa as _seed
    created = _seed()
    return Response({'created': created,
                     'total': SoAControl.objects.count()},
                    status=drf_status.HTTP_201_CREATED)


# ── SoA progress dashboard ────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def soa_summary(request):
    qs = SoAControl.objects.all()
    total = qs.count()
    by_status = {s: qs.filter(status=s).count() for s, _ in SoAControl.STATUS_CHOICES}
    by_domain = {d: qs.filter(domain=d).count() for d, _ in SoAControl.DOMAINS}
    applicable_total = qs.filter(applicable=True).count()
    return Response({
        'total': total,
        'applicable': applicable_total,
        'excluded': total - applicable_total,
        'by_status': by_status,
        'by_domain': by_domain,
    })


# ── Auditor pack — ZIP of CSVs ────────────────────────────────────────

def _csv_bytes(rows: list[dict], headers: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow([r.get(h, '') for h in headers])
    return buf.getvalue().encode('utf-8')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def auditor_pack(request):
    """One-click ZIP suitable for handing to an external ISO 27001 auditor."""
    ts = timezone.now().strftime('%Y%m%d-%H%M%S')

    z_buf = io.BytesIO()
    with zipfile.ZipFile(z_buf, 'w', zipfile.ZIP_DEFLATED) as z:

        # 1. Statement of Applicability
        soa_rows = [{
            'clause':        c.clause,
            'title':         c.title,
            'domain':        c.get_domain_display(),
            'control_type':  c.get_control_type_display(),
            'applicable':    'YES' if c.applicable else 'NO',
            'justification': c.justification,
            'status':        c.get_status_display(),
            'owner':         c.owner,
            'evidence_ref':  c.evidence_ref,
            'last_reviewed_at': c.last_reviewed_at.isoformat() if c.last_reviewed_at else '',
        } for c in SoAControl.objects.order_by('clause')]
        z.writestr('01_statement_of_applicability.csv', _csv_bytes(soa_rows, [
            'clause', 'title', 'domain', 'control_type',
            'applicable', 'justification', 'status', 'owner',
            'evidence_ref', 'last_reviewed_at',
        ]))

        # 2. Risk register
        risk_rows = [{
            'ref': r.ref, 'title': r.title, 'asset': r.asset,
            'threat': r.threat, 'vulnerability': r.vulnerability,
            'likelihood': r.likelihood, 'impact': r.impact, 'score': r.score,
            'treatment': r.get_treatment_display(),
            'treatment_plan': r.treatment_plan,
            'owner': r.owner,
            'status': r.get_status_display(),
            'residual_score': r.residual_score or '',
            'created_at': r.created_at.isoformat(),
            'reviewed_at': r.reviewed_at.isoformat() if r.reviewed_at else '',
            'controls': ';'.join(r.controls.values_list('clause', flat=True)),
        } for r in Risk.objects.prefetch_related('controls').order_by('ref')]
        z.writestr('02_risk_register.csv', _csv_bytes(risk_rows, [
            'ref', 'title', 'asset', 'threat', 'vulnerability',
            'likelihood', 'impact', 'score',
            'treatment', 'treatment_plan', 'owner', 'status',
            'residual_score', 'controls', 'created_at', 'reviewed_at',
        ]))

        # 3. 10 commandments scorecard
        cmd_rows = [{
            'number': c.number,
            'title': c.title,
            'iso_clauses': c.iso_clauses,
            'status': c.get_status_display(),
            'owner': c.owner,
            'last_audited_at': c.last_audited_at.isoformat() if c.last_audited_at else '',
            'open_findings': c.findings.filter(state=AuditFinding.STATE_OPEN).count(),
        } for c in Commandment.objects.order_by('number')]
        z.writestr('03_commandments_scorecard.csv', _csv_bytes(cmd_rows, [
            'number', 'title', 'iso_clauses', 'status', 'owner',
            'last_audited_at', 'open_findings',
        ]))

        # 4. Findings (current & historical)
        finding_rows = [{
            'commandment': f.commandment.number,
            'severity': f.get_severity_display(),
            'state': f.get_state_display(),
            'title': f.title,
            'detail': f.detail,
            'fix_hint': f.fix_hint,
            'evidence': f.evidence,
            'detected_at': f.detected_at.isoformat(),
            'resolved_at': f.resolved_at.isoformat() if f.resolved_at else '',
        } for f in AuditFinding.objects.select_related('commandment').all()]
        z.writestr('04_findings.csv', _csv_bytes(finding_rows, [
            'commandment', 'severity', 'state', 'title', 'detail',
            'fix_hint', 'evidence', 'detected_at', 'resolved_at',
        ]))

        # 5. CAPA
        capa_rows = [{
            'ref': c.ref, 'title': c.title, 'status': c.get_status_display(),
            'finding': c.finding_id or '',
            'commandment': c.commandment.number if c.commandment else '',
            'nonconformity': c.nonconformity,
            'root_cause': c.root_cause,
            'corrective_action': c.corrective_action,
            'preventive_action': c.preventive_action,
            'owner': c.owner,
            'due_date': c.due_date.isoformat() if c.due_date else '',
            'verifier': c.verifier,
            'verified_at': c.verified_at.isoformat() if c.verified_at else '',
            'closed_at': c.closed_at.isoformat() if c.closed_at else '',
        } for c in CAPA.objects.select_related('commandment').order_by('-created_at')]
        z.writestr('05_capa_register.csv', _csv_bytes(capa_rows, [
            'ref', 'title', 'status', 'finding', 'commandment',
            'nonconformity', 'root_cause', 'corrective_action',
            'preventive_action', 'owner', 'due_date',
            'verifier', 'verified_at', 'closed_at',
        ]))

        # 6. Policies
        pol_rows = [{
            'code': p.code, 'title': p.title, 'version': p.version,
            'status': p.get_status_display(),
            'owner': p.owner, 'approver': p.approver,
            'approved_at': p.approved_at.isoformat() if p.approved_at else '',
            'review_due': p.review_due.isoformat() if p.review_due else '',
            'document_url': p.document_url,
            'controls': ';'.join(p.controls.values_list('clause', flat=True)),
        } for p in Policy.objects.prefetch_related('controls').order_by('code')]
        z.writestr('06_policies.csv', _csv_bytes(pol_rows, [
            'code', 'title', 'version', 'status', 'owner', 'approver',
            'approved_at', 'review_due', 'document_url', 'controls',
        ]))

        # 7. Evidence
        ev_rows = [{
            'label': e.label, 'description': e.description, 'url': e.url,
            'control': e.control.clause if e.control else '',
            'commandment': e.commandment.number if e.commandment else '',
            'finding': e.finding_id or '',
            'captured_by': e.captured_by,
            'captured_at': e.captured_at.isoformat(),
        } for e in Evidence.objects.select_related('control', 'commandment').order_by('-captured_at')]
        z.writestr('07_evidence_register.csv', _csv_bytes(ev_rows, [
            'label', 'description', 'url', 'control', 'commandment',
            'finding', 'captured_by', 'captured_at',
        ]))

        # 8. Internal audits
        ia_rows = [{
            'ref': a.ref, 'scope': a.scope,
            'lead_auditor': a.lead_auditor,
            'scheduled_for': a.scheduled_for.isoformat() if a.scheduled_for else '',
            'completed_at': a.completed_at.isoformat() if a.completed_at else '',
            'status': a.get_status_display(),
            'summary': a.summary,
            'findings_count': a.findings_count,
            'report_url': a.report_url,
        } for a in InternalAudit.objects.order_by('-scheduled_for', 'ref')]
        z.writestr('08_internal_audits.csv', _csv_bytes(ia_rows, [
            'ref', 'scope', 'lead_auditor', 'scheduled_for', 'completed_at',
            'status', 'summary', 'findings_count', 'report_url',
        ]))

        # 9. Management reviews
        mr_rows = [{
            'review_date': m.review_date.isoformat(),
            'chair': m.chair, 'attendees': m.attendees,
            'inputs_considered': m.inputs_considered,
            'decisions': m.decisions, 'action_items': m.action_items,
            'next_review_due': m.next_review_due.isoformat() if m.next_review_due else '',
            'minutes_url': m.minutes_url,
        } for m in ManagementReview.objects.order_by('-review_date')]
        z.writestr('09_management_reviews.csv', _csv_bytes(mr_rows, [
            'review_date', 'chair', 'attendees', 'inputs_considered',
            'decisions', 'action_items', 'next_review_due', 'minutes_url',
        ]))

        # 10. Audit runs (the in-system scan history)
        ar_rows = [{
            'started_at': a.started_at.isoformat(),
            'finished_at': a.finished_at.isoformat() if a.finished_at else '',
            'actor': a.actor,
            'findings_created': a.findings_created,
            'score_pct': a.score_pct,
            'notes': a.notes,
        } for a in AuditRun.objects.order_by('-started_at')]
        z.writestr('10_audit_runs.csv', _csv_bytes(ar_rows, [
            'started_at', 'finished_at', 'actor',
            'findings_created', 'score_pct', 'notes',
        ]))

        # 11. README cover
        latest = AuditRun.objects.order_by('-started_at').first()
        readme = (
            f'ISO/IEC 27001:2022 — Audit Evidence Pack\n'
            f'Generated: {timezone.now().isoformat()}\n'
            f'Subject: Alpha Direct Insurance Company (Pty) Ltd\n'
            f'System: omni.alphadirect.co.bw (alpha-finance ERP)\n'
            f'Pack version: {ts}\n\n'
            f'Latest internal audit score: {latest.score_pct if latest else "n/a"}%\n'
            f'Latest internal audit run:   {latest.finished_at.isoformat() if latest and latest.finished_at else "n/a"}\n\n'
            'Contents\n'
            '  01_statement_of_applicability.csv  — all 93 Annex A controls\n'
            '  02_risk_register.csv               — ISO/IEC 27005 risk rows\n'
            '  03_commandments_scorecard.csv      — 10 distilled commandments\n'
            '  04_findings.csv                    — automated-audit findings\n'
            '  05_capa_register.csv               — corrective + preventive actions\n'
            '  06_policies.csv                    — policy register\n'
            '  07_evidence_register.csv           — evidence links per control\n'
            '  08_internal_audits.csv             — internal-audit plan + results\n'
            '  09_management_reviews.csv          — clause 9.3 minutes\n'
            '  10_audit_runs.csv                  — in-system automated-scan history\n'
        )
        z.writestr('README.txt', readme.encode('utf-8'))

    z_buf.seek(0)
    resp = HttpResponse(z_buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="iso27001-audit-pack-{ts}.zip"'
    return resp


# == SOP Bank (ISO 9001 QMS) ===========================================
#
#   GET  /api/v1/iso/sops/                    searchable list (all staff)
#   GET  /api/v1/iso/sops/<uuid>/download/    original document
#   POST /api/v1/iso/sops/<uuid>/acknowledge/ "I have read + understood"
#   GET  /api/v1/iso/sops/coverage/           per-department ack coverage
#
# Everything requires login only - ISO 9001 wants every employee able to
# reach their SOPs; the acknowledgment trail is the training evidence.

from django.db.models import Count, Q as _Q
from django.http import FileResponse, Http404

from .models import SOPAcknowledgement, SOPDocument

# Who may upload documents through the UI: the CFO, superusers, or anyone in the
# `sop_uploader` group (the empowered HR + Finance staff, CFO directive
# 2026-07-27). Reading + acknowledging stays open to every authenticated staff
# member — only ADDING documents is gated.
_UPLOAD_EXTS = {'docx', 'pdf', 'doc', 'pptx', 'xlsx'}
_MAX_UPLOAD_BYTES = 40 * 1024 * 1024   # 40 MB — generous for a policy PDF


def _can_upload_sop(user) -> bool:
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['cfo', 'sop_uploader']).exists()


def _owns_draft(user, sop) -> bool:
    """Only the uploader who created a draft (or a superuser / the CFO) may
    confirm or discard it — so concurrent uploaders can't publish or delete
    each other's in-flight drafts."""
    if user.is_superuser or user.groups.filter(name='cfo').exists():
        return True
    return bool(sop.uploaded_by) and sop.uploaded_by == user.username


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sop_list(request):
    can_upload = _can_upload_sop(request.user)
    qs = SOPDocument.objects.all()
    status_f = (request.query_params.get('status') or 'active').strip().lower()
    if status_f != 'all':
        qs = qs.filter(status=status_f)
    # Drafts are unconfirmed uploads — never expose them (title, owner, file)
    # to ordinary staff, whatever ?status= they pass. Only uploaders see them.
    if not can_upload:
        qs = qs.exclude(status=SOPDocument.STATUS_DRAFT)
    dept = (request.query_params.get('department') or '').strip()
    if dept:
        qs = qs.filter(department__iexact=dept)
    dtype = (request.query_params.get('doc_type') or '').strip().lower()
    if dtype in ('sop', 'policy'):
        qs = qs.filter(doc_type=dtype)
    fit = (request.query_params.get('omni_fit') or '').strip().lower()
    if fit:
        qs = qs.filter(omni_fit=fit)
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(_Q(title__icontains=q) | _Q(content_text__icontains=q) |
                       _Q(owner__icontains=q) | _Q(sop_number__icontains=q))

    my_acks = set(SOPAcknowledgement.objects.filter(user=request.user)
                  .values_list('sop_id', flat=True))
    rows = []
    for s in qs.annotate(ack_count=Count('acknowledgements'))[:500]:
        rows.append({
            'id':             str(s.id),
            'department':     s.department,
            'doc_type':       s.doc_type,
            'sop_number':     s.sop_number,
            'title':          s.title,
            'owner':          s.owner,
            'revision_date':  str(s.revision_date) if s.revision_date else None,
            'iso_compliant':  s.iso_compliant,
            'file_type':      s.file_type,
            'size_bytes':     s.size_bytes,
            'status':         s.status,
            'omni_fit':       s.omni_fit,
            'omni_fit_notes': s.omni_fit_notes,
            'uploaded_by':    s.uploaded_by,
            'ack_count':      s.ack_count,
            'my_acknowledged': s.id in my_acks,
        })
    depts = (SOPDocument.objects.filter(status='active')
             .values('department').annotate(n=Count('id')).order_by('department'))
    active = SOPDocument.objects.filter(status='active')
    return Response({
        'count': len(rows),
        'rows': rows,
        'departments': [{'name': d['department'], 'count': d['n']} for d in depts],
        'doc_type_counts': {
            'sop': active.filter(doc_type='sop').count(),
            'policy': active.filter(doc_type='policy').count(),
        },
        'can_upload': can_upload,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sop_download(request, sop_id):
    sop = SOPDocument.objects.filter(pk=sop_id).first()
    if sop is None or not sop.file:
        raise Http404('SOP not found.')
    # An unconfirmed draft's file is only downloadable by its uploader
    # (or a superuser / the CFO) — never by ordinary staff who guess the id.
    if sop.status == SOPDocument.STATUS_DRAFT and not _owns_draft(request.user, sop):
        raise Http404('SOP not found.')
    try:
        fh = sop.file.open('rb')
    except FileNotFoundError:
        raise Http404('SOP file missing from media store.')
    import os as _os
    return FileResponse(fh, as_attachment=True,
                        filename=_os.path.basename(sop.file.name))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sop_acknowledge(request, sop_id):
    sop = SOPDocument.objects.filter(pk=sop_id).first()
    if sop is None:
        return Response({'detail': 'SOP not found.'}, status=404)
    ack, created = SOPAcknowledgement.objects.get_or_create(
        sop=sop, user=request.user)
    return Response({
        'id': str(sop.id),
        'acknowledged': True,
        'acknowledged_at': ack.acknowledged_at.isoformat(),
        'already': not created,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sop_coverage(request):
    """Per-department: SOP count, acknowledgment rows, distinct readers.
    The number BOBS-style auditors ask for: 'show me who has read these'."""
    from django.contrib.auth.models import User as _User
    active_staff = _User.objects.filter(is_active=True).count()
    out = []
    depts = (SOPDocument.objects.filter(status='active')
             .values_list('department', flat=True).distinct())
    for d in sorted(depts):
        sops = SOPDocument.objects.filter(status='active', department=d)
        acks = SOPAcknowledgement.objects.filter(sop__in=sops)
        out.append({
            'department':     d,
            'sops':           sops.count(),
            'acknowledgements': acks.count(),
            'distinct_readers': acks.values('user').distinct().count(),
        })
    total_sops = SOPDocument.objects.filter(status='active').count()
    total_acks = SOPAcknowledgement.objects.count()
    return Response({
        'active_staff': active_staff,
        'total_sops': total_sops,
        'total_acknowledgements': total_acks,
        'departments': out,
    })


# ── Self-service upload (empowered HR + Finance staff) ────────────────
#
#   POST /api/v1/iso/sops/upload/           multipart file → DeepSeek suggests
#                                           department + type; saved as DRAFT
#   POST /api/v1/iso/sops/<uuid>/confirm/   {department, doc_type, title} → active
#   POST /api/v1/iso/sops/<uuid>/discard/   delete an unconfirmed draft
#
# Two legs on purpose (CFO directive 2026-07-27): the AI reads and SUGGESTS,
# the uploader CONFIRMS in one tap, so nothing is ever filed to the wrong
# department silently. Drafts are invisible to normal staff (sop_list defaults
# to status='active'), so a half-finished upload never leaks into the library.

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sop_upload(request):
    """Accept a document, stash it as a DRAFT, and return DeepSeek's suggested
    department + doc_type for the uploader to confirm."""
    if not _can_upload_sop(request.user):
        return Response(
            {'detail': 'You are not authorised to upload documents. Ask the CFO '
                       'to add you to the document-library uploaders.'},
            status=drf_status.HTTP_403_FORBIDDEN)

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'No file attached (field name: file).'},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    import os as _os
    name = _os.path.basename(f.name or 'document')
    ext = _os.path.splitext(name)[1].lstrip('.').lower()
    if ext not in _UPLOAD_EXTS:
        return Response(
            {'detail': f'Unsupported file type ".{ext}". Allowed: '
                       + ', '.join(sorted(_UPLOAD_EXTS)) + '.'},
            status=drf_status.HTTP_400_BAD_REQUEST)
    if f.size and f.size > _MAX_UPLOAD_BYTES:
        return Response({'detail': 'File is larger than 40 MB.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    # Extract text (docx only — reuse the ingest helper). Other types classify
    # from the filename alone, which is usually enough for department + kind.
    content_text = ''
    if ext == 'docx':
        try:
            from iso_compliance.management.commands.ingest_sop_bank import extract_docx_text
            f.seek(0)
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name
            try:
                content_text = extract_docx_text(tmp_path)
            finally:
                _os.unlink(tmp_path)
        except Exception:   # noqa: BLE001 — extraction is best-effort
            content_text = ''

    from .classify import classify_document
    departments = list(SOPDocument.objects.filter(status='active')
                       .values_list('department', flat=True).distinct())
    suggestion = classify_document(content_text, name, departments)

    f.seek(0)
    sop = SOPDocument(
        department=suggestion['department'] or 'Unassigned',
        doc_type=suggestion['doc_type'],
        title=suggestion['title'] or _os.path.splitext(name)[0],
        file_type=ext,
        size_bytes=f.size or 0,
        content_text=content_text,
        status=SOPDocument.STATUS_DRAFT,
        omni_fit=SOPDocument.OMNI_FIT_UNREVIEWED,
        uploaded_by=request.user.username or '',
    )
    sop.file.save(name, f, save=False)
    sop.save()

    return Response({
        'id': str(sop.id),
        'suggestion': suggestion,
        'departments': sorted(set(departments)),
        'doc_type_choices': [c[0] for c in SOPDocument.DOC_TYPE_CHOICES],
        'file_type': ext,
        'size_bytes': sop.size_bytes,
    }, status=drf_status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sop_confirm(request, sop_id):
    """Confirm (or correct) a draft's department + type + title, publishing it."""
    if not _can_upload_sop(request.user):
        return Response({'detail': 'Forbidden.'}, status=drf_status.HTTP_403_FORBIDDEN)
    sop = SOPDocument.objects.filter(pk=sop_id).first()
    if sop is None:
        return Response({'detail': 'Document not found.'}, status=drf_status.HTTP_404_NOT_FOUND)
    if not _owns_draft(request.user, sop):
        return Response({'detail': 'You can only confirm a document you uploaded.'},
                        status=drf_status.HTTP_403_FORBIDDEN)
    if sop.status != SOPDocument.STATUS_DRAFT:
        return Response({'detail': 'Only a draft upload can be confirmed.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    department = (request.data.get('department') or '').strip()
    doc_type = (request.data.get('doc_type') or '').strip().lower()
    title = (request.data.get('title') or '').strip()
    if not department:
        return Response({'detail': 'Department is required.'},
                        status=drf_status.HTTP_400_BAD_REQUEST)
    if doc_type not in (SOPDocument.DOC_TYPE_SOP, SOPDocument.DOC_TYPE_POLICY):
        return Response({'detail': 'doc_type must be "sop" or "policy".'},
                        status=drf_status.HTTP_400_BAD_REQUEST)

    sop.department = department[:80]
    sop.doc_type = doc_type
    if title:
        sop.title = title[:255]
    sop.status = SOPDocument.STATUS_ACTIVE
    sop.save(update_fields=['department', 'doc_type', 'title', 'status', 'updated_at'])
    return Response({
        'id': str(sop.id),
        'department': sop.department,
        'doc_type': sop.doc_type,
        'title': sop.title,
        'status': sop.status,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sop_discard(request, sop_id):
    """Delete an unconfirmed draft (uploader cancelled)."""
    if not _can_upload_sop(request.user):
        return Response({'detail': 'Forbidden.'}, status=drf_status.HTTP_403_FORBIDDEN)
    sop = SOPDocument.objects.filter(pk=sop_id, status=SOPDocument.STATUS_DRAFT).first()
    if sop is None:
        return Response({'detail': 'Draft not found (already confirmed or removed).'},
                        status=drf_status.HTTP_404_NOT_FOUND)
    if not _owns_draft(request.user, sop):
        return Response({'detail': 'You can only discard a document you uploaded.'},
                        status=drf_status.HTTP_403_FORBIDDEN)
    try:
        sop.file.delete(save=False)
    except Exception:   # noqa: BLE001 — file already gone
        pass
    sop.delete()
    return Response({'discarded': True})


# ── DPO / DPIA register viewsets (CFO 2026-08-13) ─────────────────────
from .models import DPIA, DPIACondition, DPIARisk
from .serializers import DPIAConditionSerializer, DPIASerializer, DPIARiskSerializer
from core.dpa_dashboard import is_dpo, is_compliance_officer


def _can_write_dpia(user) -> bool:
    """Who may maintain the DPIA register: the DPO, the Compliance Officer, the
    CFO / ISO-audit roles, and superusers.

    CFO 2026-08-18 — the DPIA viewsets used _RWPermission (_can_run_audit: only
    superuser + cfo/iso_auditor groups). So the DPO (Oratile) and Compliance
    Officer (Kakale) could not sign their OWN slot or tick a condition — every
    write returned "You do not have permission" ("she can't click anything").
    Reuses the canonical is_dpo / is_compliance_officer resolvers, so this is a
    ROLE fix, not a grant to one account.
    """
    return _can_run_audit(user) or is_dpo(user) or is_compliance_officer(user)


class _DPIAWritePermission(IsAuthenticated):
    """Read for any authenticated user; write for the data-protection actors."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return _can_write_dpia(request.user)


class DPIAViewSet(viewsets.ModelViewSet):
    queryset = DPIA.objects.prefetch_related('conditions', 'risks').all()
    serializer_class = DPIASerializer
    permission_classes = [_DPIAWritePermission]

    def perform_update(self, serializer):
        now = timezone.now()
        instance = serializer.instance

        # Segregation that mirrors the three sign-off cards: a slot may only be
        # signed/unsigned by the matching role (superuser may act on any). The DPO
        # signs the DPO card, the Compliance Officer the compliance card, the CFO
        # the CFO card — nobody signs another's slot (CFO 2026-08-18).
        user = self.request.user
        vd = serializer.validated_data
        if not getattr(user, 'is_superuser', False):
            from rest_framework.exceptions import PermissionDenied

            def _guard(field, allowed, who):
                if (field in vd and vd.get(field) != getattr(instance, field)
                        and not allowed):
                    raise PermissionDenied(
                        f'Only the {who} can change the {who} sign-off.')
            _guard('dpo_signed', is_dpo(user), 'DPO')
            _guard('compliance_signed', is_compliance_officer(user), 'Compliance Officer')
            _guard('cfo_signed', _can_run_audit(user), 'CFO')

        if serializer.validated_data.get('dpo_signed') is True and instance.dpo_signed_at is None:
            instance.dpo_signed_at = now
        elif serializer.validated_data.get('dpo_signed') is False:
            instance.dpo_signed_at = None

        if serializer.validated_data.get('compliance_signed') is True and instance.compliance_signed_at is None:
            instance.compliance_signed_at = now
        elif serializer.validated_data.get('compliance_signed') is False:
            instance.compliance_signed_at = None

        if serializer.validated_data.get('cfo_signed') is True and instance.cfo_signed_at is None:
            instance.cfo_signed_at = now
        elif serializer.validated_data.get('cfo_signed') is False:
            instance.cfo_signed_at = None

        serializer.save()

    def perform_create(self, serializer):
        instance = serializer.save()
        now = timezone.now()
        changed = False
        if instance.dpo_signed and instance.dpo_signed_at is None:
            instance.dpo_signed_at = now
            changed = True
        if instance.compliance_signed and instance.compliance_signed_at is None:
            instance.compliance_signed_at = now
            changed = True
        if instance.cfo_signed and instance.cfo_signed_at is None:
            instance.cfo_signed_at = now
            changed = True
        if changed:
            instance.save(update_fields=['dpo_signed_at', 'compliance_signed_at', 'cfo_signed_at'])


class DPIAConditionViewSet(viewsets.ModelViewSet):
    queryset = DPIACondition.objects.select_related('dpia').all()
    serializer_class = DPIAConditionSerializer
    permission_classes = [_DPIAWritePermission]

    def perform_update(self, serializer):
        instance = serializer.instance

        if serializer.validated_data.get('done') is True and instance.done_at is None:
            instance.done_at = timezone.now()
        elif serializer.validated_data.get('done') is False:
            instance.done_at = None

        serializer.save()


class DPIARiskViewSet(viewsets.ModelViewSet):
    queryset = DPIARisk.objects.select_related('dpia').all()
    serializer_class = DPIARiskSerializer
    permission_classes = [_DPIAWritePermission]
