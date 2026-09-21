"""
healthcare/upload_views.py — Smart-Upload endpoints for Revenue / Claims / Treaty.

CFO directive 2026-06-05 (Tlamelo Chimidza thread):
  POST /api/v1/health/upload/                  multipart: kind, [direction], file
  GET  /api/v1/health/uploads/?kind=revenue    list recent uploads (HR + Healthcare)

Generic xlsx parser. Detects header row by "≥5 non-empty cells", reads
all rows as dicts, computes headline totals per `kind`. Stores raw rows
inside HealthcareUpload.raw_payload so the UI can show a sample without
re-uploading.
"""
from __future__ import annotations

import io
import re
from decimal import Decimal
from datetime import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import HealthcareUpload


_MAX_BYTES = 25 * 1024 * 1024     # 25 MB hard cap per upload
_MAX_ROWS_STORED = 5000           # cap rows that go into raw_payload (display only)

# AFT medical-claims payment runs (ADI_AFT_PmtRun_YYYYMMDD.xlsx) carry a
# "Claim Lines" sheet and — being exported by AFA's tool — do NOT store sheet
# dimensions. The generic read_only _parse_xlsx below then sizes every sheet as
# 1x1, so its "largest sheet" heuristic never lands on Claim Lines and headline
# claims come back 0 (Tlamelo 2026-07-02: "zero claims for that week"). These
# files must go through the dedicated non-read_only parser + idempotent importer
# (healthcare.aft_import) instead. Detect by filename first, then by sheet name.
_AFT_NAME_RE = re.compile(r'AFT[_\s-]*PmtRun', re.IGNORECASE)


def _is_aft_run(blob: bytes, file_name: str) -> bool:
    """True if this is an AFT claims payment-run workbook."""
    if _AFT_NAME_RE.search(file_name or ''):
        return True
    try:
        import openpyxl
        # sheetnames don't depend on the (missing) dimension tag, so read_only
        # is safe + cheap here.
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True)
        return 'Claim Lines' in wb.sheetnames
    except Exception:
        return False


def _to_dec(v) -> Decimal:
    if v is None or v == '':
        return Decimal('0')
    try:
        return Decimal(str(v).replace(',', '').strip())
    except Exception:
        return Decimal('0')


def _parse_period_label(text: str) -> tuple[str, int | None, int | None]:
    """Best-effort parse for things like '202605', 'May 2026', '2026-05'."""
    if not text:
        return '', None, None
    s = str(text).strip()
    # 2026-05-01 / 2026/05  (ISO, year-first)
    m = re.search(r'(20\d{2})[-/](\d{1,2})', s)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
        if 1 <= mth <= 12:
            return f"{datetime(y, mth, 1):%B %Y}", y, mth
    # 31/10/2025 / 31-10-2025  (DD/MM/YYYY, year-LAST — Botswana convention).
    # Tlamelo 2026-06-11: the RI bordereaux carried "31/10/2025" and the parser
    # had no day/month/year branch, so every such upload fell into "Unknown
    # period". MIDDLE group is the month; if >12 it's US MM/DD/YYYY → use first.
    m = re.search(r'(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](20\d{2})(?!\d)', s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        mth = b if 1 <= b <= 12 else a
        if 1 <= mth <= 12:
            return f"{datetime(y, mth, 1):%B %Y}", y, mth
    # 10/2025 / 10-2025  (MM/YYYY, year-last)
    m = re.search(r'(?<!\d)(\d{1,2})[-/](20\d{2})(?!\d)', s)
    if m:
        mth, y = int(m.group(1)), int(m.group(2))
        if 1 <= mth <= 12:
            return f"{datetime(y, mth, 1):%B %Y}", y, mth
    # YYYYMM
    m = re.search(r'(20\d{2})(0[1-9]|1[0-2])', s)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
        return f"{datetime(y, mth, 1):%B %Y}", y, mth
    # "May 2026" (full month name, flexible separator)
    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s*[-,/ ]?\s*(20\d{2})', s, re.I)
    if m:
        try:
            d = datetime.strptime(f"{m.group(1)[:3]} 1 {m.group(2)}", '%b %d %Y')
            return f"{d:%B %Y}", d.year, d.month
        except Exception:
            pass
    # "Oct 2025" / "Oct-25" (abbreviated month)
    m = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-,/ ]\s*(20\d{2}|\d{2})\b', s, re.I)
    if m:
        try:
            yr = m.group(2)
            yr = ('20' + yr) if len(yr) == 2 else yr
            d = datetime.strptime(f"{m.group(1)[:3]} 1 {yr}", '%b %d %Y')
            return f"{d:%B %Y}", d.year, d.month
        except Exception:
            pass
    return '', None, None


def _parse_xlsx(blob: bytes, *, kind: str) -> dict:
    """Returns dict with: header_row_index, headers, rows (list of dicts),
    period_label / year / month, total_rows, total_lives_count, gross_amount,
    paid_amount, sheets (sheet names + dims). raw_payload-shaped."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
    sheet_summaries: list[dict] = []
    # Pick "best" data sheet — the one with the largest rows × cols product,
    # AFTER skipping sheets that are clearly config/lookup/instructions (Tlamelo
    # 2026-06-08: the treaty file had an "Inputs" sheet with more rows than the
    # actual "Premium Register" sheets, so the parser picked Inputs and totals
    # came out zero).
    _SKIP_SHEET_TOKENS = ('input', 'lookup', 'config', 'instruction', 'mapping', 'validation', 'readme')
    best_sn, best_score = '', 0
    summary_sn = ''
    for sn in wb.sheetnames:
        ws = wb[sn]
        score = (ws.max_row or 0) * (ws.max_column or 0)
        sheet_summaries.append({'name': sn, 'rows': ws.max_row, 'cols': ws.max_column})
        snl = sn.lower()
        if any(tok in snl for tok in _SKIP_SHEET_TOKENS):
            continue
        # Bug 410519b9 (Tlamelo 2026-06-12): treaty workbooks carry detail
        # register sheets PLUS a Summary tab with the broker's cession
        # totals — the size heuristic picked the big detail sheet. A sheet
        # named "summary" always wins; size remains the fallback.
        if 'summary' in snl and not summary_sn:
            summary_sn = sn
        if score > best_score:
            best_score, best_sn = score, sn
    if summary_sn:
        best_sn = summary_sn
    # Fallback to absolute largest if every sheet was blacklisted (shouldn't happen).
    if not best_sn and wb.sheetnames:
        best_sn = max(wb.sheetnames, key=lambda s: (wb[s].max_row or 0) * (wb[s].max_column or 0))

    out_rows: list[dict] = []
    headers: list[str] = []
    header_row_idx = 0
    period_text = ''

    if best_sn:
        ws = wb[best_sn]
        # Find header row — first row with ≥5 non-empty cells.
        rows_iter = list(ws.iter_rows(values_only=True))
        for i, r in enumerate(rows_iter, 1):
            non = [c for c in r if c not in (None, '')]
            if len(non) >= 5 and not headers:
                headers = [str(c).replace('\n', ' ').strip() if c is not None else f'col{n+1}'
                           for n, c in enumerate(r)]
                header_row_idx = i
                break
            # Look for "Bordereaux Month" / "Remit Date" style cells in early rows
            if not period_text:
                for cell in r:
                    if cell is None:
                        continue
                    sc = str(cell)
                    if any(k in sc.lower() for k in ('bordereaux month', 'remit date', 'period', 'month')):
                        # next row + next-cell pair may hold the value
                        period_text = sc

        # Read row dicts after the header row, capped at _MAX_ROWS_STORED
        if headers:
            for i, r in enumerate(rows_iter[header_row_idx:], 1):
                if i > _MAX_ROWS_STORED:
                    break
                non = [c for c in r if c not in (None, '')]
                if not non:
                    continue
                d = {}
                for col, val in zip(headers, r):
                    if val is None or val == '':
                        continue
                    if isinstance(val, datetime):
                        d[col] = val.isoformat()
                    elif isinstance(val, (int, float, Decimal)):
                        d[col] = float(val)
                    else:
                        d[col] = str(val)
                if d:
                    out_rows.append(d)

    # Resolve the period. First try whatever the header-keyword scan captured;
    # then (Tlamelo 2026-06-11) scan the first 8 rows of the data sheet and take
    # the first cell that actually PARSES to a month — instead of grabbing the
    # first string cell (which was often a title like "Reinsurance Bordereaux"
    # and never carried a date, so the upload landed in "Unknown period").
    # The call site additionally falls back to the FILENAME if this finds none.
    plabel, py, pm = _parse_period_label(period_text)
    if not py and best_sn:
        # Bug 2026-07-02: the 8-row scan grabbed a member DATE OF BIRTH (e.g.
        # Feb 1983) and mis-dated the whole GWP bordereaux. Bound the accepted
        # year to plausible bordereaux periods so DOBs can't hijack the period;
        # the FILENAME (call site) is the authoritative source for these files.
        _max_year = timezone.now().year + 1
        for r in ws.iter_rows(min_row=1, max_row=8, values_only=True):
            for c in r:
                if c is None:
                    continue
                if isinstance(c, datetime):
                    if 2020 <= c.year <= _max_year:
                        plabel, py, pm = f"{c:%B %Y}", c.year, c.month
                        break
                    continue
                if isinstance(c, str):
                    l2, y2, m2 = _parse_period_label(c)
                    if y2 and 2020 <= y2 <= _max_year:
                        plabel, py, pm = l2, y2, m2
                        break
            if py:
                break

    # Headline totals — per-kind, priority-ordered substring match (Tlamelo
    # 2026-06-08). The previous exact-match list missed real-world column names:
    # the GWP MASTER files use 'TotalPremium Incl. VAT' / 'TotalPremium', not the
    # bare 'premium inc. vat' the parser was checking for. Priority-ordered so a
    # row matching multiple candidates picks the CANONICAL one (e.g. 'TotalPremium
    # Incl. VAT' beats bare 'premium' on the same row — no double-count).
    gross = Decimal('0')
    paid  = Decimal('0')

    # Tokens are matched as case-insensitive substrings against the header name.
    # First match per row wins (per side: gross + paid).
    _GROSS_TOKENS_BY_KIND = {
        'revenue': ['totalpremium incl. vat', 'totalpremium incl vat',
                    'premium incl. vat', 'premium incl vat',
                    'totalpremium', 'gross premium',
                    'premium excl. vat', 'premium excl vat',
                    'premium'],
        'claims':  ['charged amount', 'claimed amount', 'total amount'],
        # Treaty/RI GROSS = the PREMIUM column only. Claims live in the PAID
        # side below — do NOT lump claimed/charged here (Tlamelo 2026-06-11:
        # that left RI claims at 0 and inflated premium). Reinsurance-premium
        # column names first, generic premium last.
        'treaty':  ['100% reinsurance premium', 'reinsurance premium',
                    'premium inclusive of ri', 'ri premium',
                    'totalpremium incl. vat', 'totalpremium incl vat',
                    'premium incl. vat', 'premium incl vat',
                    'totalpremium', 'gross premium',
                    'premium excl. vat', 'premium excl vat',
                    'premium'],
    }
    _PAID_TOKENS_BY_KIND = {
        'revenue': [],  # GWP has no per-row paid concept
        'claims':  ['paid amount', 'payable amount'],
        # Treaty/RI "paid" = the CLAIMS-recovered-from-reinsurer column, which
        # the dashboard's Reinsurance tab renders as Claims (and 90% Claims /
        # RI Net). The RI bordereaux names it "Reinsurance Claims (BWP)" /
        # "100% Reinsurance Claims" — none of which matched the old
        # paid/payable tokens, so claims showed 0.00 (Tlamelo 2026-06-11).
        # Specific reinsurance-claim names first; bare 'claims' is the
        # last-resort fallback.
        'treaty':  ['100% reinsurance claims', 'reinsurance claims',
                    'ri claims', 'reinsurance claim',
                    'claims incl. vat', 'claims incl vat',
                    'claims (bwp)', 'claims bwp',
                    'claimed amount', 'charged amount',
                    'paid amount', 'payable amount', 'claims'],
    }
    gross_tokens = _GROSS_TOKENS_BY_KIND.get(kind, [])
    paid_tokens  = _PAID_TOKENS_BY_KIND.get(kind, [])

    def _pick_first(row_lc, tokens):
        # Return the value of the first row-column whose lowercased header
        # CONTAINS any of the tokens (in priority order). row_lc is dict of
        # lower-cased header -> raw value.
        for tok in tokens:
            for hdr, val in row_lc.items():
                if tok in hdr:
                    return val
        return None

    # Lives count — per-kind:
    #   revenue: count rows that have a BeneficiaryFullName / beneficiary name
    #   claims/treaty: count UNIQUE member identifier (Member Number / IDNumber)
    lives = 0
    seen_members: set[str] = set()

    for r in out_rows:
        row_lc = {(k or '').lower().strip(): v for k, v in r.items()}
        gv = _pick_first(row_lc, gross_tokens)
        pv = _pick_first(row_lc, paid_tokens)
        if gv is not None: gross += _to_dec(gv)
        if pv is not None: paid  += _to_dec(pv)
        if kind == 'revenue':
            if any(t in row_lc for t in ('beneficiaryfullname', 'beneficiary name', 'beneficiary fullname')):
                lives += 1
        else:
            for t in ('member number', 'membernumber', 'memberid', 'member id', 'idnumber', 'id number'):
                if t in row_lc and row_lc[t]:
                    seen_members.add(str(row_lc[t]).strip())
                    break
    if kind != 'revenue':
        lives = len(seen_members)

    return {
        'sheets':            sheet_summaries,
        'header_row_index':  header_row_idx,
        'headers':           headers,
        'row_count':         len(out_rows),
        'rows':              out_rows,
        'period_label':      plabel,
        'period_year':       py,
        'period_month':      pm,
        'gross_amount':      str(gross),
        'paid_amount':       str(paid),
        'lives_count':       lives,
    }


class HealthcareUploadView(APIView):
    """POST a multipart xlsx, parse it, persist a HealthcareUpload row."""
    # BUG (Tlamelo, 2026-06-05): hard-coding [Session, Token] here dropped
    # core.azure_auth.AzureJWTAuthentication, so SSO users (MSAL Bearer) were
    # rejected with 401 "Authentication credentials were not provided" on every
    # upload. Inherit the project default (settings.REST_FRAMEWORK
    # DEFAULT_AUTHENTICATION_CLASSES = AzureJWT, ApiKey, Session, Token) so the
    # same Bearer that works everywhere else in omni works here too.
    permission_classes     = [IsAuthenticated]
    parser_classes         = [MultiPartParser, FormParser]

    def post(self, request):
        kind = (request.data.get('kind') or '').strip().lower()
        direction = (request.data.get('direction') or 'na').strip().lower()
        f = request.FILES.get('file')

        if kind not in {'revenue', 'claims', 'treaty'}:
            return Response({'detail': "kind must be one of revenue|claims|treaty."},
                            status=status.HTTP_400_BAD_REQUEST)
        if direction not in {'na', 'inbound', 'outbound'}:
            return Response({'detail': "direction must be one of na|inbound|outbound."},
                            status=status.HTTP_400_BAD_REQUEST)
        if kind == 'treaty' and direction == 'na':
            return Response({'detail': "Treaty uploads must declare direction=inbound|outbound."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not f:
            return Response({'detail': 'file is required (multipart/form-data, field name "file").'},
                            status=status.HTTP_400_BAD_REQUEST)
        if f.size > _MAX_BYTES:
            return Response({'detail': f'File too large ({f.size} bytes, max {_MAX_BYTES}).'},
                            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        blob = f.read()

        # AFT medical-claims payment runs must use the dedicated non-read_only
        # parser + idempotent importer — the generic read_only _parse_xlsx below
        # misreads them as zero claims (see _is_aft_run note above).
        if kind == 'claims' and _is_aft_run(blob, f.name):
            from .aft_import import import_aft_run
            try:
                rep = import_aft_run(
                    blob, f.name,
                    uploaded_by=request.user if request.user.is_authenticated else None,
                )
            except Exception as e:  # noqa: BLE001
                row = HealthcareUpload.objects.create(
                    kind=kind, direction=direction,
                    file_name=f.name, file_size=f.size,
                    uploaded_by=request.user if request.user.is_authenticated else None,
                    status=HealthcareUpload.Status.FAILED,
                    error_log=f'{type(e).__name__}: {e}',
                    raw_payload={},
                )
                return Response({
                    'id': str(row.id), 'status': 'failed',
                    'detail': 'Could not parse AFT claims run — see error_log.',
                    'error': f'{type(e).__name__}: {e}',
                }, status=status.HTTP_400_BAD_REQUEST)
            upload_id = rep.get('upload_id')
            uprow = HealthcareUpload.objects.filter(id=upload_id).first() if upload_id else None
            return Response({
                'id':           upload_id,
                'status':       rep.get('status', 'imported'),
                'kind':         'claims',
                'direction':    direction,
                'period_label': uprow.period_label if uprow else '',
                'total_rows':   rep.get('line_count', uprow.total_rows if uprow else 0),
                'lives_count':  uprow.total_lives_count if uprow else 0,
                'gross_amount': rep.get('total_charged', str(uprow.gross_amount) if uprow else '0'),
                'paid_amount':  rep.get('total_paid', str(uprow.paid_amount) if uprow else '0'),
                'remit_date':   rep.get('remit_date'),
                'message':      f"AFT claims run parsed ({rep.get('status')}).",
            }, status=status.HTTP_201_CREATED)

        try:
            parsed = _parse_xlsx(blob, kind=kind)
            # Period fallback (Tlamelo 2026-06-10): none of the real files carry
            # a parsable period on the cover sheet, so every upload landed with
            # period NULL and the monthly summary collapsed into one "Unknown
            # period" bucket. The FILENAME usually does carry it
            # (AFT_PmtRun_20260530.xlsx, "... May 2026.xlsx") — try that next.
            # The filename is the authoritative period for the monthly bordereaux
            # ("HEALTH GWP March 2026.xlsx"). Prefer it over any in-sheet date —
            # the 8-row scan can otherwise pick a member DOB and mis-date the file
            # (bug 2026-07-02: April/June landed as "February 1983"). Only fall
            # back to the in-sheet value when the filename carries no period.
            plabel, py, pm = _parse_period_label(f.name)
            if py and pm:
                parsed['period_label'] = plabel
                parsed['period_year'] = py
                parsed['period_month'] = pm
        except Exception as e:  # noqa: BLE001
            row = HealthcareUpload.objects.create(
                kind=kind, direction=direction,
                file_name=f.name, file_size=f.size,
                uploaded_by=request.user if request.user.is_authenticated else None,
                status=HealthcareUpload.Status.FAILED,
                error_log=f'{type(e).__name__}: {e}',
                raw_payload={},
            )
            return Response({
                'id':         str(row.id),
                'status':     'failed',
                'detail':     'Could not parse xlsx — see error_log.',
                'error':      f'{type(e).__name__}: {e}',
            }, status=status.HTTP_400_BAD_REQUEST)

        row = HealthcareUpload.objects.create(
            kind=kind, direction=direction,
            file_name=f.name, file_size=f.size,
            uploaded_by=request.user if request.user.is_authenticated else None,
            period_label=parsed.get('period_label', '') or '',
            period_year =parsed.get('period_year'),
            period_month=parsed.get('period_month'),
            total_rows         = parsed.get('row_count', 0),
            total_lives_count  = parsed.get('lives_count', 0),
            gross_amount       = Decimal(parsed.get('gross_amount', '0')),
            paid_amount        = Decimal(parsed.get('paid_amount', '0')),
            raw_payload        = parsed,
            status             = HealthcareUpload.Status.PARSED,
        )
        return Response({
            'id':                str(row.id),
            'status':            'parsed',
            'kind':              row.kind,
            'direction':         row.direction,
            'period_label':      row.period_label,
            'total_rows':        row.total_rows,
            'lives_count':       row.total_lives_count,
            'gross_amount':      str(row.gross_amount),
            'paid_amount':       str(row.paid_amount),
            'headers':           parsed.get('headers', [])[:80],
            'sheets':            parsed.get('sheets', []),
            'uploaded_at':       row.uploaded_at.isoformat(),
            'message':           'File parsed and persisted.',
        }, status=status.HTTP_201_CREATED)


class HealthcareUploadListView(APIView):
    """GET ?kind=revenue → recent uploads of one kind, newest first."""
    # BUG (Tlamelo, 2026-06-05): hard-coding [Session, Token] here dropped
    # core.azure_auth.AzureJWTAuthentication, so SSO users (MSAL Bearer) were
    # rejected with 401 "Authentication credentials were not provided" on every
    # upload. Inherit the project default (settings.REST_FRAMEWORK
    # DEFAULT_AUTHENTICATION_CLASSES = AzureJWT, ApiKey, Session, Token) so the
    # same Bearer that works everywhere else in omni works here too.
    permission_classes     = [IsAuthenticated]

    def get(self, request):
        kind = (request.query_params.get('kind') or '').strip().lower()
        qs = HealthcareUpload.objects.all()
        # PII guard (audit 2026-06-11): uploads carry member-level health data.
        # Staff see the whole register; everyone else only their own uploads.
        if not request.user.is_staff:
            qs = qs.filter(uploaded_by=request.user)
        if kind in {'revenue', 'claims', 'treaty'}:
            qs = qs.filter(kind=kind)
        qs = qs.order_by('-uploaded_at')[:50]
        return Response({
            'count':   qs.count() if hasattr(qs, 'count') else len(list(qs)),
            'results': [{
                'id':           str(u.id),
                'kind':         u.kind,
                'direction':    u.direction,
                'file_name':    u.file_name,
                'file_size':    u.file_size,
                'period_label': u.period_label,
                'period_year':  u.period_year,
                'period_month': u.period_month,
                'total_rows':   u.total_rows,
                'lives_count':  u.total_lives_count,
                'gross_amount': str(u.gross_amount),
                'paid_amount':  str(u.paid_amount),
                'status':       u.status,
                'error_log':    u.error_log or '',
                'uploaded_by':  (u.uploaded_by.get_full_name() or u.uploaded_by.username) if u.uploaded_by else '',
                'uploaded_at':  u.uploaded_at.isoformat(),
            } for u in qs],
        })


class HealthcareUploadDetailView(APIView):
    """GET /api/v1/health/uploads/<id>/ — full raw_payload (capped sample)."""
    # BUG (Tlamelo, 2026-06-05): hard-coding [Session, Token] here dropped
    # core.azure_auth.AzureJWTAuthentication, so SSO users (MSAL Bearer) were
    # rejected with 401 "Authentication credentials were not provided" on every
    # upload. Inherit the project default (settings.REST_FRAMEWORK
    # DEFAULT_AUTHENTICATION_CLASSES = AzureJWT, ApiKey, Session, Token) so the
    # same Bearer that works everywhere else in omni works here too.
    permission_classes     = [IsAuthenticated]

    def get(self, request, pk):
        try:
            u = HealthcareUpload.objects.get(pk=pk)
        except HealthcareUpload.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        # PII guard (audit 2026-06-11): raw_payload holds member-level rows
        # (health data, DPA-restricted). Only the uploader or staff may read
        # the detail — mirrors the owner-or-staff rule on delete() below.
        is_owner = u.uploaded_by_id is not None and u.uploaded_by_id == request.user.id
        if not (request.user.is_staff or is_owner):
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            'id':           str(u.id),
            'kind':         u.kind,
            'direction':    u.direction,
            'file_name':    u.file_name,
            'period_label': u.period_label,
            'total_rows':   u.total_rows,
            'lives_count':  u.total_lives_count,
            'gross_amount': str(u.gross_amount),
            'paid_amount':  str(u.paid_amount),
            'sheets':       (u.raw_payload or {}).get('sheets', []),
            'headers':      (u.raw_payload or {}).get('headers', []),
            'rows_sample':  (u.raw_payload or {}).get('rows', [])[:25],
            'rows_total':   (u.raw_payload or {}).get('row_count', 0),
            'status':       u.status,
            'error_log':    u.error_log,
            'uploaded_at':  u.uploaded_at.isoformat(),
        })

    def delete(self, request, pk):
        # Tlamelo 2026-06-10: duplicate uploads of the same file double-count
        # revenue / claims / treaty. Allow the uploader (or an admin) to remove
        # an upload; totals live on the row itself so a delete is clean — no
        # orphaned per-row table to cascade.
        try:
            u = HealthcareUpload.objects.get(pk=pk)
        except HealthcareUpload.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        is_owner = u.uploaded_by_id is not None and u.uploaded_by_id == request.user.id
        if not (request.user.is_staff or is_owner):
            return Response({'detail': 'Only the uploader or an admin can delete an upload.'},
                            status=status.HTTP_403_FORBIDDEN)
        info = {'id': str(u.id), 'kind': u.kind, 'file_name': u.file_name,
                'period_label': u.period_label}
        u.delete()
        return Response({'deleted': True, **info})


class HealthcareSummaryView(APIView):
    """GET /api/v1/health/summary/ — month-by-month aggregates for every kind.

    Tlamelo 2026-06-10: each tab needs a per-month + YTD summary (per financial
    year) built from the uploads. One call returns all three kinds so the
    Claims tab can compute loss ratios against Revenue months client-side.

    Grouping is (kind, period_year, period_month) over PARSED uploads only.
    `uploads` > 1 in a month means duplicates are still present — the UI badges
    that so the register can be cleaned to one file per month per tab.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count, Sum

        qs = (HealthcareUpload.objects
              .filter(status=HealthcareUpload.Status.PARSED)
              .values('kind', 'period_year', 'period_month')
              .annotate(uploads=Count('id'),
                        lives=Sum('total_lives_count'),
                        rows=Sum('total_rows'),
                        gross=Sum('gross_amount'),
                        paid=Sum('paid_amount'))
              .order_by('period_year', 'period_month'))

        kinds: dict[str, list] = {'revenue': [], 'claims': [], 'treaty': []}
        for r in qs:
            y, m = r['period_year'], r['period_month']
            label = f"{datetime(y, m, 1):%B %Y}" if (y and m) else 'Unknown period'
            kinds.setdefault(r['kind'], []).append({
                'period_year':  y,
                'period_month': m,
                'label':        label,
                'uploads':      r['uploads'],
                'rows':         r['rows'] or 0,
                'lives':        r['lives'] or 0,
                'gross':        str(r['gross'] or Decimal('0')),
                'paid':         str(r['paid'] or Decimal('0')),
            })
        return Response({'kinds': kinds})
