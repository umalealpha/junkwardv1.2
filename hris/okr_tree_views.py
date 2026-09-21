"""
hris/okr_tree_views.py — OKR Alignment Tree.

Renders the OKR set for one period as a tree by `parent`: company
objective(s) at the root, department objectives underneath, individual
objectives underneath those — whatever hierarchy HR links via `parent`
when creating an objective (not enforced by the model itself).

Endpoints (whitelist-gated, same access model as the rest of HRIS):
  GET  /api/v1/hris/okr-tree/?period=<p>   → nested tree for one period
                                              (defaults to the latest period
                                              on file when `period` is
                                              omitted)
  POST /api/v1/hris/okr-tree/objective/    → create one objective, optionally
                                              linked under a parent
  GET  /api/v1/hris/okr-tree/periods/      → distinct periods on file, for
                                              the page's period selector

Company/department objectives carry profile=None (there is no employee
behind a company- or department-wide goal), so they have no company/entity
to scope by and stay visible to every whitelisted caller. Individual
objectives ARE entity-scoped via apply_company_scope, same as every other
HRIS surface (CFO 2026-06-16 HRIS-006 entity-isolation fix).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import apply_company_scope
from hris.api_views import _deny_if_not_whitelisted
from hris.models import HRISProfile, OKR

SCOPE_COMPANY = 'company'
SCOPE_DEPARTMENT = 'department'
SCOPE_INDIVIDUAL = 'individual'
VALID_SCOPES = frozenset({SCOPE_COMPANY, SCOPE_DEPARTMENT, SCOPE_INDIVIDUAL})

# OKR.score_h1 / score_h2 are recorded on a 0-5 scale (see the help_text on
# those fields in hris/models.py, and their existing use in the 9-box rollup
# in hris/talent_views.py, which keeps the weighted average on that same 0-5
# scale). This page renders a 0-100 progress bar, so the normalisation lives
# here rather than on the model.
_SCORE_SCALE_MAX = Decimal('5')


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _period_okrs(request, period):
    """All OKRs for `period`, with individual rows clamped to the caller's
    entity grant. Company/department rows have profile=None (no employee, so
    no company to scope by) and are always visible to a whitelisted caller —
    filtering them by apply_company_scope's `__in` clause would silently drop
    every one of them for an entity-restricted caller (NULL is never IN a
    list), so they are deliberately kept out of that filter."""
    base = (OKR.objects
            .filter(period=period)
            .select_related('profile', 'profile__employee'))
    non_individual = base.exclude(scope=SCOPE_INDIVIDUAL)
    individual = apply_company_scope(
        request, base.filter(scope=SCOPE_INDIVIDUAL), 'profile__employee__company_id')
    return list(non_individual) + list(individual)


def _progress_pct(okr):
    """Average of whichever half-year scores are on file, normalised from the
    0-5 OKR score scale to a 0-100 percentage. None until at least one score
    is recorded — kept distinct from 0%, which would mean 'scored, and it was
    zero'."""
    scores = [s for s in (okr.score_h1, okr.score_h2) if s is not None]
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    pct = (avg / _SCORE_SCALE_MAX) * Decimal('100')
    pct = max(Decimal('0'), min(Decimal('100'), pct))
    return float(round(pct, 1))


def _owner_label(okr):
    if okr.scope == SCOPE_INDIVIDUAL:
        if okr.profile_id and okr.profile and okr.profile.employee:
            return okr.profile.employee.full_name
        return 'Unassigned'
    return 'Company' if okr.scope == SCOPE_COMPANY else 'Department'


def _node_dict(okr, children):
    return {
        'id': str(okr.pk),
        'name': okr.name,
        'scope': okr.scope,
        'owner': _owner_label(okr),
        'progress': _progress_pct(okr),
        'weight_pct': float(okr.weight_pct or 0),
        'target': okr.target or '',
        'parent_id': str(okr.parent_id) if okr.parent_id else None,
        'children': children,
    }


def _scope_rank(okr):
    return {SCOPE_COMPANY: 0, SCOPE_DEPARTMENT: 1}.get(okr.scope, 2)


def _child_sort_key(okr):
    return (-float(okr.weight_pct or 0), (okr.name or '').lower())


def _build_tree(okrs):
    """Assemble `okrs` (flat, already period + entity filtered) into a
    parent/children tree. An OKR whose parent isn't in this set (filtered out
    by entity scope, or genuinely parent=None) becomes a root — orphans are
    surfaced, never silently dropped."""
    by_id = {o.pk: o for o in okrs}
    children_by_parent = {}
    roots = []
    for o in okrs:
        if o.parent_id and o.parent_id in by_id:
            children_by_parent.setdefault(o.parent_id, []).append(o)
        else:
            roots.append(o)

    def build(o):
        kids = sorted(children_by_parent.get(o.pk, []), key=_child_sort_key)
        return _node_dict(o, [build(k) for k in kids])

    roots.sort(key=lambda o: (_scope_rank(o), *_child_sort_key(o)))
    return [build(o) for o in roots]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def okr_tree(request):
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    period = str(request.query_params.get('period') or '').strip()
    if not period:
        period = (OKR.objects.order_by('-period')
                  .values_list('period', flat=True).first()) or ''

    if not period:
        return Response({'period': None, 'node_count': 0, 'roots': []})

    okrs = _period_okrs(request, period)
    tree = _build_tree(okrs)
    return Response({'period': period, 'node_count': len(okrs), 'roots': tree})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def okr_periods(request):
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied
    values = list(OKR.objects.order_by('-period')
                  .values_list('period', flat=True).distinct())
    return Response({'periods': values})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def okr_create_objective(request):
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    data = request.data or {}
    period = str(data.get('period') or '').strip()
    name = str(data.get('name') or '').strip()
    scope = str(data.get('scope') or '').strip().lower()

    if not period:
        return Response({'detail': 'period is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(period) > 20:
        return Response({'detail': 'period must be at most 20 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not name:
        return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(name) > 200:
        return Response({'detail': 'name must be at most 200 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if scope not in VALID_SCOPES:
        return Response({'detail': f"scope must be one of {sorted(VALID_SCOPES)}."},
                        status=status.HTTP_400_BAD_REQUEST)

    target = str(data.get('target') or '').strip()
    if len(target) > 200:
        return Response({'detail': 'target must be at most 200 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    weight_raw = data.get('weight_pct')
    try:
        weight_pct = Decimal(str(weight_raw)) if weight_raw not in (None, '') else Decimal('0')
    except (InvalidOperation, ValueError):
        return Response({'detail': 'weight_pct must be a number.'}, status=status.HTTP_400_BAD_REQUEST)
    if weight_pct < 0 or weight_pct > 100:
        return Response({'detail': 'weight_pct must be between 0 and 100.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # profile — required iff individual, forbidden otherwise. OKR.profile is
    # nullable specifically so a company/department goal can skip it.
    profile = None
    profile_id = data.get('profile_id')
    if scope == SCOPE_INDIVIDUAL:
        if not profile_id:
            return Response({'detail': 'profile_id is required for an individual objective.'},
                            status=status.HTTP_400_BAD_REQUEST)
        profile_qs = apply_company_scope(
            request, HRISProfile.objects.select_related('employee'), 'employee__company_id')
        profile = profile_qs.filter(pk=profile_id).first()
        if profile is None:
            return Response({'detail': 'Employee not found (or outside your entity scope).'},
                            status=status.HTTP_400_BAD_REQUEST)
    elif profile_id:
        return Response({'detail': 'profile_id must be omitted for a company/department objective.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # parent — optional; must exist, in the same period, within entity scope.
    parent = None
    parent_id = data.get('parent_id')
    if parent_id:
        by_id = {str(o.pk): o for o in _period_okrs(request, period)}
        parent = by_id.get(str(parent_id))
        if parent is None:
            return Response(
                {'detail': 'Parent objective not found for this period (or outside your entity scope).'},
                status=status.HTTP_400_BAD_REQUEST)

    okr = OKR.objects.create(
        profile=profile,
        parent=parent,
        scope=scope,
        period=period,
        name=name,
        target=target,
        weight_pct=weight_pct,
    )
    return Response(_node_dict(okr, []), status=status.HTTP_201_CREATED)
