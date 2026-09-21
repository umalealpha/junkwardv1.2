"""reinsurance/controls_api.py — Reinsurer Controls & FAC Risk Register API.

Arun P. Iyer's control brief, 15-Sep-2026. Kept in its own module rather than
added to reinsurance/api_views.py so the treaty/cession endpoints that already
post to the GL are not disturbed by this work.

Nothing in here writes a journal, moves money or touches a policy.

WHAT THE NUMBERS MEAN, because getting this wrong is the whole risk:
  * "Unplaced" capacity is RETAINED by Alpha Direct. It is never counted as
    ceded, and it is reported as its own figure.
  * A missing value is reported as unknown. Four of the supplied panels state no
    share at all, and rendering that as 0% would read as "no exposure".
  * Active vs expired is date-driven, except where a person has overridden it,
    and then the override stands and is shown as an override.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import user_has_permission
from reinsurance import onboarding
from reinsurance.models import (
    FacReinsurerAllocation,
    FacRiskExposure,
    Reinsurer,
    ReinsurerSecurityAssessment,
)

log = logging.getLogger(__name__)
ZERO = Decimal('0.00')
S = Reinsurer.ApprovalStatus


def _d(v):
    """A money value as a string, or None when it is genuinely unknown.

    None and 0 are different facts and must not render the same.
    """
    return None if v is None else str(v)


def _require(user, perm: str):
    if not user_has_permission(user, perm):
        raise PermissionDenied('You do not have access to the reinsurance '
                               'controls.')


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _latest_assessment(r: Reinsurer):
    """The most recent security assessment — treating "no date" as oldest.

    The model orders ``-rating_date``, and on Postgres DESC sorts NULLS FIRST, so
    an undated stub would rank above a properly dated rating and the panel would
    show the stub. Ordering explicitly with nulls last fixes that.
    """
    return (r.security_assessments
            .order_by(F('rating_date').desc(nulls_last=True), '-created_at')
            .first())


def _assessment(a: ReinsurerSecurityAssessment) -> dict:
    return {
        'id': str(a.id),
        'rating': a.rating or None,
        'rating_agency': a.rating_agency or None,
        'rating_scale': a.rating_scale,
        'rating_scale_label': a.get_rating_scale_display(),
        'comparable_internationally': a.is_comparable_internationally,
        'outlook': a.outlook or None,
        'rating_date': a.rating_date.isoformat() if a.rating_date else None,
        'evidence_date': a.evidence_date.isoformat() if a.evidence_date else None,
        'internal_tier': a.internal_tier,
        'verified': a.verified,
        'conflicts': a.conflicts or None,
        'audited_financial_years': a.audited_financial_years or [],
        'solvency_ratio': _d(a.solvency_ratio),
        'capital_amount': _d(a.capital_amount),
        'capital_currency': a.capital_currency or None,
        'panel_provider': a.panel_provider or None,
        'panel_section': a.panel_section or None,
        # NULL share stays NULL all the way to the screen.
        'participation_share': _d(a.participation_share),
        'cover_from': a.cover_from.isoformat() if a.cover_from else None,
        'cover_to': a.cover_to.isoformat() if a.cover_to else None,
        'source_note': a.source_note or None,
    }


def _actions_for(user, r: Reinsurer) -> list:
    """What THIS person may do to THIS counterparty, next.

    The buttons mirror this; the server decides it. A blocked action is returned
    WITH its reason rather than hidden, so a person can see why they cannot act
    instead of wondering whether the screen is broken.
    """
    out = []
    for t in onboarding.TRANSITIONS.get(r.approval_status, ()):
        reason = onboarding.can_transition(user, r, t)
        out.append({
            'target': t,
            'label': dict(S.choices).get(t, t),
            'allowed': not reason,
            'blocked_because': reason or None,
            'reason_required': t in onboarding.REASON_REQUIRED,
        })
    return out


def _reinsurer(r: Reinsurer, *, detail: bool = False) -> dict:
    out = {
        'id': str(r.id),
        'name': r.name,
        'short_code': r.short_code,
        'legal_name': r.legal_name or None,
        'trading_name': r.trading_name or None,
        'carrier_group': r.carrier_group or None,
        'domicile': r.domicile or None,
        'regulator': r.regulator or None,
        'broker': r.broker or None,
        'credit_rating': r.credit_rating or None,
        'onboarding_purposes': r.onboarding_purposes or [],
        'approval_status': r.approval_status,
        'approval_status_label': r.get_approval_status_display(),
        'is_active': r.is_active,
        'effective_date': r.effective_date.isoformat() if r.effective_date else None,
        'expiry_date': r.expiry_date.isoformat() if r.expiry_date else None,
        'next_review_date': (r.next_review_date.isoformat()
                             if r.next_review_date else None),
        'is_expired': r.is_expired(),
        'may_be_placed': r.may_be_placed(),
        'placement_block_reason': r.placement_block_reason() or None,
        'suspension_reason': r.suspension_reason or None,
    }
    if not detail:
        return out
    latest = _latest_assessment(r)
    out.update({
        'registered_name': r.registered_name or None,
        'registration_number': r.registration_number or None,
        'licence_number': r.licence_number or None,
        'address': r.address or None,
        'tax_id': r.tax_id or None,
        'notes': r.notes or None,
        'latest_assessment': _assessment(latest) if latest else None,
        'assessments': [_assessment(a) for a in
                        r.security_assessments.order_by(
                            F('rating_date').desc(nulls_last=True),
                            '-created_at')[:25]],
        'transitions': [{
            'from_status': t.from_status,
            'to_status': t.to_status,
            'actor_email': t.actor_email or None,
            'comment': t.comment or None,
            'at': t.created_at_local.isoformat(),
        } for t in r.approval_transitions.all()[:50]],
    })
    return out


def _exposure(e: FacRiskExposure, *, detail: bool = False) -> dict:
    out = {
        'id': str(e.id),
        'reference': e.reference,
        'policy_number': e.policy_number or None,
        'insured_name': e.insured_name or None,
        'regulatory_class': e.regulatory_class or None,
        'currency_code': e.currency_code,
        'gross_sum_insured': _d(e.gross_sum_insured),
        'gross_premium': _d(e.gross_premium),
        'net_retention': _d(e.net_retention),
        'autofac_capacity': _d(e.autofac_capacity),
        'fac_placed_amount': _d(e.fac_placed_amount),
        # Retained, NOT ceded. Named so on every surface.
        'unplaced_retained_amount': _d(e.unplaced_retained_amount),
        'ceded_premium': _d(e.ceded_premium),
        'ceded_commission': _d(e.ceded_commission),
        'placement_date': e.placement_date.isoformat() if e.placement_date else None,
        'effective_date': e.effective_date.isoformat() if e.effective_date else None,
        'expiry_date': e.expiry_date.isoformat() if e.expiry_date else None,
        'status': e.status,
        'status_label': e.get_status_display(),
        'status_overridden': e.status_overridden,
        'status_override_reason': e.status_override_reason or None,
        'is_active': e.is_active(),
        'is_expired': e.is_expired(),
    }
    if not detail:
        return out
    out.update({
        'risk_description': e.risk_description or None,
        'risk_address': e.risk_address or None,
        'slip_reference': e.slip_reference or None,
        'evidence_note': e.evidence_note or None,
        'graphite_policy_id': e.graphite_policy_id or None,
        'source': {
            'system': e.source_system or None,
            'file': e.source_file or None,
            'sheet': e.source_sheet or None,
            'row': e.source_row or None,
            'imported_at': e.imported_at.isoformat() if e.imported_at else None,
            'reviewed_at': e.reviewed_at.isoformat() if e.reviewed_at else None,
        },
        'import_warnings': e.import_warnings or [],
        'allocation_variance': _d(e.allocation_variance()),
        'allocations': [{
            'id': str(a.id),
            'reinsurer': a.reinsurer.name,
            'reinsurer_id': str(a.reinsurer_id),
            'short_code': a.reinsurer.short_code,
            'share_percent': _d(a.share_percent),
            'allocated_amount': _d(a.allocated_amount),
            'allocated_premium': _d(a.allocated_premium),
            'commission_amount': _d(a.commission_amount),
            'slip_reference': a.slip_reference or None,
            'signed_date': a.signed_date.isoformat() if a.signed_date else None,
            'counterparty_approved': a.reinsurer.may_be_placed(),
        } for a in e.allocations.select_related('reinsurer')],
    })
    return out


# ---------------------------------------------------------------------------
# Control centre
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reinsurance_control_centre(request):
    """KPIs, the onboarding queue and the exceptions list — one call.

    Every count is a live query, and every gap is reported as a gap. Nothing on
    this screen is allowed to imply that unknown data is clean data.
    """
    _require(request.user, 're.view')
    today = timezone.localdate()

    rs = Reinsurer.objects.all()
    by_status = {row['approval_status']: row['n'] for row in
                 rs.values('approval_status').annotate(n=Count('id'))}
    pending_states = (S.PENDING_UW_MANAGER, S.PENDING_COMPLIANCE,
                      S.PENDING_PRINCIPAL, S.PENDING_CEO)
    #: Everything a person can still move, which is NOT the same as everything
    #: awaiting an approval. A Draft, and anything sent back, is waiting on
    #: someone too — and the queue is the only screen that offers the button.
    actionable_states = pending_states + (S.DRAFT, S.RETURNED,
                                          S.REJECTED, S.EXPIRED)

    approved = rs.filter(approval_status=S.APPROVED)
    expiring = approved.filter(expiry_date__isnull=False,
                               expiry_date__gte=today,
                               expiry_date__lte=today + timezone.timedelta(days=60))

    # Counterparties with NO security assessment at all. Not an error — a gap
    # that has to stay on screen until somebody closes it.
    #
    # "unverified" and "national_only" used to be a plain filter across the
    # REVERSE relation (security_assessments__verified=False, etc.), which
    # matches if ANY historical row satisfies it — and assessments are kept as
    # history and never overwritten (models.py). A counterparty whose FIRST
    # assessment was unverified or national-scale kept tripping these counters
    # forever, even after Compliance filed a proper verified international
    # rating — a stale count that no longer reflected current status (QC
    # 3885a3ec, "stale approved counts"). This reads the LATEST assessment
    # per counterparty — the same one `_latest_assessment()` shows everywhere
    # else on this screen — not merely whether an adequate row exists ever.
    no_assessment_n = 0
    unverified_n = 0
    national_only_n = 0
    for r in rs.prefetch_related('security_assessments'):
        latest = _latest_assessment(r)
        if latest is None:
            no_assessment_n += 1
            continue
        if not latest.verified:
            unverified_n += 1
        if latest.rating_scale == ReinsurerSecurityAssessment.Scale.NATIONAL:
            national_only_n += 1

    fac = FacRiskExposure.objects.exclude(status=FacRiskExposure.Status.CANCELLED)
    active_ids = [e.id for e in fac if e.is_active(today)]
    expired_ids = [e.id for e in fac if e.is_expired(today)]
    act = fac.filter(id__in=active_ids).aggregate(n=Count('id'))
    exp = fac.filter(id__in=expired_ids).aggregate(n=Count('id'))

    # Money never crosses a currency (QC P0, 16-Sep-2026: "BWP and USD are
    # added together and displayed as one BWP total"). FacRiskExposure carries
    # currency_code per row and the FAC pack holds both, so the old single
    # Sum() over the lot was adding pula to dollars and labelling the answer
    # BWP. Counts are currency-agnostic and still total; amounts are reported
    # per currency and never summed across them.
    fac_by_currency = []
    for cur in sorted(set(fac.values_list('currency_code', flat=True))):
        a = fac.filter(id__in=active_ids, currency_code=cur).aggregate(
            placed=Sum('fac_placed_amount'), retained=Sum('unplaced_retained_amount'),
            si=Sum('gross_sum_insured'), n=Count('id'))
        x = fac.filter(id__in=expired_ids, currency_code=cur).aggregate(
            placed=Sum('fac_placed_amount'), n=Count('id'))
        fac_by_currency.append({
            'currency':           cur or 'BWP',
            'active_count':       a['n'] or 0,
            'active_placed':      _d(a['placed'] or ZERO),
            'active_sum_insured': _d(a['si'] or ZERO),
            'retained_unplaced':  _d(a['retained'] or ZERO),
            'expired_count':      x['n'] or 0,
            'expired_placed':     _d(x['placed'] or ZERO),
        })
    # A single headline figure is emitted ONLY when there is genuinely one
    # currency to emit. With more than one it is null, so a client that has not
    # been taught about by_currency shows a dash rather than a wrong number —
    # the one thing a finance screen must never do.
    only = fac_by_currency[0] if len(fac_by_currency) == 1 else None

    return Response({
        'as_of': today.isoformat(),
        'counterparties': {
            'total': rs.count(),
            'approved': by_status.get(S.APPROVED, 0),
            'pending': sum(by_status.get(s, 0) for s in pending_states),
            'blocked': sum(by_status.get(s, 0) for s in
                           (S.SUSPENDED, S.REJECTED, S.EXPIRED, S.RETURNED)),
            'draft': by_status.get(S.DRAFT, 0),
            'by_status': by_status,
        },
        'evidence_gaps': {
            'no_security_assessment': no_assessment_n,
            'rating_not_verified': unverified_n,
            'national_scale_only': national_only_n,
            'approval_expiring_60d': expiring.count(),
            'note': ('A gap is shown until it is closed. An unrated or '
                     'unverified counterparty never counts as approved by '
                     'default.'),
        },
        'fac_exposure': {
            'active_count': act['n'] or 0,
            'expired_count': exp['n'] or 0,
            'currencies': [c['currency'] for c in fac_by_currency],
            'mixed_currency': len(fac_by_currency) > 1,
            'by_currency': fac_by_currency,
            # Null whenever more than one currency is in play — see the note
            # above the loop. Never a cross-currency sum.
            'active_placed':      only['active_placed']      if only else None,
            'active_sum_insured': only['active_sum_insured']  if only else None,
            'retained_unplaced':  only['retained_unplaced']   if only else None,
            'expired_placed':     only['expired_placed']      if only else None,
            'note': ('Retained/unplaced is capacity we sought and did not place. '
                     'It stays on our own book and is never shown as ceded. '
                     'Amounts are per currency and are never added across '
                     'currencies — read by_currency, not one headline total.'),
        },
        # The queue rows carry their own actions — otherwise the screen shows a
        # list of things waiting on you and no way to act on any of them, which
        # is what it did before (the handler existed and nothing called it).
        'queue': [{**_reinsurer(r),
                   'available_actions': _actions_for(request.user, r)}
                  for r in rs.filter(approval_status__in=actionable_states)
                             .select_related()
                             .prefetch_related('security_assessments')
                             # Never submitted sorts FIRST — Postgres puts NULL
                             # last on a plain ASC, which would bury every new
                             # draft under the rows already in flight.
                             .order_by(F('submitted_at').asc(nulls_first=True))[:50]],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reinsurer_list(request):
    _require(request.user, 're.view')
    qs = Reinsurer.objects.all()
    status_f = (request.query_params.get('status') or '').strip()
    if status_f:
        qs = qs.filter(approval_status=status_f)
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(short_code__icontains=q)
                       | Q(legal_name__icontains=q) | Q(carrier_group__icontains=q))
    if (request.query_params.get('placeable') or '') in ('1', 'true'):
        qs = [r for r in qs if r.may_be_placed()]
        return Response({'count': len(qs),
                         'results': [_reinsurer(r) for r in qs[:200]]})
    return Response({'count': qs.count(),
                     'results': [_reinsurer(r) for r in qs[:200]]})


#: Creating a counterparty is the first step of the SAME chain
#: onboarding.REQUIRED_PERMISSION gates, so it reuses that permission rather
#: than inventing a second answer to "who may start this".
CREATE_PERM = 'uw.counterparty.submit'

#: Free text a person types. Everything else on the model is set by the
#: workflow, by compliance, or by the KYC register - not on this form.
_CREATE_TEXT = ('legal_name', 'registered_name', 'trading_name', 'carrier_group',
                'domicile', 'registration_number', 'licence_number', 'regulator',
                'address', 'tax_id', 'broker', 'credit_rating', 'notes')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reinsurer_create(request):
    """Raise a new counterparty as a DRAFT.

    It is deliberately NOT placeable on the way out: a new row lands in Draft
    and has to walk the onboarding chain, so "just created" can never read as
    "fine to use". The screen shows that, and `may_be_placed()` enforces it.
    """
    _require(request.user, CREATE_PERM)

    name = (request.data.get('name') or '').strip()
    short_code = (request.data.get('short_code') or '').strip()
    if not name:
        return Response({'detail': 'Give the reinsurer a name.'}, status=400)
    if not short_code:
        return Response({'detail': 'Give the reinsurer a short code — it is what '
                                   'appears on journals and reports.'}, status=400)

    # Both columns are unique. Answer with the name of the existing record
    # rather than a database error, so the person knows it is already on file.
    clash = Reinsurer.objects.filter(
        Q(name__iexact=name) | Q(short_code__iexact=short_code)).first()
    if clash is not None:
        which = 'name' if clash.name.lower() == name.lower() else 'short code'
        return Response(
            {'detail': f'That {which} is already used by "{clash.name}" '
                       f'({clash.short_code}). Open that record instead of '
                       f'creating a second one.'},
            status=400)

    fields = {f: (request.data.get(f) or '').strip() for f in _CREATE_TEXT}

    # No dates here on purpose. effective/expiry/next_review are approval-stage
    # facts the approver sets, not something Underwriting types when raising a
    # draft — and a field the form cannot send is a door nobody walks through.
    purposes = request.data.get('onboarding_purposes') or []
    if isinstance(purposes, str):
        purposes = [p.strip() for p in purposes.split(',') if p.strip()]
    allowed = {c for c, _ in Reinsurer.OnboardingPurpose.choices}
    unknown = [p for p in purposes if p not in allowed]
    if unknown:
        return Response({'detail': f'Not something we onboard for: '
                                   f'{", ".join(unknown)}.'}, status=400)

    country = (request.data.get('country') or '').strip().upper() or 'BW'

    r = Reinsurer(
        name=name,
        short_code=short_code,
        country=country,
        onboarding_purposes=list(purposes),
        approval_status=Reinsurer.ApprovalStatus.DRAFT,
        is_active=True,
        **fields,
    )
    try:
        r.full_clean()
    except ValidationError as e:
        # Name the field. "Ensure this value has at most 20 characters" with no
        # hint which box is a form the person cannot fix.
        field, msgs = next(iter(e.message_dict.items()))
        label = field.replace('_', ' ')
        return Response({'detail': f'{label}: {msgs[0]}'}, status=400)
    r.save(audit_user=request.user)

    out = _reinsurer(r, detail=True)
    out['available_actions'] = _actions_for(request.user, r)
    return Response(out, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reinsurer_detail(request, reinsurer_id):
    _require(request.user, 're.view')
    try:
        r = Reinsurer.objects.get(pk=reinsurer_id)
    except Reinsurer.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    data = _reinsurer(r, detail=True)
    # What THIS user may do next — the buttons mirror this, the server decides it.
    data['available_actions'] = _actions_for(request.user, r)
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reinsurer_transition(request, reinsurer_id):
    """Move a counterparty through the onboarding chain.

    The whole decision lives in reinsurance.onboarding — this view only carries
    the request to it and turns a refusal into a sentence.
    """
    # This was missing entirely. Every other endpoint here gates on re.view, and
    # without it a stranger could probe which counterparty ids exist (404 vs 403)
    # before the state machine ever got a say.
    _require(request.user, 're.view')
    try:
        r = Reinsurer.objects.get(pk=reinsurer_id)
    except Reinsurer.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)

    target = (request.data.get('target') or '').strip()
    comment = (request.data.get('comment') or '').strip()
    if not target:
        return Response({'detail': 'Say which step you are moving it to.'},
                        status=400)
    try:
        r = onboarding.transition(request.user, r, target, comment)
    except PermissionDenied as e:
        return Response({'detail': str(e)}, status=403)
    except onboarding.TransitionRefused as e:
        return Response({'detail': '; '.join(e.messages)}, status=400)
    out = _reinsurer(r, detail=True)
    out['available_actions'] = _actions_for(request.user, r)
    return Response(out)


# ---------------------------------------------------------------------------
# FAC risk register
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fac_risk_register(request):
    """Active and expired facultative risk, and the FAC amount by reinsurer."""
    _require(request.user, 're.view')
    today = timezone.localdate()

    qs = FacRiskExposure.objects.prefetch_related('allocations__reinsurer')
    cls = (request.query_params.get('regulatory_class') or '').strip()
    if cls:
        qs = qs.filter(regulatory_class=cls)
    cur = (request.query_params.get('currency') or '').strip()
    if cur:
        qs = qs.filter(currency_code=cur)
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(policy_number__icontains=q)
                       | Q(insured_name__icontains=q))

    state = (request.query_params.get('state') or 'all').strip().lower()
    rows = list(qs[:1000])
    # Decide truncation on the SLICE, before the state filter narrows it. Asking
    # len(rows) >= 1000 after filtering is false the moment the filter drops a
    # single row, so a capped list would report itself complete and every total
    # below it would quietly understate.
    truncated = len(rows) >= 1000
    if state == 'active':
        rows = [e for e in rows if e.is_active(today)]
    elif state == 'expired':
        rows = [e for e in rows if e.is_expired(today)]

    # By reinsurer — the question the brief actually asks: every current and
    # expired FAC risk and amount, by reinsurer.
    #
    # CURRENCY (QC P0, 16-Sep-2026): an allocated_amount belongs to its
    # exposure's currency_code, so the old single active_amount/expired_amount
    # per reinsurer was adding pula to dollars and the screen then wrote BWP in
    # front of the result. Amounts are now kept per currency and are never
    # added across them; the flat keys survive only for a single-currency
    # reinsurer and go null the moment there are two, so a client that has not
    # been taught about by_currency renders a dash and not a wrong figure.
    by_reinsurer: dict[str, dict] = {}
    for e in rows:
        live = e.is_active(today)
        ccy  = e.currency_code or 'BWP'
        for a in e.allocations.all():
            k = a.reinsurer.short_code
            b = by_reinsurer.setdefault(k, {
                'reinsurer': a.reinsurer.name, 'short_code': k,
                'carrier_group': a.reinsurer.carrier_group or None,
                'approved': a.reinsurer.may_be_placed(),
                'active_count': 0, 'expired_count': 0,
                '_ccy': {},
            })
            c = b['_ccy'].setdefault(ccy, {
                'currency': ccy,
                'active_count': 0, 'active_amount': ZERO,
                'expired_count': 0, 'expired_amount': ZERO,
            })
            if live:
                b['active_count'] += 1
                c['active_count'] += 1
                c['active_amount'] += (a.allocated_amount or ZERO)
            else:
                b['expired_count'] += 1
                c['expired_count'] += 1
                c['expired_amount'] += (a.allocated_amount or ZERO)

    def _flatten(b: dict) -> dict:
        cur = sorted(b.pop('_ccy').values(), key=lambda c: c['currency'])
        for c in cur:
            c['active_amount']  = _d(c['active_amount'])
            c['expired_amount'] = _d(c['expired_amount'])
        one = cur[0] if len(cur) == 1 else None
        return {
            **b,
            'currencies':     [c['currency'] for c in cur],
            'mixed_currency': len(cur) > 1,
            'by_currency':    cur,
            'active_amount':  one['active_amount']  if one else None,
            'expired_amount': one['expired_amount'] if one else None,
        }

    # Retained/unplaced is money too, so it gets the same treatment.
    retained_by_cur: dict[str, object] = {}
    for e in rows:
        if e.is_active(today):
            k = e.currency_code or 'BWP'
            retained_by_cur[k] = (retained_by_cur.get(k, ZERO)
                                  + (e.unplaced_retained_amount or ZERO))
    retained_rows = [{'currency': k, 'amount': _d(v)}
                     for k, v in sorted(retained_by_cur.items())]

    # A silent cap on a money total is how a figure quietly understates itself.
    # Say when the list was cut, and never report a count larger than the rows
    # the totals were actually computed over.
    #
    # PAGING (16-Sep-2026): the 500-row display cap was honestly reported but
    # there was no way to ask for row 501, so the rest of the register was
    # simply unreachable. page/page_size now walk it. The totals above always
    # cover the WHOLE matched set (up to the 1,000 cap), never just this page —
    # a per-page total on a money screen is a wrong number with a right label.
    try:
        page = max(1, int(request.query_params.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get('page_size') or 500)
    except (TypeError, ValueError):
        page_size = 500
    page_size = min(max(page_size, 1), 500)
    start = (page - 1) * page_size
    shown = rows[start:start + page_size]
    return Response({
        'as_of': today.isoformat(),
        'state': state,
        'count': len(rows),
        'shown': len(shown),
        'page': page,
        'page_size': page_size,
        'has_next': start + page_size < len(rows),
        'truncated': truncated,
        'truncated_note': ('More than 1,000 risks matched. The totals below cover '
                           'the first 1,000 only — narrow the filters before '
                           'relying on them.') if truncated else None,
        'results': [_exposure(e) for e in shown],
        'by_reinsurer': sorted(
            (_flatten(b) for b in by_reinsurer.values()),
            key=lambda x: -max([float(c['active_amount'] or 0)
                                for c in x['by_currency']] or [0.0])),
        'retained_unplaced_by_currency': retained_rows,
        'retained_unplaced': (retained_rows[0]['amount']
                              if len(retained_rows) == 1 else None),
        'retained_note': ('Capacity sought and not placed. It stays on Alpha '
                          'Direct\'s own book — it is not ceded. Amounts are '
                          'per currency and are never added across them.'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fac_risk_detail(request, exposure_id):
    _require(request.user, 're.view')
    try:
        e = (FacRiskExposure.objects
             .prefetch_related('allocations__reinsurer').get(pk=exposure_id))
    except FacRiskExposure.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)
    return Response(_exposure(e, detail=True))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def security_panel(request):
    """The security panel and concentration, by carrier and by group.

    Concentration is measured per GROUP as well as per carrier: two lines
    written by two subsidiaries of one group are one exposure to that group, and
    reporting them separately understates it.
    """
    _require(request.user, 're.view')
    today = timezone.localdate()

    # Money never crosses a currency (control centre / FAC register rule, QC P0
    # 16-Sep-2026) — this screen was the one place that fix missed.
    # FacReinsurerAllocation carries no currency of its own; an allocated
    # amount belongs to its EXPOSURE's currency_code, so it is aggregated per
    # (reinsurer, exposure currency) rather than with one Sum() over every
    # allocation a reinsurer holds — that single Sum() was adding pula to
    # dollars for any reinsurer placed on both.
    alloc_rows = (FacReinsurerAllocation.objects
                  .values('reinsurer_id', 'exposure__currency_code')
                  .annotate(amount=Sum('allocated_amount'), n=Count('id')))
    alloc_by_reinsurer: dict[str, dict[str, dict]] = {}
    for row in alloc_rows:
        rid = str(row['reinsurer_id'])
        ccy = row['exposure__currency_code'] or 'BWP'
        alloc_by_reinsurer.setdefault(rid, {})[ccy] = {
            'amount': row['amount'] or ZERO, 'n': row['n'] or 0,
        }

    def _cur_list(by_ccy: dict[str, dict]) -> list[dict]:
        return sorted(
            ({'currency': c, 'amount': _d(v['amount']), 'count': v['n']}
             for c, v in by_ccy.items()),
            key=lambda x: x['currency'])

    rows = []
    for r in Reinsurer.objects.prefetch_related('security_assessments'):
        a = _latest_assessment(r)
        by_ccy = alloc_by_reinsurer.get(str(r.id), {})
        cur = _cur_list(by_ccy)
        # A single headline figure ONLY when there is genuinely one currency —
        # with more than one it is null, so a client that has not been taught
        # about by_currency shows a dash rather than a wrong number.
        one = cur[0] if len(cur) == 1 else None
        rows.append({
            'reinsurer': r.name,
            'short_code': r.short_code,
            'carrier_group': r.carrier_group or None,
            'domicile': r.domicile or None,
            'approval_status': r.approval_status,
            'approved': r.may_be_placed(),
            'rating': (a.rating or None) if a else None,
            'rating_scale': a.rating_scale if a else None,
            'rating_verified': a.verified if a else False,
            'internal_tier': a.internal_tier if a else None,
            'participation_share': _d(a.participation_share) if a else None,
            'fac_allocations': sum(v['n'] for v in by_ccy.values()),
            'currencies': [c['currency'] for c in cur],
            'mixed_currency': len(cur) > 1,
            'by_currency': cur,
            'fac_amount': one['amount'] if one else None,
            'has_assessment': a is not None,
        })

    # Concentration by carrier group — same rule, per currency again: two
    # carriers on different currencies are not one addable exposure.
    groups: dict[str, dict] = {}
    for r in rows:
        k = r['carrier_group'] or r['reinsurer']
        g = groups.setdefault(k, {'group': k, 'carriers': 0, 'unapproved': 0,
                                  '_ccy': {}})
        g['carriers'] += 1
        if not r['approved']:
            g['unapproved'] += 1
        for c in r['by_currency']:
            g['_ccy'][c['currency']] = (g['_ccy'].get(c['currency'], ZERO)
                                        + Decimal(c['amount'] or '0'))

    total_by_ccy: dict[str, Decimal] = {}
    for g in groups.values():
        for ccy, amt in g['_ccy'].items():
            total_by_ccy[ccy] = total_by_ccy.get(ccy, ZERO) + amt

    def _flatten_group(g: dict) -> dict:
        cur = sorted(({'currency': c, 'amount': _d(v)}
                      for c, v in g['_ccy'].items()), key=lambda x: x['currency'])
        one = cur[0] if len(cur) == 1 else None
        share = None
        if one is not None:
            t = total_by_ccy.get(one['currency'], ZERO)
            share = float(Decimal(one['amount']) / t * 100) if t else None
        return {
            'group': g['group'], 'carriers': g['carriers'],
            'unapproved': g['unapproved'],
            'currencies': [c['currency'] for c in cur],
            'mixed_currency': len(cur) > 1,
            'by_currency': cur,
            'fac_amount': one['amount'] if one else None,
            'share_of_fac': share,
        }

    concentration = sorted(
        (_flatten_group(g) for g in groups.values()),
        key=lambda x: -max([float(c['amount'] or 0) for c in x['by_currency']] or [0.0]))

    total_currencies = sorted(total_by_ccy)
    grand_one = (total_by_ccy[total_currencies[0]]
                if len(total_currencies) == 1 else None)

    return Response({
        'as_of': today.isoformat(),
        'panel': sorted(
            rows, key=lambda x: -max([float(c['amount'] or 0)
                                      for c in x['by_currency']] or [0.0])),
        'concentration': concentration,
        'currencies': total_currencies,
        'mixed_currency': len(total_currencies) > 1,
        'total_by_currency': [{'currency': c, 'amount': _d(total_by_ccy[c])}
                              for c in total_currencies],
        'total_fac_amount': _d(grand_one) if grand_one is not None else None,
        'scale_warning': ('National-scale and international-scale ratings are '
                          'not the same measure and are never ranked against '
                          'each other here.'),
        'currency_note': ('Amounts are per currency and are never added across '
                          'them — read by_currency, not a single headline total.'),
    })
