"""FX Payment Planning — REST endpoints (/api/v1/fx-planning/...)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import (
    ForexPaymentHistory, ForexPaymentImport, PlannedForexPayment,
    RecurringForexPayee,
)
from .permissions import can_manage_fx_planning, can_view_fx_planning
from .serializers import (
    ForexPaymentHistorySerializer, ForexPaymentImportSerializer,
    PlannedForexPaymentSerializer, RecurringForexPayeeSerializer,
)

ACTIVE_STATUSES = (PlannedForexPayment.Status.PLANNED,
                   PlannedForexPayment.Status.REQUESTED)


def _deny_view():
    return Response({'detail': 'You do not have access to FX payment planning.'},
                    status=status.HTTP_403_FORBIDDEN)


def _deny_manage():
    return Response({'detail': 'Only Finance and the CFO can change FX planning.'},
                    status=status.HTTP_403_FORBIDDEN)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())   # Monday


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fx_calendar(request):
    """The forward FX payment calendar: planned foreign payments bucketed by
    week and currency, with pula estimates and a per-account funding view."""
    if not can_view_fx_planning(request.user):
        return _deny_view()

    try:
        weeks_ahead = max(1, min(26, int(request.GET.get('weeks', 12))))
    except (TypeError, ValueError):
        weeks_ahead = 12

    today = timezone.localdate()
    horizon = today + timedelta(weeks=weeks_ahead)

    qs = (PlannedForexPayment.objects
          .filter(status__in=ACTIVE_STATUSES,
                  expected_value_date__gte=today,
                  expected_value_date__lte=horizon)
          .order_by('expected_value_date', 'currency'))

    # Week skeleton (Monday-based), so empty weeks still show.
    weeks: dict[date, dict] = {}
    ws = _week_start(today)
    while ws <= horizon:
        weeks[ws] = {'week_start': ws.isoformat(),
                     'label': f'{ws:%d %b}', 'by_currency': {}, 'lines': []}
        ws += timedelta(days=7)

    ccy_totals: dict[str, dict] = {}
    acct_totals: dict[tuple, dict] = {}
    bwp_grand = Decimal('0')

    for line in qs:
        wk = _week_start(line.expected_value_date)
        bucket = weeks.get(wk)
        if bucket is None:                       # safety (line beyond skeleton)
            continue
        ccy = line.currency or '?'
        amt = line.expected_amount or Decimal('0')
        bwp = line.estimated_bwp or Decimal('0')

        wc = bucket['by_currency'].setdefault(ccy, {'amount': Decimal('0'),
                                                    'bwp': Decimal('0'), 'count': 0})
        wc['amount'] += amt
        wc['bwp'] += bwp
        wc['count'] += 1
        bucket['lines'].append(PlannedForexPaymentSerializer(line).data)

        ct = ccy_totals.setdefault(ccy, {'amount': Decimal('0'), 'bwp': Decimal('0'),
                                         'count': 0})
        ct['amount'] += amt
        ct['bwp'] += bwp
        ct['count'] += 1

        acct_key = (line.source_account or '—', ccy)
        at = acct_totals.setdefault(acct_key, {'account': line.source_account or '—',
                                               'currency': ccy, 'amount': Decimal('0'),
                                               'bwp': Decimal('0'), 'count': 0})
        at['amount'] += amt
        at['bwp'] += bwp
        at['count'] += 1
        bwp_grand += bwp

    def _dec(v):
        return str(Decimal(v).quantize(Decimal('0.01')))

    # Rate-risk buffer (FX-002): the same pula figure stressed for a weaker pula,
    # so the team funds the worst case, not just today's rate.
    pcts = services.fx_stress_pcts()

    def _stressed(bwp):
        return {str(p): _dec(services.stress_bwp(bwp, p)) for p in pcts}

    week_list = []
    for wk in sorted(weeks):
        b = weeks[wk]
        b['by_currency'] = {c: {'amount': _dec(v['amount']), 'bwp': _dec(v['bwp']),
                                'count': v['count']}
                            for c, v in b['by_currency'].items()}
        week_list.append(b)

    return Response({
        'generated_at': timezone.now().isoformat(),
        'today': today.isoformat(),
        'weeks_ahead': weeks_ahead,
        'currencies': sorted(ccy_totals),
        'stress_pcts': [str(p) for p in pcts],
        'totals': {c: {'amount': _dec(v['amount']), 'bwp': _dec(v['bwp']),
                       'bwp_stressed': _stressed(v['bwp']), 'count': v['count']}
                   for c, v in ccy_totals.items()},
        'bwp_total': _dec(bwp_grand),
        'bwp_total_stressed': _stressed(bwp_grand),
        'by_account': sorted(
            [{'account': v['account'], 'currency': v['currency'],
              'amount': _dec(v['amount']), 'bwp': _dec(v['bwp']),
              'bwp_stressed': _stressed(v['bwp']), 'count': v['count']}
             for v in acct_totals.values()],
            key=lambda r: (r['currency'], r['account'])),
        'weeks': week_list,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fx_payees(request):
    if not can_view_fx_planning(request.user):
        return _deny_view()
    qs = RecurringForexPayee.objects.all().order_by('-active', '-confidence', 'display_name')
    return Response(RecurringForexPayeeSerializer(qs, many=True).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def fx_payee_detail(request, pk):
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    try:
        payee = RecurringForexPayee.objects.get(pk=pk)
    except RecurringForexPayee.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    ser = RecurringForexPayeeSerializer(payee, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    ser.save(confirmed_by=request.user)     # a human touched it → protect from auto-relearn
    return Response(ser.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fx_history(request):
    if not can_view_fx_planning(request.user):
        return _deny_view()
    qs = ForexPaymentHistory.objects.all().order_by('-value_date')[:500]
    return Response({
        'count': ForexPaymentHistory.objects.count(),
        'rows': ForexPaymentHistorySerializer(qs, many=True).data,
        'imports': ForexPaymentImportSerializer(
            ForexPaymentImport.objects.order_by('-created_at')[:20], many=True).data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fx_backtest(request):
    """Forecast accuracy scorecard: predicted vs actual for the last N months."""
    if not can_view_fx_planning(request.user):
        return _deny_view()
    try:
        months = max(1, min(12, int(request.GET.get('months', 3))))
    except (TypeError, ValueError):
        months = 3
    return Response(services.backtest_accuracy(months_back=months,
                                               today=timezone.localdate()))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def fx_import(request):
    """Upload an FNB forex download, then rebuild the forecast + calendar."""
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)
    data = f.read()
    if not data:
        return Response({'detail': 'The uploaded file is empty.'},
                        status=status.HTTP_400_BAD_REQUEST)
    imp = services.import_forex_file(data, f.name, user=request.user)
    payees = services.detect_recurring()
    # Fill the full horizon the calendar view can show (WEEK_CHOICES tops out at
    # 26), so a "Next 26 weeks" view is never falsely empty in weeks 13–26.
    created = services.materialise_calendar(weeks_ahead=26, user=request.user)
    return Response({
        'import': ForexPaymentImportSerializer(imp).data,
        'payees_detected': payees,
        'planned_created': created,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fx_rebuild(request):
    """Re-run the recurring detector and (re)fill the forward calendar."""
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    payees = services.detect_recurring()
    # Fill the full horizon the calendar view can show (WEEK_CHOICES tops out at
    # 26), so a "Next 26 weeks" view is never falsely empty in weeks 13–26.
    created = services.materialise_calendar(weeks_ahead=26, user=request.user)
    return Response({'payees_detected': payees, 'planned_created': created})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fx_planned_create(request):
    """Add a one-off planned foreign payment by hand (driver = manual)."""
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    d = request.data
    try:
        amount = Decimal(str(d.get('expected_amount')))
        vdate = datetime.strptime(d['expected_value_date'], '%Y-%m-%d').date()
    except Exception:  # noqa: BLE001
        return Response({'detail': 'Amount and a valid value date (YYYY-MM-DD) are required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    currency = (d.get('currency') or '').upper()[:3]
    if not currency:
        return Response({'detail': 'Currency is required.'}, status=status.HTTP_400_BAD_REQUEST)
    beneficiary = (d.get('beneficiary') or '').strip()
    if not beneficiary:
        return Response({'detail': 'Beneficiary is required.'}, status=status.HTTP_400_BAD_REQUEST)
    rate, bwp, est = services.estimate_bwp(currency, amount, vdate)
    line = PlannedForexPayment.objects.create(
        beneficiary=beneficiary[:255],
        beneficiary_key=services.normalise_key(beneficiary)[:255],
        currency=currency, expected_amount=amount, expected_value_date=vdate,
        source_account=(d.get('source_account') or '')[:40],
        driver=PlannedForexPayment.Driver.MANUAL,
        estimated_rate=rate, estimated_bwp=bwp, rate_is_estimate=est,
        notes=(d.get('notes') or '')[:2000], created_by=request.user,
    )
    return Response(PlannedForexPaymentSerializer(line).data, status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def fx_planned_detail(request, pk):
    """Edit a planned line, mark it skipped, or delete a manual one."""
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    try:
        line = PlannedForexPayment.objects.get(pk=pk)
    except PlannedForexPayment.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        line.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if line.payment_request_id:
        return Response({'detail': 'This line already has a payment request and cannot be edited.'},
                        status=status.HTTP_400_BAD_REQUEST)
    ser = PlannedForexPaymentSerializer(line, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    obj = ser.save()
    # Refresh the pula estimate if amount/currency/date changed.
    rate, bwp, est = services.estimate_bwp(obj.currency, obj.expected_amount,
                                           obj.expected_value_date)
    obj.estimated_rate, obj.estimated_bwp, obj.rate_is_estimate = rate, bwp, est
    obj.save(update_fields=['estimated_rate', 'estimated_bwp', 'rate_is_estimate', 'updated_at'])
    return Response(PlannedForexPaymentSerializer(obj).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def fx_planned_raise(request, pk):
    """Turn a planned line into a real PaymentRequest (finance → CFO → FNB)."""
    if not can_manage_fx_planning(request.user):
        return _deny_manage()
    try:
        line = PlannedForexPayment.objects.get(pk=pk)
    except PlannedForexPayment.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if line.payment_request_id:
        return Response({'detail': 'A payment request already exists for this line.'},
                        status=status.HTTP_400_BAD_REQUEST)
    pr = services.raise_as_payment_request(line, request.user)
    return Response({
        'payment_request_id': str(pr.id),
        'payment_request_ref': pr.ref,
        'line': PlannedForexPaymentSerializer(line).data,
    }, status=status.HTTP_201_CREATED)
