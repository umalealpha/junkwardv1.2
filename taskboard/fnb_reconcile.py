"""taskboard/fnb_reconcile.py — reconcile open payment requests against the FNB
"pending authorisation" batch list (CFO 2026-08-12).

The CFO downloads FNB's "Batch Payments" report (everything still sitting in the
bank awaiting his authorisation). Anything in Omni's payment-request queue whose
money is NO LONGER in that FNB list has already been authorised/paid in the bank
— so it can be cleared out of the Omni queue. Doing that match by hand is slow
and error-prone, so this module does it.

Design rules (money-safety):
  * It never clears anything. It only CLASSIFIES each open request as
    "already paid" (safe to close) or "still waiting in the bank" (keep). The
    CFO presses the existing, audited Clear button on whatever he chooses.
  * The match is deliberately conservative: a request is only called
    "already paid" when NONE of its lines can be found in the FNB list — by
    exact amount OR by a reference token (G-number / policy number). A single
    line still present in the bank keeps the whole request in the queue. False
    "keep" is harmless (it just stays in the queue); false "clear" is not, so
    the doubt always falls to "keep".
  * It also flags DUPLICATES in the FNB list itself (same amount appearing more
    than once) — that is how a refund gets paid twice.

Pure functions, no Django imports, so they are trivially unit-testable.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Reference tokens we can trust to identify a specific payment across the two
# systems: Graphite claim numbers (G2026…) and policy numbers.
_TOKEN_RE = re.compile(r'\b(?:G20\d{6,}|(?:DOMG|DOMD|MIS|COMG|COMD)\d{6,})\b', re.I)
# A money amount like 1,234.56 or 986.40 (two decimals mandatory so we don't
# grab dates / policy digits).
_AMOUNT_RE = re.compile(r'\b\d{1,3}(?:,\d{3})*\.\d{2}\b')


def _to_amount(raw) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).replace(',', '').strip()
    if not s:
        return None
    try:
        return Decimal(s).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _tokens(text: str) -> set[str]:
    return {m.upper() for m in _TOKEN_RE.findall(text or '')}


def parse_fnb_report(text: str) -> dict:
    """Turn the pasted / uploaded FNB report text into the two things the match
    needs — the set of amounts and the set of reference tokens still pending in
    the bank — plus a best-effort per-line breakdown for display + duplicates.
    """
    text = text or ''
    amounts: list[Decimal] = []
    for m in _AMOUNT_RE.findall(text):
        a = _to_amount(m)
        if a is not None:
            amounts.append(a)
    tokens = _tokens(text)

    # Best-effort rows: one per line that carries an amount, with whatever text
    # sits on that line as the "name". Used only for the duplicate alarm and for
    # showing the CFO what was read — never for the keep/clear decision.
    entries: list[dict] = []
    for line in text.splitlines():
        found = _AMOUNT_RE.findall(line)
        if not found:
            continue
        amt = _to_amount(found[-1])
        if amt is None:
            continue
        name = _AMOUNT_RE.sub('', line)
        name = re.sub(r'\b(Same Day|Authorisation Requested|Authorised|Paid)\b', '', name, flags=re.I)
        name = re.sub(r'\d{4}/\d{2}/\d{2}', '', name)
        name = ' '.join(name.split()).strip(' -\t')
        entries.append({'name': name, 'amount': str(amt)})

    return {
        'amounts': amounts,
        'amount_set': set(amounts),
        'tokens': tokens,
        'entries': entries,
    }


def find_fnb_duplicates(parsed: dict) -> list[dict]:
    """Same amount appearing more than once in the FNB list = a payment loaded
    twice. Return one row per duplicated amount with the names it appears under.
    """
    by_amount: dict[str, list[str]] = {}
    for e in parsed.get('entries', []):
        by_amount.setdefault(e['amount'], []).append(e['name'])
    dupes = []
    for amount, names in by_amount.items():
        if len(names) > 1:
            dupes.append({'amount': amount, 'count': len(names), 'names': names})
    dupes.sort(key=lambda d: Decimal(d['amount']), reverse=True)
    return dupes


def _line_in_fnb(line: dict, parsed: dict) -> tuple[bool, str]:
    """Is this payment-request line still in the FNB pending list? Returns
    (found, why)."""
    amt = _to_amount(line.get('amount'))
    if amt is not None and amt in parsed['amount_set']:
        return True, f'amount {amt}'
    line_tokens = _tokens(f"{line.get('ref','')} {line.get('description','')}")
    hit = line_tokens & parsed['tokens']
    if hit:
        return True, f'ref {sorted(hit)[0]}'
    return False, ''


def pdf_bytes_to_text(b: bytes) -> str:
    """Extract text from a born-digital FNB "Batch Payments" PDF (pdfplumber, the
    same reader core/doc_parse uses). Returns '' if it can't be read, so the
    caller degrades to an empty match rather than 500-ing."""
    try:
        import io
        import pdfplumber
        out = []
        with pdfplumber.open(io.BytesIO(b)) as pdf:
            for page in pdf.pages[:40]:
                out.append(page.extract_text() or '')
        return '\n'.join(out)
    except Exception:  # noqa: BLE001 — a bad upload must not crash the request
        return ''


def deterministic_summary(result: dict, duplicates: list[dict], *, closed: list[dict] | None = None) -> str:
    """A plain-English intelligence summary built WITHOUT any AI — the fallback
    when DeepSeek/Gemini is unavailable, so the feature always returns something
    useful (Omni must keep working if the AI box dies)."""
    paid = result.get('already_paid', [])
    pending = result.get('still_pending', [])
    closed = closed or []

    def _total(rows):
        t = Decimal('0')
        for r in rows:
            try:
                t += Decimal(str(r.get('total') or '0'))
            except (InvalidOperation, ValueError):
                pass
        return t

    lines = []
    if closed:
        lines.append(f"Closed {len(closed)} already-paid request(s) totalling P{_total(closed):,.2f} — "
                     "none of their lines are still in the bank list, so they were paid in an earlier run.")
        for r in closed[:10]:
            lines.append(f"  • {r.get('ref')} — {r.get('subject')} — P{Decimal(str(r.get('total') or 0)):,.2f}")
    elif paid:
        lines.append(f"{len(paid)} request(s) look already paid (P{_total(paid):,.2f}) — not in the bank list.")
    else:
        lines.append("Nothing to close — every open request still has money waiting in the bank.")

    lines.append(f"{len(pending)} request(s) are still waiting in the bank (P{_total(pending):,.2f}) — left in the queue.")

    if duplicates:
        lines.append("")
        lines.append("⚠️ Loaded more than once in FNB — check before authorising (a payment about to go out twice):")
        for d in duplicates[:10]:
            names = ', '.join(n for n in d.get('names', []) if n)
            lines.append(f"  • P{Decimal(str(d['amount'])):,.2f} × {d['count']} ({names})")
    return '\n'.join(lines)


def reconcile(parsed: dict, requests: list[dict]) -> dict:
    """Classify each open request against the parsed FNB list.

    `requests` is a list of dicts: {ref, subject, total, category, lines:[{amount, ref, description}]}.
    Returns {already_paid: [...], still_pending: [...], no_lines: [...]}.
    """
    already_paid, still_pending, no_lines = [], [], []
    for r in requests:
        lines = r.get('lines') or []
        checked = []
        any_in_fnb = False
        for ln in lines:
            found, why = _line_in_fnb(ln, parsed)
            any_in_fnb = any_in_fnb or found
            checked.append({
                'amount': str(_to_amount(ln.get('amount')) or ln.get('amount') or ''),
                'ref': ln.get('ref', '') or ln.get('description', ''),
                'in_fnb': found,
                'why': why,
            })
        row = {
            'ref': r.get('ref'), 'subject': r.get('subject'),
            'total': str(r.get('total')), 'category': r.get('category'),
            'lines': checked,
        }
        if not lines:
            no_lines.append(row)          # can't judge with no line detail — keep for review
        elif any_in_fnb:
            still_pending.append(row)
        else:
            already_paid.append(row)
    return {
        'already_paid': already_paid,
        'still_pending': still_pending,
        'no_lines': no_lines,
    }
