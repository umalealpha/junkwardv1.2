"""core/audit.py

Audit helper for user-administration mutations (CFO directive 2026-06-21).

Before this, several user-admin endpoints (title changes, RBAC role
grant/revoke, company-access grant/bulk, the is_administrator toggle, user
create/deactivate) changed who-can-do-what WITHOUT leaving an audit row, so the
"who changed this person's permissions and when" question could not be answered
from the trail. This helper closes that gap.

It writes a single AuditLog row using the SAME manual pattern as the
ExchangeRate.approve action (core/api_views.py) — actor=request.user, plus the
caller's IP from request.META['REMOTE_ADDR'] (ExchangeRate doesn't capture IP;
user-admin changes are sensitive enough to warrant it). It never mutates
anything but the append-only AuditLog.
"""
from .models import AuditLog


def _client_ip(request):
    """Caller IP from the request, mirroring auth_views / api_key_auth."""
    if request is None:
        return None
    return request.META.get('REMOTE_ADDR')


def log_user_admin_change(
    actor,
    target_user,
    action,
    *,
    old_values=None,
    new_values=None,
    request=None,
    description=None,
    table_name='core.UserProfile',
    record_id=None,
):
    """Write one AuditLog row for a user-administration mutation.

    Args:
        actor:        the Django User who performed the action (request.user).
                      Falls back to request.user when not given.
        target_user:  the Django User whose access/title/role changed. Used to
                      stamp the affected person into new_values and to build a
                      readable default description.
        action:       an AuditLog.Action value (CREATE / UPDATE / DELETE / ...).
        old_values:   dict of prior field values (optional; for UPDATE/DELETE).
        new_values:   dict of changed/new field values (optional). The target
                      user's id + username are merged in automatically so the
                      trail is searchable by person without a join.
        request:      the DRF request, used to capture the actor (if `actor`
                      omitted) and the client IP.
        description:  human-readable summary. Auto-generated from the action +
                      target username when omitted.
        table_name:   the affected table/dot-path (default core.UserProfile).
        record_id:    string id of the affected record. Defaults to the target
                      user's pk.

    Returns the created AuditLog row.
    """
    if actor is None and request is not None:
        actor = getattr(request, 'user', None)

    nv = dict(new_values or {})
    if target_user is not None:
        # Stamp the affected person so the trail is searchable without a join.
        nv.setdefault('user_id', str(target_user.pk))
        nv.setdefault('username', target_user.username)

    if record_id is None and target_user is not None:
        record_id = str(target_user.pk)

    if description is None:
        who = getattr(target_user, 'username', None) or (record_id or '?')
        verb = {
            AuditLog.Action.CREATE: 'Created user-admin record for',
            AuditLog.Action.UPDATE: 'Changed user-admin record for',
            AuditLog.Action.DELETE: 'Removed user-admin record for',
        }.get(action, 'User-admin change for')
        description = f'{verb} {who}'

    return AuditLog.objects.create(
        table_name=table_name,
        record_id=record_id or '',
        action=action,
        old_values=old_values,
        new_values=nv or None,
        user=actor,
        ip_address=_client_ip(request),
        description=description,
    )
