"""Reinsurance renewal-pack document download.

CFO directive 2026-06-05 (Kago renewal pack). Serves the 2026/27 renewal
source documents that power the /reinsurance/renewal dashboard. Files live in
the persistent media volume at MEDIA_ROOT/reinsurance-renewal/.

Security:
  - IsAuthenticated only.
  - Strict ALLOWLIST of exact filenames — never trusts the raw query string
    for a filesystem path (no traversal).
  - Every download is written to the AuditLog (PwC-auditable trail).
"""
import logging
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

log = logging.getLogger(__name__)


class _RenewalAskThrottle(UserRateThrottle):
    """Cap the paid DeepSeek endpoint (expert-audit fix 2026-06-05). Rate set
    on the class so it works without a project-level DEFAULT_THROTTLE_RATES."""
    scope = 'renewal_ask'
    rate = '20/min'

RENEWAL_DIR = Path(settings.MEDIA_ROOT) / 'reinsurance-renewal'

# Only these exact names are ever served. Anything else → 404.
ALLOWED = {
    '1.11-Year Historical Treaty Stats - Proportional (000).xlsx',
    '2. 11-Year Historical Treaty Stats - Non Proportional (000).xlsx',
    '3.Auto_FAC_Treaty_Performance 2026.xlsx',
    '4. XOL Claims 2026.xlsx',
    '5.Reinsurance claims Risk Profile 2026.xlsb',
    '6. Alpha Direct Proposed Structure 2026-27.xlsx',
    '2026-27 EPIs and EGNPIs revised on 3rd June 2026.xlsx',
    'Global Re.pdf',
    'MDP Analysis 2425.xlsb',
    'Oman Re Fitch rating Circular.pdf',
    'Oman Re_Corporate Profile (Digital)_compressed.pdf',
    'OmanRe Facultative_Geo Scope, Risk Appetite, Uw Capacity (April 2026).pdf',
    'Reinsurance claims Risk Profile 2026 latest.xlsb',
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def renewal_document(request):
    name = (request.query_params.get('name') or '').strip()
    if name not in ALLOWED:
        return Response({'detail': 'Unknown renewal document.'}, status=404)
    fp = RENEWAL_DIR / name
    if not fp.is_file():
        return Response(
            {'detail': 'Document not yet uploaded to the server.'}, status=404,
        )
    # Audit trail (best-effort — a logging failure must not block the download).
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            table_name='reinsurance_renewal',
            record_id=name[:255],
            action='download',
            user=request.user if request.user.is_authenticated else None,
            ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                        or request.META.get('REMOTE_ADDR') or '')[:45],
            description=f'Renewal pack document downloaded: {name}',
        )
    except Exception:    # noqa: BLE001 — never block the download, but DO record the failure
        log.exception('renewal_document: audit-log write failed for %s', name)
    return FileResponse(open(fp, 'rb'), as_attachment=True, filename=name)


# ---------------------------------------------------------------------------
# "Ask the Renewal AI" — DeepSeek-backed treaty advisor (CFO 2026-06-05)
# ---------------------------------------------------------------------------
# SECURITY / DATA:
#   - Key is read ONLY from env DEEPSEEK_API_KEY (never hardcoded, never logged).
#     If unset, the endpoint degrades gracefully (configured=false).
#   - Only the STRUCTURED renewal figures (already shown on the dashboard) are
#     sent to DeepSeek — never the raw confidential PDFs/workbooks.
RENEWAL_FACTS = (
    "You are a reinsurance treaty advisor for Alpha Direct Insurance (Botswana). "
    "Answer concisely and precisely as a treaty/underwriting expert; if unsure, say so. "
    "2026/27 renewal facts (CCY Pula): General Quota Share, leader Munich Re. "
    "Proposed cession 70% (was 30% in 2025/26); retention 30% (was 70%). "
    "EPI P29m. Ceding commission 35%, profit commission 28.5%, management expense 7.5%. "
    "Max limit P10m per risk, event limit P70m. "
    "Reinsurer panel: Munich Re of Africa 45% (AA- Fitch), GIC Re 20% (A- AM Best), "
    "FM Re 10%, Grand Re 10%, Continental Re 5% (A- AM Best), Kuwait Re 5% (A- AM Best). "
    "11-year commercial-vehicle proportional loss ratios (%): 155.8, 154.0, 103.6, 114.3, "
    "55.7, 45.3, 88.7, 64.8, then 2024/25 55.9, 2025/26 84.2. "
    "Non-motor quota-share EPI P36.2m (100% gross), 30% retained / 70% ceded. "
    "Do not invent figures beyond these."
)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([_RenewalAskThrottle])
def renewal_ask(request):
    import os
    q = (request.data.get('question') or '').strip()
    if not q:
        return Response({'detail': 'Ask a question.'}, status=400)
    key = (os.environ.get('DEEPSEEK_API_KEY') or '').strip()
    if not key:
        return Response({
            'configured': False,
            'answer': ('AI assistant is not configured yet. Set DEEPSEEK_API_KEY in '
                       'the server environment (/etc/alpha-finance/.env) to enable it.'),
        })
    try:
        # Routed through the PII firewall (core.ai_assist.deepseek_complete) so no
        # personal data leaves to DeepSeek in the clear. CFO directive 2026-07-18.
        from core.ai_assist import deepseek_complete
        ans = deepseek_complete(q[:1000], system_prompt=RENEWAL_FACTS,
                                max_tokens=600, timeout=30)
        return Response({'configured': True, 'answer': ans})
    except Exception as e:    # noqa: BLE001
        return Response(
            {'configured': True, 'answer': None,
             'detail': f'AI request failed ({type(e).__name__}). Check the DeepSeek key / connectivity.'},
            status=502,
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def renewal_manifest(request):
    """List which renewal documents are actually present on the server."""
    present = []
    for n in sorted(ALLOWED):
        fp = RENEWAL_DIR / n
        present.append({
            'name': n,
            'available': fp.is_file(),
            'size': fp.stat().st_size if fp.is_file() else 0,
        })
    return Response({'documents': present})
