"""
fnb/ai_health.py

Aria's plain-English read of the FNB bank connection (CFO directive 2026-08-04).

The /banking/fnb page shows raw signals — HTTP 403s, 400s, sync-log rows — that
a non-technical CFO cannot parse at a glance. This module turns those signals
into one plain-English summary, and answers follow-up questions, using omni's
existing PII-firewalled reasoning cascade (DeepSeek first — core.ai_assist).

SAFETY (non-negotiable): the AI only ever sees NON-SENSITIVE operational
signals — connection state, HTTP status codes, error CLASSES, counts, and
account numbers MASKED to their last 4 digits. It never sees full account
numbers, customer/vendor names, transaction descriptions, or amounts. Every
assembled prompt is additionally run through core.ai_assist.is_safe_for_ai()
as a second net before it leaves the building.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from core.ai_assist import is_safe_for_ai, reasoning_complete, DeepSeekUnavailable
from .models import FNBSyncLog, FNBWebhookEvent

log = logging.getLogger(__name__)

_SUMMARY_CACHE_KEY = 'fnb_ai_health_summary_v1'
_SUMMARY_TTL = 300  # 5 min — don't hit the AI on every page load


def _ago(dt) -> str:
    """Human 'time ago' — safe, no absolute timestamps needed by the AI."""
    if not dt:
        return 'never'
    secs = (timezone.now() - dt).total_seconds()
    if secs < 90:
        return 'just now'
    mins = secs / 60
    if mins < 90:
        return f'{int(round(mins))} min ago'
    hrs = mins / 60
    if hrs < 36:
        return f'{int(round(hrs))} h ago'
    return f'{int(round(hrs / 24))} d ago'


def _mask_tail(num: str) -> str:
    """Return only the last 4 digits of any number found — e.g. '…1000'."""
    digits = re.sub(r'\D', '', num or '')
    return f'…{digits[-4:]}' if len(digits) >= 4 else ''


def collect_safe_signals() -> dict:
    """Gather ONLY non-sensitive health signals from the FNB audit log.

    No full account numbers, names, descriptions, or amounts — anything a
    number here is either a status code, a count, or a last-4 mask.
    """
    now = timezone.now()
    since24 = now - timedelta(hours=24)
    since7d = now - timedelta(days=7)

    # ---- Notifications (the real-time alerts feed) ----------------------
    notif = FNBSyncLog.objects.filter(service=FNBSyncLog.Service.NOTIFICATION)
    notif_last = notif.order_by('-created_at').first()
    notif_last_ok = notif.filter(status=FNBSyncLog.Status.SUCCESS).order_by('-created_at').first()
    notif_fail_24h = notif.filter(created_at__gte=since24).exclude(
        status=FNBSyncLog.Status.SUCCESS).count()

    def _classify(row) -> str:
        if not row:
            return 'no calls yet'
        if row.status == FNBSyncLog.Status.SUCCESS:
            return 'working (HTTP 200)'
        if row.status == FNBSyncLog.Status.TIMEOUT or (row.http_status is None):
            return 'not responding (timeout)'
        if row.http_status == 403 and 'scope' in (row.error_message or '').lower():
            return 'blocked — access scope not granted (HTTP 403)'
        if row.http_status:
            return f'failing (HTTP {row.http_status})'
        return 'failing'

    # ---- Statements -----------------------------------------------------
    stmt = FNBSyncLog.objects.filter(service=FNBSyncLog.Service.STATEMENT)
    stmt_ok_24h = stmt.filter(created_at__gte=since24, status=FNBSyncLog.Status.SUCCESS).count()
    stmt_fail_24h = stmt.filter(created_at__gte=since24).exclude(
        status=FNBSyncLog.Status.SUCCESS).count()
    # Which account(s) are rejected — masked to last 4 digits only.
    failing_accounts: list[str] = []
    for row in stmt.filter(created_at__gte=since7d).exclude(
            status=FNBSyncLog.Status.SUCCESS).order_by('-created_at')[:30]:
        blob = f'{row.endpoint} {row.request_summary} {row.error_message}'
        for m in re.findall(r'\d{8,}', blob):
            tail = _mask_tail(m)
            code = row.http_status or '?'
            entry = f'account {tail} → HTTP {code}'
            if tail and entry not in failing_accounts:
                failing_accounts.append(entry)
    failing_accounts = failing_accounts[:5]

    # ---- Alerts actually received --------------------------------------
    last_event = FNBWebhookEvent.objects.order_by('-received_at').first()

    # ---- Connection / last sync ----------------------------------------
    last_any = FNBSyncLog.objects.order_by('-created_at').first()
    failed_24h_all = FNBSyncLog.objects.filter(
        status=FNBSyncLog.Status.FAILED, created_at__gte=since24).count()

    return {
        'connection': {
            'last_call': _ago(last_any.created_at if last_any else None),
            'last_call_status': (last_any.status if last_any else 'no calls'),
            'failed_calls_24h': failed_24h_all,
        },
        'alerts_feed': {
            'current_state': _classify(notif_last),
            'last_checked': _ago(notif_last.created_at if notif_last else None),
            'last_working': _ago(notif_last_ok.created_at if notif_last_ok else None),
            'failed_checks_24h': notif_fail_24h,
            'last_alert_received': _ago(last_event.received_at if last_event else None),
        },
        'statements': {
            'imported_ok_24h': stmt_ok_24h,
            'failed_24h': stmt_fail_24h,
            'rejected_accounts': failing_accounts,
        },
    }


def _signals_to_text(sig: dict) -> str:
    c, a, s = sig['connection'], sig['alerts_feed'], sig['statements']
    lines = [
        'FNB BANK CONNECTION — health signals (Alpha Direct, Botswana):',
        f'- Connection: last call {c["last_call"]} ({c["last_call_status"]}); '
        f'{c["failed_calls_24h"]} failed calls in 24h.',
        f'- Alerts feed: {a["current_state"]}; last checked {a["last_checked"]}; '
        f'last working {a["last_working"]}; {a["failed_checks_24h"]} failed checks in 24h; '
        f'last real alert received {a["last_alert_received"]}.',
        f'- Statements: {s["imported_ok_24h"]} imported OK in 24h, {s["failed_24h"]} failed.',
    ]
    if s['rejected_accounts']:
        lines.append('- Rejected accounts (masked): ' + '; '.join(s['rejected_accounts']) + '.')
    return '\n'.join(lines)


_ARIA_FNB_SYSTEM = (
    'You are Aria, Alpha Direct Insurance\'s on-screen finance assistant, '
    'reading the FNB (First National Bank Botswana) connection page for a '
    'NON-TECHNICAL CFO. Explain the state of the bank link in plain English — '
    'no jargon, no HTTP codes in your prose, no account numbers. Lead with '
    'whether things are broadly healthy, then name anything broken and, in one '
    'short line each, what should happen about it. Botswana context, currency '
    'Pula. Be warm, concise, confident. Under 110 words. Never invent numbers — '
    'use only the signals given. If a signal says "timeout", say the bank\'s '
    'service is not responding right now (their side), not that we did anything '
    'wrong.'
)


def health_summary(force: bool = False) -> dict:
    """Return {'summary': str, 'signals': dict, 'engine_ok': bool}.

    Cached 5 min. Never raises — on AI failure returns a plain non-AI fallback
    line built from the signals so the page still renders."""
    signals = collect_safe_signals()
    if not force:
        cached = cache.get(_SUMMARY_CACHE_KEY)
        if cached:
            cached['signals'] = signals  # keep signals live even on cache hit
            return cached

    text = _signals_to_text(signals)
    safety = is_safe_for_ai(text)
    result = {'summary': '', 'signals': signals, 'engine_ok': False}
    if not safety.safe:
        result['summary'] = _fallback_line(signals)
        return result

    try:
        out = reasoning_complete(
            f'Here are the current signals:\n\n{safety.redacted_text}\n\n'
            'Give the CFO your plain-English read now.',
            system_prompt=_ARIA_FNB_SYSTEM,
            max_tokens=320,
            feature='fnb_health',
        )
        result['summary'] = (out or '').strip() or _fallback_line(signals)
        result['engine_ok'] = bool(out and out.strip())
    except DeepSeekUnavailable as exc:
        log.warning('fnb health_summary: AI unavailable (%s)', exc)
        result['summary'] = _fallback_line(signals)

    cache.set(_SUMMARY_CACHE_KEY, result, _SUMMARY_TTL)
    return result


def _fallback_line(sig: dict) -> str:
    a = sig['alerts_feed']
    s = sig['statements']
    bits = [f'Alerts feed: {a["current_state"]} (last checked {a["last_checked"]}).']
    if s['rejected_accounts']:
        bits.append(f'{len(s["rejected_accounts"])} account(s) rejected by FNB.')
    else:
        bits.append(f'{s["imported_ok_24h"]} statement pulls succeeded in the last day.')
    return ' '.join(bits)


def answer_question(question: str) -> dict:
    """Answer a follow-up about the bank connection, grounded in the same safe
    signals. Used by the 'why is X failing?' / 'draft the FNB email' buttons.
    Returns {'ok': bool, 'answer': str}."""
    q = (question or '').strip()
    if not q:
        return {'ok': False, 'answer': ''}
    safety_q = is_safe_for_ai(q)
    if not safety_q.safe:
        return {'ok': False, 'answer': 'That question contains details I can\'t send to the AI.'}

    signals = collect_safe_signals()
    text = _signals_to_text(signals)
    safety = is_safe_for_ai(text)
    prompt = (
        f'Current FNB signals:\n\n{safety.redacted_text}\n\n'
        f'CFO asks: {safety_q.redacted_text.strip()}\n\n'
        'Answer in plain English for a non-technical CFO. If asked to draft an '
        'email to FNB, write a short point-form email (open with the name, '
        'bullets, close "Regards, Prathap Ganesharajah, CFO"), and NEVER put a '
        'full account number in it — refer to the account by its last 4 digits.'
    )
    try:
        out = reasoning_complete(
            prompt, system_prompt=_ARIA_FNB_SYSTEM, max_tokens=520,
            feature='fnb_health_ask',
        )
        return {'ok': True, 'answer': (out or '').strip()}
    except DeepSeekUnavailable as exc:
        log.warning('fnb answer_question: AI unavailable (%s)', exc)
        return {'ok': False, 'answer': 'The AI helper is not reachable right now — try again shortly.'}
