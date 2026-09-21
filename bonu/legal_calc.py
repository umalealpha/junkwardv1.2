"""bonu/legal_calc.py — every figure the legal-office screens show.

Kept apart from the API on purpose: these are pure functions over the registers,
so the arithmetic that drives two bonuses can be tested directly rather than
through an HTTP round trip.

Two rules run through all of it:

1. **One fixed panel rate for every TASK; disbursements carry no saving.** The
   external-attorney comparison for a legal task is a single flat BWP 1,900,
   the same for every task, whether or not it has a rate-map entry. It used to
   read a per-task rate out of the map (so different tasks were compared against
   different rates) and an unmapped task showed "no mapping" and dropped out of
   the saving entirely (reported 17-Sep-2026). A DISBURSEMENT — an out-of-pocket
   cost like a courier or a filing fee — is passed through at the same amount
   either way, so it is compared to itself and carries no saving; comparing it to
   the flat panel rate would invent a saving that never existed and inflate the
   quarterly bonus (CFO decision 17-Sep-2026).
2. **One quarter definition.** Calendar quarters — Q1 Jan-Mar … Q4 Oct-Dec —
   the same basis the CFO fixed for the P90,000 member benefit year on
   3 Aug 2026. A bonus measured over a window the contract does not use is not
   enforceable.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date

ZERO = Decimal('0')

# The external-attorney panel rate for a legal TASK — one flat figure, the same
# for every task, agreed by the legal office. Disbursements do NOT use it (they
# carry no saving). If the panel rate ever changes, change it here.
FIXED_EXTERNAL_TASK_RATE = Decimal('1900')


def month_key(d: date) -> str:
    return f'{d.year:04d}-{d.month:02d}'


def quarter_key(d: date) -> str:
    return f'{d.year:04d}-Q{(d.month - 1) // 3 + 1}'


def quarter_months(qkey: str) -> list[str]:
    """The three month keys inside a quarter, in order.

    A year has four quarters. `2026-Q5` parses perfectly well and would put a
    phantom period in the dropdown over an empty screen, so it is refused here
    — the one place that decides what a quarter is.
    """
    year, q = qkey.split('-Q')
    year, q = int(year), int(q)
    if not 1 <= q <= 4:
        raise ValueError(f'{qkey} is not a quarter — a year has Q1 to Q4.')
    start = (q - 1) * 3 + 1
    return [f'{year:04d}-{start + i:02d}' for i in range(3)]


def quarter_of_month(mkey: str) -> str:
    year, month = mkey.split('-')
    return f'{int(year):04d}-Q{(int(month) - 1) // 3 + 1}'


# --------------------------------------------------------------------------
# Fee notes and the external comparison
# --------------------------------------------------------------------------

def mapping_index(mappings) -> dict:
    """Fee description -> mapping, matched case- and space-insensitively so a
    line typed 'Legal Research' still finds the 'Legal research' map entry."""
    return {(m.fee_description or '').strip().lower(): m for m in mappings}


def internal_amount(fee) -> Decimal:
    return (fee.rate or ZERO) * (fee.qty or ZERO)


def external_equivalent(fee, index: dict):
    """What the same work would have cost at the external panel.

    A legal TASK is compared against the one flat panel rate, the same for every
    task, whether or not it has a map entry. A DISBURSEMENT (an out-of-pocket
    cost) is passed through at the same amount either way, so it is compared to
    its own internal amount and carries no saving. Never returns None now — an
    unmapped task is still compared, not dropped."""
    m = index.get((fee.description or '').strip().lower())
    if m is not None and m.is_disbursement:
        return internal_amount(fee)          # pass-through cost: external == internal, no saving
    return FIXED_EXTERNAL_TASK_RATE


def fee_line(fee, index: dict) -> dict:
    """One fee-note line with its comparison, ready for the screen."""
    m = index.get((fee.description or '').strip().lower())
    is_disb = m is not None and m.is_disbursement
    internal = internal_amount(fee)
    external = external_equivalent(fee, index)   # always computed now
    saving = external - internal
    pct = (saving / external) if external != ZERO else None
    return {
        'id': str(fee.id),
        'date': fee.date.isoformat() if fee.date else None,
        'client': fee.client,
        'portfolio': fee.portfolio,
        'description': fee.description,
        'unit': fee.unit,
        'rate': str(fee.rate),
        'qty': str(fee.qty),
        'amount': str(internal),
        'external_item': m.external_item if m else '',
        # A task shows the flat panel rate; a disbursement shows its own pass-through cost.
        'external_rate': str(external) if is_disb else str(FIXED_EXTERNAL_TASK_RATE),
        'calc_basis': 'Disbursement (at cost)' if is_disb else 'Fixed panel rate',
        'external_equivalent': str(external),
        'saving': str(saving),
        'pct_saved': None if pct is None else float(pct),
        'mapped': True,          # every line is compared now — task at 1900, disbursement at cost
        'is_disbursement': is_disb,
        'updated_by': fee.updated_by_email,
    }


def totals_for_fees(fees, index: dict) -> dict:
    """Internal billed, external equivalent and the in-house saving over a set
    of fee lines. Every line is compared now — a task at the flat panel rate, a
    disbursement at its own cost — so `external` and `saving` cover them all and
    nothing is left out (`unmapped` stays 0)."""
    internal = ZERO
    external = ZERO
    for f in fees:
        internal += internal_amount(f)
        external += external_equivalent(f, index)   # every line: task at 1900, disbursement at cost
    saving = external - internal
    return {
        'internal': internal,
        'mapped_internal': internal,   # every line is compared now, so this equals internal
        'external': external,
        'saving': saving,
        'pct_saved': (saving / external) if external else None,
        'unmapped': 0,
    }


# --------------------------------------------------------------------------
# Invoice savings and the SLA
# --------------------------------------------------------------------------

def savings_total(rows) -> Decimal:
    return sum((r.saving for r in rows), ZERO)


def sla_summary(rows, sla_days: int) -> dict:
    """How many reviewed bills were turned round inside the target. Bills with
    no received date are excluded from both sides — an unknown turnaround is
    not a met one, and counting it as missed would be just as wrong."""
    measured = [r for r in rows if r.turnaround_days is not None]
    within = [r for r in measured if r.turnaround_days <= sla_days]
    return {
        'measured': len(measured),
        'within': len(within),
        'unknown': len(rows) - len(measured),
        'pct': (Decimal(len(within)) / Decimal(len(measured))) if measured else None,
    }


# --------------------------------------------------------------------------
# The two bonuses
# --------------------------------------------------------------------------

def quarterly_bonus(quarter_saving: Decimal, settings) -> dict:
    """2% of the WHOLE quarterly saving once the trigger is reached — not 2% of
    each invoice on its own."""
    met = quarter_saving >= settings.quarterly_threshold
    return {
        'saving': quarter_saving,
        'threshold': settings.quarterly_threshold,
        'met': met,
        'pct': settings.quarterly_bonus_pct,
        'bonus': (quarter_saving * settings.quarterly_bonus_pct) if met else ZERO,
    }


def monthly_bonus(entered, settings) -> Decimal:
    """The entered figure, held to the cap. `entered` is None when the officer
    has not put a figure in for the month yet."""
    if entered is None:
        return ZERO
    return min(Decimal(entered), settings.monthly_bonus_cap)


def advisory_value(hours: Decimal, settings) -> Decimal:
    return (hours or ZERO) * settings.external_hourly_rate
