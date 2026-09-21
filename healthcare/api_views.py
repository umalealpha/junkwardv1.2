"""healthcare/api_views.py — Health Care Quick Quote endpoints.

CFO directive 2026-05-26: drop any document with an individual's age or
DOB, get back office rate + Hannover Re reinsurance rate + margin per
life and totals. Spec is sheet 13_HC_AgeUpload_Pricing in
Omni_Build_Spec.xlsx.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .extract import extract_text, extract_lives, normalise_lives
from .rate_card import HC_PLANS, lookup_rate, list_plans
from django.utils import timezone

log = logging.getLogger(__name__)

_ZERO = Decimal('0.00')
_MAX_BYTES = 10 * 1024 * 1024   # 10 MB


def _q2(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal('0.01'))


class HealthQuickQuotePlansView(APIView):
    """GET /api/v1/health/quick-quote/plans/ — list known plan codes."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'plans': list_plans()})


class HealthQuickQuoteView(APIView):
    """POST /api/v1/health/quick-quote/ — multipart file → live quote.

    Body (multipart/form-data):
      file:          PDF / PNG / JPG / XLSX / CSV / DOCX / TXT — required
      plan_code:     one of HC_PLANS keys (default AD_CORE)
      quote_date:    ISO date (default today)
      raw_text:      optional fallback if file extraction fails
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        upload_id = uuid.uuid4().hex
        plan_code = (request.data.get('plan_code') or 'AD_CORE').upper()
        if plan_code not in HC_PLANS:
            return Response(
                {'success': False, 'detail': f"Unknown plan_code '{plan_code}'. "
                                             f"Valid: {list(HC_PLANS.keys())}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        quote_date_raw = request.data.get('quote_date')
        try:
            quote_date = date.fromisoformat(quote_date_raw) if quote_date_raw else timezone.localdate()
        except Exception:                              # noqa: BLE001
            quote_date = timezone.localdate()

        f = request.FILES.get('file')
        raw_text_override = (request.data.get('raw_text') or '').strip()

        if not f and not raw_text_override:
            return Response(
                {'success': False, 'detail': 'file or raw_text is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        warnings: list[str] = []
        errors:   list[str] = []

        file_bytes = b''
        mime, name, pages = '', '', 0
        if f:
            if f.size > _MAX_BYTES:
                return Response(
                    {'success': False, 'detail': f'File too large ({f.size} bytes). '
                                                 f'Max {_MAX_BYTES}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            file_bytes = f.read()
            mime = (f.content_type or '').lower()
            name = f.name
            pages = 1   # not tracked precisely here

        # 1. Extract raw text
        if raw_text_override:
            raw_text = raw_text_override
        else:
            raw_text = extract_text(file_bytes, mime, name) or ''
        if not raw_text.strip():
            warnings.append('No text could be extracted from the document.')

        # 2. DeepSeek life-detection
        try:
            ds_lives = extract_lives(raw_text)
        except Exception as exc:                       # noqa: BLE001
            warnings.append(f'DeepSeek extraction error: {exc}')
            ds_lives = []

        # 3. Normalise + validate
        lives = normalise_lives(ds_lives, quote_date=quote_date)
        if not lives:
            warnings.append('No age or DOB found — no lives priced. '
                            'Pass raw_text or upload a document with age.')

        # 4. Rate-card lookup per life
        priced: list[dict] = []
        total_office = _ZERO
        total_ri     = _ZERO
        for L in lives:
            try:
                rates = lookup_rate(
                    plan_code     = plan_code,
                    age           = L['age_years'],
                    life_category = L['life_category'],
                )
            except Exception as exc:                   # noqa: BLE001
                warnings.append(f"Rate-card error for life age "
                                f"{L['age_years']}: {exc}")
                continue
            office = rates['office_monthly_bwp']
            ri     = rates['ri_monthly_bwp']
            margin = (office - ri).quantize(Decimal('0.01'))
            margin_pct = (margin / office).quantize(Decimal('0.0001')) if office else _ZERO
            row = dict(L)
            row.update({
                'plan_code':                 plan_code,
                'plan_name':                 rates['plan_name'],
                'office_monthly_bwp':        str(office),
                'ri_monthly_bwp':            str(ri),
                'margin_monthly_bwp':        str(margin),
                'margin_pct':                str(margin_pct),
            })
            priced.append(row)
            total_office += office
            total_ri     += ri
            if L['extraction_confidence'] < 0.6 and L['extraction_confidence'] > 0:
                warnings.append(
                    f'Life age {L["age_years"]}: low extraction confidence '
                    f'{L["extraction_confidence"]:.2f} — verify manually before binding.'
                )

        # 5. Aggregate totals
        plan       = HC_PLANS[plan_code]
        broker_pct = plan['broker_pct']
        nbfira_pct = plan['nbfira_pct']
        broker     = (total_office * broker_pct).quantize(Decimal('0.01'))
        levy       = (total_office * nbfira_pct).quantize(Decimal('0.01'))
        gross_margin = (total_office - total_ri).quantize(Decimal('0.01'))
        net_margin   = (gross_margin - broker - levy).quantize(Decimal('0.01'))

        # 6. Generated Django snippet (audit-friendly skeleton)
        python_code = _build_python_snippet(plan_code, priced, quote_date)

        return Response({
            'success':   True,
            'upload_id': upload_id,
            'source': {
                'mime':  mime,
                'name':  name,
                'pages': pages,
                'text_chars': len(raw_text),
            },
            'plan_code':   plan_code,
            'plan_name':   plan['name'],
            'quote_date':  quote_date.isoformat(),
            'lives':       priced,
            'totals': {
                'office_monthly_bwp':       str(total_office.quantize(Decimal('0.01'))),
                'ri_monthly_bwp':           str(total_ri.quantize(Decimal('0.01'))),
                'gross_margin_monthly_bwp': str(gross_margin),
                'broker_commission_bwp':    str(broker),
                'nbfira_levy_bwp':          str(levy),
                'net_ad_margin_bwp':        str(net_margin),
                'broker_pct':               str(broker_pct),
                'nbfira_pct':               str(nbfira_pct),
            },
            'python_code': python_code,
            'warnings':    warnings,
            'errors':      errors,
        })


def _build_python_snippet(plan_code: str, lives: list[dict],
                         quote_date: date) -> str:
    """Tiny audit-friendly snippet the CFO can review before binding."""
    members_lit = ',\n        '.join(
        f"{{'age': {L['age_years']}, 'gender': {L['gender']!r}, "
        f"'life_category': {L['life_category']!r}}}"
        for L in lives
    ) or '# no lives'
    return (
        "# Auto-generated by /api/v1/health/quick-quote/ — review before bind.\n"
        f"plan_code  = {plan_code!r}\n"
        f"quote_date = '{quote_date.isoformat()}'\n"
        "members = [\n        "
        + members_lit + "\n]\n"
        "# To create a Quote in DRAFT:\n"
        "#   from underwriting.services import create_quote_from_lives\n"
        "#   quote = create_quote_from_lives(plan_code, quote_date, members,\n"
        "#                                   created_by=request.user)\n"
        "#   quote.status = 'DRAFT'\n"
        "#   quote.save()\n"
    )
