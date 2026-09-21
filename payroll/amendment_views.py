"""
payroll/amendment_views.py — payroll amendments + generate-from-baseline.

CFO directive 2026-05-21: monthly payroll workflow is
   April baseline + uploaded amendments xlsx → May payroll.

Endpoints (auth-gated, approver-only for write actions):

    POST /api/v1/payroll/amendments/upload/
        multipart: file, target_period_id, baseline_period_id
        → parses xlsx, creates PayrollAmendmentBatch + PayrollAmendment rows
          in 'parsed' status. Caller previews the rows before applying.

    GET  /api/v1/payroll/amendment-batches/<id>/
        → batch detail with all amendment rows.

    POST /api/v1/payroll/amendment-batches/<id>/apply/
        → copies baseline payslips into target_period and applies
          every amendment row. Returns counts.

    POST /api/v1/payroll/periods/<id>/ai-review/
        → DeepSeek narrative + anomaly flags on the period's payslips.
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company, UserProfile, get_user_profile
from .models import (
    Employee, PayrollAmendment, PayrollAmendmentBatch,
    PayrollPeriod, Payslip, PayslipComponent, PayslipLine,
)


ZERO = Decimal('0.00')

# Canonical xlsx column → field mapping. CFO can rename headers in the
# template; the parser is case-insensitive and ignores leading/trailing
# whitespace.
COLUMN_ALIASES = {
    'employee':       {'employee', 'employee number', 'emp no', 'emp_no', 'employee_number', 'employee name'},
    'kind':           {'kind', 'amendment type', 'type'},
    'component':      {'component', 'component code', 'component_code', 'line item'},
    'amount':         {'amount', 'value', 'bwp', 'pula'},
    'reason':         {'reason', 'narrative', 'note', 'description'},
    'approver':       {'approver', 'approved by', 'sign off', 'signoff'},
}

VALID_KINDS = {choice[0] for choice in PayrollAmendment.Kind.choices}


# Named payroll-batch appliers who are NOT Finance-titled (CFO 2026-07-23:
# "not only Pako, Legakwa — Tshephang also"). Pako (FC) + Legakwa (FM) already
# qualify by title; this list adds anyone the CFO names regardless of title.
# Matched on the lowercased email local-part (exact or startswith), same style
# as the payroll-view list. Env-overridable without a deploy via
# OMNI_PAYROLL_APPLY_LOCAL_PARTS.
PAYROLL_APPLY_LOCAL_PARTS_DEFAULT = (
    'pkago',       # Pako Kago — Financial Controller (also by title)
    'lntabeni',    # Legakwa Ntabeni — Finance Manager (also by title)
    'tshephang',   # Tshephang Motswagae — named by CFO 2026-07-23
)


log = logging.getLogger('payroll.amendment')


def _payroll_apply_allowlist() -> set[str]:
    env = (os.environ.get('OMNI_PAYROLL_APPLY_LOCAL_PARTS') or '').strip()
    if env:
        return {p.strip().lower() for p in env.split(',') if p.strip()}
    return {x.lower() for x in PAYROLL_APPLY_LOCAL_PARTS_DEFAULT}


def _is_named_applier(user) -> bool:
    email = (getattr(user, 'email', '') or '')
    local = (email.split('@', 1)[0] if '@' in email else email).strip().lower()
    if not local:
        return False
    allow = _payroll_apply_allowlist()
    return local in allow or any(local.startswith(a) for a in allow)


def _can_apply_batch(user) -> bool:
    """Who may APPLY a payroll amendment batch: superuser, a Finance approver
    (FC / FM), or a CFO-named applier (PAYROLL_APPLY_LOCAL_PARTS). Applying only
    stages DRAFT payslips — the SoD that matters is the downstream approval +
    payment gate (see apply_amendment_batch)."""
    if getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is not None and profile.is_active and profile.title in (
        UserProfile.Title.FINANCIAL_CONTROLLER,
        UserProfile.Title.FINANCE_MANAGER,
    ):
        return True
    return _is_named_applier(user)


def _approver_only(request):
    """Apply-batch gate: Finance approver (FC / FM) OR a CFO-named applier
    (CFO 2026-07-23). Applying only stages DRAFT payslips; real SoD is
    downstream (payslip/period approval + posting authority + FNB payment)."""
    if not _can_apply_batch(request.user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(
            'Apply restricted to Finance (Financial Controller / Finance Manager) '
            'or a CFO-authorised payroll applier.'
        )


PAYROLL_VIEW_TITLES = frozenset({
    'cfo',
    'finance_manager',
    'financial_controller',
    'hr_manager',
})

# CFO directive 2026-06-16: a NAMED set of people see ALL payrolls across every
# entity, regardless of their HRIS title/department. Matched on the lowercased
# email local-part (exact OR startswith). Env-overridable without a deploy via
# OMNI_PAYROLL_VIEW_LOCAL_PARTS. Default = Unami, Dorothy, Kago, Pako, Legakwa,
# Prathap (CFO), Oprah.
PAYROLL_VIEW_LOCAL_PARTS_DEFAULT = (
    'pganesharajah',   # Prathap (CFO)
    'ubutale',         # Unami Butale
    'dikgopoleng',     # Dorothy Ikgopoleng
    'ktshutlhedi',     # Kago Tshutlhedi
    'pkago',           # Pako Kago
    'lntabeni',        # Legakwa Tsala Ntabeni
    'omogomotsi',      # Oprah Mogomotsi
)


def _payroll_view_allowlist() -> set[str]:
    env = (os.environ.get('OMNI_PAYROLL_VIEW_LOCAL_PARTS') or '').strip()
    if env:
        return {p.strip().lower() for p in env.split(',') if p.strip()}
    return {x.lower() for x in PAYROLL_VIEW_LOCAL_PARTS_DEFAULT}


def _is_named_payroll_viewer(user) -> bool:
    email = (getattr(user, 'email', '') or '')
    local = (email.split('@', 1)[0] if '@' in email else email).strip().lower()
    if not local:
        return False
    allow = _payroll_view_allowlist()
    return local in allow or any(local.startswith(a) for a in allow)


def user_can_view_payroll(user) -> bool:
    """Boolean form of the payroll-view gate (mirrors `_payroll_view` and the
    `payroll_access` probe). True for: superuser, administrator profile, an
    approver title in PAYROLL_VIEW_TITLES, or the HR department.

    Used by the `CanViewPayroll` DRF permission on the payroll viewsets so an
    ordinary employee can never LIST other people's pay or bank details.
    CFO directive 2026-06-16 (payroll-exposure hardening): the payslip /
    employee REST viewsets were `IsAuthenticated`-only and company-scoped,
    not user-scoped — any signed-in user could read everyone's payroll.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    # CFO directive 2026-06-16: named all-payroll viewers (Unami, Dorothy, Kago,
    # Pako, Legakwa, Prathap, Oprah) regardless of title/department.
    if _is_named_payroll_viewer(user):
        return True
    # CFO directive 2026-07-23: a named batch applier (e.g. Tshephang) must be
    # able to SEE the payroll batch screen in order to apply — if you can apply,
    # you can view. Keeps the apply grant usable end-to-end.
    if _is_named_applier(user):
        return True
    # CFO directive 2026-07-20 ("encrypt, but reveal with one button"): the
    # C-suite (CEO / COO / CFO) + HR team must be able to open an employee
    # record and reveal the encrypted bank / national-ID (bank-confirmation
    # letters, payments). is_hr_doc_admin is the canonical "C-suite + HR" lock
    # (#405) — OR it in so CEO / COO, who carry no payroll title, still pass.
    try:
        from hris.document_access import is_hr_doc_admin
        if is_hr_doc_admin(user):
            return True
    except Exception:  # noqa: BLE001 — never let an import hiccup deny access
        pass
    profile = get_user_profile(user)
    if profile is None:
        return False
    if getattr(profile, 'is_administrator', False):
        return True
    title = (getattr(profile, 'title', '') or '').lower()
    dept  = (getattr(profile, 'department', '') or '').lower()
    if title in PAYROLL_VIEW_TITLES:
        return True
    if 'hr' in dept or 'human res' in dept:
        return True
    # CFO directive 2026-06-16 (Lakshmi / ADRisk): an explicit HR_MANAGER / HRIS
    # role assignment also grants payroll-view — and CompanyScopedViewSetMixin
    # still clamps rows to the user's UserCompanyAccess grant, so an
    # entity-scoped HR user sees only their own entity's payroll.
    try:
        from core.models import UserRoleAssignment
        if UserRoleAssignment.objects.filter(
            user=user, role__code__in=['HR_MANAGER', 'HRIS'],
        ).exists():
            return True
    except Exception:        # noqa: BLE001
        pass
    return False


def _payroll_view(request):
    """CFO directive 2026-05-21: the Payroll page is for HR team / HR Manager /
    CFO / Finance Manager / Financial Controller only.

    Single source of truth = user_can_view_payroll() — superuser, named viewer,
    profile administrator, a payroll/HR title, the HR department, OR an
    HR_MANAGER / HRIS role assignment.

    CFO directive 2026-06-25: this gate previously checked only title/department
    and IGNORED the role layer, so a role-granted HR user (Tshephang, Veritas)
    could open the Payroll Register — whose API gate honours the role — but got a
    403 on the Payroll page, which didn't. The two must never diverge, so this
    delegates to the one function instead of re-deriving the rule.
    """
    from rest_framework.exceptions import PermissionDenied
    if not user_can_view_payroll(request.user):
        raise PermissionDenied(
            'Payroll restricted to HR team, HR Manager, CFO, and Finance Manager / Financial Controller.'
        )


def _normalise_header(s) -> str:
    return (str(s or '').strip().lower())


def _decimal(v) -> Decimal:
    if v in (None, ''):
        return ZERO
    try:
        return Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return ZERO


def _resolve_employee(ref: str, company: Company | None):
    """Resolve an employee reference to ONE Employee.

    Returns (employee|None, note). `note` is '' on a clean match, or a human
    message when the name can't be resolved — either "not found" or "ambiguous"
    (bug Pako 2026-07-23: 'Randy Taukobong' was matched only on EXACT full name,
    so 'Randy' and 'Taukobong' each returned not-found and both amendments
    failed). Match order: employee_number → exact full_name → unique fuzzy match
    (the ref is a substring / all its words appear in one full name). NEVER
    picks silently when more than one employee matches — the caller surfaces the
    note so the user can confirm.

    Terminated employees resolve ONLY on an EXACT identity match — employee
    number or exact full name (bug Emily Chilongo, RSA July 2026 — Legakwa
    2026-07-23): a FINAL SETTLEMENT (severance, leave pay, terminate) names the
    leaver precisely, and excluding terminated staff made every settlement row
    silently fail "employee not found". A FUZZY match (first name / surname /
    partial) still excludes leavers, so a bonus to "Randy" can never accidentally
    land on a former employee. Ambiguity is surfaced, never guessed.
    """
    ref = (ref or '').strip()
    if not ref:
        return None, ''
    # Archived staff (Terminated Employee Archive, 2026-08-13) are excluded
    # everywhere, including this exact-match fallback — final settlements are
    # for terminated-but-not-yet-archived leavers; once HR archives someone
    # there is no route back into a pay run short of unarchiving first.
    qs = Employee.objects.exclude(is_archived=True)
    if company is not None:
        qs = qs.filter(company=company)
    active = qs.exclude(status=Employee.Status.TERMINATED)
    # 1. exact employee number, 2. exact full name — active preferred, but a
    #    leaver resolves on an exact identity match (final settlements).
    emp = (active.filter(employee_number__iexact=ref).first()
           or qs.filter(employee_number__iexact=ref).first())
    if emp:
        return emp, ''
    emp = (active.filter(full_name__iexact=ref).first()
           or qs.filter(full_name__iexact=ref).first())
    if emp:
        return emp, ''
    # 3. fuzzy — the ref is a substring of a full name ('Randy'/'Taukobong' ->
    #    'Randy Taukobong'); or, for a multi-word ref, every word appears in one
    #    full name (handles reordering / extra spaces). Leavers are excluded here.
    cands = {e.id: e for e in active.filter(full_name__icontains=ref)}
    tokens = [t for t in ref.split() if t]
    if not cands and len(tokens) >= 2:
        q = active
        for t in tokens:
            q = q.filter(full_name__icontains=t)
        cands = {e.id: e for e in q}
    matches = list(cands.values())
    if len(matches) == 1:
        return matches[0], ''
    if len(matches) > 1:
        names = ', '.join(sorted(m.full_name for m in matches)[:5])
        return None, (f'employee {ref!r} is ambiguous — matches {len(matches)} '
                      f'people ({names}). Please give the exact name or employee number.')
    return None, f'employee {ref!r} not found'


def _resolve_component(code: str) -> PayslipComponent | None:
    if not code:
        return None
    return (
        PayslipComponent.objects.filter(code__iexact=code.strip()).first()
        or PayslipComponent.objects.filter(name__iexact=code.strip()).first()
    )


# ─── Access probe ──────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payroll_access(request):
    """GET /api/v1/payroll/access/ → {allowed: bool, can_apply: bool, reason}.
    Frontend calls this on /hris/payroll mount to decide whether to render
    the page or redirect. Mirrors the server-side gate so UI never lies.
    """
    profile = get_user_profile(request.user)
    is_super = getattr(request.user, 'is_superuser', False)
    is_admin = bool(profile and getattr(profile, 'is_administrator', False))
    title = (getattr(profile, 'title', '') or '').lower() if profile else ''
    dept  = (getattr(profile, 'department', '') or '').lower() if profile else ''
    can_apply = bool(profile and getattr(profile, 'can_approve_journal_entries', False))
    # A batch applier (FC/FM or a CFO-named applier) can always apply, and the
    # named applier is let onto the page even without a payroll-view title
    # (CFO 2026-07-23 — e.g. Tshephang).
    if _can_apply_batch(request.user):
        can_apply = True

    if is_super or is_admin:
        return Response({'allowed': True, 'can_apply': True, 'reason': 'administrator'})
    if title in PAYROLL_VIEW_TITLES:
        return Response({'allowed': True, 'can_apply': can_apply, 'reason': f'title:{title}'})
    if 'hr' in dept or 'human res' in dept:
        return Response({'allowed': True, 'can_apply': can_apply, 'reason': f'department:{dept}'})
    if _is_named_applier(request.user):
        return Response({'allowed': True, 'can_apply': True, 'reason': 'named-applier'})
    # Mirror the DATA gate exactly (user_can_view_payroll) so the frame guard never
    # bounces someone the payroll data endpoints actually allow — a CFO-named viewer,
    # the CEO/COO (HR-doc admins), or an HR_MANAGER/HRIS role (CFO brief 2026-08-26).
    if user_can_view_payroll(request.user):
        return Response({'allowed': True, 'can_apply': can_apply, 'reason': 'payroll-view'})
    return Response({'allowed': False, 'can_apply': False, 'reason': 'not in payroll access set'}, status=200)


def _read_sheet_rows(raw: bytes):
    """First worksheet as a list of row-lists. Uses python-calamine (one path
    for .xlsx/.xls/.xlsb/.ods) so an .xls no longer triggers openpyxl's
    "File is not a zip file"; falls back to openpyxl for .xlsx if calamine is
    absent. Returns None when the file cannot be read at all."""
    try:
        from python_calamine import CalamineWorkbook
        wb = CalamineWorkbook.from_filelike(io.BytesIO(raw))
        names = wb.sheet_names
        if not names:
            return []
        return wb.get_sheet_by_name(names[0]).to_python()
    except Exception:        # noqa: BLE001 — fall through to openpyxl
        pass
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    except Exception:        # noqa: BLE001
        return None


# ─── Shared helpers (used by both the xlsx upload and the AI text path) ──────

def _validate_period_pair(baseline_period, target_period):
    """Single source of the period-pair guard (Oprah report 2026-06-23):
    baseline must be a different, earlier period than the target. Returns an
    error Response, or None when the pair is valid."""
    if baseline_period.id == target_period.id:
        return Response(
            {'detail': 'Baseline and target must be different periods. '
                       'Pick the prior month as the baseline and the new month as the target.'},
            status=400,
        )
    if baseline_period.start_date >= target_period.start_date:
        return Response(
            {'detail': f'Baseline ({baseline_period.period_name}) must be an earlier period '
                       f'than the target ({target_period.period_name}). '
                       'Payroll rolls prior month → current month, not backwards.'},
            status=400,
        )
    return None


def _resolve_company(company_ref):
    company_ref = (company_ref or '').strip()
    if not company_ref:
        return None
    company = None
    if len(company_ref) == 36:
        try:
            company = Company.objects.filter(pk=company_ref).first()
        except (ValueError, ValidationError):   # 36 chars but not a valid UUID
            company = None
    return company or Company.objects.filter(code__iexact=company_ref).first()


def _add_amendment(batch, company, *, employee_ref, kind, amount,
                   component_ref='', reason='', approver=''):
    """Resolve + validate + persist ONE amendment row. Returns an error string
    for the caller's parse-errors list (row skipped), or '' on success. A
    created row may still carry a non-fatal `resolution_error` (employee /
    component not found) — that surfaces at apply time, not here."""
    emp_ref  = (employee_ref or '').strip()
    kind     = (kind or 'other').strip().lower().replace(' ', '_')
    comp_ref = (component_ref or '').strip()

    if not emp_ref and kind != 'hire':
        return 'empty employee.'
    if kind not in VALID_KINDS:
        return f'unknown kind {kind!r}.'

    emp, emp_note = _resolve_employee(emp_ref, company)
    comp = _resolve_component(comp_ref) if comp_ref else None

    res_err = ''
    if kind != 'hire' and emp is None:
        res_err = emp_note or f'employee {emp_ref!r} not found'
    elif kind in ('allowance_add', 'allowance_remove',
                  'deduction_add', 'deduction_remove') and comp is None:
        res_err = f'component {comp_ref!r} not found (required for {kind})'

    PayrollAmendment.objects.create(
        batch=batch, employee=emp, employee_ref=emp_ref, kind=kind,
        component=comp, amount=_decimal(amount), reason=(reason or '').strip()[:500],
        approver=(approver or '').strip(), resolution_error=res_err,
    )
    return ''


# ─── Upload + parse ─────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_amendments(request):
    """POST /api/v1/payroll/amendments/upload/

    multipart body:
      file              — .xlsx with the amendment rows
      target_period_id  — UUID of the period being CREATED (e.g. May 2026)
      baseline_period_id— UUID of the prior period (e.g. April 2026)
      company           — optional company UUID / code
    """
    # HR team prepares the file; apply still gated to approvers.
    _payroll_view(request)

    upload = request.FILES.get('file')
    if upload is None:
        return Response({'detail': 'Missing file.'}, status=400)
    target_id   = request.data.get('target_period_id') or ''
    baseline_id = request.data.get('baseline_period_id') or ''
    if not target_id or not baseline_id:
        return Response(
            {'detail': 'target_period_id and baseline_period_id are required.'},
            status=400,
        )

    target_period   = get_object_or_404(PayrollPeriod, pk=target_id)
    baseline_period = get_object_or_404(PayrollPeriod, pk=baseline_id)
    if target_period.status != PayrollPeriod.Status.OPEN:
        return Response({'detail': f'{target_period.period_name} is '
                         f'{target_period.get_status_display()} (locked) — re-open the period '
                         f'to load amendments into it.'}, status=409)

    # Period-pair guard (Oprah report 2026-06-23) — single-sourced.
    bad = _validate_period_pair(baseline_period, target_period)
    if bad is not None:
        return bad

    company = _resolve_company(request.data.get('company'))

    raw = upload.read()
    rows = _read_sheet_rows(raw)
    if rows is None:
        # BUG fix (Kago, report 874bae8a, 2026-06-22): the old path used openpyxl
        # (zip-only) and leaked "File is not a zip file" when an .xls was uploaded.
        # Now read via python-calamine (xlsx/xls/xlsb/ods); on a truly unreadable
        # file give a plain instruction instead of the raw error.
        return Response({'detail': "We couldn't read this file. Please open it in Excel, choose "
                                   "File → Save As → Excel Workbook (.xlsx), then upload that file."},
                        status=400)
    if not rows:
        return Response({'detail': 'This file looks empty. Upload the amendments sheet with a header row at the top.'},
                        status=400)

    header_row = rows[0]
    data_rows = rows[1:]
    header_idx: dict[str, int] = {}
    for idx, h in enumerate(header_row):
        norm = _normalise_header(h)
        for field, alts in COLUMN_ALIASES.items():
            if norm in alts:
                header_idx[field] = idx
                break

    missing = [f for f in ('employee', 'kind', 'amount') if f not in header_idx]
    if missing:
        return Response(
            {'detail': f'Missing required columns: {missing}. '
                       f'Found: {[_normalise_header(h) for h in header_row]}'},
            status=400,
        )

    # Re-open file storage for raw_xlsx
    upload.seek(0)

    with transaction.atomic():
        batch = PayrollAmendmentBatch.objects.create(
            target_period=target_period,
            baseline_period=baseline_period,
            company=company,
            file_name=upload.name,
            raw_xlsx=upload,
            uploaded_by=request.user,
            status=PayrollAmendmentBatch.Status.PARSED,
        )

        rows_created = 0
        errors = []
        for row_no, row in enumerate(data_rows, start=2):
            if not row or all(c in (None, '') for c in row):
                continue

            def cell(field):
                idx = header_idx.get(field)
                return row[idx] if idx is not None and idx < len(row) else None

            err = _add_amendment(
                batch, company,
                employee_ref=cell('employee'), kind=cell('kind') or 'other',
                amount=cell('amount'), component_ref=cell('component'),
                reason=cell('reason'), approver=cell('approver'),
            )
            if err:
                errors.append(f'row {row_no}: {err}')
                continue
            rows_created += 1

        batch.row_count = rows_created
        batch.notes = '\n'.join(errors[:50])
        batch.save(audit_user=request.user)

    return Response({
        'batch_id': str(batch.id),
        'rows_created': rows_created,
        'parse_errors': errors[:50],
        'status': batch.status,
    }, status=201)


# ─── AI: plain-English → amendments (Gemini) ─────────────────────────────────

def _parse_ai_rows(raw: str):
    """Tolerant JSON extraction: accepts {"rows":[...]}, a bare [...] list, or a
    fenced ```json block. Returns a list of dicts, or None if unreadable."""
    import json as _json
    import re as _re
    if not raw:
        return None
    s = _re.sub(r'^```(?:json)?|```$', '', raw.strip(), flags=_re.MULTILINE).strip()
    try:
        data = _json.loads(s)
    except ValueError:
        m = _re.search(r'(\{.*\}|\[.*\])', s, _re.DOTALL)
        if not m:
            return None
        try:
            data = _json.loads(m.group(1))
        except ValueError:
            return None
    if isinstance(data, dict):
        data = data.get('rows') or data.get('amendments') or []
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict)]


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def amendments_from_text(request):
    """POST /api/v1/payroll/amendments/from-text/

    Body: target_period_id, baseline_period_id, text, company?.
    Turns a plain-English description of the month's changes into structured
    amendment rows via Gemini, then stages them in a PARSED batch exactly like
    the xlsx upload (reviewed + Applied by an approver — this never applies
    anything). Hard PII (ID / bank / email / phone) is redacted by
    is_safe_for_ai() before the text leaves omni, and the user reviews every
    extracted row before Apply.
    """
    _payroll_view(request)

    text        = (request.data.get('text') or '').strip()
    target_id   = request.data.get('target_period_id') or ''
    baseline_id = request.data.get('baseline_period_id') or ''
    if not text:
        return Response({'detail': 'Describe the changes in plain English first.'}, status=400)
    if not target_id or not baseline_id:
        return Response({'detail': 'target_period_id and baseline_period_id are required.'}, status=400)

    target_period   = get_object_or_404(PayrollPeriod, pk=target_id)
    baseline_period = get_object_or_404(PayrollPeriod, pk=baseline_id)
    if target_period.status != PayrollPeriod.Status.OPEN:
        return Response({'detail': f'{target_period.period_name} is '
                         f'{target_period.get_status_display()} (locked) — re-open the period '
                         f'to load amendments into it.'}, status=409)
    bad = _validate_period_pair(baseline_period, target_period)
    if bad is not None:
        return bad
    company = _resolve_company(request.data.get('company'))

    # PII gate — redact IDs / bank / email / phone before the text leaves omni.
    from core.ai_assist import is_safe_for_ai, reasoning_complete, DeepSeekUnavailable
    report = is_safe_for_ai(text)
    if not report.safe:
        return Response({'detail': 'That text looks dominated by ID / bank numbers. Remove them '
                                   'and describe only the changes (name, what changed, amount).',
                         'notes': report.notes}, status=400)

    system = (
        "You convert a Botswana payroll manager's plain-English description of a month's "
        "payroll changes into structured rows. Output ONLY a JSON object {\"rows\": [...]}. "
        "Each row has: employee (the person's name as written, or \"\" for a generic / "
        "everyone line), kind (one of: hire, terminate, salary_change, allowance_add, "
        "allowance_remove, deduction_add, deduction_remove, bonus, overtime, arrears, "
        "tax_override, other), amount (a plain number in Pula; 0 if none or if it shows as "
        "[AMOUNT-REDACTED]), component (e.g. \"fuel allowance\" for allowance/deduction rows, "
        "else \"\"), reason (short free text). Use salary_change for a new salary, hire for a "
        "new joiner, terminate for a leaver. Do not invent people. If an amount was redacted, "
        "set amount 0 and say so in reason. "
        "CRITICAL: a person's FULL NAME is ONE employee — put the whole name "
        "(first name AND surname, e.g. \"Randy Taukobong\") in the employee field of a "
        "SINGLE row. NEVER split a first name and surname into two separate rows or two people."
    )
    # Gemini first, DeepSeek fallback (CFO's reasoning chain) — so a transient
    # Gemini 503 doesn't take the feature down. Pass only system_prompt (the kwarg
    # every engine accepts). Raises DeepSeekUnavailable only if ALL engines fail.
    try:
        raw = reasoning_complete(report.redacted_text, system_prompt=system)
    except DeepSeekUnavailable:
        return Response({'detail': 'AI is unavailable right now. You can still upload an Excel '
                                   'file in Step 2.'}, status=503)

    rows = _parse_ai_rows(raw)
    if rows is None:
        return Response({'detail': 'The AI returned something we could not read. Rephrase, or '
                                   'upload an Excel file instead.'}, status=502)

    with transaction.atomic():
        batch = PayrollAmendmentBatch.objects.create(
            target_period=target_period, baseline_period=baseline_period, company=company,
            file_name='AI — plain-English changes', uploaded_by=request.user,
            status=PayrollAmendmentBatch.Status.PARSED,
        )
        rows_created = 0
        errors: list[str] = []
        ai_rows: list[dict] = []
        for i, r in enumerate(rows, start=1):
            ai_rows.append({
                'employee':  str(r.get('employee') or '').strip(),
                'kind':      str(r.get('kind') or 'other').strip().lower().replace(' ', '_'),
                'amount':    str(r.get('amount') if r.get('amount') is not None else 0),
                'component': str(r.get('component') or '').strip(),
                'reason':    str(r.get('reason') or '').strip(),
            })
            err = _add_amendment(
                batch, company,
                employee_ref=r.get('employee'), kind=r.get('kind') or 'other',
                amount=r.get('amount'), component_ref=r.get('component'),
                reason=r.get('reason'),
            )
            if err:
                errors.append(f'item {i}: {err}')
                continue
            rows_created += 1
        batch.row_count = rows_created
        note = '\n'.join(errors[:50])
        if report.redactions_made:
            note = (f'{report.redactions_made} sensitive value(s) redacted before AI. ' + note).strip()
        batch.notes = note
        batch.save(audit_user=request.user)

    return Response({
        'batch_id':        str(batch.id),
        'rows_created':    rows_created,
        'parse_errors':    errors[:50],
        'status':          batch.status,
        'ai_rows':         ai_rows,
        'redactions_made': report.redactions_made,
    }, status=201)


# ─── Apply ─────────────────────────────────────────────────────────────────

def _is_finance_approver(user) -> bool:
    """Who may apply a batch (used to compute list `can_apply` without raising):
    Finance approver (FC/FM) or a CFO-named applier. Mirrors _approver_only."""
    return _can_apply_batch(user)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_amendment_batches(request):
    """GET /api/v1/payroll/amendment-batches/ — PARSED batches awaiting apply.

    Without this a batch was only reachable in the session that uploaded it, and
    the uploader is SoD-blocked from applying (SoD #4) — so a batch could get
    stuck with nobody able to apply it. This lets a DIFFERENT finance approver
    (FC / FM) see the pending batch and apply it (CFO 2026-07-23)."""
    _payroll_view(request)
    me = request.user
    is_appr = _is_finance_approver(me)
    qs = (PayrollAmendmentBatch.objects
          .filter(status=PayrollAmendmentBatch.Status.PARSED)
          .select_related('target_period', 'baseline_period', 'uploaded_by')
          .order_by('-created_at'))
    rows = []
    for b in qs[:100]:
        up = b.uploaded_by
        mine = bool(up and up.id == me.id)
        rows.append({
            'id':              str(b.id),
            'target_period':   b.target_period.period_name if b.target_period_id else '',
            'baseline_period': b.baseline_period.period_name if b.baseline_period_id else '',
            'row_count':       b.row_count,
            'file_name':       b.file_name,
            'uploaded_by':     (up.get_full_name() or up.username) if up else '',
            'uploaded_by_me':  mine,
            'created_at':      b.created_at.isoformat(),
            # Any finance approver (FC/FM) may apply — incl. the uploader
            # (CFO 2026-07-23: staging SoD moved downstream).
            'can_apply':       is_appr,
        })
    return Response({'batches': rows, 'is_finance_approver': is_appr})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def preflight_amendment_batch(request, batch_id):
    """Read-only pre-flight before Apply (CFO 2026-08-28): what WOULD change and
    what looks wrong, without touching a single payslip. Movement figures are an
    ESTIMATE (the exact PAYE/net recompute happens at Apply); the exceptions are
    exact. Payroll authority only."""
    _approver_only(request)
    batch  = get_object_or_404(PayrollAmendmentBatch, pk=batch_id)
    target = batch.target_period
    baseline = batch.baseline_period

    baseline_ps = list(Payslip.objects.filter(period=baseline)
                       .exclude(status=Payslip.Status.CANCELLED)
                       .select_related('employee'))
    baseline_ids = {p.employee_id for p in baseline_ps}
    baseline_gross = sum((p.gross_amount or 0) for p in baseline_ps)

    amds = list(batch.amendments.select_related('employee', 'component'))
    K = PayrollAmendment.Kind
    per_kind: dict = {}
    for a in amds:
        per_kind[a.kind] = per_kind.get(a.kind, 0) + 1

    unresolved = [{'ref': a.employee_ref, 'reason': a.resolution_error}
                  for a in amds if a.resolution_error]
    hires   = per_kind.get(K.HIRE, 0)
    leavers = per_kind.get(K.TERMINATE, 0)

    no_baseline = [a.employee.full_name for a in amds
                   if a.employee_id and not a.resolution_error
                   and a.kind != K.HIRE and a.employee_id not in baseline_ids]

    seen: dict = {}
    for a in amds:
        if a.employee_id and a.component_id and not a.resolution_error:
            key = (a.employee_id, a.component_id)
            seen[key] = seen.get(key, 0) + 1
    dup_rows = sum(1 for c in seen.values() if c > 1)

    ADD  = {K.ALLOWANCE_ADD, K.BONUS, K.OVERTIME, K.ARREARS}
    SUB  = {K.DEDUCTION_ADD}
    gross_delta = Decimal('0')
    for a in amds:
        if a.resolution_error:
            continue
        if a.kind in ADD:
            gross_delta += (a.amount or 0)
        elif a.kind in SUB:
            gross_delta -= (a.amount or 0)
    negative_amounts = [a.employee_ref for a in amds
                        if not a.resolution_error and a.kind in ADD and (a.amount or 0) < 0]

    projected_headcount = len(baseline_ids) + hires - leavers

    return Response({
        'batch': str(batch.id), 'status': batch.status,
        'target_period': target.period_name, 'baseline_period': baseline.period_name,
        'period_open': target.status == PayrollPeriod.Status.OPEN,
        'rows': len(amds),
        'by_kind': per_kind,
        'headcount': {'baseline': len(baseline_ids), 'joiners': hires,
                      'leavers': leavers, 'projected': projected_headcount},
        'gross': {'baseline': str(baseline_gross),
                  'estimated_change': str(gross_delta),
                  'estimated_after': str(baseline_gross + gross_delta),
                  'note': 'Estimate of earnings movement (new deductions netted). '
                          'Excludes new-hire/leaver payslip effects and PAYE; the exact '
                          'gross/PAYE/net are computed at Apply.'},
        'exceptions': {
            'unmatched_rows': unresolved,
            'unmatched_count': len(unresolved),
            'no_baseline_payslip': no_baseline,
            'duplicate_component_rows': dup_rows,
            'negative_earning_rows': negative_amounts,
        },
        'ready': (target.status == PayrollPeriod.Status.OPEN
                  and not unresolved and not no_baseline and dup_rows == 0
                  and not negative_amounts),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def apply_amendment_batch(request, batch_id):
    """POST /api/v1/payroll/amendment-batches/<id>/apply/

    Copies baseline_period's payslips into target_period (one per employee,
    same gross/lines) and then applies every PayrollAmendment row.
    Idempotent at the (employee, period) level — if a target payslip
    already exists it's overwritten.
    """
    _approver_only(request)

    batch = get_object_or_404(PayrollAmendmentBatch, pk=batch_id)

    # SoD moved downstream (CFO directive 2026-07-23, agreeing with FC Pako Kago
    # + FM): applying a batch only STAGES draft payslips in the target period —
    # it neither approves nor releases any payment. Segregation of duties is
    # enforced at the DOWNSTREAM gates that actually finalise/pay: payslips must
    # be APPROVED, the PayrollPeriod must be APPROVED, GL posting is limited to
    # finance_manager / cfo / payroll_poster, and the CFO authorises the bank
    # payment at FNB. A second approver at the staging step added friction
    # without control value, and blocked the uploader (Financial Controller)
    # from applying their own batch. Apply stays FINANCE-only (_approver_only);
    # both uploader AND applier are recorded (applied_by) so the trail is whole.
    if batch.status == PayrollAmendmentBatch.Status.APPLIED:
        return Response({'detail': 'Batch already applied.'}, status=409)

    baseline = batch.baseline_period
    target   = batch.target_period

    # Period lock (CFO 2026-08-28): a closed month is frozen — refuse to apply
    # into anything but an OPEN period. Finance re-opens (unlock) to correct it.
    if target.status != PayrollPeriod.Status.OPEN:
        return Response({'detail': f'{target.period_name} is {target.get_status_display()} '
                         f'(locked) — re-open the period before applying amendments.'}, status=409)

    # Defense in depth (Oprah report 2026-06-23): never copy a period onto
    # itself or roll backwards — even for a batch created before the upload
    # guard existed. Step 1 below overwrites the target's payslips atomically,
    # so a same-period or reversed batch must be refused here too.
    if baseline.id == target.id:
        return Response(
            {'detail': 'This batch has the same baseline and target period; applying it '
                       'would overwrite live payroll with a copy of itself. '
                       'Re-create the batch with the prior month as the baseline.'},
            status=400,
        )
    if baseline.start_date >= target.start_date:
        return Response(
            {'detail': f'This batch rolls backwards (baseline {baseline.period_name} is not '
                       f'earlier than target {target.period_name}); refusing to apply.'},
            status=400,
        )

    # Pre-flight readiness is a HARD server gate, not a warning (Manus QC round
    # 1, 2026-08-29; independently reproduced). The close wizard shows the batch
    # as "not ready" when any amendment row failed to resolve at parse time
    # (employee / component unmatched -> resolution_error set). Applying anyway
    # used to SKIP those rows, stage the rest as DRAFT payslips and still mark
    # the batch APPLIED (HTTP 200) -- the pre-flight was advisory only, so a
    # not-ready batch went through partially with a green result. Refuse here
    # instead: same readiness signal the screen reads (resolution_error) -- one
    # parser, no drift -- so a batch with unresolved rows returns 409, STAYS
    # PARSED and touches ZERO payslips. Fix or remove the rows and re-upload.
    unresolved = [a for a in batch.amendments.all() if a.resolution_error]
    if unresolved:
        return Response(
            {'detail': f'Batch is not ready to apply: {len(unresolved)} amendment '
                       'row(s) did not resolve (employee or component unmatched). '
                       'Fix or remove them and re-upload before applying.',
             'status': batch.status,          # stays PARSED
             'unresolved': [f'{a.employee_ref}: {a.resolution_error}'
                            for a in unresolved[:50]]},
            status=409,
        )

    baseline_payslips = (Payslip.objects
                         .filter(period=baseline)
                         .exclude(status=Payslip.Status.CANCELLED)
                         .select_related('employee')
                         .prefetch_related('lines__component'))

    created = updated = skipped = 0
    applied_amendments = 0
    fail_amendments    = 0
    errors: list[str]  = []

    with transaction.atomic():
        # 1. Copy baseline → target.  A roll-forward must reproduce the
        # VALIDATED baseline for an employee who has NO change this period — so
        # the baseline's authoritative computed totals are carried across, not
        # just the lines.  Re-deriving every total from the lines used to
        # silently re-tax an unchanged employee: a salary-sacrifice
        # HOUSING_DEDUCTION that the source import had already netted out of
        # gross got re-added, inflating GROSS/PAYE/NET (Arjun Parameswaran, RSA
        # July 2026 — Legakwa 2026-07-23; abs()-normalisation alone did NOT fix
        # it, because the drift is a gross/taxable-classification difference,
        # not a sign flip).  Fix: only TOUCHED payslips are recomputed (step 3);
        # untouched payslips keep the baseline totals verbatim.
        existing_targets = {p.employee_id: p for p in Payslip.objects
                            .filter(period=target).prefetch_related('lines')}
        # A leaver appears in the new period ONLY if this batch amends them
        # (their final settlement). A terminated employee with no amendment — or
        # a cancelled slip (excluded above) — must NEVER seed the next period as
        # a fresh payable draft, or a settled leaver would be paid again next
        # month (Fable 5 review, 2026-07-23).
        amended_emp_ids = {a.employee_id for a in batch.amendments.all() if a.employee_id}
        # THIS period's loan deductions, held aside across the rebuild below.
        # They are not in the baseline - the loan engine wrote them onto the
        # TARGET payslip after the last rebuild - so the baseline copy cannot
        # put them back, and it deliberately skips LOAN_REPAYMENT anyway.
        # Deleting them here left net pay overstated by the instalment with the
        # balance already reduced (bug 59c9fc5b-9b3d-4a5a-a546-5acab10613a6,
        # 16-Sep-2026). Note this loop rebuilds EVERY payslip in the period,
        # not only the amended ones, so a colleague with no amendment lost
        # theirs to somebody else's batch.
        preserved_loan_lines: dict = {}
        # Employees whose baseline carried a ONE-OFF line that this roll-forward
        # deliberately did NOT copy. Their baseline totals no longer describe
        # their target lines, so step 3 must recompute them - see below.
        one_off_dropped_emp_ids: set = set()
        # Joiner/leaver — one active-period rule (Prompt 02, Shared Contract
        # v1, 2026-09-12). Feature-flagged: OFF reproduces the exact prior
        # behaviour (status==TERMINATED or is_archived), so a flag flip is the
        # only rollback needed if this ever needs to be backed out.
        from payroll import config as payroll_config
        from payroll.eligibility import is_active_for_period, exclusion_reason
        use_active_period_rule = payroll_config.get_bool('payroll.active_period_rule', True)
        for src in baseline_payslips:
            if use_active_period_rule:
                # Date-driven: a leaver is excluded once their termination_date
                # is before THIS period starts — regardless of whether `status`
                # was ever flipped to TERMINATED (the bug this prompt fixes: a
                # leaver whose status stayed ACTIVE kept appearing every month
                # after their final period). Still never excludes someone this
                # batch is deliberately settling (amended_emp_ids).
                excluded = (not is_active_for_period(src.employee, target)
                           and src.employee_id not in amended_emp_ids)
                if excluded:
                    log.info('roll-forward %s: excluding %s — %s',
                            target.period_name, src.employee.full_name,
                            exclusion_reason(src.employee, target))
            else:
                # is_archived checked explicitly (not just status) — Terminated
                # Employee Archive, 2026-08-13: archiving always sets status to
                # TERMINATED too, but this stays explicit so the exclusion holds
                # even if that invariant is ever weakened.
                excluded = ((src.employee.status == Employee.Status.TERMINATED or src.employee.is_archived)
                           and src.employee_id not in amended_emp_ids)
            if excluded:
                skipped += 1
                continue
            tgt = existing_targets.get(src.employee_id)
            if tgt is None:
                tgt = Payslip.objects.create(
                    employee=src.employee,
                    period=target,
                    company=src.company,
                    status=Payslip.Status.DRAFT,
                    notes=f'Copied from {baseline.period_name}',
                )
                created += 1
            else:
                preserved_loan_lines[src.employee_id] = [
                    (ln.component_id, ln.amount, ln.notes)
                    for ln in tgt.lines.filter(component__code='LOAN_REPAYMENT')
                ]
                tgt.lines.all().delete()
                tgt.notes = f'Re-generated from {baseline.period_name}'
                tgt.status = Payslip.Status.DRAFT
                updated += 1
            for ln in src.lines.all():
                # A ONE-OFF earning belongs to the month it was earned. This
                # loop used to copy EVERY baseline line and exclude exactly one
                # code (LOAN_REPAYMENT, below), so a COMMISSION / INCENTIVE /
                # LEAVE_PAY / SEVERANCE was PAID AGAIN the next month —
                # P64,750 for ONE person out of an EMPTY amendment batch, with
                # HTTP 200 and errors: [] (2026-09-20). The classification now
                # lives on the component (PayslipComponent.is_recurring,
                # migration 0030) instead of a hard-coded list here, so the CFO
                # can mark a new component one-off without a deploy.
                if not ln.component.is_recurring:
                    one_off_dropped_emp_ids.add(src.employee_id)
                    continue
                # Never carry a loan-repayment deduction forward by copying it:
                # staff_loans/loan_service.apply_loan_repayments is the single
                # authority for LOAN_REPAYMENT lines (it amortises + stops at
                # term). Copying it here would double the deduction the month
                # the loan engine also runs. See staff_loans review 2026-07-15.
                if ln.component.code == 'LOAN_REPAYMENT':
                    continue
                PayslipLine.objects.create(
                    payslip=tgt, component=ln.component,
                    amount=ln.amount, notes=ln.notes,
                )
            # Put THIS period's loan deductions back. Restored from the target
            # payslip they were just taken from - never copied from the
            # baseline, which would carry a prior month's instalment forward.
            for comp_id, amount, notes in preserved_loan_lines.get(src.employee_id, ()):
                PayslipLine.objects.create(
                    payslip=tgt, component_id=comp_id, amount=amount, notes=notes,
                )
            # Carry the validated baseline totals verbatim (kept for untouched
            # employees; overwritten in step 3 for touched ones, which now
            # includes anyone whose one-off lines were just dropped — carrying
            # src.gross_amount verbatim is precisely what made the duplicate
            # pay reconcile and look right on the register).
            tgt.gross_amount = src.gross_amount
            tgt.paye_amount  = src.paye_amount
            tgt.net_amount   = src.net_amount
            tgt.ctc_amount   = src.ctc_amount
            tgt.source_gross = src.source_gross
            tgt.source_paye  = src.source_paye
            tgt.source_net   = src.source_net
            tgt.fx_rate_to_bwp = src.fx_rate_to_bwp
            # The currency travels with the source_* figures. Without it every
            # rolled-forward ADRisk (INR) slip reset to BWP and printed INR
            # amounts as broken pula slips (CFO 2026-09-18).
            tgt.source_currency = src.source_currency
            tgt.save(update_fields=[
                'notes', 'status', 'gross_amount', 'paye_amount', 'net_amount',
                'ctc_amount', 'source_gross', 'source_paye', 'source_net',
                'fx_rate_to_bwp', 'source_currency', 'updated_at'])

        # 1b. Joiners (Prompt 02) — an employee who becomes active during this
        # period, with no baseline payslip AND no explicit HIRE amendment in
        # this batch (that path already creates them in step 2, below), is
        # picked up automatically instead of needing HR to re-key a HIRE row.
        # Scoped to this batch's own company (same-entity only, per the
        # Shared Contract). Their recurring BASIC seeds from the active
        # EmploymentContract created on ATR conversion (Prompt 03) — a brand
        # new hire with no contract on file gets a blank DRAFT payslip.
        auto_joiner_emp_ids: set = set()
        if use_active_period_rule and batch.company_id:
            hire_amendment_emp_ids = {
                a.employee_id for a in batch.amendments.all()
                if a.employee_id and a.kind == PayrollAmendment.Kind.HIRE
            }
            existing_target_emp_ids = set(
                Payslip.objects.filter(period=target).values_list('employee_id', flat=True))
            # ANY payslip in the baseline period — CANCELLED included — means
            # this person is not a new joiner (coordinator review, 2026-09-12:
            # a CANCELLED-baseline employee was getting a fresh target draft,
            # directly contradicting the rule elsewhere in this function that
            # a cancelled slip never seeds the next period). Payslip.objects
            # here is deliberately NOT the `.exclude(status=CANCELLED)`
            # baseline_payslips queryset from step 1 above — this needs every
            # status, cancelled included.
            baseline_emp_ids = set(
                Payslip.objects.filter(period=baseline).values_list('employee_id', flat=True))
            prorate_on = payroll_config.get_bool('payroll.prorate_partial_month', False)
            working_days = payroll_config.get_int('payroll.working_days_per_month', 24)
            # hire_date__gt=baseline.end_date (not merely "isnull=False"):
            # a true joiner was hired AFTER the baseline period ended. Without
            # this, any active employee with a hire_date but no baseline
            # payslip for an unrelated reason (an HR-only record, an unpaid
            # board seat) would get a payslip auto-created every month
            # (coordinator review, 2026-09-12).
            candidates = (Employee.objects
                         .filter(company_id=batch.company_id, is_archived=False,
                                 hire_date__isnull=False, hire_date__gt=baseline.end_date)
                         .exclude(id__in=existing_target_emp_ids)
                         .exclude(id__in=hire_amendment_emp_ids)
                         .exclude(id__in=baseline_emp_ids))
            basic_comp = PayslipComponent.objects.filter(code__iexact='basic').first()
            for emp in candidates:
                if not is_active_for_period(emp, target):
                    continue
                ps = Payslip.objects.create(
                    employee=emp, period=target, company=emp.company,
                    status=Payslip.Status.DRAFT,
                    notes='Auto-included: active for this period (joiner rule).',
                )
                from payroll.eligibility import contract_for_period
                contract = contract_for_period(emp, target)
                if basic_comp and contract and contract.basic:
                    amount = contract.basic
                    if prorate_on:
                        from payroll.eligibility import working_days_in_service
                        in_service, total_days = working_days_in_service(emp, target, working_days)
                        if total_days > 0:
                            amount = (amount * in_service / total_days).quantize(Decimal('0.01'))
                    PayslipLine.objects.get_or_create(
                        payslip=ps, component=basic_comp, defaults={'amount': amount})
                # Recurring allowances/contributions snapshotted on the contract
                # at signing (Prompt 03 — ATR conversion seeds this so the
                # first payslip needs no re-keying). Component amounts are NOT
                # prorated — only the BASIC salary is (same convention the
                # rest of this pack uses for partial-month pay).
                if contract and contract.allowance_template:
                    for code, amt in contract.allowance_template.items():
                        comp = PayslipComponent.objects.filter(code__iexact=str(code)).first()
                        if comp is None:
                            log.warning('roll-forward %s: %s — allowance_template component '
                                       '%r not configured, line dropped',
                                       target.period_name, emp.full_name, code)
                            continue
                        try:
                            comp_amount = Decimal(str(amt))
                        except (InvalidOperation, TypeError, ValueError):
                            log.warning('roll-forward %s: %s — allowance_template amount %r for '
                                       '%s is not a number, line dropped',
                                       target.period_name, emp.full_name, amt, code)
                            continue
                        PayslipLine.objects.get_or_create(
                            payslip=ps, component=comp, defaults={'amount': comp_amount})
                created += 1
                # Feeds step 3's recompute (below) — without this the joiner's
                # payslip keeps its zeroed computed totals forever, reading as
                # a real 0.00 net pay for a new hire (coordinator review,
                # 2026-09-12).
                auto_joiner_emp_ids.add(emp.id)
                log.info('roll-forward %s: auto-included new joiner %s (hire_date=%s)',
                         target.period_name, emp.full_name, emp.hire_date)

        # 2. Apply amendments.  Track which employees actually had a FIGURE
        # change (so step 3 recomputes only them, not every unchanged employee)
        # and which carry a manual tax override (so recompute never clobbers the
        # typed PAYE — e.g. a leaver's tax-directive figure).
        period_payslips = {p.employee_id: p for p in Payslip.objects
                           .filter(period=target).select_related('employee')
                           .prefetch_related('lines__component')}

        # A restored loan line is a line the baseline totals above do NOT
        # account for, so those employees must be recomputed even when this
        # batch does not amend them - otherwise they keep a stored net that
        # never had the deduction in it, with nothing on the payslip to show
        # anything is missing. That is the silent half of the same bug.
        # A dropped one-off is the mirror image of the same problem: the
        # baseline totals INCLUDE pay the target lines no longer carry, so
        # those employees must be recomputed too - otherwise gross/PAYE/net
        # would still reconcile to the inflated figure with nothing on the
        # payslip to show what is missing.
        recompute_emp_ids: set = set(auto_joiner_emp_ids) | one_off_dropped_emp_ids | {
            emp_id for emp_id, lines in preserved_loan_lines.items() if lines
        }
        tax_override_emp_ids: set = set()

        # Compose with the month's OTHER already-applied batches. Step 1 above
        # re-copied the baseline, which wiped their effect on the target; re-apply
        # them here (oldest first) BEFORE this batch's rows, so a partial batch
        # (e.g. the auto-incentive batch, or another entity's) can never silently
        # revert batches already applied to this period (Fable 2026-08-28, proven).
        # Apply is update_or_create per (payslip, component): same component ->
        # last wins (a full re-upload still replaces cleanly, no double-count);
        # distinct components coexist. Prior rows stay applied=True; we only need
        # their effect + figure-recompute tracking, not to re-count them.
        prior_batches = (PayrollAmendmentBatch.objects
                         .filter(target_period=target,
                                 status=PayrollAmendmentBatch.Status.APPLIED)
                         .exclude(pk=batch.pk)
                         .order_by('applied_at', 'created_at'))
        for pb in prior_batches:
            for amd in pb.amendments.select_related('employee', 'component'):
                if amd.resolution_error:
                    continue
                if amd.employee_id is None and amd.kind != PayrollAmendment.Kind.HIRE:
                    continue
                try:
                    ok, _reason = _apply_one_amendment(amd, period_payslips, target)
                    if ok and amd.employee_id and _amendment_changes_figures(amd):
                        recompute_emp_ids.add(amd.employee_id)
                    if ok and amd.employee_id and amd.kind == PayrollAmendment.Kind.TAX_OVERRIDE:
                        tax_override_emp_ids.add(amd.employee_id)
                except Exception:                       # noqa: BLE001
                    log.exception('re-apply prior batch %s amd %s failed', pb.pk, amd.pk)

        for amd in batch.amendments.select_related('employee', 'component'):
            if amd.resolution_error:
                fail_amendments += 1
                errors.append(f'{amd.employee_ref}: {amd.resolution_error}')
                continue
            emp = amd.employee
            try:
                ok, reason = _apply_one_amendment(amd, period_payslips, target)
                if ok:
                    amd.applied = True
                    amd.save(update_fields=['applied', 'updated_at'])
                    applied_amendments += 1
                    if emp is not None and _amendment_changes_figures(amd):
                        recompute_emp_ids.add(emp.id)
                    if emp is not None and amd.kind == PayrollAmendment.Kind.TAX_OVERRIDE:
                        tax_override_emp_ids.add(emp.id)
                else:
                    fail_amendments += 1
                    # Every rejected row must surface a reason — NEVER silent
                    # (Emily Chilongo final settlement, RSA July 2026 — Legakwa
                    # 2026-07-23: rows returned False with no error line, so the
                    # draft looked untouched with no signal why).
                    who = emp.full_name if emp else amd.employee_ref
                    errors.append(
                        f'{who}: {reason or f"could not apply (kind={amd.kind})"}')
            except Exception as exc:
                fail_amendments += 1
                errors.append(f'{emp.full_name if emp else amd.employee_ref}: {exc}')

        # 3. Recompute totals — ONLY for employees whose figures changed this
        # period.  Untouched employees keep the validated baseline totals from
        # step 1.  A payslip carrying a manual tax override is recomputed
        # WITHOUT re-deriving PAYE, so the typed figure survives (CFO directive
        # 2026-05-24 still holds: an amended BASIC recomputes PAYE from the live
        # BURS brackets — only an explicit override opts out).
        for emp_id, ps in period_payslips.items():
            if emp_id not in recompute_emp_ids:
                continue
            do_paye = emp_id not in tax_override_emp_ids
            ps.recompute_totals(recompute_paye=do_paye)
            fresh_paye = getattr(ps, '_recomputed_paye', None)
            if do_paye and fresh_paye is not None:
                ps.overwrite_paye_line(fresh_paye, user=request.user)
            ps.save(update_fields=['gross_amount', 'paye_amount', 'net_amount', 'ctc_amount', 'updated_at'])

        batch.status     = PayrollAmendmentBatch.Status.APPLIED
        batch.applied_at = timezone.now()
        batch.applied_by = request.user
        batch.save(audit_user=request.user,
                   audit_description=f'Applied {applied_amendments}/{batch.row_count} amendments')

    return Response({
        'batch_id': str(batch.id),
        'baseline_period': baseline.period_name,
        'target_period': target.period_name,
        'payslips_created': created,
        'payslips_updated': updated,
        'amendments_applied': applied_amendments,
        'amendments_failed':  fail_amendments,
        'errors': errors[:100],
    })


# Kinds that change the money on a payslip (used to decide which employees get
# recomputed on roll-forward — see apply_batch step 3). TERMINATE only flips
# status, and a note-only OTHER changes nothing, so neither is here.
_FIGURE_CHANGING_KINDS = {
    PayrollAmendment.Kind.SALARY_CHANGE,
    PayrollAmendment.Kind.ALLOWANCE_ADD, PayrollAmendment.Kind.ALLOWANCE_REMOVE,
    PayrollAmendment.Kind.DEDUCTION_ADD, PayrollAmendment.Kind.DEDUCTION_REMOVE,
    PayrollAmendment.Kind.BONUS, PayrollAmendment.Kind.OVERTIME,
    PayrollAmendment.Kind.ARREARS, PayrollAmendment.Kind.TAX_OVERRIDE,
    PayrollAmendment.Kind.HIRE,
}


def _amendment_changes_figures(amd: PayrollAmendment) -> bool:
    if amd.kind in _FIGURE_CHANGING_KINDS:
        return True
    # kind "other" now posts an earning line when it carries a resolved
    # component (e.g. Severance Pay / Leave Pay on a final settlement).
    return amd.kind == PayrollAmendment.Kind.OTHER and amd.component_id is not None


def _apply_one_amendment(amd: PayrollAmendment, payslips_by_emp: dict,
                         target: PayrollPeriod) -> tuple[bool, str]:
    """Single amendment dispatcher. Returns (applied, reason). On failure the
    reason is surfaced by the caller so a rejected row is NEVER silent."""
    kind = amd.kind
    emp  = amd.employee

    # Hire: create a fresh draft payslip with just the basic.
    if kind == PayrollAmendment.Kind.HIRE:
        if emp is None:
            return False, 'new-hire row has no matching employee record'
        if emp.id in payslips_by_emp:
            return False, 'employee already has a payslip this period (hire skipped)'
        ps = Payslip.objects.create(
            employee=emp, period=target, status=Payslip.Status.DRAFT,
            notes=f'New hire: {amd.reason or "—"}',
        )
        # Use a generic basic-salary component if available
        basic = PayslipComponent.objects.filter(code__iexact='basic').first()
        if basic:
            PayslipLine.objects.create(payslip=ps, component=basic, amount=amd.amount)
        payslips_by_emp[emp.id] = ps
        return True, ''

    if emp is None:
        return False, f'employee {amd.employee_ref!r} not found'
    if emp.id not in payslips_by_emp:
        return False, (f'{emp.full_name} has no payslip in {target.period_name} '
                       f'(no baseline row and not a hire) — nothing to amend')
    ps = payslips_by_emp[emp.id]

    if kind == PayrollAmendment.Kind.TERMINATE:
        ps.status = Payslip.Status.CANCELLED
        ps.notes  = (ps.notes + f'\nTerminated: {amd.reason or "—"}').strip()
        ps.save(update_fields=['status', 'notes', 'updated_at'])
        return True, ''

    if kind == PayrollAmendment.Kind.SALARY_CHANGE:
        basic = PayslipComponent.objects.filter(code__iexact='basic').first()
        if basic is None:
            return False, 'BASIC salary component not configured'
        PayslipLine.objects.update_or_create(
            payslip=ps, component=basic, defaults={'amount': amd.amount},
        )
        return True, ''

    if kind in (PayrollAmendment.Kind.ALLOWANCE_ADD,
                PayrollAmendment.Kind.DEDUCTION_ADD,
                PayrollAmendment.Kind.BONUS,
                PayrollAmendment.Kind.OVERTIME,
                PayrollAmendment.Kind.ARREARS):
        comp = amd.component
        if comp is None:
            # Bonus / overtime / arrears can default to a generic component
            default_code = {'bonus': 'BONUS', 'overtime': 'OT', 'arrears': 'ARREARS'}.get(kind)
            if default_code:
                comp = PayslipComponent.objects.filter(code__iexact=default_code).first()
        if comp is None:
            return False, f'no pay component resolved for {kind} (component required)'
        PayslipLine.objects.update_or_create(
            payslip=ps, component=comp,
            defaults={'amount': amd.amount, 'notes': amd.reason[:200]},
        )
        return True, ''

    if kind in (PayrollAmendment.Kind.ALLOWANCE_REMOVE,
                PayrollAmendment.Kind.DEDUCTION_REMOVE):
        if amd.component is None:
            return False, f'no pay component resolved for {kind} (component required)'
        PayslipLine.objects.filter(payslip=ps, component=amd.component).delete()
        return True, ''

    if kind == PayrollAmendment.Kind.TAX_OVERRIDE:
        paye = PayslipComponent.objects.filter(code__iexact='PAYE').first()
        if paye is None:
            return False, 'PAYE component not configured'
        PayslipLine.objects.update_or_create(
            payslip=ps, component=paye,
            defaults={'amount': amd.amount, 'notes': amd.reason[:200]},
        )
        return True, ''

    # OTHER — if the row names a real pay component (e.g. "Severance Pay",
    # "Leave Pay" on a final settlement) POST it as a line so it actually lands
    # in the pay; otherwise it is a free-text note with no money effect.
    # Previously OTHER always logged a note and returned True, so a settlement
    # earning silently never landed (Emily Chilongo, RSA July 2026).
    if amd.component is not None:
        PayslipLine.objects.update_or_create(
            payslip=ps, component=amd.component,
            defaults={'amount': amd.amount, 'notes': (amd.reason or '')[:200]},
        )
        return True, ''
    ps.notes = (ps.notes + f'\nNote ({amd.reason}): {amd.amount}').strip()
    ps.save(update_fields=['notes', 'updated_at'])
    return True, ''


# ─── AI period review ──────────────────────────────────────────────────────

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def ai_review_period(request, period_id):
    """POST/GET /api/v1/payroll/periods/<id>/ai-review/
    Calls DeepSeek with a structured prompt to flag anomalies + narrate the
    period vs prior. If the API key is unset, returns a deterministic
    baseline-only diff so the surface still has something to show.
    """
    _payroll_view(request)
    period = get_object_or_404(PayrollPeriod, pk=period_id)
    payslips = (Payslip.objects.filter(period=period)
                .select_related('employee')
                .prefetch_related('lines__component'))

    summary = {
        'period':   period.period_name,
        'count':    payslips.count(),
        'gross_total':  sum((p.gross_amount or ZERO for p in payslips), ZERO),
        'paye_total':   sum((p.paye_amount  or ZERO for p in payslips), ZERO),
        'net_total':    sum((p.net_amount   or ZERO for p in payslips), ZERO),
        'ctc_total':    sum((p.ctc_amount   or ZERO for p in payslips), ZERO),
    }

    # Prior-period comparison (string match on period_name like '2026-04')
    prior = (PayrollPeriod.objects
             .filter(end_date__lt=period.start_date)
             .order_by('-end_date')
             .first())
    prior_summary = None
    if prior is not None:
        prior_ps = Payslip.objects.filter(period=prior)
        prior_summary = {
            'period':       prior.period_name,
            'count':        prior_ps.count(),
            'gross_total':  sum((p.gross_amount or ZERO for p in prior_ps), ZERO),
            'paye_total':   sum((p.paye_amount  or ZERO for p in prior_ps), ZERO),
            'net_total':    sum((p.net_amount   or ZERO for p in prior_ps), ZERO),
        }

    # Anomaly probes (deterministic)
    anomalies: list[dict] = []
    for p in payslips:
        if p.gross_amount and p.net_amount and p.net_amount < 0:
            anomalies.append({'employee': p.employee.full_name, 'flag': 'negative net',
                              'amount': str(p.net_amount)})
        if p.gross_amount and p.paye_amount and p.gross_amount > 0:
            etr = (p.paye_amount / p.gross_amount) * 100
            if etr > 35:
                anomalies.append({'employee': p.employee.full_name,
                                  'flag': f'PAYE > 35% (= {etr:.1f}%)',
                                  'amount': str(p.paye_amount)})

    # Call DeepSeek if configured
    ai_narrative = ''
    ai_unavailable_reason = ''
    try:
        from core.ai_assist import deepseek_complete, DeepSeekUnavailable
        prompt = _build_review_prompt(summary, prior_summary, anomalies)
        # deepseek_complete(user_prompt, *, system_prompt=…, max_tokens=…) — the
        # prompt is the POSITIONAL user_prompt; the auditor brief is system_prompt.
        # (Was calling system=/user=/temperature=, none of which exist, so it
        # always raised "missing … 'user_prompt'" and the narrative never showed.)
        ai_narrative = deepseek_complete(
            prompt,
            system_prompt='You are a Botswana payroll auditor. Be concise, factual, '
                          'and call out variance vs prior period in plain English. '
                          'Never reproduce employee names verbatim — use initials.',
            max_tokens=600,
        )
    except Exception as exc:
        ai_unavailable_reason = str(exc)[:200]

    return Response({
        'summary': {
            **{k: str(v) for k, v in summary.items()},
        },
        'prior': {**{k: str(v) for k, v in prior_summary.items()}} if prior_summary else None,
        'anomalies': anomalies,
        'ai_narrative': ai_narrative,
        'ai_unavailable_reason': ai_unavailable_reason,
    })


def _build_review_prompt(summary, prior_summary, anomalies):
    lines = [
        f"Period: {summary['period']}",
        f"Employees: {summary['count']}",
        f"Gross:  {summary['gross_total']}",
        f"PAYE:   {summary['paye_total']}",
        f"Net:    {summary['net_total']}",
        f"CTC:    {summary['ctc_total']}",
    ]
    if prior_summary:
        lines += [
            '',
            f"Prior period: {prior_summary['period']}",
            f"Prior Gross:  {prior_summary['gross_total']}",
            f"Prior PAYE:   {prior_summary['paye_total']}",
            f"Prior Net:    {prior_summary['net_total']}",
        ]
    if anomalies:
        lines += ['', 'Anomalies flagged by deterministic rules:']
        for a in anomalies[:25]:
            lines.append(f"  - {a['employee']}: {a['flag']} (amount={a['amount']})")
    lines += [
        '',
        'Tasks:',
        '1. One short paragraph summarising the period vs prior (variance %).',
        '2. Bullet list of the top 5 risk areas in this payroll.',
        '3. Closing sentence: ready to approve, or hold? Why?',
    ]
    return '\n'.join(lines)


# ─── Export + dashboard summary ──────────────────────────────────────────────

# Fixed leading columns; the per-component columns in between are built
# dynamically from the active PayslipComponent catalog so the export shows
# every earning and deduction that builds up Gross/Net as its own column
# (Kago / Pako request 2026-06-25). Applies to every entity — the component
# catalog is global, so all companies get the same column layout.
_LEAD_HEADERS = ['Employee', 'Employee No', 'Department', 'Company', 'Period']

_EARNING_KINDS  = ('earning', 'earning_non_taxable')
_DEDUCT_KINDS   = ('employee_pretax', 'employee_deduction')
_EMPLOYER_KINDS = ('company_contribution',)


def _component_columns(period=None):
    """Ordered component columns for the payroll export, grouped as
    (earnings, employee_deductions, employer_contributions). PAYE is shown in
    its own fixed column from the stored payslip total, so it is excluded here.
    Each list holds (code, name) in catalog sort order.

    When `period` is given, any component that ACTUALLY has a line in that period
    is included even if it was since deactivated — so an adjustment (commission,
    incentive, etc.) can never be silently dropped from the report while its
    amount still sits in the stored gross (FC Pako Kago, 2026-07-23:
    "the report must not read a components list that excludes adjustment types").
    """
    from django.db.models import Q
    from .models import PayslipComponent, PayslipLine
    cond = Q(is_active=True)
    if period is not None:
        present = list(PayslipLine.objects.filter(payslip__period=period)
                       .values_list('component_id', flat=True).distinct())
        if present:
            cond = cond | Q(id__in=present)
    cat = list(PayslipComponent.objects.filter(cond)
               .exclude(code='PAYE')
               .order_by('sort_order', 'name')
               .values_list('code', 'name', 'kind'))
    earnings = [(c, n) for c, n, k in cat if k in _EARNING_KINDS]
    deducts  = [(c, n) for c, n, k in cat if k in _DEDUCT_KINDS]
    employer = [(c, n) for c, n, k in cat if k in _EMPLOYER_KINDS]
    return earnings, deducts, employer


def _scoped_payslips(period, user, company=None):
    """Payslips for a period, clamped to the caller's entity grant + optional
    company filter."""
    from core.models import allowed_company_ids
    qs = (Payslip.objects.filter(period=period)
          .select_related('employee', 'company')
          .prefetch_related('lines__component')
          .order_by('company__code', 'employee__full_name'))
    allowed = allowed_company_ids(user)
    if allowed != {'*'}:
        qs = qs.filter(company_id__in=allowed)
    if company is not None:
        qs = qs.filter(company=company)
    return qs


def _basic_total(period, user, company=None):
    """Sum of BASIC component lines in scope. Partial for totals-only imports
    (those payslips store Gross/Net but no component breakdown)."""
    from django.db.models import Sum
    from core.models import allowed_company_ids
    qs = PayslipLine.objects.filter(payslip__period=period, component__code__iexact='BASIC')
    allowed = allowed_company_ids(user)
    if allowed != {'*'}:
        qs = qs.filter(payslip__company_id__in=allowed)
    if company is not None:
        qs = qs.filter(payslip__company=company)
    return qs.aggregate(t=Sum('amount'))['t'] or ZERO


def _write_period_sheet(ws, period, qs):
    """Write one period's per-employee rows with a full line-by-line breakdown
    plus a TOTAL row. Every earning and deduction gets its own column. Payslips
    imported as totals-only (no component lines) show the stored
    Gross/PAYE/Net/CTC with blank component cells and a 'totals only' flag — the
    breakdown was never captured for those, so it is left blank rather than
    fabricated. Returns (rows, gross, paye, net, ctc) totals."""
    from openpyxl.styles import Font
    earnings, deducts, employer = _component_columns(period)
    e_codes = [c for c, _ in earnings]
    d_codes = [c for c, _ in deducts]
    r_codes = [c for c, _ in employer]

    header = (_LEAD_HEADERS
              + [n for _, n in earnings]
              + ['GROSS', 'PAYE']
              + [n for _, n in deducts]
              + ['TOTAL DEDUCTIONS', 'NET PAY']
              + [n for _, n in employer]
              + ['CTC', 'PAYE % of Gross', 'Deductions % of Gross',
                 'Breakdown', 'Status'])
    ws.append(header)

    tg = tp = tn = tc = ZERO
    e_tot = {c: ZERO for c in e_codes}
    d_tot = {c: ZERO for c in d_codes}
    r_tot = {c: ZERO for c in r_codes}
    rows = 0

    for ps in qs:
        g = ps.gross_amount or ZERO
        p = ps.paye_amount or ZERO
        net = ps.net_amount or ZERO
        c = ps.ctc_amount or ZERO
        emp = ps.employee
        amt = {}
        for ln in ps.lines.all():
            amt[ln.component.code] = (ln.amount or ZERO)
        has_lines = bool(amt)
        total_ded = g - net
        paye_pct = (float(p) / float(g) * 100) if g else 0.0
        ded_pct = (float(total_ded) / float(g) * 100) if g else 0.0

        row = [
            emp.full_name if emp else '',
            emp.employee_number if emp else '',
            emp.department if emp else '',
            ps.company.code if ps.company else '',
            period.period_name,
        ]
        row += [(float(amt[c]) if c in amt else None) for c in e_codes]
        row += [float(g), float(p)]
        row += [(float(amt[c]) if c in amt else None) for c in d_codes]
        row += [float(total_ded), float(net)]
        row += [(float(amt[c]) if c in amt else None) for c in r_codes]
        row += [float(c), round(paye_pct, 2), round(ded_pct, 2),
                'full' if has_lines else 'totals only',
                ps.get_status_display()]
        ws.append(row)

        tg += g; tp += p; tn += net; tc += c; rows += 1
        for code in e_codes:
            if code in amt:
                e_tot[code] += amt[code]
        for code in d_codes:
            if code in amt:
                d_tot[code] += amt[code]
        for code in r_codes:
            if code in amt:
                r_tot[code] += amt[code]

    ws.append([])
    ttl_ded = tg - tn
    total_row = ([f'TOTAL ({rows} payslips)', '', '', '', period.period_name]
                 + [float(e_tot[c]) for c in e_codes]
                 + [float(tg), float(tp)]
                 + [float(d_tot[c]) for c in d_codes]
                 + [float(ttl_ded), float(tn)]
                 + [float(r_tot[c]) for c in r_codes]
                 + [float(tc), '', '', '', ''])
    ws.append(total_row)

    ncols = len(header)
    for col in range(1, ncols + 1):
        ws.cell(row=1, column=col).font = Font(bold=True)
        ws.cell(row=ws.max_row, column=col).font = Font(bold=True)
    ws.freeze_panes = 'F2'   # lock the 5 lead columns + header row
    ws.column_dimensions['A'].width = 26
    ws.column_dimensions['C'].width = 18
    return rows, tg, tp, tn, tc


def _xlsx_response(wb, fname):
    from io import BytesIO
    from django.http import HttpResponse
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_payroll(request):
    """GET /api/v1/payroll/export/?period=<id|name>&company=<id|code>
        GET /api/v1/payroll/export/?all=1&company=<id|code>   (every period)

    Download payroll as Excel for reconciliation. `?period=` → one period on one
    sheet. `?all=1` → a Summary sheet (per-period totals + grand total) plus one
    detail sheet per period. Payroll-view gated; company-scoped to the grant.
    """
    from rest_framework.exceptions import PermissionDenied
    from openpyxl import Workbook
    from openpyxl.styles import Font
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted to HR / Finance / payroll administrators.')

    company = _resolve_company(request.query_params.get('company'))
    comp_tag = company.code if company else 'all'
    want_all = str(request.query_params.get('all') or '').strip().lower() in ('1', 'true', 'yes')

    if want_all:
        # "All periods" = every period that has payroll in scope. Cap high enough
        # to cover full history (one sheet each) while guarding a pathological case.
        periods = [p for p in PayrollPeriod.objects.order_by('-start_date')
                   if _scoped_payslips(p, request.user, company).exists()][:150]
        wb = Workbook()
        summ = wb.active
        summ.title = 'Summary'
        sh = ['Period', 'Headcount', 'Basic', 'Gross', 'PAYE', 'Net', 'CTC']
        summ.append(sh)
        ghc = 0
        gh = gg = gp = gn = gc = ZERO
        for per in periods:
            qs = _scoped_payslips(per, request.user, company)
            ws = wb.create_sheet(per.period_name[:31])
            rows, tg, tp, tn, tc = _write_period_sheet(ws, per, qs)
            basic = _basic_total(per, request.user, company)
            summ.append([per.period_name, rows, float(basic),
                         float(tg), float(tp), float(tn), float(tc)])
            ghc += rows; gh += basic; gg += tg; gp += tp; gn += tn; gc += tc
        if not periods:
            summ.append(['No payroll in your scope yet.'])
        else:
            summ.append([])
            summ.append(['GRAND TOTAL', ghc, float(gh), float(gg), float(gp), float(gn), float(gc)])
            for col in range(1, len(sh) + 1):
                summ.cell(row=summ.max_row, column=col).font = Font(bold=True)
        for col in range(1, len(sh) + 1):
            summ.cell(row=1, column=col).font = Font(bold=True)
        summ.column_dimensions['A'].width = 14
        return _xlsx_response(wb, f'payroll_all_periods_{comp_tag}.xlsx'.replace(' ', '_'))

    period_ref = (request.query_params.get('period')
                  or request.query_params.get('period_name') or '').strip()
    if not period_ref:
        return Response({'detail': 'period is required (id or name like 2026-05), or pass all=1.'}, status=400)
    period = (PayrollPeriod.objects.filter(pk=period_ref).first() if len(period_ref) == 36
              else PayrollPeriod.objects.filter(period_name=period_ref).first())
    if period is None:
        return Response({'detail': f'Period {period_ref!r} not found.'}, status=404)
    wb = Workbook()
    ws = wb.active
    ws.title = f'Payroll {period.period_name}'[:31]
    _write_period_sheet(ws, period, _scoped_payslips(period, request.user, company))
    return _xlsx_response(wb, f'payroll_{period.period_name}_{comp_tag}.xlsx'.replace(' ', '_'))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payroll_summary(request):
    """GET /api/v1/payroll/summary/?company=<id|code>

    Per-period payroll totals for the caller's entity(ies) — feeds the payroll
    dashboard. Headcount / Gross / PAYE / Net / CTC are reliable (stored on the
    payslip); Basic / Commission / Incentive / Allowances are sums of their
    component lines (partial for totals-only imports).

    Commission + Incentive added 2026-07-29 on Pako Kago's bug report: they are
    material earnings that sum into Gross, but the dashboard only broke out
    Basic — so a run's commission was invisible here even once the register had
    been imported with full component lines. **Allowances** added the same day
    for the same reason: an employee's allowance (Bokani Makosha's BWP 2,000)
    was inside Gross but nowhere on this screen. Allowances = every earning
    component that is not Basic / Commission / Incentive.

    Basic + Commission + Incentive + Allowances = Gross, but ONLY for a BWP run
    with full component lines and no negative Basic/Commission/Incentive line.
    Three deliberate reasons it can differ, so do not present it as a hard tie:
      • totals-only imports have no component lines at all;
      • Basic / Commission / Incentive are summed as magnitudes (a register that
        stored one negative must not shrink the dashboard), while Allowances
        keeps its sign because a negative earning is the BURS §32 housing salary
        sacrifice that genuinely reduces gross;
      • foreign-currency slips are excluded from the line columns (see below).
    Payroll-view gated; company-scoped.
    """
    from rest_framework.exceptions import PermissionDenied
    from django.db.models import Sum, Count
    from core.models import allowed_company_ids
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted to HR / Finance / payroll administrators.')

    company = _resolve_company(request.query_params.get('company'))
    allowed = allowed_company_ids(request.user)

    base = Payslip.objects.all()
    if allowed != {'*'}:
        base = base.filter(company_id__in=allowed)
    if company is not None:
        base = base.filter(company=company)

    agg = base.values('period').annotate(
        headcount=Count('id'), gross=Sum('gross_amount'), paye=Sum('paye_amount'),
        net=Sum('net_amount'), ctc=Sum('ctc_amount'))
    by_period = {a['period']: a for a in agg}

    # One grouped query for every broken-out earning, instead of one query per
    # component code. Keys are lower-cased so the response field names line up
    # with the dashboard's own keys.
    BREAKOUTS = {'BASIC': 'basic', 'COMMISSION': 'commission', 'INCENTIVE': 'incentive'}
    # Every OTHER earning component rolls into one "Allowances" figure (Pako
    # Kago 2026-07-29: an employee's allowance must be visible on the dashboard,
    # not only inside Gross). Derived — any earning code that is not explicitly
    # broken out above lands here — so a component added later cannot silently
    # go missing from the dashboard the way Commission and Incentive did. That
    # is the L2 failure class: never gate on a hand-written set.
    EARNING_KINDS = (PayslipComponent.Kind.EARNING,
                     PayslipComponent.Kind.EARNING_NON_TAXABLE)
    lqs = PayslipLine.objects.filter(component__kind__in=EARNING_KINDS)
    if allowed != {'*'}:
        lqs = lqs.filter(payslip__company_id__in=allowed)
    if company is not None:
        lqs = lqs.filter(payslip__company=company)
    # Foreign-currency slips are EXCLUDED from the line columns. ADRisk pays in
    # INR: its lines are INR while gross_amount/paye_amount/net_amount are the
    # BWP reporting figures, so summing those lines here would add INR into a
    # BWP column sitting next to a BWP Gross. Mirrors Payslip.is_foreign_currency
    # and the same deliberate skip in payslip_breakdown._compare_stored.
    lqs = lqs.filter(payslip__source_currency__in=('BWP', ''))
    # {period_id: {'basic': …, 'commission': …, 'incentive': …, 'allowances': …}}
    lines_by_period: dict = {}
    for row in (lqs.values('payslip__period', 'component__code')
                   .annotate(t=Sum('amount'))):
        key = BREAKOUTS.get((row['component__code'] or '').upper())
        bucket = lines_by_period.setdefault(row['payslip__period'], {})
        total = row['t'] or ZERO
        if key:
            # Earnings, so take the magnitude — a register that stored a line
            # negative must not subtract from the dashboard total.
            bucket[key] = bucket.get(key, ZERO) + abs(total)
        else:
            # Allowances keep their SIGN. A negative earning is the BURS §32
            # housing salary sacrifice, which genuinely REDUCES gross (live:
            # HOUSING_ALLOWANCE sums to −19,500). abs() here would swing the
            # column by twice that and stop Basic + Commission + Incentive +
            # Allowances tying to Gross.
            bucket['allowances'] = bucket.get('allowances', ZERO) + total

    rows = []
    ghc = 0
    gh = gg = gp = gn = gc = ZERO
    gcomm = ginc = gallow = ZERO
    for per in PayrollPeriod.objects.filter(id__in=by_period.keys()).order_by('-start_date'):
        a = by_period[per.id]
        hc = a['headcount'] or 0
        bucket = lines_by_period.get(per.id) or {}
        basic = bucket.get('basic') or ZERO
        comm  = bucket.get('commission') or ZERO
        inc   = bucket.get('incentive') or ZERO
        allow = bucket.get('allowances') or ZERO
        g = a['gross'] or ZERO; p = a['paye'] or ZERO
        n = a['net'] or ZERO; c = a['ctc'] or ZERO
        rows.append({'period': per.period_name, 'headcount': hc, 'basic': str(basic),
                     'commission': str(comm), 'incentive': str(inc),
                     'allowances': str(allow),
                     'gross': str(g), 'paye': str(p), 'net': str(n), 'ctc': str(c)})
        ghc += hc; gh += basic; gcomm += comm; ginc += inc; gallow += allow
        gg += g; gp += p; gn += n; gc += c

    return Response({
        'company': company.code if company else None,
        'periods': rows,
        'grand_total': {'headcount': ghc, 'basic': str(gh),
                        'commission': str(gcomm), 'incentive': str(ginc),
                        'allowances': str(gallow), 'gross': str(gg),
                        'paye': str(gp), 'net': str(gn), 'ctc': str(gc)},
    })


# ─── Component-line backfill (CFO 2026-07-14) ────────────────────────────────
# Self-serve fix for "payroll report component fields not populating": payslips
# imported totals-only have no PayslipLine rows so the report shows blanks. The
# payroll team uploads the register here and Omni loads the per-component detail
# — but ONLY for employees whose register Gross ties the approved payslip Gross,
# so an approved total can never be silently changed.

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def component_coverage(request):
    """GET /api/v1/payroll/component-coverage/ → per (period, company): how many
    payslips carry full component lines vs are totals-only. Shows the payroll
    team exactly which runs still need a register upload, across ALL payrolls."""
    from rest_framework.exceptions import PermissionDenied
    from django.db.models import Count, Q
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted to HR / Finance / payroll administrators.')

    agg = (Payslip.objects
           .values('period__period_name', 'period__start_date', 'company__code')
           .annotate(total=Count('id', distinct=True),
                     full=Count('id', filter=Q(lines__id__isnull=False), distinct=True))
           .order_by('-period__start_date', 'company__code'))
    rows = []
    for a in agg:
        total = a['total'] or 0
        full = a['full'] or 0
        rows.append({
            'period': a['period__period_name'],
            'company': a['company__code'],
            'payslips': total,
            'with_components': full,
            'totals_only': total - full,
            'complete': total > 0 and full == total,
        })
    return Response({'coverage': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def register_upload(request):
    """POST /api/v1/payroll/register-upload/  multipart: file, period, company,
    [commit=true], [tolerance].

    Backfill payslip component lines for one (period, company) from an uploaded
    payroll register (Odoo export or the Omni layout). Reconciliation-guarded —
    lines are written only for employees whose register Gross ties the approved
    payslip Gross. Dry-run unless commit=true, so the accountant previews the
    match before writing."""
    from rest_framework.exceptions import PermissionDenied
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted to HR / Finance / payroll administrators.')

    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'No file uploaded (field "file").'}, status=400)
    if f.size > 10 * 1024 * 1024:
        return Response({'detail': 'File exceeds 10 MB.'}, status=400)
    period = (request.data.get('period') or '').strip()
    company = (request.data.get('company') or '').strip()
    if not period or not company:
        return Response({'detail': 'period (e.g. 2026-05) and company (e.g. ADIC) are required.'}, status=400)
    commit = str(request.data.get('commit') or '').strip().lower() in ('1', 'true', 'yes')
    try:
        tolerance = float(request.data.get('tolerance') or 1.0)
    except (TypeError, ValueError):
        tolerance = 1.0

    from .register_backfill import backfill
    try:
        summary = backfill(period, company, f.read(),
                           filename=getattr(f, 'name', '') or '',
                           commit=commit, tolerance=tolerance)
    except Exception as e:   # noqa: BLE001
        return Response({'detail': f'Could not process the register: {e}'}, status=400)
    if not summary.get('ok'):
        return Response({'detail': summary.get('error', 'Backfill failed.')}, status=400)
    return Response(summary, status=200)
