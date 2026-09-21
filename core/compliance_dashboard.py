"""
core/compliance_dashboard.py — AML / Compliance overview dashboard
(CFO directive 2026-07-24, for the AML officer Kakale Botana).

A private, read-only compliance cockpit for the Compliance & AML function.
It mirrors the Data Protection dashboard (core.dpa_dashboard): a maintained
monitoring register + LIVE tiles read from modules that already exist, and
deep links to those modules instead of rebuilding them.

Reuse, do not duplicate (CFO): KYC + the monthly policy control checks live in
Graphite and are LINKED here; NBFIRA capital adequacy / returns live in the
`regulatory` + `nbfira` apps; the ISO risk & CAPA registers live in
`iso_compliance`; data-privacy breaches live in `core.dpa_breach`. This view
aggregates their status and points the officer at each — no new models.
"""
from __future__ import annotations

import logging
import os

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone

logger = logging.getLogger(__name__)

# Graphite base — KYC + policy control checks live there (CFO: link, don't rebuild).
GRAPHITE_BASE = os.environ.get('GRAPHITE_BASE_URL', 'https://graphite.alphadirect.co.bw')

# ── Access ────────────────────────────────────────────────────────────────────
_DEFAULT_AML_LOCAL_PARTS = {'kbotana'}   # Kakale Botana — AML / Compliance officer


def _aml_local_parts() -> set:
    raw = os.environ.get('OMNI_AML_OFFICER_LOCAL_PARTS')
    if raw:
        return {p.strip().lower() for p in raw.split(',') if p.strip()}
    return _DEFAULT_AML_LOCAL_PARTS


def is_aml_officer(user) -> bool:
    email = (getattr(user, 'email', '') or '').strip().lower()
    return bool(email) and '@' in email and email.split('@', 1)[0] in _aml_local_parts()


def _in_compliance_dept(user) -> bool:
    prof = getattr(user, 'profile', None)
    if not (prof and prof.is_active):
        return False
    dept = (getattr(prof, 'department', '') or '').strip().lower()
    return dept in {'compliance', 'compliance & risk', 'compliance and risk', 'risk'}


def can_view_compliance_dashboard(user) -> bool:
    """AML officer + Compliance & Risk dept + C-suite/HR (#405 lock) + Finance Mgr/Controller + DPO."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if is_aml_officer(user) or _in_compliance_dept(user):
        return True
    from hris.document_access import is_hr_doc_admin          # C-suite + HR + superuser
    if is_hr_doc_admin(user):
        return True
    from core.dpa_dashboard import is_dpo
    if is_dpo(user):
        return True
    from core.models import UserProfile
    prof = getattr(user, 'profile', None)
    return bool(prof and prof.is_active
                and prof.title in {UserProfile.Title.FINANCE_MANAGER,
                                   UserProfile.Title.FINANCIAL_CONTROLLER})


# ── Monitoring register (maintained; the areas Kakale monitors) ────────────────
# source: live (read from Omni) | graphite (lives in Graphite — link) |
#         excel (manual today — systemise later) | manual (periodic check)
_REGISTER = [
    # KYC compliance
    ('KYC', 'KYC by category (MIS / DOM / COM) & broker — compliant vs non-compliant',
     'graphite', 'KYC status per policy is enforced in Graphite (KYC_COMPLIANT flag).'),
    ('KYC', 'KYC compliance for claims registered',
     'graphite', 'Claim KYC gate lives in Graphite.'),
    ('KYC', 'Policies with no KYC documents uploaded',
     'graphite', 'Missing-document exception report in Graphite.'),
    ('KYC', 'High-risk customers KYC compliance',
     'excel', 'Managed on an Excel report today — candidate to systemise.'),
    ('KYC', 'Suppliers / vendors KYC compliance',
     'excel', 'Managed on an Excel report today — vendor master exists in Omni.'),
    ('KYC', 'Intermediaries KYC compliance',
     'excel', 'Managed on an Excel report today.'),
    # Regulatory compliance monitoring
    ('Regulatory', 'Prescribed Capital Target ratio',
     'live', 'NBFIRA capital adequacy — computed in Omni.'),
    ('Regulatory', 'Risk-Based Capital (RBC) ratio',
     'live', 'NBFIRA capital adequacy — computed in Omni.'),
    ('Regulatory', 'NBFIRA license compliance status',
     'manual', 'Licence validity tracked by Compliance.'),
    ('Regulatory', 'BAOA license compliance status',
     'manual', 'Licence validity tracked by Compliance.'),
    ('Regulatory', 'Quarterly returns submission status',
     'live', 'NBFIRA returns module — status per period.'),
    ('Regulatory', 'Quarterly complaints register submission',
     'manual', 'Complaints register submitted quarterly.'),
    ('Regulatory', 'BAOA audit findings remediation',
     'live', 'Tracked as CAPA items (ISO / audit register).'),
    ('Regulatory', 'Baker Tilly audit findings remediation',
     'live', 'Tracked as CAPA items (ISO / audit register).'),
    ('Regulatory', 'Data privacy breaches',
     'live', 'Breach register on the Data Protection dashboard.'),
    # Risk assessment
    ('Risk', 'Medium & High risks remediation overview',
     'live', 'ISO 27005 risk register.'),
    # Unusual transactions
    ('Unusual transactions', 'Monitoring unusual transactions',
     'excel', 'Reviewed by Compliance; exceptions engine can feed this.'),
    ('Unusual transactions', 'Unidentified income',
     'excel', 'Unallocated receipts under review.'),
    ('Unusual transactions', 'Overpayments',
     'excel', 'Overpayment review under Compliance.'),
    # Operations
    ('Operations', "Directors' & Officers' liability insurance",
     'manual', 'Cover in force — renewal tracked.'),
    ('Operations', 'Cyber-security insurance compliance status',
     'manual', 'Cover in force — renewal tracked.'),
    ('Operations', 'Disaster-recovery tests compliance',
     'manual', 'Periodic DR test evidence.'),
    ('Operations', 'Quarterly user-access review reports',
     'manual', 'Access reviews (M365 active-user report available in Omni).'),
    ('Operations', 'Business-continuity simulations',
     'manual', 'Periodic BCP simulation evidence.'),
    # Monthly policy control checks
    ('Monthly policy checks', 'Policyholders under 18 years',
     'graphite', 'Monthly control check run against the policy book (Graphite).'),
    ('Monthly policy checks', 'Duplicate products for the same customer',
     'graphite', 'Monthly control check (Graphite).'),
    ('Monthly policy checks', 'Customers over 65 for Hospital cash-back',
     'graphite', 'Monthly control check (Graphite).'),
    ('Monthly policy checks', 'Multiple simultaneous debits (different channels, same customer)',
     'graphite', 'Monthly control check (Graphite).'),
]

_SOURCE_LABEL = {
    'live': 'Live in Omni', 'graphite': 'In Graphite', 'excel': 'On Excel',
    'manual': 'Periodic check',
}


def _register():
    return [{'group': g, 'item': it, 'source': src, 'note': n,
             'source_label': _SOURCE_LABEL.get(src, src)}
            for (g, it, src, n) in _REGISTER]


def _register_summary(rows):
    by_source = {}
    for r in rows:
        by_source[r['source']] = by_source.get(r['source'], 0) + 1
    return {
        'total_areas': len(rows),
        'live': by_source.get('live', 0),
        'graphite': by_source.get('graphite', 0),
        'excel': by_source.get('excel', 0),
        'manual': by_source.get('manual', 0),
    }


# ── Deep links to the existing modules (reuse, don't duplicate) ────────────────
_LINKS = [
    # KYC + policy control checks live in Graphite
    ('KYC & policy controls', 'KYC compliance (category / broker / claims)',
     f'{GRAPHITE_BASE}', True, 'Opens Graphite — the KYC register and control checks live there.'),
    ('KYC & policy controls', 'Monthly policy control checks',
     f'{GRAPHITE_BASE}', True, 'Under-18 / duplicate product / over-65 cash-back / multiple debits.'),
    # NBFIRA / regulatory — already built in Omni
    ('NBFIRA', 'NBFIRA dashboard', '/compliance/nbfira', False, ''),
    ('NBFIRA', 'Quarterly returns', '/compliance/nbfira/quarterly', False, ''),
    ('NBFIRA', 'Capital adequacy / solvency', '/compliance/nbfira/capital-adequacy', False, ''),
    ('NBFIRA', 'Prudential limits monitor', '/compliance/nbfira/prudential', False, ''),
    ('NBFIRA', 'Submission history & audit trail', '/compliance/nbfira/submissions', False, ''),
    # ISO / audit findings + risk
    ('Risk & audit findings', 'Risk register (Med / High remediation)', '/compliance/iso/risks', False, ''),
    ('Risk & audit findings', 'CAPA register (audit findings remediation)', '/compliance/iso/capas', False, ''),
    ('Risk & audit findings', 'Auditor pack', '/compliance/iso/audit-pack', False, ''),
    # AML/CFT registers (CFO 2026-09-16). These existed as tables from
    # 2026-09-09 but had no page, so there was nowhere to record the work and
    # nothing here to link to — the dashboard claimed to link into each
    # register while four of them had no screen at all.
    ('AML / CFT registers', 'Sanctions & PEP screening register',
     '/compliance/aml/sanctions', False,
     'One row per party screened, with the list and version searched.'),
    ('AML / CFT registers', 'Supplier / vendor screening',
     '/compliance/aml/suppliers', False,
     'Every active vendor, screened or not.'),
    ('AML / CFT registers', 'AML training register',
     '/compliance/aml/training', False,
     'Who has sat the course, and who is outstanding.'),
    ('AML / CFT registers', 'Quarterly report to the Board / EXCO',
     '/compliance/aml/board-reports', False,
     'The pack itself must be attached — a report with no document is not a report.'),
    # Data privacy
    ('Data privacy', 'Data Protection dashboard (breaches / DSR)', '/data-protection', False, ''),
]


def _links():
    return [{'group': g, 'label': lbl, 'href': href, 'external': ext, 'note': n}
            for (g, lbl, href, ext, n) in _LINKS]


# ── Live tiles (read existing modules; status only — no financial recompute) ────
def _adic_company_id():
    """Capital adequacy + returns are per LICENSED INSURER (ADIC), never group —
    matches CapitalCheckView so the tiles can never disagree with the official
    per-entity NBFIRA figure. Returns None if ADIC isn't found (then skip the tile)."""
    try:
        from core.models import Company
        return Company.objects.filter(code='ADIC').values_list('id', flat=True).first()
    except Exception as exc:
        logger.warning('compliance ADIC lookup failed: %s', exc)
        return None


def _regulatory_live():
    out = {'capital': None, 'nbfira_returns': None, 'scope': 'ADIC'}
    adic_id = _adic_company_id()
    if not adic_id:
        return out
    # ADIC capital adequacy — the SAME call CapitalCheckView makes (regulatory
    # /api_views.py: compute_capital_check(as_of, company_id=ADIC)), so this tile
    # can never disagree with the official per-entity figure. Stored snapshots are
    # unscoped/group, so we recompute per-ADIC here rather than read them.
    try:
        from regulatory.services import compute_capital_check
        res = compute_capital_check(timezone.localdate(), company_id=adic_id)
        out['capital'] = {
            'as_of': res.get('as_of'),
            'car_percent': res.get('car_percent'),
            'status': res.get('status'),
        }
    except Exception as exc:
        logger.warning('compliance capital tile failed: %s', exc)
    # NBFIRA returns (ADIC) — quarterly submission status. company is nullable and
    # the create path defaults it to null (treated as ADIC in nbfira/export.py).
    try:
        from django.db.models import Q
        from nbfira.models import NBFIRAReturn
        qs = NBFIRAReturn.objects.filter(Q(company_id=adic_id) | Q(company__isnull=True))
        latest = qs.order_by('-period_end').first()
        submitted = qs.filter(status=NBFIRAReturn.Status.SUBMITTED).count()
        out['nbfira_returns'] = {
            'total': qs.count(),
            'submitted': submitted,
            'not_submitted': qs.exclude(status=NBFIRAReturn.Status.SUBMITTED).count(),
            'latest_period': getattr(latest, 'period_label', None),
            'latest_status': getattr(latest, 'status', None),
        }
    except Exception as exc:
        logger.warning('compliance NBFIRA-returns tile failed: %s', exc)
    return out


def _risk_live():
    try:
        from iso_compliance.models import Risk
        qs = list(Risk.objects.all())
        open_rows = [r for r in qs if r.status == Risk.STATUS_OPEN]
        # Medium & High = score (L×I) >= 8 on the 1–5 scale (matches "Med/High").
        med_high_open = [r for r in open_rows if (r.score or 0) >= 8]
        return {
            'total': len(qs),
            'open': len(open_rows),
            'med_high_open': len(med_high_open),
            'mitigated': sum(1 for r in qs if r.status == Risk.STATUS_MITIGATED),
            'closed': sum(1 for r in qs if r.status == Risk.STATUS_CLOSED),
        }
    except Exception as exc:
        logger.warning('compliance risk tile failed: %s', exc)
        return None


def _capa_live():
    from django.utils import timezone
    try:
        from iso_compliance.models import CAPA
        qs = list(CAPA.objects.all())
        today = timezone.localdate()
        open_states = {CAPA.STATUS_OPEN, CAPA.STATUS_IN_PROGRESS, CAPA.STATUS_VERIFICATION}
        open_rows = [c for c in qs if c.status in open_states]
        overdue = sum(1 for c in open_rows if c.due_date and c.due_date < today)
        return {
            'total': len(qs),
            'open': len(open_rows),
            'overdue': overdue,
            'closed': sum(1 for c in qs if c.status == CAPA.STATUS_CLOSED),
        }
    except Exception as exc:
        logger.warning('compliance CAPA tile failed: %s', exc)
        return None


def _breach_live():
    try:
        from core.dpa_breach import breach_summary
        return breach_summary()
    except Exception as exc:
        logger.warning('compliance breach tile failed: %s', exc)
        return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def compliance_dashboard(request):
    from django.utils import timezone
    if not can_view_compliance_dashboard(request.user):
        return Response({'detail': 'The Compliance / AML dashboard is restricted to the '
                                   'AML officer, Compliance & Risk, C-suite, HR and Finance.'},
                        status=403)
    register = _register()
    from core.compliance_brain import latest_summary
    return Response({
        'summary': _register_summary(register),
        'register': register,
        'brain': latest_summary(),          # nightly Alpha Brain feed + AI read
        'regulatory': _regulatory_live(),
        'risk': _risk_live(),
        'capa': _capa_live(),
        'breach': _breach_live(),
        'links': _links(),
        'officer': {'name': 'Kakale Botana', 'email': 'kbotana@alphadirect.co.bw'},
        'as_of': timezone.localdate().isoformat(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def compliance_access(request):
    """Cheap gate check for the FE (mirrors /dpa-dashboard/access/)."""
    return Response({'allowed': can_view_compliance_dashboard(request.user)})
