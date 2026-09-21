"""
core/smart_upload/api_views.py — DRF endpoints for the Smart Upload widget.

Routes (wired in alpha_finance/api_router.py):

  GET  /api/v1/smart-upload/sections/
       List the supported section keys + their canonical fields.

  POST /api/v1/smart-upload/preview/   (multipart)
       Body: file=<upload>, section=<key>, company=<core.Company.code>
       Returns: {section, headers, mapping, missing_required, rows,
                 truncated, sample_rows, ai_used, source_hint}

  POST /api/v1/smart-upload/commit/    (application/json)
       Body: {section, company, rows: [{...mapped...}]}
       Returns: CommitReport (created / updated / skipped / errors / extra)

Auth: requires authenticated user. Bulk-import rights are enforced
inside the committers (per-company stamping) and by the existing
HRIS / CFO permission gates already in the affected ViewSets.
"""

from __future__ import annotations

import csv
import io
import json
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company, user_has_permission

from .extractors import extract, ExtractError
from .mapper import map_columns
from .sections import SECTIONS, get_section, list_sections
from .committers import COMMITTERS


log = logging.getLogger(__name__)


def _user_can_smart_upload(user) -> bool:
    """Authoriser for smart-upload write endpoints (parse / commit).

    Grants: Django superusers, UserProfile.is_administrator, the CFO /
    Finance Manager / Financial Controller titles (existing convention),
    and any user holding the `smart-upload` permission via UserRoleAssignment
    (CFO directive 2026-05-19, BULK_UPLOADER role).
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    try:
        profile = getattr(user, 'userprofile', None) or getattr(user, 'profile', None)
        if profile and getattr(profile, 'is_administrator', False):
            return True
        title = getattr(profile, 'title', None) if profile else None
        if title in ('cfo', 'finance_manager', 'financial_controller'):
            return True
    except Exception:    # noqa: BLE001
        pass
    return user_has_permission(user, 'smart-upload')


# ---------------------------------------------------------------------------
# GET /sections/
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sections_list(request):
    return Response({'sections': list_sections()})


# ---------------------------------------------------------------------------
# GET /template/?section=<key>
#   Returns a blank CSV with the canonical headers + a single row of
#   example values, so the CFO has a ready-to-fill template per section.
# ---------------------------------------------------------------------------

_EXAMPLES = {
    'coa':            ['400000', 'Sales — Motor', 'revenue', 'operating_revenue', '', 'N'],
    'tb':             ['400000', 'Sales — Motor', '0.00', '125150000.00', '2025-06-30', 'FY25'],
    'gl':             ['2025-04-30', 'INV-001', 'April commission', '1', '630001', '15000.00', '0.00', ''],
    'vendors':        ['SUP_001', 'Tirelo Pty Ltd', 'C04000000001', 'ops@tirelo.co.bw', '+267 71 555 1234', '30', '630001', 'Y'],
    'customers':      ['CUS_001', 'Acme Brokers', 'C04000000099', 'finance@acme.co.bw', '+267 71 555 9876', '30', '120000', 'Y'],
    'ppe':            ['AST_001', 'HP Laptop EliteBook', '151002', '13785.00', '0', '0', '2024-07-01', '2024-07-01', '36', 'straight_line', 'Head Office', 'IT'],
    'bank_accounts':  ['ADIC Operating', '101001', 'FNB Botswana', '281367', '62123456789', 'BWP', 'Y'],
    'employees':      ['EMP_001', 'Lerato', 'Modise', 'lerato@alphadirect.co.bw', '+267 71 555 0001', 'Finance Manager', 'Finance', '2024-02-01', '123456789', 'Y'],
    # Mirrors the payroll SectionSpec field order (33 columns). Period label is
    # a payroll MONTH (2026-07), never a fiscal year like "FY27".
    'payroll':        ['Lerato Modise', 'EMP_001', 'Finance', '2026-07', '2026-07-31',
                       '25000.00', '1500.00', '500.00', '0.00', '0.00', '750.00',
                       '3000.00', '0.00', '600.00', '2500.00', '400.00', '350.00',
                       '0.00', '250.00', '0.00', '0.00', '0.00',
                       '4200.00', '0.00', '0.00', '900.00', '1250.00', '0.00',
                       '900.00', '1250.00', '0.00',
                       '34850.00', '6350.00', '28500.00', '37000.00', 'Active'],
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def section_template(request):
    key = (request.query_params.get('section') or '').strip().lower()
    if key not in SECTIONS:
        return Response({'detail': f'Unknown section {key!r}.'}, status=400)
    section = get_section(key)

    headers = [f.name for f in section.fields]
    labels  = [f.label for f in section.fields]
    hints   = [f.hint  for f in section.fields]
    example = _EXAMPLES.get(key, ['' for _ in headers])
    # Pad / trim example to match headers
    if len(example) < len(headers):
        example = list(example) + [''] * (len(headers) - len(example))
    example = example[:len(headers)]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(headers)
    w.writerow(labels)
    w.writerow(hints)
    w.writerow(example)
    out.write(
        '# Smart Upload template for {label}. The first row is the canonical\n'
        '# field name (what the importer expects). Rows 2 and 3 are the\n'
        '# human label and hint for that field. Row 4 is one example.\n'
        '# Delete rows 2-4 before saving the CSV, then re-upload.\n'.format(
            label=section.label,
        )
    )

    resp = HttpResponse(out.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = (
        f'attachment; filename="smart_upload_{key}_template.csv"'
    )
    return resp


# ---------------------------------------------------------------------------
# POST /preview/
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# POST /autodetect/   (multipart)
#   Drop a file with NO section hint. The server inspects the headers,
#   scores them against every canonical SectionSpec, and returns the
#   best-matching section key + the same preview the section-specific
#   /preview/ would. CFO directive 2026-05-19 "Upload All Documents".
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def smart_upload_autodetect(request):
    if not _user_can_smart_upload(request.user):
        return Response({'detail': 'You do not have the smart-upload permission.'}, status=403)
    f = request.FILES.get('file')
    company_code = (request.data.get('company') or '').strip().upper()
    if not f:
        return Response({'detail': 'No file uploaded.'}, status=400)

    try:
        table = extract(f.read(), f.name)
    except ExtractError as e:
        return Response({'detail': f'File could not be parsed: {e}'}, status=400)
    if not table.headers:
        return Response({'detail': 'No tabular data found in this file.'}, status=400)

    # Score every section by how many required fields the heuristic can map.
    from .mapper import heuristic_map
    best_key = None
    best_score = -1
    best_total = 0
    for key, section in SECTIONS.items():
        mapping = heuristic_map(table.headers, section)
        mapped = set(mapping.values())
        required = [f.name for f in section.fields if f.required]
        score = sum(1 for r in required if r in mapped)
        total = len(required) or 1
        # Pick best required-coverage; tie-break on absolute mapped count.
        if score > best_score or (score == best_score and len(mapped) > best_total):
            best_key = key
            best_score = score
            best_total = len(mapped)

    if best_key is None:
        return Response(
            {'detail': "Couldn't infer section from headers.",
             'headers': table.headers},
            status=400,
        )

    section = SECTIONS[best_key]
    mapping, missing, ai_used = __import__(
        'core.smart_upload.mapper', fromlist=['map_columns'],
    ).map_columns(table.headers, table.rows[:3], section,
                  company_code=company_code)
    canonical_rows = []
    for r in table.rows:
        row_dict = {}
        for raw_h, canon in mapping.items():
            try:
                idx = table.headers.index(raw_h)
            except ValueError:
                continue
            row_dict[canon] = r[idx] if idx < len(r) else ''
        canonical_rows.append(row_dict)

    return Response({
        'section':           best_key,
        'section_label':     section.label,
        'company':           company_code,
        'source_hint':       table.source_hint,
        'truncated':         table.truncated,
        'headers':           table.headers,
        'mapping':           mapping,
        'missing_required':  missing,
        'ai_used':           ai_used,
        'preview_rows':      canonical_rows[:25],
        'rows':              canonical_rows,
        'row_count':         len(canonical_rows),
        'autodetect_score':  best_score,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def smart_upload_preview(request):
    if not _user_can_smart_upload(request.user):
        return Response({'detail': 'You do not have the smart-upload permission.'}, status=403)
    f = request.FILES.get('file')
    section_key = (request.data.get('section') or '').strip().lower()
    company_code = (request.data.get('company') or '').strip().upper()
    sheet = request.data.get('sheet') or None

    if not f:
        return Response({'detail': 'No file uploaded.'}, status=400)
    if section_key not in SECTIONS:
        return Response({'detail': f'Unknown section {section_key!r}.'}, status=400)

    section = get_section(section_key)

    if section.needs_company and not company_code:
        return Response(
            {'detail': 'company is required for this section.'}, status=400,
        )

    if company_code:
        if not Company.objects.filter(code=company_code).exists():
            return Response(
                {'detail': f'Unknown company {company_code!r}.'}, status=400,
            )

    try:
        table = extract(f.read(), f.name, sheet=sheet)
    except ExtractError as e:
        return Response(
            {'detail': f'File could not be parsed: {e}'}, status=400,
        )

    if not table.headers:
        return Response(
            {'detail': 'No tabular data found in this file.'}, status=400,
        )

    mapping, missing, ai_used = map_columns(
        table.headers, table.rows[:3], section,
        company_code=company_code,
    )

    # Manual mapping override (bug report Pako Kago 2026-07-29, request 2).
    # The UI's Column Mapping table lets the user pick the canonical field for
    # any column the matcher got wrong or left blank; those picks arrive as a
    # JSON object {raw_header: canonical_field} and WIN over the heuristic.
    # An empty value means "ignore this column".
    override_raw = request.data.get('mapping_override') or ''
    if override_raw:
        try:
            override = json.loads(override_raw) if isinstance(override_raw, str) else dict(override_raw)
        except (ValueError, TypeError):
            return Response(
                {'detail': 'mapping_override must be a JSON object of '
                           '{"column": "field"}.'}, status=400,
            )
        valid_fields = {f.name for f in section.fields}
        for raw_h, canon in override.items():
            if raw_h not in table.headers:
                continue
            if not canon:
                mapping.pop(raw_h, None)          # explicitly ignore the column
                continue
            if canon not in valid_fields:
                return Response(
                    {'detail': f'Unknown field {canon!r} for section '
                               f'{section.key!r}.'}, status=400,
                )
            # One canonical field per column — release whoever held it.
            for other_h in [h for h, c in mapping.items() if c == canon and h != raw_h]:
                mapping.pop(other_h)
            mapping[raw_h] = canon
        missing = [f.name for f in section.fields
                   if f.required and f.name not in set(mapping.values())]

    canonical_rows = []
    for r in table.rows:
        row_dict = {}
        for raw_h, canon in mapping.items():
            try:
                idx = table.headers.index(raw_h)
            except ValueError:
                continue
            row_dict[canon] = r[idx] if idx < len(r) else ''
        canonical_rows.append(row_dict)

    return Response({
        'section':         section.key,
        'section_label':   section.label,
        'company':         company_code,
        'source_hint':     table.source_hint,
        'truncated':       table.truncated,
        'headers':         table.headers,
        'mapping':         mapping,
        # Field catalogue for this section, so the UI can offer a manual
        # mapping dropdown per column without a second request.
        'fields': [
            {'name': f.name, 'label': f.label, 'required': f.required, 'kind': f.kind}
            for f in section.fields
        ],
        'missing_required': missing,
        'ai_used':         ai_used,
        'preview_rows':    canonical_rows[:25],     # for UI table
        'rows':            canonical_rows,          # full payload for /commit/
        'row_count':       len(canonical_rows),
    })


# ---------------------------------------------------------------------------
# POST /commit/
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser])
def smart_upload_commit(request):
    if not _user_can_smart_upload(request.user):
        return Response({'detail': 'You do not have the smart-upload permission.'}, status=403)
    body = request.data or {}
    section_key  = (body.get('section') or '').strip().lower()
    company_code = (body.get('company') or '').strip().upper()
    rows         = body.get('rows') or []
    # CFO directive 2026-05-19: `mode='replace'` lets a re-upload purge any
    # prior smart_upload JEs for the same (company, period) before writing
    # the new ones. Default 'create' preserves legacy behaviour.
    mode = (body.get('mode') or 'create').strip().lower()
    if mode not in ('create', 'replace'):
        return Response(
            {'detail': f'mode must be "create" or "replace" (got {mode!r}).'},
            status=400,
        )

    # CFO directive 2026-05-20: TB write-blocker for protected companies.
    # `OMNI_TEST_MODE=1` activates the guard. While active, any TB write
    # (mode=create OR mode=replace) targeting a company listed in
    # `OMNI_TEST_MODE_PROTECTED_COMPANIES` (default 'ADIC') is rejected
    # with HTTP 423 so a stray test commit can't trash production data
    # the team has already uploaded.
    #
    # CFO unsets `OMNI_TEST_MODE` (or removes the company from the list)
    # before legitimate uploads to that entity. The check fires at request
    # time, so toggling the env requires only a backend restart, not a
    # redeploy.
    import os as _os
    if (_os.environ.get('OMNI_TEST_MODE') or '').strip() == '1':
        protected = {
            c.strip().upper() for c in (
                _os.environ.get('OMNI_TEST_MODE_PROTECTED_COMPANIES') or 'ADIC'
            ).split(',') if c.strip()
        }
        if section_key == 'tb' and company_code in protected:
            log.warning(
                'OMNI_TEST_MODE=1 blocked TB commit to protected company %s by %s.',
                company_code, request.user.username,
            )
            return Response({
                'detail': (
                    f'OMNI_TEST_MODE is active and {company_code} is on the '
                    f'protected-companies list. Refusing TB write to prevent '
                    f'accidental destruction of production data. Unset '
                    f'OMNI_TEST_MODE (or remove {company_code} from '
                    f'OMNI_TEST_MODE_PROTECTED_COMPANIES) to proceed.'
                ),
                'test_mode_block': True,
                'protected_companies': sorted(protected),
                'section': section_key,
                'company': company_code,
            }, status=423)

    # External-audit follow-up 2026-05-19: honour Idempotency-Key header.
    # 60-second sliding window per (user, key). Returns cached response on
    # replay so a double-click can't post twice. Defends against the
    # browser-retry case before our external_ref-based de-dup kicks in.
    idem_key = (request.META.get('HTTP_IDEMPOTENCY_KEY') or '').strip()
    if idem_key:
        from django.core.cache import cache
        cache_key = f'smart_upload:idem:{request.user.id}:{idem_key}'
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

    if section_key not in SECTIONS:
        return Response({'detail': f'Unknown section {section_key!r}.'}, status=400)
    section = get_section(section_key)

    if not isinstance(rows, list):
        return Response({'detail': 'rows must be a list.'}, status=400)
    if len(rows) > 50000:
        return Response({'detail': 'Too many rows in one batch (max 50,000).'}, status=400)

    company = None
    if section.needs_company:
        company = Company.objects.filter(code=company_code).first()
        if not company:
            return Response(
                {'detail': f'Unknown company {company_code!r}.'}, status=400,
            )

    committer = COMMITTERS.get(section_key)
    if not committer:
        return Response(
            {'detail': f'No committer registered for {section_key!r}.'}, status=500,
        )

    log.info(
        'smart_upload commit: section=%s company=%s rows=%s mode=%s user=%s',
        section_key, company_code, len(rows), mode, request.user.username,
    )

    # CFO directive 2026-05-20: forward period_start / period_end /
    # period_label from the upload form so period-bound committers
    # (TB / GL / Payroll) can override the file's row-level values.
    period_kw = {
        k: (body.get(k) or '').strip()
        for k in ('period_start', 'period_end', 'period_label')
        if body.get(k)
    }
    # CFO directive 2026-05-20: explicit "Unlock & amend" confirmation
    # forwarded as force_unlock=true. Committer refuses to override a
    # locked period unless this flag is present.
    if body.get('force_unlock'):
        period_kw['force_unlock'] = 'true'

    # CFO directive 2026-05-20 (post-3-times-bug): block silent duplicate
    # TB uploads. If section=tb + mode=create + a smart_upload_tb JE
    # already exists for (company, entry_date), respond HTTP 409 with the
    # existing JE metadata so the UI can prompt "Replace or Cancel"
    # instead of producing a second posted JE on the same date.
    if section_key == 'tb' and mode == 'create' and company is not None:
        from core.smart_upload.committers import _date as _coerce_date
        last_end = None
        if 'period_end' in period_kw:
            last_end = _coerce_date(period_kw['period_end'])
        if not last_end:
            for r in rows:
                d = _coerce_date(r.get('period_end'))
                if d:
                    last_end = d
        if last_end is not None:
            from ledger.models import JournalEntry
            existing = (JournalEntry.objects
                        .filter(source_type='smart_upload_tb',
                                company=company, entry_date=last_end)
                        .order_by('-created_at')
                        .first())
            if existing:
                return Response({
                    'detail': (
                        f'A trial balance already exists for {company.code} '
                        f'{last_end.isoformat()} (JE {existing.entry_number}). '
                        f'Pass mode="replace" to overwrite, or use the '
                        f'Clear-TB button to remove the prior upload first.'
                    ),
                    'duplicate_exists': True,
                    'existing': {
                        'entry_number': existing.entry_number,
                        'lines': existing.lines.count(),
                        'created_at': existing.created_at.isoformat(),
                        'created_by': (existing.created_by.username
                                       if existing.created_by_id else None),
                    },
                    'company': company.code,
                    'period_end': last_end.isoformat(),
                    'section': section_key,
                }, status=status.HTTP_409_CONFLICT)

    # Bug fix 2026-06-03 (ADSA triple-load): GL uploads had NO duplicate guard
    # (only TB did) and commit_gl defaults to mode='create', which SKIPS the
    # replace-dedup block. A user re-uploaded the ADSA GL pack 3x within one
    # minute -> 1035 posted JEs (345 x 3). Mirror the TB guard: if
    # section=gl + mode=create and prior smart_upload_gl JEs already exist for
    # this company within the supplied rows' date span, refuse with HTTP 409 so
    # the UI prompts Replace/Cancel instead of silently stacking another copy.
    if section_key == 'gl' and mode == 'create' and company is not None:
        from core.smart_upload.committers import _date as _coerce_date
        from ledger.models import JournalEntry
        row_dates = [d for d in (_coerce_date(r.get('entry_date')) for r in rows) if d]
        if row_dates:
            d_min, d_max = min(row_dates), max(row_dates)
            existing_qs = (JournalEntry.objects
                           .filter(source_type='smart_upload_gl', company=company,
                                   entry_date__gte=d_min, entry_date__lte=d_max))
            n_existing = existing_qs.count()
            if n_existing:
                sample = existing_qs.order_by('-created_at').first()
                noun = 'entry' if n_existing == 1 else 'entries'
                return Response({
                    'detail': (
                        f'{n_existing} GL journal {noun} already exist for '
                        f'{company.code} between {d_min.isoformat()} and '
                        f'{d_max.isoformat()} (e.g. {sample.entry_number}). '
                        f'Re-uploading in create mode would ADD a duplicate '
                        f'copy. Pass mode="replace" to overwrite the existing '
                        f'GL for this date range, or cancel.'
                    ),
                    'duplicate_exists': True,
                    'existing': {
                        'count': n_existing,
                        'sample_entry_number': sample.entry_number,
                        'created_at': sample.created_at.isoformat(),
                        'created_by': (sample.created_by.username
                                       if sample.created_by_id else None),
                    },
                    'company': company.code,
                    'date_range': [d_min.isoformat(), d_max.isoformat()],
                    'section': section_key,
                }, status=status.HTTP_409_CONFLICT)

    try:
        report = committer(rows, company, request.user, mode=mode, **period_kw)
    except DjangoValidationError as e:
        # A balance / period / posting-rule violation is a DATA problem the
        # uploader can fix — return 400 with the exact message (e.g.
        # "Entry is not balanced in INR: debits X ≠ credits Y") instead of a
        # raw 500. The committer raised inside transaction.atomic(), so the
        # half-written draft JE has already been rolled back.
        msgs = e.messages if hasattr(e, 'messages') else [str(e)]
        detail = '; '.join(str(m) for m in msgs)
        log.warning('smart_upload commit rejected (validation): %s', detail)
        return Response(
            {'detail': f'{section_key} upload rejected: {detail}',
             'section': section_key, 'company': company_code, 'mode': mode,
             'errors': list(msgs)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:    # noqa: BLE001
        log.exception('smart_upload commit failed')
        return Response(
            {'detail': f'Commit failed: {e}'}, status=500,
        )

    out = report.as_dict()
    out['section'] = section_key
    out['company'] = company_code
    out['mode'] = mode

    # CFO directive 2026-05-20: smart_upload_commit returned HTTP 200 even
    # when zero JEs were created (errors[] was populated but team missed
    # them because the status code said success). Now: if nothing was
    # written AND we collected one or more errors, surface a 400 with a
    # `detail` summary so apiFetch's error path fires and the UI shows a
    # red banner instead of a green tick.
    has_errors = bool(report.errors)
    nothing_written = (
        (report.created or 0) == 0 and (report.updated or 0) == 0
    )
    if has_errors and nothing_written:
        err_summary = '; '.join(str(e) for e in (report.errors or [])[:3])
        if len(report.errors or []) > 3:
            err_summary += f' (+{len(report.errors) - 3} more)'
        out['detail'] = f'{section_key} upload failed: {err_summary}'

        # CFO directive 2026-05-20: never go silent. Ask DeepSeek to
        # translate the raw errors into operator-friendly guidance, with a
        # deterministic fallback if the API is unreachable. The helper
        # only sees the section key, raw errors, and CSV headers — no row
        # data crosses the boundary.
        try:
            from core.ai_assist import explain_upload_failure
            out['ai_explanation'] = explain_upload_failure(
                section=section_key,
                errors=report.errors,
                missing_required=None,
                sample_headers=[],
                expected_fields=[f.name for f in section.fields],
            )
        except Exception as exc:    # noqa: BLE001
            log.warning('explain_upload_failure raised: %s', exc)

        # CFO directive 2026-06-03: lift the friendly fields to the top of
        # the response so the SmartUpload component can render a single
        # "I couldn't load this <section>" modal rather than a raw error
        # toast. Frontend reads: friendly_title / friendly_message /
        # friendly_fix / missing_accounts. The raw `detail` + `errors` stay
        # for the audit/details expander.
        extra = report.extra or {}
        if extra.get('missing_accounts'):
            out['missing_accounts'] = extra['missing_accounts'][:50]
        if extra.get('missing_dr') or extra.get('missing_cr'):
            out['missing_dr'] = extra.get('missing_dr')
            out['missing_cr'] = extra.get('missing_cr')
        section_label = (section.label if section else section_key.upper()).rstrip('s')
        out['friendly_title'] = f"I couldn't load this {section_label}"
        ai = out.get('ai_explanation') or {}
        out['friendly_message'] = (ai.get('explanation') or out.get('detail') or '').strip()
        out['friendly_fix'] = (ai.get('suggested_fix') or '').strip()
        out['friendly_source'] = ai.get('source') or 'fallback'

        if idem_key:
            from django.core.cache import cache
            cache.set(
                f'smart_upload:idem:{request.user.id}:{idem_key}',
                out, timeout=60,
            )
        return Response(out, status=status.HTTP_400_BAD_REQUEST)

    if idem_key:
        from django.core.cache import cache
        cache.set(
            f'smart_upload:idem:{request.user.id}:{idem_key}',
            out, timeout=60,
        )
    return Response(out, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /tb/clear/
# ---------------------------------------------------------------------------
#
# CFO directive 2026-05-20 (post-3-times-bug): the team uploaded the same
# trial balance three times by accident before the 409-duplicate guard
# (above) shipped. They need a one-click "wipe my TB uploads" button so
# that fix-forward is a single action instead of a Django-shell incident.
#
# Scope of the wipe:
#   - source_type = 'smart_upload_tb' only. GL / Odoo / manual JEs untouched.
#   - optionally narrowed to a single period_end date.
#   - scoped to one company.
#
# Permissions: smart-upload (BULK_UPLOADER + CFO + admin all qualify).
# QuerySet.delete() bypasses JournalEntry.delete()'s POSTED guard, which
# is intentional — these JEs are posted and that is the whole reason
# we're wiping them.
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser])
def smart_upload_tb_clear(request):
    if not _user_can_smart_upload(request.user):
        return Response(
            {'detail': 'You do not have the smart-upload permission.'},
            status=403,
        )
    body = request.data or {}
    company_code = (body.get('company') or '').strip().upper()
    period_end = (body.get('period_end') or '').strip()
    date_from = (body.get('date_from') or '').strip()
    date_to = (body.get('date_to') or '').strip()

    if not company_code:
        return Response({'detail': 'company is required.'}, status=400)
    company = Company.objects.filter(code=company_code).first()
    if not company:
        return Response(
            {'detail': f'Unknown company {company_code!r}.'}, status=400,
        )

    # CFO directive 2026-05-27 (Pako/Kago): clearing ALL TB uploads at once
    # is disabled. The caller MUST scope the wipe — either a single
    # period_end OR a date range (date_from/date_to) on entry_date. This
    # prevents the "it cleared everything" incident.
    if not (period_end or date_from or date_to):
        return Response({
            'detail': 'A date range is required. Provide date_from and/or '
                      'date_to (YYYY-MM-DD) — clearing all trial balances at '
                      'once is disabled.',
        }, status=400)

    from ledger.models import JournalEntry
    from core.smart_upload.committers import _date as _coerce_date

    # Clear both smart-upload TB and GL rows for this company — both are the
    # user's own uploads via the TB / GL Upload screen and are the whole point
    # of the wipe-and-reupload button. Odoo-migrated (odoo_gl_*), manual and PO
    # JEs are deliberately NOT matched, so real history stays protected.
    # Fix 2026-06-04 (Legakwa/ADSA): ADSA's loaded data is source_type
    # 'smart_upload_gl', so the old TB-only filter matched 0 and the clear
    # silently did nothing ("Cleared 0 TB JEs").
    SMART_UPLOAD_SOURCES = ('smart_upload_tb', 'smart_upload_gl')
    qs = JournalEntry.objects.filter(
        source_type__in=SMART_UPLOAD_SOURCES, company=company,
    )
    if period_end:
        d = _coerce_date(period_end)
        if not d:
            return Response(
                {'detail': f'period_end must be YYYY-MM-DD (got {period_end!r}).'},
                status=400,
            )
        qs = qs.filter(entry_date=d)
    else:
        if date_from:
            df = _coerce_date(date_from)
            if not df:
                return Response(
                    {'detail': f'date_from must be YYYY-MM-DD (got {date_from!r}).'},
                    status=400,
                )
            qs = qs.filter(entry_date__gte=df)
        if date_to:
            dt = _coerce_date(date_to)
            if not dt:
                return Response(
                    {'detail': f'date_to must be YYYY-MM-DD (got {date_to!r}).'},
                    status=400,
                )
            qs = qs.filter(entry_date__lte=dt)
        # Guard inverted ranges.
        if date_from and date_to and _coerce_date(date_from) > _coerce_date(date_to):
            return Response(
                {'detail': 'date_from is after date_to.'}, status=400,
            )

    pre_count = qs.count()
    entry_numbers = list(qs.values_list('entry_number', flat=True))
    if pre_count == 0:
        # Be honest about WHY nothing matched: the company may hold data loaded
        # via Odoo migration or manual entry, which this tool deliberately does
        # not touch. Surface that so "0 cleared" is not misread as "no data".
        other = (JournalEntry.objects.filter(company=company)
                 .exclude(source_type__in=SMART_UPLOAD_SOURCES))
        if period_end:
            other = other.filter(entry_date=_coerce_date(period_end))
        else:
            if date_from:
                other = other.filter(entry_date__gte=_coerce_date(date_from))
            if date_to:
                other = other.filter(entry_date__lte=_coerce_date(date_to))
        other_n = other.count()
        if other_n:
            detail = (
                f'No smart-upload TB/GL rows to clear for that scope. Note: '
                f'{company.code} has {other_n} other journal '
                f'{"entry" if other_n == 1 else "entries"} in range from Odoo '
                f'migration / manual entry — these are protected and are NOT '
                f'cleared by this tool.'
            )
        else:
            detail = 'No smart-upload TB/GL rows to clear for that scope.'
        return Response({
            'deleted_count': 0,
            'deleted_lines': 0,
            'entry_numbers': [],
            'company': company.code,
            'period_end': period_end or None,
            'date_from': date_from or None,
            'date_to': date_to or None,
            'protected_count': other_n,
            'detail': detail,
        }, status=200)

    log.warning(
        'smart_upload_tb_clear: user=%s company=%s period_end=%s range=%s..%s deleting %d JEs',
        request.user.username, company.code, period_end or '(none)',
        date_from or '(none)', date_to or '(none)', pre_count,
    )
    deleted_total, breakdown = qs.delete()
    deleted_lines = breakdown.get('ledger.JournalEntryLine', 0)

    return Response({
        'deleted_count': pre_count,
        'deleted_lines': deleted_lines,
        'breakdown': breakdown,
        'entry_numbers': entry_numbers,
        'company': company.code,
        'period_end': period_end or None,
        'date_from': date_from or None,
        'date_to': date_to or None,
    }, status=200)
