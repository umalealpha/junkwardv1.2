"""
procurement/claims_review_check.py — POST /api/v1/claims-po/<id>/review-check/

"Aria's pre-send checks" (CFO feature #4, 2026-07-08). Before a claims handler
generates / emails the two POs, this runs a set of deterministic sanity checks
over the parsed assessment + the computed split and returns advisory flags.

DELIBERATELY RULE-BASED, LOCAL — no external AI call. Alpha Direct's AI policy
(core/ai_assist.py docstring + CFO directive) forbids sending claim AMOUNTS to
external reasoning engines, and the whole value here is checking those amounts.
So every check runs in-process on the real numbers; nothing leaves the server.
(A future de-identified AI qualitative layer can be added over ratios/part
descriptions only — flagged for later, not built here.)

Advisory only: this never blocks generation. The frontend shows the flags; the
handler decides.
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import resolve_company_id_param
from core.api_key_auth import ApiKeyScopePermission
from .claims_models import ClaimsAssessment
from .claims_engine import (
    build_allocation_plan, apply_allocations, claims_split, compute_excess,
)
from .claims_api import _resolve_vendor_contacts

# A markup above this reads as a data-entry error rather than a real rate.
MARKUP_CEILING_PCT = 40.0
# A part priced this many times the median part is worth a second look.
PART_OUTLIER_FACTOR = 5.0


def _f(v):
    return None if v is None else float(v)


class ClaimsReviewCheckView(APIView):
    """Deterministic pre-send checks for a claims assessment's two POs."""
    permission_classes = [IsAuthenticated, ApiKeyScopePermission]

    def post(self, request, pk):
        # Company-scoped fetch (same rule the rest of claims-po uses).
        qs = ClaimsAssessment.objects.select_related('company')
        cid = resolve_company_id_param(request)
        assessment = qs.filter(pk=pk).first()
        if assessment is None:
            return Response({'detail': 'Assessment not found.'}, status=404)
        if cid and assessment.company_id and str(assessment.company_id) != str(cid):
            return Response({'detail': 'Assessment not found.'}, status=404)

        report = assessment.report_json or {}
        flags: list[dict] = []

        def flag(sev, msg):
            flags.append({'severity': sev, 'message': msg, 'source': 'rule'})

        if not report:
            flag('high', 'This assessment has not been parsed yet — nothing to check.')
            return Response({'flags': flags, 'ai_ok': False, 'checks_ran': False})

        markup_pct = _f(assessment.markup_pct)
        plan = build_allocation_plan(report, markup_pct)
        allocated = apply_allocations(plan, assessment.line_allocations)
        split = claims_split(
            report,
            excess_pct=(None if assessment.excess_pct is None
                        else float(assessment.excess_pct) / 100.0),
            excess_min=_f(assessment.excess_min),
            markup_pct=markup_pct,
            excess_amount=_f(assessment.excess_amount),
        )
        excess = compute_excess(
            report, allocated,
            _f(assessment.excess_pct), _f(assessment.excess_min),
            _f(assessment.excess_amount),
        )
        spec = split.get('specialised', {})
        excess_amt = float(excess['amount_incl']) if excess else float(spec.get('excess') or 0)
        gross = float(spec.get('gross_excl') or 0)

        # 1. Excess must not exceed the repair value (would make a negative PO).
        if gross > 0 and excess_amt > gross:
            flag('high', f'Excess (P{excess_amt:,.2f}) is larger than the repairer '
                         f'work (P{gross:,.2f}) — the repairer PO would be negative. '
                         'Check the excess.')
        # 2. Excess unusually large vs the repair (>60%): worth confirming.
        elif gross > 0 and excess_amt > 0.60 * gross:
            pct = round(100 * excess_amt / gross)
            flag('warn', f'Excess is {pct}% of the repair value — high. Confirm the '
                         'excess figure against the policy.')

        # 3. Markup ceiling.
        if markup_pct is not None and markup_pct > MARKUP_CEILING_PCT:
            flag('warn', f'Markup is {markup_pct:.0f}% — above the usual {MARKUP_CEILING_PCT:.0f}% '
                         'ceiling. Confirm it was entered correctly.')

        # 4. Non-excess line at zero / negative price.
        for row in allocated:
            for ln in row.get('lines', []):
                up = float(ln.get('unit_price') or 0)
                if up <= 0:
                    flag('warn', f'"{str(ln.get("description",""))[:50]}" has a '
                                 f'{"zero" if up == 0 else "negative"} price — check it '
                                 'is not a data error.')
                    break

        # 5. Part-price outlier (a supplier part far above the others).
        part_prices = []
        for row in allocated:
            if row.get('category') == 'Parts':
                for ln in row.get('lines', []):
                    up = float(ln.get('unit_price') or 0)
                    if up > 0:
                        part_prices.append((up, str(ln.get('description', ''))[:50]))
        if len(part_prices) >= 3:
            vals = sorted(p for p, _ in part_prices)
            median = vals[len(vals) // 2]
            if median > 0:
                for up, desc in part_prices:
                    if up > PART_OUTLIER_FACTOR * median:
                        flag('warn', f'Part "{desc}" (P{up:,.2f}) is far above the '
                                     f'other parts (median P{median:,.2f}) — verify the price.')
                        break

        # 6. Unresolved / blank vendor on any allocation row.
        labels = []
        for row in allocated:
            v = (row.get('vendor') or '').strip()
            if v and v not in labels:
                labels.append(v)
            elif not v:
                flag('high', f'The "{row.get("category","")}" line has no vendor — '
                             'assign one before generating.')
        if labels:
            resolved = _resolve_vendor_contacts(labels, assessment.company)
            for lbl in labels:
                if resolved.get(lbl) is None:
                    flag('warn', f'Vendor "{lbl}" does not match exactly one supplier '
                                 'on file — pick the right one so its PO can be raised.')

        # 7. Split reconciles to the two PO blocks (defensive).
        try:
            grand = float(split['grand']['incl'])
            parts_incl = float(split['motor_centre']['incl'])
            spec_incl = float(spec.get('incl') or 0)
            if abs(grand - (parts_incl + spec_incl)) > 0.05:
                flag('high', 'The split total does not reconcile to the two PO totals — '
                             'do not send; report this.')
        except (KeyError, TypeError, ValueError):
            pass

        if not flags:
            flags.append({'severity': 'info',
                          'message': 'Checks passed — nothing unusual on this assessment.',
                          'source': 'rule'})

        return Response({'flags': flags, 'ai_ok': False, 'checks_ran': True})
