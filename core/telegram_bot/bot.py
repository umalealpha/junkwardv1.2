"""
core/telegram_bot/bot.py

Telegram bot loop — long-polling, read-only, password-gated.

Authentication
--------------
Each Telegram chat must enter the password (TELEGRAM_BOT_PASSWORD)
before queries are answered. After auth, sessions persist in memory
for TELEGRAM_BOT_SESSION_MINUTES (default 60). On bot restart the
session table is wiped — every user re-enters the password.

Authorisation
-------------
TELEGRAM_BOT_AUTHORIZED_USERS is a comma-separated list of Telegram
user IDs allowed through. Any other user gets a flat 'not authorised'
reply and never sees the password prompt. Empty list = open (still
password-gated) — useful for the first day of testing.

Read-only by construction
-------------------------
The bot calls only read-side service functions. There is no path in
this code that creates, posts, approves, pays, or modifies anything
in the ERP.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from decouple import Csv, config

from core.telegram_bot import intent, services


log = logging.getLogger(__name__)


_API_TPL = 'https://api.telegram.org/bot{token}/{method}'


# Tracks consecutive network failures so we can back off + only log the first
# failure and the subsequent recovery (instead of one log line per retry).
_NET_STATE = {
    'consecutive_failures': 0,
    'last_log_emitted': 0.0,
}


def _is_transient_network_error(exc: BaseException) -> bool:
    """ConnectionError / DNS failure / read timeout — silent-retry territory."""
    msg = str(exc).lower()
    if isinstance(exc, requests.ConnectionError):
        return True
    if isinstance(exc, requests.Timeout):
        return True
    return any(s in msg for s in (
        'connection aborted', 'connection reset',
        'getaddrinfo failed', 'name resolution',
        'remote host', 'temporary failure',
    ))


def _backoff_seconds(failures: int) -> float:
    """Cap exponential backoff at 60s. 1, 2, 4, 8, 16, 32, 60..."""
    return min(60.0, max(1.0, 2 ** (failures - 1)))


# ---------------------------------------------------------------------------
# Session store (in-memory, sliding TTL)
# ---------------------------------------------------------------------------

class SessionStore:
    """Per-Telegram-user authentication state, in-memory only."""

    def __init__(self, session_minutes: int):
        self._sessions: dict[str, datetime] = {}
        self._ttl = timedelta(minutes=max(1, session_minutes))

    def authenticate(self, user_id: str) -> None:
        self._sessions[user_id] = datetime.utcnow()

    def is_authenticated(self, user_id: str) -> bool:
        last = self._sessions.get(user_id)
        if last is None:
            return False
        if datetime.utcnow() - last > self._ttl:
            self._sessions.pop(user_id, None)
            return False
        # Sliding window — extend on every authed action
        self._sessions[user_id] = datetime.utcnow()
        return True

    def logout(self, user_id: str) -> None:
        self._sessions.pop(user_id, None)


# ---------------------------------------------------------------------------
# Telegram HTTP wrapper
# ---------------------------------------------------------------------------

def _send_message(token: str, chat_id: int, text: str, parse_mode: str = 'Markdown') -> bool:
    """Send a message. Falls back to plain text if Markdown parsing fails."""
    try:
        resp = requests.post(
            _API_TPL.format(token=token, method='sendMessage'),
            json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        log.warning('sendMessage %s: %s', resp.status_code, resp.text[:200])
        # Re-send without Markdown if parser tripped
        if 'parse' in (resp.text or '').lower() or "can't parse" in (resp.text or '').lower():
            requests.post(
                _API_TPL.format(token=token, method='sendMessage'),
                json={'chat_id': chat_id, 'text': text},
                timeout=10,
            )
        return False
    except requests.RequestException as exc:
        log.warning('sendMessage exception: %s', exc)
        return False


def _get_updates(token: str, offset: Optional[int], timeout: int = 30) -> list:
    """Long-poll getUpdates with silent-retry on transient network errors.

    Home / hotspot Wi-Fi will drop DNS or reset the long-poll occasionally.
    We swallow those, back off exponentially (up to 60s), and only log the
    first failure in a burst plus the recovery — no log spam.
    """
    params = {'timeout': timeout}
    if offset is not None:
        params['offset'] = offset
    try:
        resp = requests.get(
            _API_TPL.format(token=token, method='getUpdates'),
            params=params,
            timeout=timeout + 5,
        )
        if resp.status_code != 200:
            log.warning('getUpdates %s: %s', resp.status_code, resp.text[:200])
            return []
        # Successful round-trip — clear the failure counter and log recovery
        # if we had been in a failure run.
        if _NET_STATE['consecutive_failures']:
            log.info('Telegram reachable again after %d failed attempts.',
                     _NET_STATE['consecutive_failures'])
            _NET_STATE['consecutive_failures'] = 0
        return resp.json().get('result', []) or []
    except requests.RequestException as exc:
        _NET_STATE['consecutive_failures'] += 1
        n = _NET_STATE['consecutive_failures']
        if _is_transient_network_error(exc):
            # First failure of a burst gets logged; subsequent ones are silent.
            if n == 1:
                log.info('Telegram unreachable (network blip). Backing off; '
                         'will keep retrying quietly. Cause: %s', exc.__class__.__name__)
            time.sleep(_backoff_seconds(n))
        else:
            log.warning('getUpdates exception: %s', exc)
        return []


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _help_text() -> str:
    return (
        "🛰️ *ARIA* — _Alpha Direct, at your command._\n"
        "Everything you see in Omni, I can pull — instantly, read-only.\n\n"
        "*💰 Money & performance*\n"
        "• `revenue` / `gwp` — Gross Written Premium\n"
        "• `ma` / `pnl` — full P&L (New Format)\n"
        "• `balance sheet` — financial position\n"
        "• `key metrics` — loss / expense / combined ratio\n"
        "• `cash` · `receivables` · `payables`\n"
        "• `fy24 pnl` / `fy24 bs` — prior year\n\n"
        "*📋 Operations*\n"
        "• `pending POs` · paste any PO number\n"
        "• `bills to pay` · `exceptions` · `status`\n\n"
        "*🛡️ Claims*\n"
        "• `claims` — register summary\n"
        "• `claim <number>` · `claims for <name>`\n\n"
        "*👥 People*\n"
        "• `<name> salary` / `pay <name> last month`\n"
        "• `<name> leave` / `my leave days`\n"
        "• `hours <name>` — Time Doctor\n\n"
        "*🚗 Fleet & assets*\n"
        "• `fleet` · `where is <plate>` · `what car does <name> drive`\n"
        "• `asset <tag or name>`\n\n"
        "_Just talk to me in plain English. `/logout` when you're done._"
    )


from datetime import date as _date

# FY24 closing snapshot is dated 30 June 2024
_FY24_ANCHOR = _date(2024, 6, 30)


_HANDLERS = {
    'revenue':         lambda i: services.revenue_last_n_months(i.get('months') or 6),
    'cash_balance':    lambda i: services.cash_balance(),
    'pending_pos':     lambda i: services.pending_purchase_orders(),
    'po_detail':       lambda i: services.purchase_order_detail(i.get('po_number') or ''),
    'bills_to_pay':    lambda i: services.bills_to_pay(),
    'ar_outstanding':  lambda i: services.ar_outstanding(),
    'ap_outstanding':  lambda i: services.ap_outstanding(),
    'open_exceptions': lambda i: services.open_exceptions(),
    'pnl_summary':         lambda i: services.management_accounts(),
    'management_accounts': lambda i: services.management_accounts(),
    'balance_sheet':       lambda i: services.balance_sheet(),
    'key_metrics':         lambda i: services.key_metrics(),
    # FY24 prior-year comparatives
    'fy24_pnl':            lambda i: services.management_accounts(_FY24_ANCHOR),
    'fy24_bs':             lambda i: services.balance_sheet(_FY24_ANCHOR),
    'fy24_metrics':        lambda i: services.key_metrics(_FY24_ANCHOR),
    'system_status':   lambda i: services.system_status(),
    'payroll':         lambda i: services.staff_pay(
                           i.get('employee_query') or '',
                           period_name=i.get('period_name') or None,
                           want_last_month=bool(i.get('want_last_month')),
                       ),
    'time_doctor':     lambda i: services.time_doctor_query(i.get('employee_query') or ''),
    'fleet':           lambda i: services.fleet_query(i.get('query') or ''),
    'assets':          lambda i: services.asset_query(i.get('query') or ''),
    'leave':           lambda i: services.leave_query(i.get('employee_query') or ''),
    'claims':          lambda i: services.claims_query(i.get('query') or ''),
    'help':            lambda i: _help_text(),
    'unknown':         lambda i: "I didn't catch that. Type `help` for what I can answer.",
}

# Intents that release identifiable personal data (staff pay, individual hours,
# leave) or customer data (claims). Answered ONLY to explicitly whitelisted
# Telegram IDs — never in OPEN/test mode. Fleet + assets are company property
# (entry-whitelist only).
_SENSITIVE_INTENTS = {'payroll', 'time_doctor', 'leave', 'claims'}

# Intents where "my …" should resolve to the sender (the authorised user),
# using TELEGRAM_BOT_SELF_NAME. Applies to their own pay / hours / leave.
_SELF_INTENTS = {'payroll', 'time_doctor', 'leave'}
_SELF_WORDS = {'my', 'me', 'mine', 'myself', 'i'}

# Off-menu questions fall back to DeepSeek over a company-level figure bundle
# (CFO 2026-07-16). Kill-switch via env. Individual pay is never in that
# bundle — it stays on the gated payroll intent.
_AI_FALLBACK = config('TELEGRAM_BOT_AI_ANSWER', default=True, cast=bool)


def _handle_query(text: str, *, allow_sensitive: bool = False,
                  self_name: str = '') -> str:
    parsed = intent.parse_intent(text)
    if parsed['intent'] in _SENSITIVE_INTENTS and not allow_sensitive:
        return (
            "🔒 That's restricted. This bot releases staff pay, leave, hours "
            "and claims only to named authorised users. Ask the CFO to add "
            "your Telegram ID to the authorised list."
        )
    # "my leave" / "my salary" / "my hours" → resolve to the sender. If the
    # self-name shortcut isn't configured, DON'T pass the pronoun into the name
    # matcher (icontains 'my' would match Amy/Myra/Smyth → wrong person); ask.
    if (parsed['intent'] in _SELF_INTENTS
            and (parsed.get('employee_query') or '').strip().lower() in _SELF_WORDS):
        if self_name:
            parsed['employee_query'] = self_name
        else:
            return ("Whose record? The “my …” shortcut isn't set up for this "
                    "chat — ask by name instead, e.g. `leave for <name>`.")
    if parsed['intent'] == 'unknown' and _AI_FALLBACK:
        return services.ai_answer(text)
    handler = _HANDLERS.get(parsed['intent'], _HANDLERS['unknown'])
    try:
        return handler(parsed)
    except Exception as exc:  # noqa: BLE001 — we surface everything to the chat
        log.exception('Handler crashed for intent=%s', parsed.get('intent'))
        return f"Eish, that one broke: `{exc.__class__.__name__}`. Try again or ask differently."


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop() -> None:
    token       = config('TELEGRAM_BOT_TOKEN', default='')
    # SECURITY (2026-07-17 audit): no default password, and the bot refuses to
    # start with an empty allow-list — otherwise anyone who finds the bot could
    # unlock it with a known default and query company financials.
    password    = config('TELEGRAM_BOT_PASSWORD', default='')
    authorized  = config('TELEGRAM_BOT_AUTHORIZED_USERS', default='', cast=Csv())
    session_min = config('TELEGRAM_BOT_SESSION_MINUTES', default=60, cast=int)
    # Name used to resolve "my leave" / "my salary" / "my hours" to the CFO's
    # own record. Optional — unset simply disables the "my …" shortcut.
    self_name   = config('TELEGRAM_BOT_SELF_NAME', default='')

    if not token:
        raise RuntimeError(
            'TELEGRAM_BOT_TOKEN is not set in .env. Aborting bot start.'
        )
    if not password:
        raise RuntimeError(
            'TELEGRAM_BOT_PASSWORD is not set in .env. Aborting bot start '
            '(refusing to run with no/known password).'
        )
    if not [u for u in authorized if str(u).strip()]:
        raise RuntimeError(
            'TELEGRAM_BOT_AUTHORIZED_USERS is empty. Aborting bot start '
            '(refusing to serve financial data to an open allow-list).'
        )

    sessions = SessionStore(session_minutes=session_min)
    whitelist = {str(u).strip() for u in authorized if str(u).strip()}
    offset: Optional[int] = None

    log.info('Telegram bot starting. whitelist=%s session_min=%s',
             sorted(whitelist) or 'OPEN', session_min)
    print(f'[telegram-bot] Listening (whitelist={sorted(whitelist) or "OPEN"}). '
          'Ctrl-C to stop.', flush=True)

    while True:
        try:
            updates = _get_updates(token, offset=offset, timeout=30)
        except KeyboardInterrupt:
            print('\n[telegram-bot] Shutting down.', flush=True)
            return

        for upd in updates:
            offset = upd['update_id'] + 1
            msg = upd.get('message')
            if not msg or 'text' not in msg:
                continue

            chat_id = msg['chat']['id']
            from_user = msg.get('from') or {}
            user_id = str(from_user.get('id', ''))
            username = from_user.get('username') or from_user.get('first_name') or '?'
            text = msg['text'].strip()

            log.info('IN  user=%s @%s text=%r', user_id, username, text[:120])

            # --- Enrolment helper (works before auth + before the gate) -----
            # A phone number is NOT a Telegram ID; the whitelist matches this
            # numeric user_id. /whoami lets a person read their own ID to send
            # to the CFO for authorisation.
            if text.strip().lower() in ('/whoami', 'whoami', '/id', 'who am i'):
                _send_message(
                    token, chat_id,
                    f"*Your Telegram ID:* `{user_id}`\n"
                    f"Name: {username}\n\n"
                    f"Send this ID to the CFO to be added to the authorised "
                    f"users list.")
                log.info('WHOAMI user_id=%s @%s', user_id, username)
                continue

            # --- Whitelist gate (if configured) ----------------------------
            if whitelist and user_id not in whitelist:
                _send_message(
                    token, chat_id,
                    f"You are not authorised to use this bot.\n"
                    f"Telegram ID `{user_id}` is not on the whitelist. "
                    f"Speak to the CFO."
                )
                log.warning('REJECTED non-whitelisted user_id=%s @%s', user_id, username)
                continue

            # --- Slash commands --------------------------------------------
            if text.startswith('/start'):
                _send_message(token, chat_id,
                    "🛰️ *Aria* — Alpha Direct.\n"
                    "Send the password to begin.")
                continue

            if text.startswith('/logout'):
                sessions.logout(user_id)
                _send_message(token, chat_id, "Logged out. Send the password to log in again.")
                continue

            if text.startswith('/help'):
                if sessions.is_authenticated(user_id):
                    _send_message(token, chat_id, _help_text())
                else:
                    _send_message(token, chat_id, "Send the password first to use the bot.")
                continue

            # --- Auth gate -------------------------------------------------
            if not sessions.is_authenticated(user_id):
                if text == password:
                    sessions.authenticate(user_id)
                    _send_message(
                        token, chat_id,
                        f"🛰️ *Aria online.* Good to see you, Chief.\n"
                        f"I have eyes on everything you see in Omni — money, "
                        f"people, claims, fleet, the lot. Read-only, always.\n"
                        f"_Session live for {session_min} min._\n\n" + _help_text(),
                    )
                else:
                    _send_message(token, chat_id,
                                  "Password required. Send the password to continue.")
                continue

            # --- Authenticated query --------------------------------------
            # Sensitive intents (staff pay) require an EXPLICIT whitelist —
            # fail closed: never released in OPEN/test mode.
            allow_sensitive = bool(whitelist) and user_id in whitelist
            reply = _handle_query(text, allow_sensitive=allow_sensitive,
                                  self_name=self_name)
            _send_message(token, chat_id, reply)

        if not updates:
            time.sleep(1)
