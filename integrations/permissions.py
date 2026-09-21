"""
integrations/permissions.py — access control for the inbound event store.

SECURITY FIX (2026-08-25, Manus nine-area retest P0): IntegrationEventViewSet
declared no `permission_classes` at all, so it fell back to the project default
(`IsAuthenticated`). That made the whole event pipeline reachable by ANY logged-in
staffer:

  * POST /api/v1/events/            → runs EventProcessor synchronously, which
                                      creates customer invoices, vendor bills and
                                      credit notes with `_system_user()` (the first
                                      superuser) as created_by,
  * GET  /api/v1/events/            → the raw `event_data` payloads,
  * POST /api/v1/events/<id>/retry/ → re-runs the processor.

So an operational user could have booked revenue or a payable under a superuser's
name. Nothing was abused — the table was empty on prod when this was found — but
the hole had to close before Graphite starts pushing for real.

The split enforced here:

  create          → SERVICE CALLERS ONLY. A scoped ApiKey (`graphite-events`, or
                    `admin`). A human session/token is refused outright: an inbound
                    integration event is by definition machine-to-machine, and a
                    person who needs to book an invoice has the invoice screens.
  list / retrieve  → finance administrators (the payloads are the raw feed).
  retry            → finance administrators, OR an event-scoped service key so
                    an integration can re-drive its own failed push. Safe: retry
                    only runs on FAILED/RECEIVED, so a PROCESSED event can never
                    be double-booked, and such a key may create events anyway.

Trust level for the human arm is `core.permissions.is_finance_administrator`
(superuser OR UserProfile.is_administrator OR title == CFO) rather than
`CanViewFinancials`, because retry writes to the ledger.
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission

from core.permissions import is_finance_administrator

# Scopes that may POST an inbound event. `admin` is the existing catch-all.
SERVICE_SCOPES = frozenset({'graphite-events', 'admin'})


def is_service_key_request(request) -> bool:
    """True when this request was authenticated by an ApiKey carrying a scope
    that is allowed to submit inbound events.

    Duck-typed on `allowed_scopes` exactly as `ApiKeyScopePermission` does, so
    no import dance with core.models.
    """
    api_key = getattr(request, 'auth', None)
    if api_key is None or not hasattr(api_key, 'allowed_scopes'):
        return False
    return bool(SERVICE_SCOPES & set(api_key.allowed_scopes or ()))


class IntegrationEventAccess(BasePermission):
    """Per-action gate for IntegrationEventViewSet (see module docstring)."""

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False

        action = getattr(view, 'action', None)

        if action == 'create':
            self.message = (
                'Inbound integration events may only be submitted by an '
                'authorised service key, not by a signed-in user.'
            )
            return is_service_key_request(request)

        # list / retrieve / retry — and anything added later, fail-closed.
        self.message = (
            'The integration event feed is restricted to finance administrators.'
        )
        # A service key with an events scope keeps read access so an integration
        # can confirm what it sent; every other key type is refused here.
        if is_service_key_request(request):
            return True
        return is_finance_administrator(user)
