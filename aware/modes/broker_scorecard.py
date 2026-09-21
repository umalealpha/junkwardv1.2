"""Graphite Aware — MODE: Broker Scorecard (CFO 2026-08-22).

Answers the CFO's "which brokers actually make us money" — WITHOUT pretending to
be an audited actuarial loss ratio. It reuses the reviewed, finance-blessed
queries in broker_analysis.broker_report (money is reliable there) and adds:

  1. Broker de-duplication — the split-agency problem (one broker showing as two
     rows) is MERGED here (broker_analysis only warns), so each broker is counted
     once. Names are matched on a normalised key (case/spacing/suffix stripped).
  2. A MONEY view, NOT a loss ratio. Per broker: premium in (annualised in-force),
     claims PAID in the last 12 months, open reserve, and NET = premium − paid
     (negative = the broker's claims paid out exceed the premium it brings in).
     A per-broker loss ratio is deliberately NOT shown: the real data proves it
     misleading (a broker can hold a small current book yet carry large claims
     from business it no longer writes, so a ratio reads as hundreds of percent).
     A true ratio needs a separate actuarial build (earned premium matched to
     claims by underwriting year).
  3. Top-20 open claims by outstanding reserve — the biggest unsettled exposure
     "coming for payment", so Finance can pre-fund. Reuses claims_registry (safe
     fields only, no personal data).
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.broker_names import merge_key as _norm

from .broker_analysis import broker_report
from .claims_registry import claims_registry_report

_TOP_CLAIMS = 20




def broker_scorecard_report(user=None) -> Dict[str, Any]:
    base = broker_report(user)
    brokers: List[Dict[str, Any]] = base.get('brokers', [])  # external only

    # ── de-duplicate split agencies ───────────────────────────────────────────
    merged: Dict[str, Dict[str, Any]] = {}
    merged_names: List[str] = []
    for b in brokers:
        k = _norm(b['broker'])
        if k not in merged:
            merged[k] = {**b}
        else:
            m = merged[k]
            if b['broker'] != m['broker'] and b['broker'] not in merged_names:
                merged_names.append(b['broker'])
                if m['broker'] not in merged_names:
                    merged_names.append(m['broker'])
            # keep the longer display name, sum the money/counts
            if len(b['broker']) > len(m['broker']):
                m['broker'] = b['broker']
            for f in ('active_pol', 'inforce_gwp', 'nb_cnt', 'nb_gwp',
                      'net_paid_12m', 'outstanding'):
                m[f] = (m.get(f) or 0) + (b.get(f) or 0)
    rows = list(merged.values())

    # ── money view: Net = premium in − claims paid (recomputed post-merge) ────
    # No loss-ratio: the real data proved it misleading — a broker can hold a
    # small CURRENT book yet carry large claims from a book it no longer writes
    # (e.g. one broker: ~P0.4m premium vs ~P4m paid → a "ratio" reads 1000%+,
    # which is not a real loss ratio). Money (premium vs paid) is honest and
    # answers "who makes us money" directly. Outstanding reserve is shown as a
    # separate future-exposure figure, NOT folded into Net (Net is realised cash).
    book = sum(r['inforce_gwp'] for r in rows) or 0.0
    loss_makers = 0
    for r in rows:
        prem = r.get('inforce_gwp') or 0
        paid = r.get('net_paid_12m') or 0
        r['pct'] = round(prem / book * 100, 1) if book else 0.0
        r['net'] = round(prem - paid)   # 12-month realised money contribution
        if r['net'] < 0:
            loss_makers += 1

    # most negative Net first — the brokers costing us money surface at the top
    rows.sort(key=lambda x: (x['net'], -x['inforce_gwp']))

    # ── top-20 open claims coming for payment (reuse claims registry) ─────────
    # Pin the ordering here (largest outstanding first) rather than relying on
    # the registry's own sort, and keep only the fields Finance needs to prepare
    # a payment — claim number, status, broker, outstanding. Policy number is
    # dropped from this box to keep the PII surface minimal.
    reg = claims_registry_report(user)
    top_claims = [
        {'claim': c.get('claim'), 'status': c.get('status'),
         'broker': c.get('broker'), 'outstanding': c.get('outstanding')}
        for c in sorted((reg.get('rows') or []),
                        key=lambda c: -(c.get('outstanding') or 0))[:_TOP_CLAIMS]
    ]

    notes = [
        "Net = premium in (annualised in-force) − claims PAID in the last 12 "
        "months. Positive means the broker's book contributes money; negative "
        "(red) means claims paid out exceed the premium it currently brings in.",
        "No loss-ratio % is shown on purpose. A per-broker ratio is unreliable "
        "here — a broker can hold a small current book yet carry large claims "
        "from business it no longer writes, so a ratio reads as hundreds of "
        "percent and misleads. A true ratio needs a separate actuarial build "
        "(earned premium matched to claims by underwriting year).",
        "Outstanding reserve is the money still to be paid on that broker's open "
        "claims — shown separately as future exposure, not inside Net.",
        "Brokers whose record was split across two agency rows are MERGED here so "
        "each broker is counted once. Money figures come from the reviewed Broker "
        "Analysis queries (read-only from Graphite).",
        "The Top-20 open claims box lists the biggest unsettled exposure by "
        "outstanding reserve so Finance can be ready to fund them. Safe fields "
        "only — no claimant names, ID numbers or injury detail.",
    ]
    if merged_names:
        notes.append("Merged duplicate agency record(s): "
                     + ", ".join(sorted(set(merged_names))) + ".")

    from django.utils import timezone
    return {
        'generated_at': timezone.now().strftime('%d %b %Y, %H:%M'),
        'totals': base.get('totals', {}),
        'channel': base.get('channel', {}),
        'loss_makers': loss_makers,
        'brokers': rows,
        'open_claims_count': reg.get('open_count', 0),
        'open_claims_total_outstanding': reg.get('total_outstanding', 0),
        'top_claims': top_claims,
        'notes': notes,
    }
