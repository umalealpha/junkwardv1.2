"""
core/pii_firewall.py — reversible PII tokenisation gateway for outbound LLM calls.

CFO directive 2026-07-18 (Option B, after the Botswana DPA No. 18/2024 audit):
keep using DeepSeek (and the other external engines) but NEVER let identifiable
personal data leave the building in the clear. Every external-LLM call is wrapped
so PII is swapped for reversible placeholders (PERSON_1, ID_1, ACCT_1 …) BEFORE the
request leaves, and the real values are swapped back into the model's answer LOCALLY.
The model sees only safe tokens; the caller/user sees the identical answer — so no
feature is lost.

MODES — env ``PII_FIREWALL_MODE`` (read per-call, so it flips without a code deploy):
  off       pass-through; byte-identical to the pre-firewall behaviour. Rollback target.
  monitor   DETECT + LOG what *would* be tokenised, but send the ORIGINAL text.
            Behaviour-identical to ``off`` — used to prove nothing breaks before the flip.
  tokenize  replace PII with reversible tokens outbound, rehydrate inbound.  ← the fix.
  block     if PII is detected, REFUSE the external call (raise) — fail closed.

Detection = regex (Omang/national-ID, bank/long numbers, e-mail, phone, passport)
plus an optional cached dictionary of known staff/customer names from the DB.
**Money amounts, ISO dates and ERP reference codes (JE-/PO-/GRN-/INV- …) are stashed
and PRESERVED** so financial-reasoning features keep working unchanged.

This module never logs raw PII — only per-category counts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from functools import wraps
from typing import Callable, Optional

log = logging.getLogger('pii-firewall')

VALID_MODES = ('off', 'monitor', 'tokenize', 'block')
_DEFAULT_MODE = 'monitor'   # safe default: detect + log, but change nothing.


def current_mode() -> str:
    """Read the active mode at call-time. Env first (flip via /etc/alpha-finance/.env
    + restart), falling back to Django settings, then the safe default. Kept dead
    simple so rollback is a one-line env change to ``off``."""
    raw = (os.environ.get('PII_FIREWALL_MODE') or '').strip().lower()
    if raw in VALID_MODES:
        return raw
    try:
        from django.conf import settings
        s = str(getattr(settings, 'PII_FIREWALL_MODE', '') or '').strip().lower()
        if s in VALID_MODES:
            return s
    except Exception:      # noqa: BLE001 — settings not ready
        pass
    return _DEFAULT_MODE


# ── Tokens we must PRESERVE (never treat as PII) ──────────────────────────────
# ERP reference codes, ISO dates, AND money amounts. Amounts are stashed FIRST so
# the numeric PII rules (Omang=9 digits, bank=10+ digits) can never eat a figure —
# this is the H1 fix (Fable review 2026-07-18): a BWP 34,567,890.50 or 125150000.0
# must reach the model intact for the commentary/GL-review features to reason.
_SAFE_TOKEN_PATTERNS = [
    re.compile(r'\bJE-\d{4}-\d{6}\b'),
    re.compile(r'\bPO-[A-Z]{2,4}-\d{4}-\d{6}\b'),
    re.compile(r'\bGRN-\d{4}-\d{6}\b'),
    re.compile(r'\bINV-\d{4,}\b'),
    re.compile(r'\bBILL-\d{4,}\b'),
    re.compile(r'\bPCV-\d{4}-\d{6}\b'),
    re.compile(r'\bFY\d{2,4}(?:_\d+M)?\b'),
    re.compile(r'\b\d{4}Q[1-4]\b'),
    re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),                 # ISO date
    re.compile(r'\b\d{4}-\d{2}\b'),                       # YYYY-MM
    re.compile(r'\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b'),      # 1,250,000.00 — money
    # Bare decimal amount, ≤12 integer digits so a 13+-digit account number that
    # arrives with a spurious ".00" (spreadsheet float coercion) still falls to the
    # ACCT rule instead of being preserved in the clear (H1 residual, Fable review).
    re.compile(r'\b\d{1,12}\.\d{1,2}\b'),                 # 34567890.50 / 125150000.0
]

# ── PII detectors, in priority order. (category, compiled-regex) ─────────────
_PII_PATTERNS = [
    ('EMAIL', re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    # Passport-style: 1-2 letters + 6-8 digits
    ('PASS',  re.compile(r'\b[A-Z]{1,2}\d{6,8}\b')),
    # Botswana Omang — exactly 9 digits, not part of a longer run, no leading +
    ('ID',    re.compile(r'(?<![+\d])\b\d{9}\b(?!\d)')),
    # Bank / long identifiers — 10+ contiguous digits
    ('ACCT',  re.compile(r'(?<![+\d.])\b\d{10,}\b')),
    # Phone — +CC then 6+ separator/digits, OR a bare 8-digit BW mobile (7xxxxxxx).
    # (H1 fix: the 3xxxxxxx form was dropped — it collided with 30-39M amounts and
    #  30th/31st compact dates; BW mobiles are 7-prefixed.)
    ('PHONE', re.compile(r'\+\d[\d\s().\-]{6,}\d')),
    ('PHONE', re.compile(r'(?<![+\d])\b7\d{7}\b(?!\d)')),
]

# Names shorter than this, or in the stoplist, are never matched (false-positive guard).
_NAME_MIN_LEN = 4          # H2 fix: 3 was too permissive ("One", "Gift" …).
_NAME_MAX_DICT = 8000      # cap the alternation so the regex stays fast.
# Ordinary words that are also given names in Botswana — never tokenise these.
_NAME_STOPLIST = {
    # org / finance nouns
    'the', 'and', 'for', 'insurance', 'direct', 'alpha', 'health', 'claims',
    'motor', 'life', 'group', 'limited', 'company', 'account', 'bank', 'test',
    'admin', 'user', 'staff', 'office', 'gross', 'total', 'net', 'premium',
    'policy', 'claim', 'payment', 'vendor', 'client', 'member', 'report',
    # months (compact DOB / date words)
    'january', 'february', 'march', 'april', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    # common English/Setswana words that double as given names
    'one', 'gift', 'precious', 'justice', 'blessing', 'lucky', 'peace', 'hope',
    'goodwill', 'success', 'wisdom', 'faith', 'joy', 'lovely', 'pretty', 'baby',
    'thanks', 'god', 'given', 'kind', 'gently',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
}
_NAME_CACHE: dict[str, object] = {'regex': None, 'at': 0.0}
_NAME_TTL = 3600.0   # rebuild the name dictionary at most hourly


def _names_regex():
    """Compiled alternation of known staff/customer names from the DB, cached for
    an hour. Returns None if names are disabled, the DB is unavailable, or the set
    is empty. Never raises — a failure here just means names aren't tokenised
    (regex identifiers still are).

    S1 fix (Fable review 2026-07-18): the real name fields are Employee.full_name
    and RewardMember.customer_name — the previous first_name/last_name pairs threw
    FieldError and silently disabled all name masking. Each label is now guarded
    independently and failures are logged at WARNING, not debug."""
    if (os.environ.get('PII_FIREWALL_NAMES', '1').strip().lower()
            in ('0', 'false', 'no', 'off')):
        return None
    now = time.time()
    if _NAME_CACHE['regex'] is not None and (now - float(_NAME_CACHE['at'])) < _NAME_TTL:
        return _NAME_CACHE['regex']

    names: set[str] = set()
    from django.apps import apps
    for label, field in (
        ('payroll.Employee', 'full_name'),
        ('rewards.RewardMember', 'customer_name'),
    ):
        try:
            Model = apps.get_model(label)
            for (value,) in Model.objects.values_list(field).iterator():
                if not value:
                    continue
                for tok in str(value).split():
                    t = tok.strip(".,;:'\"()")
                    if len(t) >= _NAME_MIN_LEN and t.lower() not in _NAME_STOPLIST:
                        names.add(t)
        except Exception as exc:      # noqa: BLE001 — model missing / DB down
            log.warning('pii-firewall: name source %s unavailable (%s)', label, exc)

    if not names:
        _NAME_CACHE.update(regex=None, at=now)
        return None
    if len(names) > _NAME_MAX_DICT:
        # Keep the longest (most-identifying, least likely to be a common word).
        names = set(sorted(names, key=len, reverse=True)[:_NAME_MAX_DICT])
    # Longest first so multi-part names anchor correctly.
    alt = '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    rx = re.compile(r'(?<![A-Za-z])(?:' + alt + r')(?![A-Za-z])')
    _NAME_CACHE.update(regex=rx, at=now)
    return rx


class Tokeniser:
    """Reversible PII → placeholder mapper for a single LLM call."""

    def __init__(self):
        self.map: dict[str, str] = {}          # token -> original value
        self._by_value: dict[tuple, str] = {}  # (category, value) -> token
        self._counters: dict[str, int] = defaultdict(int)
        self.counts: dict[str, int] = defaultdict(int)

    def _token(self, category: str, value: str) -> str:
        key = (category, value)
        if key in self._by_value:
            return self._by_value[key]
        self._counters[category] += 1
        token = f'{category}_{self._counters[category]}'
        self._by_value[key] = token
        self.map[token] = value
        self.counts[category] += 1
        return token

    def scrub(self, text: Optional[str]) -> Optional[str]:
        """Replace PII with reversible tokens. ERP codes, dates and money amounts
        are protected first so PII patterns can't eat them."""
        if not text:
            return text
        out = str(text)

        # Phase 1 — stash safe tokens (ERP codes, dates, amounts).
        stash: dict[str, str] = {}
        idx = [0]

        def _stash(m):
            idx[0] += 1
            k = f'\x00SAFE{idx[0]}\x00'
            stash[k] = m.group(0)
            return k
        for pat in _SAFE_TOKEN_PATTERNS:
            out = pat.sub(_stash, out)

        # Phase 2 — names (dictionary), then structured identifiers.
        rx = _names_regex()
        if rx is not None:
            out = rx.sub(lambda m: self._token('PERSON', m.group(0)), out)
        for category, pat in _PII_PATTERNS:
            out = pat.sub(lambda m, c=category: self._token(c, m.group(0)), out)

        # Phase 3 — restore safe tokens.
        for k, original in stash.items():
            out = out.replace(k, original)
        return out

    def rehydrate(self, text: Optional[str], *, json_safe: bool = False) -> Optional[str]:
        """Swap tokens back to the real values in the model's answer. Matches the
        exact token or its markdown-escaped form only (``PERSON_1`` / ``PERSON\\_1``);
        spaced/hyphenated variants are deliberately NOT matched (they fail safe to a
        visible placeholder rather than risk splicing PII into unrelated text — M1,
        Fable review 2026-07-18). Warns if a token we sent never came back.

        ``json_safe`` (M2 fix): when the caller used ``response_format='json_object'``
        the token sits inside a JSON string, so a real value containing a quote or
        backslash (an address, a name like O"…) would break the caller's
        ``json.loads``. With json_safe we substitute the JSON-escaped value, keeping
        the document valid."""
        if not text or not self.map:
            return text
        out = str(text)
        missing = []
        # Longest tokens first so PERSON_11 isn't clipped by PERSON_1.
        for token in sorted(self.map, key=len, reverse=True):
            cat, _, num = token.partition('_')
            value = self.map[token]
            if json_safe:
                value = json.dumps(value)[1:-1]   # escape " \ and control chars
            # Match the exact token or its markdown-escaped form (PERSON\_1), fully
            # word-bounded. We deliberately DO NOT match space/hyphen variants
            # (M1 confirm, Fable review 2026-07-18): "Beneficiary ID 1" / "Contact
            # Person 1" style headers would otherwise get real PII spliced into an
            # unrelated word. Failing to a visible placeholder is safe; splicing the
            # wrong value into other text is not.
            pat = re.compile(r'\b' + re.escape(cat) + r'\\?_' + re.escape(num) + r'\b',
                             re.IGNORECASE)
            new_out, n = pat.subn(lambda _m, v=value: v, out)
            if n == 0:
                missing.append(token)
            out = new_out
        if missing:
            log.warning('pii-firewall: %d token(s) not echoed back by the model '
                        '(possible placeholder leak): %s', len(missing),
                        ', '.join(missing))
        return out

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _log_egress(provider: str, fn_name: str, mode: str, tk: 'Tokeniser') -> None:
    if tk.total:
        summary = ', '.join(f'{k}×{v}' for k, v in sorted(tk.counts.items()))
        log.info('pii-firewall[%s] %s via %s: %d PII item(s) [%s]',
                 mode, fn_name, provider, tk.total, summary)


def firewall(provider: str, unavailable_exc: type) -> Callable:
    """Decorator for an external-LLM ``*_complete(user_prompt, *, system_prompt=…)``
    function. Tokenises PII outbound and rehydrates the answer inbound, per the
    active mode. ``unavailable_exc`` is the provider's own 'unavailable' exception,
    raised in ``block`` mode so the reasoning fail-over chain skips this provider."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(user_prompt, *args, **kwargs):
            mode = current_mode()
            if mode == 'off':
                return fn(user_prompt, *args, **kwargs)

            tk = Tokeniser()
            scrubbed_user = tk.scrub(user_prompt)
            system_prompt = kwargs.get('system_prompt')
            scrubbed_system = tk.scrub(system_prompt) if system_prompt else system_prompt
            _log_egress(provider, getattr(fn, '__name__', 'llm'), mode, tk)

            if mode == 'monitor':
                # Detect + log only — send the ORIGINAL, so behaviour is unchanged.
                return fn(user_prompt, *args, **kwargs)

            if mode == 'block' and tk.total:
                raise unavailable_exc(
                    f'PII firewall blocked {tk.total} personal-data item(s) '
                    f'from leaving to {provider}.')

            # tokenize mode (the compliant, feature-preserving path)
            new_kwargs = dict(kwargs)
            if system_prompt is not None:
                new_kwargs['system_prompt'] = scrubbed_system
            result = fn(scrubbed_user, *args, **new_kwargs)
            if isinstance(result, str):
                json_mode = kwargs.get('response_format') == 'json_object'
                return tk.rehydrate(result, json_safe=json_mode)
            return result
        return wrapper
    return decorator
