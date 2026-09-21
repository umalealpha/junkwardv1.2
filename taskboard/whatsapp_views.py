"""
taskboard/whatsapp_views.py — CFO-only WhatsApp reminder console (CFO 2026-07-14).

Endpoints (mounted in alpha_finance/api_router.py). ALL gated by CFO authority —
reuses core.api_key_views._can_manage_keys (superuser OR is_administrator OR
title=CFO), the same gate as the Secrets Vault. Staff phone numbers are therefore
visible ONLY to the CFO.

  GET    /api/v1/whatsapp/config/                 is the API key set? (no secret returned)
  GET    /api/v1/whatsapp/contacts/               list the CFO phonebook
  POST   /api/v1/whatsapp/contacts/               add one {name,phone,role,is_manager,notes}
                                                   or bulk {paste:"Name +267...\\n..."}
  PATCH  /api/v1/whatsapp/contacts/<uuid>/        edit a contact
  DELETE /api/v1/whatsapp/contacts/<uuid>/        remove a contact
  GET    /api/v1/whatsapp/overdue/                overdue people + suggested reminder
  POST   /api/v1/whatsapp/send/                   send {contact_ids?,phones?,message,task_id?}
  GET    /api/v1/whatsapp/log/                     recent sent/attempted reminders
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api_key_views import _can_manage_keys
from taskboard import whatsapp_service as wa


def _denied():
    return Response(
        {'detail': 'CFO / administrator / superuser only. Your staff phone list '
                   'is private to you.'},
        status=status.HTTP_403_FORBIDDEN,
    )


def _contact_row(c) -> dict:
    return {
        'id':         str(c.id),
        'name':       c.name,
        'phone':      c.phone,
        'role':       c.role,
        'is_manager': c.is_manager,
        'active':     c.active,
        'notes':      c.notes,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def whatsapp_config(request):
    if not _can_manage_keys(request.user):
        return _denied()
    from core.ai_assist import get_llm_key
    token_set = bool(get_llm_key('WHATSAPP_TOKEN'))
    phone_set = bool(get_llm_key('WHATSAPP_PHONE_ID'))
    waba_set = bool(get_llm_key('WHATSAPP_WABA_ID'))
    return Response({
        'configured':    token_set and phone_set,
        'token_set':     token_set,
        'phone_id_set':  phone_set,
        'waba_id_set':   waba_set,
        'secrets_url':   '/settings/secrets',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def whatsapp_templates(request):
    """The prepared templates + their live Meta approval status. `manual_text` is
    the exact body to paste into WhatsApp Manager if submitting by hand."""
    if not _can_manage_keys(request.user):
        return _denied()
    statuses = wa.template_status()          # {} when WABA not set / read fails
    rows = []
    for name, t in wa.TEMPLATES.items():
        rows.append({
            'name':        name,
            'category':    t['category'],
            'language':    wa.TEMPLATE_LANG,
            'body':        t['body'],
            'params':      t['params'],
            'example':     t['example'][0] if t['example'] else [],
            'status':      statuses.get(name, 'NOT_SUBMITTED'),
        })
    return Response({'templates': rows, 'waba_configured': wa.waba_configured()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def whatsapp_submit_templates(request):
    """Submit the prepared templates to Meta for approval (needs WHATSAPP_WABA_ID)."""
    if not _can_manage_keys(request.user):
        return _denied()
    return Response(wa.submit_templates())


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def whatsapp_register(request):
    """One-time: turn on sending by registering the number on the Cloud API.

    Body {pin: '123456'} — the WhatsApp number's 6-digit two-step-verification
    PIN. Passed straight to Meta and never stored. Fixes the
    '(#133010) Account not registered' error that blocks every send until done.
    """
    if not _can_manage_keys(request.user):
        return _denied()
    ok, err = wa.register_number((request.data or {}).get('pin') or '')
    if ok:
        return Response({'ok': True,
                         'detail': 'Number registered — WhatsApp sending is now on.'})
    return Response({'ok': False, 'detail': err}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def whatsapp_contacts(request):
    if not _can_manage_keys(request.user):
        return _denied()
    from taskboard.models import WhatsAppContact

    if request.method == 'GET':
        rows = [_contact_row(c) for c in WhatsAppContact.objects.all()]
        return Response({'contacts': rows, 'count': len(rows)})

    body = request.data or {}

    # Bulk paste — "Name  +267..." per line
    if body.get('paste'):
        parsed = wa.parse_paste(body['paste'])
        created, skipped = [], 0
        for row in parsed:
            if not row['phone']:
                skipped += 1
                continue
            if WhatsAppContact.objects.filter(phone=row['phone']).exists():
                skipped += 1
                continue
            c = WhatsAppContact.objects.create(
                name=row['name'], phone=row['phone'], created_by=request.user,
            )
            created.append(_contact_row(c))
        return Response({'created': created, 'created_count': len(created),
                         'skipped': skipped}, status=status.HTTP_201_CREATED)

    # Single contact
    name = (body.get('name') or '').strip()
    phone = wa.normalize_phone(body.get('phone') or '')
    if not name:
        return Response({'detail': 'name is required.'}, status=400)
    if not phone:
        return Response({'detail': 'A valid phone number is required.'}, status=400)
    if WhatsAppContact.objects.filter(phone=phone).exists():
        return Response({'detail': f'A contact with phone {phone} already exists.'}, status=400)
    c = WhatsAppContact.objects.create(
        name=name, phone=phone,
        role=(body.get('role') or '').strip(),
        is_manager=bool(body.get('is_manager')),
        notes=(body.get('notes') or ''),
        created_by=request.user,
    )
    return Response(_contact_row(c), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def whatsapp_contact_detail(request, pk):
    if not _can_manage_keys(request.user):
        return _denied()
    from taskboard.models import WhatsAppContact
    c = WhatsAppContact.objects.filter(pk=pk).first()
    if not c:
        return Response({'detail': 'Not found.'}, status=404)

    if request.method == 'DELETE':
        c.delete()
        return Response({'id': str(pk), 'deleted': True})

    body = request.data or {}
    if 'name' in body:
        c.name = (body.get('name') or '').strip() or c.name
    if 'phone' in body:
        p = wa.normalize_phone(body.get('phone') or '')
        if p:
            if WhatsAppContact.objects.filter(phone=p).exclude(pk=c.pk).exists():
                return Response({'detail': f'Phone {p} already on another contact.'}, status=400)
            c.phone = p
    if 'role' in body:
        c.role = (body.get('role') or '').strip()
    if 'is_manager' in body:
        c.is_manager = bool(body.get('is_manager'))
    if 'active' in body:
        c.active = bool(body.get('active'))
    if 'notes' in body:
        c.notes = body.get('notes') or ''
    c.save()
    return Response(_contact_row(c))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def whatsapp_overdue(request):
    """Overdue tasks grouped by person, matched to a phonebook contact, with a
    ready-to-send reminder message and a wa.me fallback link."""
    if not _can_manage_keys(request.user):
        return _denied()
    from taskboard.models import WhatsAppContact

    contacts = list(WhatsAppContact.objects.filter(active=True))

    def match(name: str):
        n = (name or '').lower()
        for c in contacts:
            cn = c.name.lower()
            if cn and (cn in n or n in cn):
                return c
            parts = [p for p in n.split() if len(p) > 2]
            if parts and all(p in cn for p in parts):
                return c
        return None

    out = []
    for g in wa.overdue_by_person():
        c = match(g['name'])
        first = (g['name'].split() or ['there'])[0]
        titles = g['tasks'][:5]
        bullet = '\n'.join(f'• {t}' for t in titles)
        more = f'\n(+{len(g["tasks"]) - 5} more)' if len(g['tasks']) > 5 else ''
        msg = (f'Hi {first}, this is a reminder from Alpha Direct. '
               f'You have {len(g["tasks"])} overdue task(s) past the deadline:\n'
               f'{bullet}{more}\nPlease update or complete them today. Thank you.')
        out.append({
            'assignee_id':     g['assignee_id'],
            'name':            g['name'],
            'overdue':         len(g['tasks']),
            'tasks':           g['tasks'],
            'message':         msg,
            'contact':         _contact_row(c) if c else None,
            'phone':           c.phone if c else '',
            'wa_link':         wa.wa_link(c.phone, msg) if c else '',
            'template_name':   'task_overdue_reminder',
            'template_params': [first, str(len(g['tasks']))],
        })
    return Response({'people': out, 'count': len(out),
                     'api_configured': wa.is_configured()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def whatsapp_send(request):
    """Send a reminder to one or more contacts / raw numbers. Always returns a
    wa.me fallback link per recipient so the CFO can send by hand if the API
    isn't configured or a send fails."""
    if not _can_manage_keys(request.user):
        return _denied()
    from taskboard.models import WhatsAppContact, WhatsAppMessage

    body = request.data or {}
    task_id = body.get('task_id') or None

    # ── Template mode ──────────────────────────────────────────────────────
    # {template: 'task_overdue_reminder', recipients: [{contact_id|phone, params, name?}]}
    # Sends an APPROVED template (works outside the 24h window). Each recipient
    # carries its own params (e.g. [first_name, count]).
    template = (body.get('template') or '').strip()
    if template:
        if template not in wa.TEMPLATES:
            return Response({'detail': f'Unknown template "{template}".'}, status=400)
        recips = body.get('recipients') or []
        results = []
        for r in recips:
            phone, name = '', (r.get('name') or '')
            if r.get('contact_id'):
                c = WhatsAppContact.objects.filter(pk=r['contact_id']).first()
                if c:
                    phone, name = c.phone, c.name
            phone = phone or wa.normalize_phone(r.get('phone') or '')
            if not phone:
                continue
            params = r.get('params') or []
            ok, pid, err = wa.send_template(phone, template, params)
            preview = f'[{template}] ' + ' | '.join(str(p) for p in params)
            WhatsAppMessage.objects.create(
                to_name=name, to_phone=phone, body=preview,
                status=(WhatsAppMessage.Status.SENT if ok else WhatsAppMessage.Status.FAILED),
                provider_id=pid, error=err, task_id=task_id, sent_by=request.user,
            )
            results.append({'name': name, 'phone': phone, 'ok': ok, 'error': err,
                            'wa_link': ''})
        if not results:
            return Response({'detail': 'No valid recipients.'}, status=400)
        sent = sum(1 for x in results if x['ok'])
        return Response({'results': results, 'sent': sent, 'failed': len(results) - sent,
                         'mode': 'template', 'api_configured': wa.is_configured()})

    # ── Free-text mode ─────────────────────────────────────────────────────
    message = (body.get('message') or '').strip()
    if not message:
        return Response({'detail': 'message is required.'}, status=400)

    recipients: list[dict] = []

    for cid in (body.get('contact_ids') or []):
        c = WhatsAppContact.objects.filter(pk=cid).first()
        if c:
            recipients.append({'name': c.name, 'phone': c.phone})
    for raw in (body.get('phones') or []):
        p = wa.normalize_phone(raw)
        if p:
            recipients.append({'name': '', 'phone': p})

    if not recipients:
        return Response({'detail': 'No valid recipients.'}, status=400)

    results = []
    for r in recipients:
        ok, pid, err = wa.send_message(r['phone'], message)
        # ACCEPTED, not SENT: free-form only reaches someone who messaged us in
        # the last 24h. Outside that window Meta returns 200 and drops it. Never
        # let this log assert a delivery we cannot prove — see wa.send_message.
        WhatsAppMessage.objects.create(
            to_name=r['name'], to_phone=r['phone'], body=message,
            status=(WhatsAppMessage.Status.ACCEPTED if ok else WhatsAppMessage.Status.FAILED),
            provider_id=pid, error=err, task_id=task_id, sent_by=request.user,
        )
        results.append({
            'name': r['name'], 'phone': r['phone'],
            'ok': ok, 'error': err,
            'wa_link': wa.wa_link(r['phone'], message),
        })

    sent = sum(1 for x in results if x['ok'])
    return Response({'results': results, 'sent': sent,
                     'failed': len(results) - sent,
                     'mode': 'freeform',
                     'delivery_confirmed': False,
                     'delivery_warning':
                         'WhatsApp accepted these, but a free-typed message only '
                         'arrives if the person messaged the Alpha Direct number in '
                         'the last 24 hours. For a reminder that always arrives, use '
                         'an approved template. Or use the "send by hand" link.',
                     'api_configured': wa.is_configured()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def whatsapp_log(request):
    if not _can_manage_keys(request.user):
        return _denied()
    from taskboard.models import WhatsAppMessage
    rows = [{
        'id':         str(m.id),
        'to_name':    m.to_name,
        'to_phone':   m.to_phone,
        'body':       m.body,
        'status':     m.status,
        'error':      m.error,
        'sent_by':    (m.sent_by.get_full_name() or m.sent_by.username) if m.sent_by_id else '',
        'created_at': m.created_at.isoformat() if m.created_at else None,
    } for m in WhatsAppMessage.objects.select_related('sent_by')[:100]]
    return Response({'messages': rows, 'count': len(rows)})
