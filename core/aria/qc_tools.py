"""
core/aria/qc_tools.py — action + search + messaging tools for the Aria AI assistant.

Available to whitelisted managers/EXCO. These tools let Aria act on behalf
of the user: search payments, approve/reject, look up staff, count pending,
and send popup messages to other staff (CFO only).

Every action is audit-logged under the REAL user's name.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from .action_intents import (
    INTENT_TTL_SECONDS, consume_intent, issue_intent, payment_fingerprint,
)

log = logging.getLogger('aria.qc_tools')

ARIA_POWER_USERS = {
    'pganesharajah@alphadirect.co.bw',
    'cfo@alphadirect.co.bw',
    'pkago@alphadirect.co.bw',
    'ktshutlhedi@alphadirect.co.bw',
    'kmasilo@alphadirect.co.bw',
    'lbasotli@alphadirect.co.bw',
    'omogomotsi@alphadirect.co.bw',
    'bmokone@alphadirect.co.bw',
    'bnaidu@alphadirect.co.bw',
    'asundaram@alphadirect.co.bw',
    'arjun@alphadirect.co.bw',
    'ubutale@alphadirect.co.bw',
    'dmosweu@alphadirect.co.bw',
}

CFO_EMAILS = {
    'pganesharajah@alphadirect.co.bw',
    'cfo@alphadirect.co.bw',
}


def is_power_user(user) -> bool:
    email = getattr(user, 'email', '') or ''
    return email.lower() in ARIA_POWER_USERS


def is_cfo(user) -> bool:
    email = getattr(user, 'email', '') or ''
    return email.lower() in CFO_EMAILS


def search_payments(*, payee: str = '', ref: str = '', status: str = '',
                    limit: int = 10) -> dict:
    from taskboard.models import PaymentRequest
    from django.db.models import Q

    qs = PaymentRequest.objects.select_related('created_by').order_by('-created_at')
    if ref:
        qs = qs.filter(ref__icontains=ref)
    if payee:
        qs = qs.filter(Q(payee__icontains=payee) | Q(subject__icontains=payee))
    if status:
        qs = qs.filter(status=status)

    results = []
    for p in qs[:limit]:
        total = sum(
            float(li.get('amount', 0))
            for li in (p.line_items or [])
            if isinstance(li, dict)
        )
        results.append({
            'ref': p.ref,
            'payee': p.payee or '',
            'subject': p.subject or '',
            'status': p.get_status_display(),
            'status_code': p.status,
            'category': p.get_category_display() if p.category else '',
            'entity': p.entity or '',
            'total': f'{total:,.2f} {p.currency}',
            'created_by': (p.created_by.get_full_name() if p.created_by else ''),
            'created_at': p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else '',
        })

    return {'count': len(results), 'payments': results}


def count_pending(*, user=None) -> dict:
    from taskboard.models import PaymentRequest, OmniTask

    pf = PaymentRequest.objects.filter(status='pending_finance').count()
    pc = PaymentRequest.objects.filter(status='pending_cfo').count()
    exc = PaymentRequest.objects.filter(status='exception').count()
    draft = PaymentRequest.objects.filter(status='draft').count()
    tasks_open = OmniTask.objects.filter(status__in=['open', 'in_progress']).count()

    return {
        'pending_finance_signoff': pf,
        'pending_cfo_authorisation': pc,
        'exceptions_with_committee': exc,
        'drafts': draft,
        'open_tasks': tasks_open,
    }


def lookup_staff(*, name: str = '', email: str = '') -> dict:
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    User = get_user_model()

    qs = User.objects.filter(is_active=True)
    if name:
        parts = name.split()
        q = Q()
        for part in parts:
            q &= (Q(first_name__icontains=part) | Q(last_name__icontains=part)
                   | Q(username__icontains=part))
        qs = qs.filter(q)
    if email:
        qs = qs.filter(email__icontains=email)

    results = []
    for u in qs[:10]:
        results.append({
            'name': u.get_full_name() or u.username,
            'email': u.email or '',
            'is_staff': u.is_staff,
            'last_login': u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else 'never',
        })

    return {'count': len(results), 'staff': results}


def _screen_roles(user):
    """(is_cfo, is_first_approver) exactly as the payment screen decides them."""
    from taskboard.payment_views import _is_cfo, _is_first_approver
    return _is_cfo(user), _is_first_approver(user)


def _approve_refusal(p, user) -> str | None:
    """Mirror of the payment screen's approve rule (payment_request_decide):
    only finance sign-off exists as an 'approve'. At the CFO stage money leaves
    through the FNB load or a Clear on the screen — Aria never marks a payment
    paid (it used to, skipping PAY-DUP-01 / PAY-BANK-DOC; Opus judge 18-Sep)."""
    is_cfo_user, is_first = _screen_roles(user)
    if p.status == 'pending_cfo':
        return (f'Payment {p.ref} is already signed off and waiting at the CFO stage. '
                'It is paid through the FNB load (or cleared on the payment screen) — not from Aria.')
    if p.status != 'pending_finance':
        return f'Payment {p.ref} is {p.get_status_display()} — cannot approve.'
    if not is_first:
        return 'Only a finance approver (Pako, Kago or Legakwa) can sign off at this stage.'
    if p.created_by_id == user.id:
        return 'You cannot sign off a payment you raised (segregation of duties).'
    return None


def _reject_refusal(p, user, reason) -> str | None:
    """Mirror of the payment screen's reject rule (payment_request_decide)."""
    if not reason:
        return 'A reason is required when rejecting.'
    is_cfo_user, is_first = _screen_roles(user)
    if p.status == 'pending_finance':
        if not is_first:
            return 'Only a finance approver (Pako, Kago or Legakwa) can reject at this stage.'
        if p.created_by_id == user.id:
            return 'You cannot decide a payment you raised (segregation of duties).'
        return None
    if p.status == 'pending_cfo':
        if not (is_cfo_user or is_first):
            return 'Only a finance approver or the CFO can reject at this stage.'
        if is_first and not is_cfo_user and p.first_approver_id == user.id:
            return 'You signed this request off, so you cannot also reject it. Ask the CFO or another finance approver.'
        return None
    return f'Payment {p.ref} is {p.get_status_display()} — cannot reject.'


def _confirmation_card(p, user, action: str, *, reason: str = '', notes: str = '') -> dict:
    """First call of approve/reject: changes nothing, returns what the user must
    see plus a short-lived token. aria_chat moves the token OFF the model's view
    and onto a Confirm card, so only the human's tap can complete the action
    (L-AGENT, CFO 18-Sep-2026: confirmation enforced server-side, not by prompt)."""
    return {
        'ok': True,
        'needs_confirmation': True,
        'action': action,
        'ref': p.ref,
        'payee': p.payee or '',
        'amount': str(p.total),
        'currency': p.currency or 'BWP',
        'status': p.get_status_display(),
        'reason': reason,
        'notes': notes,
        'confirm_token': issue_intent(user=user, action=action, ref=p.ref,
                                      fingerprint=payment_fingerprint(p, reason or notes)),
        'expires_in_seconds': INTENT_TTL_SECONDS,
    }


def _decide_on_the_screen(p, user, decision: str, notes: str):
    """Run the payment screen's own decide endpoint as this user, so every
    control it applies (SOD, PAY-DUP-01 recheck, bank-change warning,
    PAY-BANK-DOC, CFO task) applies here too. Returns (ok, status_code, data)."""
    from rest_framework.test import APIRequestFactory, force_authenticate
    from taskboard.payment_views import payment_request_decide
    req = APIRequestFactory().post(f'/api/v1/payment-requests/{p.pk}/decide/',
                                   {'decision': decision, 'notes': notes}, format='json')
    force_authenticate(req, user=user)
    resp = payment_request_decide(req, req_id=p.pk)
    return 200 <= resp.status_code < 300, resp.status_code, (resp.data or {})


def _complete(*, ref: str, user, action: str, text: str, confirm_token: str) -> dict:
    from taskboard.models import PaymentRequest
    from django.db import transaction
    from core.models import AuditLog
    # 1. Short transaction: lock, re-check, consume the intent — then COMMIT.
    #    The screen's decide code loads FNB and emails the CFO when it thinks its
    #    sign-off is committed; it must never run inside a transaction of ours
    #    that could still roll back and leave the FNB load behind (Opus judge).
    with transaction.atomic():
        try:
            p = PaymentRequest.objects.select_for_update().get(ref=ref)
        except PaymentRequest.DoesNotExist:
            return {'ok': False, 'error': f'Payment {ref} not found.'}
        refusal = (_approve_refusal(p, user) if action == 'approve'
                   else _reject_refusal(p, user, text))
        if refusal:
            return {'ok': False, 'error': refusal}
        err = consume_intent(confirm_token, user=user, action=action, ref=ref,
                             fingerprint=payment_fingerprint(p, text))
        if err:
            return {'ok': False, 'error': err}
    # 2. The payment screen's own decide, outside any transaction of ours; it
    #    re-locks and re-checks everything itself.
    ok, code, data = _decide_on_the_screen(p, user, action, text)
    if not ok:
        return {'ok': False, 'error': str(data.get('detail') or data.get('error')
                                          or f'The payment screen refused it ({code}).'),
                'screen_status': code}
    p.refresh_from_db()
    # A duplicate found on the re-check sends the request to the exception
    # committee with a 200 — that is not an approval.
    if action == 'approve' and (data.get('control') or p.status == 'exception'):
        return {'ok': False, 'screen_status': code, 'new_status': p.get_status_display(),
                'error': ('Not approved: the payment screen found a possible duplicate and sent it '
                          'to the exception committee.')}
    try:
        AuditLog.objects.create(
            user=user, table_name='paymentrequest', record_id=str(p.pk),
            action=AuditLog.Action.APPROVE if action == 'approve' else AuditLog.Action.UPDATE,
            new_values={'aria_action': f'{action}_via_aria', 'ref': ref, 'text': text,
                        'confirmed_via': 'server_intent'},
            description=f'{action}_via_aria via Aria AI assistant (user tapped Confirm)',
        )
    except Exception:  # noqa: BLE001 — the decision itself is done and audited by the screen
        log.exception('Aria audit row failed after a completed %s on %s', action, ref)
    return {'ok': True, 'ref': ref, 'new_status': p.get_status_display(),
            'action': f'{action}_via_aria', 'acted_by': user.get_full_name()}


def approve_payment(*, ref: str, user, notes: str = '', confirm_token: str = '') -> dict:
    from taskboard.models import PaymentRequest
    if confirm_token:
        return _complete(ref=ref, user=user, action='approve', text=notes, confirm_token=confirm_token)
    p = PaymentRequest.objects.filter(ref=ref).first()
    if p is None:
        return {'ok': False, 'error': f'Payment {ref} not found.'}
    refusal = _approve_refusal(p, user)
    if refusal:
        return {'ok': False, 'error': refusal}
    return _confirmation_card(p, user, 'approve', notes=notes)


def reject_payment(*, ref: str, user, reason: str, confirm_token: str = '') -> dict:
    from taskboard.models import PaymentRequest
    if confirm_token:
        return _complete(ref=ref, user=user, action='reject', text=reason, confirm_token=confirm_token)
    p = PaymentRequest.objects.filter(ref=ref).first()
    if p is None:
        return {'ok': False, 'error': f'Payment {ref} not found.'}
    refusal = _reject_refusal(p, user, reason)
    if refusal:
        return {'ok': False, 'error': refusal}
    return _confirmation_card(p, user, 'reject', reason=reason)


def recent_audit_log(*, table: str = '', limit: int = 10) -> dict:
    from core.models import AuditLog

    qs = AuditLog.objects.order_by('-created_at')
    if table:
        qs = qs.filter(table_name__icontains=table)

    results = []
    for entry in qs[:limit]:
        results.append({
            'user': entry.user.get_full_name() if entry.user else 'system',
            'table': entry.table_name,
            'action': str(entry.action),
            'record_id': entry.record_id or '',
            'at': entry.created_at.strftime('%Y-%m-%d %H:%M') if entry.created_at else '',
        })

    return {'count': len(results), 'entries': results}


def _popup_identifier(u) -> str:
    return (u.email or u.username or '').strip()


def send_popup_message(*, recipient_name: str, message: str, user,
                       confirm_recipient: str = '') -> dict:
    """Send a popup message to a staff member. CFO only.

    TWO CALLS, ALWAYS. The first call never sends: it resolves the name and
    returns who it matched. Only a second call carrying `confirm_recipient`
    (that person's exact email or username) creates the popup.

    The CFO asked Aria to "double check with me the name". Instructing the
    model to confirm is a rule it can skip; requiring an identifier it can only
    have learned from the first call's reply is a gate it cannot. A wrong or
    guessed identifier is refused rather than delivered to the wrong person.
    """
    if not is_cfo(user):
        return {'ok': False, 'error': 'Only the CFO can send popup messages.'}

    if not message.strip():
        return {'ok': False, 'error': 'Message cannot be empty.'}

    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from core.models import AriaPopup
    User = get_user_model()

    parts = recipient_name.strip().split()
    if not parts:
        return {'ok': False, 'error': 'A recipient name is required.'}

    q = Q()
    for part in parts:
        q &= (Q(first_name__icontains=part) | Q(last_name__icontains=part))
    matches = list(User.objects.filter(q, is_active=True)[:5])

    if not matches:
        return {'ok': False, 'error': f'No active staff member found matching "{recipient_name}".'}

    candidates = [
        {'name': u.get_full_name() or u.username, 'confirm_recipient': _popup_identifier(u)}
        for u in matches
    ]

    if not confirm_recipient:
        return {
            'ok': False,
            'needs_confirmation': True,
            'error': ('Not sent yet. Show the CFO exactly who this would go to and the '
                      'message, and ask them to confirm. Then call send_popup_message '
                      'again with confirm_recipient set to that person\'s value below.'),
            'matches': candidates,
            'message_to_send': message,
        }

    wanted = confirm_recipient.strip().lower()
    recipient = next((u for u in matches if _popup_identifier(u).lower() == wanted), None)
    if recipient is None:
        return {
            'ok': False,
            'error': (f'"{confirm_recipient}" is not one of the people matching '
                      f'"{recipient_name}". Nothing was sent. Confirm again using one '
                      f'of the exact values listed.'),
            'matches': candidates,
        }

    AriaPopup.objects.create(sender=user, recipient=recipient, message=message)

    return {
        'ok': True,
        'sent_to': recipient.get_full_name() or recipient.username,
        'email': recipient.email,
        'message_preview': message[:100],
    }
