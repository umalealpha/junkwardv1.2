"""DRF permission — the independence gate.

Read: editors, view-only execs, and platform superusers.
Write (POST/PUT/PATCH/DELETE): the Internal Audit editor roster ONLY.
Superuser status grants read but NOT write, by design (see access.py).
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS

from .access import can_view, is_editor


class InternalAuditAccess(BasePermission):
    message = 'Internal Audit is restricted to the audit function and the exec/board viewers.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if request.method in SAFE_METHODS:
            return can_view(user)
        return is_editor(user)
