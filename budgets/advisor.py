"""
budgets/advisor.py — FY27 budget advisor.

Wires DeepSeek (CFO directive 2026-06-28) as an *advisory* layer over the budget
simulator. Hard rules:
  - The LLM never computes the P&L — the frontend engine (budgetModel.ts) does.
    DeepSeek only reasons about the numbers it is handed and recommends lever moves.
  - It never writes to the database. Output is text only.
  - The API key is read from the environment (DEEPSEEK_API_KEY); it is never logged,
    returned to the client, or accepted from the request.
  - If the key/endpoint is unavailable, a deterministic house-rules fallback runs so
    the feature degrades gracefully instead of erroring.

DeepSeek exposes an OpenAI-compatible Chat Completions API.
"""
from __future__ import annotations

# Uses the shared DeepSeek client (core.ai_assist.deepseek_complete), which resolves
# DEEPSEEK_API_KEY from the CFO Secrets Vault first, then env — so a vault-only key
# still works. DeepSeek only (no Anthropic fallback) per the standing rule.

# FY27 context the advisor must reason against (from Kago's model + the Decision Summary).
_SYSTEM = (
    "You are the FY2026/27 budget advisor for Alpha Direct Insurance (Botswana, currency BWP, "
    "figures in P Millions). You advise the CFO on a short-term insurance budget. You do NOT do "
    "arithmetic — the figures you are given were already computed by a deterministic engine; treat "
    "them as ground truth and reason about them.\n"
    "Anchor facts:\n"
    "- Base case: GWP P151.6m, EBITDA -P1.5m, PAT -P6.0m, EBITDA margin on NEP ~-2.4%. The base "
    "case is a LOSS: the new Group management fee (7.5% of GWP = P11.4m, added to opex) exceeds the "
    "P3.04m EXCO-secondment salary saving. Opex also carries a ring-fenced P0.5m AI & software "
    "subscriptions provision (CFO 2026-07-13). The management-fee basis (gross GWP vs net earned "
    "premium) is still to be confirmed by the CFO — flag it if asked.\n"
    "- Committed fallback (cost-cut): EBITDA P4.7m, PAT P0.2m (P6.21m discretionary cuts; the AI "
    "provision is retained — AI tooling is operational, not discretionary).\n"
    "- FY26 forecast: GWP P133.1m, EBITDA P9.1m, PAT P1.7m (after a P3m bad-debts provision).\n"
    "- The EBITDA target is ~P8m; a P160m GWP floor was previously preferred.\n"
    "- Gross loss ratios are set prudently at ~FY26-actual levels (Motor 65%, Property 65%, "
    "Union Legal 76%, Guarantee 80%; blended gross ~54%, net ~46%). Union Legal & Guarantee remain "
    "high-loss lines — flag them.\n"
    "- Reinsurance cession ~63% of GWP (overall, incl. XL/CAT); higher cession lowers retained risk "
    "but RI commission income offsets cost. The risk-software fee is 2.5% of GWP, so opex scales with revenue.\n"
    "When a sensitivity table is provided (PAT/EBITDA at a range of ONE lever, the others held at their "
    "current value), USE IT to answer break-even / maximum / minimum / what-if questions: read the crossing "
    "point straight off the table, or linearly interpolate between the two nearest rows. That is reading "
    "results you were given, not doing the P&L arithmetic yourself. State the crossing precisely (e.g. 'PAT "
    "hits zero at a gross loss ratio of ~61%; above that you make a loss').\n"
    "Style: terse, numerate, insurance-literate, like a CFO's analyst. No markdown headings, no preamble. "
    "If the CFO asked a specific question, answer THAT first in one line, then (if useful) add the biggest "
    "risk or the lever move that changes the answer. Otherwise give: (1) a one-line verdict on whether this "
    "scenario is sound, (2) 2-3 concrete lever moves with rough P Mn impact, (3) the single biggest risk. "
    "Keep under 160 words."
)

_SWEEP_LABELS = {
    'lossRatio': 'Gross loss ratio',
    'cession':   'RI cession (% of GWP)',
    'opex':      'Operating expenses (P Mn)',
    'gwp':       'GWP (P Mn)',
}


def _render_sweeps(sweeps: dict) -> str:
    """Format the frontend-computed sensitivity tables into compact prompt text.
    Each entry: key -> [{x, pat, ebitda}, ...]. x is a fraction for pct levers,
    a P-Mn figure for money levers."""
    if not sweeps:
        return ''
    lines = []
    for key, rows in sweeps.items():
        if not rows:
            continue
        is_pct = key in ('lossRatio', 'cession')
        pts = []
        for r in rows:
            try:
                x = float(r.get('x'))
                xf = f'{x * 100:.0f}%' if is_pct else f'P{x:.0f}m'
                pts.append(f"{xf}: PAT P{float(r.get('pat', 0)):.1f}m, EBITDA P{float(r.get('ebitda', 0)):.1f}m")
            except (TypeError, ValueError):
                continue
        if pts:
            lines.append(f"{_SWEEP_LABELS.get(key, key)} (others held at current) — " + '; '.join(pts))
    if not lines:
        return ''
    return 'Sensitivity tables (deterministic engine output — read/interpolate these):\n' + '\n'.join(lines) + '\n'


def _fallback(scenario: dict) -> dict:
    """Deterministic house-rules advice when DeepSeek is unavailable."""
    lev = scenario.get('levers', {})
    out = scenario.get('outputs', {})
    tips = []
    lr = float(lev.get('lossRatio', 0))
    pat = float(out.get('pat', 0))
    ebitda = float(out.get('ebitda', 0))
    cession = float(lev.get('cession', 0))
    margin = float(out.get('nepMargin', 0))
    if lr < 0.52:
        tips.append(f"Gross loss ratio {lr*100:.0f}% is below the ~54% base — "
                    "only credible with a signed repricing/underwriting plan.")
    if pat < 0.2:
        tips.append(f"PAT P{pat:.1f}m is below the cost-cut fallback (P0.2m) — pull opex toward the "
                    "cost-cut P40.2m or lift GWP toward the P160m floor. The base case is itself a "
                    "loss (P-6.0m), driven by the P11.4m Group management fee.")
    elif pat < 1.0:
        tips.append(f"PAT P{pat:.1f}m is around the cost-cut fallback (P0.2m) but below the P1m aim.")
    if ebitda < 8.0:
        tips.append(f"EBITDA P{ebitda:.1f}m is under the ~P8m target.")
    if cession > 0.80:
        tips.append(f"Cession {cession*100:.0f}% is high — you keep little risk but lean on RI commission; "
                    "confirm the treaty supports it.")
    if not tips:
        tips.append("Scenario is within the modelled envelope; keep the Health BU revenue flagged as a "
                    "first-year ramp.")
    verdict = "Sound" if (pat >= 0.2 and ebitda >= 0.0) else "Loss / stretch — needs a lever move"
    return {
        'ok': True, 'source': 'Aria advisor (offline)',
        'text': f"Verdict: {verdict}.\n- " + "\n- ".join(tips) +
                "\n\n(Aria advisor offline — deterministic house-rules used.)",
    }


def ask(scenario: dict) -> dict:
    """scenario = {levers:{gwp,lossRatio,cession,opex}, outputs:{...}, question?:str, sweeps?:{}}.
    Returns {ok, source, text}. DeepSeek only; house-rules fallback on failure."""
    lev, out = scenario.get('levers', {}), scenario.get('outputs', {})
    q = (scenario.get('question') or '').strip()
    user = (
        "Scenario levers — "
        f"GWP P{float(lev.get('gwp',0)):.1f}m; gross loss ratio {float(lev.get('lossRatio',0))*100:.1f}%; "
        f"RI cession {float(lev.get('cession',0))*100:.1f}% of GWP; operating expenses P{float(lev.get('opex',0)):.1f}m.\n"
        "Engine outputs — "
        f"NEP P{float(out.get('nep',0)):.1f}m; net claims P{float(out.get('netClaims',0)):.1f}m; "
        f"RI commission P{float(out.get('commission',0)):.1f}m; gross profit P{float(out.get('grossProfit',0)):.1f}m; "
        f"EBITDA P{float(out.get('ebitda',0)):.2f}m; PAT P{float(out.get('pat',0)):.2f}m; "
        f"EBITDA margin {float(out.get('nepMargin',0))*100:.1f}%.\n"
        + _render_sweeps(scenario.get('sweeps') or {})
        + (f"CFO question: {q}\n" if q else "")
        + ("Answer the CFO question above using the sensitivity tables where relevant."
           if q else "Assess this scenario and recommend lever moves.")
    )
    # DeepSeek via the shared client (vault → env key resolution). No Anthropic.
    from core.ai_assist import deepseek_complete, DeepSeekUnavailable
    try:
        text = (deepseek_complete(user, system_prompt=_SYSTEM) or '').strip()
        if not text:
            return _fallback(scenario)
        return {'ok': True, 'source': 'Aria advisor', 'text': text}   # brand shown to users (CFO 2026-06-29)
    except DeepSeekUnavailable:
        return _fallback(scenario)
    except Exception:  # noqa: BLE001 — never let the advisor 500 the page
        return _fallback(scenario)
