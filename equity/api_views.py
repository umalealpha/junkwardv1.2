"""equity/api_views.py — editable cap-table/ESOP register + self-service My Equity.

Two audiences:
  • Finance / EXCO — full CRUD over stakeholders, holdings, grants and vesting
    (the same whitelist that already gates the read-only Capital Story).
  • Any logged-in holder — a read-only view of THEIR OWN equity only, resolved
    from their staff record, never the whole board.
"""
from __future__ import annotations

from django.db.models import Sum
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from reporting import equity_data
from . import services
from .models import EsopGrant, ShareHolding, Stakeholder, VestingTranche
from .serializers import (
    EsopGrantSerializer, ShareHoldingSerializer, StakeholderSerializer,
    VestingTrancheSerializer,
)


# ── access ────────────────────────────────────────────────────────────────────
def _can_manage(request) -> bool:
    """Reuse the Capital Story whitelist (EXCO / Finance / admin). Managing the
    register is the same trust level as seeing it."""
    from reporting.equity_views import _can_view_equity  # lazy: avoids import cycle
    return _can_view_equity(request)


class EquityManage(BasePermission):
    message = "Equity register is restricted to EXCO and Finance."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and _can_manage(request))


# ── register CRUD (Finance / EXCO) ──────────────────────────────────────────────
class StakeholderViewSet(viewsets.ModelViewSet):
    queryset = Stakeholder.objects.prefetch_related('holdings', 'grants__tranches').all()
    serializer_class = StakeholderSerializer
    permission_classes = [IsAuthenticated, EquityManage]


class ShareHoldingViewSet(viewsets.ModelViewSet):
    queryset = ShareHolding.objects.select_related('stakeholder').all()
    serializer_class = ShareHoldingSerializer
    permission_classes = [IsAuthenticated, EquityManage]


class EsopGrantViewSet(viewsets.ModelViewSet):
    queryset = EsopGrant.objects.select_related('stakeholder').prefetch_related('tranches').all()
    serializer_class = EsopGrantSerializer
    permission_classes = [IsAuthenticated, EquityManage]


class VestingTrancheViewSet(viewsets.ModelViewSet):
    queryset = VestingTranche.objects.select_related('grant__stakeholder').all()
    serializer_class = VestingTrancheSerializer
    permission_classes = [IsAuthenticated, EquityManage]


# ── vesting schedule helper (preview, then apply) ───────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated, EquityManage])
def grant_generate_schedule(request, pk):
    """Preview a standard 4-year / 1-year-cliff schedule for a grant. Does NOT
    save — Finance reviews it against the Letter of Grant, then POSTs to apply."""
    try:
        grant = EsopGrant.objects.get(pk=pk)
    except EsopGrant.DoesNotExist:
        return Response({'detail': 'Grant not found.'}, status=404)
    tranches = services.generate_standard_tranches(grant)
    if not tranches:
        return Response({'detail': 'Set a grant date and units first.', 'tranches': []}, status=400)
    return Response({'tranches': tranches})


@api_view(['POST'])
@permission_classes([IsAuthenticated, EquityManage])
def grant_apply_schedule(request, pk):
    """Replace a grant's vesting tranches with the supplied list.
    Body: {tranches: [{vest_date, units, note?}, ...]}."""
    try:
        grant = EsopGrant.objects.get(pk=pk)
    except EsopGrant.DoesNotExist:
        return Response({'detail': 'Grant not found.'}, status=404)
    rows = (request.data or {}).get('tranches') or []
    if not isinstance(rows, list):
        return Response({'detail': 'tranches must be a list.'}, status=400)
    grant.tranches.all().delete()
    created = [
        VestingTranche(grant=grant, vest_date=r['vest_date'],
                       units=int(r.get('units') or 0), note=(r.get('note') or ''))
        for r in rows if r.get('vest_date')
    ]
    VestingTranche.objects.bulk_create(created)
    return Response({'saved': len(created)}, status=201)


# ── the Capital Story payload, now DB-backed ────────────────────────────────────
def build_capital_story() -> dict:
    """The full Capital Story, with the cap table and ESOP grants read from the
    live register and the plan-level / historical reference parts kept from
    reporting.equity_data. Falls back to the constants only when the register is
    empty (i.e. before the seed migration has run)."""
    payload = equity_data.capital_story()

    stakeholders = list(
        Stakeholder.objects.prefetch_related('holdings', 'grants__tranches').all()
    )
    if not stakeholders:
        payload['security_holder_count'] = len(payload.get('cap_table', [])) + payload.get('esop', {}).get('option_holders', 0)
        payload['register_source'] = 'seed-constants'
        return payload

    # Cap table from DB holdings, aggregated per stakeholder.
    rows = []
    for s in stakeholders:
        holdings = list(s.holdings.all())
        if not holdings:
            continue
        shares = sum(h.shares for h in holdings)
        usd = sum(float(h.usd_invested) for h in holdings)
        klass = '+'.join(sorted({h.klass for h in holdings}))
        rows.append({'holder': s.name, 'shares': shares, 'usd': usd,
                     'klass': klass, 'note': s.note, '_pct': 0.0})
    total_shares = sum(r['shares'] for r in rows) or 1
    for r in rows:
        r['pct'] = round(r['shares'] / total_shares * 100, 2)
        r.pop('_pct', None)
    rows.sort(key=lambda r: r['shares'], reverse=True)
    payload['cap_table'] = rows
    payload['cap_table_total'] = {
        'shares': sum(r['shares'] for r in rows),
        'pct': round(sum(r['pct'] for r in rows), 2),
        'usd': round(sum(r['usd'] for r in rows), 2),
    }

    # ESOP grants from DB.
    grants = []
    price = services.current_share_price_usd()
    holder_ids = set()
    for s in stakeholders:
        for g in s.grants.all():
            if g.status != 'active':
                continue
            holder_ids.add(s.id)
            vs = services.vesting_summary(g)
            grants.append({
                'grantee': s.name,
                'units': g.units,
                'pct_pool': float(g.pct_pool),
                'pct_fd': float(g.pct_fd),
                'status': g.status,
                'worth_usd': round(g.units * price, 2),
                'vesting': vs,
            })
    grants.sort(key=lambda g: g['units'], reverse=True)
    esop = dict(payload.get('esop', {}))
    granted = sum(g['units'] for g in grants)
    esop['grants'] = grants
    esop['option_holders'] = len(holder_ids)
    esop['granted'] = granted
    pool = esop.get('pool_size') or 0
    if pool:
        esop['available'] = pool - granted
        esop['pct_utilized'] = round(granted / pool * 100, 2)
    esop['granted_total'] = {
        'units': granted,
        'pct_pool': round(sum(g['pct_pool'] for g in grants), 2),
        'pct_fd': round(sum(g['pct_fd'] for g in grants), 3),
    }
    payload['esop'] = esop

    # Carta counts a security holder as a unique person/entity on the cap table —
    # shareholders and option holders, current ones only.
    current = [s for s in stakeholders if s.is_current and (s.holdings.all() or s.grants.all())]
    payload['security_holder_count'] = len(current)
    payload['current_share_price_usd'] = round(price, 4)
    payload['register_source'] = 'live-db'
    return payload


# ── My Equity (any logged-in holder) ────────────────────────────────────────────
def _stakeholder_for(user):
    """Resolve the caller to their own Stakeholder, if any — by staff record
    first, then by email. Mirrors hris._profile_for's two-link approach."""
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        s = Stakeholder.objects.filter(employee=emp).first()
        if s:
            return s
    email = (getattr(user, 'email', '') or '').strip()
    if email:
        return Stakeholder.objects.filter(email__iexact=email).first()
    return None


def _my_equity_data(user) -> dict | None:
    s = _stakeholder_for(user)
    if s is None:
        return None
    price = services.current_share_price_usd()
    holdings = [{
        'klass': h.klass, 'shares': h.shares,
        'usd_invested': float(h.usd_invested), 'note': h.note,
    } for h in s.holdings.all()]
    grants = []
    for g in s.grants.all():
        vs = services.vesting_summary(g)
        vested_units = vs['vested_units'] if vs['has_schedule'] else None
        grants.append({
            'units': g.units,
            'status': g.status,
            'grant_date': g.grant_date.isoformat() if g.grant_date else None,
            'expiry_date': g.expiry_date.isoformat() if g.expiry_date else None,
            'exercise_price_usd': float(g.exercise_price_usd) if g.exercise_price_usd is not None else None,
            'letter_ref': g.letter_ref,
            'pct_pool': float(g.pct_pool),
            'pct_fd': float(g.pct_fd),
            'worth_usd': round(g.units * price, 2),
            'vested_worth_usd': round(vested_units * price, 2) if vested_units is not None else None,
            'vesting': vs,
        })
    total_units = sum(g['units'] for g in grants)
    total_shares = sum(h['shares'] for h in holdings)
    return {
        'holder': s.name,
        'is_current': s.is_current,
        'holdings': holdings,
        'grants': grants,
        'current_share_price_usd': round(price, 4),
        'totals': {
            'option_units': total_units,
            'option_worth_usd': round(total_units * price, 2),
            'shares': total_shares,
            'share_worth_usd': round(total_shares * price, 2),
        },
        'issuer': equity_data.ISSUER,
        'price_basis': 'Indicative — pre-money valuation ÷ fully-diluted shares. Not a market price or an offer to buy.',
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_equity(request):
    """The caller's own equity only. 200 with has_equity=False when the login
    is not matched to any holder — a plain, non-error state."""
    data = _my_equity_data(request.user)
    if data is None:
        return Response({'has_equity': False,
                         'detail': 'No equity is recorded against your login. If you hold shares or options, ask Finance to link your record.'})
    data['has_equity'] = True
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_equity_statement_pdf(request):
    data = _my_equity_data(request.user)
    if data is None:
        return Response({'detail': 'No equity recorded against your login.'},
                        status=status.HTTP_404_NOT_FOUND)
    from django.http import HttpResponse
    from .pdf import generate_my_equity_pdf
    pdf = generate_my_equity_pdf(data)
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = 'inline; filename="my-equity-statement.pdf"'
    return resp
