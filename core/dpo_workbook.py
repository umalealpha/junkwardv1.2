"""
core/dpo_workbook.py — parse + serve the DPO's compliance workbook.

Oratile (DPO) maintains a DPA-2024 compliance workbook offline (ROPA, systems
inventory, security controls, policies, a compliance/gap tracker, incidents and
training registers). She uploads the .xlsx here; we parse every sheet generically
to JSON, compute a gap summary, and the Data Protection dashboard renders it.
Reuse-not-rebuild (CFO 2026-07-24): no per-sheet models — one snapshot per upload.

PII: the Training Attendance + Quiz Register sheets carry staff names/emails/scores.
Those rows are kept server-side but STRIPPED from the API response (only their
aggregate — completed %, avg score, outstanding — is returned). AD-POL-AI-GOV-001:
never send this file to an external model.
"""
from __future__ import annotations

import io
from statistics import mean

from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from core.dpa_dashboard import can_view_dpa_dashboard, is_dpo
from core.models import DpoWorkbook

# Rows are returned to the client ONLY for these known-safe sheets. Anything
# else — a PII sheet, a renamed/spaced sheet, or a NEW unknown sheet — has its
# rows WITHHELD by default (default-deny). Staff detail never reaches the client;
# only the aggregates in `summary` do. (Fable C5-b, 2026-07-24.)
_SAFE_SHEETS = {'validated ropa', 'compliance tracker', 'alpha systems inventory',
                'incidents', 'policies & supporting docs', 'security controls',
                'systems by department', 'instructions'}
_MAX_ROWS = 500


def _norm(name: str) -> str:
    return ' '.join((name or '').lower().split())


def _is_pii_sheet(name: str) -> bool:
    n = _norm(name)
    return any(w in n for w in ('training', 'quiz', 'attendance'))


def _s(v) -> str:
    if v is None:
        return ''
    if hasattr(v, 'isoformat'):
        try:
            return v.isoformat()[:10]
        except Exception:  # noqa: BLE001
            return str(v)
    return str(v).strip()


def _header_row_idx(rows: list[list[str]]) -> int:
    """The header is the row with the most non-empty cells among the first 3
    (some sheets carry a one-cell title row above the real header)."""
    best_i, best_n = 0, -1
    for i, r in enumerate(rows[:3]):
        n = sum(1 for c in r if c)
        if n > best_n:
            best_i, best_n = i, n
    return best_i


def parse_workbook(django_file) -> tuple[dict, dict]:
    """Parse every sheet → {sheet: {headers, rows}} + a gap summary dict.
    Tolerant: unknown sheets still parse; missing columns are skipped."""
    import openpyxl
    data = django_file.read()
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    sheets: dict = {}
    for ws in wb.worksheets:
        raw = []
        for r in ws.iter_rows(values_only=True):
            cells = [_s(c) for c in r]
            if any(cells):
                raw.append(cells)
            if len(raw) > _MAX_ROWS + 5:
                break
        if not raw:
            continue
        # Prose/instruction sheet → keep as notes.
        if max((sum(1 for c in r if c) for r in raw[:3]), default=0) < 2:
            sheets[ws.title] = {'type': 'notes', 'lines': [r[0] for r in raw if r and r[0]][:40]}
            continue
        hi = _header_row_idx(raw)
        # Trim TRAILING blank header cells only — never drop an interior blank,
        # or every column after it shifts left and the gap counts read the wrong
        # column (Fable, 2026-07-24). Interior blanks get a positional name.
        hrow = list(raw[hi])
        while hrow and not hrow[-1]:
            hrow.pop()
        headers = [h or f'col{i + 1}' for i, h in enumerate(hrow)]
        rows = []
        for r in raw[hi + 1:hi + 1 + _MAX_ROWS]:
            if not any(r):
                continue
            rows.append({headers[i]: (r[i] if i < len(r) else '') for i in range(len(headers))})
        sheets[ws.title] = {'type': 'table', 'headers': headers, 'rows': rows}
    summary = _summarise(sheets)
    return sheets, summary


# ---- gap analysis (tolerant column matching) --------------------------------
def _rows(sheets, name):
    s = sheets.get(name) or {}
    return s.get('rows', []) if s.get('type') == 'table' else []


def _get(row: dict, *needles) -> str:
    for k, v in row.items():
        kl = k.lower()
        if all(n in kl for n in needles):
            return (v or '').strip()
    return ''


def _is_yes(v: str) -> bool:
    v = v.strip().lower()
    return v in {'y', 'yes', 'true', '1'} or v.startswith('yes')


def _summarise(sheets: dict) -> dict:
    out: dict = {}
    gaps: list[str] = []

    ropa = _rows(sheets, 'Validated ROPA')
    if ropa:
        val = lambda r: _get(r, 'validation')  # noqa: E731
        red = sum(1 for r in ropa if '🔴' in val(r))
        amber = sum(1 for r in ropa if '🟠' in val(r))
        done = sum(1 for r in ropa if '✅' in val(r))
        out['ropa'] = {'total': len(ropa), 'validated': done, 'amber': amber, 'red': red}
        if amber + red:
            gaps.append(f'{amber + red} ROPA activit{"y" if amber + red == 1 else "ies"} not fully validated')

    sysrows = _rows(sheets, 'Alpha Systems Inventory')
    if sysrows:
        outside = sum(1 for r in sysrows if _is_yes(_get(r, 'stored', 'outside')) or _is_yes(_get(r, 'processed', 'outside')))
        no_dpa = sum(1 for r in sysrows if _get(r, 'dpa', 'place') and not _is_yes(_get(r, 'dpa', 'place')))
        out['systems'] = {'total': len(sysrows), 'cross_border': outside, 'no_dpa': no_dpa}
        if no_dpa:
            gaps.append(f'{no_dpa} system{"s" if no_dpa != 1 else ""} without a data-processing agreement')

    ctrl = _rows(sheets, 'Security Controls')
    if ctrl:
        not_ok = sum(1 for r in ctrl if _get(r, 'status') and _get(r, 'status').lower() not in {'ok', 'implemented', 'in place', 'complete', 'compliant', '✅'})
        out['controls'] = {'total': len(ctrl), 'not_ok': not_ok}

    pol = _rows(sheets, 'Policies & Supporting Docs')
    if pol:
        attn = sum(1 for r in pol if _get(r, 'status') and _get(r, 'status').lower() not in {'approved', 'active', 'complete', 'published', 'signed', 'in place'})
        out['policies'] = {'total': len(pol), 'needs_attention': attn}
        if attn:
            gaps.append(f'{attn} polic{"y" if attn == 1 else "ies"} not yet approved/current')

    comp = _rows(sheets, 'Compliance Tracker')
    if comp:
        opengaps = sum(1 for r in comp if _get(r, 'next', 'gap') or (_get(r, 'status') and _get(r, 'status').lower() not in {'complete', 'compliant', 'met', 'done', '✅'}))
        out['compliance'] = {'total': len(comp), 'gaps': opengaps}
        if opengaps:
            gaps.append(f'{opengaps} DPA compliance gap{"s" if opengaps != 1 else ""} open on the tracker')

    inc = _rows(sheets, 'Incidents')
    if inc:
        openi = sum(1 for r in inc if _get(r, 'status') and _get(r, 'status').lower() not in {'closed', 'resolved', 'complete'})
        out['incidents'] = {'total': len(inc), 'open': openi}
        if openi:
            gaps.append(f'{openi} incident{"s" if openi != 1 else ""} still open')

    quiz = _rows(sheets, 'Quiz Register')
    if quiz:
        done = sum(1 for r in quiz if _is_yes(_get(r, 'completed')))
        scores = []
        for r in quiz:
            v = _get(r, 'score').replace('%', '')
            try:
                scores.append(float(v))
            except (ValueError, TypeError):
                pass
        out['training'] = {'total': len(quiz), 'completed': done,
                           'outstanding': len(quiz) - done,
                           'avg_score': round(mean(scores), 1) if scores else None}
        if len(quiz) - done:
            gaps.append(f'{len(quiz) - done} staff have not completed data-protection training')

    out['gaps'] = gaps
    return out


def _public_sheets(sheets: dict) -> dict:
    """Return rows ONLY for known-safe, non-PII sheets. A PII sheet OR any
    unknown/renamed sheet has its rows (or notes lines) withheld — default-deny.
    Staff detail never reaches the client; only aggregates in `summary` do."""
    safe = {}
    for name, s in sheets.items():
        known = _norm(name) in _SAFE_SHEETS
        if s.get('type') == 'table' and (_is_pii_sheet(name) or not known):
            safe[name] = {'type': 'table', 'headers': s.get('headers', []),
                          'rows': [], 'pii_withheld': len(s.get('rows', []))}
        elif s.get('type') == 'notes' and not known:
            safe[name] = {'type': 'notes', 'lines': [], 'withheld': len(s.get('lines', []))}
        else:
            safe[name] = s
    return safe


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dpo_workbook(request):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted to the DPO, C-suite, HR and Finance.'}, status=403)
    wb = DpoWorkbook.objects.first()
    if wb is None:
        return Response({'has_workbook': False})
    return Response({
        'has_workbook': True,
        'file_name': wb.file_name,
        'uploaded_at': wb.created_at,
        'uploaded_by': getattr(wb.uploaded_by, 'username', None),
        'summary': wb.summary,
        'sheets': _public_sheets(wb.sheets),
        'can_upload': is_dpo(request.user) or getattr(request.user, 'is_superuser', False),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def dpo_workbook_upload(request):
    if not (is_dpo(request.user) or getattr(request.user, 'is_superuser', False)):
        return Response({'detail': 'Only the DPO can upload the compliance workbook.'}, status=403)
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'No file uploaded.'}, status=400)
    if not f.name.lower().endswith(('.xlsx', '.xlsm')):
        return Response({'detail': 'Please upload the .xlsx workbook.'}, status=400)
    if getattr(f, 'size', 0) and f.size > 15 * 1024 * 1024:
        return Response({'detail': 'Workbook too large (max 15 MB).'}, status=400)
    try:
        f.seek(0)
        sheets, summary = parse_workbook(f)
    except Exception as exc:  # noqa: BLE001
        return Response({'detail': f'Could not read the workbook: {exc}'}, status=400)
    f.seek(0)
    wb = DpoWorkbook.objects.create(
        file=f, file_name=f.name[:200], uploaded_by=request.user,
        sheets=sheets, summary=summary,
    )
    # DPA data-minimisation: keep only the newest 3 snapshots — each holds the
    # staff-PII training rows + the raw file. Purge older ones (row + media).
    for old in list(DpoWorkbook.objects.all()[3:]):
        try:
            if old.file:
                old.file.delete(save=False)
        except Exception:  # noqa: BLE001
            pass
        old.delete()
    return Response({'ok': True, 'file_name': wb.file_name, 'summary': summary}, status=201)
