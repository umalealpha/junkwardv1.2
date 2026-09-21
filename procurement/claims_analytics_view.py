"""
procurement/claims_analytics_view.py — Claims-PO analytics endpoint.

GET /api/v1/reports/claims-po-analytics/   (registered in alpha_finance/api_router.py)

Read-only, company-scoped analytics over PurchaseOrder(department='claims')
plus ClaimsAssessment turnaround. Metrics:

  1. total spend — this month + last-12-months monthly trend (by issue_date)
  2. top 10 suppliers by total_amount
  3. excess deducted — the excess is the NEGATIVE (unit_price < 0), no-VAT
     line on a claims repairer PO (see claims_api.py); totals reported positive
  4. turnaround — avg days from ClaimsAssessment.created_at to the earliest
     PO it generated (status POS_CREATED, po ids recorded in po_results)
  5. repairer vs parts split — a PO with a negative line is a "repairer" PO,
     otherwise "parts"
  6. sent status — POs emailed vs not, via PurchaseOrder.last_emailed_at.
     That field is being added in a parallel change and MAY NOT exist on the
     model or in the DB yet — guarded so a missing field/column never 500s
     (reported as sent_known=false instead).

Company scope mirrors the other procurement report views (report_views.py):
accepts ?company=<uuid or Company.code>. No writes, no migrations.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .claims_models import ClaimsAssessment
from .models import PurchaseOrder, PurchaseOrderLine
from .report_views import _company_id
from django.utils import timezone


# Statuses that never count as spend.
_EXCLUDED_STATUSES = (
    PurchaseOrder.Status.CANCELLED,
    PurchaseOrder.Status.REJECTED,
    PurchaseOrder.Status.EXPIRED,
)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    """First day of the month n months after d (d must be a month start)."""
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, 1)


def _f(value) -> float:
    """Decimal-or-None -> float (0.0 for None). Keeps the JSON shape plain."""
    return float(value) if value is not None else 0.0


class ClaimsPOAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = timezone.localdate()
        company_id = _company_id(request)

        pos = (PurchaseOrder.objects
               .filter(department=PurchaseOrder.Department.CLAIMS)
               .exclude(status__in=_EXCLUDED_STATUSES))
        assessments = ClaimsAssessment.objects.all()
        if company_id:
            pos = pos.filter(company_id=company_id)
            assessments = assessments.filter(company_id=company_id)

        # ---- 1. total spend + monthly trend (last 12 months incl. current) --
        window_start = _add_months(_month_start(today), -11)
        by_month = {
            row['m']: row
            for row in (pos.filter(issue_date__gte=window_start)
                        .annotate(m=TruncMonth('issue_date'))
                        .values('m')
                        .annotate(spend=Sum('total_amount'), count=Count('id')))
        }
        monthly = []
        for i in range(12):
            m = _add_months(window_start, i)
            row = by_month.get(m, {})
            monthly.append({
                'month': m.strftime('%Y-%m'),
                'label': m.strftime('%b %y'),
                'spend': _f(row.get('spend')),
                'count': row.get('count', 0),
            })

        overall = pos.aggregate(total=Sum('total_amount'), count=Count('id'))
        spend_this_month = _f(
            pos.filter(issue_date__gte=_month_start(today))
               .aggregate(s=Sum('total_amount'))['s']
        )

        # ---- 2. top 10 suppliers by total_amount ----------------------------
        top_suppliers = [
            {'supplier': row['supplier__name'] or '(unnamed)',
             'total': _f(row['total']),
             'count': row['count']}
            for row in (pos.values('supplier__name')
                        .annotate(total=Sum('total_amount'), count=Count('id'))
                        .order_by('-total')[:10])
        ]

        # ---- 3. excess deducted (negative unit_price lines) -----------------
        excess_lines = PurchaseOrderLine.objects.filter(
            purchase_order__in=pos, unit_price__lt=0,
        )
        excess_agg = excess_lines.aggregate(
            total=Sum('line_total'),
            po_count=Count('purchase_order_id', distinct=True),
        )
        excess_po_count = excess_agg['po_count'] or 0
        # Excess lines are negative — report positive magnitudes.
        excess_total = abs(_f(excess_agg['total']))
        excess_avg = (excess_total / excess_po_count) if excess_po_count else 0.0

        # ---- 5. repairer vs parts split (has-negative-line heuristic) -------
        excess_po_ids = set(excess_lines.values_list('purchase_order_id', flat=True))
        split_agg = pos.aggregate(
            repairer_count=Count('id', filter=Q(id__in=excess_po_ids)),
            repairer_value=Sum('total_amount', filter=Q(id__in=excess_po_ids)),
            parts_count=Count('id', filter=~Q(id__in=excess_po_ids)),
            parts_value=Sum('total_amount', filter=~Q(id__in=excess_po_ids)),
        )
        split = {
            'repairer': {'count': split_agg['repairer_count'] or 0,
                         'value': _f(split_agg['repairer_value'])},
            'parts':    {'count': split_agg['parts_count'] or 0,
                         'value': _f(split_agg['parts_value'])},
        }

        # ---- 4. turnaround: assessment.created_at -> earliest generated PO --
        # po_results rows carry the created PO ids (claims_api.create_pos).
        assessment_pos = []   # (assessment_created_at, [po_id, ...])
        all_po_ids = set()
        for created_at, po_results in (
            assessments.filter(status=ClaimsAssessment.Status.POS_CREATED)
                       .values_list('created_at', 'po_results')
        ):
            ids = [r.get('id') for r in (po_results or [])
                   if isinstance(r, dict) and r.get('id')]
            if ids:
                assessment_pos.append((created_at, ids))
                all_po_ids.update(ids)

        po_created = dict(
            PurchaseOrder.objects.filter(id__in=all_po_ids)
                                 .values_list('id', 'created_at')
        ) if all_po_ids else {}
        po_created = {str(k): v for k, v in po_created.items()}

        diffs = []
        for a_created, ids in assessment_pos:
            times = [po_created[i] for i in ids if i in po_created]
            if not times or a_created is None:
                continue
            # A PO cannot logically precede its own assessment, so a negative
            # delta is clock skew / same-instant creation, not missing data —
            # clamp to 0 (count it as a same-day, 0-turnaround sample) rather
            # than discard it, which would silently drop same-day claims.
            delta = (min(times) - a_created).total_seconds() / 86400.0
            diffs.append(max(0.0, delta))
        avg_turnaround_days = round(sum(diffs) / len(diffs), 1) if diffs else None

        # ---- 6. sent status — guarded: last_emailed_at may not exist yet ----
        # The field is being added by a parallel change; the model attribute
        # and/or the DB column may be missing right now. Missing => unknown/0,
        # never a 500. Runs LAST + in its own atomic block so a DB error
        # can't poison the earlier queries.
        # Only POs THIS module generates carry the emailed stamp — legacy
        # imported claims POs (P0xxxx) predate the email button and would make
        # "0 / 4713 emailed" misleading (Manus audit 2026-07-08). Scope the
        # sent metric to engine-generated POs (po_number 'PO-CLM-...').
        sent = unsent = 0
        sent_known = False
        if any(f.name == 'last_emailed_at'
               for f in PurchaseOrder._meta.get_fields()):
            try:
                with transaction.atomic():
                    engine_pos = pos.filter(po_number__startswith='PO-CLM-')
                    sent = engine_pos.filter(last_emailed_at__isnull=False).count()
                    unsent = engine_pos.filter(last_emailed_at__isnull=True).count()
                sent_known = True
            except Exception:  # noqa: BLE001 — column not migrated yet
                sent = unsent = 0
                sent_known = False

        return Response({
            'company': company_id,
            'as_of': today.isoformat(),
            'totals': {
                'po_count': overall['count'] or 0,
                'total_spend': _f(overall['total']),
                'spend_this_month': spend_this_month,
                'excess_total': excess_total,
                'excess_avg': round(excess_avg, 2),
                'excess_po_count': excess_po_count,
                'avg_turnaround_days': avg_turnaround_days,
                'turnaround_sample': len(diffs),
                'sent': sent,
                'unsent': unsent,
                'sent_known': sent_known,
            },
            'monthly': monthly,
            'top_suppliers': top_suppliers,
            'split': split,
            'notes': [
                'Spend = PurchaseOrder.total_amount on department=claims POs, '
                'excluding cancelled / rejected / expired (drafts included — '
                'claims POs are generated as drafts).',
                'Excess = negative (unit_price < 0) PO lines; shown as a '
                'positive deduction amount.',
                'Repairer vs parts: a PO carrying a negative excess line is '
                'bucketed as repairer, all other claims POs as parts.',
                'Turnaround = days from assessment upload to its generated '
                'POs (earliest), over assessments with POs created.',
            ],
        })
