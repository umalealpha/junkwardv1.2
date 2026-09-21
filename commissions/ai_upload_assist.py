"""commissions/ai_upload_assist.py — Aria's plain-English help when a commission
upload can't be read (bug f3a02295, CFO 2026-08-22: "wire deepseek in so this
feature becomes smarter so we don't make these mistakes").

Scope, on purpose:
  * DIAGNOSIS ONLY. Aria reads the top rows of a sheet the deterministic importer
    could NOT parse, and returns ONE short, plain-English sentence telling the
    uploader what's wrong and how to fix it (rename a column, pick the right
    group, this looks like a summary not a detail sheet, ...).
  * It NEVER decides which column is the commission amount — no AI-chosen number
    ever reaches the ledger. The real import stays 100% deterministic
    (importer.py). This keeps financial integrity while making the error helpful.
  * Best-effort. Any failure (AI down, disabled, timeout, odd file) returns '' and
    the caller keeps its own deterministic message. Omni never depends on the AI.

Reuses the house cascade `core.ai_assist.reasoning_complete` (local Ollama →
DeepSeek → Gemini) which is already PII-firewalled; we additionally send only the
first few rows and redact them first, so a client book never leaves the box.
"""
from __future__ import annotations

import logging

log = logging.getLogger('commissions')

# Our target fields, so Aria knows what a valid detail sheet needs.
_FIELDS = ('Policy Number', 'Client/Policy Name', 'New Business/Renewal/'
           'Endorsement/Previous Month', 'Amount Collected', 'Annualised Premium',
           'Commission Rate', 'Collection Date', 'Commissions')


def _top_rows(path, sheets=4, rows=8, cells=25):
    """The first few rows of the first few sheets, as short strings — enough to
    show the header layout, small enough to stay cheap and low-PII."""
    from .importer import _iter_sheets
    out = []
    for name, sheet_rows, _xlsb in _iter_sheets(path):
        preview = []
        for r in sheet_rows[:rows]:
            preview.append([('' if c is None else str(c))[:40] for c in list(r)[:cells]])
        out.append((str(name)[:40], preview))
        if len(out) >= sheets:
            break
    return out


def explain_upload_problem(path, *, inhouse: bool) -> str:
    """One plain-English sentence on why the sheet wouldn't import, or '' if the
    AI is unavailable / declines. Never raises."""
    try:
        from core.ai_assist import reasoning_complete, is_safe_for_ai

        sheets = _top_rows(path)
        if not sheets:
            return ''
        # compact, redacted view of just the header area
        lines = []
        for name, rows in sheets:
            lines.append(f'Sheet "{name}":')
            for r in rows:
                lines.append('  | ' + ' | '.join(r))
        raw = '\n'.join(lines)
        report = is_safe_for_ai(raw)
        if not report.safe:
            return ''
        # ONLY ever send the redacted text — never the raw cells. A commission
        # sheet can carry client names / policy numbers, so if the firewall
        # produced nothing usable we skip the AI entirely rather than fall back
        # to raw (C5, panel 2026-08-22). reasoning_complete is itself
        # PII-firewalled — this is the belt-and-braces layer.
        sample = (report.redacted_text or '').strip()
        if not sample:
            return ''

        mode = ('an IN-HOUSE agent SUMMARY (one row per agent: agent name + a '
                'single gross commission figure)') if inhouse else (
                'a PER-POLICY detail sheet (one row per policy)')
        prompt = (
            "You are Aria, helping an insurance staff member who just tried to "
            "upload a commission spreadsheet into Omni, and it could not be read.\n"
            f"Omni expected {mode}. A valid PER-POLICY sheet needs a header row "
            f"containing at least a 'Policy Number' column AND a 'Commissions' "
            f"column (other useful columns: {', '.join(_FIELDS)}). A valid "
            "IN-HOUSE summary needs an 'Agent Name' column and a 'Gross "
            "Commission' column.\n"
            "Here are the first rows of their file (values may be redacted for "
            "privacy):\n"
            f"{sample}\n\n"
            "In ONE short sentence, plain English, no jargon, tell them the most "
            "likely reason it didn't load and the single fix (e.g. rename a "
            "column, this looks like a summary so switch the group, or a header "
            "row is missing). Do not guess amounts. Start with 'Tip:'."
        )
        hint = reasoning_complete(
            prompt,
            system_prompt="You give one short, friendly, practical fix. No preamble.",
            max_tokens=90,
            timeout=15.0,
        )
        hint = (hint or '').strip().replace('\n', ' ')
        # keep it to a single tidy sentence
        return hint[:300]
    except Exception:
        log.info('commission upload AI assist unavailable; no hint added', exc_info=True)
        return ''
