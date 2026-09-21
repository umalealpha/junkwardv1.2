"""payroll/bank_import_views.py — bulk bank-details upload with HR-Manager sign-off.

Flow (CFO 2026-07-15):
  * An uploader (Pako / Legakwa / Tshephang, or Unami / Dorothy / CFO) POSTs the
    salary bank file. We parse it, match each row to an Employee, derive the bank
    NAME from the branch code (payroll.bank_codes), and store a PENDING batch.
    NOTHING is written to any Employee at this point.
  * Dorothy (HR Manager) reviews the preview and approves → the matched rows
    commit to Employee.bank_account_no / bank_branch_code / bank_name (audited).
  * Or she rejects with a reason.

Maker-checker: the approver must be a DIFFERENT person from the uploader AND an
authorised approver. The system never keys account numbers — a human puts them
in the file and a human approves the commit.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.decorators import (
    api_view, parser_classes, permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from .bank_codes import account_is_mangled, resolve_bank
from .models import BankDetailImportBatch, Employee
# Reuse the exact, battle-tested employee matcher from the leave importer so a
# person is never mismatched to someone else's bank account.
from hris.leave_upload_views import (
    _build_employee_index, _match_employee, _norm_name, _read_xlsx,
)

User = get_user_model()

# Approver local-parts always allowed (Dorothy = HR Manager; Unami; the CFO).
_APPROVER_LOCAL_PARTS = {'dikgopoleng', 'ubutale', 'pganesharajah'}
# Column-header aliases (lower-cased) → our field.
_NAME_KEYS    = ('recipient name', 'name', 'full name', 'employee', 'employee name')
_ACCOUNT_KEYS = ('recipient account number', 'account number', 'account no',
                 'account', 'bank account', 'bank account no', 'bank_account_no')
_BRANCH_KEYS  = ('branch code', 'branch', 'branch_code', 'sort code', 'sort_code')


def _local_part(user) -> str:
    return (getattr(user, 'email', '') or getattr(user, 'username', '') or '').split('@')[0].lower()


def _user_title(user) -> str:
    prof = getattr(user, 'profile', None) or getattr(user, 'userprofile', None)
    return (getattr(prof, 'title', '') or '').lower() if prof else ''


def _can_approve_bank_import(user) -> bool:
    """HR Manager / Unami / Dorothy / CFO / superuser may approve a bank batch."""
    if not user or not user.is_authenticated:
        return False
    if _local_part(user) in _APPROVER_LOCAL_PARTS:
        return True
    if _user_title(user) in ('hr_manager', 'cfo'):
        return True
    return bool(getattr(user, 'is_superuser', False))


def _build_prefix_index():
    """[(norm_full_name, Employee)] for truncated-name matching.

    FNB salary files truncate the recipient name at 20 chars (e.g.
    'Ditso Pako Motlhaban' for 'Ditso Pako Motlhabane'), so the exact/first-last
    matcher misses them. This lets us treat the file name as a leading prefix.
    """
    return [(_norm_name(e.full_name), e)
            for e in Employee.objects.all().only('id', 'full_name')]


def _prefix_match(name, prefix_index):
    """Match a truncated file name → the ONE employee whose full name begins
    with it. Returns (Employee|None, matched_bool). Conservative on purpose:
      - requires ≥ 12 normalised chars (no short-name collisions),
      - requires EXACTLY ONE employee to start with the string (no guessing
        between candidates — money must never go to the wrong account).
    A hit is flagged 'verify' in the row so a human eyeballs it before approve.
    """
    key = _norm_name(name)
    if len(key) < 12:
        return None, False
    hits = [e for (nf, e) in prefix_index if nf.startswith(key)]
    # de-dupe by employee id (guards against duplicate employee rows)
    uniq = {e.id: e for e in hits}
    if len(uniq) == 1:
        return next(iter(uniq.values())), True
    return None, False


def _pick(row: dict, keys) -> str:
    for k in keys:
        if k in row and row[k] not in (None, ''):
            return row[k]
    return ''


def _account_to_str(val):
    """(clean_string, read_as_number) for an account cell.

    openpyxl (data_only) returns numeric cells as int/float — a long account can
    lose leading zeros or, past 15 digits, precision. Return the value AND a flag
    so the row can warn 'upload as Text'. Strings pass straight through.
    """
    if val is None:
        return '', False
    if isinstance(val, bool):
        return '', True
    if isinstance(val, int):
        return str(val), True                      # numeric read — leading zeros may be lost
    if isinstance(val, float):
        if val.is_integer() and abs(val) < 1e15:
            return str(int(val)), True
        return repr(val), True                     # precision-risk / exponent
    return str(val).strip(), False


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def bank_import_upload(request):
    """Parse the uploaded file → preview → save a PENDING batch. No commit."""
    if not user_can_access_hris(request.user):
        return Response({'detail': 'You do not have access to upload bank details.'}, status=403)

    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'Attach the bank file as "file".'}, status=400)

    headers, raw_rows = _read_xlsx(f)
    if not raw_rows:
        return Response({'detail': 'The file is empty or unreadable.'}, status=400)
    if not (set(_NAME_KEYS) & set(headers)):
        return Response({'detail': 'No name column found. Expected a "Recipient Name" (or "Name") column.'}, status=400)

    idx = _build_employee_index()
    prefix_index = _build_prefix_index()
    rows, matched, committable = [], 0, 0
    for i, r in enumerate(raw_rows, start=1):
        name = str(_pick(r, _NAME_KEYS) or '').strip()
        acct, numeric_read = _account_to_str(_pick(r, _ACCOUNT_KEYS))
        branch = str(_pick(r, _BRANCH_KEYS) or '').strip()

        emp, reason = _match_employee(name, idx)
        prefix_matched = False
        if emp is None:
            # Truncated-name fallback (FNB cuts recipient names at 20 chars).
            emp, prefix_matched = _prefix_match(name, prefix_index)
            if emp is not None:
                reason = None
        verdict = resolve_bank(acct, branch)
        warnings = [w for w in [verdict.get('warning', '')] if w]
        if prefix_matched:
            warnings.insert(0, f'Matched on truncated name → {emp.full_name}. VERIFY this is the right person before approving.')
        if numeric_read and not account_is_mangled(acct):
            warnings.append('Account was read as a number — verify leading zeros; upload with the '
                            'account column formatted as Text to be safe.')

        if emp is None:
            status = 'no_match'
            warnings.insert(0, reason or 'no employee match')
        elif not acct or not branch:
            status = 'incomplete'
            warnings.insert(0, 'Missing account number or branch code.')
        elif account_is_mangled(acct):
            status = 'account_check'
        else:
            status = 'ready' if verdict['bank_name'] else 'ready_no_bank'
            committable += 1

        if emp is not None:
            matched += 1
        rows.append({
            'row': i, 'name': name, 'account': acct, 'branch': branch,
            'matched': emp is not None,
            'employee_id': str(emp.id) if emp else None,
            'employee': emp.full_name if emp else None,
            'employee_number': emp.employee_number if emp else None,
            'bank_name': verdict['bank_name'], 'bank_source': verdict['source'],
            'status': status,
            'warning': ' '.join(warnings),
        })

    batch = BankDetailImportBatch.objects.create(
        file_name=(getattr(f, 'name', '') or '')[:255],
        rows_total=len(rows), rows_matched=matched, parsed_rows=rows,
        created_by=request.user,
    )
    batch.save(audit_user=request.user,
               audit_description=f'Bank import uploaded ({len(rows)} rows, {matched} matched)')
    return Response({
        'id': str(batch.id), 'status': batch.status,
        'rows_total': len(rows), 'rows_matched': matched, 'rows_committable': committable,
        'rows': rows,
    }, status=201)


def _serialize(b: BankDetailImportBatch, include_rows=False) -> dict:
    d = {
        'id': str(b.id), 'file_name': b.file_name, 'status': b.status,
        'status_display': b.get_status_display(),
        'rows_total': b.rows_total, 'rows_matched': b.rows_matched,
        'rows_committed': b.rows_committed,
        'created_by': getattr(b.created_by, 'username', None),
        'created_at': b.created_at.isoformat(),
        'approved_by': getattr(b.approved_by, 'username', None),
        'approved_at': b.approved_at.isoformat() if b.approved_at else None,
        'rejected_by': getattr(b.rejected_by, 'username', None),
        'rejection_reason': b.rejection_reason,
        'can_approve': False,   # set per-request below
    }
    if include_rows:
        d['rows'] = b.parsed_rows
    return d


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bank_import_list(request):
    if not user_can_access_hris(request.user):
        return Response({'detail': 'No access.'}, status=403)
    can_approve = _can_approve_bank_import(request.user)
    out = []
    for b in BankDetailImportBatch.objects.select_related(
            'created_by', 'approved_by', 'rejected_by')[:100]:
        d = _serialize(b)
        d['can_approve'] = can_approve and b.status == 'pending' and b.created_by_id != request.user.id
        out.append(d)
    return Response({'results': out, 'can_approve': can_approve})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bank_import_detail(request, pk):
    if not user_can_access_hris(request.user):
        return Response({'detail': 'No access.'}, status=403)
    try:
        b = BankDetailImportBatch.objects.select_related(
            'created_by', 'approved_by', 'rejected_by').get(pk=pk)
    except BankDetailImportBatch.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    d = _serialize(b, include_rows=True)
    d['can_approve'] = (_can_approve_bank_import(request.user)
                        and b.status == 'pending' and b.created_by_id != request.user.id)
    return Response(d)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bank_import_approve(request, pk):
    """Dorothy approves → commit each committable row to its Employee (audited)."""
    try:
        b = BankDetailImportBatch.objects.select_related('created_by').get(pk=pk)
    except BankDetailImportBatch.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    if b.status != 'pending':
        return Response({'detail': f'This batch is already {b.status}.'}, status=400)
    if not _can_approve_bank_import(request.user):
        return Response({'detail': 'Only an HR Manager (or CFO) may approve a bank-details import.'}, status=403)
    if b.created_by_id == request.user.id:
        return Response({'detail': 'The person who uploaded a batch cannot approve it — a second person must.'}, status=403)

    committed = 0
    for row in b.parsed_rows:
        if row.get('status') not in ('ready', 'ready_no_bank'):
            continue
        emp_id = row.get('employee_id')
        if not emp_id:
            continue
        try:
            emp = Employee.objects.get(pk=emp_id)
        except Employee.DoesNotExist:
            continue
        emp.bank_account_no  = str(row.get('account') or '')[:50]
        emp.bank_branch_code = str(row.get('branch') or '')[:20]
        if row.get('bank_name'):
            emp.bank_name = str(row['bank_name'])[:100]
        emp.save(audit_user=request.user,
                 audit_description=f'Bank details set from import {b.id} (approved by {request.user.username})')
        committed += 1

    b.status = 'approved'
    b.approved_by = request.user
    b.approved_at = timezone.now()
    b.rows_committed = committed
    b.save(audit_user=request.user,
           audit_description=f'Bank import approved — {committed} employees updated')
    return Response(_serialize(b, include_rows=True))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bank_import_reject(request, pk):
    try:
        b = BankDetailImportBatch.objects.get(pk=pk)
    except BankDetailImportBatch.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    if b.status != 'pending':
        return Response({'detail': f'This batch is already {b.status}.'}, status=400)
    if not _can_approve_bank_import(request.user):
        return Response({'detail': 'Only an HR Manager (or CFO) may reject a bank-details import.'}, status=403)
    b.status = 'rejected'
    b.rejected_by = request.user
    b.rejected_at = timezone.now()
    b.rejection_reason = str(request.data.get('reason', ''))[:2000]
    b.save(audit_user=request.user, audit_description='Bank import rejected')
    return Response(_serialize(b, include_rows=True))
