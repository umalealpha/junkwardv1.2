"""
core/intel_api.py — the machine-to-machine endpoint for core/intel_summary.py.

GET /api/v1/intel/summary/?month=2026-06&company=ADIC
    Authorization: Bearer <INTEL_SUMMARY_TOKEN>

Counts and totals only. 401 when the token is missing, wrong, or unconfigured.
Every call is logged (caller IP + what was asked for) — never the token.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.intel_summary import (build_comparison, build_summary, caller_allowed,
                                client_ip, feed_company_code, token_ok)

logger = logging.getLogger(__name__)


class IntelSummaryView(APIView):
    """Read-only aggregate feed for Alpha Brain. Token-gated, no PII."""

    # The shared-token check below is the ONLY wall, and it must be the one that
    # runs. authentication_classes MUST stay empty: with AZURE_SSO_ENABLED=true
    # (which is the case on prod) AzureJWTAuthentication RAISES on any non-JWT
    # Bearer value, so DRF answers 403 "Bad token header" before this view is
    # ever entered and no shared-token integration can work. Proven on prod
    # 2026-07-25.
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not token_ok(request) or not caller_allowed(request):
            return Response({'detail': 'Unauthorised.'}, status=401)

        month = (request.query_params.get('month') or '').strip() or None
        # The caller does NOT choose the entity — feed_company_code pins it
        # (CFO 2026-07-26: the brain sees ADIC), so neither asking for another
        # company nor omitting the parameter can widen the scope.
        company = feed_company_code((request.query_params.get('company') or '').strip() or None)
        try:
            payload = build_summary(month=month, company_code=company)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)

        logger.info('intel_summary served: month=%s company=%s ip=%s',
                    month or 'default', company or 'ALL',
                    client_ip(request) or '?')
        return Response(payload, status=200)


class IntelSummaryStaffView(APIView):
    """The same numbers for a signed-in staff member, plus Alpha Brain's side.

    Powers the Intelligence Summary page. Ordinary session/SSO auth — this is the
    staff surface, not the machine feed, so it keeps the normal authentication
    chain and no shared token is involved. Still aggregates only.

    ENTITY ISOLATION (DeepSeek review 2026-07-26 — this was open):
    CFO decision 2026-07-26 is that every Alpha Direct employee may see this
    page. That means everyone sees the DEFAULT entity — it does NOT mean
    everyone may pull any subsidiary's profit, or the all-company rollup. So the
    default is the same entity the brain sees (INTEL_SUMMARY_COMPANY, i.e. ADIC),
    and asking for a different company — or for the rollup — requires that the
    caller already has that company under core.models.allowed_company_ids, the
    same gate the rest of omni uses.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.models import Company, allowed_company_ids

        month = (request.query_params.get('month') or '').strip() or None
        asked = (request.query_params.get('company') or '').strip() or None
        default = (getattr(settings, 'INTEL_SUMMARY_COMPANY', '') or '').strip()
        if default.upper() == 'ALL':
            default = ''

        company = asked or default or None
        allowed = allowed_company_ids(request.user)
        unrestricted = allowed == {'*'}

        if not unrestricted:
            if company is None:
                # The rollup spans every entity — never an implicit default.
                return Response(
                    {'detail': 'Choose a company — the all-company view needs '
                               'group access.'}, status=403)
            from core.intel_summary import resolve_company
            co = resolve_company(company)
            if co is None:
                return Response({'detail': f'Unknown company code: {company}'}, status=400)
            # Compare the RESOLVED row's code, not the raw query string: the row
            # was looked up case-insensitively, so a string that merely looks
            # like the default must not grant the default's bypass.
            if str(co.id) not in allowed and co.code.upper() != (default or '').upper():
                return Response({'detail': 'You do not have access to that company.'},
                                status=403)

        try:
            payload = build_comparison(month=month, company_code=company)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
        return Response(payload, status=200)
