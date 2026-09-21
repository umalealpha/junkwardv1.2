"""
taskboard/whatsapp_service.py — WhatsApp reminder sending (CFO directive 2026-07-14).

The CFO wants to fire WhatsApp reminders at staff (especially managers) who miss
task deadlines. This module:

  * resolves the WhatsApp API credentials from the EXISTING CFO Secrets Vault
    (Settings → Secrets) via core.ai_assist.get_llm_key — the same vault-first
    resolver the LLM keys use, so the CFO pastes the key in ONE place, no redeploy;
  * sends a text message through the Meta WhatsApp Cloud API;
  * ALWAYS also builds a wa.me deep link so the CFO can send a reminder by hand
    (from his own WhatsApp) even before the API key is configured;
  * builds the overdue-per-person suggestion list from the live task board.

Vault slots (category = API, filled by the CFO in the Secrets UI):
  WHATSAPP_TOKEN      — the WhatsApp Cloud API access token ("the API key")
  WHATSAPP_PHONE_ID   — the sender phone-number id from Meta
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

import requests

from core.ai_assist import get_llm_key

log = logging.getLogger(__name__)

GRAPH_VERSION = 'v20.0'
BW_COUNTRY_CODE = '267'
MAX_BODY = 4096
TEMPLATE_PARAM_MAX = 1024          # Meta's hard cap per template body parameter
TEMPLATE_LANG = 'en'

# Pre-approved WhatsApp message templates (CFO 2026-07-14). Meta requires an
# APPROVED template for business-initiated messages outside the 24h reply window.
# All UTILITY category (task/account updates to an existing relationship — the
# approvable, non-marketing kind). {{1}}, {{2}} are positional body variables.
TEMPLATES = {
    'task_overdue_reminder': {
        'category': 'UTILITY',
        'body': ('Hi {{1}}, this is a reminder from Alpha Direct. You have {{2}} '
                 'task(s) that are past their deadline. Please update or complete '
                 'them today. Thank you.'),
        'example': [['Kago', '3']],
        'params': ['first_name', 'overdue_count'],
    },
    'task_due_today': {
        'category': 'UTILITY',
        'body': ('Hi {{1}}, this is a reminder from Alpha Direct. Your task "{{2}}" '
                 'is due today. Please complete it before end of day. Thank you.'),
        'example': [['Kago', 'Submit June claims report']],
        'params': ['first_name', 'task_title'],
    },
    # Evening exec summary of the ADH service-provider network (CFO 2026-09-01).
    'exec_provider_report': {
        'category': 'UTILITY',
        'body': ('Alpha Direct — provider network evening update ({{1}}). {{2}} '
                 'providers ready to accept ADH clients, {{3}} still to chase, {{4}} '
                 'new application(s). The full dashboard is in your Omni email.'),
        'example': [['01 Sep', '114', '56', '2']],
        'params': ['date', 'ready', 'to_chase', 'new_apps'],
    },
}


def _graph(path: str) -> str:
    return f'https://graph.facebook.com/{GRAPH_VERSION}/{path}'


def normalize_phone(raw: str) -> str:
    """Return a digits-only international number (no '+') for wa.me / the API.

    Accepts '+2677...', '2677...', '7XXXXXXX' (Botswana 8-digit local, auto
    prefixed 267), and strips spaces / dashes / brackets.
    """
    if not raw:
        return ''
    s = re.sub(r'[^\d+]', '', str(raw).strip()).lstrip('+')
    if len(s) == 8 and s.startswith('7'):        # Botswana local mobile
        s = BW_COUNTRY_CODE + s
    return s


def wa_link(phone: str, text: str) -> str:
    """Click-to-chat deep link — opens WhatsApp with the message pre-filled."""
    p = normalize_phone(phone)
    return f'https://wa.me/{p}?text={quote(text or "")}' if p else ''


def clean_template_param(value) -> str:
    """Make one value safe to pass as a template body parameter.

    Meta REJECTS the whole send if any parameter contains a newline, a tab, or
    more than 4 consecutive spaces:

        (#100) Invalid parameter — "Param text cannot have new-line/tab
        characters or more than 4 consecutive spaces"

    and caps each parameter at 1024 characters. This is not theoretical: it is
    the bug that silently broke 100% of Graphite V2's template sends for months
    (Arjun Iyer's handover, 2026-08-03). Omni's params are short by design
    (a first name, a count, a task title) but a task title is free text a user
    typed, so it CAN carry a line break — never trust it.

    Truncation is deliberate and visible ('…'), never a silent mid-sentence cut.
    """
    s = str('' if value is None else value)
    s = re.sub(r'[\r\n\t\v\f]+', ' ', s)      # newlines / tabs -> one space
    s = re.sub(r' {2,}', ' ', s).strip()      # squeeze runs (>4 is rejected)
    if len(s) > TEMPLATE_PARAM_MAX:
        s = s[:TEMPLATE_PARAM_MAX - 1].rstrip() + '…'
    return s


def is_configured() -> bool:
    """True when both the API token and sender phone-id are set in the vault."""
    return bool(get_llm_key('WHATSAPP_TOKEN')) and bool(get_llm_key('WHATSAPP_PHONE_ID'))


def send_message(to_phone: str, text: str) -> tuple[bool, str, str]:
    """Send a free-form WhatsApp text via the Meta Cloud API.

    ⚠️ ok=True means META ACCEPTED IT — **not** that it will be delivered.
    A free-form ("type": "text") message only reaches the recipient if THEY
    messaged our business number within the last 24 hours. Outside that window
    Meta accepts the request, returns HTTP 200 with a message id, and silently
    discards the message. There is no error to catch.

    Most Omni contacts are staff who have never messaged the Alpha Direct
    number, so they are permanently outside that window — free-form to them is
    a no-op that looks like a success. Callers must therefore record this as
    ACCEPTED, never SENT (see WhatsAppMessage.Status), and any proactive
    reminder should go out as an approved template via send_template().

    Returns (accepted, provider_message_id, error). Never raises — the caller
    logs the result to WhatsAppMessage.
    """
    token = get_llm_key('WHATSAPP_TOKEN')
    phone_id = get_llm_key('WHATSAPP_PHONE_ID')
    to = normalize_phone(to_phone)

    if not token or not phone_id:
        return (False, '',
                'WhatsApp API key not set. Paste WHATSAPP_TOKEN and '
                'WHATSAPP_PHONE_ID in Settings → Secrets, then try again.')
    if not to:
        return (False, '', 'Invalid phone number.')

    url = f'https://graph.facebook.com/{GRAPH_VERSION}/{phone_id}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': (text or '')[:MAX_BODY]},
    }
    try:
        r = requests.post(
            url, json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=20,
        )
        if r.status_code // 100 == 2:
            data = r.json() or {}
            pid = ((data.get('messages') or [{}])[0] or {}).get('id', '')
            return (True, pid, '')
        return (False, '', f'{r.status_code}: {r.text[:300]}')
    except Exception as exc:  # noqa: BLE001 — network / provider failure
        log.warning('WhatsApp send failed: %s', exc)
        return (False, '', str(exc)[:300])


def waba_configured() -> bool:
    """True when the API token + WhatsApp Business Account id are set — needed to
    submit/read message templates (a different id from the sender phone id)."""
    return bool(get_llm_key('WHATSAPP_TOKEN')) and bool(get_llm_key('WHATSAPP_WABA_ID'))


def submit_templates() -> dict:
    """Create the prepared templates on the WhatsApp Business Account so Meta can
    approve them. Idempotent-ish: an "already exists" reply counts as ok. Needs
    WHATSAPP_TOKEN + WHATSAPP_WABA_ID. Returns {ok, results:[{name,ok,status,error}]}.
    """
    token = get_llm_key('WHATSAPP_TOKEN')
    waba = get_llm_key('WHATSAPP_WABA_ID')
    if not token or not waba:
        return {'ok': False,
                'error': 'Set WHATSAPP_TOKEN and WHATSAPP_WABA_ID in Settings → Secrets first.',
                'results': []}
    out = []
    for name, t in TEMPLATES.items():
        payload = {
            'name': name,
            'language': TEMPLATE_LANG,
            'category': t['category'],
            'components': [{
                'type': 'BODY',
                'text': t['body'],
                'example': {'body_text': t['example']},
            }],
        }
        try:
            r = requests.post(_graph(f'{waba}/message_templates'), json=payload,
                              headers={'Authorization': f'Bearer {token}'}, timeout=20)
            j = r.json() if r.content else {}
            if r.status_code // 100 == 2:
                out.append({'name': name, 'ok': True, 'status': j.get('status', 'SUBMITTED'), 'error': ''})
            else:
                err = ((j.get('error') or {}).get('message') or r.text)[:200]
                exists = 'already exists' in err.lower()
                out.append({'name': name, 'ok': exists,
                            'status': 'EXISTS' if exists else 'ERROR', 'error': '' if exists else err})
        except Exception as exc:  # noqa: BLE001
            out.append({'name': name, 'ok': False, 'status': 'ERROR', 'error': str(exc)[:200]})
    return {'ok': all(x['ok'] for x in out), 'results': out}


def template_status() -> dict:
    """Live approval status of our templates from Meta: {name: 'APPROVED'|'PENDING'|...}.
    Empty dict if the WABA isn't configured or the read fails."""
    token = get_llm_key('WHATSAPP_TOKEN')
    waba = get_llm_key('WHATSAPP_WABA_ID')
    if not token or not waba:
        return {}
    try:
        r = requests.get(_graph(f'{waba}/message_templates'),
                         params={'fields': 'name,status,category', 'limit': 200},
                         headers={'Authorization': f'Bearer {token}'}, timeout=20)
        if r.status_code // 100 != 2:
            return {}
        return {d['name']: d.get('status') for d in (r.json().get('data') or [])
                if d.get('name') in TEMPLATES}
    except Exception:  # noqa: BLE001
        return {}


def send_template(to_phone: str, template_name: str, params: list) -> tuple[bool, str, str]:
    """Send an APPROVED template message (works outside the 24h window)."""
    token = get_llm_key('WHATSAPP_TOKEN')
    phone_id = get_llm_key('WHATSAPP_PHONE_ID')
    to = normalize_phone(to_phone)
    if not token or not phone_id:
        return (False, '', 'WhatsApp API key not set.')
    if not to:
        return (False, '', 'Invalid phone number.')
    template = {'name': template_name, 'language': {'code': TEMPLATE_LANG}}
    if params:
        template['components'] = [{
            'type': 'body',
            'parameters': [{'type': 'text', 'text': clean_template_param(p)}
                           for p in params],
        }]
    payload = {'messaging_product': 'whatsapp', 'to': to, 'type': 'template', 'template': template}
    try:
        r = requests.post(_graph(f'{phone_id}/messages'), json=payload,
                          headers={'Authorization': f'Bearer {token}'}, timeout=20)
        if r.status_code // 100 == 2:
            pid = ((r.json().get('messages') or [{}])[0] or {}).get('id', '')
            return (True, pid, '')
        return (False, '', f'{r.status_code}: {r.text[:300]}')
    except Exception as exc:  # noqa: BLE001
        log.warning('WhatsApp template send failed: %s', exc)
        return (False, '', str(exc)[:300])


def register_number(pin: str) -> tuple[bool, str]:
    """One-time: register the sender phone number on the WhatsApp Cloud API.

    Until the number is registered, Meta rejects EVERY send with
    '(#133010) Account not registered' — regardless of a valid token or an
    approved template. This posts to /{phone_id}/register with the number's
    6-digit two-step-verification PIN. The PIN is used here and NEVER stored.
    Returns (ok, error).
    """
    token = get_llm_key('WHATSAPP_TOKEN')
    phone_id = get_llm_key('WHATSAPP_PHONE_ID')
    if not token or not phone_id:
        return (False, 'Set WHATSAPP_TOKEN and WHATSAPP_PHONE_ID in Settings → Secrets first.')
    pin = re.sub(r'\D', '', str(pin or ''))
    if len(pin) != 6:
        return (False, 'The registration PIN must be exactly 6 digits.')
    try:
        r = requests.post(
            _graph(f'{phone_id}/register'),
            json={'messaging_product': 'whatsapp', 'pin': pin},
            headers={'Authorization': f'Bearer {token}'}, timeout=20,
        )
        if r.status_code // 100 == 2:
            return (True, '')
        j = r.json() if r.content else {}
        return (False, ((j.get('error') or {}).get('message') or r.text)[:300])
    except Exception as exc:  # noqa: BLE001 — network / provider failure
        log.warning('WhatsApp register failed: %s', exc)
        return (False, str(exc)[:300])


def parse_paste(text: str) -> list[dict]:
    """Parse a pasted block of 'Name  +267...' lines into contact dicts.

    Tolerant of separators: 'Name, +267...', 'Name - 71234567', '+267.. Name'.
    A line with no phone-like token is skipped.
    """
    rows: list[dict] = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r'\+?\d[\d\s\-()]{6,}\d', line)
        if not m:
            continue
        phone = m.group(0)
        name = (line[:m.start()] + ' ' + line[m.end():]).strip(' ,-\t|')
        name = re.sub(r'\s{2,}', ' ', name)
        rows.append({'name': name or 'Unknown', 'phone': normalize_phone(phone)})
    return rows


def overdue_by_person() -> list[dict]:
    """Live overdue tasks grouped by assignee, for the reminder suggestions.

    Mirrors the /task-dashboard overdue logic (taskboard.services.is_overdue).
    """
    from core.models import OmniTask
    from taskboard import services

    qs = (OmniTask.objects
          .exclude(status=OmniTask.Status.CANCELLED)
          .exclude(status=OmniTask.Status.DONE)
          .select_related('assignee'))

    people: dict = {}
    for t in qs:
        if not t.assignee_id or not services.is_overdue(t):
            continue
        g = people.setdefault(t.assignee_id, {
            'assignee_id': t.assignee_id,
            'name': t.assignee.get_full_name() or t.assignee.username,
            'tasks': [],
        })
        g['tasks'].append(t.title)
    return sorted(people.values(), key=lambda g: -len(g['tasks']))
