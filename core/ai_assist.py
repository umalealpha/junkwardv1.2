"""
core/ai_assist.py

Thin DeepSeek client + safety filter for reasoning-only AI calls.

CFO-authorised exception to AD-POL-AI-GOV-001 (Anthropic-only) dated
2026-05-09 — DeepSeek is permitted for *non-sensitive* reasoning tasks
only. Every prompt MUST go through is_safe_for_ai() before send. The
filter scrubs:
  - 9-digit Botswana Omang patterns and other ID-shaped digits
  - Email addresses
  - Phone-shaped digit runs
  - Anything that looks like a bank account number (8+ contiguous digits)
  - Salary / amount figures (decimal numbers > 1,000)

What we DO send: account codes, account names, free-text descriptions
that the user typed, chart-of-accounts category labels.

What we do NOT send: customer / employee names, ID numbers, salary
figures, claim amounts, policy numbers, contact details.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings

from core.pii_firewall import firewall


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safety filter
# ---------------------------------------------------------------------------

# Track-B audit 2026-05-24: tightened. Whitelist tokens the CFO needs
# in chat (JE numbers, PO numbers, account codes, period labels) BEFORE
# the redaction sweep, so legitimate references survive. PII patterns
# anchored more narrowly.

_SAFE_TOKEN_PATTERNS = [
    re.compile(r'\bJE-\d{4}-\d{6}\b'),         # JE numbers
    re.compile(r'\bPO-[A-Z]{2,4}-\d{4}-\d{6}\b'),  # PO numbers
    re.compile(r'\bGRN-\d{4}-\d{6}\b'),        # GRN numbers
    re.compile(r'\bINV-\d{4,}\b'),             # invoice numbers
    re.compile(r'\bBILL-\d{4,}\b'),
    re.compile(r'\bFY\d{2,4}(_\d+M)?\b'),      # FY25, FY26_9M, etc.
    re.compile(r'\b\d{4}Q[1-4]\b'),            # 2026Q3
    re.compile(r'\b\d{4}-\d{2}-\d{2}\b'),      # ISO dates
    re.compile(r'\b\d{4}-\d{2}\b'),            # YYYY-MM
]

_PATTERNS = [
    # Botswana Omang exactly 9 digits (anchored to word boundary, no leading +)
    (re.compile(r'(?<![+\d])\b\d{9}\b(?!\d)'),                   '[ID-REDACTED]'),
    # 10+ contiguous digits (bank accounts, long IDs) — not 8/9 to spare
    # account codes like 280001 (6 digits), JE entry numbers without prefix.
    (re.compile(r'\b\d{10,}\b'),                                 '[NUMBER-REDACTED]'),
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
                                                                 '[EMAIL-REDACTED]'),
    # Phone-like: 8+ digits with separators (Botswana mobile = 8 digits but
    # most are written with country code +267, so requires + or 8+ contiguous)
    (re.compile(r'\+\d[\d\s().-]{6,}\d'),                        '[PHONE-REDACTED]'),
    # Decimal-ish money figures with 2+ thousand-separators
    (re.compile(r'\b\d{1,3}(?:[,.\s]\d{3}){2,}(?:\.\d{1,2})?\b'),'[AMOUNT-REDACTED]'),
    (re.compile(r'\b\d{5,}\.\d{2}\b'),                           '[AMOUNT-REDACTED]'),
]


@dataclass
class SafetyReport:
    safe: bool
    redacted_text: str
    redactions_made: int
    notes: list


def is_safe_for_ai(text: str) -> SafetyReport:
    """
    Returns a SafetyReport. The redacted_text is what may be sent. If
    redactions_made is high we still allow it (the redactions did their
    job), but extreme cases (text that's mostly numbers / IDs) should
    short-circuit the caller.
    """
    if text is None:
        return SafetyReport(safe=True, redacted_text='', redactions_made=0, notes=[])

    redactions = 0
    out = str(text)

    # Phase 1: pluck whitelisted ERP tokens into placeholders so they
    # survive the PII sweep, then restore at the end.
    placeholders: dict[str, str] = {}
    next_idx = [0]
    def _stash(m):
        next_idx[0] += 1
        key = f'__SAFE_TOKEN_{next_idx[0]}__'
        placeholders[key] = m.group(0)
        return key
    for pat in _SAFE_TOKEN_PATTERNS:
        out = pat.sub(_stash, out)

    for pattern, replacement in _PATTERNS:
        new_out, count = pattern.subn(replacement, out)
        if count:
            redactions += count
            out = new_out

    # Phase 3: restore whitelisted tokens
    for key, original in placeholders.items():
        out = out.replace(key, original)

    # Sanity: refuse anything that's >50% redacted bracketed tags
    redacted_chars = sum(len(r) for r in re.findall(r'\[[A-Z\-]+\]', out))
    notes = []
    if len(out) > 0 and redacted_chars / max(len(out), 1) > 0.5:
        return SafetyReport(safe=False, redacted_text=out, redactions_made=redactions,
                            notes=['Input is dominated by redacted PII patterns; refusing AI call.'])

    if redactions:
        notes.append(f'{redactions} pattern(s) redacted before AI send.')

    return SafetyReport(safe=True, redacted_text=out, redactions_made=redactions, notes=notes)


# ---------------------------------------------------------------------------
# DeepSeek HTTP client
# ---------------------------------------------------------------------------

class DeepSeekUnavailable(Exception):
    pass


def get_llm_key(name: str) -> str:
    """Resolve an LLM API key — the CFO Secrets Vault first (Settings → Secrets,
    Fernet-encrypted, CFO-only), then settings/env. Lets the CFO manage every
    model key in ONE place without a redeploy. `name` is both the vault entry
    name and the env var (e.g. 'GEMINI_API_KEY'). Reads the ciphertext directly
    — no audited-reveal spam."""
    try:
        from core.models import VaultSecret
        from core import vault_crypto
        vs = (VaultSecret.objects
              .filter(name=name, category=VaultSecret.Category.API)
              .first())
        if vs and vs.secret_ciphertext:
            val = (vault_crypto.decrypt(vs.secret_ciphertext) or '').strip()
            if val:
                return val
    except Exception as exc:        # noqa: BLE001 — vault unavailable / pre-migration
        log.debug('get_llm_key(%s): vault lookup failed (%s) — using env', name, exc)
    return (getattr(settings, name, '') or '').strip()


@firewall('DeepSeek', DeepSeekUnavailable)
def deepseek_complete(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
    response_format: Optional[str] = None,  # 'json_object' for JSON mode
    max_tokens: int = 800,
) -> str:
    """
    One-shot completion via DeepSeek's chat completions API (OpenAI-compatible).

    Raises DeepSeekUnavailable if the key isn't set or the call fails.
    """
    # CFO directive 2026-07-19: once DeepSeek (China) is switched off for data
    # protection, the work must CONTINUE. When DEEPSEEK_ENABLED is false, every
    # DeepSeek call transparently falls back to the backup cloud engine (Gemini —
    # also PII-firewalled), so all ~20 direct callers + the reasoning cascade keep
    # working with no per-caller change. Flip in one line: DEEPSEEK_ENABLED=false.
    if not getattr(settings, 'DEEPSEEK_ENABLED', True):
        # Keep the DeepSeek contract: every direct caller + the reasoning cascade
        # catch DeepSeekUnavailable, NOT GeminiUnavailable. Translate a Gemini
        # outage so ~24 callers degrade gracefully instead of 500-ing (H1, Fable
        # review 2026-07-19).
        try:
            return gemini_complete(
                user_prompt, system_prompt=system_prompt, timeout=timeout,
                response_format=response_format, max_tokens=max_tokens,
            )
        except GeminiUnavailable as exc:
            raise DeepSeekUnavailable(f'DeepSeek disabled; Gemini fallback failed: {exc}') from exc
    api_key  = get_llm_key('DEEPSEEK_API_KEY')
    api_base = getattr(settings, 'DEEPSEEK_API_BASE', 'https://api.deepseek.com')
    model    = model or getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')

    if not api_key:
        raise DeepSeekUnavailable('DEEPSEEK_API_KEY is not configured.')

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})

    payload = {
        'model':       model,
        'messages':    messages,
        'temperature': 0.2,  # reasoning-leaning; lower = more deterministic
        'max_tokens':  max_tokens,
    }
    if response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type':  'application/json',
    }

    try:
        resp = requests.post(
            f'{api_base}/chat/completions',
            headers=headers,
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.warning('DeepSeek request failed: %s', exc)
        raise DeepSeekUnavailable(f'Network error: {exc}')

    if resp.status_code != 200:
        log.warning('DeepSeek non-200: %s %s', resp.status_code, resp.text[:200])
        raise DeepSeekUnavailable(f'HTTP {resp.status_code}')

    try:
        data = resp.json()
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise DeepSeekUnavailable(f'Malformed response: {exc}')


# ---------------------------------------------------------------------------
# Gemini client — BACKUP reasoning engine (CFO directive 2026-06-12)
# ---------------------------------------------------------------------------

class GeminiUnavailable(Exception):
    pass


@firewall('Gemini', GeminiUnavailable)
def gemini_complete(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
    response_format: Optional[str] = None,  # 'json_object' for JSON mode
    max_tokens: int = 800,
) -> str:
    """
    One-shot completion via Gemini's OpenAI-compatible endpoint. Same shape as
    deepseek_complete so it is a drop-in fallback. Raises GeminiUnavailable if
    the key isn't set or the call fails. Only ever called on text already
    cleared by is_safe_for_ai().
    """
    api_key  = get_llm_key('GEMINI_API_KEY')
    api_base = getattr(settings, 'GEMINI_API_BASE',
                       'https://generativelanguage.googleapis.com/v1beta/openai')
    model    = model or getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash')

    if not api_key:
        raise GeminiUnavailable('GEMINI_API_KEY is not configured.')

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})

    payload = {'model': model, 'messages': messages, 'temperature': 0.2, 'max_tokens': max_tokens}
    if response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}

    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    try:
        resp = requests.post(f'{api_base}/chat/completions', headers=headers,
                             data=json.dumps(payload), timeout=timeout)
    except requests.RequestException as exc:
        log.warning('Gemini request failed: %s', exc)
        raise GeminiUnavailable(f'Network error: {exc}')

    if resp.status_code != 200:
        log.warning('Gemini non-200: %s %s', resp.status_code, resp.text[:200])
        raise GeminiUnavailable(f'HTTP {resp.status_code}')

    try:
        data = resp.json()
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise GeminiUnavailable(f'Malformed response: {exc}')


class OpenAIUnavailable(Exception):
    pass


@firewall('OpenAI', OpenAIUnavailable)
def openai_complete(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
    response_format: Optional[str] = None,  # 'json_object' for JSON mode
    max_tokens: int = 800,
) -> str:
    """One-shot completion via OpenAI (CFO directive 2026-07-13 — the fallback for
    when Gemini fails). Same OpenAI-compatible shape as gemini_complete, so it is a
    drop-in chain link. Defaults to the CHEAPEST model (gpt-4o-mini) to save cost;
    override with settings.OPENAI_MODEL. Key resolves from the CFO Secrets Vault
    (Settings → Secrets, name OPENAI_API_KEY). Only called on is_safe_for_ai() text.
    Raises OpenAIUnavailable if the key isn't set or the call fails."""
    api_key  = get_llm_key('OPENAI_API_KEY')
    api_base = getattr(settings, 'OPENAI_API_BASE', 'https://api.openai.com/v1')
    model    = model or getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')

    if not api_key:
        raise OpenAIUnavailable('OPENAI_API_KEY is not configured.')

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})

    # The gpt-5 / o-series reasoning models refuse 'max_tokens' (they want
    # 'max_completion_tokens') and refuse a custom temperature. Sending the
    # old shape returns HTTP 400, which the chain reads as 'engine down' and
    # silently falls through to a weaker model - so the strongest model on the
    # key was unreachable while looking merely unconfigured. They also spend
    # tokens thinking before answering, so a budget sized for a plain chat
    # model comes back BLANK (same trap as DeepSeek reasoning) - hence the floor.
    _reasoning = model.startswith(('gpt-5', 'o1', 'o3', 'o4'))
    payload = {'model': model, 'messages': messages}
    if _reasoning:
        payload['max_completion_tokens'] = max(max_tokens * 2, 4096)
    else:
        payload['temperature'] = 0.2
        payload['max_tokens'] = max_tokens
    if response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}

    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    try:
        resp = requests.post(f'{api_base}/chat/completions', headers=headers,
                             data=json.dumps(payload), timeout=timeout)
    except requests.RequestException as exc:
        log.warning('OpenAI request failed: %s', exc)
        raise OpenAIUnavailable(f'Network error: {exc}')

    if resp.status_code != 200:
        log.warning('OpenAI non-200: %s %s', resp.status_code, resp.text[:200])
        raise OpenAIUnavailable(f'HTTP {resp.status_code}')

    try:
        data = resp.json()
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise OpenAIUnavailable(f'Malformed response: {exc}')


# ---------------------------------------------------------------------------
# Anthropic + Grok clients (CFO directive 2026-06-18 — 4-engine reasoning chain)
# ---------------------------------------------------------------------------

class AnthropicUnavailable(Exception):
    pass


@firewall('Anthropic', AnthropicUnavailable)
def anthropic_complete(user_prompt: str, *, system_prompt: Optional[str] = None,
                       model: Optional[str] = None, timeout: float = 30.0,
                       response_format: Optional[str] = None) -> str:
    """Anthropic Messages API. No JSON-mode param — for JSON, instruct it in the
    prompt (response_format is accepted but ignored). is_safe_for_ai()-cleared
    text only. Key via get_llm_key (vault → env)."""
    api_key = get_llm_key('ANTHROPIC_API_KEY')
    if not api_key:
        raise AnthropicUnavailable('ANTHROPIC_API_KEY is not configured.')
    model = model or getattr(settings, 'ANTHROPIC_MODEL', 'claude-sonnet-4-6')
    body = {'model': model, 'max_tokens': 800, 'temperature': 0.2,
            'messages': [{'role': 'user', 'content': user_prompt}]}
    if system_prompt:
        body['system'] = system_prompt
    headers = {'x-api-key': api_key, 'anthropic-version': '2023-06-01',
               'content-type': 'application/json'}
    try:
        resp = requests.post('https://api.anthropic.com/v1/messages',
                             headers=headers, data=json.dumps(body), timeout=timeout)
    except requests.RequestException as exc:
        raise AnthropicUnavailable(f'Network error: {exc}')
    if resp.status_code != 200:
        log.warning('Anthropic non-200: %s %s', resp.status_code, resp.text[:200])
        raise AnthropicUnavailable(f'HTTP {resp.status_code}')
    try:
        return resp.json()['content'][0]['text']
    except (KeyError, IndexError, ValueError) as exc:
        raise AnthropicUnavailable(f'Malformed response: {exc}')


class GrokUnavailable(Exception):
    pass


@firewall('Grok', GrokUnavailable)
def grok_complete(user_prompt: str, *, system_prompt: Optional[str] = None,
                  model: Optional[str] = None, timeout: float = 30.0,
                  response_format: Optional[str] = None) -> str:
    """xAI Grok via its OpenAI-compatible endpoint. is_safe_for_ai()-cleared text
    only. Key via get_llm_key (vault → env)."""
    api_key  = get_llm_key('GROK_API_KEY')
    api_base = getattr(settings, 'GROK_API_BASE', 'https://api.x.ai/v1')
    model    = model or getattr(settings, 'GROK_MODEL', 'grok-2-latest')
    if not api_key:
        raise GrokUnavailable('GROK_API_KEY is not configured.')
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})
    payload = {'model': model, 'messages': messages, 'temperature': 0.2, 'max_tokens': 800}
    if response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    try:
        resp = requests.post(f'{api_base}/chat/completions', headers=headers,
                             data=json.dumps(payload), timeout=timeout)
    except requests.RequestException as exc:
        raise GrokUnavailable(f'Network error: {exc}')
    if resp.status_code != 200:
        log.warning('Grok non-200: %s %s', resp.status_code, resp.text[:200])
        raise GrokUnavailable(f'HTTP {resp.status_code}')
    try:
        return resp.json()['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise GrokUnavailable(f'Malformed response: {exc}')


# ---------------------------------------------------------------------------
# Vision (image + text) with automatic failover — used by the meal-photo scorer
# ---------------------------------------------------------------------------

class VisionUnavailable(Exception):
    pass


def _vision_call(base: str, model: str, api_key: str, prompt: str,
                 image_data_url: str, extra: dict, timeout: float) -> str:
    payload = {
        'model': model, 'temperature': 0, 'max_tokens': 512,
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image_url', 'image_url': {'url': image_data_url}},
        ]}],
        **extra,
    }
    try:
        resp = requests.post(f'{base}/chat/completions',
                             headers={'Authorization': f'Bearer {api_key}',
                                      'Content-Type': 'application/json'},
                             data=json.dumps(payload), timeout=timeout)
    except requests.RequestException as exc:
        raise VisionUnavailable(f'Network error: {exc}')
    if resp.status_code != 200:
        raise VisionUnavailable(f'HTTP {resp.status_code} {resp.text[:150]}')
    try:
        data = resp.json()
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise VisionUnavailable(f'Malformed response: {exc}')
    if not (content or '').strip():
        # A thinking model that truncated returns empty/near-empty content — the
        # bug that 503'd the meal scan. Treat as a failure so we try the next engine.
        fin = ''
        try:
            fin = data['choices'][0].get('finish_reason', '')
        except Exception:  # noqa: BLE001
            pass
        raise VisionUnavailable(f'empty content (finish_reason={fin})')
    return content


def vision_complete(prompt: str, image_data_url: str, *, timeout: float = 30.0) -> str:
    """One-shot IMAGE+text completion with automatic failover across
    VISION-capable engines, mirroring reasoning_complete for text.

    DeepSeek is deliberately ABSENT: its API (deepseek-chat/reasoner) is
    text-only and cannot see an image, so it can't score a meal photo. The
    chain is: gemini-2.5-flash → gemini-flash-latest → gemini-2.5-flash-lite
    (all same key) → Grok vision (a different provider, only if a GROK key is
    set). gemini-flash-latest is a self-updating ALIAS chosen on purpose: pinned
    versions get retired (gemini-2.0-flash now 404s "no longer available"), an
    alias does not. reasoning_effort:'none' stops the flash models from spending
    the whole token budget on hidden thinking (which truncated the score to
    '{\"' and 503'd the scan) — every model in the chain is verified to accept
    it (gemini-2.5-pro is excluded because it rejects a 0 thinking budget). Keys
    resolve vault→env via get_llm_key. Raises VisionUnavailable only if ALL
    configured vision engines fail; engines with no key are skipped."""
    gem_base = getattr(settings, 'GEMINI_API_BASE',
                       'https://generativelanguage.googleapis.com/v1beta/openai')
    gem_key = get_llm_key('GEMINI_API_KEY')
    gem_primary = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash')
    # Fallback flash models, in order. Config-overridable so retirements can be
    # handled without a code change. All must accept reasoning_effort:'none'.
    gem_fallbacks = getattr(settings, 'GEMINI_VISION_FALLBACKS',
                            ['gemini-flash-latest', 'gemini-2.5-flash-lite'])
    grok_base = getattr(settings, 'GROK_API_BASE', 'https://api.x.ai/v1')
    grok_key = get_llm_key('GROK_API_KEY')
    grok_model = getattr(settings, 'GROK_VISION_MODEL', 'grok-2-vision-1212')

    chain, seen = [], set()
    if gem_key:
        for mdl in [gem_primary, *gem_fallbacks]:
            if mdl and mdl not in seen:
                seen.add(mdl)
                chain.append(('Gemini/' + mdl, gem_base, mdl, gem_key, {'reasoning_effort': 'none'}))
    if grok_key:
        chain.append(('Grok/' + grok_model, grok_base, grok_model, grok_key, {}))
    if not chain:
        raise VisionUnavailable('No vision engine configured (need GEMINI_API_KEY or GROK_API_KEY).')

    errors = []
    for label, base, model, key, extra in chain:
        try:
            return _vision_call(base, model, key, prompt, image_data_url, extra, timeout)
        except VisionUnavailable as e:
            errors.append(f'{label}: {e}')
            log.warning('vision_complete: %s failed (%s) — trying next', label, e)
    raise VisionUnavailable('All vision engines failed — ' + '; '.join(errors))


# ---------------------------------------------------------------------------
# Local Ollama — FREE tier-0 of reasoning_complete (CFO directive 2026-07-18)
# ---------------------------------------------------------------------------

class OllamaUnavailable(Exception):
    pass


_THINK_RE = re.compile(r'(?s)^.*?</think>')


def ollama_complete(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
    response_format: Optional[str] = None,  # 'json_object' for JSON mode
    max_tokens: int = 800,
) -> str:
    """One-shot completion via a LOCAL Ollama (OpenAI-compatible /v1). Tier-0 of
    reasoning_complete: FREE and private, tried FIRST when a local model is
    reachable (OLLAMA_API_BASE set — e.g. the CFO's Mac). On the cloud server
    OLLAMA_API_BASE is empty, so this raises OllamaUnavailable immediately and the
    cascade falls straight through to cloud DeepSeek — i.e. "local first, else
    cloud" exactly where local is physically reachable, a no-op everywhere else.
    Reasoning models (deepseek-r1) prepend a <think>…</think> block; it is stripped
    so the caller gets a clean answer. No API key needed for local Ollama."""
    api_base = get_llm_key('OLLAMA_API_BASE') or getattr(settings, 'OLLAMA_API_BASE', '')
    if not api_base:
        raise OllamaUnavailable('OLLAMA_API_BASE not set — no local model reachable.')
    api_base = api_base.rstrip('/')
    model = model or getattr(settings, 'OLLAMA_MODEL', 'deepseek-r1:14b')

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})

    payload = {
        'model':       model,
        'messages':    messages,
        'temperature': 0.2,
        'max_tokens':  max_tokens,
        'stream':      False,
    }
    if response_format == 'json_object':
        payload['response_format'] = {'type': 'json_object'}

    headers = {'Content-Type': 'application/json'}
    try:
        resp = requests.post(
            f'{api_base}/chat/completions',
            headers=headers,
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OllamaUnavailable(f'Local Ollama unreachable: {exc}')

    if resp.status_code != 200:
        raise OllamaUnavailable(f'HTTP {resp.status_code}')

    try:
        content = resp.json()['choices'][0]['message']['content']
    except (KeyError, IndexError, ValueError) as exc:
        raise OllamaUnavailable(f'Malformed response: {exc}')

    # deepseek-r1 & other reasoning models: drop the <think>…</think> preamble.
    if '</think>' in content:
        content = _THINK_RE.sub('', content, count=1)
    return content.strip()


def _answer_inadequate(text: Optional[str], response_format: Optional[str] = None) -> bool:
    """True when a cheap-tier answer is too weak to trust, so reasoning_complete
    escalates to the next (stronger) tier. Deliberately NARROW — no second LLM to
    judge (that would double the cost and defeat the saving): a blank/whitespace
    answer, or (JSON mode only) output that will not parse as JSON. Everything else
    is accepted on the cheap tier."""
    if not text or not text.strip():
        return True
    if response_format == 'json_object':
        try:
            json.loads(text)
        except (ValueError, TypeError):
            return True
    return False


_ENGINE_MODEL_SETTING = {
    'DeepSeek': 'DEEPSEEK_MODEL', 'Gemini': 'GEMINI_MODEL', 'Ollama': 'OLLAMA_MODEL',
    'OpenAI': 'OPENAI_MODEL', 'Grok': 'GROK_MODEL', 'Anthropic': 'ANTHROPIC_MODEL',
}


def _log_ai_speed(feature: str, engine: str, ms: int, *, ok: bool, escalated: bool) -> None:
    """Best-effort write to the AI speed log (CFO directive 2026-07-19) — proves
    response times to the auditor and flags slowdowns. NEVER raises: a logging
    failure must not break the AI call. Stores timing + engine only, no content."""
    try:
        from core.models import AISpeedLog
        # When the DeepSeek kill-switch is on, the 'DeepSeek' cascade slot is
        # actually served by Gemini — record it truthfully so the audit log never
        # shows DeepSeek (China) rows after the cut-off (M3, Fable review 2026-07-19).
        if engine == 'DeepSeek' and not getattr(settings, 'DEEPSEEK_ENABLED', True):
            engine = 'Gemini(fallback)'
        setting_key = _ENGINE_MODEL_SETTING.get(engine.split('(')[0], '')
        model_name = str(getattr(settings, setting_key, '') or '')[:80]
        AISpeedLog.objects.create(
            feature=(feature or '')[:64], engine=(engine or '?')[:32],
            model_name=model_name, ms=max(0, int(ms)), ok=ok, escalated=escalated,
        )
    except Exception:  # noqa: BLE001 — logging must never break the AI call
        log.debug('ai speed log write skipped', exc_info=True)


def _accepted_kwargs(fn, kwargs: dict) -> dict:
    """The subset of `kwargs` that `fn` actually declares.

    Engines in the cascade have drifted apart: some take `max_tokens`, some do
    not. Filtering here keeps the fallback chain working instead of turning a
    signature mismatch into a dead feature.
    """
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}


def reasoning_complete(user_prompt: str, **kwargs) -> str:
    """Cheapest-capable engine first, escalate to premium ONLY when the cheap tier
    cannot do it (CFO directive 2026-07-18 — "review local first, else cloud").

    Order: local Ollama (FREE, if reachable) → DeepSeek (cheap cloud) → Gemini →
    OpenAI → Grok → Anthropic (last resort). "Cannot do it" = the engine errored,
    OR returned a blank answer, OR (JSON mode) returned unparseable JSON — then the
    next, stronger tier is tried. Most requests are served on the free/cheap tier;
    premium is only paid for when it is actually needed.

    Each key resolves CFO Secrets Vault → env (get_llm_key); an engine with no key
    (or, for Ollama, no OLLAMA_API_BASE) is skipped. Callers must pass text already
    cleared by is_safe_for_ai(). Raises DeepSeekUnavailable only if EVERY configured
    engine fails outright."""
    import time as _t
    _t0 = _t.monotonic()
    feature = kwargs.pop('feature', '') or ''      # optional caller label for the speed log
    chain = [
        ('Ollama',    ollama_complete,    OllamaUnavailable),
        ('DeepSeek',  deepseek_complete,  DeepSeekUnavailable),
        ('Gemini',    gemini_complete,    GeminiUnavailable),
        ('OpenAI',    openai_complete,    OpenAIUnavailable),
        ('Grok',      grok_complete,      GrokUnavailable),
        ('Anthropic', anthropic_complete, AnthropicUnavailable),
    ]
    response_format = kwargs.get('response_format')
    errors = []
    last_weak = None                      # best real-but-weak answer seen, as a floor
    for i, (name, fn, exc_cls) in enumerate(chain):
        is_last = (i == len(chain) - 1)
        # Not every engine takes every kwarg — grok_complete and
        # anthropic_complete have no `max_tokens`, so a caller that passes it
        # used to kill the whole fallback chain with a TypeError the moment the
        # cheap tiers failed, and the feature reported "AI unavailable" while
        # two working engines were never tried (found 20-Sep-2026 by running the
        # training-course generator against a real document). Pass each engine
        # only what it actually accepts.
        accepted = _accepted_kwargs(fn, kwargs)
        if len(accepted) != len(kwargs):
            dropped = sorted(set(kwargs) - set(accepted))
            log.debug('reasoning_complete: %s does not accept %s — omitted',
                      name, ', '.join(dropped))
        try:
            out = fn(user_prompt, **accepted)
        except exc_cls as e:
            errors.append(f'{name}: {e}')
            log.warning('reasoning_complete: %s unavailable (%s) — escalating', name, e)
            continue
        if not is_last and _answer_inadequate(out, response_format):
            if out and out.strip():
                last_weak = out           # keep it in case every richer tier fails
            errors.append(f'{name}: weak/blank answer')
            log.info('reasoning_complete: %s answer weak — escalating to next tier', name)
            continue
        if name not in ('Ollama', 'DeepSeek'):
            log.info('reasoning_complete: served by %s (escalated from cheap tier)', name)
        _log_ai_speed(feature, name, int((_t.monotonic() - _t0) * 1000), ok=True, escalated=(i > 0))
        return out
    if last_weak is not None:             # everything richer failed — don't lose a real answer
        log.warning('reasoning_complete: all tiers weak/failed — returning best cheap answer')
        _log_ai_speed(feature, 'weak', int((_t.monotonic() - _t0) * 1000), ok=True, escalated=True)
        return last_weak
    _log_ai_speed(feature, 'none', int((_t.monotonic() - _t0) * 1000), ok=False, escalated=True)
    raise DeepSeekUnavailable('All reasoning engines unavailable — ' + '; '.join(errors))


# ---------------------------------------------------------------------------
# High-level reasoning helpers — no sensitive data sent
# ---------------------------------------------------------------------------

ACCOUNT_SUGGESTION_SYSTEM = (
    'You are an experienced Botswana CFO. Given a free-text description of '
    'a transaction and a list of available chart-of-accounts entries, '
    'suggest the 3 most likely accounts to use. Respond ONLY with valid JSON '
    'of the form: {"suggestions":[{"code":"...","reason":"..."}]}. '
    'Keep reasons under 80 characters.'
)


def suggest_je_accounts(description: str, accounts: list[dict]) -> list[dict]:
    """
    Returns up to 3 suggested chart-of-accounts entries for a free-text
    JE description. *accounts* is a list of {code, name, account_type, sub_type}
    dicts (passed in by the caller — typically the active CoA).

    Returns [] if DeepSeek is unavailable, the input is unsafe, or the
    response can't be parsed. Caller treats AI suggestions as advisory.
    """
    safety = is_safe_for_ai(description or '')
    if not safety.safe:
        return []

    desc = safety.redacted_text.strip()
    if not desc:
        return []

    # Trim accounts to a sensible budget (avoid giant prompts)
    catalogue = [
        {'code': a['code'], 'name': a['name'],
         'type': a.get('account_type', ''), 'sub': a.get('sub_type', '')}
        for a in accounts[:200]
    ]

    user_prompt = (
        f'Transaction description: "{desc}"\n\n'
        f'Available accounts (JSON): {json.dumps(catalogue)}\n\n'
        'Return the JSON object with up to 3 suggestions.'
    )

    try:
        raw = deepseek_complete(
            user_prompt,
            system_prompt=ACCOUNT_SUGGESTION_SYSTEM,
            response_format='json_object',
        )
    except DeepSeekUnavailable:
        return []

    try:
        parsed = json.loads(raw)
        out = []
        for s in (parsed.get('suggestions') or [])[:3]:
            code = (s.get('code') or '').strip()
            reason = (s.get('reason') or '').strip()[:120]
            if code:
                out.append({'code': code, 'reason': reason})
        return out
    except (json.JSONDecodeError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Chart of Accounts / GL upload reviewers (CFO directive 2026-05-17)
# ---------------------------------------------------------------------------

_COA_REVIEW_SYSTEM = (
    'You are an experienced Botswana general insurance CFO reviewing a chart '
    'of accounts upload for Alpha Direct Insurance Company (BWP-denominated, '
    'July–June fiscal year). For each suspect account flag:\n'
    '- statement_class wrong (BS vs PNL),\n'
    '- normal_balance wrong (D for assets/expenses, C for liabilities/equity/revenue),\n'
    '- fs_line_item label inconsistent with similar accounts,\n'
    '- missing common accounts a Botswana insurer must have '
    '(GWP, Net Earned Premium, Gross/Net Claims, Reinsurance Recoverable, '
    'IBNR Reserve, Unearned Premium Reserve, Commission Expense, '
    'Bank, VAT Payable, WHT Payable, Share Capital, Retained Earnings).\n'
    'Respond ONLY with valid JSON: '
    '{"verdict":"clean|warnings|errors",'
    '"flagged_rows":[{"code":"...","field":"...","issue":"...","severity":"high|medium|low"}],'
    '"missing":["..."],'
    '"summary":"<one paragraph, under 400 chars>"}.'
)


def review_coa_upload(rows: list[dict], *, timeout: float = 20.0) -> dict:
    """
    Send CoA rows to DeepSeek for a second-pass review. Returns a verdict dict
    with flagged_rows, missing accounts, and a one-paragraph summary.

    Failure modes (DeepSeek unavailable, safety filter trip, malformed
    response) all return {'verdict':'unavailable', ...} — the caller treats
    the absence of a review as informational, never blocking.
    """
    if not rows:
        return {'verdict': 'unavailable', 'reason': 'no rows to review'}

    compact = [
        {
            'code': r.get('account_code', ''),
            'name': r.get('account_name', ''),
            'class': r.get('statement_class', ''),
            'fs': r.get('fs_line_item', ''),
            'dc': r.get('normal_balance', ''),
        }
        for r in rows[:300]  # cap to keep the prompt bounded
    ]

    payload_text = json.dumps(compact)
    safety = is_safe_for_ai(payload_text)
    if not safety.safe:
        return {'verdict': 'unavailable', 'reason': 'safety filter refused payload'}

    user_prompt = (
        f'Chart of Accounts upload preview ({len(rows)} rows, showing first '
        f'{len(compact)}):\n\n{safety.redacted_text}\n\n'
        'Review per the system prompt. Return JSON only.'
    )

    try:
        raw = deepseek_complete(
            user_prompt,
            system_prompt=_COA_REVIEW_SYSTEM,
            response_format='json_object',
            timeout=timeout,
        )
    except DeepSeekUnavailable as exc:
        return {'verdict': 'unavailable', 'reason': str(exc)}

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {'verdict': 'unavailable', 'reason': 'malformed AI response'}

    return {
        'verdict': (parsed.get('verdict') or 'unavailable')[:20],
        'flagged_rows': (parsed.get('flagged_rows') or [])[:50],
        'missing': (parsed.get('missing') or [])[:30],
        'summary': (parsed.get('summary') or '')[:400],
        'rows_reviewed': len(compact),
        'rows_total': len(rows),
    }


_GL_REVIEW_SYSTEM = (
    'You are an experienced Botswana general insurance CFO reviewing a '
    'general-ledger balances upload. For each suspect line flag:\n'
    '- amount on the wrong side (e.g. asset account posted as a credit),\n'
    '- statement_class wrong (BS vs PNL),\n'
    '- amounts that look orders-of-magnitude off given the account type,\n'
    '- duplicate-looking lines (same code, similar amounts).\n'
    'Also flag missing major accounts (a non-zero GWP, Bank, Claims, '
    'Acquisition / Commission Expense are mandatory for ADIC). Cite the '
    'aggregate totals provided.\n'
    'Respond ONLY with valid JSON: '
    '{"verdict":"clean|warnings|errors",'
    '"flagged_rows":[{"code":"...","issue":"...","severity":"high|medium|low"}],'
    '"missing":["..."],'
    '"summary":"<one paragraph, under 400 chars>"}.'
)


_TREATY_REVIEW_SYSTEM = (
    'You are an experienced Botswana general insurance CFO reviewing a '
    'batch of reinsurance treaty rows uploaded for the upcoming FY '
    '(Alpha Direct Insurance treaty year runs 1 July – 30 June). Flag:\n'
    '- proportional treaties (quota_share / surplus) missing cession_share_percent,\n'
    '- non-proportional treaties (xl / stop_loss) missing retention_amount or limit_amount,\n'
    '- treaty periods that do not align to the 1-Jul–30-Jun cycle,\n'
    '- expiry_date earlier than inception_date,\n'
    '- duplicate treaty_numbers,\n'
    '- unknown treaty_type values,\n'
    '- reinsurer codes that look mis-spelled or unfamiliar.\n'
    'Respond ONLY with valid JSON: '
    '{"verdict":"clean|warnings|errors",'
    '"flagged_rows":[{"treaty_number":"...","field":"...","issue":"...","severity":"high|medium|low"}],'
    '"summary":"<one paragraph, under 400 chars>"}.'
)


_ARIA_CHAT_SYSTEM = (
    'You are ARIA — Alpha Direct Insurance Company\'s on-dashboard AI '
    'analyst. Picture yourself as a rat-sized, sharp-suited (pink blazer, '
    'tortoiseshell glasses, black turtleneck) finance copilot who lives in '
    'the corner of the screen. You belong to the team — you speak as a '
    'colleague, not a chatbot. The user is an Alpha Direct staff member or '
    'executive looking at the Omni ERP.\n'
    '\n'
    'Voice (CFO directive 2026-05-28 — super human, super cool):\n'
    '  - Concise, factual, warm. Confident, not chirpy. Botswana-Botswana, '
    '    not Silicon Valley.\n'
    '  - Address the user by first name when natural — never twice in the '
    '    same reply. Vary your openers; never two replies in a row that '
    '    start the same way.\n'
    '  - When you have nothing useful to add, say so in one line and offer '
    '    the next sensible action. Never pad.\n'
    '  - If the previous turn left a thread open ("what about the other '
    '    company?", "and last month?"), continue it without re-asking the '
    '    obvious.\n'
    '  - Cite sources for any number you quote ("Sources: ma_pl_spec / '
    '    Mar-2026 MA workbook" etc.) in a tiny footer line — keep it '
    '    one short line.\n'
    '  - Offer one concrete next click per reply when relevant: a route '
    '    like `/reports/trial-balance` or `/bills` so the user can act in '
    '    one tap.\n'
    '\n'
    'Grounding rules (non-negotiable):\n'
    '  - When asked about figures, ONLY quote numbers from the dashboard '
    '    context provided in the user prompt. If a number isn\'t in the '
    '    context, say so plainly. NEVER invent figures.\n'
    '  - Insurance terms: GWP = Gross Written Premium, NEP = Net Earned '
    '    Premium, RI = Reinsurance, IBNR = Incurred But Not Reported, '
    '    UPR = Unearned Premium Reserve, BWP = Botswana Pula. Alpha Direct\'s '
    '    fiscal year is 1 July – 30 June.\n'
    '\n'
    'Screen how-tos (state these accurately — never invent fields):\n'
    '  - Bank Reconciliation (/banking): to reconcile, click the '
    '    "Upload Statement" button, choose the account from the "Bank '
    '    Account" picker (a dropdown — beside the Upload Statement button '
    '    on the panel, and again inside the upload dialog), attach the '
    '    CSV/OFX/QFX/XLSX file, then Upload. The statement binds to that '
    '    account automatically and is remembered on re-open. There is NO '
    '    free-text "Bank Account / ID" field — selection is always a '
    '    dropdown of configured accounts, never a typed account number.\n'
    '\n'
    'Compliance reminder duty (CFO directive 2026-05-22):\n'
    '  - The context block may include `upcoming_deadlines`, `urgent_deadlines`, '
    '    `pending_approvals`, `tb_status`, `open_exceptions`, and `route_hint`. '
    '    USE THEM.\n'
    '  - If `urgent_deadlines` is non-empty, lead the reply with the most '
    '    urgent item (name, days_left, authority) — even if the user asked '
    '    about something else, mention it in one short line at the top.\n'
    '  - When the user asks "what\'s due" / "deadlines" / "VAT" / "BURS" / '
    '    "NBFIRA" / "PAYE", list the next 3-5 items from `upcoming_deadlines` '
    '    as a compact bullet list with due_date + days_left + authority.\n'
    '  - When `tb_status.balanced` is false, flag it once and suggest '
    '    /reports/trial-balance.\n'
    '  - When `pending_approvals.total` > 0, mention the queue depth once.\n'
    '\n'
    'Tone: terse, direct, sharp-sharp. Botswana cultural fit — occasional '
    'Setswana phrase ("akere", "sharp sharp", "tlhe") is welcome but never '
    'crude. Default to under 150 words unless the user asks for detail.'
)


_ARIA_MOOD_TAILS = {
    'formal':
        'Tone: formal, neutral, board-paper register. No slang, no Setswana, '
        'no emoji. Use full sentences.',
    'sharp':
        'Tone: terse, direct, Botswana sharp-sharp. Short sentences. '
        'Occasional Setswana phrase ("akere", "sharp sharp", "tlhe", '
        '"ke yone eo") welcome where it fits. No emoji unless asked.',
    'naughty':
        'Tone: playful and a little cheeky. May tease the user lightly when '
        'they ask trivial things. Banter is allowed; vulgarity, sexual '
        'content, or mocking-by-name are NOT. May address the user by name '
        'with mock-formality ("Right, boss"). Setswana banter encouraged. '
        'Always factually correct first, sass after.',
}


# ---------------------------------------------------------------------------
# ARIA tool-use loop (CFO directive 2026-05-24)
#
# DeepSeek supports OpenAI-style function calling via `tools=[...]` +
# `tool_calls` in the response. We expose every callable in
# `core/aria/tools.py` as a tool, plus a NL period parser, so ARIA can
# fetch the exact numbers it needs (e.g. P&L for "March 2026") instead
# of guessing from the dashboard snapshot.
#
# The loop is bounded (max 5 iterations) and behaves identically to the
# legacy one-shot call when the model returns no tool_calls.
# ---------------------------------------------------------------------------

_ARIA_TOOL_NAMES = (
    'get_tb_status',
    'get_pl_summary',
    'get_pending_approvals',
    'get_anomaly_count',
    'route_hint',
    'parse_period_phrase',
)

_ARIA_QC_TOOL_NAMES = (
    'search_payments',
    'count_pending',
    'lookup_staff',
    'approve_payment',
    'reject_payment',
    'recent_audit_log',
    'send_popup_message',
)

# JSON-schema parameter specs — DeepSeek expects strict JSON Schema.
# We keep them hand-written (vs inspect-derived) so the model sees
# precise, business-meaningful descriptions for each argument.
_ARIA_TOOL_SCHEMAS = {
    'get_tb_status': {
        'type': 'object',
        'properties': {
            'as_of_date': {
                'type':        'string',
                'description': 'ISO date (YYYY-MM-DD). Defaults to today.',
            },
            'company_id': {
                'type':        'string',
                'description': 'Optional company UUID to scope the TB.',
            },
        },
        'required': [],
    },
    'get_pl_summary': {
        'type': 'object',
        'properties': {
            'from_date': {
                'type':        'string',
                'description': 'Period start, ISO date YYYY-MM-DD.',
            },
            'to_date': {
                'type':        'string',
                'description': 'Period end, ISO date YYYY-MM-DD.',
            },
            'company_id': {
                'type':        'string',
                'description': 'Optional company UUID to scope the P&L.',
            },
        },
        'required': ['from_date', 'to_date'],
    },
    'get_pending_approvals': {
        'type':       'object',
        'properties': {},
        'required':   [],
    },
    'get_anomaly_count': {
        'type':       'object',
        'properties': {
            'as_of_date': {
                'type':        'string',
                'description': 'ISO date YYYY-MM-DD. Defaults to today.',
            },
        },
        'required': [],
    },
    'route_hint': {
        'type': 'object',
        'properties': {
            'path': {
                'type':        'string',
                'description': 'URL path, e.g. /reports/profit-loss.',
            },
        },
        'required': ['path'],
    },
    'parse_period_phrase': {
        'type': 'object',
        'properties': {
            'phrase': {
                'type':        'string',
                'description': (
                    'Natural-language period: "March 2026", "FY25", '
                    '"FY26 9M", "Q1 FY26", "this month", "last quarter", '
                    '"YTD", etc.'
                ),
            },
        },
        'required': ['phrase'],
    },
}

_ARIA_QC_TOOL_SCHEMAS = {
    'search_payments': {
        'type': 'object',
        'properties': {
            'payee': {'type': 'string', 'description': 'Payee name or subject keyword.'},
            'ref': {'type': 'string', 'description': 'Payment reference number (e.g. PAY-000123).'},
            'status': {'type': 'string', 'description': 'Filter: pending_finance, pending_cfo, paid, rejected, cancelled, exception, draft.'},
        },
        'required': [],
    },
    'count_pending': {'type': 'object', 'properties': {}, 'required': []},
    'lookup_staff': {
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'Staff member name (first, last, or both).'},
            'email': {'type': 'string', 'description': 'Email address or part of it.'},
        },
        'required': [],
    },
    'approve_payment': {
        'type': 'object',
        'properties': {
            'ref': {'type': 'string', 'description': 'Payment reference to approve. ALWAYS confirm with the user first.'},
            'notes': {'type': 'string', 'description': 'Optional approval notes.'},
        },
        'required': ['ref'],
    },
    'reject_payment': {
        'type': 'object',
        'properties': {
            'ref': {'type': 'string', 'description': 'Payment reference to reject.'},
            'reason': {'type': 'string', 'description': 'Reason for rejection (required).'},
        },
        'required': ['ref', 'reason'],
    },
    'recent_audit_log': {
        'type': 'object',
        'properties': {
            'table': {'type': 'string', 'description': 'Filter by table name (e.g. paymentrequest).'},
        },
        'required': [],
    },
    'send_popup_message': {
        'type': 'object',
        'properties': {
            'recipient_name': {'type': 'string', 'description': 'Full or partial name of the staff member to message.'},
            'message': {'type': 'string', 'description': 'The message text to display in their popup.'},
            'confirm_recipient': {
                'type': 'string',
                'description': ('Leave EMPTY on the first call — that call never sends, it only '
                                'reports who the name matched. After the CFO confirms, call again '
                                'with the exact confirm_recipient value from that reply.'),
            },
        },
        'required': ['recipient_name', 'message'],
    },
}

_ARIA_QC_TOOL_DESCRIPTIONS = {
    'search_payments':      'Search payment requests by payee name, subject, reference number, or status.',
    'count_pending':        'Count payments waiting at each stage (finance sign-off, CFO, exceptions, drafts) and open tasks.',
    'lookup_staff':         'Look up a staff member by name or email.',
    'approve_payment':      'Prepare approval of a payment request. It never approves by itself: it puts a Confirm card with the details on the user\'s screen, and only their tap approves.',
    'reject_payment':       'Prepare rejection of a payment request with a reason. It never rejects by itself: the user taps Confirm on the card that appears.',
    'recent_audit_log':     'Fetch recent audit log entries, optionally filtered by table name.',
    'send_popup_message':   'Send a popup message to a staff member (CFO only). A large overlay appears on their screen with the message and a Close button. ALWAYS confirm the recipient name and message with the user first.',
}

_ARIA_TOOL_DESCRIPTIONS = {
    'get_tb_status':         'Return whether the trial balance is balanced as of a date.',
    'get_pl_summary':        'Return GWP / NEP / Gross Profit / EBITDA / PBT / PAT totals for a date range. Always call parse_period_phrase first if the user supplied a natural-language period.',
    'get_pending_approvals': 'Return counts of JEs and POs awaiting approval.',
    'get_anomaly_count':     'Return the number of open Exceptions raised by the integrity scan.',
    'route_hint':            'Map a dashboard URL path to a one-line hint about the screen.',
    'parse_period_phrase':   'Translate "March 2026" / "FY25" / "Q1 FY26" / "this quarter" into ISO from_date and to_date. ALWAYS use this before calling get_pl_summary with a user-supplied period.',
}


def _aria_tool_specs(*, power_user: bool = False) -> list[dict]:
    """Build the OpenAI-compatible tools=[...] payload list."""
    names = list(_ARIA_TOOL_NAMES)
    schemas = dict(_ARIA_TOOL_SCHEMAS)
    descs = dict(_ARIA_TOOL_DESCRIPTIONS)
    if power_user:
        names += list(_ARIA_QC_TOOL_NAMES)
        schemas.update(_ARIA_QC_TOOL_SCHEMAS)
        descs.update(_ARIA_QC_TOOL_DESCRIPTIONS)
    return [
        {
            'type':     'function',
            'function': {
                'name':        name,
                'description': descs[name],
                'parameters':  schemas[name],
            },
        }
        for name in names
    ]


def _aria_execute_tool(name: str, args: dict, *, user, dashboard_context: dict) -> dict:
    """Dispatch a tool_call to the right helper. Returns a JSON-serialisable dict.

    Errors are wrapped — we never raise out of the loop so the model can
    self-correct on the next iteration.
    """
    from datetime import date as _date
    args = args or {}

    try:
        if name == 'parse_period_phrase':
            from core.aria.period_parser import parse_period_phrase
            phrase = args.get('phrase') or ''
            start, end = parse_period_phrase(phrase)
            return {
                'from_date': start.isoformat(),
                'to_date':   end.isoformat(),
                'phrase':    phrase,
            }

        if name == 'get_tb_status':
            from core.aria.tools import get_tb_status
            as_of = _parse_iso(args.get('as_of_date'))
            company_id = (
                args.get('company_id')
                or (dashboard_context or {}).get('selected_company_id')
            )
            return get_tb_status(today=as_of, company_id=company_id)

        if name == 'get_pl_summary':
            from core.aria.tools import get_pl_summary
            from_d = _parse_iso(args.get('from_date'))
            to_d   = _parse_iso(args.get('to_date'))
            if from_d is None or to_d is None:
                return {'error': 'from_date and to_date are required (ISO YYYY-MM-DD).'}
            company_id = (
                args.get('company_id')
                or (dashboard_context or {}).get('selected_company_id')
            )
            return get_pl_summary(from_d, to_d, company_id=company_id)

        if name == 'get_pending_approvals':
            from core.aria.tools import get_pending_approvals
            return get_pending_approvals(user)

        if name == 'get_anomaly_count':
            from core.aria.tools import get_anomaly_count
            as_of = _parse_iso(args.get('as_of_date'))
            return get_anomaly_count(today=as_of)

        if name == 'route_hint':
            from core.aria.tools import route_hint
            return {'hint': route_hint(args.get('path') or '')}

        # QC / action tools (power users only)
        if name == 'search_payments':
            from core.aria.qc_tools import search_payments
            return search_payments(payee=args.get('payee', ''), ref=args.get('ref', ''), status=args.get('status', ''))
        if name == 'count_pending':
            from core.aria.qc_tools import count_pending
            return count_pending(user=user)
        if name == 'lookup_staff':
            from core.aria.qc_tools import lookup_staff
            return lookup_staff(name=args.get('name', ''), email=args.get('email', ''))
        if name == 'approve_payment':
            from core.aria.qc_tools import approve_payment
            return approve_payment(ref=args.get('ref', ''), user=user, notes=args.get('notes', ''))
        if name == 'reject_payment':
            from core.aria.qc_tools import reject_payment
            return reject_payment(ref=args.get('ref', ''), user=user, reason=args.get('reason', ''))
        if name == 'recent_audit_log':
            from core.aria.qc_tools import recent_audit_log
            return recent_audit_log(table=args.get('table', ''))
        if name == 'send_popup_message':
            from core.aria.qc_tools import send_popup_message
            return send_popup_message(recipient_name=args.get('recipient_name', ''),
                                      message=args.get('message', ''),
                                      confirm_recipient=args.get('confirm_recipient', '') or '',
                                      user=user)

        return {'error': f'unknown tool: {name}'}
    except Exception as exc:    # noqa: BLE001
        log.warning('ARIA tool %s failed: %s', name, exc)
        return {'error': str(exc)[:300]}


def _parse_iso(s):
    if not s:
        return None
    from datetime import date as _date
    try:
        y, m, d = str(s).split('-')
        return _date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def aria_chat(
    user_message: str,
    *,
    user_first_name: str = '',
    dashboard_context: dict | None = None,
    history: list[dict] | None = None,
    mood: str = 'sharp',
    timeout: float = 25.0,
    user=None,
    max_tool_iterations: int = 5,
    power_user: bool = False,
) -> dict:
    """One-turn chat with DeepSeek for the Aria on-dashboard assistant.

    Returns {'ok': True, 'reply': str, 'tool_trace': [...]} on success, or
    {'ok': False, 'reason': str} on failure. Never raises.

    `dashboard_context` is the headline KPI dict from the dashboard (cash,
    GWP, claims, payables, receivables, net profit, etc.) — passed in so
    Aria can answer 'how is cash doing?' without us calling the API again.
    `history` is the prior turn list ([{role:'user'|'assistant', content:str}, ...]).

    Tool-use loop (CFO directive 2026-05-24): if DeepSeek returns
    `tool_calls` in any iteration we execute them and feed the JSON
    result back as a `role:tool` message, then re-prompt. Loop is bounded
    by `max_tool_iterations`. If `tool_calls` is absent on the first
    response we behave exactly like the legacy one-shot path.
    """
    if not user_message or not user_message.strip():
        return {'ok': False, 'reason': 'empty message'}

    safety = is_safe_for_ai(user_message)
    if not safety.safe:
        return {'ok': False, 'reason': 'safety filter blocked the message'}

    ctx_lines = []
    if user_first_name:
        ctx_lines.append(f'User: {user_first_name}.')
    if dashboard_context:
        ctx_lines.append('Dashboard snapshot (BWP, current FY view):')
        for k, v in list(dashboard_context.items())[:30]:
            ctx_lines.append(f'  {k}: {v}')
    context_block = '\n'.join(ctx_lines)

    user_prompt = (
        (f'{context_block}\n\n' if context_block else '') +
        f'User asks: {safety.redacted_text.strip()}'
    )

    mood_tail = _ARIA_MOOD_TAILS.get(mood, _ARIA_MOOD_TAILS['sharp'])
    tool_hint = (
        '\n\nTool use: you have function-calling tools available. When the '
        'user asks about figures for a specific period, call '
        '`parse_period_phrase` to convert the phrase into ISO dates, then '
        '`get_pl_summary` to fetch the numbers. Quote ONLY values returned '
        'by tool calls or already in the context block — never invent.'
    )
    if power_user:
        tool_hint += (
            '\n\nYou also have ACTION tools: search_payments, count_pending, '
            'lookup_staff, approve_payment, reject_payment, recent_audit_log, '
            'send_popup_message. '
            'You can search payments, approve/reject them, look up staff, and '
            'send popup messages to staff on the CFO\'s behalf. '
            'CRITICAL SAFETY RULES: '
            '(1) Before approve_payment or reject_payment, ALWAYS show the details '
            'and get explicit YES. '
            '(2) send_popup_message ALWAYS takes two calls and the first one never '
            'sends. Call it first WITHOUT confirm_recipient; it replies with who the '
            'name matched. Show the CFO that person and the exact message, ask '
            '"Shall I send this?", and only after they say yes call it again with '
            'confirm_recipient set to the exact value from that reply. '
            '(3) If it returns several matches, show them and ask which one — never '
            'guess, and never invent a confirm_recipient value. '
            'The user can speak English or Setswana — understand both. '
            'All actions are recorded under the real user\'s name.'
        )
    system_prompt = _ARIA_CHAT_SYSTEM + '\n\n' + mood_tail + tool_hint
    messages = [{'role': 'system', 'content': system_prompt}]
    for h in (history or [])[-6:]:  # cap history at last 6 turns
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': str(h['content'])[:2000]})
    messages.append({'role': 'user', 'content': user_prompt})

    api_key  = getattr(settings, 'DEEPSEEK_API_KEY', '') or ''
    api_base = getattr(settings, 'DEEPSEEK_API_BASE', 'https://api.deepseek.com')
    model    = getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat')
    if not api_key:
        return {'ok': False, 'reason': 'DEEPSEEK_API_KEY not configured'}

    tools_payload = _aria_tool_specs(power_user=power_user)
    _allowed_names = set(_ARIA_TOOL_NAMES) | (set(_ARIA_QC_TOOL_NAMES) if power_user else set())
    tool_trace: list[dict] = []
    pending_actions: list[dict] = []

    for iteration in range(max_tool_iterations):
        payload = {
            'model':       model,
            'messages':    messages,
            'temperature': 0.6,
            'max_tokens':  1000 if power_user else 600,
            'tools':       tools_payload,
            'tool_choice': 'auto',
        }
        try:
            resp = requests.post(
                f'{api_base}/chat/completions',
                headers={'Authorization': f'Bearer {api_key}',
                         'Content-Type': 'application/json'},
                data=json.dumps(payload),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return {'ok': False, 'reason': f'network: {exc}'}
        if resp.status_code != 200:
            return {'ok': False, 'reason': f'HTTP {resp.status_code}'}

        try:
            data    = resp.json()
            choice  = data['choices'][0]
            message = choice.get('message') or {}
        except (KeyError, IndexError, ValueError) as exc:
            return {'ok': False, 'reason': f'malformed: {exc}'}

        tool_calls = message.get('tool_calls') or []
        content    = message.get('content') or ''

        # No more tool calls → we have the final answer.
        if not tool_calls:
            return {
                'ok':              True,
                'reply':           (content or '').strip(),
                'tool_trace':      tool_trace,
                'pending_actions': pending_actions,
            }

        # Echo the assistant turn (with tool_calls intact) back into the
        # message list — the OpenAI-style protocol requires this so each
        # `role: tool` response can reference a `tool_call_id`.
        messages.append({
            'role':       'assistant',
            'content':    content or '',
            'tool_calls': tool_calls,
        })

        for tc in tool_calls:
            tc_id  = tc.get('id') or f'call_{iteration}'
            fn     = (tc.get('function') or {})
            name   = fn.get('name') or ''
            raw_a  = fn.get('arguments') or '{}'
            try:
                args = json.loads(raw_a) if isinstance(raw_a, str) else (raw_a or {})
            except (json.JSONDecodeError, TypeError):
                args = {}
            if name not in _allowed_names:
                tool_result = {'error': f'unknown tool: {name}'}
            else:
                tool_result = _aria_execute_tool(
                    name, args,
                    user=user,
                    dashboard_context=dashboard_context or {},
                )
            if isinstance(tool_result, dict) and tool_result.get('confirm_token'):
                # The token goes to the user's Confirm card, never to the model
                # (which could otherwise "confirm" on its own) nor to the stored trace.
                pending_actions.append(dict(tool_result))
                tool_result = {k: v for k, v in tool_result.items() if k != 'confirm_token'}
                tool_result['message'] = (
                    'Nothing has been done yet. A Confirm card with these details is now on '
                    "the user's screen; only their tap can complete it. Tell them to check "
                    'the details and tap Confirm or Cancel. You cannot complete it yourself.')
            tool_trace.append({
                'iteration': iteration,
                'name':      name,
                'arguments': args,
                'result':    tool_result,
            })
            messages.append({
                'role':         'tool',
                'tool_call_id': tc_id,
                'name':         name,
                'content':      json.dumps(tool_result, default=str),
            })

    # Exhausted the loop without a final text-only reply.
    return {
        'ok':         False,
        'reason':     'tool-use loop exceeded max iterations',
        'tool_trace': tool_trace,
    }


def review_treaty_upload(rows: list[dict], *, timeout: float = 20.0) -> dict:
    """Review a batch of reinsurance treaty rows via DeepSeek. Same advisory
    contract as review_coa_upload / review_gl_upload."""
    if not rows:
        return {'verdict': 'unavailable', 'reason': 'no rows to review'}

    compact = [
        {
            'treaty_number': r.get('treaty_number', ''),
            'description': r.get('description', ''),
            'reinsurer_code': r.get('reinsurer_code', ''),
            'treaty_type': r.get('treaty_type', ''),
            'lob': r.get('line_of_business', ''),
            'inception': r.get('inception_date', ''),
            'expiry': r.get('expiry_date', ''),
            'cession_share_percent': r.get('cession_share_percent', ''),
            'commission_percent': r.get('commission_percent', ''),
            'retention': r.get('retention_amount', ''),
            'limit': r.get('limit_amount', ''),
        }
        for r in rows[:200]
    ]
    payload_text = json.dumps(compact)
    safety = is_safe_for_ai(payload_text)
    if not safety.safe:
        return {'verdict': 'unavailable', 'reason': 'safety filter refused payload'}

    user_prompt = (
        f'Reinsurance treaty upload preview ({len(rows)} rows, showing first '
        f'{len(compact)}):\n\n{safety.redacted_text}\n\n'
        'Review per the system prompt. Return JSON only.'
    )

    try:
        raw = deepseek_complete(
            user_prompt,
            system_prompt=_TREATY_REVIEW_SYSTEM,
            response_format='json_object',
            timeout=timeout,
        )
    except DeepSeekUnavailable as exc:
        return {'verdict': 'unavailable', 'reason': str(exc)}

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {'verdict': 'unavailable', 'reason': 'malformed AI response'}

    return {
        'verdict': (parsed.get('verdict') or 'unavailable')[:20],
        'flagged_rows': (parsed.get('flagged_rows') or [])[:50],
        'summary': (parsed.get('summary') or '')[:400],
        'rows_reviewed': len(compact),
        'rows_total': len(rows),
    }


def review_gl_upload(
    rows: list[dict], *, totals: dict, as_of_date: str, timeout: float = 20.0,
) -> dict:
    """Same shape as review_coa_upload but for GL balance rows."""
    if not rows:
        return {'verdict': 'unavailable', 'reason': 'no rows to review'}

    compact = [
        {
            'code': r.get('account_code', ''),
            'name': r.get('account_name', ''),
            'amount': r.get('amount', ''),
            'dc': r.get('dc', ''),
            'class': r.get('statement_class', ''),
        }
        for r in rows[:300]
    ]
    payload_text = json.dumps({'rows': compact, 'totals': totals,
                               'as_of_date': as_of_date})
    safety = is_safe_for_ai(payload_text)
    if not safety.safe:
        return {'verdict': 'unavailable', 'reason': 'safety filter refused payload'}

    user_prompt = (
        f'General Ledger upload preview as of {as_of_date} '
        f'({len(rows)} rows, showing first {len(compact)}; '
        f'totals: {json.dumps(totals)}):\n\n{safety.redacted_text}\n\n'
        'Review per the system prompt. Return JSON only.'
    )

    try:
        raw = deepseek_complete(
            user_prompt,
            system_prompt=_GL_REVIEW_SYSTEM,
            response_format='json_object',
            timeout=timeout,
        )
    except DeepSeekUnavailable as exc:
        return {'verdict': 'unavailable', 'reason': str(exc)}

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {'verdict': 'unavailable', 'reason': 'malformed AI response'}

    return {
        'verdict': (parsed.get('verdict') or 'unavailable')[:20],
        'flagged_rows': (parsed.get('flagged_rows') or [])[:50],
        'missing': (parsed.get('missing') or [])[:30],
        'summary': (parsed.get('summary') or '')[:400],
        'rows_reviewed': len(compact),
        'rows_total': len(rows),
    }


# ---------------------------------------------------------------------------
# Upload-failure explainer — runs on the commit error path
# ---------------------------------------------------------------------------

def explain_upload_failure(
    section: str,
    errors: list,
    *,
    missing_required: list | None = None,
    sample_headers: list | None = None,
    expected_fields: list | None = None,
    timeout: float = 6.0,
) -> dict:
    """
    Ask DeepSeek to translate a list of raw commit errors into operator-
    friendly guidance ("what went wrong" + "what to change in the CSV").

    No row data is sent — only the section key, the textual errors,
    optional CSV headers (column names are not PII), and the schema's
    expected field names. Falls back to a deterministic message when
    DeepSeek is unavailable so the user always sees something useful.

    Returns:
        {
          'explanation': '...',     # plain-English reason
          'suggested_fix': '...',   # concrete next step (e.g. add column X)
          'source': 'deepseek'|'fallback',
        }
    """
    errs_clean = [str(e)[:300] for e in (errors or [])][:6]
    miss = list(missing_required or [])[:10]
    hdrs = [str(h)[:80] for h in (sample_headers or [])][:30]
    exp = list(expected_fields or [])[:30]

    # Fast deterministic fallback first — works without DeepSeek.
    fallback = _explain_upload_failure_fallback(section, errs_clean, miss, hdrs, exp)

    try:
        system = (
            'You are an ERP onboarding helper for Alpha Direct. The user '
            'uploaded a CSV for a smart-upload section and the commit '
            'returned errors. Translate the raw errors into ONE short '
            'paragraph an accountant can act on. Return ONLY JSON: '
            '{"explanation": "...", "suggested_fix": "..."}. Each field '
            'under 240 characters. No code, no markdown.'
        )
        user = (
            f'Section: {section}\n'
            f'Expected fields: {exp}\n'
            f'CSV headers (raw): {hdrs}\n'
            f'Missing required: {miss}\n'
            f'Raw errors:\n- ' + '\n- '.join(errs_clean)
        )
        raw = deepseek_complete(
            user, system_prompt=system, timeout=timeout,
            response_format='json_object',
        )
    except DeepSeekUnavailable as exc:
        log.info('explain_upload_failure: DeepSeek unavailable (%s)', exc)
        return {**fallback, 'source': 'fallback'}

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {**fallback, 'source': 'fallback'}

    explanation = (parsed.get('explanation') or '').strip()[:400]
    fix         = (parsed.get('suggested_fix') or '').strip()[:400]
    if not explanation:
        return {**fallback, 'source': 'fallback'}
    return {
        'explanation': explanation,
        'suggested_fix': fix or fallback['suggested_fix'],
        'source': 'deepseek',
    }


def _explain_upload_failure_fallback(section, errors, missing, headers, expected):
    """Deterministic explanation used when DeepSeek is unreachable."""
    # Recognise the common error patterns and respond directly.
    joined = ' | '.join(errors).lower()
    if 'no usable period_end' in joined:
        return {
            'explanation': (
                'The CSV has no column the importer could map to the required '
                f'`period_end` field. Section `{section}` needs a date column '
                'naming the last day of the period covered.'
            ),
            'suggested_fix': (
                'Rename your date column to `period_end` (values like '
                '`2025-06-30`) and re-upload. Headers like "Period End", '
                '"As of", or "Date" usually auto-map; if yours did not, edit '
                'the CSV header.'
            ),
        }
    if 'tb unbalanced' in joined:
        return {
            'explanation': (
                'The trial balance does not balance. Total debits must equal '
                'total credits within 0.05.'
            ),
            'suggested_fix': (
                'Open the CSV, sum the debit and credit columns separately, '
                'and fix the difference shown in the error. A rounding row of '
                '<= 5 cents is fine; anything bigger means data is missing.'
            ),
        }
    if 'not in coa' in joined or 'account does not exist' in joined:
        return {
            'explanation': (
                'One or more account codes in the CSV do not exist in the '
                "company's Chart of Accounts. Those rows are skipped."
            ),
            'suggested_fix': (
                'Either correct the typo on the offending rows or upload the '
                'missing accounts via Smart Upload → Chart of Accounts first.'
            ),
        }
    if missing:
        return {
            'explanation': (
                f'Required column(s) missing from the CSV header: '
                f'{", ".join(missing)}.'
            ),
            'suggested_fix': (
                f'Add the missing column(s) to your CSV (rename headers if '
                f'they exist under a different name). Expected fields for '
                f'`{section}`: {", ".join(expected)}.'
            ),
        }
    # Generic fallback
    head = errors[0] if errors else 'Unknown error.'
    return {
        'explanation': f'Commit failed: {head}',
        'suggested_fix': (
            'Re-check the CSV against the section template and the '
            'preview screen, then upload again.'
        ),
    }


# ---------------------------------------------------------------------------
# Reconciliation exception explanation — CFO directive 2026-05-24.
# Used by reporting/management/commands/reconcile_balances.py.
# Sends the metric, period, two source labels + values, and asks
# DeepSeek to name the likely cause + suggested fix in JSON.
# ---------------------------------------------------------------------------

_RECON_SYSTEM_PROMPT = (
    'You are a senior insurance-finance ERP engineer reviewing a '
    'reconciliation exception. Two builders read the same posted journal '
    'entry lines for ADIC and disagreed on a financial number. Likely '
    'causes are: '
    '(a) MA spec gap — a GL code not enumerated in ma_pl_spec.MA_LINES '
    'or ma_bs_spec.SECTIONS; '
    '(b) sign convention mismatch — income vs expense direction flipped '
    'in a section subtotal; '
    '(c) hard-coded GL prefix override that differs between builders; '
    "(d) is_active / is_archived filter applied on one path only; "
    '(e) fs_line_item label drift — the team\'s upload label does not '
    'match the spec label byte-for-byte; '
    '(f) period filter difference — one builder cumulative since launch, '
    'the other range-filtered. '
    'Respond with strict JSON only. No prose, no markdown.'
)

_RECON_USER_TEMPLATE = (
    'Company: {company_code}\n'
    'Period: {period_label} ({period_start} -> {period_end})\n'
    'Metric: {metric}\n'
    '\n'
    'Source A: {source_a_name} = BWP {source_a_value:,.2f}\n'
    'Source B: {source_b_name} = BWP {source_b_value:,.2f}\n'
    'Delta:    BWP {delta_bwp:+,.2f}  ({delta_pct:+.2f}%)\n'
    '\n'
    'Return JSON: '
    '{{"cause": "<one or two sentences>", '
    '"suggested_fix": "<one or two sentences naming the file/spec to edit>", '
    '"confidence": <0.0 to 1.0>}}'
)


def explain_reconciliation(row) -> dict:
    """Ask DeepSeek to explain a Reconciliation exception.

    `row` is a reporting.models.Reconciliation instance. Returns a dict
    with keys cause, suggested_fix, confidence. Raises
    DeepSeekUnavailable if the API key is missing or the call fails;
    caller is expected to catch + log.
    """
    prompt = _RECON_USER_TEMPLATE.format(
        company_code   = getattr(row.company, 'code', '?'),
        period_label   = row.period_label,
        period_start   = row.period_start,
        period_end     = row.period_end,
        metric         = row.metric,
        source_a_name  = row.source_a_name,
        source_a_value = float(row.source_a_value),
        source_b_name  = row.source_b_name,
        source_b_value = float(row.source_b_value),
        delta_bwp      = float(row.delta_bwp),
        delta_pct      = float(row.delta_pct),
    )
    raw = deepseek_complete(
        prompt,
        system_prompt=_RECON_SYSTEM_PROMPT,
        response_format='json_object',
        timeout=25.0,
    )
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # Salvage what we can rather than throw.
        return {
            'cause':         (raw or '')[:500],
            'suggested_fix': '',
            'confidence':    0.0,
        }
    return {
        'cause':         str(data.get('cause', ''))[:2000],
        'suggested_fix': str(data.get('suggested_fix', ''))[:2000],
        'confidence':    float(data.get('confidence', 0) or 0),
    }
