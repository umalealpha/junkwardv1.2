"""
budgets/views.py

API views for Budget management.

CFO directive 2026-06-17 (Oprah bug 5ccd4c77): Finance had no surface to enter
or upload budget figures, so the Budget vs Actual report was always empty. Added
two write paths used by the new Budget Setup screen:

  POST /api/v1/budgets/set-lines/   {fiscal_period, department, lines:[...]}
        Upsert the Budget for (period, department) and REPLACE its lines from
        the payload — powers the manual grid (enter an amount per GL account).

  POST /api/v1/budgets/upload/      multipart: file, fiscal_period, department
        Parse a CSV/XLSX (columns: account_code | amount [| notes]) and set the
        budget lines the same way — powers the file upload.

Both, plus create/update/add-line/approve, are restricted to Finance
(superuser / administrator / CFO / finance-manager / financial-controller, or a
user holding the `budget.manage` permission). Reads stay open to any
authenticated user so the report renders for everyone.
"""

import csv as _csv
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from django.utils import timezone

from ledger.models import Account, FiscalPeriod
from .models import Budget, BudgetLine
from .serializers import (
    BudgetListSerializer,
    BudgetDetailSerializer,
    BudgetCreateSerializer,
    BudgetLineSerializer,
)

FINANCE_TITLES = {'cfo', 'finance_manager', 'financial_controller'}


def _can_manage_budget(user) -> bool:
    """True if `user` may create/edit budgets — Finance only."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    prof = getattr(user, 'profile', None)
    if prof is not None and getattr(prof, 'is_administrator', False):
        return True
    if prof is not None and (getattr(prof, 'title', '') or '').lower() in FINANCE_TITLES:
        return True
    try:
        from core.models import user_has_permission
        if user_has_permission(user, 'budget.manage'):
            return True
    except Exception:        # noqa: BLE001
        pass
    return False


def _budget_snapshot(budget):
    """Small totals snapshot used as audit old/new values."""
    from django.db.models import Sum
    agg = budget.lines.aggregate(t=Sum('amount'))
    return {
        'status': budget.status,
        'line_count': budget.lines.count(),
        'total_amount': str(agg['t'] or '0.00'),
    }


def _audit_budget(*, user, budget, action, old, new, description, request=None):
    """Write an immutable AuditLog row for a budget change (anti-manipulation).
    CFO/Oprah 2026-06-17: budgets must be traceable — who changed what, when."""
    try:
        from core.models import AuditLog
        ip = None
        if request is not None:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR'))
        AuditLog.objects.create(
            table_name='Budget',
            record_id=str(budget.pk),
            action=action,
            old_values=old,
            new_values=new,
            user=user if getattr(user, 'is_authenticated', False) else None,
            ip_address=ip,
            description=description,
        )
    except Exception:        # noqa: BLE001 — never let audit failure block the write
        import logging
        logging.getLogger('budgets').exception('budget audit log failed')


def _dec(v) -> Decimal:
    if v is None or str(v).strip() == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v).replace(',', '').replace('P', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _read_budget_table(file_obj):
    """Return [{account_code, amount, notes}] from a CSV or XLSX upload."""
    raw = file_obj.read()
    name = (getattr(file_obj, 'name', '') or '').lower()
    is_xlsx = raw[:4] == b'PK\x03\x04' or name.endswith(('.xlsx', '.xlsm'))
    if is_xlsx:
        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        table = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        text = raw.decode('utf-8-sig', errors='replace')
        table = list(_csv.reader(StringIO(text)))
    if not table:
        return []
    headers = [('' if c is None else str(c)).strip().lower() for c in table[0]]

    def col(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return None

    ci = col('account_code', 'account code', 'code', 'account', 'gl', 'gl code', 'gl_code')
    ai = col('amount', 'budget', 'budget_amount', 'budgeted', 'value')
    ni = col('notes', 'note', 'comment')
    rows = []
    for r in table[1:]:
        if r is None or all(c is None or str(c).strip() == '' for c in r):
            continue
        code = str(r[ci]).strip() if ci is not None and ci < len(r) and r[ci] is not None else ''
        if not code:
            continue
        rows.append({
            'account_code': code,
            'amount': _dec(r[ai]) if ai is not None and ai < len(r) else Decimal('0.00'),
            'notes': (str(r[ni]).strip() if ni is not None and ni < len(r) and r[ni] is not None else ''),
        })
    return rows


def _upsert_budget_lines(*, period, department, line_rows, user, description=''):
    """Create-or-update the Budget for (period, department) and replace its lines.

    `line_rows` items carry EITHER `account` (id/uuid) OR `account_code`. Unknown
    codes are skipped and reported. Returns (budget, applied, errors)."""
    budget, _ = Budget.objects.get_or_create(
        fiscal_period=period, department=department,
        defaults={'created_by': user, 'description': description or ''},
    )
    if description and budget.description != description:
        budget.description = description
        budget.save(update_fields=['description', 'updated_at'])

    applied, errors = 0, []
    resolved = {}   # account_id -> (amount, notes)
    for i, row in enumerate(line_rows, start=1):
        acct = None
        if row.get('account'):
            acct = Account.objects.filter(pk=row['account']).first()
        if acct is None and row.get('account_code'):
            code = str(row['account_code']).strip()
            acct = (Account.objects.filter(code=code).first()
                    or Account.objects.filter(code__iexact=code).first())
        if acct is None:
            errors.append({'row': i, 'error': f"Account not found: {row.get('account_code') or row.get('account')!r}"})
            continue
        amt = row['amount'] if isinstance(row.get('amount'), Decimal) else _dec(row.get('amount'))
        resolved[acct.id] = (amt, row.get('notes', ''))

    # Replace the budget's lines with the resolved set (skip zero+empty rows so
    # the grid can clear an account by sending 0/blank).
    BudgetLine.objects.filter(budget=budget).delete()
    for acct_id, (amt, notes) in resolved.items():
        if amt == Decimal('0.00') and not notes:
            continue
        BudgetLine.objects.create(budget=budget, account_id=acct_id, amount=amt, notes=notes or '')
        applied += 1
    return budget, applied, errors


class BudgetViewSet(viewsets.ModelViewSet):
    """
    CRUD for budgets.

    list:     GET  /api/v1/budgets/
    create:   POST /api/v1/budgets/
    detail:   GET  /api/v1/budgets/{id}/
    update:   PUT/PATCH /api/v1/budgets/{id}/
    approve:  POST /api/v1/budgets/{id}/approve/
    add-line: POST /api/v1/budgets/{id}/add-line/
    set-lines:POST /api/v1/budgets/set-lines/   (bulk grid save)
    upload:   POST /api/v1/budgets/upload/      (CSV/XLSX)
    """

    queryset = Budget.objects.select_related(
        'fiscal_period', 'created_by', 'approved_by'
    ).prefetch_related('lines__account').all()

    filterset_fields = ['department', 'status']
    search_fields = ['description', 'fiscal_period__period_name']
    ordering_fields = ['created_at', 'fiscal_period__start_date']

    def _guard_write(self, request):
        if not _can_manage_budget(request.user):
            return Response(
                {'detail': 'Budget editing is restricted to Finance '
                           '(CFO / Finance Manager / Financial Controller / admin).'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def get_serializer_class(self):
        if self.action == 'create':
            return BudgetCreateSerializer
        if self.action in ('retrieve',):
            return BudgetDetailSerializer
        return BudgetListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        fiscal_period = self.request.query_params.get('fiscal_period')
        if fiscal_period:
            qs = qs.filter(fiscal_period_id=fiscal_period)
        period_name = self.request.query_params.get('period_name')
        if period_name:
            qs = qs.filter(fiscal_period__period_name=period_name)
        department = self.request.query_params.get('department')
        if department:
            qs = qs.filter(department=department)
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        denied = self._guard_write(request)
        return denied if denied is not None else super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        denied = self._guard_write(request)
        return denied if denied is not None else super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        denied = self._guard_write(request)
        return denied if denied is not None else super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """POST /api/v1/budgets/{id}/approve/ — mark budget as approved."""
        denied = self._guard_write(request)
        if denied is not None:
            return denied
        budget = self.get_object()
        if budget.status == Budget.Status.APPROVED:
            return Response(
                {'detail': 'Budget is already approved.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not budget.lines.exists():
            return Response(
                {'detail': 'Cannot approve a budget with no line items.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        old = _budget_snapshot(budget)
        budget.status = Budget.Status.APPROVED
        budget.approved_by = request.user
        budget.approved_at = timezone.now()
        budget.save()
        from core.models import AuditLog
        _audit_budget(user=request.user, budget=budget, action=AuditLog.Action.APPROVE,
                      old=old, new=_budget_snapshot(budget),
                      description=(f'Approved & locked budget {budget.fiscal_period.period_name} / '
                                   f'{budget.get_department_display()} by {request.user.username}'),
                      request=request)
        return Response(BudgetDetailSerializer(budget).data)

    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        """POST /api/v1/budgets/{id}/revise/ — unlock an approved budget for editing.

        Finance-only and audited: an approved (locked) budget can ONLY be changed
        after an explicit, logged revise — so figures can't be manipulated silently.
        """
        denied = self._guard_write(request)
        if denied is not None:
            return denied
        budget = self.get_object()
        if budget.status != Budget.Status.APPROVED:
            return Response({'detail': 'Only an approved budget can be revised.'},
                            status=status.HTTP_400_BAD_REQUEST)
        old = _budget_snapshot(budget)
        budget.status = Budget.Status.REVISED
        budget.approved_by = None
        budget.approved_at = None
        budget.save()
        from core.models import AuditLog
        _audit_budget(user=request.user, budget=budget, action=AuditLog.Action.UPDATE,
                      old=old, new=_budget_snapshot(budget),
                      description=(f'Unlocked (revise) budget {budget.fiscal_period.period_name} / '
                                   f'{budget.get_department_display()} by {request.user.username}'),
                      request=request)
        return Response(BudgetDetailSerializer(budget).data)

    @action(detail=True, methods=['post'], url_path='add-line')
    def add_line(self, request, pk=None):
        """POST /api/v1/budgets/{id}/add-line/ — add a budget line."""
        denied = self._guard_write(request)
        if denied is not None:
            return denied
        budget = self.get_object()
        if budget.status == Budget.Status.APPROVED:
            return Response(
                {'detail': 'Cannot modify an approved budget.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = BudgetLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(budget=budget)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='set-lines')
    def set_lines(self, request):
        """POST /api/v1/budgets/set-lines/ — bulk grid save.

        body: {fiscal_period, department, description?, lines:[{account|account_code,
               amount, notes?}]}  → upserts the Budget and replaces its lines.
        """
        denied = self._guard_write(request)
        if denied is not None:
            return denied
        data = request.data or {}
        period = FiscalPeriod.objects.filter(pk=data.get('fiscal_period')).first()
        if period is None:
            return Response({'detail': 'Valid fiscal_period is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        department = (data.get('department') or Budget.Department.MASTER)
        if department not in dict(Budget.Department.choices):
            return Response({'detail': f'Unknown department {department!r}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        existing = Budget.objects.filter(fiscal_period=period, department=department).first()
        if existing and existing.status == Budget.Status.APPROVED:
            return Response({'detail': 'This budget is approved and locked. Revise it first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        old = _budget_snapshot(existing) if existing else {'status': 'new', 'line_count': 0, 'total_amount': '0.00'}
        lines = data.get('lines') or []
        budget, applied, errors = _upsert_budget_lines(
            period=period, department=department, line_rows=lines,
            user=request.user, description=data.get('description', ''),
        )
        from core.models import AuditLog
        _audit_budget(user=request.user, budget=budget, action=AuditLog.Action.UPDATE,
                      old=old, new=_budget_snapshot(budget),
                      description=(f'Set {applied} budget line(s) (manual) for {period.period_name} / '
                                   f'{budget.get_department_display()} by {request.user.username}'),
                      request=request)
        out = BudgetDetailSerializer(budget).data
        out['applied'] = applied
        out['errors'] = errors
        out['message'] = (f'{applied} budget line(s) saved for '
                          f'{period.period_name} / {budget.get_department_display()}'
                          + (f', {len(errors)} skipped' if errors else '') + '.')
        return Response(out)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload(self, request):
        """POST /api/v1/budgets/upload/ — CSV/XLSX budget upload.

        multipart: file, fiscal_period, department. Columns: account_code | amount [| notes].
        """
        denied = self._guard_write(request)
        if denied is not None:
            return denied
        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': 'Attach a .csv or .xlsx file in the "file" field.'},
                            status=status.HTTP_400_BAD_REQUEST)
        period = FiscalPeriod.objects.filter(pk=request.data.get('fiscal_period')).first()
        if period is None:
            return Response({'detail': 'Valid fiscal_period is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        department = (request.data.get('department') or Budget.Department.MASTER)
        if department not in dict(Budget.Department.choices):
            return Response({'detail': f'Unknown department {department!r}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        existing = Budget.objects.filter(fiscal_period=period, department=department).first()
        if existing and existing.status == Budget.Status.APPROVED:
            return Response({'detail': 'This budget is approved and locked. Revise it first.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            rows = _read_budget_table(f)
        except Exception as exc:    # noqa: BLE001
            return Response({'detail': f'Could not read the file: {exc}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({'detail': 'No data rows found. Expected columns: account_code, amount.'},
                            status=status.HTTP_400_BAD_REQUEST)
        old = _budget_snapshot(existing) if existing else {'status': 'new', 'line_count': 0, 'total_amount': '0.00'}
        budget, applied, errors = _upsert_budget_lines(
            period=period, department=department, line_rows=rows, user=request.user,
        )
        from core.models import AuditLog
        _audit_budget(user=request.user, budget=budget, action=AuditLog.Action.UPDATE,
                      old=old, new=_budget_snapshot(budget),
                      description=(f'Loaded {applied} budget line(s) (upload) for {period.period_name} / '
                                   f'{budget.get_department_display()} by {request.user.username}'),
                      request=request)
        out = BudgetDetailSerializer(budget).data
        out['applied'] = applied
        out['errors'] = errors
        out['message'] = (f'{applied} budget line(s) loaded for '
                          f'{period.period_name} / {budget.get_department_display()}'
                          + (f', {len(errors)} skipped' if errors else '') + '.')
        return Response(out)


# ===========================================================================
# Budget Library (BudgetPack) — a home for each year's budget pack.
# CFO directive 2026-06-27 ("a place where we can keep our budgets").
# ===========================================================================
from decimal import Decimal as _D, InvalidOperation as _IO

from django.http import FileResponse, Http404
from django.utils.dateparse import parse_date as _parse_date
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated

from .models import BudgetPack, BudgetPackFile


def _pack_dict(p, request):
    return {
        'id': str(p.id), 'fy_label': p.fy_label,
        'company': p.company.code if p.company_id else None,
        'company_name': (p.company.name if p.company_id else 'Group / consolidated'),
        'period_start': p.period_start.isoformat() if p.period_start else None,
        'period_end': p.period_end.isoformat() if p.period_end else None,
        'status': p.status, 'status_label': p.get_status_display(),
        'scenario': p.scenario,
        'gwp_target': float(p.gwp_target) if p.gwp_target is not None else None,
        'ebitda': float(p.ebitda) if p.ebitda is not None else None,
        'pat': float(p.pat) if p.pat is not None else None,
        'ebitda_margin': float(p.ebitda_margin) if p.ebitda_margin is not None else None,
        'notes': p.notes, 'source': p.source,
        'created_at': p.created_at.isoformat(),
        'files': [{
            'id': str(f.id), 'kind': f.kind, 'kind_label': f.get_kind_display(),
            'label': f.label or (f.file.name.split('/')[-1] if f.file else ''),
            'filename': (f.file.name.split('/')[-1] if f.file else ''),
            'size': (f.file.size if f.file else 0),
            'download_url': request.build_absolute_uri(f'/api/v1/budgets/pack-files/{f.id}/download/'),
        } for f in p.files.all()],
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def budget_packs(request):
    """GET list every saved budget pack; POST create a new one (Finance only)."""
    if request.method == 'GET':
        qs = BudgetPack.objects.select_related('company').prefetch_related('files').all()
        return Response({'count': qs.count(), 'packs': [_pack_dict(p, request) for p in qs]})

    if not _can_manage_budget(request.user):
        return Response({'detail': 'Budget management is restricted to Finance.'}, status=403)
    d = request.data or {}

    def _dec(k):
        v = d.get(k)
        if v in (None, ''):
            return None
        try:
            return _D(str(v))
        except (_IO, ValueError):
            return None

    company = None
    code = (d.get('company') or '').strip()
    if code:
        from core.models import Company
        company = Company.objects.filter(code__iexact=code).first()
    try:
        p = BudgetPack.objects.create(
            fy_label=(d.get('fy_label') or '').strip()[:20] or 'FY?',
            company=company,
            period_start=_parse_date(d.get('period_start') or ''),
            period_end=_parse_date(d.get('period_end') or ''),
            status=(d.get('status') or 'draft').strip() or 'draft',
            scenario=(d.get('scenario') or '').strip()[:60],
            gwp_target=_dec('gwp_target'), ebitda=_dec('ebitda'),
            pat=_dec('pat'), ebitda_margin=_dec('ebitda_margin'),
            notes=(d.get('notes') or '').strip(),
            source=(d.get('source') or '').strip()[:120],
            created_by=request.user if request.user.is_authenticated else None,
        )
    except Exception as exc:  # noqa: BLE001
        return Response({'detail': f'Could not create budget pack: {exc}'}, status=400)
    return Response(_pack_dict(p, request), status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def budget_pack_upload(request, pk):
    """Attach a file (model / deck / calculator) to a budget pack (Finance only)."""
    if not _can_manage_budget(request.user):
        return Response({'detail': 'Budget management is restricted to Finance.'}, status=403)
    p = BudgetPack.objects.filter(pk=pk).first()
    if p is None:
        return Response({'detail': 'Budget pack not found.'}, status=404)
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a file in the "file" field.'}, status=400)
    kind = (request.data.get('kind') or 'other').strip()
    if kind not in {c[0] for c in BudgetPackFile.Kind.choices}:
        kind = 'other'
    BudgetPackFile.objects.create(
        pack=p, file=f, kind=kind,
        label=(request.data.get('label') or getattr(f, 'name', '') or '').strip()[:200])
    return Response(_pack_dict(p, request), status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def budget_pack_file_download(request, fid):
    bf = BudgetPackFile.objects.filter(pk=fid).first()
    if bf is None or not bf.file:
        raise Http404('Budget file not found.')
    return FileResponse(bf.file.open('rb'), as_attachment=True,
                        filename=(bf.file.name.split('/')[-1] or 'budget'))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def budget_advisor(request):
    """Advisory only: hand the simulator's levers + computed outputs to DeepSeek and
    return its read on the scenario. Never recomputes the P&L, never writes the DB."""
    from . import advisor
    d = request.data or {}
    scenario = {
        'levers': d.get('levers') or {},
        'outputs': d.get('outputs') or {},
        'question': d.get('question') or '',
        'sweeps': d.get('sweeps') or {},
    }
    return Response(advisor.ask(scenario))


# ===========================================================================
# Strategic Plan Library (PlanPack) — the archive behind the 5-Year Plan Cockpit.
# Finance request 2026-08-04: "work the same way the budget library and budget
# cockpit work", so this mirrors budget_packs above field for field.
# ===========================================================================
from .models import PlanPack, PlanPackFile
from .uploads import normalise_upload

import logging as _logging

log = _logging.getLogger('budgets')


def _plan_dict(p, request):
    return {
        'id': str(p.id),
        'entity': p.entity,
        'label': p.label,
        'scenario_slug': p.scenario_slug,
        'scenario_label': p.get_scenario_slug_display(),
        'status': p.status,
        'status_label': p.get_status_display(),
        'prepared_by': p.prepared_by,
        'prepared_date': p.prepared_date.isoformat() if p.prepared_date else None,
        'department': p.department,
        # Empty while a draft — the page then computes from the shared model so a
        # draft can never disagree with the Cockpit. Frozen verbatim on approval.
        'base_figures': p.base_figures or {},
        'figures_locked': p.figures_locked,
        'assumptions': p.assumptions or [],
        'narrative': p.narrative,
        'approved_at': p.approved_at.isoformat() if p.approved_at else None,
        'created_at': p.created_at.isoformat(),
        'files': [{
            'id': str(f.id), 'kind': f.kind, 'kind_label': f.get_kind_display(),
            'label': f.label or (f.file.name.split('/')[-1] if f.file else ''),
            'filename': (f.file.name.split('/')[-1] if f.file else ''),
            'size': (f.file.size if f.file else 0),
            'download_url': request.build_absolute_uri(
                f'/api/v1/plan-packs/files/{f.id}/download/'),
        } for f in p.files.all()],
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def plan_packs(request):
    """GET every saved plan pack; POST create one (Finance only)."""
    if request.method == 'GET':
        qs = PlanPack.objects.prefetch_related('files').all()
        entity = (request.query_params.get('entity') or '').strip()
        if entity:
            qs = qs.filter(entity__iexact=entity)
        st = (request.query_params.get('status') or '').strip()
        if st:
            qs = qs.filter(status=st)
        return Response({
            'count': qs.count(),
            'can_manage': _can_manage_budget(request.user),
            'packs': [_plan_dict(p, request) for p in qs],
        })

    if not _can_manage_budget(request.user):
        return Response({'detail': 'The strategic plan library is restricted to Finance.'},
                        status=403)
    d = request.data or {}
    label = (d.get('label') or '').strip()[:120]
    if not label:
        return Response({'detail': 'A plan needs a label, e.g. "FY2026–FY2030 · Base Case".'},
                        status=400)
    scenario = (d.get('scenario_slug') or 'base').strip()
    if scenario not in {c[0] for c in PlanPack.Scenario.choices}:
        return Response({'detail': f'Unknown scenario "{scenario}".'}, status=400)
    try:
        p = PlanPack.objects.create(
            entity=(d.get('entity') or 'AD_INSURTECH').strip()[:40],
            label=label,
            scenario_slug=scenario,
            status=(d.get('status') or 'draft').strip() or 'draft',
            prepared_by=(d.get('prepared_by') or '').strip()[:120],
            prepared_date=_parse_date(d.get('prepared_date') or '') or None,
            department=(d.get('department') or 'CFO Office').strip()[:120],
            base_figures=d.get('base_figures') or {},
            assumptions=d.get('assumptions') or [],
            narrative=(d.get('narrative') or '').strip()[:600],
            created_by=request.user if request.user.is_authenticated else None,
        )
    except Exception as exc:  # noqa: BLE001
        return Response({'detail': f'Could not create the plan pack: {exc}'}, status=400)
    return Response(_plan_dict(p, request), status=201)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def plan_pack_detail(request, pk):
    """GET one pack; PATCH its status / narrative / figures (Finance only).

    Approving is the one write with teeth: it freezes `base_figures` so the pack
    stops tracking the live model and becomes quotable. An approved pack's
    figures cannot be edited afterwards — archive it and raise a new one instead.
    """
    p = PlanPack.objects.prefetch_related('files').filter(pk=pk).first()
    if p is None:
        return Response({'detail': 'Plan pack not found.'}, status=404)
    if request.method == 'GET':
        return Response(_plan_dict(p, request))

    if not _can_manage_budget(request.user):
        return Response({'detail': 'The strategic plan library is restricted to Finance.'},
                        status=403)

    d = request.data or {}
    fields = []

    new_status = (d.get('status') or '').strip()
    if new_status:
        if new_status not in {c[0] for c in PlanPack.Status.choices}:
            return Response({'detail': f'Unknown status "{new_status}".'}, status=400)
        if new_status == PlanPack.Status.APPROVED and p.status != PlanPack.Status.APPROVED:
            figures = d.get('base_figures') or p.base_figures
            if not figures:
                return Response(
                    {'detail': 'Send the figures being approved (base_figures) so they '
                               'can be frozen. An approved plan must not keep moving.'},
                    status=400)
            p.base_figures = figures
            p.approved_by = request.user if request.user.is_authenticated else None
            p.approved_at = timezone.now()
            fields += ['base_figures', 'approved_by', 'approved_at']
        p.status = new_status
        fields.append('status')

    if p.figures_locked and 'base_figures' in d and not new_status:
        return Response(
            {'detail': 'This plan is approved, so its figures are locked. Archive it '
                       'and raise a new pack if the numbers have changed.'},
            status=400)

    for key, cast in (('narrative', lambda v: str(v)[:600]),
                      ('prepared_by', lambda v: str(v)[:120]),
                      ('label', lambda v: str(v)[:120]),
                      ('assumptions', lambda v: v),
                      ('base_figures', lambda v: v)):
        if key in d and not (key == 'base_figures' and 'base_figures' in fields):
            setattr(p, key, cast(d[key]))
            fields.append(key)

    if not fields:
        return Response({'detail': 'Nothing to change.'}, status=400)
    p.save(update_fields=list(dict.fromkeys(fields)) + ['updated_at'])
    return Response(_plan_dict(p, request))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def plan_pack_upload(request, pk):
    """Attach a file (workbook / spec / deck / app) to a plan pack (Finance only)."""
    if not _can_manage_budget(request.user):
        return Response({'detail': 'The strategic plan library is restricted to Finance.'},
                        status=403)
    p = PlanPack.objects.filter(pk=pk).first()
    if p is None:
        return Response({'detail': 'Plan pack not found.'}, status=404)
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a file in the "file" field.'}, status=400)
    kind = (request.data.get('kind') or 'other').strip()
    if kind not in {c[0] for c in PlanPackFile.Kind.choices}:
        kind = 'other'
    # Label from the name the uploader sent, before any .gz is stripped.
    label = (request.data.get('label') or getattr(f, 'name', '') or '').strip()[:200]
    # Files gzipped to get past the WAF must be stored decompressed, or every
    # download hands back a binary blob under an .html name.
    stored, note = normalise_upload(f)
    PlanPackFile.objects.create(pack=p, file=stored, kind=kind, label=label)
    if note:
        log.info('plan pack %s: %s %s', p.pk, label, note)
    return Response(_plan_dict(p, request), status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def plan_pack_file_download(request, fid):
    pf = PlanPackFile.objects.filter(pk=fid).first()
    if pf is None or not pf.file:
        raise Http404('Plan file not found.')
    return FileResponse(pf.file.open('rb'), as_attachment=True,
                        filename=(pf.file.name.split('/')[-1] or 'plan'))
