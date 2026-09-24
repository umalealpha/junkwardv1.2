"""core/vault_views.py — CFO-only Secrets Vault CRUD + audited reveal.

Endpoints (mounted in alpha_finance/api_router.py):
  GET    /api/v1/admin/vault/            list secrets (MASKED — no plaintext)
  POST   /api/v1/admin/vault/            create  {name, category, username?, url?, notes?, secret}
  PATCH  /api/v1/admin/vault/<uuid>/     update fields and/or rotate `secret`
  DELETE /api/v1/admin/vault/<uuid>/     delete
  POST   /api/v1/admin/vault/<uuid>/reveal/  return plaintext ONCE (writes an AuditLog)
  POST   /api/v1/admin/vault/<uuid>/share/   mint a ONE-TIME hand-over link {recipient?, hours?}
  GET    /api/v1/vault-share/<token>/        public page with a 'Show once' button (nothing revealed)
  POST   /api/v1/vault-share/<token>/        reveal ONCE + kill the link (HTML page)

Access: CFO authority only — superuser OR is_administrator OR title=CFO
(reuses core.api_key_views._can_manage_keys). The plaintext secret is NEVER
returned by list; only by the explicit reveal endpoint, which is audit-logged.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
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



# ── One-time hand-over link (CFO 2026-09-21) ─────────────────────────────────
SHARE_DEFAULT_HOURS = 24
SHARE_MAX_HOURS     = 72


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def vault_share(request, pk):
    """Mint a one-time link for this secret. CFO authority only. The token is
    returned exactly once here and only its hash is stored."""
    if not _can_manage_keys(request.user):
        return _denied()
    import secrets as _secrets
    from datetime import timedelta

    from django.conf import settings

    from core.models import VaultSecret, VaultShareLink
    obj = VaultSecret.objects.filter(pk=pk).first()
    if not obj:
        return Response({'detail': 'Not found.'}, status=404)
    body = request.data or {}
    try:
        hours = int(body.get('hours') or SHARE_DEFAULT_HOURS)
    except (TypeError, ValueError):
        hours = SHARE_DEFAULT_HOURS
    hours = max(1, min(hours, SHARE_MAX_HOURS))
    token = _secrets.token_urlsafe(32)
    link = VaultShareLink.objects.create(
        secret=obj, token_hash=VaultShareLink.hash_token(token),
        recipient=(body.get('recipient') or '').strip()[:200],
        created_by=request.user,
        expires_at=timezone.now() + timedelta(hours=hours),
    )
    _audit(request.user, obj, 'create',
           f'Minted one-time share link for vault secret "{obj.name}" '
           f'(recipient: {link.recipient or "unspecified"}, {hours}h)')
    base = getattr(settings, 'PUBLIC_BASE_URL', '').rstrip('/')
    return Response({
        'url':        f'{base}/api/v1/vault-share/{token}/',
        'expires_at': link.expires_at.isoformat(),
        'recipient':  link.recipient,
    }, status=status.HTTP_201_CREATED)


_SHARE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Alpha Direct - one-time secret</title>
<style>body{font-family:'Book Antiqua',Palatino,Georgia,serif;background:#F6F7F9;color:#0D1B2A;margin:0;padding:32px 16px}
.card{max-width:560px;margin:0 auto;background:#fff;border-top:4px solid #F4A623;border-radius:8px;padding:28px 28px 24px;box-shadow:0 2px 10px rgba(13,27,42,.08)}
h1{font-size:20px;margin:0 0 6px}p{line-height:1.5;margin:8px 0}.muted{color:#6B7280;font-size:13px}
button{background:#0D1B2A;color:#fff;border:0;border-radius:6px;padding:12px 18px;font-size:15px;cursor:pointer;margin-top:10px}
.secret{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:15px;background:#F1F5F9;border:1px solid #E5E7EB;border-radius:6px;padding:14px;word-break:break-all;user-select:all;margin-top:12px}
.warn{color:#B91C1C}</style></head><body><div class="card">%s
<p class="muted" style="margin-top:22px">Alpha Direct Insurance Company (Pty) Ltd - Omni Secrets Vault</p></div></body></html>"""


def _share_page(inner: str, code: int = 200) -> HttpResponse:
    resp = HttpResponse(_SHARE_PAGE % inner, status=code, content_type='text/html; charset=utf-8')
    resp['Cache-Control'] = 'no-store, max-age=0'
    resp['Referrer-Policy'] = 'no-referrer'
    resp['X-Robots-Tag'] = 'noindex, nofollow'
    return resp


_DEAD = ('<h1>This link has already been used or has expired.</h1>'
         '<p>A one-time link only opens once. Ask the sender for a new one.</p>')


@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([])   # no session auth: DRF's SessionAuthentication would
                              # enforce CSRF on a recipient who is signed into Omni
@permission_classes([AllowAny])
def vault_share_open(request, token):
    """The recipient's page. GET only shows a button (so a mail scanner's
    pre-fetch cannot burn the link). POST reveals the secret exactly once,
    inside one database lock, and the link is dead from then on."""
    from django.db import transaction

    from core.models import VaultShareLink
    h = VaultShareLink.hash_token(token)
    if request.method == 'GET':
        link = VaultShareLink.objects.filter(token_hash=h).select_related('secret').first()
        if not link:
            return _share_page('<h1>This link is not valid.</h1><p>Check it was copied in full, or ask the sender for a new one.</p>', 404)
        if not link.is_live():
            return _share_page(_DEAD, 410)
        when = timezone.localtime(link.expires_at).strftime('%d %b %Y %H:%M')
        return _share_page(
            f'<h1>{escape(link.secret.name)}</h1>'
            '<p>This is a one-time hand-over from the Alpha Direct Secrets Vault. '
            'The value is shown <strong>once</strong>. Have a safe place ready to paste it, then press the button.</p>'
            '<form method="post"><button type="submit">Show it once</button></form>'
            f'<p class="muted">Link valid until {when} (Botswana time).</p>')

    with transaction.atomic():
        link = (VaultShareLink.objects.select_for_update()
                .filter(token_hash=h).select_related('secret').first())
        if not link:
            return _share_page('<h1>This link is not valid.</h1>', 404)
        if not link.is_live():
            return _share_page(_DEAD, 410)
        sec = link.secret
        value = sec.reveal()
        if not value:
            # Nothing to show (empty or undecryptable). Never spend the one
            # chance on an empty box: leave the link live and say so.
            transaction.set_rollback(True)
            return _share_page('<h1>The value could not be produced.</h1>'
                               '<p>The link is still valid. Ask the sender to check the vault entry.</p>', 500)
        ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()[:64]
        link.opened_at = timezone.now()
        link.opened_from = ip
        link.save(update_fields=['opened_at', 'opened_from', 'updated_at'])
        sec.last_revealed_at = link.opened_at
        sec.save(update_fields=['last_revealed_at', 'updated_at'])
    _audit(None, sec, 'download',
           f'One-time share link OPENED for vault secret "{sec.name}" '
           f'(recipient: {link.recipient or "unspecified"}, from {ip or "unknown"})')
    return _share_page(
        f'<h1>{escape(sec.name)}</h1>'
        '<p class="warn"><strong>Shown once.</strong> This link is now dead. Copy the value now.</p>'
        f'<div class="secret">{escape(value)}</div>'
        f'<p class="muted">{len(value)} characters. Do not forward it by email or chat.</p>')

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
