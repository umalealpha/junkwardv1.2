"""
payroll/assistant.py — the Omni Payroll Assistant (CFO 2026-07-14: "make the
whole payroll process seamless and fun").

A natural-language helper on the Payroll Dashboard. The payroll team asks things
like "what still needs finishing?" or "is ADIC up to date?" and gets a short,
friendly, action-oriented answer routed through core.ai_assist.reasoning_complete
(Gemini → OpenAI → DeepSeek → … per the CFO's failover chain).

PRIVACY: the model is given ONLY a completeness snapshot — per (period, company)
counts of payslips vs how many carry a full component breakdown. No names, no
salaries, no bank details. The snapshot is also run through is_safe_for_ai() as a
belt-and-braces guard before it leaves the building.
"""
from __future__ import annotations

import json

from django.db.models import Count, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .amendment_views import user_can_view_payroll
from .models import Payslip

SYSTEM = (
    "You are the Omni Payroll Assistant for Alpha Direct (a Botswana insurer). You help "
    "the payroll team get every monthly payroll run complete and reconciled in Omni, and "
    "you make the job feel easy. You are given a JSON snapshot of payroll COMPLETENESS per "
    "run — each entry is a period + company with: payslips, with_components (full breakdown), "
    "and totals_only (no per-component breakdown yet). "
    "Answer the user's question in plain, warm, concrete English. Use ONLY the snapshot — "
    "never invent numbers or names. When a run is 'totals only', the one-step fix is: "
    "Payroll Dashboard → Component completeness → Upload register (it previews how many tie, "
    "then loads — it never changes an approved total). Be specific about which runs need it. "
    "Keep it under 130 words. A little Setswana warmth is welcome (e.g. 'sharp sharp') but "
    "don't overdo it. End with the single most useful next step."
)

# Company code → friendly name, so the assistant can speak in names not codes.
NAMES = {
    'ADIC': 'Alpha Direct Insurance', 'ADRG': 'ADRisk Global', 'UNI': 'Unicoin',
    'RSA': 'Risk Software Africa', 'QIH': 'Quantum Insurance Holdings',
    'VCM': 'Veritas Capital', 'ADIL': 'Alpha Direct Life', 'ADIPL': 'Alpha Direct Insurtech',
    'ADSA': 'Alpha Direct South Africa', 'AIZ': 'Alpha Insurtech Zambia',
    'GCX': 'Gaborone Coin Exchange',
}


def _snapshot(limit: int = 120) -> list[dict]:
    agg = (Payslip.objects
           .values('period__period_name', 'company__code')
           .annotate(total=Count('id', distinct=True),
                     full=Count('id', filter=Q(lines__id__isnull=False), distinct=True))
           .order_by('-period__period_name', 'company__code'))
    rows = []
    for a in agg:
        total = a['total'] or 0
        full = a['full'] or 0
        rows.append({
            'period': a['period__period_name'],
            'company': a['company__code'],
            'company_name': NAMES.get(a['company__code'], a['company__code']),
            'payslips': total, 'with_components': full, 'totals_only': total - full,
        })
    return rows[:limit]


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payroll_assistant(request):
    """POST /api/v1/payroll/assistant/  {question} → {answer}. Payroll-view gated.
    Only a PII-free completeness snapshot is sent to the model."""
    if not user_can_view_payroll(request.user):
        raise PermissionDenied('Payroll data is restricted to HR / Finance / payroll administrators.')
    question = (request.data.get('question') or '').strip()
    if not question:
        return Response({'detail': 'Ask a question about your payroll runs.'}, status=400)
    if len(question) > 500:
        question = question[:500]

    snap = _snapshot()
    context = json.dumps(snap, separators=(',', ':'))

    # Belt-and-braces: the snapshot is counts only, but scrub before it leaves.
    try:
        from core.ai_assist import is_safe_for_ai
        report = is_safe_for_ai(context + ' ' + question)
        if not report.safe:
            return Response({'answer': "I can't answer that — it looked like it contained "
                             "personal data (ID / account / medical). Ask about run "
                             "completeness instead, e.g. 'what still needs finishing?'",
                             'runs': len(snap)})
    except Exception:  # noqa: BLE001 — never let the guard's own failure block a safe call
        pass

    prompt = (f"Payroll completeness snapshot (JSON, newest first):\n{context}\n\n"
              f"Payroll team member asks: {question}")
    try:
        from core.ai_assist import reasoning_complete
        answer = reasoning_complete(prompt, system_prompt=SYSTEM, max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        return Response({'detail': f'The assistant is unavailable right now: {e}'}, status=503)
    return Response({'answer': (answer or '').strip(), 'runs': len(snap)})
