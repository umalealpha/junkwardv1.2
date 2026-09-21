"""
core/audit_reads.py — record READS of sensitive personal data in the immutable
audit trail (DPA audit L-5: the trail logged writes/downloads but not reads).
Best-effort — a logging failure must never break the read. CFO directive 2026-07-19.
"""
from __future__ import annotations


def log_read(user, table_name, record_id, description='', request=None) -> None:
    try:
        from core.models import AuditLog
        ip = None
        if request is not None:
            ip = ((request.META.get('HTTP_X_FORWARDED_FOR', '') or '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR'))
        AuditLog.objects.create(
            table_name=str(table_name)[:100],
            record_id=str(record_id)[:255],
            action=AuditLog.Action.READ,
            user=user if getattr(user, 'is_authenticated', False) else None,
            ip_address=ip,
            description=(str(description)[:500] or f'Read {table_name} {record_id}'),
        )
    except Exception:  # noqa: BLE001 — audit logging must never break a read
        pass
