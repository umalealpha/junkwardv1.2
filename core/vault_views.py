"""core/vault_views.py — CFO-only Secrets Vault CRUD + audited reveal.

Endpoints (mounted in alpha_finance/api_router.py):
  GET    /api/v1/admin/vault/            list secrets (MASKED — no plaintext)
  POST   /api/v1/admin/vault/            create  {name, category, username?, url?, notes?, secret}
  PATCH  /api/v1/admin/vault/<uuid>/     update fields and/or rotate `secret`
  DELETE /api/v1/admin/vault/<uuid>/     delete
  POST   /api/v1/admin/vault/<uuid>/reveal/  return plaintext ONCE (writes an AuditLog)

Access: CFO authority only — superuser OR is_administrator OR title=CFO
(reuses core.api_key_views._can_manage_keys). The plaintext secret is NEVER
returned by list; only by the explicit reveal endpoint, which is audit-logged.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api_key_views import _can_manage_keys


def _denied():
    return Response(
        {'detail': 'CFO / administrator / superuser only.'},
        status=status.HTTP_403_FORBIDDEN,
    )


def _row(s) -> dict:
    """Masked representation — NEVER includes the plaintext or ciphertext."""
    return {
        'id':               str(s.id),
        'name':             s.name,
        'category':         s.category,
        'category_display': s.get_category_display(),
        'username':         s.username,
        'url':              s.url,
        'notes':            s.notes,
        'has_secret':       bool(s.secret_ciphertext),
        'created_by':       (s.created_by.username if s.created_by_id else None),
        'created_at':       s.created_at.isoformat() if s.created_at else None,
        'updated_at':       s.updated_at.isoformat() if s.updated_at else None,
        'last_revealed_at': s.last_revealed_at.isoformat() if s.last_revealed_at else None,
        'last_revealed_by': (s.last_revealed_by.username if s.last_revealed_by_id else None),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def vault_collection(request):
    if not _can_manage_keys(request.user):
        return _denied()
    from core.models import VaultSecret

    if request.method == 'GET':
        rows = [_row(s) for s in VaultSecret.objects.select_related('created_by', 'last_revealed_by').all()]
        return Response({'secrets': rows, 'count': len(rows)})

    # POST — create
    body   = request.data or {}
    name   = (body.get('name') or '').strip()
    secret = body.get('secret') or ''
    if not name:
        return Response({'detail': 'name is required.'}, status=400)
    if not secret:
        return Response({'detail': 'secret is required.'}, status=400)
    from core.models import VaultSecret
    if VaultSecret.objects.filter(name__iexact=name).exists():
        return Response({'detail': f'A secret named "{name}" already exists.'}, status=400)

    category = (body.get('category') or 'other').strip().lower()
    valid = {c[0] for c in VaultSecret.Category.choices}
    if category not in valid:
        category = 'other'

    obj = VaultSecret(
        name=name, category=category,
        username=(body.get('username') or '').strip(),
        url=(body.get('url') or '').strip(),
        notes=(body.get('notes') or ''),
        created_by=request.user,
    )
    obj.set_secret(str(secret))
    obj.save()
    _audit(request.user, obj, 'create', f'Created vault secret "{obj.name}"')
    return Response(_row(obj), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def vault_detail(request, pk):
    if not _can_manage_keys(request.user):
        return _denied()
    from core.models import VaultSecret
    obj = VaultSecret.objects.filter(pk=pk).first()
    if not obj:
        return Response({'detail': 'Not found.'}, status=404)

    if request.method == 'DELETE':
        name = obj.name
        obj.delete()
        _audit(request.user, None, 'delete', f'Deleted vault secret "{name}"', record_id=str(pk))
        return Response({'id': str(pk), 'deleted': True})

    # PATCH — update fields and/or rotate secret
    body = request.data or {}
    changed = []
    for f in ('username', 'url', 'notes'):
        if f in body:
            setattr(obj, f, (body.get(f) or '').strip() if f != 'notes' else (body.get(f) or ''))
            changed.append(f)
    if 'category' in body:
        category = (body.get('category') or 'other').strip().lower()
        valid = {c[0] for c in VaultSecret.Category.choices}
        obj.category = category if category in valid else 'other'
        changed.append('category')
    if 'name' in body and (body.get('name') or '').strip():
        new_name = body['name'].strip()
        if VaultSecret.objects.filter(name__iexact=new_name).exclude(pk=obj.pk).exists():
            return Response({'detail': f'A secret named "{new_name}" already exists.'}, status=400)
        obj.name = new_name
        changed.append('name')
    if body.get('secret'):
        obj.set_secret(str(body['secret']))
        changed.append('secret(rotated)')
    obj.save()
    _audit(request.user, obj, 'update', f'Updated vault secret "{obj.name}" ({", ".join(changed) or "no change"})')
    return Response(_row(obj))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def vault_reveal(request, pk):
    if not _can_manage_keys(request.user):
        return _denied()
    from core.models import VaultSecret
    obj = VaultSecret.objects.filter(pk=pk).first()
    if not obj:
        return Response({'detail': 'Not found.'}, status=404)
    obj.last_revealed_at = timezone.now()
    obj.last_revealed_by = request.user
    obj.save(update_fields=['last_revealed_at', 'last_revealed_by', 'updated_at'])
    _audit(request.user, obj, 'download', f'Revealed vault secret "{obj.name}"')
    return Response({
        'id':       str(obj.id),
        'name':     obj.name,
        'username': obj.username,
        'secret':   obj.reveal(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vault_qc_get(request, name):
    """The QC agent's one narrow door into the vault (CFO 2026-09-07: "create a
    space in omni secrets for me to put all my credentials so it can pick it from
    there"). Reads ONE secret by name, and ONLY from the ``qc/`` namespace — the
    CFO files them as e.g. ``qc/cloudflare_api_token``. Allowed for the scoped
    'qc-bot' ApiKey and for CFO authority; everything else 403. Every read is
    stamped + audit-logged like a human reveal. HRIS / portal / bank secrets never
    pass through here because their names do not start with ``qc/``."""
    scopes = getattr(getattr(request, 'auth', None), 'allowed_scopes', None) or []
    if 'qc-bot' not in scopes and not _can_manage_keys(request.user):
        return _denied()
    full = f'qc/{name}'
    from core.models import VaultSecret
    obj = VaultSecret.objects.filter(name=full).first()
    if not obj:
        return Response({'detail': f'No vault secret named "{full}". Add it in Omni → Settings → Secrets.'}, status=404)
    obj.last_revealed_at = timezone.now()
    obj.last_revealed_by = request.user
    obj.save(update_fields=['last_revealed_at', 'last_revealed_by', 'updated_at'])
    _audit(request.user, obj, 'download', f'QC agent read vault secret "{obj.name}"')
    return Response({'name': obj.name, 'username': obj.username, 'url': obj.url, 'secret': obj.reveal()})


def _audit(user, obj, action, description, record_id=None):
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            table_name='VaultSecret',
            record_id=record_id or (str(obj.id) if obj else ''),
            action=getattr(AuditLog.Action, action.upper(), action),
            user=user,
            description=description,
        )
    except Exception:  # noqa: BLE001 — audit must never break the operation
        pass
