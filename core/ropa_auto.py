"""
core/ropa_auto.py — self-updating ROPA (CFO 2026-07-24, for DPO Oratile).

The Records of Processing Activities shouldn't be kept by hand. This builds the
register FROM the live system: each processing activity Omni actually runs
(core.ropa.ROPA — the curated system knowledge) is enriched with a LIVE signal
(a real record count from the module that holds the data) and reconciled against
the DPO's uploaded "Validated ROPA" sheet, so the dashboard flags DRIFT —
activities the system is doing that aren't yet recorded in her register.

Robust by design: every live counter is wrapped — a missing/renamed model yields
no number (never a crash). Aggregate counts only; no personal data leaves here.
"""
from __future__ import annotations

import logging

from core.ropa import ROPA, ROPA_VERSION

log = logging.getLogger(__name__)

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.dpa_dashboard import can_view_dpa_dashboard


# activity key -> keywords that identify it in the DPO's free-text ROPA rows.
# Rows are matched as whole-row blobs padded with spaces both ends, so short
# keywords are two-sided (' hr ', ' ai ') to avoid substring hits ("AWS Mumbai"
# contains "ai "). Ambiguous words are dropped: 'driver' (→"driver's licence" in
# KYC rows), 'policy' (→"policyholder"/"privacy policy"). Fable K7, 2026-07-24.
_MATCH = {
    'payroll':      ('payroll', ' hr ', 'human resource', 'employee', 'salary', 'staff'),
    'underwriting': ('underwrit', 'premium'),
    'claims':       ('claim',),
    'kyc':          ('kyc', 'identity', 'omang', 'know your', 'verification'),
    'telematics':   ('telematic', 'nexus', 'trip', 'tracking'),
    'marketing':    ('marketing', 'rewards', 'loyalty'),
    'ai':           (' ai ', 'artificial', 'aware', 'automated'),
}


def _count(fn):
    """Run a live counter; any failure (missing model, DB error) -> None.
    Degrades gracefully (a bad counter never breaks the register) but LOGS so a
    real regression isn't hidden."""
    try:
        return int(fn())
    except Exception as exc:  # noqa: BLE001
        log.warning('ropa_auto live counter failed: %s', exc)
        return None


def _live_counts() -> dict:
    """Per-activity live signal from the real modules. Each wrapped — a bad
    mapping degrades to None, never breaks the register."""
    counts: dict = {}

    def emp():
        from payroll.models import Employee
        return Employee.objects.count()

    def customers():
        from billing.models import Contact
        return Contact.objects.filter(contact_type='customer').count()

    def ai30():
        from datetime import timedelta
        from django.utils import timezone
        from core.models import AISpeedLog
        return AISpeedLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=30)).count()

    counts['payroll'] = {'n': _count(emp), 'label': 'employees on payroll'}
    counts['kyc'] = {'n': _count(customers), 'label': 'customer records'}
    counts['underwriting'] = {'n': _count(customers), 'label': 'customer records'}
    counts['ai'] = {'n': _count(ai30), 'label': 'AI calls (30d)'}
    return counts


def _validated_activities() -> tuple[list[str], str | None]:
    """The activity/purpose text of every row in the DPO's latest uploaded
    Validated ROPA — used to tell which system activities she has recorded."""
    try:
        from core.models import DpoWorkbook
        wb = DpoWorkbook.objects.first()
        if not wb:
            return [], None
        sheet = (wb.sheets or {}).get('Validated ROPA') or {}
        rows = sheet.get('rows', []) if sheet.get('type') == 'table' else []
        blob = []
        for r in rows:
            # pad both ends so two-sided keywords (' hr ', ' ai ') match at row edges too
            blob.append(' ' + ' '.join(str(v) for v in r.values()).lower() + ' ')
        return blob, wb.created_at.isoformat() if wb.created_at else None
    except Exception as exc:  # noqa: BLE001
        log.warning('ropa_auto could not read validated ROPA: %s', exc)
        return [], None


def build_live_ropa() -> dict:
    counts = _live_counts()
    validated_blobs, wb_at = _validated_activities()

    def is_recorded(key: str) -> bool:
        kws = _MATCH.get(key, (key,))
        return any(any(k in blob for k in kws) for blob in validated_blobs)

    activities = []
    needs = 0
    for a in ROPA:
        key = a['key']
        live = counts.get(key, {})
        recorded = is_recorded(key) if validated_blobs else None
        if recorded is False:
            needs += 1
        activities.append({
            'key': key,
            'activity': a['activity'],
            'basis': a['basis'],
            'data': a['data'],
            'recipients': a['recipients'],
            'retention': a['retention'],
            'transfers': a['transfers'],
            'live_count': live.get('n'),
            'live_label': live.get('label'),
            'recorded': recorded,          # True=in her ROPA, False=drift, None=no workbook yet
        })
    return {
        'version': ROPA_VERSION,
        'workbook_uploaded_at': wb_at,
        'has_validated_ropa': bool(validated_blobs),
        'summary': {
            'total': len(activities),
            'recorded': sum(1 for a in activities if a['recorded'] is True),
            'needs_recording': needs,
        },
        'activities': activities,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ropa_auto(request):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted to the DPO, C-suite, HR and Finance.'}, status=403)
    return Response(build_live_ropa())
