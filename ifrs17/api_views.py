"""
ifrs17/api_views.py — the IFRS 17 endpoints.

Read is gated to finance/management (`CanViewFinancials`). Nothing here writes to
the ledger, and nothing here changes a frozen figure — the module reads the signed
valuation and produces its own statements.
"""
from __future__ import annotations

from decimal import Decimal as D

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials, CanViewRegulatoryReturns

from . import constants as K
from .bridge import build_bridge, gwp_check
from .disclosures import build_all
from .statistics import build_all as build_stats
from .engine import Levers, compute
from . import exceptions_service as XS
from . import materiality as M
from .models import IFRS17Exception


def _first_error(exc) -> str:
    """Flatten a Django ValidationError into one sentence for the UI."""
    if hasattr(exc, 'message_dict'):
        for msgs in exc.message_dict.values():
            if msgs:
                return msgs[0]
    msgs = getattr(exc, 'messages', None)
    return msgs[0] if msgs else str(exc)


def _levers_from(params) -> Levers:
    """Build a lever set from query params, falling back to the signed defaults.

    Anything unparseable falls back rather than 500ing — a slider is a view
    control, and a bad value must never be able to produce a wrong figure.
    """
    def dec(key, default):
        raw = params.get(key)
        if raw in (None, ''):
            return default
        try:
            v = D(str(raw))
            return v if v.is_finite() else default          # reject nan / inf
        except Exception:                                   # noqa: BLE001
            return default

    def flag(key, default):
        raw = params.get(key)
        if raw in (None, ''):
            return default
        return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')

    return Levers(
        ra_pct=dec('ra_pct', K.BASE_LEVERS['ra_pct']),
        che_factor_pct=dec('che_factor_pct', K.BASE_LEVERS['che_factor_pct']),
        salvage_subro_pct=dec('salvage_subro_pct', K.BASE_LEVERS['salvage_subro_pct']),
        attributable_share_pct=dec('attributable_share_pct',
                                   K.BASE_LEVERS['attributable_share_pct']),
        ibnr_tail_factor=dec('ibnr_tail_factor', K.BASE_LEVERS['ibnr_tail_factor']),
        discounting=flag('discounting', K.BASE_LEVERS['discounting']),
        jbb_commission_pct=dec('jbb_commission_pct', K.BASE_LEVERS['jbb_commission_pct']),
        onerous_test=flag('onerous_test', K.BASE_LEVERS['onerous_test']),
        health_modelled=flag('health_modelled', K.BASE_LEVERS['health_modelled']),
    )


def _segments_from(params) -> set[str] | None:
    raw = (params.get('segments') or '').strip()
    if not raw:
        return None
    wanted = {s.strip().lower() for s in raw.split(',') if s.strip()}
    return wanted & set(K.SEGMENT_FY26) or None


def _serialise(c) -> dict:
    return {
        'year': c.year,
        'insurance_revenue': c.insurance_revenue,
        'insurance_service_expenses': c.insurance_service_expenses,
        'service_result_before_reinsurance': c.service_result_before_reinsurance,
        'net_reinsurance_result': c.net_reinsurance_result,
        'insurance_service_result': c.insurance_service_result,
        'profit_before_tax': c.profit_before_tax,
        'lrc': c.lrc,
        'lic_best_estimate': c.lic_best_estimate,
        'lic_risk_adjustment': c.lic_risk_adjustment,
        'insurance_contract_liabilities': c.insurance_contract_liabilities,
        'reinsurance_contract_assets': c.reinsurance_contract_assets,
        'net_ifrs17_liability': c.net_ifrs17_liability,
        'gross_case_reserves': c.gross_case_reserves,
        'gross_ibnr': c.gross_ibnr,
        'che_reserve': c.che_reserve,
        'salvage_subrogation': c.salvage_subrogation,
        'claims_incurred': c.claims_incurred,
        'attributable_expenses': c.attributable_expenses,
        'acquisition_amortisation': c.acquisition_amortisation,
        'loss_ratio': c.loss_ratio,
        'combined_ratio': c.combined_ratio,
        'expense_ratio': c.expense_ratio,
        'acquisition_ratio': c.acquisition_ratio,
        'by_segment': [
            {'segment': s.segment, 'premium': s.premium, 'commission': s.commission,
             'claims': s.claims, 'unearned_premium': s.unearned_premium,
             'loss_ratio': s.loss_ratio, 'combined_ratio': s.combined_ratio}
            for s in c.by_segment
        ],
        'by_uwy': c.by_uwy,
        'by_treaty': c.by_treaty,
        'notes': c.notes,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def valuation(request):
    """The valuation at whatever the sliders currently say.

    `?ra_pct=0.08&jbb_commission_pct=0.41&segments=motor,property` etc.
    With no parameters this is Empirica's signed FY2026 valuation, verbatim.
    """
    year = (request.query_params.get('year') or 'FY2026').upper()
    if year not in K.REPORTED:
        return Response({'detail': f'No valuation for {year}.'}, status=400)
    levers = _levers_from(request.query_params)
    c = compute(levers, year=year, segments_on=_segments_from(request.query_params))
    base = compute(Levers.base(), year=year)
    out = _serialise(c)
    out['delta_vs_base'] = {
        'profit_before_tax': c.profit_before_tax - base.profit_before_tax,
        'insurance_revenue': c.insurance_revenue - base.insurance_revenue,
        'insurance_contract_liabilities': (c.insurance_contract_liabilities
                                           - base.insurance_contract_liabilities),
        'gross_ibnr': c.gross_ibnr - base.gross_ibnr,
        'net_ifrs17_liability': c.net_ifrs17_liability - base.net_ifrs17_liability,
    }
    out['is_signed_basis'] = (out['delta_vs_base']['profit_before_tax'] == 0)
    return Response(out)


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def disclosures(request):
    """Every financial-statement disclosure, at the current lever positions."""
    year = (request.query_params.get('year') or 'FY2026').upper()
    if year != 'FY2026':
        # The disclosure builders are written for FY2026; a FY2025 set would carry
        # false movement notes (Fable 2026-08-25). FY2025 is a comparative inside
        # the FY2026 tables, not a standalone disclosure set yet.
        return Response({'detail': 'Disclosures are available for FY2026 only. '
                                   'FY2025 appears as the comparative column.'},
                        status=400)
    c = compute(_levers_from(request.query_params), year=year,
                segments_on=_segments_from(request.query_params))
    return Response({'year': year, 'tables': build_all(c), 'notes': c.notes})


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def statistics(request):
    """Loss ratios by product, six-year trend, growth, mix, ratios, development —
    all derived from the signed figures at the current lever positions."""
    year = (request.query_params.get('year') or 'FY2026').upper()
    if year != 'FY2026':
        return Response({'detail': 'Statistics are available for FY2026.'}, status=400)
    c = compute(_levers_from(request.query_params), year=year,
                segments_on=_segments_from(request.query_params))
    return Response({'year': year, 'tables': build_stats(c)})


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def bridge(request):
    """The IFRS 17 to Management Accounts walk.

    Optional `investment_income`, `other_income`, `tax` — the three figures the
    valuation does not carry. Without them the walk reports what it cannot explain
    rather than absorbing it.
    """
    year = (request.query_params.get('year') or 'FY2025').upper()
    if year not in K.REPORTED:
        return Response({'detail': f'No valuation for {year}.'}, status=400)

    def dec(key):
        raw = request.query_params.get(key)
        if raw in (None, ''):
            return D('0')
        try:
            return D(str(raw))
        except Exception:                                   # noqa: BLE001
            return D('0')

    r = build_bridge(year, investment_income=dec('investment_income'),
                     other_income=dec('other_income'), tax=dec('tax'))
    return Response({
        'year': r.year,
        'ifrs17_profit_before_tax': r.ifrs17_pbt,
        'steps': [{'label': s.label, 'amount': s.amount, 'source': s.source,
                   'note': s.note} for s in r.steps],
        'waterfall': [{'label': l, 'amount': a, 'running': t} for l, a, t in r.running],
        'derived_ma_pat': r.derived_ma_pat,
        'frozen_ma_pat': r.frozen_ma_pat,
        'unexplained': r.unexplained,
        'reconciles': r.reconciles,
        'comparable': r.comparable,
        'conflict': r.conflict,
        'gwp_check': gwp_check(year),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def levers(request):
    """The slider definitions — label, base, range, and why each one matters."""
    return Response({
        'base': {k: v for k, v in K.BASE_LEVERS.items()},
        'jbb': {
            'provisional': K.JBB_PROVISIONAL_RATE,
            'scale_min': K.JBB_SCALE_MIN,
            'scale_max': K.JBB_SCALE_MAX,
            'ceded_premium': K.JBB_CEDED_PREMIUM_FY26,
            'sensitivity_at_max': (K.JBB_SCALE_MAX - K.JBB_PROVISIONAL_RATE)
                                  * K.JBB_CEDED_PREMIUM_FY26,
            'warning': 'Sensitivity only. The report states this is "not a bookable '
                       'adjustment, and it has not been recognised."',
        },
        'segments': list(K.SEGMENT_FY26),
        'health_excluded': True,
        'known_variances': K.KNOWN_VARIANCES,
    })


class _BadYear(Exception):
    pass


def _export_computed(request):
    """Compute for an export. Raises _BadYear for anything the disclosure
    builders cannot honestly produce yet — they are written for FY2026, and the
    A.1/A.3/IBNR notes hard-code FY2026 movements, so a FY2025 export would emit
    a FALSE auditor-facing statement (Fable 2026-08-25). FY2025 stays a
    comparative inside the FY2026 tables until disclosures.py is year-aware."""
    from .engine import compute
    year = (request.query_params.get('year') or 'FY2026').upper()
    if year != 'FY2026':
        raise _BadYear(year)
    return compute(_levers_from(request.query_params), year=year,
                   segments_on=_segments_from(request.query_params)), year


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def export_xlsx(request):
    """The disclosure pack as a workbook, at the current lever positions."""
    from .export import workbook_bytes
    try:
        c, year = _export_computed(request)
    except _BadYear as e:
        return Response({'detail': f'Disclosure export is available for FY2026 only, '
                                   f'not {e}.'}, status=400)
    data = workbook_bytes(c)
    resp = HttpResponse(
        data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="IFRS17_disclosures_{year}.xlsx"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def export_docx(request):
    """The disclosure notes as a Word document for the financial statements."""
    from .export import disclosure_docx
    try:
        c, year = _export_computed(request)
    except _BadYear as e:
        return Response({'detail': f'Disclosure export is available for FY2026 only, '
                                   f'not {e}.'}, status=400)
    data = disclosure_docx(c)
    resp = HttpResponse(
        data, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    resp['Content-Disposition'] = f'attachment; filename="IFRS17_disclosures_{year}.docx"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def data_request(request):
    """Empirica's data-request workbook, pre-filled from the ledger."""
    from .data_request import workbook_bytes
    try:
        c, year = _export_computed(request)
    except _BadYear as e:
        return Response({'detail': f'The data request is available for FY2026 only, '
                                   f'not {e}.'}, status=400)
    data = workbook_bytes(c)
    resp = HttpResponse(
        data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="IFRS17_data_request_{year}.xlsx"'
    return resp


# ---------------------------------------------------------------------------
# The exception register (CFO directive 2026-08-26)
# ---------------------------------------------------------------------------
def _serialise_exception(e) -> dict:
    return {
        'id': str(e.id),
        'ref': e.ref,
        'financial_year': e.financial_year,
        'title': e.title,
        'detail': e.detail,
        'source': e.source,
        'amount': e.amount,
        'severity': e.severity,
        'band': e.band,
        'band_display': e.get_band_display(),
        'materiality_basis': e.materiality_basis,
        'materiality_reason': e.materiality_reason,
        'threshold_at_seed': e.threshold_at_seed,
        'status': e.status,
        'status_display': e.get_status_display(),
        'explanation': e.explanation,
        'action': e.action,
        'explanation_required': e.explanation_required,
        'is_outstanding': e.is_outstanding,
        'owner': getattr(e.owner, 'get_full_name', lambda: '')() or getattr(e.owner, 'username', '') if e.owner_id else '',
        'answered_by': getattr(e.answered_by, 'get_full_name', lambda: '')() or getattr(e.answered_by, 'username', '') if e.answered_by_id else '',
        'answered_at': e.answered_at,
        'reviewed_by': getattr(e.reviewed_by, 'get_full_name', lambda: '')() or getattr(e.reviewed_by, 'username', '') if e.reviewed_by_id else '',
        'reviewed_at': e.reviewed_at,
        'review_note': e.review_note,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def exceptions(request):
    """The exception register: material first, with the materiality working.

    `?year=FY2026&band=material` — band is optional.
    """
    year = (request.query_params.get('year') or 'FY2026').upper()
    qs = IFRS17Exception.objects.filter(financial_year=year)
    band = (request.query_params.get('band') or '').strip().lower()
    if band:
        qs = qs.filter(band=band)
    order = {'material': 0, 'watch': 1, 'immaterial': 2}
    rows = sorted(qs, key=lambda e: (order.get(e.band, 9), -e.amount))
    return Response({
        'summary': XS.register_summary(year),
        'exceptions': [_serialise_exception(e) for e in rows],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewRegulatoryReturns])
def exceptions_seed(request):
    """Create the register rows from the actuary's disclosures. Idempotent."""
    year = (request.data.get('year') or 'FY2026').upper()
    if year not in K.REPORTED:
        return Response({'detail': f'No valuation for {year}.'}, status=400)
    return Response(XS.seed_register(year, user=request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def exceptions_answer(request, pk):
    """Finance records the explanation and the action."""
    try:
        e = IFRS17Exception.objects.get(pk=pk)
    except (IFRS17Exception.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'Exception not found.'}, status=404)
    try:
        XS.answer(e,
                  explanation=request.data.get('explanation') or '',
                  action=request.data.get('action') or '',
                  user=request.user,
                  ip=request.META.get('REMOTE_ADDR'))
    except DjangoValidationError as exc:
        return Response({'detail': _first_error(exc)}, status=400)
    return Response(_serialise_exception(e))


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewRegulatoryReturns])
def exceptions_review(request, pk):
    """CFO / FC / FM accepts the answer, or returns it with what is missing."""
    try:
        e = IFRS17Exception.objects.get(pk=pk)
    except (IFRS17Exception.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'Exception not found.'}, status=404)
    accept = bool(request.data.get('accept'))
    try:
        XS.review(e, accept=accept,
                  note=request.data.get('note') or '',
                  user=request.user,
                  ip=request.META.get('REMOTE_ADDR'))
    except DjangoValidationError as exc:
        return Response({'detail': _first_error(exc)}, status=400)
    return Response(_serialise_exception(e))


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def exceptions_export_xlsx(request):
    """The exception report, as a real workbook."""
    from .exceptions_export import register_workbook_bytes
    year = (request.query_params.get('year') or 'FY2026').upper()
    data = register_workbook_bytes(year)
    resp = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = (
        f'attachment; filename="IFRS17-Exception-Report-{year}.xlsx"')
    return resp
