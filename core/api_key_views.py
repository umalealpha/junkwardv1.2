"""
core/api_key_views.py — admin CRUD for ApiKey.

Endpoints:
  POST   /api/v1/admin/api-keys/        create  (superuser only)
  GET    /api/v1/admin/api-keys/        list    (superuser only)
  DELETE /api/v1/admin/api-keys/<uuid>/ revoke  (superuser only, soft)

The plaintext key is returned ONLY on creation. Subsequent list calls
return only the prefix + label. If the key is lost, revoke + create
a new one.

Body schema for POST:
  {
    "label":           "Manus Automation",
    "allowed_scopes":  ["smart-upload"],
    "service_user":    "manus@alphadirect.co.bw"  (optional — created if missing)
  }
"""

from __future__ import annotations

import secrets

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


_KEY_LEN_HEX = 64   # 32 bytes -> 64 hex chars
_PREFIX_LEN  = 12


def _is_superuser(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


def _can_manage_keys(user) -> bool:
    """CFO-authority gate (the CFO's SSO account is NOT a Django superuser, so a
    strict is_superuser check locked the CFO out of his own key console —
    2026-06-08). Allow superuser OR is_administrator OR title=CFO. Deliberately
    NOT finance_manager — issuing keys is higher-trust than uploading."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser:
        return True
    prof = getattr(user, 'profile', None)
    if not prof:
        return False
    try:
        from core.models import UserProfile
        return bool(prof.is_administrator or prof.title == UserProfile.Title.CFO)
    except Exception:  # noqa: BLE001
        return bool(getattr(prof, 'is_administrator', False))


def _generate_key() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, pbkdf2_hash)."""
    plaintext = secrets.token_hex(_KEY_LEN_HEX // 2)
    prefix    = plaintext[:_PREFIX_LEN]
    hashed    = make_password(plaintext)
    return plaintext, prefix, hashed


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def api_keys_collection(request):
    if not _can_manage_keys(request.user):
        return Response({'detail': 'Superuser required.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.models import ApiKey

    if request.method == 'GET':
        rows = list(
            ApiKey.objects.select_related('service_user', 'created_by')
            .order_by('-created_at')
            .values(
                'id', 'label', 'key_prefix', 'allowed_scopes',
                'is_active', 'last_used_at', 'last_used_ip',
                'created_at',
                'service_user__username', 'service_user__email',
                'created_by__username',
            )
        )
        return Response({'keys': rows, 'count': len(rows)})

    # POST
    body  = request.data or {}
    label = (body.get('label') or '').strip()
    scopes = body.get('allowed_scopes') or []
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        return Response({'detail': 'allowed_scopes must be a list of strings.'}, status=400)
    if not label:
        return Response({'detail': 'label is required.'}, status=400)
    if not scopes:
        return Response({'detail': 'allowed_scopes must contain at least one scope.'}, status=400)

    # Resolve / create service user
    service_user_email = (body.get('service_user') or '').strip().lower()
    if service_user_email:
        sa = User.objects.filter(email__iexact=service_user_email).first()
        if not sa:
            # Auto-create the service account if it doesn't exist yet.
            sa = User.objects.create_user(
                username=service_user_email.split('@')[0],
                email=service_user_email,
                first_name=label.split()[0] if label else 'service',
                last_name='Automation',
                is_active=True,
                is_staff=False,
            )
            sa.set_unusable_password()
            sa.save()
    else:
        sa = request.user   # fall back to the caller

    plaintext, prefix, hashed = _generate_key()
    obj = ApiKey.objects.create(
        label=label,
        key_prefix=prefix,
        key_hash=hashed,
        service_user=sa,
        allowed_scopes=scopes,
        is_active=True,
        created_by=request.user,
    )
    return Response({
        'id':             str(obj.id),
        'label':          obj.label,
        'service_user':   sa.email,
        'allowed_scopes': obj.allowed_scopes,
        'key_prefix':     obj.key_prefix,
        'key':            plaintext,   # ONE-SHOT — caller must store immediately
        'created_at':     obj.created_at.isoformat(),
        'note':           ('Save the `key` value now. It is the only time the '
                           'plaintext is returned by this API. If lost, revoke '
                           'this key and create a new one.'),
    }, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def api_keys_detail(request, pk):
    if not _can_manage_keys(request.user):
        return Response({'detail': 'Superuser required.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.models import ApiKey
    obj = ApiKey.objects.filter(pk=pk).first()
    if not obj:
        return Response({'detail': 'Not found.'}, status=404)
    obj.is_active = False
    obj.save(update_fields=['is_active', 'updated_at'])
    return Response({'id': str(obj.id), 'revoked': True})
