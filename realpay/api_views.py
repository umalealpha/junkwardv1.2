"""API endpoints for RealPay monthly reports."""

from __future__ import annotations

import datetime
import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from django.utils import timezone

log = logging.getLogger(__name__)

from .models import (
    RealPayImportControl, RealPayMonthlyReport, RealPayResponseCode, RealPayTransaction,
)
from .services import backfill, compute_analytics, generate_commentary, pull_month


def _ser(r: RealPayMonthlyReport) -> dict:
    return {
        'id':                  str(r.id),
        'period_year':         r.period_year,
        'period_month':        r.period_month,
        'period_label':        r.period_label,
        'beneficiary_user_id': r.beneficiary_user_id,
        'beneficiary_label':   r.beneficiary_label,
        'status':              r.status,
        'txn_count_total':     r.txn_count_total,
        'txn_count_successful': r.txn_count_successful,
        'txn_count_failed':    r.txn_count_failed,
        'amount_collected':    str(r.amount_collected),
        'amount_failed':       str(r.amount_failed),
        'amount_net':          str(r.amount_net),
        'ai_commentary':       r.ai_commentary,
        'xlsx_url':            r.xlsx_file.url if r.xlsx_file else None,
        'pulled_at':           r.pulled_at.isoformat() if r.pulled_at else None,
        'analysed_at':         r.analysed_at.isoformat() if r.analysed_at else None,
        'error_log':           r.error_log,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_reports_list(request):
    """List monthly reports — newest first."""
    rows = list(RealPayMonthlyReport.objects.all()[:200])
    return Response({'reports': [_ser(r) for r in rows]})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def realpay_report_detail(request, report_id):
    try:
        r = RealPayMonthlyReport.objects.get(pk=report_id)
    except RealPayMonthlyReport.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    if request.method == 'GET':
        return Response(_ser(r))
    # PATCH — re-run analysis
    if (request.data or {}).get('regenerate_commentary'):
        generate_commentary(r)
    return Response(_ser(r))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def realpay_pull_month(request):
    """Manual single-month pull. Body: {year, month, beneficiary_user_id, label}."""
    if not request.user.is_superuser:
        prof = getattr(request.user, 'profile', None)
        if not (prof and getattr(prof, 'is_administrator', False)):
            return Response({'detail': 'Permission denied.'}, status=403)
    body = request.data or {}
    try:
        y = int(body.get('year'))
        m = int(body.get('month'))
    except (TypeError, ValueError):
        return Response({'detail': 'year and month must be integers.'}, status=400)
    user_id = (body.get('beneficiary_user_id') or '').strip()
    if not user_id:
        return Response({'detail': 'beneficiary_user_id required.'}, status=400)
    label = (body.get('label') or '').strip()
    try:
        r = pull_month(year=y, month=m, beneficiary_user_id=user_id,
                       beneficiary_label=label, user=request.user)
        generate_commentary(r)
        return Response(_ser(r), status=201)
    except Exception as exc:  # noqa: BLE001
        return Response({'detail': str(exc)[:500]}, status=502)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_analytics(request):
    """Cross-month analytics — defaulters, multi-debit payers, top10, trend."""
    try:
        months = int(request.query_params.get('months') or 12)
    except (TypeError, ValueError):
        months = 12
    user_id = (request.query_params.get('beneficiary_user_id') or '').strip()
    return Response(compute_analytics(months=months, beneficiary_user_id=user_id))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def realpay_backfill(request):
    """Run a Jul-2025 → today backfill. Body: {beneficiary_user_id, label,
    from_year=2025, from_month=7}."""
    if not request.user.is_superuser:
        prof = getattr(request.user, 'profile', None)
        if not (prof and getattr(prof, 'is_administrator', False)):
            return Response({'detail': 'Permission denied.'}, status=403)
    body = request.data or {}
    user_id = (body.get('beneficiary_user_id') or '').strip()
    if not user_id:
        return Response({'detail': 'beneficiary_user_id required.'}, status=400)
    label   = (body.get('label') or '').strip()
    fy      = int(body.get('from_year')  or 2025)
    fm      = int(body.get('from_month') or 7)
    reports = backfill(from_year=fy, from_month=fm,
                       beneficiary_user_id=user_id, beneficiary_label=label,
                       user=request.user)
    return Response({'count': len(reports),
                     'reports': [_ser(r) for r in reports]})


# ===========================================================================
# RealPay Collections module (CFO directive 2026-06-05).
# Objective 1 — Failed / Error Debit Tracker.
# Objective 2 — Collections Dashboard.
# Both are read-only reports over RealPayTransaction; the reason-join uses the
# normalized result code (see realpay.models.normalize_response_code).
# ===========================================================================

from decimal import Decimal as _D
from django.db.models import Count   # noqa: E402

_ZERO = _D('0.00')

# Build-spec Open-Decision defaults, surfaced to the UI for sign-off.
_OD_ASSUMPTIONS = {
    'tracker': [
        'OD-1: period filters on the debit action date (Installment Date).',
        'OD-2: FAILED and ERROR are shown as separate buckets (different reason codes).',
        'OD-5/OD-6: the Transaction Report exposes NO product code, so the reason is '
        'matched on the normalized response code alone; if the code maps to different '
        'descriptions across products the row is flagged AMBIGUOUS rather than guessed.',
        'Counts are debit ATTEMPTS (one row = one debit attempt), not distinct installments. '
        'A re-presented debit appears once per attempt, so "Failed" counts failed attempts; '
        'an installment that failed then succeeded on retry appears in both buckets.',
        'Sentinel result codes (XX, CA) mean "no bank response / not presented" and are not '
        'counted as unmatched reason lookups.',
    ],
    'dashboard': [
        'OD-1: period filters on the transaction date.',
        'OD-3: "Collected" sums AmountCollected over SETTLED rows only (collected_amount > 0). '
        'Verified against the source: AmountCollected is non-zero only on settled (Result 00) '
        'rows — PROCESSING / FAILED / CANCELLED rows carry 0 — so the total is net of '
        'non-settlement. Reconcile to bank before publishing externally.',
        'OD-4: COM/COMG are shown as "Corporate lines" and DOM/DOMG as "Personal lines" '
        '(split per Bokani 2026-06-24; previously one combined bucket).',
        'Line counts shown are "collected lines" (settled debits); "attempts" (all debit rows, '
        'incl. failed/zero) is shown separately so success rate is visible.',
        'Any client number whose leading letters are not MIS / COM / DOM / COMG / DOMG is shown '
        'under "Other (unmapped)" so the grand total always reconciles to Client Billing.',
    ],
}

# Recognised status buckets (build-spec said 3; live data has 5).
_STATUSES = ['SUCCESSFUL', 'FAILED', 'PROCESSING', 'ERROR', 'CANCELLED']

# RealPay sentinel result markers — not a bank response code, so no reason lookup.
_SENTINEL_CODES = {'XX', 'CA'}
_SENTINEL_LABEL = 'No bank response / not presented'


def _parse_date(raw):
    s = (raw or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            return datetime.datetime.strptime(s[:10].replace('/', '-')
                                              if fmt == '%Y-%m-%d' else s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_param(qp, key):
    """Return (date|None, error|None). Distinguishes absent (ok) from
    present-but-unparseable (400) so a bad date never silently drops the filter."""
    raw = (qp.get(key) or '').strip()
    if not raw:
        return None, None
    d = _parse_date(raw)
    if d is None:
        return None, f'Unparseable {key} date: {raw!r} (use YYYY-MM-DD).'
    return d, None


def _csv_safe(v):
    """Neutralise spreadsheet formula injection on exported cells (a value
    starting with = + - @ TAB or CR is executed as a formula by Excel/Calc)."""
    s = '' if v is None else str(v)
    return ("'" + s) if s[:1] in ('=', '+', '-', '@', '\t', '\r') else s


def _reason_index():
    """Build {code_norm: {'desc': str|None, 'ambiguous': bool, 'candidates': [...]}}.

    The Transaction Report has no product code, so we look up on the normalized
    code alone. If the same code maps to >1 distinct description across products
    we mark it ambiguous (OD-5: never pick arbitrarily)."""
    idx = {}
    by_code = {}
    for rc in RealPayResponseCode.objects.all().only('code_norm', 'product_code', 'description'):
        by_code.setdefault(rc.code_norm, []).append((rc.product_code, rc.description))
    for code, pairs in by_code.items():
        descs = sorted({d for _, d in pairs if d})
        if len(descs) == 1:
            idx[code] = {'desc': descs[0], 'ambiguous': False, 'candidates': descs}
        elif len(descs) > 1:
            idx[code] = {'desc': None, 'ambiguous': True, 'candidates': descs}
        else:
            idx[code] = {'desc': None, 'ambiguous': False, 'candidates': []}
    return idx


def _status_filter_values(status_param):
    s = (status_param or 'all').strip().lower().replace('-', '_')
    return {
        'all': None, '': None,
        'failed': ['FAILED'],
        'error': ['ERROR'],
        'failed_error': ['FAILED', 'ERROR'],
        'success': ['SUCCESSFUL'], 'successful': ['SUCCESSFUL'],
        'processing': ['PROCESSING'],
        'cancelled': ['CANCELLED'], 'canceled': ['CANCELLED'],
    }.get(s, None)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_debit_tracker(request):
    """Objective 1 — Failed / Error Debit Tracker.

    Query params: start, end (YYYY-MM-DD), status (all|failed|error|failed_error|
    success|processing|cancelled), export (0|1), limit (screen cap, default 1000).
    """
    qp = request.query_params
    start, e1 = _parse_date_param(qp, 'start')
    end, e2 = _parse_date_param(qp, 'end')
    if e1 or e2:
        return Response({'detail': e1 or e2}, status=400)
    status_vals = _status_filter_values(qp.get('status'))

    base = RealPayTransaction.objects.filter(source=RealPayTransaction.Source.TRANSACTION)
    if start:
        base = base.filter(txn_date__gte=start)
    if end:
        base = base.filter(txn_date__lte=end)

    # Summary counts over the date-filtered set (before the status filter), so
    # the user always sees the full Success/Failed/Error/Processing/Cancelled split.
    counts = {row['current_status']: row['n']
              for row in base.values('current_status').annotate(n=Count('id'))}
    summary = {s: counts.get(s, 0) for s in _STATUSES}
    summary['OTHER'] = sum(v for k, v in counts.items() if k not in _STATUSES)
    summary['TOTAL'] = sum(counts.values())

    rows_qs = base if status_vals is None else base.filter(current_status__in=status_vals)
    rows_qs = rows_qs.order_by('-txn_date', 'client_number')

    ridx = _reason_index()
    matched_codes = [c for c, h in ridx.items() if h['desc'] and not h['ambiguous']]

    def _reason(r):
        """Reason only for FAILED/ERROR rows; success/processing/cancelled blank."""
        if r.current_status not in ('FAILED', 'ERROR'):
            return {'reason': '', 'matched': True, 'ambiguous': False}
        code = r.result_code_norm or ''
        if code in _SENTINEL_CODES:                 # XX / CA — not a bank code
            return {'reason': _SENTINEL_LABEL, 'matched': True, 'ambiguous': False}
        if code == '00':                            # success code on a failed row = anomaly
            return {'reason': f'CONTRADICTION: status={r.current_status} but result=00',
                    'matched': False, 'ambiguous': False}
        if code == '':
            return {'reason': 'No response code on row', 'matched': False, 'ambiguous': False}
        hit = ridx.get(code)
        if hit and hit['ambiguous']:
            return {'reason': 'AMBIGUOUS: ' + ' | '.join(hit['candidates']),
                    'matched': False, 'ambiguous': True}
        if not hit or not hit['desc']:
            return {'reason': f'UNMATCHED (code {r.result_code})',
                    'matched': False, 'ambiguous': False}
        return {'reason': hit['desc'], 'matched': True, 'ambiguous': False}

    # Export branch — stream the entire filtered set as CSV (audit-logged).
    # Spec §6: client NAMES are re-attached locally via VLOOKUP, never exported.
    if (qp.get('export') or '').strip().lower() in ('1', 'csv', 'true'):
        import csv as _csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type='text/csv')
        fn = f'realpay_debit_tracker_{start or "all"}_{end or "all"}.csv'
        resp['Content-Disposition'] = f'attachment; filename="{fn}"'
        resp['Cache-Control'] = 'no-store'
        resp['Pragma'] = 'no-cache'
        w = _csv.writer(resp)
        w.writerow(['Installment Date', 'Client Number', 'Contract', 'Total Amount',
                    'Collected Amount', 'Current Status', 'Result Code', 'Reason', 'Client Bank'])
        n_exported = 0
        for r in rows_qs.iterator(chunk_size=2000):
            info = _reason(r)
            w.writerow([_csv_safe(x) for x in (
                r.txn_date.isoformat() if r.txn_date else '',
                r.client_number, r.contract_number,
                str(r.total_amount), str(r.collected_amount),
                r.current_status, r.result_code, info['reason'], r.client_bank,
            )])
            n_exported += 1
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name='realpay_transaction', record_id='debit-tracker-export',
                action='download',
                user=request.user if request.user.is_authenticated else None,
                ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                            or request.META.get('REMOTE_ADDR') or '')[:45],
                description=(f'RealPay debit-tracker export: {n_exported} rows '
                             f'(start={qp.get("start") or "-"}, end={qp.get("end") or "-"}, '
                             f'status={qp.get("status") or "all"}). No client names (spec §6).'),
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('debit-tracker export audit-log failed')
        return resp

    try:
        limit = max(1, min(int(qp.get('limit') or 1000), 5000))
    except (TypeError, ValueError):
        limit = 1000

    total_rows = rows_qs.count()

    # Unmatched reason count over the FULL filtered set (independent of the screen
    # cap): FAILED/ERROR rows carrying a real code that did not map (ambiguous
    # included), excluding blank / 00 / sentinel which are "no reason expected".
    fe = base.filter(current_status__in=['FAILED', 'ERROR'])
    total_fe = fe.count()
    no_reason_expected = fe.filter(
        result_code_norm__in=(['', '00'] + list(_SENTINEL_CODES))).count()
    matched_fe = fe.filter(result_code_norm__in=matched_codes).count() if matched_codes else 0
    unmatched_full = max(0, total_fe - matched_fe - no_reason_expected)

    out = []
    for r in rows_qs[:limit]:
        info = _reason(r)
        out.append({
            'txn_date': r.txn_date.isoformat() if r.txn_date else None,
            'client_number': r.client_number,
            'contract_number': r.contract_number,
            'total_amount': str(r.total_amount),
            'collected_amount': str(r.collected_amount),
            'current_status': r.current_status,
            'result_code': r.result_code,
            'reason': info['reason'],
            'matched': info['matched'],
            'ambiguous': info['ambiguous'],
            'client_bank': r.client_bank,
        })

    return Response({
        'summary': summary,
        'rows': out,
        'row_count_filtered': total_rows,
        'rows_truncated': total_rows > limit,
        'unmatched_reason_count': unmatched_full,
        'filters': {'start': qp.get('start'), 'end': qp.get('end'),
                    'status': qp.get('status') or 'all'},
        'assumptions': _OD_ASSUMPTIONS['tracker'],
        'data_present': summary['TOTAL'] > 0,
    })


import re as _re   # noqa: E402
_PREFIX_RE = _re.compile(r'^[A-Z]+')


def _classify_prefix(client_number):
    """Classify by the EXACT leading-letter run (not substring startswith), so
    'MISC'/'DOMESTIC'/'COMMERCIAL' fall to OTHER instead of mis-bucketing."""
    cn = (client_number or '').strip().upper()
    m = _PREFIX_RE.match(cn)
    pref = m.group(0) if m else ''
    if pref == 'MIS':
        return ('INSTANT', 'INSTANT')
    # OD-4 (Bokani 2026-06-24): split Corporate (COM/COMG) vs Personal (DOM/DOMG);
    # was one combined "Corporate and Personal" bucket.
    if pref in ('COM', 'COMG'):
        return ('CORPORATE', 'Corporate lines')
    if pref in ('DOM', 'DOMG'):
        return ('PERSONAL', 'Personal lines')
    return ('OTHER', 'Other (unmapped)')


def _prefix_code(client_number):
    """The raw leading-letter run of a client number (MIS / COM / COMG / DOM /
    DOMG / ''). Finance reconciles the export against their own MIS/COMG/DOMG
    report, so the file carries the prefix as well as the product label."""
    m = _PREFIX_RE.match((client_number or '').strip().upper())
    return m.group(0) if m else ''


def _date_only(v):
    """Render a tracking date as plain YYYY-MM-DD. Graphite stores
    InstalmentActionDate as an ISO string ('2026-04-01T00:00:00Z'); the uploaded
    fallback stores a real date. Anything unexpected passes through as-is."""
    if v is None:
        return ''
    if hasattr(v, 'isoformat'):
        return v.isoformat()[:10]
    return str(v)[:10]


def _collections_dashboard_live(request, qp, start, end, do_export, live):
    """Build the Collections Dashboard response from the LIVE Graphite feed.
    Same product split and response shape as the uploaded-file path; the split is
    computed with the identical ``_classify_prefix`` so numbers stay comparable.
    'Collected' = instalments with status S; 'attempts' = actual debit attempts
    (S/F/W/R/E) — scheduled/cancelled mandates are not counted."""
    from . import graphite_feed

    defs = [('INSTANT', 'INSTANT'),
            ('CORPORATE', 'Corporate lines'),
            ('PERSONAL', 'Personal lines'),
            ('OTHER', 'Other (unmapped)')]
    groups = {k: {'key': k, 'label': lbl, 'collected': _ZERO, 'collected_lines': 0,
                  'attempts': 0, 'awaiting_result': 0} for k, lbl in defs}
    unmapped_prefixes = {}
    for r in live['rows']:
        key, _lbl = _classify_prefix(r['pref'])
        g = groups[key]
        g['attempts'] += r['attempts']
        g['awaiting_result'] += r.get('awaiting_result', 0)
        g['collected_lines'] += r['collected_lines']
        g['collected'] += _D(str(r['collected_amount']))
        if key == 'OTHER':
            p = (r['pref'] or '(blank)')
            unmapped_prefixes[p] = unmapped_prefixes.get(p, 0) + r['attempts']

    total = sum((g['collected'] for g in groups.values()), _ZERO)

    # Export branch — settled rows straight from Graphite (audit-logged).
    if do_export:
        import csv as _csv
        from django.http import HttpResponse
        _CAP = 200000
        rows = graphite_feed.collected_rows(start, end, limit=_CAP) or []
        resp = HttpResponse(content_type='text/csv')
        fn = f'realpay_collections_live_{start or "all"}_{end or "all"}.csv'
        resp['Content-Disposition'] = f'attachment; filename="{fn}"'
        resp['Cache-Control'] = 'no-store'
        resp['Pragma'] = 'no-cache'
        w = _csv.writer(resp)
        # Product Code is the raw clientNumber prefix (MIS / COM / COMG / DOM /
        # DOMG) Finance reconciles against; Grouping stays the product label so
        # the file still ties to the on-screen split.
        # Client NAME is included for the finance-permissioned reconciliation
        # download only (CFO 2026-09-08, replacing the local-VLOOKUP rule).
        names = graphite_feed.client_names([r.get('cn') for r in rows])
        w.writerow(['Client Number', 'Client Name', 'Product Code', 'Grouping',
                    'Tracking Start Date', 'Collected Amount'])
        for row in rows:
            cn = row.get('cn') or ''
            _k, lbl = _classify_prefix(cn)
            w.writerow([_csv_safe(cn), _csv_safe(names.get(cn.strip(), '')),
                        _csv_safe(_prefix_code(cn)), _csv_safe(lbl),
                        _csv_safe(_date_only(row.get('action_date'))),
                        _csv_safe(str(row.get('amt') or _ZERO))])
        w.writerow([])
        # Fail LOUD: a capped read must never masquerade as a complete file.
        if len(rows) >= _CAP:
            w.writerow(['INCOMPLETE EXPORT', '', '', '',
                        f'row cap {_CAP} reached — narrow the date range', ''])
        w.writerow(['TOTAL COLLECTED', '', '', '', '', str(total)])
        w.writerow(['ROWS EXPORTED', '', '', '', '', str(len(rows))])
        w.writerow(['COLLECTED LINES ON SCREEN', '', '', '', '',
                    str(sum(g['collected_lines'] for g in groups.values()))])
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name='realpay_contract_installments',
                record_id='collections-dashboard-live-export', action='download',
                user=request.user if request.user.is_authenticated else None,
                ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                            or request.META.get('REMOTE_ADDR') or '')[:45],
                description=(f'RealPay LIVE collections export: {len(rows)} settled rows, '
                             f'total={total} (start={qp.get("start") or "-"}, end={qp.get("end") or "-"}).'),
            )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception('live collections export audit-log failed')
        return resp

    total_collected_lines = sum(g['collected_lines'] for g in groups.values())
    total_attempts = sum(g['attempts'] for g in groups.values())

    out_groups = []
    for k, _lbl in defs:
        g = groups[k]
        if k == 'OTHER' and g['attempts'] == 0 and g['awaiting_result'] == 0:
            continue
        out_groups.append({
            'key': g['key'], 'label': g['label'],
            'collected': str(g['collected']),
            'collected_lines': g['collected_lines'],
            'attempts': g['attempts'],
            'awaiting_result': g['awaiting_result'],
            # 🔴 Debits were RAISED here and no result ever came back, so a
            # collected figure of zero is an ABSENCE OF INFORMATION, not a fact.
            # Rendering it as "BWP 0.00" told Finance the corporate book
            # collected nothing in September when the truth is that the
            # RealPay -> Graphite result feed has been dead since June.
            'no_result_received': g['attempts'] == 0 and g['awaiting_result'] > 0,
            'pct': (float(g['collected'] / total * 100) if total else 0.0),
        })

    awaiting_total = sum(g['awaiting_result'] for g in groups.values())
    blind_groups = [g['label'] for g in out_groups if g['no_result_received']]

    return Response({
        'groups': out_groups,
        'awaiting_result_total': awaiting_total,
        'groups_with_no_result': blind_groups,
        'total_collected': str(total),
        'total_collected_lines': total_collected_lines,
        'total_attempts': total_attempts,
        'total_count': total_attempts,   # back-compat
        'undated_rows': 0,
        'reconciled': None,              # live source — no import-file to reconcile to
        'control_total': None,
        'variance': None,
        'filters': {
            'start': start.isoformat() if start else qp.get('start'),
            'end': end.isoformat() if end else qp.get('end'),
            'defaulted_to_current_month': not (qp.get('start') or qp.get('end')),
        },
        'unmapped_prefixes': sorted(
            ({'prefix': p, 'count': c} for p, c in unmapped_prefixes.items()),
            key=lambda x: -x['count'],
        ),
        'assumptions': _OD_ASSUMPTIONS['dashboard'],
        'data_present': total_attempts > 0,
        'source': 'graphite-live',
        'live': True,
        'as_of': timezone.localdate().isoformat(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_collections_dashboard(request):
    """Objective 2 — Collections Dashboard. Collected amount by product grouping
    (INSTANT vs Corporate vs Personal vs Other) for a date range. Query params:
    start, end (YYYY-MM-DD), export (0|1). "Collected" sums settled rows only.
    Reads LIVE from Graphite when the read bridge is wired; uploaded rows are the
    dev fallback."""
    qp = request.query_params
    start, e1 = _parse_date_param(qp, 'start')
    end, e2 = _parse_date_param(qp, 'end')
    if e1 or e2:
        return Response({'detail': e1 or e2}, status=400)

    do_export = (qp.get('export') or '').strip().lower() in ('1', 'csv', 'true')

    # The CSV carries client NAMES (CFO 2026-09-08), so the download — and only
    # the download — is gated on the finance permission. The on-screen dashboard
    # is aggregate-only and stays open to any signed-in staff member, which is
    # what bug 6d76ec6c asked for. Without this the export was IsAuthenticated,
    # i.e. every Omni login could have pulled the customer list.
    if do_export and not CanViewFinancials().has_permission(request, None):
        return Response({'detail': CanViewFinancials.message}, status=403)

    # Default the dashboard to the CURRENT MONTH when no range is given, so the
    # headline figure is a clean single month that reconciles to Finance's own
    # month-by-month RealPay pull (Bokani, 2026-09-07) — not an all-history sum
    # (P97.8M) that can never match a monthly figure. The feed already groups on
    # InstalmentActionDate (the tracking-start date), so a month selected here
    # captures every debit in its correct month with no two-report combine.
    # Explicit start/end still override; CSV export keeps the caller's range.
    if start is None and end is None and not do_export:
        _today = timezone.localdate()
        start = _today.replace(day=1)
        end = _today

    from . import graphite_feed

    # ---- RealPay Client Billing (authoritative where it has been loaded) -----
    # Graphite's instalment feed is a mirror of RealPay's outcomes, and for
    # August 2026 it never received them: of the 2,528 policies RealPay's own
    # Client Billing report says collected, 44% of Instant still read
    # 'processing', 72% of Corporate/Domestic read 'raised, no result', and 13%
    # /25% had no August row at all (measured on the read-only replica against
    # Bokani's export, bug 6a48367f, 2026-09-15). A dashboard cannot report
    # money off a feed that did not report.
    #
    # So where Finance has uploaded RealPay's own Client Billing Period report
    # for the window, THAT is the figure, and Graphite is shown beside it as a
    # cross-check. Where no billing rows have been loaded, nothing changes and
    # the Graphite live read stands — including its "no result received, this is
    # not a zero" honesty.
    #
    # The window filters on tracking_start_date, which is what makes the
    # two-report combine work: a debit tracked in August that only settles in
    # the September report still lands in August. Rows loaded before that field
    # existed carry NULL and are deliberately NOT backfilled — inferring it from
    # txn_date would silently restate months Finance has already reported.
    billing_base = RealPayTransaction.objects.filter(
        source=RealPayTransaction.Source.BILLING,
        tracking_start_date__isnull=False)
    if start:
        billing_base = billing_base.filter(tracking_start_date__gte=start)
    if end:
        billing_base = billing_base.filter(tracking_start_date__lte=end)
    use_billing = billing_base.exists()

    live = graphite_feed.collections_by_group(start, end)
    if live.get('configured') and not use_billing:
        return _collections_dashboard_live(request, qp, start, end, do_export, live)

    # A billing row loaded before tracking_start_date existed carries NULL, so
    # once the month switches to the billing basis it is in NO total at all —
    # not counted, not flagged, just gone. Money silently out of a figure is the
    # worst outcome here, so count those rows and report them beside the total.
    legacy_undated_billing_rows = 0
    if use_billing:
        legacy = RealPayTransaction.objects.filter(
            source=RealPayTransaction.Source.BILLING,
            tracking_start_date__isnull=True)
        if start:
            legacy = legacy.filter(txn_date__gte=start)
        if end:
            legacy = legacy.filter(txn_date__lte=end)
        legacy_undated_billing_rows = legacy.count()

    if use_billing:
        base = billing_base
    else:
        base = RealPayTransaction.objects.filter(
            source=RealPayTransaction.Source.BILLING)
        if start:
            base = base.filter(txn_date__gte=start)
        if end:
            base = base.filter(txn_date__lte=end)

    defs = [('INSTANT', 'INSTANT'),
            ('CORPORATE', 'Corporate lines'),
            ('PERSONAL', 'Personal lines'),
            ('OTHER', 'Other (unmapped)')]
    groups = {k: {'key': k, 'label': lbl, 'collected': _ZERO,
                  'collected_lines': 0, 'attempts': 0} for k, lbl in defs}
    unmapped_prefixes = {}
    undated = 0

    export_rows = []

    # Settlement gate: only rows where money actually moved (collected_amount > 0)
    # count toward "collected"/"collected_lines"; every row counts as an "attempt".
    for cn, nm, amt, td in base.values_list(
            'client_number', 'client_name', 'collected_amount',
            'txn_date').iterator(chunk_size=5000):
        key, _lbl = _classify_prefix(cn)
        g = groups[key]
        g['attempts'] += 1
        a = amt or _ZERO
        if a > _ZERO:
            g['collected'] += a
            g['collected_lines'] += 1
            if do_export:
                export_rows.append((cn, nm or '', _prefix_code(cn), g['label'],
                                    _date_only(td), str(a)))
        if td is None:
            undated += 1
        if key == 'OTHER':
            p = (cn or '').strip().upper()[:4] or '(blank)'
            unmapped_prefixes[p] = unmapped_prefixes.get(p, 0) + 1

    total = sum((g['collected'] for g in groups.values()), _ZERO)

    # Export branch (CSV of settled rows, audit-logged) — AC#4 reproducible artifact.
    if do_export:
        import csv as _csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type='text/csv')
        fn = f'realpay_collections_{start or "all"}_{end or "all"}.csv'
        resp['Content-Disposition'] = f'attachment; filename="{fn}"'
        resp['Cache-Control'] = 'no-store'
        resp['Pragma'] = 'no-cache'
        w = _csv.writer(resp)
        w.writerow(['Client Number', 'Client Name', 'Product Code', 'Grouping',
                    'Tracking Start Date', 'Collected Amount'])
        for row in export_rows:
            w.writerow([_csv_safe(x) for x in row])
        w.writerow([])
        w.writerow(['TOTAL COLLECTED', '', '', '', '', str(total)])
        w.writerow(['ROWS EXPORTED', '', '', '', '', str(len(export_rows))])
        try:
            from core.models import AuditLog
            AuditLog.objects.create(
                table_name='realpay_transaction', record_id='collections-dashboard-export',
                action='download',
                user=request.user if request.user.is_authenticated else None,
                ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                            or request.META.get('REMOTE_ADDR') or '')[:45],
                description=(f'RealPay collections export: {len(export_rows)} settled rows, '
                             f'total={total} (start={qp.get("start") or "-"}, end={qp.get("end") or "-"}).'),
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('collections export audit-log failed')
        return resp

    total_collected_lines = sum(g['collected_lines'] for g in groups.values())
    total_attempts = sum(g['attempts'] for g in groups.values())

    out_groups = []
    for k, _lbl in defs:
        g = groups[k]
        if k == 'OTHER' and g['attempts'] == 0:
            continue
        out_groups.append({
            'key': g['key'], 'label': g['label'],
            'collected': str(g['collected']),
            'collected_lines': g['collected_lines'],
            'attempts': g['attempts'],
            'pct': (float(g['collected'] / total * 100) if total else 0.0),
        })

    # Reconciliation control — compares the computed total to the row sum actually
    # loaded at import (only meaningful for the full, unfiltered file).
    reconciled = None
    control_total = None
    variance = None
    if not start and not end:
        ctrl = (RealPayImportControl.objects
                .filter(source=RealPayTransaction.Source.BILLING)
                .order_by('-created_at').first())
        if ctrl:
            control_total = str(ctrl.collected_sum)
            variance = str(total - ctrl.collected_sum)
            reconciled = abs(total - ctrl.collected_sum) < _D('0.01')

    return Response({
        'groups': out_groups,
        'total_collected': str(total),
        'total_collected_lines': total_collected_lines,
        'total_attempts': total_attempts,
        'total_count': total_attempts,   # back-compat
        'undated_rows': undated,
        # Billing rows from before the field existed: in the window by txn_date
        # but excluded from the billing-basis total. Reported so the gap is
        # visible rather than silent.
        'legacy_undated_billing_rows': legacy_undated_billing_rows,
        'legacy_undated_note': (
            f'{legacy_undated_billing_rows} billing row(s) loaded before the '
            'tracking date existed are not in this total. Re-upload the Client '
            'Billing export for this period to include them.'
        ) if legacy_undated_billing_rows else None,
        'reconciled': reconciled,
        'control_total': control_total,
        'variance': variance,
        'filters': {
            'start': start.isoformat() if start else qp.get('start'),
            'end': end.isoformat() if end else qp.get('end'),
            'defaulted_to_current_month': not (qp.get('start') or qp.get('end')),
        },
        'unmapped_prefixes': sorted(
            ({'prefix': p, 'count': c} for p, c in unmapped_prefixes.items()),
            key=lambda x: -x['count'],
        ),
        'source': 'client-billing' if use_billing else 'uploaded-rows',
        'source_label': ("RealPay Client Billing report (uploaded)" if use_billing
                         else "Uploaded RealPay rows"),
        'cross_check': _graphite_cross_check(live, total) if use_billing else None,
        'assumptions': _OD_ASSUMPTIONS['dashboard'],
        'data_present': total_attempts > 0,
    })


def _graphite_cross_check(live, billing_total):
    """What Graphite says for the same window, beside what RealPay says.

    Shown, never used to adjust the figure. The whole point of reading the
    Client Billing report is that Graphite under-reports; silently reconciling
    the two would hide exactly the gap Finance needs to see. ``difference`` is
    RealPay minus Graphite, so a positive number is collection Graphite never
    heard about.
    """
    if not live.get('configured'):
        return None
    rows = live.get('rows') or []
    g_total = sum(_D(str(r.get('collected_amount') or 0)) for r in rows)
    awaiting = sum(int(r.get('awaiting_result') or 0) for r in rows)
    return {
        'graphite_collected': str(g_total),
        'difference': str(billing_total - g_total),
        'graphite_awaiting_result': awaiting,
        'note': ('Graphite is the mirror of RealPay\'s outcome feed. Where it is '
                 'lower, those collections were never reported back to it — the '
                 'figure above comes from RealPay\'s own Client Billing report.'),
    }


# ===========================================================================
# RealPay Collections ANALYTICS (CFO directive 2026-06-05) — monthly trend,
# paying-client counts, success rate, status mix, by-merchant / bank / grouping.
# Source = Transaction Report rows. Charges are NOT in the transaction report.
# ===========================================================================

_GROUP_LABELS = {'INSTANT': 'INSTANT',
                 'CORPORATE': 'Corporate lines',
                 'PERSONAL': 'Personal lines',
                 'OTHER': 'Other (unmapped)'}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_collections_analytics(request):
    """Monthly collections + paying-client counts + success rate + breakdowns.
    Query params: start, end (YYYY-MM-DD). Read-only over transaction rows."""
    from django.db.models import Count, Sum, Q
    from django.db.models.functions import TruncMonth

    qp = request.query_params
    start, e1 = _parse_date_param(qp, 'start')
    end, e2 = _parse_date_param(qp, 'end')
    if e1 or e2:
        return Response({'detail': e1 or e2}, status=400)

    base = RealPayTransaction.objects.filter(source=RealPayTransaction.Source.TRANSACTION)
    if start:
        base = base.filter(txn_date__gte=start)
    if end:
        base = base.filter(txn_date__lte=end)

    pos = Q(collected_amount__gt=0)
    _today_ym = timezone.localdate().strftime('%Y-%m')

    # Monthly series. success_rate is SETTLED basis: SUCCESSFUL / (SUCCESSFUL +
    # FAILED + ERROR) — i.e. of debits that reached a pass/fail decision.
    # In-flight (PROCESSING/RETRY) and never-presented (CANCELLED) are excluded
    # from the rate denominator so it is comparable month-to-month.
    monthly = []
    for r in (base.annotate(m=TruncMonth('txn_date')).values('m')
              .annotate(attempts=Count('id'),
                        collected=Sum('collected_amount', filter=pos),
                        paying=Count('client_number', distinct=True, filter=pos),
                        successful=Count('id', filter=Q(current_status='SUCCESSFUL')),
                        failed=Count('id', filter=Q(current_status__in=['FAILED', 'ERROR'])))
              .order_by('m')):
        if r['m'] is None:
            continue
        att = r['attempts'] or 0
        succ = r['successful'] or 0
        fail = r['failed'] or 0
        resolved = succ + fail
        ym = r['m'].strftime('%Y-%m')
        monthly.append({
            'month': ym,
            'label': r['m'].strftime('%b %Y'),
            'collected': str(r['collected'] or _ZERO),
            'paying_clients': r['paying'] or 0,
            'attempts': att,
            'successful': succ,
            'failed': fail,
            'resolved': resolved,
            'success_rate': round(succ / resolved * 100, 1) if resolved else 0.0,
            'partial': ym == _today_ym,
        })

    by_status = {r['current_status'] or '(blank)': r['n']
                 for r in base.values('current_status').annotate(n=Count('id')).order_by('-n')}

    by_merchant = [{'merchant': r['merchant'] or '(none)',
                    'collected': str(r['collected'] or _ZERO),
                    'attempts': r['attempts'],
                    'paying_clients': r['paying'] or 0}
                   for r in (base.values('merchant').annotate(
                       collected=Sum('collected_amount', filter=pos),
                       attempts=Count('id'),
                       paying=Count('client_number', distinct=True, filter=pos))
                       .order_by('-collected'))]

    by_bank = [{'bank': r['client_bank'] or '(none)',
                'collected': str(r['collected'] or _ZERO),
                'attempts': r['attempts']}
               for r in (base.values('client_bank').annotate(
                   collected=Sum('collected_amount', filter=pos),
                   attempts=Count('id')).order_by('-collected')[:12])]

    # By client-number grouping (Python pass over settled rows)
    gsum = {'INSTANT': _ZERO, 'CORPORATE': _ZERO, 'PERSONAL': _ZERO, 'OTHER': _ZERO}
    gcnt = {'INSTANT': 0, 'CORPORATE': 0, 'PERSONAL': 0, 'OTHER': 0}
    for cn, amt in base.filter(pos).values_list('client_number', 'collected_amount').iterator(chunk_size=5000):
        k, _l = _classify_prefix(cn)
        gsum[k] += (amt or _ZERO)
        gcnt[k] += 1
    by_grouping = [{'key': k, 'label': _GROUP_LABELS[k],
                    'collected': str(gsum[k]), 'collected_lines': gcnt[k]}
                   for k in ('INSTANT', 'CORPORATE', 'PERSONAL', 'OTHER') if gcnt[k] or k != 'OTHER']

    agg = base.aggregate(collected=Sum('collected_amount', filter=pos),
                         attempts=Count('id'),
                         paying=Count('client_number', distinct=True, filter=pos),
                         successful=Count('id', filter=Q(current_status='SUCCESSFUL')),
                         failed=Count('id', filter=Q(current_status__in=['FAILED', 'ERROR'])))
    att = agg['attempts'] or 0
    resolved = (agg['successful'] or 0) + (agg['failed'] or 0)
    totals = {'collected': str(agg['collected'] or _ZERO), 'attempts': att,
              'paying_clients': agg['paying'] or 0,          # distinct over the whole period
              'successful': agg['successful'] or 0,
              'resolved': resolved,
              'success_rate': round((agg['successful'] or 0) / resolved * 100, 1) if resolved else 0.0}
    undated = base.filter(txn_date__isnull=True).count()

    # Fiscal-year rollup (Alpha Direct FY = Jul..Jun)
    fy = {}
    for mo in monthly:
        y, mm = int(mo['month'][:4]), int(mo['month'][5:7])
        fyear = y + 1 if mm >= 7 else y
        key = f'FY{str(fyear)[2:]}'
        b = fy.setdefault(key, {'fy': key, 'collected': _ZERO, 'months': 0,
                                'attempts': 0, 'paying_peak': 0})
        b['collected'] += _D(mo['collected'])
        b['months'] += 1
        b['attempts'] += mo['attempts']
        b['paying_peak'] = max(b['paying_peak'], mo['paying_clients'])
    fy_summary = [{'fy': v['fy'], 'collected': str(v['collected']),
                   'months': v['months'], 'attempts': v['attempts'],
                   'paying_peak': v['paying_peak']} for v in fy.values()]

    cov = [m['month'] for m in monthly]
    partial_latest = cov[-1] if (monthly and monthly[-1]['partial']) else None
    return Response({
        'coverage': {'months': cov, 'min': cov[0] if cov else None,
                     'max': cov[-1] if cov else None,
                     'partial_latest_month': partial_latest,
                     'note': ('Coverage reflects the transaction reports loaded into Omni so far. '
                              'Earlier months (FY25 / Jul 2025–Jan 2026) ARE available in the '
                              'RealPay portal under Archive → Transactions Report (Archived) and '
                              'can be pulled in to extend this view. The latest month may be partial.')},
        'monthly': monthly,
        'by_status': by_status,
        'by_merchant': by_merchant,
        'by_bank': by_bank,
        'by_grouping': by_grouping,
        'totals': totals,
        'fy_summary': fy_summary,
        'undated_rows': undated,
        'notes': [
            'Counts are debit ATTEMPTS (one row = one attempt incl. re-presentations); '
            'an installment that failed then succeeded on retry appears in more than one bucket.',
            'Success rate is the SETTLED basis: SUCCESSFUL ÷ (SUCCESSFUL + FAILED + ERROR). '
            'In-flight (PROCESSING/RETRY) and never-presented (CANCELLED) are excluded from the rate.',
            '"Collected" sums settled rows only (collected_amount > 0).',
            'Per-entity paying-client counts are distinct WITHIN each entity and overlap across '
            'entities, so they do not sum to the period total.',
            'The latest month may be partial (incomplete debit cycle) — flagged where shown.',
        ],
        'charges': {'available': False,
                    'note': ('RealPay charges (Cost-to-client) are not in the Transactions Report — '
                             'they live in the separate Client Billing detail report. Pull that report '
                             'to populate a charges panel.')},
        'data_present': att > 0,
    })


# ---------------------------------------------------------------------------
# Upload-and-reconcile (Keetile Mokhendo's request, handover note 2026-08-17).
#
# The CLI importer (import_realpay_collections) has existed since June, but it
# needs a shell on the box — so in practice only I could run it, and Keetile's
# reconciliation stayed a manual monthly job. This is the same work behind a
# file picker: he exports from RealPay, drops the file here, and gets the
# reconciliation plus a written read of it.
#
# Financial data, so the gate is CanViewFinancials — not a bare login. Nothing
# here writes a journal, moves money or touches a policy: it reads the upload,
# reads Graphite read-only, and returns the comparison.
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def realpay_billing_upload(request):
    """POST a RealPay Client Billing Period export → rows loaded, figures back.

    This is the only way tracking_start_date ever gets into the database, and
    therefore the only way the dashboard switches off the Graphite estimate for
    a month. Without it the whole billing basis is dormant.

    Multipart with `file`. Idempotent: re-uploading the same export updates the
    same rows rather than doubling the month (merge_rows upserts on row_key).
    """
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'Choose a Client Billing Period export to upload.'},
                        status=400)
    if getattr(f, 'size', 0) > 60 * 1024 * 1024:
        return Response({'detail': 'That file is over 60 MB. Export a shorter date range.'},
                        status=400)

    from .billing_import import BillingParseError, merge_rows, parse_client_billing
    try:
        parsed = parse_client_billing(f.read(), f.name)
    except BillingParseError as e:
        # Already written for a person to act on — pass it straight through.
        return Response({'detail': str(e)}, status=400)
    except Exception:  # noqa: BLE001
        log.exception('RealPay billing: could not read %r', f.name)
        return Response({'detail': "Couldn't read that file. Re-save it as .xlsx and try again."},
                        status=400)

    if not parsed['rows']:
        return Response({'detail': 'No billing rows with a tracking date were found in that file.'},
                        status=400)

    batch = timezone.localdate().isoformat()
    result = merge_rows(parsed['rows'], batch)
    result['file'] = {'name': f.name, 'sheet': parsed.get('sheet'),
                      'rows_read': len(parsed['rows']),
                      'rows_skipped': parsed.get('skipped'),
                      'date_column_used': parsed.get('used_date_column')}
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def realpay_recon_upload(request):
    """POST a RealPay export (.xlsb / .xlsx / .csv) → reconciliation + commentary.

    Multipart with `file`. Optional `commentary=0` to skip the AI read (the
    figures are the point; the words are the convenience).
    """
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'Choose a RealPay export file to upload.'}, status=400)
    if getattr(f, 'size', 0) > 60 * 1024 * 1024:
        return Response({'detail': 'That file is over 60 MB. Export a shorter date range.'},
                        status=400)

    from .recon import commentary as _commentary, parse_realpay_file, reconcile
    try:
        parsed = parse_realpay_file(f.read(), f.name)
    except ValueError as e:
        # A person-readable reason, not a stack trace: they can act on this.
        return Response({'detail': str(e)}, status=400)
    except Exception:  # noqa: BLE001
        log.exception('RealPay recon: could not read %r', f.name)
        return Response({'detail': "Couldn't read that file. Re-save it as .xlsx and try again."},
                        status=400)

    if not parsed['rows']:
        return Response({'detail': 'No debit rows with a contract number were found in that file.'},
                        status=400)

    result = reconcile(parsed['rows'])
    result['file'] = {'name': f.name, 'sheet': parsed['sheet'],
                      'rows_read': len(parsed['rows']),
                      'rows_skipped_no_contract': parsed['skipped']}
    want_ai = str(request.data.get('commentary', '1')).lower() not in ('0', 'false', 'no')
    result['commentary'] = (_commentary(result) if want_ai
                            else {'ok': False, 'text': '', 'reason': 'Not requested.'})
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def realpay_graphite_live(request):
    """LIVE RealPay collections read straight from Graphite (source of truth).

    Independent of the vendor API credential — reads the debit-order instalments
    Graphite already collects, via the read-only bridge. Never mixed into the
    uploaded RealPayTransaction rows, so it cannot double-count. See
    realpay/graphite_feed.py.
    """
    from .graphite_feed import monthly_collections
    try:
        months = int(request.query_params.get('months') or 6)
    except (TypeError, ValueError):
        months = 6
    try:
        return Response(monthly_collections(months=months))
    except Exception as exc:  # noqa: BLE001 — surface the read failure, don't 500 blank
        log.warning('realpay graphite-live read failed: %s', exc)
        return Response({'configured': False, 'rows': [], 'totals': {}, 'last30': {},
                         'error': str(exc)[:300]}, status=200)
