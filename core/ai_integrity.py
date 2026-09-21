"""
core/ai_integrity.py — DeepSeek-backed integrity checks on the ledger.

CFO directive 2026-05-18: brainstormed 8 integrity tests, shipping 3.

Each scanner is independent so they can run in parallel later; for now
the public entry point `run_integrity_scan(...)` executes them in series
and returns a flat list of findings. Categories:

  NARRATIVE_MISMATCH    — JE narrative doesn't reasonably match the
                          accounts the lines hit.
  VENDOR_GL_MISMATCH    — Posted vendor bill hits an expense GL that is
                          implausible for the supplier type.
  AMOUNT_ANOMALY        — Cluster of round / duplicated / oddly-paired
                          amounts in the window.

Every scanner:
  * Pulls JE summary rows from the DB (no PII — only account NAMES, not
    customer / employee names).
  * Sends a compact JSON list to DeepSeek with a structured prompt.
  * Parses the JSON response back into Finding rows.
  * Falls back gracefully when DeepSeek is unavailable (returns []).

The scanner does NOT mutate the ledger. Findings surface in
/reports/ai-integrity for the CFO + close team to action manually.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Sum, Count, Q

from core.ai_assist import DeepSeekUnavailable, deepseek_complete
from ledger.models import Account, JournalEntry, JournalEntryLine


log = logging.getLogger(__name__)


@dataclass
class Finding:
    je_id:       str
    entry_date:  str
    severity:    str    # 'high' | 'medium' | 'low'
    category:    str    # NARRATIVE_MISMATCH | VENDOR_GL_MISMATCH | AMOUNT_ANOMALY
    message:     str
    accounts:    list[str]
    amount_bwp:  float


# ─── Common DB pull helper ────────────────────────────────────────────

def _je_summaries(from_date: date, to_date: date,
                  company_id: str | None = None,
                  limit: int = 200) -> list[dict[str, Any]]:
    """Compact one-row-per-JE list — feeds every scanner.

    Returns at most `limit` rows ordered newest-first. No PII — only
    GL account names + first 200 chars of narrative.
    """
    qs = (JournalEntry.objects
          .filter(entry_date__gte=from_date, entry_date__lte=to_date)
          .order_by('-entry_date')[:limit])
    if company_id:
        qs = qs.filter(company_id=company_id)

    out: list[dict[str, Any]] = []
    for je in qs.select_related('company').iterator():
        lines = (JournalEntryLine.objects
                 .filter(journal_entry=je)
                 .select_related('account')
                 .values('account__code', 'account__name', 'debit_bwp', 'credit_bwp'))
        total_dr = sum(Decimal(ln['debit_bwp'] or 0)  for ln in lines)
        accts = sorted({(ln['account__code'] or '') + ' ' + (ln['account__name'] or '')
                        for ln in lines})
        out.append({
            'je_id':       str(je.pk),
            'entry_date':  je.entry_date.isoformat() if je.entry_date else '',
            'narrative':   (je.description or '')[:200],
            'source_type': je.source_type or '',
            'company':     getattr(je.company, 'code', '') if je.company_id else '',
            'amount_bwp':  float(total_dr),
            'accounts':    accts,
        })
    return out


# ─── Scanner 1 — narrative ↔ accounts coherence ──────────────────────

NARRATIVE_SYSTEM = (
    'You are a senior insurance ledger auditor. For each journal entry, '
    'decide if the narrative (description) is coherent with the GL accounts '
    'the lines hit. Reject only CLEAR mismatches (e.g. narrative says '
    '"salary payment" but accounts are reinsurance contras). Ignore minor '
    'wording differences. Return ONLY JSON: '
    '{"findings":[{"je_id":"...","severity":"high|medium|low","message":"..."}]}'
)

def scan_narrative_coherence(rows: list[dict]) -> list[Finding]:
    if not rows:
        return []
    payload = json.dumps([{
        'je_id':     r['je_id'],
        'narrative': r['narrative'],
        'accounts':  r['accounts'][:6],     # cap to keep prompt small
    } for r in rows if r['narrative']], default=str)

    try:
        raw = deepseek_complete(
            payload, system_prompt=NARRATIVE_SYSTEM,
            response_format='json_object', timeout=20.0,
        )
    except DeepSeekUnavailable as exc:
        log.warning('Narrative scan unavailable: %s', exc)
        return []

    try:
        parsed = json.loads(raw)
        items  = parsed.get('findings') or []
    except (json.JSONDecodeError, AttributeError):
        return []

    by_id = {r['je_id']: r for r in rows}
    findings: list[Finding] = []
    for it in items:
        jid = str(it.get('je_id') or '')
        src = by_id.get(jid)
        if not src or not it.get('message'):
            continue
        findings.append(Finding(
            je_id=      jid,
            entry_date= src['entry_date'],
            severity=   it.get('severity') or 'medium',
            category=   'NARRATIVE_MISMATCH',
            message=    str(it['message'])[:400],
            accounts=   src['accounts'],
            amount_bwp= src['amount_bwp'],
        ))
    return findings


# ─── Scanner 2 — vendor → expense GL plausibility ────────────────────

VENDOR_SYSTEM = (
    'You audit accounts payable. Each entry has a supplier name '
    '(extracted from the narrative) and the GL the expense was booked '
    'to. Flag mismatches where the GL is implausible for that vendor '
    '(e.g. Eskom posted to "Telecommunications" or Microsoft to '
    '"Reinsurance Recoveries"). Return ONLY JSON: '
    '{"findings":[{"je_id":"...","severity":"high|medium|low","message":"..."}]}'
)

# Cheap rule to pull a probable supplier name from narrative. Used to
# pre-filter rows before sending to DeepSeek so we don't waste tokens
# on entries with no vendor reference.
KNOWN_VENDOR_TOKENS = (
    'TELKOM', 'MTN', 'CELL C', 'VODACOM', 'ESKOM', 'CITY OF',
    'UBER', 'BOLT', 'PWC', 'DELOITTE', 'KPMG', 'EY',
    'AON', 'MARSH', 'AMAZON', 'GOOGLE', 'MICROSOFT', 'AWS',
    'CLAUDE', 'OPENAI', 'GENRIC', 'FNB',
)

def _probable_vendor(narrative: str) -> str:
    up = (narrative or '').upper()
    for tok in KNOWN_VENDOR_TOKENS:
        if tok in up:
            return tok
    return ''

def scan_vendor_gl(rows: list[dict]) -> list[Finding]:
    enriched = []
    for r in rows:
        vendor = _probable_vendor(r['narrative'])
        if not vendor:
            continue
        enriched.append({
            'je_id':   r['je_id'],
            'vendor':  vendor,
            'gl':      r['accounts'][:4],
        })
    if not enriched:
        return []
    try:
        raw = deepseek_complete(
            json.dumps(enriched, default=str),
            system_prompt=VENDOR_SYSTEM,
            response_format='json_object', timeout=20.0,
        )
    except DeepSeekUnavailable as exc:
        log.warning('Vendor-GL scan unavailable: %s', exc)
        return []
    try:
        items = (json.loads(raw) or {}).get('findings') or []
    except (json.JSONDecodeError, AttributeError):
        return []

    by_id = {r['je_id']: r for r in rows}
    findings: list[Finding] = []
    for it in items:
        jid = str(it.get('je_id') or '')
        src = by_id.get(jid)
        if not src or not it.get('message'):
            continue
        findings.append(Finding(
            je_id=      jid,
            entry_date= src['entry_date'],
            severity=   it.get('severity') or 'medium',
            category=   'VENDOR_GL_MISMATCH',
            message=    str(it['message'])[:400],
            accounts=   src['accounts'],
            amount_bwp= src['amount_bwp'],
        ))
    return findings


# ─── Scanner 3 — amount anomaly pattern ──────────────────────────────

ANOMALY_SYSTEM = (
    'You are looking for SUSPICIOUS PATTERNS in a list of journal entry '
    'amounts and dates. Flag clusters of identical round amounts on the '
    'same day, duplicated amounts re-used across the window, and '
    'amounts that look like manual splits (e.g. 100,000 = 50,000 + '
    '50,000 on the same day). Return ONLY JSON: '
    '{"findings":[{"je_id":"...","severity":"high|medium|low","message":"..."}]}'
)

def _amount_signals(rows: list[dict]) -> list[dict]:
    """Pre-filter — only send rows that look interesting."""
    counts = Counter(round(r['amount_bwp'] or 0, 2) for r in rows)
    suspicious_amounts = {amt for amt, n in counts.items() if amt > 0 and n >= 3}
    out = []
    for r in rows:
        amt = round(r['amount_bwp'] or 0, 2)
        # Round-number heuristic: ends in 000 or 500
        round_hit = amt > 0 and (amt % 1000 == 0 or amt % 500 == 0)
        dup_hit   = amt in suspicious_amounts
        if not (round_hit or dup_hit):
            continue
        out.append({
            'je_id':      r['je_id'],
            'date':       r['entry_date'],
            'amount':     amt,
            'narrative':  r['narrative'][:80],
            'round_hit':  round_hit,
            'dup_hit':    dup_hit,
        })
    return out[:80]   # cap on token cost

def scan_amount_anomaly(rows: list[dict]) -> list[Finding]:
    signals = _amount_signals(rows)
    if not signals:
        return []
    try:
        raw = deepseek_complete(
            json.dumps(signals, default=str),
            system_prompt=ANOMALY_SYSTEM,
            response_format='json_object', timeout=20.0,
        )
    except DeepSeekUnavailable as exc:
        log.warning('Amount anomaly scan unavailable: %s', exc)
        return []
    try:
        items = (json.loads(raw) or {}).get('findings') or []
    except (json.JSONDecodeError, AttributeError):
        return []

    by_id = {r['je_id']: r for r in rows}
    findings: list[Finding] = []
    for it in items:
        jid = str(it.get('je_id') or '')
        src = by_id.get(jid)
        if not src or not it.get('message'):
            continue
        findings.append(Finding(
            je_id=      jid,
            entry_date= src['entry_date'],
            severity=   it.get('severity') or 'low',
            category=   'AMOUNT_ANOMALY',
            message=    str(it['message'])[:400],
            accounts=   src['accounts'],
            amount_bwp= src['amount_bwp'],
        ))
    return findings


# ─── Public entry point ──────────────────────────────────────────────

def run_integrity_scan(
    *,
    from_date: date,
    to_date:   date,
    company_id: str | None = None,
    scanners:   tuple[str, ...] = ('narrative', 'vendor', 'amount'),
) -> dict[str, Any]:
    """Run the requested scanners over [from_date, to_date] and return findings."""
    rows = _je_summaries(from_date, to_date, company_id=company_id, limit=200)
    all_findings: list[Finding] = []
    if 'narrative' in scanners:
        all_findings += scan_narrative_coherence(rows)
    if 'vendor' in scanners:
        all_findings += scan_vendor_gl(rows)
    if 'amount' in scanners:
        all_findings += scan_amount_anomaly(rows)

    severity_rank = {'high': 0, 'medium': 1, 'low': 2}
    all_findings.sort(key=lambda f: (severity_rank.get(f.severity, 9), f.entry_date))
    return {
        'from_date': from_date.isoformat(),
        'to_date':   to_date.isoformat(),
        'company':   company_id or 'ALL',
        'sample_size': len(rows),
        'findings':  [asdict(f) for f in all_findings],
        'counts':    {
            'NARRATIVE_MISMATCH': sum(1 for f in all_findings if f.category == 'NARRATIVE_MISMATCH'),
            'VENDOR_GL_MISMATCH': sum(1 for f in all_findings if f.category == 'VENDOR_GL_MISMATCH'),
            'AMOUNT_ANOMALY':     sum(1 for f in all_findings if f.category == 'AMOUNT_ANOMALY'),
            'TOTAL':              len(all_findings),
        },
    }
