"""
core/compliance_brain.py — nightly Alpha Brain → local-AI compliance feed
(CFO directive 2026-07-24).

Instead of the Compliance/AML dashboard linking out to Graphite, we FETCH a
PII-FREE aggregate summary from Alpha Brain (the Graphite bolt-on), let OUR OWN
AI read it at midnight, and store the plain-English result. The dashboard then
shows fresh, analysed figures each morning.

Hard rules (AD-POL-AI-GOV-001):
- Only AGGREGATE counts are analysed — never customer rows.
  **Note what this actually reads.** Alpha Brain has no counts-only endpoint;
  `/api/summary` and friends do not exist. The only feed carrying the figures is
  `/api/queue`, and the same response also carries `teams` — the exception rows,
  with client names and policy numbers. So we fetch that and strip it to
  `_AGGREGATE_KEYS` at the moment of receipt, the same whitelist Graphite's own
  `BrainProxyController::summary()` applies. Nothing downstream ever sees a row.
  (This paragraph used to claim we read a `/health`-style summary and not the
  queue. That was true of the design and false of the code — exactly the kind of
  stale comment a maintainer trusts before deleting the whitelist as redundant.)
- Everything sent to the AI is passed through is_safe_for_ai() first, and
  reasoning_complete() tries the LOCAL engine (Ollama) before any cloud engine.
- No API key in code: ALPHA_BRAIN_SUMMARY_URL + optional ALPHA_BRAIN_TOKEN come
  from the environment / vault; the token is sent as a header and never logged.
- Fetch is tolerant: if Alpha Brain isn't activated yet, we record an
  "awaiting feed" row and the dashboard keeps the last good figures.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from django.utils import timezone

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 12  # seconds


def _brain_url() -> str:
    return (os.environ.get('ALPHA_BRAIN_SUMMARY_URL') or '').strip()


# The ONLY keys we keep. Alpha Brain has no counts-only endpoint — its
# `/api/queue` is the feed that carries the figures, and the SAME response also
# carries `teams`: the exception rows themselves, with client names and policy
# numbers. That is why this feed was never switched on.
#
# Graphite's own BrainProxyController::summary() already solves this by
# whitelisting counts and never passing `teams` on. We do exactly the same, at
# the moment of receipt, so the rows are dropped before anything can store, log
# or analyse them. Add a key here only after checking it cannot carry a person.
#
# `kyc` is NOT here on purpose. The live payload has no such key (verified on
# prod 2026-08-01: the response is exactly generatedAt/source/liveArms/total/
# counts/byDomain), so it was whitelisted on faith — and its rendering path is
# the dangerous one: `_aggregate_text` json.dumps-es whatever `kyc` holds
# straight into the AI prompt. If Alpha Brain later ships `kyc` as a list of the
# 470 KYC exception ROWS — the most plausible upstream change there is — those
# rows would go to the model with nothing but `is_safe_for_ai` in the way.
# Whitelist keys whose shape has been seen. Add `kyc` back when it exists and is
# confirmed counts-only.
_AGGREGATE_KEYS = ('generatedAt', 'source', 'liveArms', 'total',
                   'counts', 'byDomain')


def _aggregate_only(payload: dict) -> dict:
    """Reduce the response to counts. Anything unlisted is discarded."""
    return {k: payload[k] for k in _AGGREGATE_KEYS if k in payload}


def fetch_summary():
    """GET the aggregate from Alpha Brain and strip it to counts.
    Returns (ok, data, note). data = {counts, total, byDomain, kyc, generatedAt}.
    Never raises, and never returns a customer row."""
    url = _brain_url()
    if not url:
        return False, {}, 'ALPHA_BRAIN_SUMMARY_URL not set — Alpha Brain not activated yet.'
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    token = (os.environ.get('ALPHA_BRAIN_TOKEN') or '').strip()
    if token:
        # Alpha Brain authenticates with a standard bearer token. The legacy
        # X-Alpha-Brain-Token header is kept alongside it for any deployment
        # still expecting that form. Neither is ever logged.
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('X-Alpha-Brain-Token', token)
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read().decode('utf-8', 'replace')
        data = json.loads(body)
        if not isinstance(data, dict):
            return False, {}, 'Alpha Brain returned a non-object payload.'
        # Strip FIRST, return second. Nothing downstream ever sees the rows.
        return True, _aggregate_only(data), ''
    except Exception as exc:
        # Logs the exception TYPE only — the body may hold the rows we are
        # taking care not to keep.
        logger.warning('compliance_brain fetch failed: %s', type(exc).__name__)
        return False, {}, f'Could not reach Alpha Brain ({type(exc).__name__}).'


def _aggregate_text(counts: dict, kyc: dict) -> str:
    """A compact, PII-free rendering of the aggregate for the AI to read."""
    lines = ['Exception counts by team:']
    for team, n in (counts or {}).items():
        lines.append(f'  {team}: {n}')
    if kyc:
        lines.append('KYC / policy-control aggregates:')
        for k, v in kyc.items():
            lines.append(f'  {k}: {json.dumps(v, ensure_ascii=False)}')
    return '\n'.join(lines) if len(lines) > 1 else 'No aggregate figures available.'


_SYSTEM = (
    "You are a compliance analyst for an insurer. You are given ONLY aggregate "
    "counts (no customer data). Write 2-4 short plain-English sentences for the "
    "AML officer: what stands out, what needs attention, and the overall trend. "
    "No preamble, no markdown, no customer identifiers."
)


def analyse(counts: dict, kyc: dict):
    """Local-first AI read of the aggregate. Returns (narrative, engine).
    Safe on any failure (returns ('', ''))."""
    from core.ai_assist import is_safe_for_ai, reasoning_complete, DeepSeekUnavailable
    text = _aggregate_text(counts, kyc)
    report = is_safe_for_ai(text)          # aggregate is already PII-free; belt + braces
    try:
        out = reasoning_complete(report.redacted_text, system_prompt=_SYSTEM,
                                 feature='compliance-brain')
        return (out or '').strip(), 'reasoning_complete'
    except DeepSeekUnavailable as exc:
        logger.warning('compliance_brain analyse: all AI engines down (%s)', exc)
        return '', ''
    except Exception as exc:
        logger.warning('compliance_brain analyse failed: %s', exc)
        return '', ''


def run_nightly():
    """Fetch → analyse → store one ComplianceBrainSummary row. Returns the row."""
    from django.utils import timezone
    from core.models import ComplianceBrainSummary
    ok, data, note = fetch_summary()
    counts = data.get('counts', {}) if ok else {}
    kyc = data.get('kyc', {}) if ok else {}
    narrative, engine = analyse(counts, kyc) if ok else ('', '')
    row = ComplianceBrainSummary.objects.create(
        as_of=timezone.localdate(),
        source_url=_brain_url(),
        fetched_ok=ok,
        counts=counts,
        kyc=kyc,
        ai_narrative=narrative,
        ai_engine=engine,
        note=note,
    )
    logger.info('compliance_brain nightly: ok=%s engine=%s total=%s',
                ok, engine, data.get('total') if ok else '-')
    return row


def latest_summary() -> dict:
    """For the dashboard. Latest run for status/narrative; latest good run for the
    numbers (so a transient fetch miss doesn't blank the figures)."""
    from core.models import ComplianceBrainSummary
    latest = ComplianceBrainSummary.objects.first()
    if not latest:
        return {'activated': False, 'note': 'Awaiting Alpha Brain activation (Pramod).',
                'as_of': None, 'ai_narrative': '', 'counts': {}, 'kyc': {}}
    good = latest if latest.fetched_ok else ComplianceBrainSummary.objects.filter(
        fetched_ok=True).first()
    return {
        'activated': bool(good),
        'fetched_ok': latest.fetched_ok,
        'as_of': (good.as_of.isoformat() if good else latest.as_of.isoformat()),
        'updated_at': (good.created_at.isoformat() if good else None),
        'counts': good.counts if good else {},
        'kyc': good.kyc if good else {},
        'ai_narrative': latest.ai_narrative or (good.ai_narrative if good else ''),
        'ai_engine': latest.ai_engine,
        'note': latest.note,
    }
