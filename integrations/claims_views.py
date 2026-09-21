"""
integrations/claims_views.py

Claims register (Bokani 2026-06-24): every Graphite claim on ONE screen with
filters + summary breakdowns + Excel export, mirroring the RealPay collections
tab. Read-only over the integrations.GraphiteClaim mirror (filled by
`pull_graphite_claims`). No GL involvement.

GET /api/v1/claims-register/
  Filters: status, claim_type, product, handler, search (claim#/policy#/customer),
           from, to (registered date YYYY-MM-DD), page, per_page.
  export=1|xlsx -> download the filtered list as Excel.
"""
from __future__ import annotations

from io import BytesIO

from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials

from .models import GraphiteClaim
from .claims_register_reconciliation import cached_reconciliation

_CLAIM_HEADERS = ['Claim #', 'Status', 'Type', 'Product', 'Policy #',
                  'Customer', 'Handler', 'Reported Date', 'Date of Loss', 'Damage Cause',
                  'Total Reserve', 'Total Payment', 'Balance']


def _claims_qs(qp):
    qs = GraphiteClaim.objects.all()
    v = (qp.get('status') or '').strip()
    if v:
        qs = qs.filter(status__iexact=v)
    v = (qp.get('claim_type') or '').strip()
    if v:
        qs = qs.filter(claim_type__iexact=v)
    v = (qp.get('product') or '').strip()
    if v:
        qs = qs.filter(product_name__icontains=v)
    v = (qp.get('handler') or '').strip()
    if v:
        qs = qs.filter(claim_handler__icontains=v)
    v = (qp.get('search') or '').strip()
    if v:
        qs = qs.filter(Q(claim_number__icontains=v) |
                       Q(policy_number__icontains=v) |
                       Q(customer_name__icontains=v))
    df = parse_date((qp.get('from') or '')[:10]) if qp.get('from') else None
    dt = parse_date((qp.get('to') or '')[:10]) if qp.get('to') else None
    if df:
        qs = qs.filter(registered_date__gte=df)
    if dt:
        qs = qs.filter(registered_date__lte=dt)
    return qs


def _row(c):
    return {
        'id': str(c.id),
        'graphite_id': c.graphite_id,
        'claim_number': c.claim_number,
        'status': c.status,
        'claim_type': c.claim_type,
        'product_name': c.product_name,
        'policy_number': c.policy_number,
        'customer_name': c.customer_name,
        'is_company': c.is_company,
        'claim_handler': c.claim_handler,
        # The date the claim was REPORTED (Graphite's registered_claim). This
        # is what the from/to filter has always used; it was missing from the
        # register itself, so the only date on screen was the date of loss and
        # the register read as though it were built on that (Bokani Makosha,
        # 2026-09-16).
        'registered_date': c.registered_date.isoformat() if c.registered_date else None,
        'date_of_loss': c.date_of_loss.isoformat() if c.date_of_loss else None,
        'damage_cause': c.damage_cause or '',
        'total_reserve': str(c.total_reserve or 0),
        'total_payment': str(c.total_payment or 0),
        'balance': str(c.balance or 0),
    }


def _xlsx(qs):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = 'Claims'
    ws.append(_CLAIM_HEADERS)
    n = 0
    for c in qs.iterator(chunk_size=2000):
        ws.append([
            c.claim_number, c.status, c.claim_type, c.product_name,
            c.policy_number, c.customer_name, c.claim_handler,
            c.registered_date.isoformat() if c.registered_date else '',
            c.date_of_loss.isoformat() if c.date_of_loss else '',
            c.damage_cause or '',
            float(c.total_reserve or 0), float(c.total_payment or 0), float(c.balance or 0),
        ])
        n += 1
    ws.append([])
    ws.append([f'TOTAL: {n} claims'])
    for col in range(1, len(_CLAIM_HEADERS) + 1):
        ws.cell(row=1, column=col).font = Font(bold=True)
    ws.column_dimensions['A'].width = 16
    ws.column_dimensions['F'].width = 28
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="claims_register.xlsx"'
    return resp


@api_view(['GET'])
@permission_classes([CanViewFinancials])
def graphite_feed_claim_drill(request):
    """The actual claims behind a Graphite Feeds row — with client name + subject
    (CFO 2026-09-01). Finance/exec only, because this is the one claims view that
    shows client PII on the otherwise name-free Graphite Feeds page.

    Reuses the claims-register query and row exactly (`_claims_qs` / `_row`), so a
    feed row maps straight onto its filters — Claims-by-type passes
    `?claim_type=Motor`, Claims-by-group / Major-claims pass `?search=COMG` or a
    claim number. Reconciliation is SURFACED, not hidden: the response carries the
    register's real total so the UI can show it next to the feed's own count
    (the two pipelines are known to disagree — see the reconciliation audit)."""
    qs = _claims_qs(request.query_params).order_by('-total_payment')
    try:
        per = min(300, max(10, int(request.query_params.get('per_page') or 200)))
    except (TypeError, ValueError):
        per = 200
    rows = [_row(c) for c in qs[:per]]
    sums = qs.aggregate(reserve=Sum('total_reserve'), payment=Sum('total_payment'))
    last = GraphiteClaim.objects.aggregate(m=Max('updated_at'))['m']
    return Response({
        'total': qs.count(),
        'shown': len(rows),
        'results': rows,
        'total_reserve': str(sums['reserve'] or 0),
        'total_payment': str(sums['payment'] or 0),
        'last_synced': last.isoformat() if last else None,
        'source': 'Graphite V2 claims (read-only mirror, refreshed nightly)',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_claims_register(request):
    qp = request.query_params
    qs = _claims_qs(qp)

    if (qp.get('export') or '').strip().lower() in ('1', 'xlsx', 'csv', 'true'):
        return _xlsx(qs)

    total = qs.count()

    def bd(field, limit=None):
        q = qs.values(field).annotate(n=Count('id')).order_by('-n')
        return [{'key': r[field] or '(blank)', 'count': r['n']}
                for r in (q[:limit] if limit else q)]

    try:
        page = max(1, int(qp.get('page') or 1))
        per = min(200, max(10, int(qp.get('per_page') or 50)))
    except (TypeError, ValueError):
        page, per = 1, 50
    start = (page - 1) * per
    rows = [_row(c) for c in qs[start:start + per]]

    sums = qs.aggregate(reserve=Sum('total_reserve'), payment=Sum('total_payment'),
                        balance=Sum('balance'))
    last_sync = GraphiteClaim.objects.aggregate(m=Max('updated_at'))['m']
    return Response({
        'total': total,
        'page': page,
        'per_page': per,
        'total_reserve': str(sums['reserve'] or 0),
        'total_payment': str(sums['payment'] or 0),
        'total_balance': str(sums['balance'] or 0),
        'by_status': bd('status'),
        'by_type': bd('claim_type'),
        'by_product': bd('product_name', 15),
        'by_handler': bd('claim_handler', 15),
        'results': rows,
        'last_synced': last_sync.isoformat() if last_sync else None,
        'source': 'Graphite V2 claims (read-only mirror)',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def claims_register_reconciliation_view(request):
    """Omni-vs-Graphite reconciliation on the claims register (Bokani 2026-09-19).

    Compares Omni's claims mirror to live Graphite at the same moment:
    - Record counts: total, matched, each-side-only, with reserve differences
    - Reserve and payment bridges: OMNI → expected Graphite with all adjustments
    - Details: claims driving gaps (restated reserves, Graphite-only, Omni-only)

    Both sides read at one timestamp; Graphite unavailable → shows "unavailable".
    ?from=&to= are the register's own date filter (registered/created date);
    default is this month to date."""
    from .claims_register_reconciliation import default_range
    qp = request.query_params
    d_from, d_to = default_range()
    try:
        d_from = parse_date((qp.get('from') or '')[:10]) or d_from
        d_to = parse_date((qp.get('to') or '')[:10]) or d_to
    except ValueError:
        return Response({'detail': 'from/to must be YYYY-MM-DD dates.'}, status=400)
    if d_from > d_to:
        return Response({'detail': 'from must not be after to.'}, status=400)
    return Response(cached_reconciliation(d_from, d_to))
