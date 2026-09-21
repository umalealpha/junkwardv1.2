"""
salvage/external_views.py

Integration surface between omni (Django/Postgres) and the external
Motor Liquidators V3 salvage portal (Express/SQLite on port 3700).

Three endpoints, all gated by `motor-liquidators` ApiKey scope:

  POST /api/v1/salvage/external/sale-completed/
      ML posts when a sale is finalised on its portal.
      Omni either matches a pre-existing salvage.Sale or creates a
      minimal one, then auto-posts the GL JE via salvage.services.post_sale_to_gl.
      Idempotent on external_sale_ref.

  GET  /api/v1/salvage/external/vehicle-lookup/?claim=...&policy=...
      ML asks omni for vehicle details (brand / model / year / VIN /
      sum_insured) given a claim or policy reference. Tries the local
      claims.Claim table first; if not found and GRAPHITE_API_BASE +
      BIZSURE_API_BASE creds are set in /etc/alpha-finance/.env, falls
      back to those source systems.

  GET  /api/v1/salvage/external/recoveries-summary/?from=...&to=...
      Dashboard tile data: claims paid out vs salvage recovered, by
      brand / category / month. Powers omni's "Insurance Recoveries"
      tile and the executive view.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Sale-completed webhook
# ---------------------------------------------------------------------------

class MLSaleCompletedView(APIView):
    """
    POST /api/v1/salvage/external/sale-completed/

    Body:
      {
        "external_sale_ref": "INV-260520-0001",   (required, idempotency key)
        "item_code":         "SLV-10101-2026",    (required)
        "buyer_code":        "BYR-0001",
        "buyer_name":        "John Doe",
        "buyer_phone":       "+267 71234567",
        "buyer_email":       "john@example.com",
        "sale_price":        "11400.00",          (VAT-inclusive, BWP)
        "payment_method":    "cash|eft|cheque|mobile_money|card",
        "payment_ref":       "EFT-2026-05-20-XYZ",
        "sale_date":         "2026-05-20"
      }

    Returns 201 with the created JE number (or 200 + existing JE if
    external_sale_ref already processed).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from salvage.models import SalvageItem, Sale
        from salvage.services import post_sale_to_gl

        d = request.data or {}
        required = ('external_sale_ref', 'item_code', 'sale_price',
                    'payment_method', 'sale_date', 'buyer_name')
        missing = [k for k in required if not d.get(k)]
        if missing:
            return Response({'error': f'Missing fields: {missing}'},
                            status=status.HTTP_400_BAD_REQUEST)

        ext_ref = str(d['external_sale_ref'])

        # Idempotency: re-posting same external ref returns the prior result.
        existing = Sale.objects.filter(
            payment_ref=ext_ref,
        ).first()
        if existing:
            return Response({
                'status':            'already_processed',
                'sale_id':           str(existing.id),
                'journal_entry_id':  str(existing.journal_entry_id) if existing.journal_entry_id else None,
                'external_sale_ref': ext_ref,
            }, status=status.HTTP_200_OK)

        try:
            item = SalvageItem.objects.get(item_code=d['item_code'])
        except SalvageItem.DoesNotExist:
            return Response({'error': f"Unknown item_code: {d['item_code']}"},
                            status=status.HTTP_404_NOT_FOUND)

        try:
            sale_price = Decimal(str(d['sale_price']))
        except (InvalidOperation, ValueError):
            return Response({'error': f"Invalid sale_price: {d['sale_price']}"},
                            status=status.HTTP_400_BAD_REQUEST)

        sale_date = parse_date(str(d['sale_date']))
        if not sale_date:
            return Response({'error': f"Invalid sale_date: {d['sale_date']}"},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            sale = Sale.objects.create(
                item            = item,
                buyer_name      = str(d.get('buyer_name'))[:200],
                buyer_phone     = str(d.get('buyer_phone', ''))[:40],
                buyer_email     = str(d.get('buyer_email', ''))[:254],
                sale_price      = sale_price,
                payment_method  = str(d.get('payment_method')),
                payment_ref     = ext_ref,
                sale_date       = sale_date,
                sold_by         = request.user if request.user and request.user.is_authenticated else None,
                notes           = f"Auto-posted from Motor Liquidators V3 (buyer_code={d.get('buyer_code','')})",
            )
            je = post_sale_to_gl(sale, user=request.user if request.user and request.user.is_authenticated else None)

        return Response({
            'status':            'created',
            'sale_id':           str(sale.id),
            'journal_entry_id':  str(je.id) if je else None,
            'journal_entry_number': je.entry_number if je else None,
            'external_sale_ref': ext_ref,
        }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# 2. Vehicle lookup proxy
# ---------------------------------------------------------------------------

def _lookup_local_claim(claim_no: str):
    """Return a dict if a local Claim with this number resolves to a vehicle."""
    try:
        from claims.models import Claim
        c = Claim.objects.filter(claim_number=claim_no).first()
        if not c:
            return None
        return {
            'source':           'omni_claims',
            'claim_number':     c.claim_number,
            'policy_number':    getattr(c, 'policy_number', '') or '',
            'brand':            getattr(c, 'vehicle_make', '') or '',
            'model':            getattr(c, 'vehicle_model', '') or '',
            'year':             getattr(c, 'vehicle_year', None),
            'vin':              getattr(c, 'vehicle_vin', '') or getattr(c, 'chassis_number', '') or '',
            'registration':     getattr(c, 'vehicle_registration', '') or '',
            'sum_insured':      str(getattr(c, 'sum_insured', '') or ''),
        }
    except Exception:    # noqa: BLE001
        return None


def _lookup_graphite(claim_no: str | None, policy_no: str | None):
    """Call Graphite if creds are configured. Returns dict or None."""
    base  = getattr(settings, 'GRAPHITE_API_BASE', '') or ''
    token = getattr(settings, 'GRAPHITE_API_TOKEN', '') or ''
    if not base or not token:
        return None
    try:
        import requests
        params = {}
        if claim_no:  params['claim_number']  = claim_no
        if policy_no: params['policy_number'] = policy_no
        r = requests.get(
            f"{base.rstrip('/')}/v1/vehicle-lookup",
            params=params,
            headers={'Authorization': f'Bearer {token}'},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json()
            return {
                'source':        'graphite',
                'claim_number':  d.get('claim_number') or claim_no or '',
                'policy_number': d.get('policy_number') or policy_no or '',
                'brand':         d.get('brand', '') or d.get('make', ''),
                'model':         d.get('model', ''),
                'year':          d.get('year'),
                'vin':           d.get('vin', '') or d.get('chassis_number', ''),
                'registration':  d.get('registration_number', ''),
                'sum_insured':   str(d.get('sum_insured', '')),
            }
    except Exception as exc:    # noqa: BLE001
        log.warning('Graphite lookup failed: %s', exc)
    return None


def _lookup_bizsure(claim_no: str | None, policy_no: str | None):
    """Call Bizsure if creds are configured. Returns dict or None."""
    base = getattr(settings, 'BIZSURE_API_BASE', '') or ''
    key  = getattr(settings, 'BIZSURE_API_KEY', '') or ''
    if not base or not key:
        return None
    try:
        import requests
        params = {}
        if claim_no:  params['claimRef']  = claim_no
        if policy_no: params['policyRef'] = policy_no
        r = requests.get(
            f"{base.rstrip('/')}/api/vehicle",
            params=params,
            headers={'X-API-Key': key},
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json()
            return {
                'source':        'bizsure',
                'claim_number':  d.get('claimRef') or claim_no or '',
                'policy_number': d.get('policyRef') or policy_no or '',
                'brand':         d.get('make', ''),
                'model':         d.get('model', ''),
                'year':          d.get('manufactureYear'),
                'vin':           d.get('vin', '') or d.get('chassisNumber', ''),
                'registration':  d.get('regNumber', ''),
                'sum_insured':   str(d.get('sumInsured', '')),
            }
    except Exception as exc:    # noqa: BLE001
        log.warning('Bizsure lookup failed: %s', exc)
    return None


class MLVehicleLookupView(APIView):
    """
    GET /api/v1/salvage/external/vehicle-lookup/?claim=...&policy=...

    Resolution order: local omni claims.Claim → Graphite → Bizsure.
    First hit wins. Returns 404 if no source resolves.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        claim_no  = (request.query_params.get('claim')  or '').strip() or None
        policy_no = (request.query_params.get('policy') or '').strip() or None
        if not (claim_no or policy_no):
            return Response({'error': "Provide ?claim=... and/or ?policy=..."},
                            status=400)

        if claim_no:
            data = _lookup_local_claim(claim_no)
            if data:
                return Response(data)

        for fn in (_lookup_graphite, _lookup_bizsure):
            data = fn(claim_no, policy_no)
            if data:
                return Response(data)

        return Response({
            'error':  'No vehicle data found in omni / Graphite / Bizsure.',
            'claim':  claim_no,
            'policy': policy_no,
            'sources_checked': ['omni_claims', 'graphite', 'bizsure'],
        }, status=404)


# ---------------------------------------------------------------------------
# 3. Recoveries summary
# ---------------------------------------------------------------------------

class MLRecoveriesSummaryView(APIView):
    """
    GET /api/v1/salvage/external/recoveries-summary/?from=YYYY-MM-DD&to=YYYY-MM-DD&company=...

    Sum-insured vs sale-proceeds roll-up across the salvage book within
    the date window. Powers the omni dashboard "Insurance Recoveries"
    tile and the executive view in the ML portal.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum, Count, Q
        from salvage.models import SalvageItem, Sale

        today = timezone.localdate()
        from_d = parse_date(request.query_params.get('from') or '') or date(today.year, 1, 1)
        to_d   = parse_date(request.query_params.get('to')   or '') or today

        # apiFetch injects the topbar company as its UUID id; the ML portal and
        # hand-built URLs pass the code, which raised a UUID ValidationError
        # (500) on the id-only filter. Accept both, and when the value matches
        # no entity return an empty roll-up rather than every company's.
        # CFO standing rule 2026-07-28.
        from core.mixins import resolve_company
        raw = (request.query_params.get('company') or '').strip() or None
        company = resolve_company(raw) if raw else None
        unresolved = bool(raw) and company is None

        item_qs = SalvageItem.objects.all()
        if company:
            item_qs = item_qs.filter(company_id=company.id)
        elif unresolved:
            item_qs = item_qs.none()

        sale_qs = Sale.objects.filter(sale_date__gte=from_d, sale_date__lte=to_d)
        if company:
            sale_qs = sale_qs.filter(item__company_id=company.id)
        elif unresolved:
            sale_qs = sale_qs.none()

        total_asking_price = item_qs.aggregate(
            t=Sum('asking_price'),
        )['t'] or Decimal('0')
        total_recovered = sale_qs.aggregate(
            t=Sum('sale_price'),
        )['t'] or Decimal('0')
        pending_recovery = (item_qs.exclude(sales__isnull=False)
                            .aggregate(t=Sum('asking_price'))['t'] or Decimal('0'))

        recovery_rate = (
            float(total_recovered) / float(total_asking_price) * 100
            if total_asking_price else 0.0
        )

        by_brand = list(
            sale_qs.values('item__vehicle_brand__name')
            .annotate(count=Count('id'), total=Sum('sale_price'))
            .order_by('-total')[:10]
        )
        by_month = list(
            sale_qs.extra(select={'month': "to_char(sale_date,'YYYY-MM')"})
            .values('month')
            .annotate(count=Count('id'), total=Sum('sale_price'))
            .order_by('month')
        )

        return Response({
            'from_date':        from_d.isoformat(),
            'to_date':          to_d.isoformat(),
            'company_id':       str(company.id) if company else None,
            'total_sum_insured': str(total_asking_price),
            'total_asking_price': str(total_asking_price),
            'total_recovered':   str(total_recovered),
            'pending_recovery':  str(pending_recovery),
            'recovery_rate_pct': round(recovery_rate, 2),
            'item_count':        item_qs.count(),
            'sale_count':        sale_qs.count(),
            'by_brand':          by_brand,
            'by_month':          by_month,
        })
