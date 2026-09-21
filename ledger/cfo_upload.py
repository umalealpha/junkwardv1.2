"""
ledger/cfo_upload.py — CFO-only bulk-upload endpoints.

Three endpoints, all gated to CFO / admin / superuser + override password:

  POST /api/v1/admin/cfo-upload-tb/   — Trial balance (Odoo format, legacy)
  POST /api/v1/admin/cfo-upload-coa/  — Chart of Accounts (CFO format, new)
  POST /api/v1/admin/cfo-upload-gl/   — General Ledger balances (CFO format, new)

Plus three GET endpoints returning blank CSV templates:

  GET /api/v1/admin/cfo-upload-coa/template/
  GET /api/v1/admin/cfo-upload-gl/template/
  GET /api/v1/admin/cfo-upload-status/

The CoA + GL endpoints accept the CFO-mandated column schema
(see _COA_COLUMNS / _GL_COLUMNS below). They are intentionally simpler than
import_tb_csv: no Odoo prefix inference, no period rollup — just the
columns the CFO specified, parsed verbatim.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company, UserProfile, user_has_permission

import logging as _logging
from django.utils import timezone
_log = _logging.getLogger('omni.tb_upload')

# SECURITY (2026-07-17 audit): no hardcoded override password. The value comes
# only from the OMNI_FINANCIAL_LOCK_OVERRIDE env var; when unset the lock is
# fail-closed and cannot be overridden.
DEFAULT_OVERRIDE = None
ZERO = Decimal('0.00')


# ─── Auto-unlock-upload-relock for TB upload (CFO directive 2026-05-26) ───────
#
# A TB CSV posts one JournalEntry per period via import_tb_csv. JE.post()
# requires an OPEN FiscalPeriod covering the entry date (see
# FiscalPeriod.get_open_period_for_date — it filters status=OPEN). If the
# target period is LOCKED the post fails with "no open fiscal period".
#
# This helper resolves which FiscalPeriods the CSV targets, temporarily
# unlocks the locked ones (status → OPEN, keeping the cfo/fm signoff FKs
# intact), and returns a snapshot the caller restores in a finally block —
# so periods are ALWAYS re-locked, even if the import raises mid-way.
#
# Only status is flipped. Signature FKs (locked_by_cfo / locked_by_fm) are
# never touched, so re-locking is a pure status restore — the dual-sign
# audit trail is preserved.

def _resolve_target_period_pks(raw_text: str) -> list:
    """FiscalPeriod PKs whose date-range covers any period the CSV targets."""
    import calendar
    import csv as _csv
    import io as _io
    from datetime import date as _date
    from ledger.models import FiscalPeriod
    try:
        from ledger.management.commands.import_tb_csv import PERIOD_DATES
    except Exception:        # noqa: BLE001
        PERIOD_DATES = {}

    csv_periods = set()
    reader = _csv.DictReader(_io.StringIO(raw_text))
    for row in reader:
        p = (row.get('Period') or '').strip()
        if p:
            csv_periods.add(p)

    entry_dates = []
    for p in csv_periods:
        if p in PERIOD_DATES:
            entry_dates.append(PERIOD_DATES[p])
            continue
        # Plain monthly label, e.g. "2025-07"
        if len(p) == 7 and p[4] == '-':
            try:
                y, m = int(p[:4]), int(p[5:7])
                last = calendar.monthrange(y, m)[1]
                entry_dates.append(_date(y, m, last))
            except ValueError:
                pass

    pks = set()
    for d in entry_dates:
        for fp in FiscalPeriod.objects.filter(start_date__lte=d, end_date__gte=d):
            pks.add(fp.pk)
    return list(pks)


def _unlock_target_periods(period_pks: list) -> list:
    """Flip locked target periods to OPEN. Return snapshots for re-locking."""
    from ledger.models import FiscalPeriod
    snapshots = []
    for pk in period_pks:
        fp = FiscalPeriod.objects.filter(pk=pk).first()
        if fp is None or fp.status == FiscalPeriod.Status.OPEN:
            continue
        snapshots.append({'pk': pk, 'status': fp.status,
                          'period_name': fp.period_name,
                          'company': fp.company.code if fp.company_id else 'system'})
        FiscalPeriod.objects.filter(pk=pk).update(status=FiscalPeriod.Status.OPEN)
        _log.warning(
            'TB upload: auto-unlocking period %s (entity=%s) — was %s, now OPEN',
            fp.period_name, snapshots[-1]['company'], snapshots[-1]['status'],
        )
    return snapshots


def _relock_periods(snapshots: list) -> None:
    """Restore the exact prior lock status for every unlocked period."""
    from ledger.models import FiscalPeriod
    for snap in snapshots:
        FiscalPeriod.objects.filter(pk=snap['pk']).update(status=snap['status'])
        _log.warning(
            'TB upload: re-locking period %s (entity=%s) — restored to %s',
            snap['period_name'], snap['company'], snap['status'],
        )


# ─── Authorisation helpers ────────────────────────────────────────────────────

def _user_is_cfo(user: User) -> bool:
    """Authorised to use the CFO bulk-upload pages.

    CFO directive 2026-05-17 (evening): widened from "CFO only" to also
    accept Finance Manager and Financial Controller titles, since the
    CFO is delegating the day-to-day upload of CoA / GL / TB / treaty
    data to the finance team. The override password is still required
    on every commit, so the maker/checker control is preserved.

    Eligible:
      - Django superuser
      - Role-based `cfo-upload` permission (e.g. BULK_UPLOADER role)
      - UserProfile.is_administrator
      - title in {CFO, Finance Manager, Financial Controller}
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # CFO directive 2026-05-19 (BULK_UPLOADER role): the `cfo-upload`
    # permission is an explicit grant that bypasses the title/profile check,
    # so service accounts (e.g. excoboard) without a UserProfile can still
    # access the CFO upload endpoints.
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


def _check_override(request) -> tuple[bool, Response | None]:
    """Verify the financial-lock override password.

    CFO directive 2026-05-19: when env var
    ``OMNI_FINANCIAL_LOCK_BYPASS=1`` is set, the password check is
    skipped so external tooling (Manus AI re-import window) can post
    without knowing the password. The bypass is opt-in per deploy —
    DO NOT set it permanently; unset after the upload window closes.

    Without the bypass, behaviour is unchanged: an `override_password`
    field must be in the request body and must equal
    ``OMNI_FINANCIAL_LOCK_OVERRIDE`` (defaults to ``DEFAULT_OVERRIDE``).
    """
    if (os.environ.get('OMNI_FINANCIAL_LOCK_BYPASS') or '').strip() == '1':
        import logging
        logging.getLogger(__name__).warning(
            'OMNI_FINANCIAL_LOCK_BYPASS=1 — financial lock skipped for %s.',
            request.path,
        )
        return True, None

    import hmac
    submitted = (request.data.get('override_password') or '').strip()
    expected = os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE') or DEFAULT_OVERRIDE
    if not expected:
        return False, Response(
            {'error': 'Financial-lock override is not configured on the server.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not submitted:
        return False, Response(
            {'error': 'override_password is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not hmac.compare_digest(submitted, expected):
        return False, Response(
            {'error': 'Wrong override password.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return True, None


def _check_cfo(request) -> Response | None:
    if not _user_is_cfo(request.user):
        return Response(
            {'error': 'CFO-only endpoint. Your account does not have authority.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


# ─── CSV helpers ──────────────────────────────────────────────────────────────

def _decimal(s) -> Decimal:
    s = (s or '').strip().replace(',', '')
    if not s or s.lower() == 'none':
        return ZERO
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return ZERO


def _normalise_header(row: dict) -> dict:
    """Lowercase + strip CSV header keys for forgiving column matching."""
    return {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}


# ─── Existing TB upload (legacy Odoo format) ──────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def cfo_upload_tb(request):
    """Upload an Odoo-format TB CSV and run import_tb_csv --commit on it."""
    if (deny := _check_cfo(request)):
        return deny
    ok, err = _check_override(request)
    if not ok:
        return err

    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response(
            {'error': 'file is required (multipart/form-data, key="file").'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not uploaded.name.lower().endswith(('.csv', '.txt')):
        return Response(
            {'error': f'File must be .csv. Got: {uploaded.name}'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if uploaded.size > 50 * 1024 * 1024:
        return Response({'error': 'File too large (max 50 MB).'},
                        status=status.HTTP_400_BAD_REQUEST)

    head = uploaded.read(2048).decode('utf-8-sig', errors='replace')
    uploaded.seek(0)
    required_columns = ['Period', 'Row Type', 'Account Code',
                        'End Balance Debit (BWP)', 'End Balance Credit (BWP)']
    missing = [c for c in required_columns if c not in head]
    if missing:
        return Response(
            {'error': f'CSV is missing required column(s): {missing}. '
                      f'Expected the alpha_direct_full_tb_complete.csv format.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # CFO directive 2026-05-24: dup-row control on TB upload. A row is
    # a duplicate if the same (Period, Row Type, Account Code) triple
    # appears twice — that would double-post the JE line and silently
    # corrupt the balance. Override flag bypasses (CFO emergency only).
    allow_duplicates = str(request.data.get('allow_duplicates', 'false')).lower() == 'true'
    raw_text = uploaded.read().decode('utf-8-sig', errors='replace')
    uploaded.seek(0)
    seen_keys: dict[tuple, int] = {}
    dup_rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(raw_text))
    for ln, row in enumerate(reader, start=2):
        key = (
            (row.get('Period') or '').strip(),
            (row.get('Row Type') or '').strip(),
            (row.get('Account Code') or '').strip(),
        )
        if not key[2] or key[1] != 'ACCOUNT':
            continue
        if key in seen_keys:
            dup_rows.append({
                'line': ln,
                'period': key[0], 'row_type': key[1], 'account_code': key[2],
                'first_seen_line': seen_keys[key],
            })
            continue
        seen_keys[key] = ln
    if dup_rows and not allow_duplicates:
        return Response({
            'error': f'{len(dup_rows)} duplicate (Period, Row Type, Account Code) '
                     f'row(s) in TB. Would double-post the JE. Fix the file or '
                     f'resubmit with allow_duplicates=true to keep first occurrence.',
            'duplicate_rows': dup_rows[:50],
            'duplicate_count': len(dup_rows),
        }, status=status.HTTP_400_BAD_REQUEST)

    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / f'cfo_upload_{request.user.id}_{uploaded.name}'
    with tmp_path.open('wb') as f:
        for chunk in uploaded.chunks():
            f.write(chunk)

    # Auto-unlock-upload-relock: temporarily open any locked target period so
    # the import can post, then restore the exact prior lock state — always,
    # even on error (CFO directive 2026-05-26). raw_text was read above.
    unlocked_snapshots = []
    try:
        unlocked_snapshots = _unlock_target_periods(
            _resolve_target_period_pks(raw_text)
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning('TB upload: period pre-unlock scan failed (non-fatal): %s', exc)

    captured = io.StringIO()
    try:
        call_command('import_tb_csv', file=str(tmp_path), commit=True, stdout=captured)
        success, log = True, captured.getvalue()
    except Exception as exc:  # noqa: BLE001
        success = False
        log = f'{captured.getvalue()}\n\nERROR: {exc.__class__.__name__}: {exc}'
    finally:
        # ALWAYS re-lock — even if the import raised mid-batch.
        try:
            _relock_periods(unlocked_snapshots)
        except Exception as exc:  # noqa: BLE001
            _log.error('TB upload: re-lock FAILED for %s: %s',
                       [s['period_name'] for s in unlocked_snapshots], exc)
        try: tmp_path.unlink()
        except OSError: pass

    from django.db.models import Sum
    from ledger.models import JournalEntryLine
    summary = {}
    try:
        adic = Company.objects.filter(code='ADIC').first()
        if adic:
            for label, dt in [('fy25_close', date_cls(2025, 6, 30)),
                              ('fy26_9m_close', date_cls(2026, 3, 31))]:
                bank_dr = JournalEntryLine.objects.filter(
                    journal_entry__company=adic, journal_entry__status='posted',
                    journal_entry__entry_date=dt, account__code__startswith='280',
                ).aggregate(t=Sum('debit_bwp'))['t']
                bank_cr = JournalEntryLine.objects.filter(
                    journal_entry__company=adic, journal_entry__status='posted',
                    journal_entry__entry_date=dt, account__code__startswith='280',
                ).aggregate(t=Sum('credit_bwp'))['t']
                summary[f'bank_{label}_net'] = str((bank_dr or 0) - (bank_cr or 0))
    except Exception as exc:  # noqa: BLE001
        summary['summary_error'] = str(exc)

    return Response(
        {'success': success, 'filename': uploaded.name, 'size_bytes': uploaded.size,
         'uploaded_by': request.user.username,
         'log_tail': '\n'.join(log.splitlines()[-30:]), 'summary': summary},
        status=status.HTTP_200_OK if success else status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ─── NEW: Chart of Accounts upload (CFO-mandated format, 2026-05-17) ──────────

_COA_COLUMNS = [
    'company',          # Company.code (e.g. ADIC) — informational
    'account_code',     # unique key (string, max 20)
    'account_name',     # display label
    'statement_class',  # BS or PNL
    'fs_line_item',     # free-text: 'Cash and bank', 'Crossover', etc.
    'normal_balance',   # D or C
]

# Map (statement_class, normal_balance_dc) → (account_type, sub_type) defaults.
# These mirror the existing PREFIX_TYPE_MAP semantics in import_tb_csv so the
# rest of the system (balance sheet / P&L reports) continues to work for
# CFO-uploaded accounts. Sub_type is intentionally generic — the CFO's
# fs_line_item is the *display* taxonomy; sub_type stays for downstream
# report bucketing.
_TYPE_INFERENCE = {
    ('BS',  'D'): ('asset',     'current_asset'),
    ('BS',  'C'): ('liability', 'current_liability'),
    ('PNL', 'D'): ('expense',   'operating_expense'),
    ('PNL', 'C'): ('revenue',   'operating_revenue'),
}


def _parse_coa_csv(uploaded) -> tuple[list[dict], list[dict], list[str] | None]:
    """Parse a CoA CSV into (valid_rows, error_rows, missing_columns).

    valid_rows  : list of normalised dicts ready for upsert
    error_rows  : list of {line, code, error} dicts for malformed rows
    missing_cols: None if the header is fine, else the list of missing columns
    """
    text = uploaded.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], [], []

    header_lower = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in _COA_COLUMNS if c not in header_lower]
    if missing:
        return [], [], missing

    valid_rows: list[dict] = []
    error_rows: list[dict] = []

    for line_no, raw in enumerate(reader, start=2):
        row = _normalise_header(raw)
        code = row.get('account_code', '').strip()
        name = row.get('account_name', '').strip()
        sclass = row.get('statement_class', '').strip().upper()
        fs_line = row.get('fs_line_item', '').strip()
        dc = row.get('normal_balance', '').strip().upper()
        company_code = row.get('company', '').strip()

        if not code:
            continue  # silent skip on blank-code rows (CSV padding)

        if sclass not in ('BS', 'PNL'):
            error_rows.append({'line': line_no, 'code': code,
                               'error': f'statement_class must be BS or PNL, got "{sclass}"'})
            continue
        if dc not in ('D', 'C'):
            error_rows.append({'line': line_no, 'code': code,
                               'error': f'normal_balance must be D or C, got "{dc}"'})
            continue
        if not name:
            error_rows.append({'line': line_no, 'code': code,
                               'error': 'account_name is required'})
            continue

        valid_rows.append({
            'line': line_no,
            'account_code': code,
            'account_name': name,
            'statement_class': sclass,
            'fs_line_item': fs_line,
            'normal_balance': dc,
            'company': company_code,
        })

    # CFO directive 2026-05-24: in-file duplicate-account-code control.
    # Same account_code appearing more than once = reject (the second
    # row silently overwriting the first was a known footgun).
    first_seen: dict[str, int] = {}
    deduped: list[dict] = []
    for r in valid_rows:
        code = r['account_code']
        if code in first_seen:
            error_rows.append({
                'line':  r['line'],
                'code':  code,
                'error': f'duplicate account_code "{code}" '
                         f'(first seen on line {first_seen[code]})',
            })
            continue
        first_seen[code] = r['line']
        deduped.append(r)

    return deduped, error_rows, None


def _commit_coa_rows(rows: list[dict], *, archive_unmatched: bool) -> dict:
    """Apply parsed CoA rows to the database. Returns a result dict."""
    from ledger.models import Account
    created = updated = unchanged = 0
    seen_codes: set[str] = set()
    row_errors: list[dict] = []

    with transaction.atomic():
        for r in rows:
            owner = None
            comp = r.get('company') or ''
            if comp:
                owner = Company.objects.filter(code__iexact=comp).first()
                if not owner:
                    row_errors.append({'line': r['line'], 'code': r['account_code'],
                                       'error': f'unknown company "{comp}"'})
                    continue

            atype, stype = _TYPE_INFERENCE[(r['statement_class'], r['normal_balance'])]

            obj, was_created = Account.objects.get_or_create(
                code=r['account_code'],
                defaults={
                    'name': r['account_name'],
                    'account_type': atype,
                    'sub_type': stype,
                    'statement_class': r['statement_class'],
                    'fs_line_item': r['fs_line_item'],
                    'normal_balance_dc': r['normal_balance'],
                    'owner_company': owner,
                    'is_archived': False,
                },
            )
            seen_codes.add(r['account_code'])
            if was_created:
                created += 1
                continue

            changed = False
            if obj.name != r['account_name']:
                obj.name = r['account_name']; changed = True
            if obj.statement_class != r['statement_class']:
                obj.statement_class = r['statement_class']; changed = True
            if obj.fs_line_item != r['fs_line_item']:
                obj.fs_line_item = r['fs_line_item']; changed = True
            if obj.normal_balance_dc != r['normal_balance']:
                obj.normal_balance_dc = r['normal_balance']; changed = True
            if obj.owner_company_id != (owner.id if owner else None):
                obj.owner_company = owner; changed = True
            if not obj.account_type:
                obj.account_type = atype; obj.sub_type = stype; changed = True
            elif r['statement_class'] == 'BS' and obj.account_type in ('revenue', 'expense'):
                obj.account_type = atype; obj.sub_type = stype; changed = True
            elif r['statement_class'] == 'PNL' and obj.account_type in ('asset', 'liability', 'equity'):
                obj.account_type = atype; obj.sub_type = stype; changed = True
            if obj.is_archived:
                obj.is_archived = False; changed = True
            if changed:
                obj.save()
                updated += 1
            else:
                unchanged += 1

        archived = 0
        if archive_unmatched and seen_codes:
            qs = Account.objects.exclude(code__in=seen_codes).filter(is_archived=False)
            archived = qs.update(is_archived=True)

    return {
        'created': created, 'updated': updated, 'unchanged': unchanged,
        'archived': archived, 'archive_unmatched': archive_unmatched,
        'row_errors': row_errors,
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def cfo_upload_coa(request):
    """
    Upload a Chart of Accounts CSV.

    Form-data:
      file              : the CSV
      override_password : OMNI_FINANCIAL_LOCK_OVERRIDE
      preview           : if 'true', parse + DeepSeek review only — no write.
                          Otherwise commit.
      archive_unmatched : default 'true'. Only used on commit.

    Required columns: company, account_code, account_name, statement_class,
    fs_line_item, normal_balance. See _COA_COLUMNS.
    """
    if (deny := _check_cfo(request)):
        return deny
    ok, err = _check_override(request)
    if not ok:
        return err

    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response({'error': 'file is required (multipart/form-data, key="file").'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not uploaded.name.lower().endswith(('.csv', '.txt')):
        return Response({'error': f'File must be .csv. Got: {uploaded.name}'},
                        status=status.HTTP_400_BAD_REQUEST)
    if uploaded.size > 20 * 1024 * 1024:
        return Response({'error': 'File too large (max 20 MB).'},
                        status=status.HTTP_400_BAD_REQUEST)

    preview_mode = str(request.data.get('preview', 'false')).lower() == 'true'
    archive_unmatched = str(request.data.get('archive_unmatched', 'true')).lower() == 'true'
    allow_duplicates  = str(request.data.get('allow_duplicates', 'false')).lower() == 'true'

    valid_rows, error_rows, missing_cols = _parse_coa_csv(uploaded)
    if missing_cols == []:
        return Response({'error': 'CSV is empty or missing a header row.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if missing_cols:
        return Response(
            {'error': f'CSV is missing required column(s): {missing_cols}',
             'expected_columns': _COA_COLUMNS},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # CFO directive 2026-05-24: in-file duplicate-account-code control.
    # Hard reject the whole upload if any code appears twice. CFO can
    # bypass with allow_duplicates=true (then second-occurrence rows
    # are silently skipped — first wins).
    dup_errors = [e for e in error_rows if 'duplicate account_code' in e['error']]
    if dup_errors and not allow_duplicates and not preview_mode:
        return Response({
            'error': f'{len(dup_errors)} duplicate account_code(s) in file. '
                     f'Fix the file or resubmit with allow_duplicates=true to '
                     f'keep the first occurrence and skip the rest.',
            'duplicate_rows': dup_errors[:50],
            'duplicate_count': len(dup_errors),
        }, status=status.HTTP_400_BAD_REQUEST)

    if preview_mode:
        # Run AI review on the parsed rows — never blocks if DeepSeek is down.
        try:
            from core.ai_assist import review_coa_upload
            ai_review = review_coa_upload(valid_rows)
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
    result = _commit_coa_rows(valid_rows, archive_unmatched=archive_unmatched)
    all_errors = error_rows + result['row_errors']
    return Response({
        'success': True,
        'preview': False,
        'filename': uploaded.name,
        'uploaded_by': request.user.username,
        'created': result['created'],
        'updated': result['updated'],
        'unchanged': result['unchanged'],
        'archived': result['archived'],
        'archive_unmatched': result['archive_unmatched'],
        'error_rows': all_errors[:50],
        'error_count': len(all_errors),
    }, status=status.HTTP_200_OK)


# ─── NEW: General Ledger balances upload ──────────────────────────────────────

_GL_COLUMNS = [
    'company',           # Company.code, used to scope the JE
    'account_code',      # links to Account.code; auto-created if missing
    'account_name',      # used when auto-creating an Account
    'amount',            # absolute or signed value (we read both, but DC wins)
    'dc',                # 'D' or 'C' — which side of the JE this lands on
    'statement_class',   # BS or PNL — used to auto-tag any new account
    'fs_line_item',      # free-text label, applied to the Account
]


def _parse_gl_csv(uploaded, default_company_code: str) -> tuple[list[dict], list[dict], list[str] | None]:
    """Parse a GL CSV into (valid_rows, error_rows, missing_cols).

    valid_rows  : list of {line, company_code, account_code, account_name,
                  amount (Decimal, positive), dc, statement_class, fs_line_item}
    error_rows  : list of {line, code, error} dicts
    missing_cols: None if header is fine, else the list of missing columns
    """
    text = uploaded.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], [], []

    header_lower = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in _GL_COLUMNS if c not in header_lower]
    if missing:
        return [], [], missing

    valid_rows: list[dict] = []
    error_rows: list[dict] = []

    for line_no, raw in enumerate(reader, start=2):
        row = _normalise_header(raw)
        code = row.get('account_code', '').strip()
        name = row.get('account_name', '').strip()
        amt_raw = row.get('amount', '').strip()
        dc = row.get('dc', '').strip().upper()
        sclass = row.get('statement_class', '').strip().upper()
        fs_line = row.get('fs_line_item', '').strip()
        comp_code = row.get('company', '').strip() or default_company_code

        if not code:
            continue
        if dc not in ('D', 'C'):
            error_rows.append({'line': line_no, 'code': code,
                               'error': f'dc must be D or C, got "{dc}"'})
            continue
        if sclass and sclass not in ('BS', 'PNL'):
            error_rows.append({'line': line_no, 'code': code,
                               'error': f'statement_class must be BS or PNL or blank, got "{sclass}"'})
            continue

        amount = abs(_decimal(amt_raw))
        if amount == ZERO:
            continue  # skip zero-amount rows silently

        valid_rows.append({
            'line': line_no,
            'company_code': comp_code,
            'account_code': code,
            'account_name': name,
            'amount': amount,
            'dc': dc,
            'statement_class': sclass,
            'fs_line_item': fs_line,
        })

    return valid_rows, error_rows, None


def _gl_totals(rows: list[dict]) -> dict:
    total_dr = sum((r['amount'] for r in rows if r['dc'] == 'D'), ZERO)
    total_cr = sum((r['amount'] for r in rows if r['dc'] == 'C'), ZERO)
    return {
        'debit': str(total_dr), 'credit': str(total_cr),
        'diff': str(total_dr - total_cr),
        'in_balance': abs(total_dr - total_cr) <= Decimal('100.00'),
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def cfo_upload_gl(request):
    """
    Upload a General Ledger balances CSV. Produces ONE balanced JournalEntry
    with one line per CSV row (skipping zero-amount rows).

    Required form-data:
      file:              the CSV (columns above)
      override_password: lock override
    Optional form-data:
      as_of_date:        YYYY-MM-DD, the entry_date for the JE.
                         Defaults to last day of the previous month.
      company:           default Company.code if a row has blank `company`.
                         Defaults to 'ADIC'.
      description:       JE description (defaults to filename).

    Behaviour:
      - Rows are validated for required fields + sane (statement_class, dc).
      - Accounts not yet in the CoA are AUTO-CREATED (so the CFO can iterate
        without an explicit CoA upload first). Auto-created accounts are
        tagged with the CSV's classification.
      - Sum of debits must equal sum of credits to within 100 BWP (rounding
        tolerance); a Rounding Suspense line absorbs the diff if needed
        (same pattern as import_tb_csv).
      - One JE is created and posted directly.
    """
    if (deny := _check_cfo(request)):
        return deny
    ok, err = _check_override(request)
    if not ok:
        return err

    uploaded = request.FILES.get('file')
    if not uploaded:
        return Response(
            {'error': 'file is required (multipart/form-data, key="file").'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not uploaded.name.lower().endswith(('.csv', '.txt')):
        return Response({'error': f'File must be .csv. Got: {uploaded.name}'},
                        status=status.HTTP_400_BAD_REQUEST)
    if uploaded.size > 50 * 1024 * 1024:
        return Response({'error': 'File too large (max 50 MB).'},
                        status=status.HTTP_400_BAD_REQUEST)

    as_of_raw = (request.data.get('as_of_date') or '').strip()
    if as_of_raw:
        try:
            as_of = date_cls.fromisoformat(as_of_raw)
        except ValueError:
            return Response({'error': f'as_of_date must be YYYY-MM-DD, got "{as_of_raw}"'},
                            status=status.HTTP_400_BAD_REQUEST)
    else:
        # Last day of previous month
        today = timezone.localdate()
        first_of_this = today.replace(day=1)
        from datetime import timedelta
        as_of = first_of_this - timedelta(days=1)

    default_company_code = (request.data.get('company') or 'ADIC').strip()
    description = (request.data.get('description') or f'GL upload — {uploaded.name}').strip()
    preview_mode = str(request.data.get('preview', 'false')).lower() == 'true'

    default_company = Company.objects.filter(code__iexact=default_company_code).first()
    if not default_company:
        return Response({'error': f'Default company "{default_company_code}" not found.'},
                        status=status.HTTP_400_BAD_REQUEST)

    valid_rows, parse_errors, missing_cols = _parse_gl_csv(uploaded, default_company_code)
    if missing_cols == []:
        return Response({'error': 'CSV is empty or missing a header row.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if missing_cols:
        return Response(
            {'error': f'CSV is missing required column(s): {missing_cols}',
             'expected_columns': _GL_COLUMNS},
            status=status.HTTP_400_BAD_REQUEST,
        )

    totals = _gl_totals(valid_rows)

    if preview_mode:
        try:
            from core.ai_assist import review_gl_upload
            ai_review = review_gl_upload(
                [{'account_code': r['account_code'], 'account_name': r['account_name'],
                  'amount': str(r['amount']), 'dc': r['dc'],
                  'statement_class': r['statement_class']} for r in valid_rows],
                totals=totals, as_of_date=as_of.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            ai_review = {'verdict': 'unavailable', 'reason': str(exc)}

        return Response({
            'success': True,
            'preview': True,
            'filename': uploaded.name,
            'row_count': len(valid_rows),
            'error_count': len(parse_errors),
            'error_rows': parse_errors[:50],
            'as_of_date': as_of.isoformat(),
            'company': default_company_code,
            'totals': totals,
            'sample_rows': [
                {'account_code': r['account_code'], 'account_name': r['account_name'],
                 'amount': str(r['amount']), 'dc': r['dc'],
                 'statement_class': r['statement_class'],
                 'fs_line_item': r['fs_line_item']}
                for r in valid_rows[:20]
            ],
            'ai_review': ai_review,
        }, status=status.HTTP_200_OK)

    # ── Commit path ─────────────────────────────────────────────────────────
    from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine
    from core.models import Currency
    from django.core.exceptions import ValidationError as DjangoValidationError

    sys_user = User.objects.filter(is_superuser=True).order_by('id').first()
    if not sys_user:
        return Response({'error': 'No superuser exists to attribute the import to.'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    Currency.objects.get_or_create(
        code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
    )

    parsed = []
    error_rows = list(parse_errors)
    accounts_created = 0

    try:
        with transaction.atomic():
            for r in valid_rows:
                line_no = r['line']
                code = r['account_code']
                name = r['account_name']
                dc = r['dc']
                sclass = r['statement_class']
                fs_line = r['fs_line_item']
                comp_code = r['company_code']
                amount = r['amount']

                company = Company.objects.filter(code__iexact=comp_code).first()
                if not company:
                    error_rows.append({'line': line_no, 'code': code,
                                       'error': f'unknown company "{comp_code}"'})
                    continue

                try:
                    acct = Account.objects.get(code=code)
                    # Update classification if blank and CSV provides it
                    if sclass and not acct.statement_class:
                        acct.statement_class = sclass
                        if not acct.normal_balance_dc:
                            acct.normal_balance_dc = dc
                        if fs_line and not acct.fs_line_item:
                            acct.fs_line_item = fs_line
                        acct.save(update_fields=[
                            'statement_class', 'normal_balance_dc', 'fs_line_item',
                        ])
                except Account.DoesNotExist:
                    if not sclass:
                        error_rows.append({'line': line_no, 'code': code,
                                           'error': 'account does not exist and statement_class is blank; '
                                                    'either upload the CoA first or include statement_class'})
                        continue
                    atype, stype = _TYPE_INFERENCE[(sclass, dc)]
                    acct = Account.objects.create(
                        code=code, name=name or code,
                        account_type=atype, sub_type=stype,
                        statement_class=sclass, fs_line_item=fs_line,
                        normal_balance_dc=dc, owner_company=company,
                    )
                    accounts_created += 1

                if acct.is_summary_only:
                    error_rows.append({'line': line_no, 'code': code,
                                       'error': 'account is summary-only; cannot post JE lines'})
                    continue

                parsed.append({
                    'company': company, 'account': acct,
                    'amount': amount, 'dc': dc,
                })

            if error_rows:
                transaction.set_rollback(True)
                return Response({
                    'success': False,
                    'error': f'{len(error_rows)} row(s) failed validation; nothing was imported.',
                    'error_rows': error_rows[:50],
                    'error_count': len(error_rows),
                }, status=status.HTTP_400_BAD_REQUEST)

            if not parsed:
                return Response({'success': False, 'error': 'No postable rows found in CSV.'},
                                status=status.HTTP_400_BAD_REQUEST)

            # ── Phase 2: balance check + JE creation ────────────────────────────
            total_dr = sum((p['amount'] for p in parsed if p['dc'] == 'D'), ZERO)
            total_cr = sum((p['amount'] for p in parsed if p['dc'] == 'C'), ZERO)
            rounding_diff = total_dr - total_cr

            if abs(rounding_diff) > Decimal('100.00'):
                transaction.set_rollback(True)
                return Response({
                    'success': False,
                    'error': f'CSV does not balance: total Dr {total_dr} vs total Cr {total_cr} '
                             f'(diff {rounding_diff}). Tolerance is 100 BWP for rounding.',
                    'totals': {'debit': str(total_dr), 'credit': str(total_cr),
                               'diff': str(rounding_diff)},
                }, status=status.HTTP_400_BAD_REQUEST)

            # Resolve scoping company (most common in CSV)
            from collections import Counter
            company_counts = Counter(p['company'].id for p in parsed)
            scope_company_id = company_counts.most_common(1)[0][0]
            scope_company = Company.objects.get(id=scope_company_id)

            # Ensure fiscal period exists
            period_name = f'{as_of.year}-{as_of.month:02d}'
            if not FiscalPeriod.objects.filter(period_name=period_name).exists():
                import calendar
                last = calendar.monthrange(as_of.year, as_of.month)[1]
                FiscalPeriod.objects.create(
                    period_name=period_name,
                    start_date=date_cls(as_of.year, as_of.month, 1),
                    end_date=date_cls(as_of.year, as_of.month, last),
                    status=FiscalPeriod.Status.OPEN,
                )

            je = JournalEntry.objects.create(
                entry_date=as_of,
                description=description,
                source_type='cfo_gl_upload',
                journal_type=JournalEntry.JournalType.GENERAL,
                status=JournalEntry.Status.DRAFT,
                company=scope_company,
                currency_code_id='BWP',
                created_by=sys_user,
                is_related_party=False,
            )

            for p in parsed:
                if p['dc'] == 'D':
                    dr, cr = p['amount'], ZERO
                else:
                    dr, cr = ZERO, p['amount']
                JournalEntryLine.objects.create(
                    journal_entry=je, account=p['account'],
                    description='CFO GL upload',
                    debit_amount=dr, credit_amount=cr,
                    debit_bwp=dr, credit_bwp=cr,
                )

            if rounding_diff != ZERO:
                rounding, _ = Account.objects.get_or_create(
                    code='999999',
                    defaults={
                        'name': 'TB Import Rounding Suspense',
                        'account_type': 'equity', 'sub_type': 'rounding_suspense',
                    },
                )
                if rounding_diff > ZERO:
                    JournalEntryLine.objects.create(
                        journal_entry=je, account=rounding,
                        description='CFO GL upload rounding (Dr-Cr diff)',
                        debit_amount=ZERO, credit_amount=rounding_diff,
                        debit_bwp=ZERO, credit_bwp=rounding_diff,
                    )
                else:
                    JournalEntryLine.objects.create(
                        journal_entry=je, account=rounding,
                        description='CFO GL upload rounding (Cr-Dr diff)',
                        debit_amount=abs(rounding_diff), credit_amount=ZERO,
                        debit_bwp=abs(rounding_diff), credit_bwp=ZERO,
                    )

            je.post(user=sys_user, _allow_direct=True)
    except DjangoValidationError as exc:
        # Surface the lock / validation reason to the caller (Manus) as 400
        # instead of a bare 500 with no body. The atomic() block has already
        # rolled back, so no JE was persisted.
        messages = getattr(exc, 'messages', None) or [str(exc)]
        return Response({
            'success': False,
            'error': '; '.join(str(m) for m in messages),
            'error_type': 'ValidationError',
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:  # noqa: BLE001
        # Last-line guard so the caller always gets a useful error string.
        return Response({
            'success': False,
            'error': f'{type(exc).__name__}: {exc}',
            'error_type': type(exc).__name__,
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        'success': True,
        'filename': uploaded.name,
        'uploaded_by': request.user.username,
        'as_of_date': as_of.isoformat(),
        'company': scope_company.code,
        'journal_entry_number': je.entry_number,
        'line_count': je.lines.count(),
        'accounts_auto_created': accounts_created,
        'totals': {
            'debit': str(total_dr), 'credit': str(total_cr),
            'rounding_diff': str(rounding_diff),
        },
    }, status=status.HTTP_200_OK)


# ─── CSV templates (downloadable) ─────────────────────────────────────────────

_COA_TEMPLATE_ROWS = [
    ['ADIC', '1110', 'FNB BWP Operating Account',          'BS',  'Cash and bank',                'D'],
    ['ADIC', '2140', 'Vendor Payable',                     'BS',  'Trade and other payables',     'C'],
    ['ADIC', '4100', 'Gross Written Premium',              'PNL', 'Gross written premium',        'C'],
    ['ADIC', '5100', 'Claims Incurred - Gross',            'PNL', 'Gross claims incurred',        'D'],
    ['ADIC', '9999', 'Crossover / Inter-company',          'BS',  'Crossover',                    'D'],
]

_GL_TEMPLATE_ROWS = [
    ['ADIC', '1110', 'FNB BWP Operating Account', '12883982.11', 'D', 'BS',  'Cash and bank'],
    ['ADIC', '2140', 'Vendor Payable',            '4523109.00',  'C', 'BS',  'Trade and other payables'],
    ['ADIC', '4100', 'Gross Written Premium',     '125202058.61','C', 'PNL', 'Gross written premium'],
    ['ADIC', '5100', 'Claims Incurred - Gross',   '78514329.00', 'D', 'PNL', 'Gross claims incurred'],
]


def _csv_response(headers: list[str], rows: list[list[str]], filename: str) -> HttpResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow(r)
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cfo_upload_coa_template(request):
    if (deny := _check_cfo(request)):
        return deny
    return _csv_response(_COA_COLUMNS, _COA_TEMPLATE_ROWS, 'coa_template.csv')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cfo_upload_gl_template(request):
    if (deny := _check_cfo(request)):
        return deny
    return _csv_response(_GL_COLUMNS, _GL_TEMPLATE_ROWS, 'gl_template.csv')


# ─── Status probe (used by frontend gate) ─────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cfo_upload_status(request):
    """Reports whether the current user is authorised to use the CFO upload page."""
    return Response({
        'is_cfo': _user_is_cfo(request.user),
        'username': request.user.username,
        'override_password_configured': bool(
            os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE') or DEFAULT_OVERRIDE
        ),
    })
