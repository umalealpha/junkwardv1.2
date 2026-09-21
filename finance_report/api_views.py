"""
finance_report/api_views.py — the monthly Premium / Claims / Loss Ratio report.

Upload the three source workbooks, get the three tabs back. Nothing is stored
and nothing is posted: this is a report, not a ledger entry. It reads the files
in memory, computes, and returns — so it cannot drift out of agreement with the
source the way a saved copy would, and it can never touch the GL.

Restricted to the finance/management group that already gates the other
financial reports.
"""
from __future__ import annotations

import io
import logging
import re

from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from core.permissions import CanViewFinancials

from .engine import HEALTH_INSURANCE, UNION_LEGAL, build_report
from .omni_sources import health_rows, union_legal_rows
from .parsers import (
    SourceColumnMissing, manual_premium_rows, parse_claims_as_on_date,
    parse_month_on_month, parse_premium_board,
)

# A Premium Board runs to ~90,000 rows and a Month-on-Month to ~430,000, so the
# ceiling is generous — but not unbounded, because an unbounded upload is how a
# report endpoint becomes a way to exhaust the server's memory.
logger = logging.getLogger(__name__)

MAX_ROWS = 750_000

# 'YYYY-MM' and nothing else. '2026-7' or 'Jul-2026' matches no row, so every
# table renders at zero, the reconciliation says "balanced", and the report
# looks finished. A typo in a period box must not be able to do that.
MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _months_of_rows(rows):
    return sorted({r.month for r in rows})


def _read(upload):
    """Read one uploaded workbook. calamine is the fast path for every format;
    if it cannot open a particular file, fall back to the pure-Python readers so
    an odd export still parses rather than failing outright."""
    raw = upload.read()

    from python_calamine import CalamineWorkbook
    try:
        wb = CalamineWorkbook.from_filelike(io.BytesIO(raw))
        sheet = wb.get_sheet_by_index(0)
        data = sheet.to_python(skip_empty_area=False)
        if not data:
            return [], [], False
        header = list(data[0])
        rows = [list(r) for r in data[1:MAX_ROWS + 1]]
        return header, rows, (len(data) - 1) > MAX_ROWS
    except Exception as e:
        # A file calamine chokes on: hand it to the format-specific reader. The
        # fallback returns the same correct data, but it is the slow pure-Python
        # path — so a silent fall-back is the original 67s bug reincarnated with
        # no signal. Log it (loudly) so a file that always takes the slow path is
        # visible, then fall back.
        name = (getattr(upload, 'name', '') or '').lower()
        logger.warning('calamine could not read %r (%s) — falling back to the '
                       'pure-Python reader (slower).', name or '?', e, exc_info=True)
        return _fallback_xlsb(raw) if name.endswith('.xlsb') else _fallback_xlsx(raw)


def _fallback_xlsx(raw):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        header = list(next(it, []) or [])
        rows, truncated = [], False
        for i, r in enumerate(it):
            if i >= MAX_ROWS:
                truncated = True
                break
            rows.append(list(r))
        return header, rows, truncated
    finally:
        wb.close()


def _fallback_xlsb(raw):
    from pyxlsb import open_workbook
    rows, truncated = [], False
    with open_workbook(io.BytesIO(raw)) as wb:
        with wb.get_sheet(wb.sheets[0]) as sh:
            it = sh.rows()
            first = next(it, None)
            if first is None:
                return [], [], False
            header = [c.v for c in first]
            for i, r in enumerate(it):
                if i >= MAX_ROWS:
                    truncated = True
                    break
                rows.append([c.v for c in r])
    return header, rows, truncated


@api_view(['POST'])
@permission_classes([CanViewFinancials])
@parser_classes([MultiPartParser])
def build_finance_report(request):
    """POST the source workbooks; get the finished report back.

    Files (all optional, so a part of the report can be checked on its own):
      premium_board       .xlsx  Corporate + Personal premium
      month_on_month      .xlsx  Instant Insurance + Motor Comprehensive premium
      claims_as_on_date   .xlsb  every claim reserve

    Fields:
      months        comma-separated 'YYYY-MM' — the reporting period. Left out,
                    the period is taken from the premium rows.
      union_legal   amount for Union Legal Insurance, as month:amount pairs
      health        amount for Health Insurance, same shape
    """
    premium_rows, claims_rows, read = [], [], {}

    try:
        f = request.FILES.get('premium_board')
        if f:
            try:
                header, rows, truncated = _read(f)
            except Exception:
                return Response({'detail': 'Could not read the Premium Board file as an '
                                 'Excel workbook. Please re-save it as .xlsx or .xlsb.'}, status=400)
            parsed, skipped = parse_premium_board(header, rows)
            premium_rows += parsed
            read['premium_board'] = {'rows_read': len(rows), 'rows_used': len(parsed),
                                     'skipped': skipped, 'truncated': truncated}

        f = request.FILES.get('month_on_month')
        if f:
            try:
                header, rows, truncated = _read(f)
            except Exception:
                return Response({'detail': 'Could not read the Month-on-Month file as an '
                                 'Excel workbook. Please re-save it as .xlsx or .xlsb.'}, status=400)
            parsed, skipped = parse_month_on_month(header, rows)
            premium_rows += parsed
            read['month_on_month'] = {'rows_read': len(rows), 'rows_used': len(parsed),
                                      'skipped': skipped, 'truncated': truncated}

        f = request.FILES.get('claims_as_on_date')
        if f:
            try:
                header, rows, truncated = _read(f)
            except Exception:
                return Response({'detail': 'Could not read the Claims As On Date file as an '
                                 'Excel workbook. Please re-save it as .xlsx or .xlsb.'}, status=400)
            parsed, skipped = parse_claims_as_on_date(header, rows)
            claims_rows += parsed
            read['claims_as_on_date'] = {'rows_read': len(rows), 'rows_used': len(parsed),
                                         'skipped': skipped, 'truncated': truncated}

        premium_rows += manual_premium_rows(
            _manual(request.data.get('union_legal'), UNION_LEGAL)
            + _manual(request.data.get('health'), HEALTH_INSURANCE))
    except SourceColumnMissing as e:
        # Name the column rather than returning an empty report: a report that
        # totals nothing looks finished.
        return Response({'detail': str(e)}, status=400)
    except ValueError as e:
        return Response({'detail': str(e)}, status=400)

    if not premium_rows and not claims_rows:
        return Response({'detail': 'No source files were readable. Attach at least one.'},
                        status=400)

    months = [m.strip() for m in (request.data.get('months') or '').split(',') if m.strip()]
    bad = [m for m in months if not MONTH_RE.match(m)]
    if bad:
        return Response(
            {'detail': f'Period months must look like 2026-07. Could not read: {", ".join(bad)}.'},
            status=400)

    # Union Legal and Health come from Omni (BONU schedule + health bordereaux),
    # per the CFO's instruction, for the months not supplied by hand. A hand-
    # typed figure for a line still wins — the person overrode it deliberately.
    # Everything below — the Omni-sourced Union Legal / Health pulls and the
    # report engine itself — used to run OUTSIDE any try/except, so a fault in one
    # uploaded file reached the user only as a bare HTTP 500, which the frontend
    # shows as the generic "Could not build the report. Please try again." with no
    # clue what actually broke. Bug 919ec55f (Babusi Rasenyai, 2026-09-01): log
    # the real traceback and return a named error so the fault is diagnosable
    # instead of silent. No figure or mapping changes here.
    try:
        period = months or _months_of_rows(premium_rows)
        hand_lines = {r.line for r in premium_rows if r.line in (UNION_LEGAL, HEALTH_INSURANCE)}
        omni_meta = {}
        if UNION_LEGAL not in hand_lines:
            rows_u, meta_u = union_legal_rows(period)
            premium_rows += manual_premium_rows(rows_u)
            omni_meta['union_legal'] = meta_u
        if HEALTH_INSURANCE not in hand_lines:
            rows_h, meta_h = health_rows(period)
            premium_rows += manual_premium_rows(rows_h)
            omni_meta['health'] = meta_h

        report = build_report(premium_rows, claims_rows, months=months or None)
    except (SourceColumnMissing, ValueError) as e:
        # A data problem we can name (a column the engine still needed, a value it
        # could not read) — 400, the reporter can act on it directly.
        logger.warning('finance-report build: data fault: %s', e, exc_info=True)
        return Response({'detail': str(e)}, status=400)
    except Exception:
        # Any other fault is a real engine error. Log the whole traceback (this is
        # what was missing) and tell the user plainly, without a stack trace.
        logger.exception('finance-report build: unexpected engine error')
        return Response(
            {'detail': 'The report engine hit an unexpected error building the '
                       'tables. The details have been logged for the team — '
                       'please let IT know you saw this so we can pin it down.'},
            status=500)

    report['sources'] = read
    report['omni_sources'] = omni_meta
    report['manual_lines'] = [UNION_LEGAL, HEALTH_INSURANCE]
    return Response(report)


def _manual(raw, line):
    """'2026-07:1234.56,2026-08:900' -> the manual rows for one line.

    Union Legal and Health each feed a single figure a month from a report that
    needs no splitting, so they are typed in rather than parsed.
    """
    out = []
    for pair in (raw or '').split(','):
        pair = pair.strip()
        if not pair:
            continue
        month, _, amount = pair.partition(':')
        if not amount:
            raise ValueError(f'Expected month:amount, got {pair!r}.')
        out.append({'month': month.strip(), 'line': line, 'amount': amount.strip()})
    return out
