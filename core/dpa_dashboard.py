"""
core/dpa_dashboard.py — Data Protection dashboard (CFO directive 2026-07-19).

A private, read-only compliance view for the DPO (Oratile Tlhomelang) + C-suite +
HR + the Finance Manager: the DPA audit scorecard (live status), proof the AI PII
shield is working (from the speed log), and the cross-border data map.

Access reuses the confidential-access lock from PR #405 (pinned by account, not
title, so it can't drift) and adds the DPO + Finance Manager. No new models — v1
reads existing data (AISpeedLog) + a maintained findings register.
"""
from __future__ import annotations

import os

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

# ── Access ────────────────────────────────────────────────────────────────────
_DEFAULT_DPO_LOCAL_PARTS = {'otlhomelang'}   # Oratile Tlhomelang — DPO (CFO 2026-07-19)


def _dpo_local_parts() -> set:
    raw = os.environ.get('OMNI_DPA_DPO_LOCAL_PARTS')
    if raw:
        return {p.strip().lower() for p in raw.split(',') if p.strip()}
    return _DEFAULT_DPO_LOCAL_PARTS


def is_dpo(user) -> bool:
    email = (getattr(user, 'email', '') or '').strip().lower()
    return bool(email) and '@' in email and email.split('@', 1)[0] in _dpo_local_parts()


_DEFAULT_COMPLIANCE_LOCAL_PARTS = {'kbotana'}   # Kakale Botana — Compliance Officer


def _compliance_local_parts() -> set:
    raw = os.environ.get('OMNI_DPA_COMPLIANCE_LOCAL_PARTS')
    if raw:
        return {p.strip().lower() for p in raw.split(',') if p.strip()}
    return _DEFAULT_COMPLIANCE_LOCAL_PARTS


def is_compliance_officer(user) -> bool:
    """The Compliance Officer who signs the compliance slot on a DPIA (CFO
    2026-08-18). Same email-local-part model as is_dpo, env-overridable."""
    email = (getattr(user, 'email', '') or '').strip().lower()
    return (bool(email) and '@' in email
            and email.split('@', 1)[0] in _compliance_local_parts())


def can_view_dpa_dashboard(user) -> bool:
    """DPO + C-suite + HR (via the #405 lock) + Finance Manager."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from hris.document_access import is_hr_doc_admin          # C-suite + HR + superuser
    if is_hr_doc_admin(user) or is_dpo(user):
        return True
    from core.models import UserProfile
    prof = getattr(user, 'profile', None)
    return bool(prof and prof.is_active
                and prof.title in {UserProfile.Title.FINANCE_MANAGER,
                                   UserProfile.Title.FINANCIAL_CONTROLLER})


# ── Audit findings register (maintained; status is honest/live) ────────────────
# status: fixed | in_progress | open | pass
_FINDINGS = [
    ('S-1', 'severe', 'No Data Protection Impact Assessment (DPIA)', 'open',
     'Commission a DPIA module by module. People/legal task — I can draft the template.'),
    ('S-2', 'severe', 'No documented lawful basis / processing register (ROPA)', 'open',
     'Map each activity → basis (payroll = contract + legal; policyholder = contract).'),
    ('S-3', 'severe', 'Genuine privacy notice', 'in_progress',
     'Staff login notice is live; unbundle from the HR undertaking + add a policyholder notice.'),
    ('S-4', 'severe', 'Customer KYC/PII sent to AI unmasked (cross-border)', 'fixed',
     'PII firewall LIVE in tokenize — personal data is tokenised before every external-LLM call.'),
    ('S-5', 'severe', 'Sensitive PII in plain text (no encryption at rest)', 'open',
     'Field-level encryption for Omang/bank/passport + KMS-encrypted DB volume & backups.'),
    ('S-6', 'severe', 'No 72-hour breach-notification process', 'open',
     'Breach register + SOP driving the 72-hour Commission + data-subject notice. Buildable next.'),
    ('H-1', 'high', 'DPO appointed + registered; ROPA', 'in_progress',
     'DPO appointed (Oratile Tlhomelang). IDPC registration + ROPA still outstanding.'),
    ('H-2', 'high', 'Full Part VIII transparency content', 'open',
     'Add controller identity, recipients, retention, transfers, full rights, right to complain.'),
    ('H-3', 'high', 'Data-subject rights workflow', 'open',
     'Access / rectification / portability / objection + a request log with the statutory clock.'),
    ('H-4', 'high', 'Retention not enforced', 'open',
     'Enforced purge/anonymise per data class. Buildable next.'),
    ('H-5', 'high', 'AI scrubber coverage', 'fixed',
     'PII firewall now wraps every external engine (DeepSeek/Gemini/OpenAI/Anthropic/Grok).'),
    ('H-6', 'high', 'Cross-border transfers / processors undocumented', 'in_progress',
     'Processor-DPA register now LIVE (signable in-app). First agreement issued to '
     'ADRisk / The Risk Co (India) for the Alpha Brain extracts. Still to paper: '
     'WebFleet, Time Doctor, Microsoft, RealPay.'),
    ('H-7', 'high', 'Latent raw file.url in some APIs', 'open',
     'Verified NOT live-exploitable (DEBUG off, no /media route); close the anti-pattern.'),
    ('H-8', 'high', 'Vault key derived from SECRET_KEY', 'open',
     'Set a dedicated VAULT_FERNET_KEY + re-encrypt.'),
    ('H-9', 'high', 'Biometric face-match wired to EU/US (OFF now)', 'open',
     'Gate behind DPIA + consent before any activation. Currently disabled.'),
    ('L-1', 'low', 'Full bank account shown over the API', 'fixed', 'Masked to last-4 (2026-07-19).'),
    ('L-2', 'low', 'Secrets/tokens in URLs', 'fixed', 'Time Doctor is header-first; token-in-URL only when the API forces it.'),
    ('L-3', 'low', 'HR document per-document ownership', 'fixed', 'Enforced via can_access_hr_document.'),
    ('L-4', 'low', 'No forced DB transport TLS', 'fixed', "sslmode configurable (default 'prefer'); set require on RDS move."),
    ('L-5', 'low', 'Audit trail logs writes but not reads', 'open', 'Add read auditing + audit the customer identity model.'),
    ('P-1', 'pass', 'No liability-exclusion clause (the DPSM trap)', 'pass',
     'Genuine strength — a codebase search found none. Never add one.'),
]


def _findings():
    return [{'id': i, 'severity': sev, 'title': t, 'status': s, 'note': n}
            for (i, sev, t, s, n) in _FINDINGS]


def _summary(findings):
    by_status, by_sev_open = {}, {}
    for f in findings:
        by_status[f['status']] = by_status.get(f['status'], 0) + 1
        if f['status'] in ('open', 'in_progress') and f['severity'] != 'pass':
            by_sev_open[f['severity']] = by_sev_open.get(f['severity'], 0) + 1
    total = len([f for f in findings if f['severity'] != 'pass'])
    done = len([f for f in findings if f['status'] == 'fixed'])
    return {
        'total_findings': total,
        'fixed': done,
        'in_progress': by_status.get('in_progress', 0),
        'open': by_status.get('open', 0),
        'pct_fixed': round(100 * done / total) if total else 0,
        'open_by_severity': by_sev_open,
    }


# ── Cross-border data map ──────────────────────────────────────────────────────
_DATA_MAP = [
    ('Omni hosting', 'AWS Cape Town (af-south-1)', 'South Africa', 'adequate',
     'SA is on Botswana’s adequacy list. Transfer agreement pending (H-6).'),
    ('AI — cheap tier', 'DeepSeek', 'China', 'shielded',
     'PII tokenised before it leaves (firewall). To be cut once the local AI server is live.'),
    ('AI — backup tier', 'Google Gemini', 'USA', 'shielded', 'PII tokenised before it leaves (firewall).'),
    ('AI — premium tier', 'OpenAI / Anthropic / Grok', 'USA', 'shielded', 'PII tokenised before it leaves (firewall).'),
    ('Fleet telematics', 'WebFleet', 'EU', 'review', 'Processor DPA to document (H-6).'),
    ('Time tracking', 'Time Doctor', 'USA', 'review', 'Processor DPA to document (H-6).'),
    ('Email / files', 'Microsoft 365', 'EU/USA', 'review', 'Processor DPA to document (H-6).'),
    ('Payments', 'RealPay', 'South Africa', 'adequate', 'SA adequate; DPA to document.'),
]


def _data_map():
    return [{'purpose': p, 'processor': proc, 'location': loc, 'status': st, 'note': n}
            for (p, proc, loc, st, n) in _DATA_MAP]


# ── AI shield stats (live, from the speed log) ─────────────────────────────────
def _ai_shield():
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Count, Avg
    from core.pii_firewall import current_mode
    from core.models import AISpeedLog

    since = timezone.now() - timedelta(days=30)
    qs = AISpeedLog.objects.filter(created_at__gte=since)
    total = qs.count()
    by_engine = list(qs.values('engine').annotate(n=Count('id')).order_by('-n'))
    avg_ms = qs.aggregate(a=Avg('ms'))['a'] or 0
    return {
        'firewall_mode': current_mode(),          # 'tokenize' = personal data replaced before it leaves
        'protected': current_mode() in ('tokenize', 'block'),
        'ai_calls_30d': total,
        'avg_ms': round(avg_ms),
        'by_engine': by_engine,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dpa_dashboard(request):
    from django.utils import timezone
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Data Protection dashboard is restricted to the DPO, '
                                   'C-suite, HR and the Finance Manager.'}, status=403)
    findings = _findings()
    from core.dpa_breach import breach_summary
    from core.retention import retention_summary
    from core.dpa_dsr import dsr_summary
    from core.dpia import dpia_summary, DPIAS
    from core.ropa import ropa_summary, ROPA
    from core.processor_dpa import processor_dpa_register
    return Response({
        'summary': _summary(findings),
        'findings': findings,
        'ai_shield': _ai_shield(),
        'data_map': _data_map(),
        'processor_dpas': processor_dpa_register(),
        'breach': breach_summary(),
        'retention': retention_summary(),
        'dsr': dsr_summary(),
        'dpia': {'summary': dpia_summary(), 'items': DPIAS},
        'ropa': {'summary': ropa_summary(), 'items': ROPA},
        'dpo': {'name': 'Oratile Tlhomelang', 'email': 'otlhomelang@alphadirect.co.bw'},
        'as_of': timezone.localdate().isoformat(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dpa_access(request):
    """Cheap gate check for the FE (mirrors /aware/access/)."""
    return Response({'allowed': can_view_dpa_dashboard(request.user)})
