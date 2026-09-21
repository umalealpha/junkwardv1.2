"""genric/api_views.py — the endpoints behind the one button.

    POST /api/v1/genric/generate/        press the button
    GET  /api/v1/genric/runs/            what has been generated before
    GET  /api/v1/genric/runs/<id>/       one run's manifest
    GET  /api/v1/genric/config/          what the pack still needs answered

Reads only. There is no endpoint here that posts a journal, changes a GL
mapping or cancels a policy, and there must never be one: the pack recommends.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewRegulatoryReturns

from . import constants as K
from . import policies as P
from .config import (
    REMIT_TO_QUESTION,
    REMIT_TO_SETTING,
    UNPAID_DAYS_KEY,
    UNPAID_DAYS_QUESTION,
    is_remit_to_configured,
    is_unpaid_days_configured,
    unpaid_days_before_cancellation,
)
from .exports import invoice_pdf, pack_workbook
from .models import GenricPackRun
from .services import PackResult, build_pack, generate_pack

#: CFO directive 2026-08-17, applied to GENRIC on 13 Sep 2026: the pack is a
#: regulatory submission, so it sits behind the SAME gate as the NBFIRA returns
#: — management, the Financial Controller and the Finance Manager only. That is
#: deliberately tighter than CanViewFinancials, which also lets in accountants,
#: bookkeepers, analysts and read-only executives.
_PERMS = [IsAuthenticated, CanViewRegulatoryReturns]


def _policies_from_request(request):
    """Current + prior policy rows, from whichever source the caller gave.

    Upload is the primary path — it is what Finance has in hand and what the
    build prompt lists as an input. ``graphite_ro`` is the alternate for when
    the book can be read directly.
    """
    cur_file = request.FILES.get('policy_export')
    pri_file = request.FILES.get('prior_policy_export')
    if cur_file:
        return (P.load_export(cur_file.read()),
                P.load_export(pri_file.read()) if pri_file else [],
                'graphite_export')
    if str(request.data.get('use_graphite_ro', '')).lower() in ('1', 'true', 'yes'):
        return P.load_from_graphite_ro(), [], 'graphite_ro'
    return [], [], ''


def _pay_window_block():
    """The pay window, as an ANSWER rather than just an absence.

    Dropping the question off ``open_questions`` is not the same as telling
    anyone what the answer is. Whoever opens this page has to be able to see
    WHICH number the cancellations report is running on, and where to go and
    change it, without reading the code or the admin. Returns None only if the
    row has been blanked, in which case it is back on the open-questions list.
    """
    if not is_unpaid_days_configured():
        return None
    days = unpaid_days_before_cancellation()
    return {
        'setting': UNPAID_DAYS_KEY,
        'days': days,
        'plain': f'Policies unpaid for more than {days} days',
    }


@api_view(['GET'])
@permission_classes(_PERMS)
def genric_config(request):
    """What the pack still needs, and the settings it is running on."""
    return Response({
        'entity': f'{K.ENTITY_NAME} ({K.ENTITY_BOOK})',
        'fnb_collection_account': K.FNB_COLLECTION_ACCOUNT,
        'treaty': {
            'ceding_commission': str(K.CEDING_COMMISSION_RATE),
            'ceding_commission_note':
                'The older MOU said 10% — the signed treaty wins.',
            'quota_share_ceded': str(K.QUOTA_SHARE_CEDED),
            'retention': str(K.RETENTION),
            'sa_vat_rate': str(K.SA_VAT_RATE),
            'reinsurance_vat': 'zero-rated (cross-border)',
            'grace_months': K.CANCELLATION_GRACE_MONTHS,
            'policy_age_flag_months': K.POLICY_AGE_FLAG_MONTHS,
        },
        'payment_direction': K.PAYMENT_DIRECTION,
        'pay_window': _pay_window_block(),
        'open_questions': (
            ([] if is_unpaid_days_configured() else [{
                'for': 'CFO',
                'blocks': 'Cancellations report',
                'question': UNPAID_DAYS_QUESTION,
                'setting': UNPAID_DAYS_KEY,
            }])
            + ([] if is_remit_to_configured() else [{
                'for': 'CFO',
                'blocks': 'Invoice — where to pay',
                'question': REMIT_TO_QUESTION,
                'setting': REMIT_TO_SETTING,
            }])
        ),
    })


@api_view(['POST'])
@permission_classes(_PERMS)
def genric_generate(request):
    """ONE button: Generate GENRIC Pack."""
    try:
        year = int(request.data.get('year'))
        month = int(request.data.get('month'))
    except (TypeError, ValueError):
        return Response({'error': 'year and month are required, as whole numbers.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not 1 <= month <= 12:
        return Response({'error': f'month must be 1-12, got {month}.'},
                        status=status.HTTP_400_BAD_REQUEST)

    charges_raw = request.data.get('collection_charges') or '0'
    try:
        charges = Decimal(str(charges_raw))
    except (InvalidOperation, ValueError):
        return Response({'error': f'collection_charges {charges_raw!r} is not a number.'},
                        status=status.HTTP_400_BAD_REQUEST)

    current, prior, source = _policies_from_request(request)

    result = generate_pack(
        year, month, user=request.user,
        current_policies=current, prior_policies=prior, policy_source=source,
        collection_charges=charges,
        explicit_invoice_number=request.data.get('invoice_number') or None,
    )

    return Response({
        'run_id': str(result.run.id),
        'period': result.context.period_label,
        'status': result.run.status,
        'manifest': result.run.manifest,
        'blocked': result.run.blocked_notes,
        'reports': [r.as_dict() for r in result.reports],
    })


@api_view(['GET'])
@permission_classes(_PERMS)
def genric_runs(request):
    rows = GenricPackRun.objects.all()[:100]
    return Response({'results': [{
        'id': str(r.id),
        'period': r.period_label,
        'status': r.status,
        'generated_at': r.generated_at,
        'generated_by': getattr(r.generated_by, 'username', None),
        'confirmed_gwp_incl_vat': str(r.confirmed_gwp_incl_vat),
        'net_reinsurance_premium_due': str(r.net_reinsurance_premium_due),
        'invoice_number': r.invoice_number,
        'blocked_count': len(r.blocked_notes or []),
    } for r in rows]})


@api_view(['GET'])
@permission_classes(_PERMS)
def genric_run_detail(request, pk):
    try:
        run = GenricPackRun.objects.get(pk=pk)
    except GenricPackRun.DoesNotExist:
        return Response({'error': 'Pack run not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    return Response({
        'id': str(run.id), 'period': run.period_label, 'status': run.status,
        'generated_at': run.generated_at, 'manifest': run.manifest,
        'blocked': run.blocked_notes, 'invoice_number': run.invoice_number,
    })


def _rebuild(run: GenricPackRun, request):
    """Re-RENDER the stored run, to produce a file. NOTHING is written.

    A download is not a new pack. Calling the generator here wrote a second
    GenricPackRun and burned the next invoice number, so pressing "Export Excel"
    then "Invoice PDF" on one July pack left three rows in /runs/ and a PDF
    numbered 027 inside a file named 025. The figures that identify the pack —
    its invoice number and the collection charges Finance keyed in — come off
    the STORED run, so the same run always renders the same document.

    The policy book is not stored on the run (it is an attached export), so an
    export needs the export again. Say that plainly rather than silently
    producing a pack with an empty policy book.
    """
    current, prior, source = _policies_from_request(request)
    ctx, built = build_pack(
        run.period_year, run.period_month,
        current_policies=current, prior_policies=prior,
        policy_source=source or run.policy_source,
        collection_charges=run.collection_charges or Decimal('0.00'),
        explicit_invoice_number=run.invoice_number or None,
    )
    # The STORED run, never a new one. build_pack() writes nothing.
    return PackResult(run=run, context=ctx, reports=built)


@api_view(['POST'])
@permission_classes(_PERMS)
def genric_export_xlsx(request, pk):
    try:
        run = GenricPackRun.objects.get(pk=pk)
    except GenricPackRun.DoesNotExist:
        return Response({'error': 'Pack run not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    result = _rebuild(run, request)
    buf = pack_workbook(result)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = (
        f'attachment; filename="GENRIC-Pack-{run.period_year}-'
        f'{run.period_month:02d}.xlsx"')
    return resp


@api_view(['POST'])
@permission_classes(_PERMS)
def genric_export_invoice_pdf(request, pk):
    try:
        run = GenricPackRun.objects.get(pk=pk)
    except GenricPackRun.DoesNotExist:
        return Response({'error': 'Pack run not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    result = _rebuild(run, request)
    resp = HttpResponse(invoice_pdf(result), content_type='application/pdf')
    resp['Content-Disposition'] = (
        f'attachment; filename="{run.invoice_number or "GENRIC-invoice"}.pdf"')
    return resp
