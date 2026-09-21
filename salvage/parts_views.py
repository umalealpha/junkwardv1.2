"""salvage/parts_views.py — Veritas Parts & Savings API.

Two endpoints, both gated by the existing Veritas/ADIC permission:

  POST /api/v1/salvage/parts/upload/     multipart `file`, workbook kind
                                         auto-detected from its sheets.
  GET  /api/v1/salvage/parts/summary/    everything the dashboard renders.

Re-uploading a month REPLACES that month (delete-then-insert inside one
transaction), so the team can correct a sheet and send it again without
doubling the figures — the failure mode the payroll runs kept hitting.

No GL posting, no payment, no customer data. These are management figures
lifted out of a spreadsheet.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from rest_framework import status as drf_status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .parts_models import (
    AssessmentSaving, EntrySource, PartsContractPricing, PartsHistory, PartsSpend,
    PartsUpload,
)
from .parts_parser import (
    detect_workbook_kind, fiscal_year_label, parse_assessment_savings, parse_parts_summary,
)
from .permissions import IsSalvageUser

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _money(value) -> str:
    return f'{Decimal(value or 0):.2f}'


def _name_key(name: str) -> str:
    """Group key for a supplier or repairer.

    The team types the same name several ways — 'Specialised Panel Beaters'
    and 'SPECIALISED PANEL BEATERS' are one shop, and left ungrouped they
    split the savings league into two half-sized entries. Grouping is
    case- and whitespace-insensitive; the name shown is the spelling that
    appears on the most jobs, never a machine-cased rewrite.
    """
    return ' '.join((name or '').split()).upper()


def _merge_by_name(rows: list[dict], name_field: str, weight_field: str,
                   sum_fields: tuple[str, ...]) -> list[dict]:
    """Fold rows whose names differ only by case or spacing into one.

    The name kept is the spelling with the largest `weight_field` among the
    variants — never a machine-cased rewrite of the team's own text.
    """
    merged: dict[str, dict] = {}
    for row in rows:
        key = _name_key(row[name_field]) or 'NOT STATED'
        target = merged.get(key)
        if target is None:
            merged[key] = dict(row) | {'_best': row[weight_field]}
            continue
        if row[weight_field] > target['_best']:
            target[name_field] = row[name_field]
            target['_best'] = row[weight_field]
        for field in sum_fields:
            target[field] = target[field] + row[field]
    for row in merged.values():
        row.pop('_best', None)
    return list(merged.values())


class PartsUploadView(APIView):
    """Accept one of the two monthly workbooks and store its rows."""

    permission_classes = [IsAuthenticated, IsSalvageUser]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': 'No file supplied. Attach the workbook as `file`.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if uploaded.size > MAX_UPLOAD_BYTES:
            return Response({'detail': 'That file is larger than 15 MB.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        data = uploaded.read()
        try:
            kind = detect_workbook_kind(data)
        except Exception as exc:                      # noqa: BLE001
            return Response({'detail': str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST)

        try:
            if kind == PartsUpload.Kind.ASSESSMENT:
                return Response(self._store_assessment(request, uploaded.name, data),
                                status=drf_status.HTTP_201_CREATED)
            return Response(self._store_parts(request, uploaded.name, data),
                            status=drf_status.HTTP_201_CREATED)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=drf_status.HTTP_400_BAD_REQUEST)
        except Exception as exc:                      # noqa: BLE001
            log.exception('parts upload failed: %s', exc)
            return Response({'detail': f'Could not read that workbook: {exc}'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

    # -- assessment savings ------------------------------------------------
    @transaction.atomic
    def _store_assessment(self, request, file_name: str, data: bytes) -> dict:
        parsed = parse_assessment_savings(data)
        sheets = [s for s in parsed['sheets'] if s['period']]
        if not sheets:
            raise ValueError('No dated assessment rows found in that workbook.')

        periods = sorted({s['period'] for s in sheets})
        # Replace, never add to, the months this file covers.
        AssessmentSaving.objects.filter(period__in=periods,
                                        source=EntrySource.UPLOAD).delete()

        upload = PartsUpload.objects.create(
            kind=PartsUpload.Kind.ASSESSMENT,
            file_name=file_name,
            uploaded_by=request.user if request.user.is_authenticated else None,
            periods=[p.isoformat() for p in periods],
            reconciliation=[
                {
                    'month':         s['period'].isoformat(),
                    'block':         'Assessment savings (sheet total)',
                    'stated':        _money((s['stated'] or {}).get('total')) if s['stated'] else None,
                    'computed':      _money(s['computed']['total']),
                    'variance_rows': s['variance_rows'],
                }
                for s in sheets
            ],
        )

        rows = [
            AssessmentSaving(
                upload=upload,
                source=EntrySource.UPLOAD,
                entered_by=upload.uploaded_by,
                period=sheet['period'],
                assessment_id=row['assessment_id'],
                reg_no=row['reg_no'][:32],
                vehicle=row['vehicle'][:120],
                repairer=row['repairer'][:120],
                req_auth_date=row['req_auth_date'],
                quote_parts=row['quote_parts'],   quote_labour=row['quote_labour'],
                quote_paint=row['quote_paint'],   quote_total=row['quote_total'],
                report_parts=row['report_parts'], report_labour=row['report_labour'],
                report_paint=row['report_paint'], report_total=row['report_total'],
                saving_parts=row['saving_parts'], saving_labour=row['saving_labour'],
                saving_paint=row['saving_paint'], saving_total=row['saving_total'],
                file_saving_total=row['file_saving_total'],
                savings_variance=row['savings_variance'],
            )
            for sheet in sheets for row in sheet['rows']
        ]
        AssessmentSaving.objects.bulk_create(rows, batch_size=500)
        upload.rows_created = len(rows)
        upload.save(update_fields=['rows_created', 'updated_at'])

        return {
            'kind':           upload.kind,
            'file_name':      file_name,
            'months':         [p.isoformat() for p in periods],
            'rows_created':   len(rows),
            'reconciliation': upload.reconciliation,
            'message': (
                f'Read {len(rows)} assessed jobs across '
                f'{len(periods)} month(s). Those months were replaced, not added to.'
            ),
        }

    # -- parts purchases ---------------------------------------------------
    @transaction.atomic
    def _store_parts(self, request, file_name: str, data: bytes) -> dict:
        parsed = parse_parts_summary(data)
        spend, contract = parsed['spend'], parsed['contract_pricing']
        if not spend and not contract:
            raise ValueError('No supplier or contract-pricing figures found in that workbook.')

        months = sorted({r['month'] for r in spend} | {r['month'] for r in contract})
        PartsSpend.objects.filter(month__in=months, source=EntrySource.UPLOAD).delete()
        PartsContractPricing.objects.filter(month__in=months,
                                            source=EntrySource.UPLOAD).delete()

        upload = PartsUpload.objects.create(
            kind=PartsUpload.Kind.PARTS,
            file_name=file_name,
            uploaded_by=request.user if request.user.is_authenticated else None,
            periods=[m.isoformat() for m in months],
            reconciliation=[
                {
                    'month':      line['month'],
                    'block':      line['block'],
                    'stated':     _money(line['stated']),
                    'computed':   _money(line['computed']),
                    'difference': _money(line['difference']),
                }
                for line in parsed['recon']
            ],
        )

        PartsSpend.objects.bulk_create([
            PartsSpend(upload=upload, source=EntrySource.UPLOAD,
                       entered_by=upload.uploaded_by,
                       category=r['category'], supplier=r['supplier'][:120],
                       month=r['month'], amount=r['amount'])
            for r in spend
        ], batch_size=500)
        PartsContractPricing.objects.bulk_create([
            PartsContractPricing(upload=upload, source=EntrySource.UPLOAD,
                                 entered_by=upload.uploaded_by,
                                 month=r['month'], amount=r['amount'])
            for r in contract
        ], batch_size=500)

        # History is a whole-workbook restatement — replace it outright.
        if parsed['history']:
            PartsHistory.objects.all().delete()
            PartsHistory.objects.bulk_create([
                PartsHistory(upload=upload, fy_label=r['fy_label'],
                             month_number=r['month_number'], amount=r['amount'])
                for r in parsed['history']
            ], batch_size=500)

        upload.rows_created = len(spend) + len(contract)
        upload.save(update_fields=['rows_created', 'updated_at'])

        return {
            'kind':           upload.kind,
            'file_name':      file_name,
            'months':         [m.isoformat() for m in months],
            'rows_created':   upload.rows_created,
            'reconciliation': upload.reconciliation,
            'message': (
                f'Read {len(spend)} supplier lines and {len(contract)} contract-pricing '
                f'month(s). Those months were replaced, not added to.'
            ),
        }


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSalvageUser])
def parts_summary(request):
    """Everything the Veritas Parts & Savings dashboard renders."""
    months_back = int(request.query_params.get('months', 12) or 12)

    spend_by_month: dict[str, dict[str, str]] = defaultdict(dict)
    for row in (PartsSpend.objects
                .values('month', 'category')
                .annotate(total=Sum('amount'))
                .order_by('month')):
        spend_by_month[row['month'].isoformat()][row['category']] = _money(row['total'])

    contract = {r['month'].isoformat(): _money(r['total'])
                for r in (PartsContractPricing.objects
                          .values('month').annotate(total=Sum('amount')).order_by('month'))}

    savings_by_month = [
        {
            'month':         row['period'].isoformat(),
            'jobs':          row['jobs'],
            'quote_total':   _money(row['quote']),
            'report_total':  _money(row['report']),
            'saving_parts':  _money(row['parts']),
            'saving_labour': _money(row['labour']),
            'saving_paint':  _money(row['paint']),
            'saving_total':  _money(row['total']),
        }
        for row in (AssessmentSaving.objects
                    .values('period')
                    .annotate(jobs=Count('id'), quote=Sum('quote_total'),
                              report=Sum('report_total'), parts=Sum('saving_parts'),
                              labour=Sum('saving_labour'), paint=Sum('saving_paint'),
                              total=Sum('saving_total'))
                    .order_by('period'))
    ]

    months = sorted(set(spend_by_month) | set(contract) | {r['month'] for r in savings_by_month})
    months = months[-months_back:]

    timeline = []
    for month in months:
        blocks = spend_by_month.get(month, {})
        dealership  = Decimal(blocks.get('DEALERSHIP',  '0'))
        aftermarket = Decimal(blocks.get('AFTERMARKET', '0'))
        windscreen  = Decimal(blocks.get('WINDSCREEN',  '0'))
        saving = next((Decimal(r['saving_total']) for r in savings_by_month
                       if r['month'] == month), Decimal('0'))
        timeline.append({
            'month':            month,
            'fy':               fiscal_year_label(date.fromisoformat(month)),
            'dealership':       _money(dealership),
            'aftermarket':      _money(aftermarket),
            'windscreen':       _money(windscreen),
            'parts_total':      _money(dealership + aftermarket + windscreen),
            'contract_pricing': contract.get(month, '0.00'),
            'assessment_saving': _money(saving),
        })

    supplier_rows = _merge_by_name(
        [{'supplier': r['supplier'], 'category': r['category'],
          'amount': Decimal(r['total'] or 0)}
         for r in (PartsSpend.objects
                   .filter(month__in=[date.fromisoformat(m) for m in months])
                   .values('supplier', 'category')
                   .annotate(total=Sum('amount')))],
        name_field='supplier', weight_field='amount', sum_fields=('amount',),
    )
    supplier_rows.sort(key=lambda r: r['amount'], reverse=True)
    top_suppliers = [
        {'supplier': r['supplier'], 'category': r['category'], 'amount': _money(r['amount'])}
        for r in supplier_rows[:15]
    ]

    repairer_rows = _merge_by_name(
        [{'repairer': r['repairer'] or 'Not stated', 'jobs': r['jobs'],
          'quoted': Decimal(r['quote'] or 0), 'saving': Decimal(r['total'] or 0)}
         for r in (AssessmentSaving.objects
                   .filter(period__in=[date.fromisoformat(m) for m in months])
                   .values('repairer')
                   .annotate(jobs=Count('id'), quote=Sum('quote_total'),
                             total=Sum('saving_total')))],
        name_field='repairer', weight_field='jobs', sum_fields=('jobs', 'quoted', 'saving'),
    )
    repairer_rows.sort(key=lambda r: r['saving'], reverse=True)
    top_repairers = [
        {
            'repairer':   r['repairer'],
            'jobs':       r['jobs'],
            'quoted':     _money(r['quoted']),
            'saving':     _money(r['saving']),
            'saving_pct': (f"{(r['saving'] / r['quoted'] * 100):.1f}" if r['quoted'] else '0.0'),
        }
        for r in repairer_rows[:15]
    ]

    history: dict[str, dict[int, str]] = defaultdict(dict)
    for row in PartsHistory.objects.all():
        history[row.fy_label][row.month_number] = _money(row.amount)

    variance_rows = [
        {
            'assessment_id': r.assessment_id,
            'month':         r.period.isoformat(),
            'repairer':      r.repairer,
            'file_total':    _money(r.file_saving_total),
            'computed':      _money(r.saving_total),
            'difference':    _money(r.savings_variance),
        }
        for r in AssessmentSaving.objects
        .exclude(savings_variance=Decimal('0.00'))
        .order_by('-period')[:50]
    ]

    uploads = [
        {
            'kind':           u.kind,
            'kind_display':   u.get_kind_display(),
            'file_name':      u.file_name,
            'uploaded_at':    u.created_at.isoformat(),
            'uploaded_by':    (u.uploaded_by.get_full_name() or u.uploaded_by.username)
                              if u.uploaded_by else 'System',
            'months':         u.periods,
            'rows_created':   u.rows_created,
            'reconciliation': u.reconciliation,
        }
        for u in PartsUpload.objects.select_related('uploaded_by').order_by('-created_at')[:12]
    ]

    # The newest month on record can be a contract-pricing-only stub (the
    # September column was already filled in when the August file was sent),
    # which would head the page with two zeros. Lead with the last month that
    # carries figures; the stub still shows in the timeline.
    latest = next(
        (t for t in reversed(timeline)
         if Decimal(t['parts_total']) or Decimal(t['assessment_saving'])),
        timeline[-1] if timeline else None,
    )
    totals = {
        'parts_spend':       _money(sum(Decimal(t['parts_total']) for t in timeline)),
        'contract_pricing':  _money(sum(Decimal(t['contract_pricing']) for t in timeline)),
        'assessment_saving': _money(sum(Decimal(t['assessment_saving']) for t in timeline)),
        'jobs':              sum(r['jobs'] for r in savings_by_month if r['month'] in months),
        'months':            len(timeline),
    }

    return Response({
        'totals':          totals,
        'latest_month':    latest,
        'timeline':        timeline,
        'savings_by_month': [r for r in savings_by_month if r['month'] in months],
        'top_suppliers':   top_suppliers,
        'top_repairers':   top_repairers,
        'fy_history':      {fy: [history[fy].get(m, '0.00') for m in
                                 list(range(7, 13)) + list(range(1, 7))]
                            for fy in sorted(history)},
        'variance_rows':   variance_rows,
        'uploads':         uploads,
    })
