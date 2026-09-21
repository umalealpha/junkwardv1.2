"""
ifrs17/materiality.py — which exceptions Finance must actually answer.

CFO directive 2026-08-26: "create an IFRS 17 exception report and they have to
explain what are those exceptions. Just don't put everything — major ones only,
small variance ignore, work on the materiality."

So this module is the filter. It turns the fourteen disclosed variances in
`constants.KNOWN_VARIANCES` into three bands, and ONLY the top band creates an
obligation on Finance to write an explanation.

The threshold is derived, not invented
--------------------------------------
Benchmark is IFRS 17 insurance revenue, because this is a revenue-driven
insurance book and revenue is the most stable of the reported measures.

    overall materiality      = 0.5% x insurance revenue 133,416,295 =  667,081
    performance materiality  = 75% of overall                       =  500,311
    register threshold       = performance materiality, rounded down =  500,000

Cross-check against the other usual benchmark: 5% of profit before tax
(12,864,243) is 643,212 — above our threshold, so the revenue basis is the
tighter of the two and nothing material escapes on the profit view either.

Two things are material by NATURE regardless of size
----------------------------------------------------
A small number can still be a big problem when it is a recognition,
completeness or audit-evidence issue rather than a measurement rounding. Those
are listed in `MATERIAL_BY_NATURE` with the reason, so the classification is
never a matter of opinion at the time someone reads the register.

Nothing here recomputes a figure. This module only decides who has to write.
"""
from __future__ import annotations

from decimal import Decimal as D

from . import constants as K

# --- the derivation, kept as constants so the register can show its working ---
MATERIALITY_BENCHMARK = 'insurance_revenue'
MATERIALITY_BENCHMARK_LABEL = 'IFRS 17 insurance revenue'
OVERALL_MATERIALITY_PCT = D('0.005')      # 0.5% of insurance revenue
PERFORMANCE_MATERIALITY_PCT = D('0.75')   # 75% of overall, the usual haircut

# Rounded DOWN to a round number a human can hold in their head. Rounding down
# is the conservative direction: it pulls MORE items into the register.
REGISTER_THRESHOLD = D('500000')

# The alternative benchmark, recorded so the choice is auditable.
PBT_BENCHMARK_PCT = D('0.05')             # 5% of profit before tax

# Items at or above this share of the threshold are shown as a watch list:
# visible, trending toward material, but NOT demanding an explanation.
WATCH_BAND_PCT = D('0.50')

BAND_MATERIAL = 'material'
BAND_WATCH = 'watch'
BAND_IMMATERIAL = 'immaterial'


# Material by nature — the amount is small but the issue is not a rounding.
# Keyed by DQ ref, value is the reason shown in the register.
MATERIAL_BY_NATURE: dict[str, str] = {
    'DQ-02': (
        'Recognition and audit evidence, not size. 30% of the reinsurer panel '
        'is unconfirmed and one confirmed participant is rated B+ with a 2.44% '
        'one-year default probability, yet no expected credit loss has been '
        'computed at all against a 35.4m reinsurance contract asset. The '
        'computed ECL is small; having no ECL and no signed panel evidence is '
        'not.'
    ),
    'DQ-03': (
        'Completeness of measurement, not size. Health is an entire product '
        'line sitting outside the eight-segment model, it is loss-making, and '
        'it carries ceded premium that is not modelled because no Health '
        'treaty slip exists. An unmodelled line cannot be shown to be measured '
        'correctly at any amount.'
    ),
}


def overall_materiality() -> D:
    """0.5% of IFRS 17 insurance revenue."""
    revenue = K.REPORTED['FY2026']['insurance_revenue']
    return (revenue * OVERALL_MATERIALITY_PCT).quantize(D('0.01'))


def performance_materiality() -> D:
    """75% of overall materiality — the figure the threshold is rounded from."""
    return (overall_materiality() * PERFORMANCE_MATERIALITY_PCT).quantize(D('0.01'))


def pbt_cross_check() -> D:
    """5% of profit before tax, the alternative benchmark."""
    pbt = K.REPORTED['FY2026']['profit_before_tax']
    return (pbt * PBT_BENCHMARK_PCT).quantize(D('0.01'))


def watch_floor() -> D:
    """The bottom of the watch band."""
    return (REGISTER_THRESHOLD * WATCH_BAND_PCT).quantize(D('0.01'))


def classify(variance: dict) -> dict:
    """Band one KNOWN_VARIANCES entry, and say WHY in words.

    Returns the band, whether an explanation is required, and the reason —
    so the register never shows a classification a reader cannot challenge.
    """
    ref = variance['ref']
    amount = D(str(variance.get('amount') or 0))
    threshold = REGISTER_THRESHOLD

    by_nature = MATERIAL_BY_NATURE.get(ref)
    if by_nature:
        return {
            'band': BAND_MATERIAL,
            'explanation_required': True,
            'basis': 'qualitative',
            'reason': by_nature,
        }

    if amount >= threshold:
        return {
            'band': BAND_MATERIAL,
            'explanation_required': True,
            'basis': 'quantitative',
            'reason': (
                f'{amount:,.2f} is at or above the register threshold of '
                f'{threshold:,.0f} (75% of 0.5% of IFRS 17 insurance revenue).'
            ),
        }

    if amount >= watch_floor():
        return {
            'band': BAND_WATCH,
            'explanation_required': False,
            'basis': 'quantitative',
            'reason': (
                f'{amount:,.2f} is below the {threshold:,.0f} threshold but at '
                f'or above half of it, so it is monitored rather than answered.'
            ),
        }

    return {
        'band': BAND_IMMATERIAL,
        'explanation_required': False,
        'basis': 'quantitative',
        'reason': (
            f'{amount:,.2f} is below half the {threshold:,.0f} threshold — a '
            f'small variance, disclosed and accepted without a response.'
        ),
    }


def basis_summary() -> dict:
    """The materiality working, for the top of the register and the report."""
    return {
        'benchmark': MATERIALITY_BENCHMARK,
        'benchmark_label': MATERIALITY_BENCHMARK_LABEL,
        'benchmark_amount': K.REPORTED['FY2026']['insurance_revenue'],
        'overall_materiality_pct': OVERALL_MATERIALITY_PCT,
        'overall_materiality': overall_materiality(),
        'performance_materiality_pct': PERFORMANCE_MATERIALITY_PCT,
        'performance_materiality': performance_materiality(),
        'register_threshold': REGISTER_THRESHOLD,
        'pbt_cross_check_pct': PBT_BENCHMARK_PCT,
        'pbt_cross_check': pbt_cross_check(),
        'watch_floor': watch_floor(),
        'note': (
            'Only exceptions in the material band require a written explanation '
            'from Finance. Watch-band items are monitored. Immaterial items are '
            'disclosed and accepted with no response required.'
        ),
    }


def classified_variances() -> list[dict]:
    """Every known variance with its band attached, material first."""
    out = []
    for v in K.KNOWN_VARIANCES:
        c = classify(v)
        out.append({**v, **{f'materiality_{k}': val for k, val in c.items()}})
    order = {BAND_MATERIAL: 0, BAND_WATCH: 1, BAND_IMMATERIAL: 2}
    out.sort(key=lambda r: (order[r['materiality_band']],
                            -D(str(r.get('amount') or 0))))
    return out
