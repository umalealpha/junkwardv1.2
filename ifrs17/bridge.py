"""
ifrs17/bridge.py — the IFRS 17 ↔ Management Accounts reconciliation.

WHY THIS IS THE FIRST THING THE MODULE DOES
────────────────────────────────────────────
Empirica's FY2025 IFRS 17 profit before tax is 26,838,707.
The frozen Management Accounts FY2025 PAT is 0.292 Mn.

Both are correct. They answer different questions:

  * IFRS 17 measures the INSURANCE SERVICE RESULT. It excludes non-attributable
    overheads, investment income, other income and tax, and it takes the movement
    in ceded reserves as income.
  * The Management Accounts measure the WHOLE COMPANY after everything.

A 26.5 million gap between two correct figures for the same company and the same
year is a production incident waiting for the first meeting where somebody quotes
one of them for the other. So the bridge is permanent, visible, and built before
any disclosure table.

FROZEN-NUMBER DISCIPLINE
────────────────────────
The MA workbook is the source of truth for MA figures — omni reconciles to it,
never the other way around. If a bridged figure disagrees with the frozen
register, this module surfaces BOTH and names which one is registered truth. It
never picks silently. (Same rule as digital-cfo-avatar/tools.detect_frozen_conflict,
and the reason the "GWP is 99M" tile was a production incident.)

Nothing here posts to the general ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D

from .constants import REPORTED

# ---------------------------------------------------------------------------
# The frozen register. These are the CFO's Management Accounts figures — the
# authoritative ADIC numbers. NEVER edit without three explicit yeses.
# ---------------------------------------------------------------------------
FROZEN_MA: dict[str, dict[str, D]] = {
    'FY2025': {
        'gross_written_premium': D('125148692'),      # 125.15 Mn
        'net_earned_premium':    D('52480000'),       # 52.48 Mn
        'gross_profit':          D('46380000'),       # 46.38 Mn
        'ebitda':                D('2480000'),        # 2.48 Mn
        'profit_after_tax':      D('292000'),         # 0.292 Mn
    },
    # FY2026 full-year MA is NOT yet frozen — the 30 June 2026 audit is still
    # running on the old accounting system and closes around 30 September 2026.
    # The 9-month figures below are the frozen ones (Jul 25 → Mar 26); they are
    # here so the bridge can say "not comparable yet" rather than invent a total.
    'FY2026_9M': {
        'gross_written_premium': D('96180000'),       # 96.18 Mn
        'net_earned_premium':    D('42010000'),       # 42.01 Mn
        'profit_after_tax':      D('950000'),         # 0.950 Mn
    },
}

TOLERANCE = D('0.005')      # half a thebe


@dataclass
class BridgeStep:
    """One labelled line in the walk. `amount` is signed."""
    label: str
    amount: D
    source: str
    note: str = ''


@dataclass
class BridgeResult:
    year: str
    steps: list[BridgeStep] = field(default_factory=list)
    ifrs17_pbt: D = D('0')
    derived_ma_pat: D = D('0')
    frozen_ma_pat: D | None = None
    unexplained: D = D('0')
    reconciles: bool = False
    conflict: dict | None = None
    comparable: bool = True

    @property
    def running(self) -> list[tuple[str, D, D]]:
        """(label, amount, running_total) so the UI can render a waterfall."""
        out, total = [], self.ifrs17_pbt
        out.append(('IFRS 17 profit before tax', self.ifrs17_pbt, total))
        for s in self.steps:
            total += s.amount
            out.append((s.label, s.amount, total))
        return out


def build_bridge(
    year: str,
    *,
    investment_income: D = D('0'),
    other_income: D = D('0'),
    ceded_reserve_movement: D | None = None,
    tax: D = D('0'),
    frozen_ma_pat: D | None = None,
) -> BridgeResult:
    """Walk IFRS 17 profit before tax to Management Accounts PAT.

    The four inputs that IFRS 17 does not carry — investment income, other
    income, tax, and the non-IFRS-17 treatment of the ceded reserve movement —
    are supplied by Finance, because they live in the ledger and not in the
    valuation. Each becomes its own labelled, visible step. Nothing is netted.

    `ceded_reserve_movement` defaults to the non-cash contribution the valuation
    itself reports (the part of amounts recoverable that is NOT cash received).
    """
    rep = REPORTED.get(year)
    if rep is None:
        raise ValueError(f'No reported IFRS 17 result for {year}.')

    res = BridgeResult(year=year, ifrs17_pbt=rep['profit_before_tax'])

    # 1. Non-attributable expenses. IFRS 17 excludes them from the service
    #    result; the Management Accounts carry them in full.
    res.steps.append(BridgeStep(
        label='Less: non-attributable operating expenses',
        amount=-rep['non_attributable_expenses'],
        source=f'report §3.10 — total opex {rep["total_operating_expenses"]:,} '
               f'less attributable {rep["attributable_expenses"]:,}',
        note='Excluded from the IFRS 17 insurance service result; borne by the company.',
    ))

    # 2. The non-cash ceded reserve movement. IFRS 17 recognises the movement in
    #    amounts recoverable as income; the MA see only the cash.
    if ceded_reserve_movement is None:
        ceded_reserve_movement = rep['amounts_recoverable'] - rep['direct_recoveries_received']
    res.steps.append(BridgeStep(
        label='Less: non-cash movement in ceded reserves taken as IFRS 17 income',
        amount=-ceded_reserve_movement,
        source=f'amounts recoverable {rep["amounts_recoverable"]:,} less direct '
               f'recoveries received {rep["direct_recoveries_received"]:,}',
        note='IFRS 17 income that is not cash. This is the single biggest reason the '
             'two profit figures differ.',
    ))

    # 3–5. The lines the valuation does not measure at all.
    if investment_income:
        res.steps.append(BridgeStep(
            label='Add: investment income', amount=investment_income,
            source='ledger', note='Outside the IFRS 17 insurance service result.'))
    if other_income:
        res.steps.append(BridgeStep(
            label='Add: other income', amount=other_income, source='ledger'))
    if tax:
        res.steps.append(BridgeStep(
            label='Less: taxation', amount=-abs(tax), source='ledger',
            note='IFRS 17 reports before tax; the MA report PAT.'))

    res.derived_ma_pat = res.ifrs17_pbt + sum(s.amount for s in res.steps)

    # ------------------------------------------------------------------
    # Compare to the frozen register — and NEVER pick silently.
    # ------------------------------------------------------------------
    frozen = frozen_ma_pat
    if frozen is None:
        frozen = (FROZEN_MA.get(year) or {}).get('profit_after_tax')
    res.frozen_ma_pat = frozen

    if frozen is None:
        # FY2026 full year is not frozen until the audit closes. Say so; do not
        # invent a comparison.
        res.comparable = False
        return res

    res.unexplained = res.derived_ma_pat - frozen
    res.reconciles = abs(res.unexplained) <= TOLERANCE
    if not res.reconciles:
        res.conflict = {
            'metric': f'{year} profit after tax',
            'derived_from_ifrs17': res.derived_ma_pat,
            'frozen_management_accounts': frozen,
            'difference': res.unexplained,
            'registered_truth': 'frozen_management_accounts',
            'message': (
                f'The IFRS 17 walk lands at {res.derived_ma_pat:,.2f} while the frozen '
                f'Management Accounts PAT is {frozen:,.2f}. The Management Accounts '
                f'workbook is the registered truth. The difference of '
                f'{res.unexplained:,.2f} is not yet explained by a labelled step — '
                f'supply the missing investment income, other income or tax figure '
                f'rather than absorbing it.'
            ),
        }
    return res


def gwp_check(year: str) -> dict:
    """Does the valuation's gross written premium agree with the frozen register?

    FY2025 must tie EXACTLY to 125,148,692 (125.15 Mn). Quoting anything else is
    a production incident; the CFO has corrected it around a hundred times.
    """
    rep = REPORTED.get(year) or {}
    frozen = (FROZEN_MA.get(year) or {}).get('gross_written_premium')
    valuation = rep.get('gross_written_premium')
    if frozen is None or valuation is None:
        return {'comparable': False, 'year': year,
                'valuation': valuation, 'frozen': frozen}
    diff = valuation - frozen
    return {
        'comparable': True,
        'year': year,
        'valuation': valuation,
        'frozen': frozen,
        'difference': diff,
        'ties': abs(diff) <= TOLERANCE,
        'registered_truth': 'frozen_management_accounts',
    }
