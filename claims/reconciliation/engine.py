"""
claims/reconciliation/engine.py — month-end claims reconciliation, the arithmetic.

Pure Python. No ORM, no Django, no I/O — so it can be unit-tested directly with
worked examples (Empirica/ifrs17.engine follows the same rule: "the engine owns
nothing else"). Callers (a view, a management command, a future scheduler — none
of which exist yet) pass in the period's balances and the period's paid claims;
this module returns the figures. It never posts to the GL and never reads or
writes the database itself.

SPEC (Kago Tshutlhedi, Finance Manager, 2026-09):

    Incurred Claims (P&L) = Closing Claims Payable − Opening Claims Payable
                             − Claims Paid

Where Opening/Closing Claims Payable are the outstanding-claims liability
balances at the start/end of the period. omni does not hold a point-in-time
history of that balance today — `integrations.GraphiteClaim` is a live mirror
(upserted in place from the nightly Graphite pull; see graphite_live_claims.py),
not a dated snapshot register, so there is no "the balance as it stood on
31 May" to read back out of it. Opening/Closing Claims Payable are therefore
INPUTS to this engine (e.g. from a Finance-prepared trial balance / claims
register extract for that date), never invented or derived here.

VAT: Claims Paid arrives GROSS of VAT. Claims Paid excl. VAT = Payment / (1 +
vat_rate). The VAT rate is a PARAMETER (Botswana is 14% today but it changes —
never hard-code 1.14) and rounds HALF UP (a tax decision, never a language
default).

Reinsurance: each claim payment splits Retention / GQS / MQS / Surplus. Total RI
= GQS + MQS + Surplus. Retention% + Total RI% must equal 100% for every claim;
a claim that doesn't is a DATA ERROR, flagged in `exceptions`, never silently
absorbed into the total.

Two different rounding rules are used on purpose, for two different jobs —
never unify them:
  - VAT is a TAX FIGURE: it rounds HALF UP (Botswana's stated rule).
  - The RI split is an ALLOCATION of one fixed total across four shares: it
    uses the largest-remainder method (floor every share, hand the leftover
    cents to the largest discarded fractions) so the shares always sum
    exactly to the payment and none can go negative. HALF UP has no such
    guarantee here — see `_allocate_largest_remainder`'s docstring for the
    case that breaks it (two-or-more shares rounding up together can
    overshoot the payment by more than any single share can absorb).

Treaty percentages (which % apply to which claim) are NOT invented here. This
repo's `reinsurance.ReinsuranceTreaty` model carries one blended
`cession_share_percent` per treaty (QS/Surplus) — it has no per-claim
Retention/GQS/MQS/Surplus breakdown, and no FAC/treaty register keyed on
(treaty name, policy) with that four-way split was found anywhere in the
codebase (searched for GQS/MQS, `reinsurance/`, and every `*fac*` path). So the
split percentages for each claim are an INPUT the caller supplies (e.g. from
Finance's treaty/bordereau workings) — never a hard-coded or guessed number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

ZERO = Decimal('0.00')
HUNDRED = Decimal('100')

# How far a claim's Retention% + Total RI% may drift from 100% and still pass.
# Guards against binary float noise in an upstream export (~1e-12), never a
# genuine data error — one unit of the `_pct` quantum (0.0001), not a whole
# hundredth of a percentage point.
_PERCENT_TOLERANCE = Decimal('0.0001')


def money(value) -> Decimal:
    """Two decimals, HALF UP. Rounding is a tax decision, never a language default."""
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _pct(value) -> Decimal:
    """Four-decimal precision for a percentage split, HALF UP."""
    return Decimal(value).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class ClaimPayment:
    """One claim payment made in the period, gross of VAT, with its RI split.

    `retention_pct`, `gqs_pct`, `mqs_pct`, `surplus_pct` are whole percentages
    (e.g. 30 for 30%), keyed by the caller to whatever treaty applies to this
    claim's policy — this engine does not look treaties up itself (see module
    docstring: no authoritative per-claim treaty-split source was found).
    """
    claim_number: str
    policy_number: str
    treaty_name: str
    payment_gross: Decimal
    retention_pct: Decimal = ZERO
    gqs_pct: Decimal = ZERO
    mqs_pct: Decimal = ZERO
    surplus_pct: Decimal = ZERO

    @property
    def total_ri_pct(self) -> Decimal:
        return _pct(self.gqs_pct) + _pct(self.mqs_pct) + _pct(self.surplus_pct)

    @property
    def split_pct_total(self) -> Decimal:
        return _pct(self.retention_pct) + self.total_ri_pct


@dataclass
class RIException:
    """A claim whose Retention% + Total RI% did not add up to 100%."""
    claim_number: str
    policy_number: str
    treaty_name: str
    retention_pct: Decimal
    total_ri_pct: Decimal
    split_pct_total: Decimal

    def as_dict(self) -> dict:
        return {
            'claim_number': self.claim_number,
            'policy_number': self.policy_number,
            'treaty_name': self.treaty_name,
            'retention_pct': str(self.retention_pct),
            'total_ri_pct': str(self.total_ri_pct),
            'split_pct_total': str(self.split_pct_total),
            'variance_pct': str(self.split_pct_total - HUNDRED),
        }


@dataclass
class ClaimPaymentResult:
    """One claim's payment, de-grossed of VAT and split by RI share."""
    claim_number: str
    policy_number: str
    treaty_name: str
    payment_gross: Decimal
    payment_excl_vat: Decimal
    vat_amount: Decimal
    retention_amount: Decimal
    gqs_amount: Decimal
    mqs_amount: Decimal
    surplus_amount: Decimal
    total_ri_amount: Decimal
    is_valid_split: bool


@dataclass
class ReconciliationResult:
    period_label: str
    vat_rate: Decimal

    opening_claims_payable: Decimal
    closing_claims_payable: Decimal
    claims_paid_gross: Decimal
    claims_paid_excl_vat: Decimal
    vat_on_claims_paid: Decimal

    incurred_claims: Decimal  # the core formula's output — P&L figure

    retention_total: Decimal
    total_ri_total: Decimal

    claim_results: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)  # list[RIException]

    @property
    def has_exceptions(self) -> bool:
        return bool(self.exceptions)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _require_decimal(value, name: str) -> Decimal:
    """Reject a float outright rather than silently coercing it — Decimal(0.14)
    is 0.1400000000000000133..., not 0.14."""
    if isinstance(value, float):
        raise TypeError(f'{name} must be a Decimal (or int/str), not float: {value!r}')
    return Decimal(value)


def degross_vat(payment_gross: Decimal, vat_rate: Decimal) -> tuple[Decimal, Decimal]:
    """Split a gross payment into (excl_vat, vat_amount). vat_rate is a
    fraction, e.g. Decimal('0.14') for 14% — NEVER hard-coded, always passed
    in by the caller. Rounds HALF UP (Botswana VAT rule)."""
    gross = _require_decimal(payment_gross, 'payment_gross')
    rate = _require_decimal(vat_rate, 'vat_rate')
    excl_vat = money(gross / (Decimal('1') + rate))
    vat_amount = money(gross - excl_vat)
    return excl_vat, vat_amount


def _allocate_largest_remainder(payment_excl_vat: Decimal, pct_by_key: dict) -> dict:
    """Standard largest-remainder allocation, safe by construction.

    Each share is first FLOORED to the cent (ROUND_DOWN) — never HALF UP —
    so no individual share can exceed its true pro-rata portion, and the
    leftover (payment minus the sum of the floors) is therefore always
    between 0 and (n-1) cents and NEVER negative. Those leftover cents are
    then handed out one at a time to the shares with the largest discarded
    fraction, in `pct_by_key`'s (fixed) order on a tie.

    Rounding every share HALF UP first and patching the residual into one of
    them (an earlier version of this function) cannot be made safe: when two
    or more shares round up together, the total overshoot can exceed any
    single share, so plugging it all into one share can drive it negative
    (e.g. excl-VAT 0.02 split 25/25/25/25: each share is 0.005 and rounds
    HALF UP to 0.01, the four sum to 0.04 against a 0.02 payment — a residual
    of -0.02 that no single 0.01 share can absorb). Flooring first makes that
    structurally impossible: every floored share is <= its true portion, so
    the sum of floors is always <= the payment.
    """
    cents = (payment_excl_vat * 100).to_integral_value(rounding=ROUND_DOWN)
    floor_amounts = {}
    fractions = {}
    floor_cents_total = 0
    for key, pct in pct_by_key.items():
        exact = payment_excl_vat * pct / HUNDRED
        floor_amt = exact.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        floor_amounts[key] = floor_amt
        fractions[key] = exact - floor_amt
        floor_cents_total += int(floor_amt * 100)

    leftover_cents = int(cents) - floor_cents_total
    # Largest discarded fraction first; ties broken by pct_by_key's own
    # (fixed) insertion order — retention, gqs, mqs, surplus.
    ordered_keys = sorted(pct_by_key.keys(), key=lambda k: fractions[k], reverse=True)
    for i in range(leftover_cents):
        key = ordered_keys[i % len(ordered_keys)]
        floor_amounts[key] += Decimal('0.01')
    return floor_amounts


def split_claim_ri(claim: ClaimPayment, payment_excl_vat: Decimal) -> ClaimPaymentResult:
    """Split one claim's (VAT-excl) payment across Retention/GQS/MQS/Surplus,
    and flag it if Retention% + Total RI% != 100%.

    Two different rounding rules for two different purposes, deliberately
    not unified:
      - VAT (`degross_vat`/`money`) is a TAX figure — it rounds HALF UP,
        Botswana's stated rule, because that is what the tax law says a
        single number must round to.
      - The RI split here is an ALLOCATION of one fixed total (the payment)
        across four shares — it uses the largest-remainder method (floor
        each share, hand out the leftover cents to the largest fractions)
        so the shares always sum EXACTLY to the payment and none can go
        negative. HALF UP has no such guarantee for an allocation: patching
        a HALF-UP residual into a single share can drive it negative (see
        `_allocate_largest_remainder`'s docstring). Do not "simplify" these
        into one rule — they solve different problems.

    For an INVALID split every share stays pro-rata (HALF UP, the original
    per-share rounding) so the exception still shows the real gap — the
    largest-remainder allocation only applies once a split is confirmed to
    actually total 100%."""
    is_valid = abs(claim.split_pct_total - HUNDRED) <= _PERCENT_TOLERANCE

    if is_valid:
        allocated = _allocate_largest_remainder(payment_excl_vat, {
            'retention': _pct(claim.retention_pct),
            'gqs': _pct(claim.gqs_pct),
            'mqs': _pct(claim.mqs_pct),
            'surplus': _pct(claim.surplus_pct),
        })
        retention_amt = allocated['retention']
        gqs_amt = allocated['gqs']
        mqs_amt = allocated['mqs']
        surplus_amt = allocated['surplus']
    else:
        retention_amt = money(payment_excl_vat * _pct(claim.retention_pct) / HUNDRED)
        gqs_amt = money(payment_excl_vat * _pct(claim.gqs_pct) / HUNDRED)
        mqs_amt = money(payment_excl_vat * _pct(claim.mqs_pct) / HUNDRED)
        surplus_amt = money(payment_excl_vat * _pct(claim.surplus_pct) / HUNDRED)

    return ClaimPaymentResult(
        claim_number=claim.claim_number,
        policy_number=claim.policy_number,
        treaty_name=claim.treaty_name,
        payment_gross=money(claim.payment_gross),
        payment_excl_vat=payment_excl_vat,
        vat_amount=money(claim.payment_gross) - payment_excl_vat,
        retention_amount=retention_amt,
        gqs_amount=gqs_amt,
        mqs_amount=mqs_amt,
        surplus_amount=surplus_amt,
        total_ri_amount=gqs_amt + mqs_amt + surplus_amt,
        is_valid_split=is_valid,
    )


def reconcile_period(
    period_label: str,
    opening_claims_payable: Decimal,
    closing_claims_payable: Decimal,
    claim_payments: list[ClaimPayment],
    vat_rate: Decimal,
) -> ReconciliationResult:
    """Compute one month's claims reconciliation.

    Args:
        period_label: e.g. 'FY26-Aug'. Stored verbatim, not parsed.
        opening_claims_payable: the outstanding-claims liability at period
            start (INPUT — see module docstring; not derived here).
        closing_claims_payable: the outstanding-claims liability at period end
            (INPUT — same caveat).
        claim_payments: every claim payment made in the period, gross of VAT,
            each carrying its own Retention/GQS/MQS/Surplus %.
        vat_rate: fraction, e.g. Decimal('0.14'). A parameter — it changes.

    Returns:
        ReconciliationResult — the core formula's figures, the VAT split, the
        per-claim RI split, and the exceptions list for any claim whose split
        does not sum to 100%.
    """
    vat_rate = _require_decimal(vat_rate, 'vat_rate')
    opening = money(opening_claims_payable)
    closing = money(closing_claims_payable)

    claim_results: list[ClaimPaymentResult] = []
    exceptions: list[RIException] = []
    total_paid_gross = ZERO
    total_paid_excl_vat = ZERO
    total_vat = ZERO
    total_retention = ZERO
    total_ri = ZERO

    for claim in claim_payments:
        excl_vat, vat_amount = degross_vat(claim.payment_gross, vat_rate)
        result = split_claim_ri(claim, excl_vat)
        claim_results.append(result)

        total_paid_gross += result.payment_gross
        total_paid_excl_vat += result.payment_excl_vat
        total_vat += result.vat_amount
        total_retention += result.retention_amount
        total_ri += result.total_ri_amount

        if not result.is_valid_split:
            exceptions.append(RIException(
                claim_number=claim.claim_number,
                policy_number=claim.policy_number,
                treaty_name=claim.treaty_name,
                retention_pct=_pct(claim.retention_pct),
                total_ri_pct=claim.total_ri_pct,
                split_pct_total=claim.split_pct_total,
            ))

    # The core formula. Claims Paid here is the VAT-EXCLUSIVE figure — the P&L
    # claims expense never carries VAT (VAT is not a cost to the insurer).
    incurred_claims = money(closing - opening - total_paid_excl_vat)

    return ReconciliationResult(
        period_label=period_label,
        vat_rate=vat_rate,
        opening_claims_payable=opening,
        closing_claims_payable=closing,
        claims_paid_gross=money(total_paid_gross),
        claims_paid_excl_vat=money(total_paid_excl_vat),
        vat_on_claims_paid=money(total_vat),
        incurred_claims=incurred_claims,
        retention_total=money(total_retention),
        total_ri_total=money(total_ri),
        claim_results=claim_results,
        exceptions=exceptions,
    )
