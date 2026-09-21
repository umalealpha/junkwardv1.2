"""
nbfira/a1_math.py — Statement A.1 Prescribed Capital Target, the way the
NBFIRA workbook actually computes it.

CFO decision 2026-08-18: adopt the filed workbook's method. Omni previously
computed `max(MCR, IRC×g + MER×g + MRC×g)` — a flat factor times premium, a
linear sum, and the g-factors as MULTIPLIERS held as stored constants. Every
one of those three is wrong. Verified cell by cell against all four FY2026
filed returns.

The real chain (all figures P'000):

  1. Class charge      irc[c] = 25% × ANWP[c] × (1 + factor[c])
  2. Bucket charge     mrc[b] = allocation_to_MRCTR[b] × factor[b]
  3. Combine           agg    = ((ΣIRC + MER)² + ΣMRC²)^0.5     <- root of squares
  4. Apportion         each *_adj = agg × own_total / (ΣIRC + MER + ΣMRC)
  5. Allocate assets   walk the buckets in order, filling the adjusted
                       IRC+MER requirement first, then the adjusted MRCTR
  6. Derive g          g_ins = 1 − 0.5 × (Σ factor×irc_alloc) / Σ irc_alloc
                       g_mkt = 1 −       (Σ factor×mrc_alloc) / Σ mrc_alloc
  7. Target            PCT = MAX(MCR, (((IRC_adj+MER_adj)/g_ins)² + (MRC_adj/g_mkt)²)^0.5)

**g is DERIVED, never stored.** It moves with the asset mix: the filed returns
show 0.8250/0.6500 for Q1, Q2 and Q4 but 0.8241/0.6635 for Q3. A stored
constant cannot be right.

**The ANWP input is FORWARD-LOOKING** — "assumed annual net written premium for
the next 12 months". It is emphatically NOT historical GWP by class, which is
what Omni's old `per_class_gwp()` stub was reaching for. It has to be an entered
assumption, so `compute_a1` takes it as an argument and never derives it.

One deliberate departure from the sheet. The workbook's PCT cell is
`IF(g_ins>0, MAX(...), 0)`, so with no assets allocated it returns **zero**. In
the workbook that never happens; in Omni, whose risk inputs are still unfilled,
it would happen on every period — and a Prescribed Capital Target displayed as
0.00 reads as "no capital required". The MCR floor is statutory, so this module
floors at MCR instead of ever returning zero, and says so in `notes`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

ZERO = Decimal('0')
QUARTER = Decimal('0.25')


def _d(v) -> Decimal:
    """Anything → Decimal. None/blank → 0. Excel leaves skipped cells empty."""
    if v is None or v == '':
        return ZERO
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _sqrt(v: Decimal) -> Decimal:
    """Decimal square root. `Decimal.sqrt` keeps everything in Decimal context
    rather than losing precision through float."""
    return v.sqrt() if v > 0 else ZERO


def class_charge(anwp, factor) -> Decimal:
    """25% × ANWP × (1 + factor) — the workbook's per-class insurance charge."""
    return QUARTER * _d(anwp) * (Decimal('1') + _d(factor))


def allocate(
    available: Sequence[Decimal],
    need_first: Decimal,
    need_second: Decimal,
) -> tuple[list[Decimal], list[Decimal]]:
    """Walk asset buckets in order, filling `need_first` (adjusted IRC+MER) then
    `need_second` (adjusted MRCTR) out of what each bucket has spare.

    Mirrors the workbook's C/D/E columns:
        C = MIN(available, remaining_first)
        D = available − C
        E = MIN(D, remaining_second)

    Order matters — it is what makes the derived g-factors depend on the asset
    MIX rather than only the totals.
    """
    first, second = [], []
    rem1, rem2 = need_first, need_second
    for avail in available:
        c = avail if avail < rem1 else rem1
        first.append(c)
        rem1 -= c
        d = avail - c
        e = d if d < rem2 else rem2
        second.append(e)
        rem2 -= e
    return first, second


def _weighted_ratio(factors: Sequence[Decimal],
                    allocation: Sequence[Decimal]) -> Decimal:
    """Σ(factor × allocation) / Σ(allocation). Zero when nothing is allocated."""
    total = sum(allocation, ZERO)
    if total == 0:
        return ZERO
    weighted = sum((f * a for f, a in zip(factors, allocation)), ZERO)
    return weighted / total


def compute_a1(
    *,
    mcr: Decimal,
    classes: Sequence[str],
    irc_factors: Mapping[str, Decimal],
    anwp: Mapping[str, Decimal],
    mer_total: Decimal,
    buckets: Sequence[str],
    mrc_factors: Mapping[str, Decimal],
    net_assets: Mapping[str, Decimal],
    alloc_mrctr: Mapping[str, Decimal],
) -> dict:
    """Compute every A.1 figure. Pure — no Django, no database, no I/O.

    `net_assets` and `alloc_mrctr` are per-bucket; a bucket's spare capacity for
    the asset-allocation walk is `net_assets[b] − alloc_mrctr[b]`, exactly as the
    workbook's column B does it.

    Returns per-class / per-bucket charges, the adjusted trio, the DERIVED
    g-factors and the target, plus `notes` listing anything that made the answer
    fall back to a floor.
    """
    notes: list[str] = []
    mcr = _d(mcr)

    irc = {c: class_charge(anwp.get(c), irc_factors.get(c)) for c in classes}
    irc_total = sum(irc.values(), ZERO)
    mer_total = _d(mer_total)

    mrc = {b: _d(alloc_mrctr.get(b)) * _d(mrc_factors.get(b)) for b in buckets}
    mrc_total = sum(mrc.values(), ZERO)

    denom = irc_total + mer_total + mrc_total
    if denom > 0:
        agg = _sqrt((irc_total + mer_total) ** 2 + mrc_total ** 2)
        irc_adj = agg * irc_total / denom
        mer_adj = agg * mer_total / denom
        mrc_adj = agg * mrc_total / denom
    else:
        agg = irc_adj = mer_adj = mrc_adj = ZERO
        notes.append('No insurance, event or market risk charge — the risk '
                     'inputs are not loaded.')

    spare = [_d(net_assets.get(b)) - _d(alloc_mrctr.get(b)) for b in buckets]
    factors = [_d(mrc_factors.get(b)) for b in buckets]
    irc_alloc, mrc_alloc = allocate(spare, irc_adj + mer_adj, mrc_adj)

    g_ins = Decimal('1') - Decimal('0.5') * _weighted_ratio(factors, irc_alloc)
    g_mkt = Decimal('1') - _weighted_ratio(factors, mrc_alloc)
    if sum(irc_alloc, ZERO) == 0:
        g_ins = ZERO
    if sum(mrc_alloc, ZERO) == 0:
        g_mkt = ZERO

    # The sheet returns 0 here; we floor at the statutory MCR instead — see the
    # module docstring. A target shown as 0.00 would read as "none required".
    if g_ins > 0 and g_mkt > 0:
        risk_based = _sqrt(((irc_adj + mer_adj) / g_ins) ** 2
                           + (mrc_adj / g_mkt) ** 2)
    elif g_ins > 0:
        risk_based = (irc_adj + mer_adj) / g_ins
    else:
        risk_based = ZERO
        notes.append('No assets allocated, so the g-factors cannot be derived '
                     '— falling back to the minimum capital requirement.')

    pct = mcr if mcr > risk_based else risk_based
    if pct == mcr and risk_based < mcr:
        notes.append('The minimum capital requirement is the binding constraint.')

    return {
        'irc': irc,
        'irc_total': irc_total,
        'mer_total': mer_total,
        'mrc': mrc,
        'mrc_total': mrc_total,
        'combined': agg,
        'irc_adj': irc_adj,
        'mer_adj': mer_adj,
        'mrc_adj': mrc_adj,
        'g_insurance': g_ins,
        'g_market': g_mkt,
        'risk_based': risk_based,
        'pct': pct,
        'notes': notes,
    }
