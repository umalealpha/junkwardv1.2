"""
bonu/ai_review.py — DeepSeek over the residue the rules cannot describe.

CFO 2026-08-03: *"wire deepseek to make it smarter."*

The rules in `forensics.py` catch what can be written down: duplicates, tariff breaches,
arithmetic, impossible days. What they cannot catch is *shape* — a firm whose narrative
says "perusal and consideration" on eleven matters in one week, work that escalates just
under a threshold, or a pattern of charges that reads like padding to an experienced eye.
That is what this pass is for. It runs AFTER the rules, over what survived.

TWO NON-NEGOTIABLES:

**1. No member data leaves.** A legal matter is inherently sensitive — a divorce, a
criminal charge, a debt. Every payload is built field by field from figures and codes, is
put through `is_safe_for_ai()`, and firm names are replaced with `Firm A/B/C…` so an
external model never receives who is suing whom (AD-POL-AI-GOV-001, not waivable).
`matter_description` is the one free-text field and it is truncated and redacted.

**2. The AI never decides anything.** It returns questions and a suspicion score. Money
is only ever recovered by a human asking the firm. Nothing here writes to the ledger,
approves an invoice, or contacts anybody.

Engine order comes from `reasoning_complete`: local Ollama first (free), then DeepSeek,
then the cloud tiers — so most of this costs nothing.
"""
from __future__ import annotations

import json
from decimal import Decimal

MAX_LINES_PER_CALL = 120
MAX_DESC = 90

SYSTEM = """You are a forensic auditor reviewing legal fees billed to an insurance
scheme in Botswana. The scheme pays panel law firms for members' legal matters.
Deterministic checks have ALREADY run (duplicates, tariff breaches, arithmetic,
impossible hours, weekend dates). Do NOT repeat them.

Look for what a rule cannot express:
- narrative padding: vague or repeated descriptions that could cover anything
- fee-splitting: one piece of work broken into several charges
- creeping scope: matters that grow month after month with no resolution
- charges that cluster just below an approval threshold
- work that looks inconsistent with the service code or the matter type
- firms whose mix shifts toward higher-margin work over time

Return STRICT JSON only:
{"findings":[{"severity":"high|medium|low","title":"...","detail":"...",
"amount_at_risk":0,"question_for_firm":"...","firm":"Firm A","line_ids":[1,2]}],
"firm_notes":[{"firm":"Firm A","pattern":"...","what_to_ask":"..."}],
"overall":"one paragraph for the CFO"}

Rules: never name a person. Quantify money at risk when you can, else 0. If nothing
stands out, return empty lists and say so in "overall" — a clean bill is a valid answer
and inventing findings is worse than none."""


def _anon_firms(lines):
    """Firm names → Firm A, Firm B… so no external model learns who bills what."""
    mapping, rev, nxt = {}, {}, 0
    for l in lines:
        fid = l.invoice.firm_id
        if fid not in mapping:
            label = f'Firm {chr(65 + nxt)}' if nxt < 26 else f'Firm {nxt + 1}'
            mapping[fid] = label
            rev[label] = l.invoice.firm.name
            nxt += 1
    return mapping, rev


def build_payload(lines, findings_already=None):
    """The PII-safe payload. Figures, codes and dates only."""
    from core.ai_assist import is_safe_for_ai
    lines = list(lines)[:MAX_LINES_PER_CALL]
    firm_map, firm_rev = _anon_firms(lines)
    rows = []
    for l in lines:
        desc = (l.matter_description or '')[:MAX_DESC]
        rep = is_safe_for_ai(desc)
        rows.append({
            'id': l.pk if isinstance(l.pk, int) else str(l.pk)[:8],
            'firm': firm_map[l.invoice.firm_id],
            'invoice': l.invoice.invoice_number,
            'service_date': l.service_date.isoformat() if l.service_date else None,
            'matter': (l.matter_ref or '')[:24],          # the firm's own file ref, not a person
            'service_code': (l.service_code or '')[:24],
            'matter_type': l.matter_type,      # a category, never a narrative — names nobody
            'basis': l.basis,
            'units': float(l.units) if l.units is not None else None,
            'rate': float(l.rate) if l.rate is not None else None,
            'amount': float(l.amount or 0),
            'narrative': rep.redacted_text,
        })
    return {
        'currency': 'BWP',
        'lines': rows,
        'rules_already_found': [
            {'code': f['code'], 'severity': f['severity'], 'title': f['title']}
            for f in (findings_already or [])[:40]
        ],
    }, firm_rev


def review(lines, findings_already=None, feature='bonu_ai_review'):
    """Run the AI pass. Returns (result_dict, firm_name_map, error_or_None).

    Never raises on a model failure — a dashboard must still render when the AI is down.
    """
    from core.ai_assist import reasoning_complete
    payload, firm_rev = build_payload(lines, findings_already)
    if not payload['lines']:
        return {'findings': [], 'firm_notes': [], 'overall': 'No invoice lines to review yet.'}, {}, None

    prompt = (SYSTEM + '\n\nINVOICE LINES (JSON):\n'
              + json.dumps(payload, separators=(',', ':')))
    try:
        raw = reasoning_complete(prompt, feature=feature)
    except BaseException as exc:      # noqa: BLE001 — every engine down must not 500 the page
        return ({'findings': [], 'firm_notes': [],
                 'overall': 'AI review unavailable — the deterministic checks above still stand.'},
                firm_rev, f'{type(exc).__name__}: {exc}'[:180])

    text = (raw or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        text = text.split('\n', 1)[1] if '\n' in text else text
        text = text.rsplit('```', 1)[0]
    try:
        data = json.loads(text[text.find('{'):text.rfind('}') + 1])
    except BaseException:             # noqa: BLE001
        return ({'findings': [], 'firm_notes': [],
                 'overall': (text[:600] or 'AI returned nothing usable.'),
                 'unparsed': True}, firm_rev, 'AI response was not valid JSON')

    data.setdefault('findings', [])
    data.setdefault('firm_notes', [])
    data.setdefault('overall', '')
    # Put the real firm names back for the CFO's screen — they never left the building.
    for f in data['findings']:
        if f.get('firm') in firm_rev:
            f['firm_real'] = firm_rev[f['firm']]
    for n in data['firm_notes']:
        if n.get('firm') in firm_rev:
            n['firm_real'] = firm_rev[n['firm']]
    return data, firm_rev, None


def persist(data, lines_by_id):
    """Save AI findings as BonuFinding rows (source=ai) so a query survives the screen."""
    from bonu.models import BonuFinding
    made = 0
    for f in (data.get('findings') or []):
        sev = f.get('severity') if f.get('severity') in ('high', 'medium', 'low') else 'medium'
        ids = [i for i in (f.get('line_ids') or []) if i in lines_by_id]
        line = lines_by_id.get(ids[0]) if ids else None
        BonuFinding.objects.create(
            invoice=getattr(line, 'invoice', None), line=line,
            firm=getattr(getattr(line, 'invoice', None), 'firm', None),
            code='AI_' + (f.get('title') or 'REVIEW')[:32].upper().replace(' ', '_'),
            severity=sev, source='ai',
            title=(f.get('title') or 'AI observation')[:200],
            detail=(f.get('detail') or '')[:4000],
            amount_at_risk=Decimal(str(f.get('amount_at_risk') or 0)),
            question_for_firm=(f.get('question_for_firm') or '')[:2000])
        made += 1
    return made
