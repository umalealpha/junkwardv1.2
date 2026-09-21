"""
ifrs17/engine.py — the arithmetic, in one place.

Pure functions. No ORM, no Django, no I/O — so it can be unit-tested directly
against Empirica's own tables, which is exactly what ifrs17/tests/test_parity.py
does. The rule copied from frontend/src/lib/fiveYearModel.ts: THE PAGE OWNS NO
ARITHMETIC. Here it is the mirror rule — the engine owns nothing else.

`frontend/src/lib/ifrs17Model.ts` implements the same maths for the cockpit. The
two MUST agree to the cent; test_parity.py holds both to the report.

METHOD (Empirica, 30 June 2026 — report §3):
    PAA for every group. No discounting (paras 56 / 59(b) expedients).
    Risk adjustment 6% of fulfilment cash flows at the 75th percentile.
    Eight reporting segments; Health excluded and not modelled.

Nothing in here posts to the general ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_HALF_UP

from .constants import (
    BASE_LEVERS, HEALTH_FY26, IBNR_BY_UWY_FY26, JBB_CEDED_PREMIUM_FY26,
    JBB_PROVISIONAL_RATE, REPORTED, SEGMENT_FY26, TREATIES_FY26,
)

ZERO = D('0')


def money(v: D) -> D:
    """Two decimals, HALF UP. Rounding is a tax decision, never a language default."""
    return D(v).quantize(D('0.01'), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Levers
# ---------------------------------------------------------------------------

@dataclass
class Levers:
    """The cockpit sliders. Defaults are Empirica's FY2026 selections, verbatim."""
    ra_pct: D = BASE_LEVERS['ra_pct']
    confidence_level: int = BASE_LEVERS['confidence_level']
    che_factor_pct: D = BASE_LEVERS['che_factor_pct']
    salvage_subro_pct: D = BASE_LEVERS['salvage_subro_pct']
    attributable_share_pct: D = BASE_LEVERS['attributable_share_pct']
    ibnr_tail_factor: D = BASE_LEVERS['ibnr_tail_factor']
    discounting: bool = BASE_LEVERS['discounting']
    jbb_commission_pct: D = BASE_LEVERS['jbb_commission_pct']
    onerous_test: bool = BASE_LEVERS['onerous_test']
    health_modelled: bool = BASE_LEVERS['health_modelled']

    @classmethod
    def base(cls) -> 'Levers':
        return cls()


@dataclass
class SegmentRow:
    segment: str
    premium: D
    commission: D
    claims: D
    unearned_premium: D
    attributable_expense: D = ZERO
    loss_ratio: D = ZERO
    combined_ratio: D = ZERO


@dataclass
class Computed:
    """Everything the cockpit, the statements and the disclosures read."""
    year: str
    levers: Levers

    insurance_revenue: D = ZERO
    claims_incurred: D = ZERO
    attributable_expenses: D = ZERO
    acquisition_amortisation: D = ZERO
    insurance_service_expenses: D = ZERO
    service_result_before_reinsurance: D = ZERO

    allocation_of_reinsurance_premiums: D = ZERO
    amounts_recoverable: D = ZERO
    net_reinsurance_result: D = ZERO
    insurance_service_result: D = ZERO
    profit_before_tax: D = ZERO

    lrc: D = ZERO
    loss_component: D = ZERO
    gross_case_reserves: D = ZERO
    gross_ibnr: D = ZERO
    che_reserve: D = ZERO
    salvage_subrogation: D = ZERO
    lic_best_estimate: D = ZERO
    lic_risk_adjustment: D = ZERO
    insurance_contract_liabilities: D = ZERO

    arc: D = ZERO
    aric_best_estimate: D = ZERO
    aric_risk_adjustment: D = ZERO
    reinsurance_contract_assets: D = ZERO
    net_ifrs17_liability: D = ZERO

    loss_ratio: D = ZERO
    combined_ratio: D = ZERO
    expense_ratio: D = ZERO
    acquisition_ratio: D = ZERO

    by_segment: list[SegmentRow] = field(default_factory=list)
    by_uwy: list[dict] = field(default_factory=list)
    by_treaty: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Components (report §3.5 – §3.11)
# ---------------------------------------------------------------------------

def claims_handling_expense_reserve(gross_ibnr: D, gross_case_reserves: D,
                                    factor: D) -> D:
    """§3.7 — factor applied to (best-estimate gross IBNR + HALF of case reserves).

    The half is not a simplification: it is the stated reserve base. FY2026's whole
    increase came from the base, not the factor, because no approved FY2026
    claims-handling allocation was provided and the FY2025 factor was carried
    forward unchanged.
    """
    return money(factor * (gross_ibnr + gross_case_reserves / 2))


def salvage_subrogation_deduction(gross_case_reserves: D, gross_ibnr: D,
                                  pct: D) -> D:
    """§3.11 — a management-approved expected-recovery estimate, expressed against
    gross case reserves plus gross IBNR. Returned POSITIVE; the caller subtracts."""
    return money(pct * (gross_case_reserves + gross_ibnr))


def risk_adjustment(fulfilment_cash_flows: D, pct: D) -> D:
    """§3.8 — a flat percentage of fulfilment cash flows, calibrated to the 75th
    percentile. Applied gross AND to reinsurance held."""
    return money(pct * fulfilment_cash_flows)


def liability_for_incurred_claims(*, case_reserves: D, ibnr: D, che: D,
                                  salvage: D) -> D:
    """§3.5 Table 6 — the best estimate, before the risk adjustment."""
    return money(case_reserves + ibnr + che - salvage)


def chain_ladder_ibnr(by_uwy: dict, tail_factor: D,
                      signed_total: D,
                      base_tail: D = D('1.003100')) -> tuple[D, list[dict]]:
    """§3.6 / §5.2 — IBNR by underwriting year.

    🔴 THE SIGNED SELECTION IS THE TRUTH, NOT MY RE-ADDITION.

    Empirica's selection is a reperformed, signed actuarial opinion. This function
    does NOT re-run the triangle and then overrule it. At the signed tail factor it
    returns `signed_total` verbatim; the per-year rows are carried for display and
    analysis.

    That matters here because the report's own Table 21 does not foot: the seven
    underwriting-year rows sum to 21,155,255 against a stated selection of
    21,155,286 — a 31 rounding difference inside the report (recorded as DQ-11).
    Re-adding the rows and calling the answer "the IBNR" would silently replace a
    signed figure with a derived one that is 31 lower, and then quietly drag the
    claims-handling reserve, the salvage deduction, the LIC, the balance sheet and
    the net position with it. That is precisely how a valuation stops tying to the
    report it claims to implement.

    When the tail lever MOVES, the signed total is scaled by (tail / base_tail), so
    the slider shows sensitivity around the signed answer rather than inventing a
    different selection. The report values the FY25 nil-tail-to-1.003100 change at
    896,862, which this reproduces.
    """
    rows = []
    scale = (tail_factor / base_tail) if tail_factor != base_tail else D('1')
    for uwy, (observed, ultimate, outstanding, ibnr) in sorted(by_uwy.items()):
        rows.append({
            'underwriting_year': uwy,
            'observed_cumulative': money(observed),
            'selected_ultimate': money(ultimate),
            'outstanding': money(outstanding),
            'ibnr': money(ibnr * scale) if ibnr else money(ibnr),
        })
    return money(signed_total * scale), rows


def attributable_expenses(total_opex: D, share: D, *, signed: D | None = None,
                          signed_share: D = D('0.929')) -> D:
    """§3.10 — the attributable portion of total operating expenses.

    FY2026 92.9%, FY2025 80.5%. The report is explicit that the APPORTIONMENT
    change, not cost escalation, drives the 22.7% headline rise: of the 5,398,775
    increase, 3,891,418 is apportionment and only 1,507,357 is the underlying cost
    base growing (6.3%, in line with premium).

    The published 92.9% is a ROUNDED display of the real apportionment: the signed
    29,168,270 against total opex of 31,381,207 is 92.9435%, not 92.9000%. Naively
    multiplying by 0.929 loses 15,129. So the signed figure is returned at the
    signed share, and the lever scales proportionally away from it.
    """
    if signed is not None and share == signed_share:
        return money(signed)
    if signed is not None:
        return money(signed * (share / signed_share))
    return money(total_opex * share)


def jbb_commission_sensitivity(rate: D,
                               ceded_premium: D = JBB_CEDED_PREMIUM_FY26,
                               provisional: D = JBB_PROVISIONAL_RATE) -> D:
    """§6.3 — the JBB Motor quota share commission sensitivity.

    Commission is booked at the 25% provisional rate against a contractual sliding
    scale of 24%–41% settled on final underwriting-year loss ratio. At 41% on
    54,450,560 of ceded premium the commission is 8,712,000 higher.

    🔴 The report's own words: "That is a sensitivity, not a bookable adjustment,
    and it has not been recognised." Anything calling this MUST label it so.
    """
    return money((rate - provisional) * ceded_premium)


# ---------------------------------------------------------------------------
# The whole valuation
# ---------------------------------------------------------------------------

def _levers_at_signed_defaults(levers: Levers) -> bool:
    """True while every lever that feeds the reserves still sits on the signed
    selection — i.e. nothing has been dragged, so the signed figures stand."""
    return (levers.ra_pct == BASE_LEVERS['ra_pct']
            and levers.che_factor_pct == BASE_LEVERS['che_factor_pct']
            and levers.salvage_subro_pct == BASE_LEVERS['salvage_subro_pct']
            and levers.ibnr_tail_factor == BASE_LEVERS['ibnr_tail_factor'])


def compute(levers: Levers | None = None, *, year: str = 'FY2026',
            segments_on: set[str] | None = None) -> Computed:
    """Run the valuation. Mirrors `compute(levers, on)` in ifrs17Model.ts.

    At base levers with every segment on, this reproduces the report to the cent —
    that is the parity test, and it is a hard gate.
    """
    levers = levers or Levers.base()
    rep = REPORTED[year]
    c = Computed(year=year, levers=levers)

    seg_source = SEGMENT_FY26 if year == 'FY2026' else {}
    on = segments_on if segments_on is not None else set(seg_source)

    # ---- segments -------------------------------------------------------
    prem = comm = clms = unearned = ZERO
    for name, (p, cm, cl, ue) in seg_source.items():
        if name not in on:
            continue
        prem += p
        comm += cm
        clms += cl
        unearned += ue

    include_health = levers.health_modelled and year == 'FY2026'
    if include_health:
        prem += HEALTH_FY26['gross_written_premium']
        clms += HEALTH_FY26['claims_incurred']
        c.notes.append(
            'Health is INCLUDED. It is not one of the eight reporting segments and '
            'Empirica does not model it; it loses 108,337 and carries 70,353 of '
            'ceded premium with no identifiable Health treaty. Including it here is '
            'a sensitivity, not the signed valuation.')

    full_book = (not seg_source) or (on == set(seg_source) and not include_health)

    # ---- reserves -------------------------------------------------------
    # 🔴 The signed figures are the base. Levers move AWAY from them; they never
    # replace them with a re-derivation. See chain_ladder_ibnr's docstring for the
    # 31-pula example of why this matters.
    at_base = _levers_at_signed_defaults(levers)

    c.gross_case_reserves = money(rep['gross_case_reserves'])
    if year == 'FY2026':
        c.gross_ibnr, c.by_uwy = chain_ladder_ibnr(
            IBNR_BY_UWY_FY26, levers.ibnr_tail_factor, rep['gross_ibnr'])
    else:
        c.gross_ibnr = money(rep['gross_ibnr'])

    # DELTA-FORM RESERVES (Fable 2026-08-25). Each component is the SIGNED figure
    # plus the change the levers cause, measured as f(current) - f(base). At base
    # the two evaluations cancel and the component is EXACTLY the signed figure —
    # so there is no basis-switch discontinuity, no jump when a lever is nudged a
    # hair off base, and the signed valuation still stands verbatim. The earlier
    # code hard-switched between "signed" and "fully re-derived", which left a
    # ~15-pula seam at the boundary (the DQ-11/DQ-12 rounding class).
    case = c.gross_case_reserves
    ibnr_base = money(rep['gross_ibnr'])

    def _che(ibnr, factor):
        return claims_handling_expense_reserve(ibnr, case, factor)

    def _salv(ibnr, pct):
        return (salvage_subrogation_deduction(case, ibnr, pct)
                if rep['salvage_subrogation_deduction'] else ZERO)

    che_now = _che(c.gross_ibnr, levers.che_factor_pct)
    che_base = _che(ibnr_base, BASE_LEVERS['che_factor_pct'])
    c.che_reserve = money(rep['che_reserve'] + (che_now - che_base))

    salv_now = _salv(c.gross_ibnr, levers.salvage_subro_pct)
    salv_base = _salv(ibnr_base, BASE_LEVERS['salvage_subro_pct'])
    c.salvage_subrogation = money(-rep['salvage_subrogation_deduction']
                                  + (salv_now - salv_base))

    # LIC best estimate follows from its own components, and is likewise anchored
    # to the signed figure: signed + the movement in (case + ibnr + che - salvage).
    lic_now = liability_for_incurred_claims(
        case_reserves=case, ibnr=c.gross_ibnr, che=che_now, salvage=salv_now)
    lic_base = liability_for_incurred_claims(
        case_reserves=case, ibnr=ibnr_base, che=che_base, salvage=salv_base)
    c.lic_best_estimate = money(rep['lic_best_estimate'] + (lic_now - lic_base))

    ra_now = risk_adjustment(c.lic_best_estimate, levers.ra_pct)
    ra_base = risk_adjustment(rep['lic_best_estimate'], BASE_LEVERS['ra_pct'])
    c.lic_risk_adjustment = money(rep['lic_risk_adjustment'] + (ra_now - ra_base))

    # ---- liability for remaining coverage -------------------------------
    c.lrc = money(unearned) if seg_source and not full_book else money(rep['lrc'])
    c.loss_component = ZERO          # §3.9 — none carried at either date
    if levers.onerous_test:
        c.notes.append(
            'Onerousness test ON. Engineering (295% gross loss ratio) and Guarantee '
            '(144%) are the candidates; their combined premium of 1,076,575 is 0.8% '
            'of the book, so no loss component would be material. Empirica '
            'recommends a formal test before the FY2027 valuation.')

    c.insurance_contract_liabilities = money(
        c.lrc + c.lic_best_estimate + c.lic_risk_adjustment)

    # ---- reinsurance held ------------------------------------------------
    c.arc = money(rep.get('arc', ZERO))
    c.aric_best_estimate = money(rep.get('aric_best_estimate', ZERO))
    # Ceded RA is anchored to the signed figure the same delta way, so the
    # reinsurance asset is continuous through base too.
    if c.aric_best_estimate and 'aric_risk_adjustment' in rep:
        aric_ra_now = risk_adjustment(c.aric_best_estimate, levers.ra_pct)
        aric_ra_base = risk_adjustment(c.aric_best_estimate, BASE_LEVERS['ra_pct'])
        c.aric_risk_adjustment = money(
            rep['aric_risk_adjustment'] + (aric_ra_now - aric_ra_base))
    else:
        c.aric_risk_adjustment = money(rep.get('aric_risk_adjustment', ZERO))
    c.reinsurance_contract_assets = money(
        rep['reinsurance_contract_assets']
        + (c.aric_risk_adjustment - rep.get('aric_risk_adjustment', ZERO)))
    c.net_ifrs17_liability = money(
        c.insurance_contract_liabilities - c.reinsurance_contract_assets)

    # ---- result ----------------------------------------------------------
    c.insurance_revenue = money(prem) if (seg_source and not full_book) \
        else money(rep['insurance_revenue'])
    c.claims_incurred = money(clms) if (seg_source and not full_book) \
        else money(rep['claims_incurred'])
    c.attributable_expenses = attributable_expenses(
        rep['total_operating_expenses'], levers.attributable_share_pct,
        signed=rep['attributable_expenses'])
    c.acquisition_amortisation = money(comm) if (seg_source and not full_book) \
        else money(rep['acquisition_commission_paid'])

    # Insurance service expenses in DELTA FORM (Fable H90-twin, 2026-08-25).
    #
    # At the signed basis this is the signed figure, verbatim. When a lever moves
    # the reserves or the expense apportionment, ISE moves by the SAME delta —
    # more risk adjustment or more attributable expense is more expense, so ISE
    # goes more negative and profit falls.
    #
    # The earlier version re-derived ISE from scratch the instant any lever left
    # base. That did two wrong things at once: it never let a reserve lever reach
    # profit at full book (Python), and it introduced a ~2.18m basis-switch jump
    # that made raising the risk adjustment RAISE profit on the cockpit (the TS
    # twin). A sensitivity screen where more prudence reads as more profit is
    # worse than no screen. Both engines now use this identical delta form.
    signed_ise = rep['insurance_service_expenses']
    if seg_source and not full_book:
        # A segment has been switched off — the signed total no longer applies,
        # so ISE is the live sum of what is left in the book.
        c.insurance_service_expenses = money(
            -(c.claims_incurred + c.attributable_expenses
              + c.acquisition_amortisation + c.lic_risk_adjustment))
    else:
        c.insurance_service_expenses = money(
            signed_ise
            - (c.attributable_expenses - rep['attributable_expenses'])
            - (c.lic_risk_adjustment - rep['lic_risk_adjustment'])
            - (c.lic_best_estimate - rep['lic_best_estimate']))
    c.service_result_before_reinsurance = money(
        c.insurance_revenue + c.insurance_service_expenses)

    c.allocation_of_reinsurance_premiums = money(
        rep['allocation_of_reinsurance_premiums'])
    c.amounts_recoverable = money(rep['amounts_recoverable'])
    # The JBB slider moves reinsurance commission, which reduces the net cost of
    # reinsurance held. Labelled a sensitivity everywhere it surfaces.
    jbb_delta = (jbb_commission_sensitivity(levers.jbb_commission_pct)
                 if year == 'FY2026' else ZERO)
    if jbb_delta:
        c.notes.append(
            f'JBB Motor QS commission modelled at {levers.jbb_commission_pct:.2%} '
            f'against the 25.00% provisional rate booked: {jbb_delta:,.0f} of '
            f'additional commission. SENSITIVITY ONLY — the report states this is '
            f'"not a bookable adjustment, and it has not been recognised".')
    c.net_reinsurance_result = money(
        c.allocation_of_reinsurance_premiums + c.amounts_recoverable + jbb_delta)

    c.insurance_service_result = money(
        c.service_result_before_reinsurance + c.net_reinsurance_result)
    c.profit_before_tax = c.insurance_service_result   # no net insurance finance result

    # ---- ratios ----------------------------------------------------------
    if c.insurance_revenue:
        c.loss_ratio = c.claims_incurred / c.insurance_revenue
        c.acquisition_ratio = c.acquisition_amortisation / c.insurance_revenue
        c.expense_ratio = c.attributable_expenses / c.insurance_revenue
        c.combined_ratio = c.loss_ratio + c.acquisition_ratio + c.expense_ratio

    # ---- per-segment rows (why the toggles can work at all) --------------
    for name, (p, cm, cl, ue) in sorted(seg_source.items()):
        if name not in on:
            continue
        lr = (cl / p) if p else ZERO
        ar = (cm / p) if p else ZERO
        c.by_segment.append(SegmentRow(
            segment=name, premium=money(p), commission=money(cm),
            claims=money(cl), unearned_premium=money(ue),
            attributable_expense=money(levers.attributable_share_pct
                                       * rep['total_operating_expenses']
                                       * (p / rep['gross_written_premium'])),
            loss_ratio=lr,
            combined_ratio=lr + ar + (c.expense_ratio or ZERO),
        ))

    # ---- treaties --------------------------------------------------------
    if year == 'FY2026':
        for t, (ceded, commission, recovery) in sorted(TREATIES_FY26.items()):
            cm2 = commission + (jbb_delta if t == 'jbb_motor_qs' else ZERO)
            c.by_treaty.append({
                'treaty': t,
                'ceded_premium': money(ceded),
                'commission': money(cm2),
                'direct_recovery': money(recovery),
                'net_premium_cost': money(ceded - cm2),
                'direct_economics': money(recovery + cm2 - ceded),
            })

    if levers.discounting:
        c.notes.append(
            'Discounting ON. The signed valuation takes the paragraph 56 and 59(b) '
            'expedients and applies NO discounting to the LRC or the LIC, because '
            'claims settle quickly relative to the coverage period. Turning this on '
            'departs from the signed basis and needs an actuarial yield curve that '
            'the valuation does not carry.')

    return c
