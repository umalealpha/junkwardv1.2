"""
reinsurance/treaty_upload.py — CFO-only bulk treaty upload endpoint.

CFO directive 2026-05-17: reinsurance is negotiated annually 1-Jul–30-Jun.
At the start of each treaty year the CFO uploads an Excel sheet with the
new treaties so they're captured in the system. Same DeepSeek preview →
commit pattern as the CoA / GL uploads.

POST /api/v1/admin/cfo-upload-treaty/
  - file: .xlsx or .csv
  - override_password: OMNI_FINANCIAL_LOCK_OVERRIDE
  - preview: 'true' → parse + AI review only; no DB write
GET  /api/v1/admin/cfo-upload-treaty/template/  — blank CSV template

Required columns (case-insensitive, header row in row 1 of sheet 1):
  treaty_number, description, reinsurer_code, treaty_type,
  line_of_business, inception_date, expiry_date, cession_share_percent,
  commission_percent, retention_amount, limit_amount, currency_code, notes

Behaviour:
  - Upsert by treaty_number. Updates description / dates / terms / status.
  - Looks up reinsurer by short_code (case-insensitive). If missing, the
    row errors — CFO must create the reinsurer first via Django admin
    or via /api/v1/reinsurers/ POST. (We deliberately do NOT auto-create
    counterparties — that's a control point.)
  - DeepSeek review runs on the parsed rows before commit, flagging
    common issues (missing share% on QS/Surplus, missing retention/limit
    on XL, off-cycle dates, etc).
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date as date_cls, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.http import HttpResponse

from core.models import UserProfile, user_has_permission

# SECURITY (2026-07-17 audit): no hardcoded override password — env var only,
# fail-closed when unset.
DEFAULT_OVERRIDE = None

# Mirror the model choices for friendly validation.
TREATY_TYPES = {'quota_share', 'surplus', 'xl', 'stop_loss', 'facultative'}
PROPORTIONAL = {'quota_share', 'surplus'}
NON_PROPORTIONAL = {'xl', 'stop_loss'}

REQUIRED_COLUMNS = [
    'treaty_number', 'description', 'reinsurer_code', 'treaty_type',
    'line_of_business', 'inception_date', 'expiry_date',
    'cession_share_percent', 'commission_percent',
    'retention_amount', 'limit_amount',
    'currency_code', 'notes',
]


# ─── Auth helpers (mirrors ledger/cfo_upload.py) ──────────────────────────────

def _user_is_cfo(user: User) -> bool:
    """Same eligibility as ledger.cfo_upload._user_is_cfo — widened on
    2026-05-17 to include Finance Manager + Financial Controller so the
    CFO can delegate treaty uploads to the finance team while the
    override password preserves the control point."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # Role-based bypass: service accounts with cfo-upload permission
    if user_has_permission(user, 'cfo-upload'):
        return True
    profile = getattr(user, 'userprofile', None) or getattr(user, 'profile', None)
    if not profile:
        return False
    if profile.is_administrator:
        return True
    if profile.title in (
        UserProfile.Title.CFO,
        UserProfile.Title.FINANCE_MANAGER,
        UserProfile.Title.FINANCIAL_CONTROLLER,
    ):
        return True
    return False


def _check_cfo(request) -> Response | None:
    if not _user_is_cfo(request.user):
        return Response(
            {'error': 'CFO-only endpoint. Your account does not have authority.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _check_override(request) -> tuple[bool, Response | None]:
    import hmac
    submitted = (request.data.get('override_password') or '').strip()
    expected = os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE') or DEFAULT_OVERRIDE
    if not expected:
        return False, Response(
            {'error': 'Financial-lock override is not configured on the server.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if not submitted:
        return False, Response({'error': 'override_password is required.'},
                               status=status.HTTP_400_BAD_REQUEST)
    if not hmac.compare_digest(submitted, expected):
        return False, Response({'error': 'Wrong override password.'},
                               status=status.HTTP_401_UNAUTHORIZED)
    return True, None


# ─── Cell helpers ─────────────────────────────────────────────────────────────

def _str(v: Any) -> str:
    if v is None:
        return ''
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, (date_cls, datetime)):
        return v.isoformat()[:10]
    return str(v).strip()


def _decimal(v: Any) -> Decimal | None:
    s = _str(v).replace(',', '').replace('%', '')
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> date_cls | None:
    if v is None or v == '':
        return None
    if isinstance(v, date_cls) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = _str(v)
    # Accept YYYY-MM-DD or DD/MM/YYYY
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ─── Parsing — XLSX via openpyxl, CSV as fallback ─────────────────────────────

def _rows_from_xlsx(uploaded) -> tuple[list[dict], list[str] | None]:
    """Returns (raw_rows_as_dicts, header_list_or_None_if_empty)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return [], None

    try:
        wb = load_workbook(uploaded, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001
        return [], None
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return [], None
    header_norm = [(_str(h)).lower() for h in (header or [])]
    out = []
    for r in rows_iter:
        if not r or all(c is None or _str(c) == '' for c in r):
            continue
        d = {header_norm[i]: r[i] for i in range(min(len(header_norm), len(r)))}
        out.append(d)
    return out, header_norm


def _rows_from_csv(uploaded) -> tuple[list[dict], list[str] | None]:
    text = uploaded.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], None
    header = [(h or '').strip().lower() for h in reader.fieldnames]
    out = []
    for raw in reader:
        d = {(k or '').strip().lower(): v for k, v in raw.items()}
        if not any((_str(v) for v in d.values())):
            continue
        out.append(d)
    return out, header


def _parse(uploaded, filename: str) -> tuple[list[dict], list[str] | None]:
    name = (filename or '').lower()
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        return _rows_from_xlsx(uploaded)
    return _rows_from_csv(uploaded)


# ─── Validation ───────────────────────────────────────────────────────────────

def _validate_row(line_no: int, row: dict) -> tuple[dict | None, dict | None]:
    """Returns (valid_dict, error_dict). Exactly one will be non-None."""
    treaty_number = _str(row.get('treaty_number'))
    if not treaty_number:
        return None, None  # silent skip on blank
    description = _str(row.get('description'))
    reinsurer_code = _str(row.get('reinsurer_code'))
    treaty_type = _str(row.get('treaty_type')).lower().replace(' ', '_')
    lob = _str(row.get('line_of_business'))
    inception = _date(row.get('inception_date'))
    expiry = _date(row.get('expiry_date'))
    share = _decimal(row.get('cession_share_percent'))
    commission = _decimal(row.get('commission_percent'))
    retention = _decimal(row.get('retention_amount'))
    limit_amt = _decimal(row.get('limit_amount'))
    currency = _str(row.get('currency_code')) or 'BWP'
    notes = _str(row.get('notes'))

    errs = []
    if not description:
        errs.append('description is required')
    if not reinsurer_code:
        errs.append('reinsurer_code is required')
    if not treaty_type or treaty_type not in TREATY_TYPES:
        errs.append(f'treaty_type must be one of {sorted(TREATY_TYPES)}, got "{treaty_type}"')
    if not lob:
        errs.append('line_of_business is required')
    if not inception:
        errs.append('inception_date is required (YYYY-MM-DD)')
    if not expiry:
        errs.append('expiry_date is required (YYYY-MM-DD)')
    if inception and expiry and expiry < inception:
        errs.append('expiry_date earlier than inception_date')
    if treaty_type in PROPORTIONAL and share is None:
        errs.append('cession_share_percent required for proportional treaties')
    if treaty_type in NON_PROPORTIONAL and (retention is None or limit_amt is None):
        errs.append('retention_amount AND limit_amount required for non-proportional treaties')

    if errs:
        return None, {'line': line_no, 'treaty_number': treaty_number,
                      'error': '; '.join(errs)}

    return {
        'line': line_no,
        'treaty_number': treaty_number,
        'description': description,
        'reinsurer_code': reinsurer_code,
        'treaty_type': treaty_type,
        'line_of_business': lob,
        'inception_date': inception.isoformat(),
        'expiry_date': expiry.isoformat(),
        'cession_share_percent': str(share) if share is not None else '',
        'commission_percent': str(commission) if commission is not None else '',
        'retention_amount': str(retention) if retention is not None else '',
        'limit_amount': str(limit_amt) if limit_amt is not None else '',
        'currency_code': currency,
        'notes': notes,
    }, None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def cfo_upload_treaty(request):
    if (deny := _check_cfo(request)):
        return deny
    ok, err = _check_override(request)
    if not ok:
        return err

    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'file is required (multipart/form-data, key="file").'},
                        status=status.HTTP_400_BAD_REQUEST)
    if uploaded.size > 20 * 1024 * 1024:
        return Response({'error': 'File too large (max 20 MB).'},
                        status=status.HTTP_400_BAD_REQUEST)
    name = uploaded.name.lower()
    if not (name.endswith('.csv') or name.endswith('.xlsx') or name.endswith('.xlsm') or name.endswith('.txt')):
        return Response({'error': f'File must be .csv or .xlsx, got {uploaded.name}'},
                        status=status.HTTP_400_BAD_REQUEST)

    preview_mode = str(request.data.get('preview', 'false')).lower() == 'true'

    rows, header = _parse(uploaded, uploaded.name)
    if header is None:
        return Response({'error': 'Could not read file. For Excel ensure data is on the first sheet with a header row.'},
                        status=status.HTTP_400_BAD_REQUEST)
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return Response(
            {'error': f'File is missing required column(s): {missing}',
             'expected_columns': REQUIRED_COLUMNS,
             'received_columns': header},
            status=status.HTTP_400_BAD_REQUEST,
        )

    valid_rows: list[dict] = []
    error_rows: list[dict] = []
    for i, r in enumerate(rows, start=2):
        ok_row, err_row = _validate_row(i, r)
        if err_row:
            error_rows.append(err_row)
        elif ok_row:
            valid_rows.append(ok_row)

    if preview_mode:
        try:
            from core.ai_assist import review_treaty_upload
            ai_review = review_treaty_upload(valid_rows)
        except Exception as exc:  # noqa: BLE001
            ai_review = {'verdict': 'unavailable', 'reason': str(exc)}
        return Response({
            'success': True,
            'preview': True,
            'filename': uploaded.name,
            'row_count': len(valid_rows),
            'error_count': len(error_rows),
            'error_rows': error_rows[:50],
            'sample_rows': valid_rows[:20],
            'ai_review': ai_review,
        }, status=status.HTTP_200_OK)

    # Commit path
    from django.db import transaction
    from reinsurance.models import Reinsurer, ReinsuranceTreaty
    from core.models import Currency

    sys_user = User.objects.filter(is_superuser=True).order_by('id').first()
    created = updated = unchanged = 0
    row_errors: list[dict] = list(error_rows)

    with transaction.atomic():
        for r in valid_rows:
            reinsurer = Reinsurer.objects.filter(
                short_code__iexact=r['reinsurer_code']
            ).first()
            if not reinsurer:
                row_errors.append({
                    'line': r['line'], 'treaty_number': r['treaty_number'],
                    'error': f'Unknown reinsurer_code "{r["reinsurer_code"]}". '
                             f'Create the reinsurer first.',
                })
                continue

            Currency.objects.get_or_create(
                code=r['currency_code'],
                defaults={'name': r['currency_code'], 'symbol': r['currency_code']},
            )

            defaults = {
                'description': r['description'],
                'reinsurer': reinsurer,
                'treaty_type': r['treaty_type'],
                'line_of_business': r['line_of_business'],
                'inception_date': r['inception_date'],
                'expiry_date': r['expiry_date'],
                'currency_code_id': r['currency_code'],
                'cession_share_percent': Decimal(r['cession_share_percent']) if r['cession_share_percent'] else None,
                'commission_percent': Decimal(r['commission_percent']) if r['commission_percent'] else None,
                'retention_amount': Decimal(r['retention_amount']) if r['retention_amount'] else None,
                'limit_amount': Decimal(r['limit_amount']) if r['limit_amount'] else None,
                'notes': r['notes'],
            }

            obj, was_created = ReinsuranceTreaty.objects.get_or_create(
                treaty_number=r['treaty_number'],
                defaults={**defaults, 'status': ReinsuranceTreaty.Status.DRAFT},
            )
            if was_created:
                created += 1
                continue

            changed = any(getattr(obj, k) != v for k, v in defaults.items()
                          if k != 'reinsurer' and not k.endswith('_id'))
            if obj.reinsurer_id != reinsurer.id:
                changed = True
            if changed:
                for k, v in defaults.items():
                    setattr(obj, k, v)
                if sys_user:
                    obj.save(audit_user=sys_user, audit_description='Updated via CFO treaty upload')
                else:
                    obj.save()
                updated += 1
            else:
                unchanged += 1

    return Response({
        'success': True,
        'preview': False,
        'filename': uploaded.name,
        'uploaded_by': request.user.username,
        'created': created,
        'updated': updated,
        'unchanged': unchanged,
        'error_rows': row_errors[:50],
        'error_count': len(row_errors),
    }, status=status.HTTP_200_OK)


_TEMPLATE_ROWS = [
    ['QS-MOTOR-FY26', 'Motor QS 35% — Africa Re', 'AFRICA_RE', 'quota_share',
     'Motor', '2025-07-01', '2026-06-30', '35.0000', '25.0000', '', '', 'BWP',
     'Renewed at same terms'],
    ['XL-PROP-FY26', 'Property XL 5M xs 2M — Munich Re', 'MUNICH_RE', 'xl',
     'Property', '2025-07-01', '2026-06-30', '', '', '2000000.00', '5000000.00',
     'BWP', '4 free reinstatements'],
    ['SURPLUS-LIAB-FY26', 'Liability Surplus 60% — Swiss Re', 'SWISS_RE', 'surplus',
     'Liability', '2025-07-01', '2026-06-30', '60.0000', '20.0000', '', '', 'BWP',
     ''],
]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cfo_upload_treaty_template(request):
    if (deny := _check_cfo(request)):
        return deny
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REQUIRED_COLUMNS)
    for r in _TEMPLATE_ROWS:
        w.writerow(r)
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="treaty_template.csv"'
    return resp
