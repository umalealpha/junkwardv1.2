"""
core/retention.py — data-retention policy + past-retention REPORT (DPA audit H-4).

v1 is REPORT-ONLY: it shows how many records sit past their keep-date, per data
class, so the DPO can see the exposure. It does NOT delete anything — a destructive
purge on live payroll/claims/KYC must be a deliberate, signed-off, staged step
(same caution as field-level encryption). CFO directive 2026-07-19.
"""
from __future__ import annotations

# (model label, date field, keep-years). Best-effort — a missing model/field is
# skipped, never raises. Years are policy defaults; confirm with the DPO/legal.
RETENTION = [
    ('payroll.Payslip',       'created_at', 6),
    ('billing.Invoice',       'created_at', 7),
    ('procurement.PurchaseOrder', 'created_at', 7),
    ('hris.LeaveRequest',     'created_at', 3),
    ('core.AuditLog',         'created_at', 7),
    ('rewards.PointsTransaction', 'created_at', 5),
    # Candidate CVs are PII on local disk. 6-month advert window; hired kept
    # longer (their CV joins the employee file). The purge_old_cvs command
    # enforces the hired-exemption — this line is the DPO exposure read only.
    ('recruitment.Candidate',     'created_at', 0.5),
]


def retention_report() -> dict:
    from django.apps import apps
    from django.utils import timezone
    from datetime import timedelta
    now = timezone.now()
    classes, total_past = [], 0
    for label, field, years in RETENTION:
        try:
            model = apps.get_model(label)
            cutoff = now - timedelta(days=365 * years)
            n = model.objects.filter(**{f'{field}__lt': cutoff}).count()
            total_past += n
            classes.append({'model': label, 'field': field, 'years': years, 'past_count': n})
        except Exception as e:  # noqa: BLE001 — missing model/field just skipped
            classes.append({'model': label, 'years': years, 'error': str(e)[:80]})
    return {'total_past_retention': total_past, 'classes': classes,
            'note': 'Report only — no records are deleted. Purge is a separate signed-off step.'}


def retention_summary() -> dict:
    r = retention_report()
    return {'total_past_retention': r['total_past_retention'], 'classes_tracked': len(RETENTION)}
