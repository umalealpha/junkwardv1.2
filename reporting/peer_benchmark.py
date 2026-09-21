"""
peer_benchmark.py — Botswana short-term insurance market benchmark, FY2025.

Alpha Direct's own numbers come from the CFO's Management Accounts workbook
(Alpha_Direct_MA_-June 2026.xlsx, sheets PL and BS) for FY26, year ended
30 June 2026. They are entered here as FROZEN constants and MUST agree with
that workbook — never with an omni dashboard tile. See test_peer_benchmark.py,
which fails if any of them drifts.

Peer numbers are transcribed from the signed FY2025 Annual Financial
Statements purchased from the Registrar and supplied by Legakwa Ntabeni on
10 September 2026. Each peer records its own year-end because the market does
not share one: Hollard and Sunshine close 30 June, WestSure 28 February, and
the rest 31 December. Comparisons are therefore period-adjacent, not
period-identical, and `year_end` is surfaced to the screen for that reason.

Every insurer here reports under IFRS 17 except Alpha Direct, whose MA pack is
still presented on the pre-IFRS-17 management basis the CFO froze on
13 May 2026. The reconciliation that makes the two comparable is documented in
`_adic_comparable_basis()` below — it is the whole reason this module exists
rather than a spreadsheet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# Report currency throughout: millions of Botswana Pula.
UNITS = 'Mn BWP'


@dataclass
class Insurer:
    """One insurer, one financial year, as reported."""
    name: str
    short: str
    year_end: str
    fiscal_year: str
    # Top line. `insurance_revenue` is the IFRS 17 earned measure that every
    # peer publishes; `gross_written_premium` is only disclosed by some.
    insurance_revenue: float
    gross_written_premium: Optional[float] = None
    # Reinsurance, shown gross so the cession decision is visible.
    reinsurance_ceded: Optional[float] = None
    reinsurance_recoveries: Optional[float] = None
    reinsurance_commission: Optional[float] = None
    net_reinsurance_cost: Optional[float] = None
    # Claims and acquisition.
    claims_incurred: Optional[float] = None
    acquisition_costs: Optional[float] = None
    # Operating base.
    operating_expenses: Optional[float] = None
    staff_costs: Optional[float] = None
    insurance_service_result: Optional[float] = None
    # Below the insurance result.
    investment_and_other_income: Optional[float] = None
    profit_before_tax: Optional[float] = None
    taxation: Optional[float] = None
    profit_after_tax: Optional[float] = None
    # Balance sheet.
    total_assets: Optional[float] = None
    total_equity: Optional[float] = None
    cash: Optional[float] = None
    # Free-text caveat shown next to the row on screen.
    note: str = ''
    is_us: bool = False
    source: str = ''

    # -- derived ---------------------------------------------------------
    # Each ratio returns None rather than 0.0 when an input is missing, so a
    # gap in a peer's disclosure never renders as a real value of zero. The
    # screen prints an em dash for None.

    @property
    def net_revenue(self) -> Optional[float]:
        """Revenue actually retained after reinsurance — the denominator that
        makes a heavy ceder comparable to a light one."""
        if self.net_reinsurance_cost is None:
            return None
        return round(self.insurance_revenue - self.net_reinsurance_cost, 3)

    @property
    def underwriting_profit(self) -> Optional[float]:
        """Insurance service result less operating expenses. Deliberately
        EXCLUDES investment income and other income — the CFO's question was
        what the insurance business earns on its own."""
        if self.insurance_service_result is None or self.operating_expenses is None:
            return None
        return round(self.insurance_service_result - self.operating_expenses, 3)

    @property
    def combined_ratio(self) -> Optional[float]:
        """Cost of underwriting as a percentage of INSURANCE REVENUE. Above 100
        means the insurance book loses money before investments.

        The denominator is insurance revenue, not retained revenue. Retained
        revenue subtracts the NET reinsurance cost, which nets claim recoveries
        into the top line while the claims themselves sit gross in the
        numerator — and for a peer whose reinsurance was a net credit in the
        year (Old Mutual and Bryte both were in FY25) it produces a denominator
        LARGER than revenue and a combined ratio that moves the wrong way.
        Insurance revenue is the one denominator that is defined identically
        for all nine insurers. `combined_ratio_on_retained` keeps the other
        view for anyone who wants it.
        """
        uw = self.underwriting_profit
        if uw is None or not self.insurance_revenue:
            return None
        return round((1 - uw / self.insurance_revenue) * 100, 1)

    @property
    def combined_ratio_on_retained(self) -> Optional[float]:
        """The same measure over revenue net of the reinsurance cost. Kept for
        comparison; see the note on combined_ratio for why it is not the
        headline."""
        uw, nr = self.underwriting_profit, self.net_revenue
        if uw is None or not nr:
            return None
        return round((1 - uw / nr) * 100, 1)

    def _over(self, numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
        if numerator is None or not denominator:
            return None
        return round(numerator / denominator * 100, 1)

    @property
    def cession_ratio(self) -> Optional[float]:
        return self._over(self.reinsurance_ceded, self.insurance_revenue)

    @property
    def staff_to_revenue(self) -> Optional[float]:
        return self._over(self.staff_costs, self.insurance_revenue)

    @property
    def staff_to_net_revenue(self) -> Optional[float]:
        return self._over(self.staff_costs, self.net_revenue)

    @property
    def expense_to_revenue(self) -> Optional[float]:
        return self._over(self.operating_expenses, self.insurance_revenue)

    @property
    def expense_to_net_revenue(self) -> Optional[float]:
        return self._over(self.operating_expenses, self.net_revenue)

    @property
    def gross_loss_ratio(self) -> Optional[float]:
        return self._over(self.claims_incurred, self.insurance_revenue)

    @property
    def acquisition_ratio(self) -> Optional[float]:
        return self._over(self.acquisition_costs, self.insurance_revenue)

    @property
    def insurance_service_margin(self) -> Optional[float]:
        return self._over(self.insurance_service_result, self.insurance_revenue)

    @property
    def pat_margin(self) -> Optional[float]:
        return self._over(self.profit_after_tax, self.insurance_revenue)

    @property
    def return_on_equity(self) -> Optional[float]:
        # Negative equity makes ROE meaningless, not just negative — WestSure
        # would otherwise print a large positive number off a negative base.
        if self.total_equity is None or self.total_equity <= 0:
            return None
        return self._over(self.profit_after_tax, self.total_equity)

    @property
    def return_on_assets(self) -> Optional[float]:
        return self._over(self.profit_after_tax, self.total_assets)

    @property
    def effective_tax_rate(self) -> Optional[float]:
        if self.taxation is None or not self.profit_before_tax or self.profit_before_tax <= 0:
            return None
        return self._over(self.taxation, self.profit_before_tax)

    @property
    def reinsurance_commission_rate(self) -> Optional[float]:
        """Ceding commission earned as a percentage of premium handed over."""
        return self._over(self.reinsurance_commission, self.reinsurance_ceded)

    @property
    def underwriting_share_of_profit(self) -> Optional[float]:
        """How much of the bottom line the insurance book actually produced.
        A low number means the profit came from investments, not underwriting."""
        uw = self.underwriting_profit
        if uw is None or not self.profit_after_tax or self.profit_after_tax <= 0:
            return None
        return round(uw / self.profit_after_tax * 100, 1)

    @property
    def solvency_ratio(self) -> Optional[float]:
        return self._over(self.total_equity, self.total_assets)

    @property
    def operating_leverage(self) -> Optional[float]:
        if not self.total_equity or self.total_equity <= 0:
            return None
        return round(self.insurance_revenue / self.total_equity, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            net_revenue=self.net_revenue,
            underwriting_profit=self.underwriting_profit,
            combined_ratio=self.combined_ratio,
            combined_ratio_on_retained=self.combined_ratio_on_retained,
            cession_ratio=self.cession_ratio,
            staff_to_revenue=self.staff_to_revenue,
            staff_to_net_revenue=self.staff_to_net_revenue,
            expense_to_revenue=self.expense_to_revenue,
            expense_to_net_revenue=self.expense_to_net_revenue,
            gross_loss_ratio=self.gross_loss_ratio,
            acquisition_ratio=self.acquisition_ratio,
            insurance_service_margin=self.insurance_service_margin,
            pat_margin=self.pat_margin,
            return_on_equity=self.return_on_equity,
            return_on_assets=self.return_on_assets,
            effective_tax_rate=self.effective_tax_rate,
            reinsurance_commission_rate=self.reinsurance_commission_rate,
            underwriting_share_of_profit=self.underwriting_share_of_profit,
            solvency_ratio=self.solvency_ratio,
            operating_leverage=self.operating_leverage,
        )
        return d


# ---------------------------------------------------------------------------
# Alpha Direct — FY26, year ended 30 June 2026
# ---------------------------------------------------------------------------
# Source: Alpha_Direct_MA_-June 2026.xlsx, sheet 'PL' (column B, "June 2026 -
# Actual") and sheet 'BS' (column FY26). FROZEN — do not edit without the CFO.

ADIC_MA = {
    'gross_written_premium':      133.378,
    'premiums_ceded':              80.774,
    'change_in_upr':                0.441,
    'net_earned_premium':          53.045,
    'gross_claims':                76.413,
    'claims_recovered':            51.726,
    'subrogations_salvages':        2.924,
    'net_claims_incurred':         21.763,
    'insurance_in_a_box':           5.736,
    'bonu_acquisition':             0.430,
    'commission_from_reinsurers':  21.683,
    'commissions_paid':             7.617,
    'broker_entertainment':         0.245,
    'health_insurance':             0.181,
    'net_acquisition_income':       7.474,
    'gross_profit':                38.755,
    'other_income':                 2.245,
    'employee_costs':              11.959,
    'bonus_pay':                    1.216,
    'total_operating_expenses':    31.381,
    'total_provisions':             5.484,
    'ebitda':                       4.135,
    'depreciation':                 1.845,
    'ebit':                         2.290,
    'finance_cost':                -0.300,   # net income in FY26
    'profit_before_tax':            2.589,
    'taxation':                     0.000,
    'profit_after_tax':             2.589,
    'total_assets':                54.523,
    'total_equity':                17.249,
    'cash':                         9.440,
}

# Prior-year and 9-month comparatives, for the trend strip on the dashboard.
# FY25 from MA-June2025-ADIC-FY25.xlsx; FY26-9M from MA-Mar2026-ADIC-9M.xlsx.
# The two expense bases, stated once so nothing has to re-derive them.
RUNNING_COST_BASE = round(31.381 + 1.845, 3)              # opex + depreciation
UNDERWRITING_COST_BASE = round(31.381 + 5.484 + 1.845, 3)  # + provisions

ADIC_HISTORY = [
    {'period': 'FY25 (12M to Jun-25)', 'gwp': 125.149, 'nep': 52.484,
     'ceded': 74.024, 'net_claims': 15.711, 'opex': 29.523, 'pat': 0.292},
    {'period': 'FY26 9M (to Mar-26)',  'gwp':  96.177, 'nep': 42.008,
     'ceded': 58.998, 'net_claims': 17.073, 'opex': 25.440, 'pat': 0.950},
    {'period': 'FY26 (12M to Jun-26)', 'gwp': 133.378, 'nep': 53.045,
     'ceded': 80.774, 'net_claims': 21.763, 'opex': 31.381, 'pat': 2.589},
]


def _adic_comparable_basis() -> Insurer:
    """Restate the MA pack onto the IFRS 17 shape the peers publish.

    Three moves, and each one matters:

    1. `insurance_revenue`. Peers publish IFRS 17 insurance revenue, which is
       premium EARNED. The MA's closest equivalent is GWP adjusted for the UPR
       movement, so that is what is used — not GWP itself, which would flatter
       Alpha Direct against every peer in the table.

    2. `insurance_service_result`. Peers strike this after claims, acquisition
       costs AND the expenses directly attributable to servicing contracts.
       The MA reaches the same place through 'Gross Profit', which is already
       net earned premium less net claims plus net acquisition income.
       Provisions and depreciation sit below that line in the MA but inside
       the insurance service result for most peers, so both are pushed into
       operating expenses here — the conservative direction, and the one that
       keeps `underwriting_profit` honest.

    3. `net_reinsurance_cost`. Shown net of the ceding commission Alpha Direct
       earns back, which is how the peers present it. Gross cession is kept
       separately in `reinsurance_ceded` because the cession rate is the point
       of the whole exercise.
    """
    m = ADIC_MA
    insurance_revenue = m['gross_written_premium'] + m['change_in_upr']
    net_reinsurance_cost = (
        m['premiums_ceded']
        - m['claims_recovered']
        - m['subrogations_salvages']
        - m['commission_from_reinsurers']
    )
    # Operating expenses on the peer basis: the MA's own opex, plus the two
    # items the MA reports below gross profit but peers absorb above it.
    #
    # NOTE ON PROVISIONS. This figure INCLUDES the P5.484m of provisions, and it
    # must, because they land on the underwriting result and therefore belong in
    # the combined ratio. It is deliberately NOT the same as the "running cost
    # base" in peer_benchmark_detail.py, which excludes provisions so that the
    # cost comparison against peers is like-for-like (peers rarely disclose an
    # expected-credit-loss charge at all). Two different questions, two
    # different numerators — see RUNNING_COST_BASE below, and never mix them.
    operating_expenses = (
        m['total_operating_expenses'] + m['total_provisions'] + m['depreciation']
    )
    # Insurance service result before those operating expenses.
    insurance_service_result = m['gross_profit']
    acquisition_costs = (
        m['commissions_paid'] + m['insurance_in_a_box'] + m['bonu_acquisition']
        + m['broker_entertainment'] + m['health_insurance']
    )
    return Insurer(
        name='Alpha Direct Insurance Company',
        short='Alpha Direct',
        year_end='30 Jun 2026',
        fiscal_year='FY26',
        insurance_revenue=round(insurance_revenue, 3),
        gross_written_premium=m['gross_written_premium'],
        reinsurance_ceded=m['premiums_ceded'],
        reinsurance_recoveries=round(m['claims_recovered'] + m['subrogations_salvages'], 3),
        reinsurance_commission=m['commission_from_reinsurers'],
        net_reinsurance_cost=round(net_reinsurance_cost, 3),
        claims_incurred=m['gross_claims'],
        acquisition_costs=round(acquisition_costs, 3),
        operating_expenses=round(operating_expenses, 3),
        staff_costs=round(m['employee_costs'] + m['bonus_pay'], 3),
        insurance_service_result=insurance_service_result,
        investment_and_other_income=round(m['other_income'] - m['finance_cost'], 3),
        profit_before_tax=m['profit_before_tax'],
        taxation=m['taxation'],
        profit_after_tax=m['profit_after_tax'],
        total_assets=m['total_assets'],
        total_equity=m['total_equity'],
        cash=m['cash'],
        is_us=True,
        note='MA pack restated onto the IFRS 17 shape the peers publish. '
             'Provisions and depreciation moved into operating expenses.',
        source='Alpha_Direct_MA_-June 2026.xlsx, sheets PL and BS',
    )


# ---------------------------------------------------------------------------
# Peers — FY2025 signed Annual Financial Statements
# ---------------------------------------------------------------------------
# Transcribed 10 September 2026 from the statements supplied by Legakwa
# Ntabeni. Line references are recorded in the handover pack. Where a peer
# does not disclose a figure the field is left None on purpose: a missing
# disclosure is not a zero, and the screen must show it as unknown.

PEERS: list[Insurer] = [
    Insurer(
        name='Botswana Insurance Company Limited', short='BIC',
        year_end='31 Dec 2025', fiscal_year='FY25',
        insurance_revenue=718.048,
        reinsurance_ceded=365.034, reinsurance_recoveries=138.637,
        net_reinsurance_cost=159.885,
        claims_incurred=308.188, acquisition_costs=106.813,
        operating_expenses=41.963, staff_costs=56.492,
        insurance_service_result=83.147,
        investment_and_other_income=21.792,
        profit_before_tax=64.612, taxation=14.664, profit_after_tax=49.947,
        total_assets=634.880, total_equity=154.762, cash=194.607,
        note='Company standalone. Group insurance revenue 794.4. '
             'Prescribed Capital Target cover 1.53x.',
        source='BIC Group AFS 2025, company column',
    ),
    Insurer(
        name='The Hollard Insurance Company of Botswana', short='Hollard',
        year_end='30 Jun 2025', fiscal_year='FY25',
        insurance_revenue=444.463,
        reinsurance_ceded=106.411, reinsurance_recoveries=48.317,
        net_reinsurance_cost=58.094,
        claims_incurred=269.235, acquisition_costs=68.916,
        operating_expenses=21.782, staff_costs=49.889,
        insurance_service_result=48.218,
        investment_and_other_income=22.595,
        profit_before_tax=44.895, taxation=10.513, profit_after_tax=34.382,
        total_equity=149.887,
        note='Same 30 June year-end as Alpha Direct — the cleanest '
             'like-for-like in the market. Retains most of its motor book.',
        source='Hollard Insurance Company of Botswana AFS 2025',
    ),
    Insurer(
        name='Old Mutual Short-Term Insurance (Botswana)', short='Old Mutual',
        year_end='31 Dec 2025', fiscal_year='FY25',
        insurance_revenue=304.906, gross_written_premium=284.737,
        reinsurance_ceded=140.640, reinsurance_recoveries=69.587,
        net_reinsurance_cost=-4.948,
        claims_incurred=221.972, acquisition_costs=71.934,
        operating_expenses=2.756, staff_costs=16.683,
        insurance_service_result=11.445,
        investment_and_other_income=13.524,
        profit_before_tax=22.213, taxation=2.670, profit_after_tax=19.543,
        total_assets=286.313, total_equity=138.308, cash=148.587,
        note="Reported in P'000. Operating expenses look tiny because staff "
             '(16.7) and a group management fee (33.9) sit inside insurance '
             'service expenses, not below the line. Net reinsurance was a '
             'credit in FY25.',
        source='Old Mutual Short-Term Insurance (Botswana) AFS 2025',
    ),
    Insurer(
        name='Phoenix of Botswana Assurance Company', short='Phoenix',
        year_end='31 Dec 2025', fiscal_year='FY25',
        insurance_revenue=125.555, gross_written_premium=133.063,
        reinsurance_ceded=87.977, reinsurance_recoveries=41.741,
        net_reinsurance_cost=46.236,
        claims_incurred=39.726, acquisition_costs=10.329,
        operating_expenses=20.616, staff_costs=8.062,
        insurance_service_result=29.265,
        investment_and_other_income=4.510,
        profit_before_tax=9.556, taxation=1.113, profit_after_tax=8.443,
        total_assets=101.338, total_equity=32.399, cash=52.726,
        note='THE CLOSEST COMPARATOR. Gross written premium 133.1 against '
             'Alpha Direct 133.4 — the same size company, in the same market, '
             'in the same year.',
        source='PHOENIX BOTSWANA AFS 2025',
    ),
    Insurer(
        name='BICB Limited (Bryte Risk Services Botswana)', short='Bryte',
        year_end='31 Dec 2025', fiscal_year='FY25',
        insurance_revenue=165.637,
        reinsurance_ceded=50.618, reinsurance_recoveries=72.223,
        net_reinsurance_cost=-21.605,
        claims_incurred=124.366, acquisition_costs=35.317,
        operating_expenses=25.098, staff_costs=18.171,
        insurance_service_result=27.603,
        investment_and_other_income=17.128,
        profit_before_tax=18.648, taxation=-2.297, profit_after_tax=20.945,
        total_assets=335.374, total_equity=119.752, cash=65.691,
        note="Reported in P'000, scanned source. Profit exceeds pre-tax "
             'profit because FY25 carried a tax credit. Prescribed Capital '
             'Target requirement P47.1m.',
        source='BICB Limited (Bryte Risk Services Botswana) AFS 2025',
    ),
    Insurer(
        name='BIHL Insurance Company (Insure Guard)', short='Insure Guard',
        year_end='31 Dec 2025', fiscal_year='FY25',
        insurance_revenue=76.350,
        reinsurance_ceded=8.021, reinsurance_recoveries=2.601,
        reinsurance_commission=2.056, net_reinsurance_cost=3.364,
        claims_incurred=18.110, acquisition_costs=18.593,
        operating_expenses=14.660, staff_costs=14.946,
        insurance_service_result=28.217,
        investment_and_other_income=2.709,
        profit_before_tax=15.688, taxation=4.665, profit_after_tax=11.023,
        total_assets=125.603, total_equity=79.508, cash=50.977,
        note='Cedes almost nothing — 10.5% — and keeps the margin. '
             'Capital cover 2.67x, the strongest in the set. '
             'Net written premium 72.7.',
        source='BIHL Insurance Company (Insure Guard) AFS 2025',
    ),
    Insurer(
        name='WestSure Insurance Botswana', short='WestSure',
        year_end='28 Feb 2025', fiscal_year='FY25',
        insurance_revenue=75.663,
        reinsurance_ceded=52.580, reinsurance_recoveries=22.363,
        reinsurance_commission=15.506, net_reinsurance_cost=19.426,
        claims_incurred=50.611,
        operating_expenses=27.438, staff_costs=7.352,
        insurance_service_result=3.117,
        investment_and_other_income=5.967,
        profit_before_tax=-2.848, taxation=-0.504, profit_after_tax=-2.344,
        total_assets=87.623, total_equity=-0.670, cash=14.162,
        note='DISTRESSED. Liabilities exceed assets by P0.67m, capital cover '
             '0.88x against a regulatory minimum of 1.0, and the auditors '
             'flagged a material going-concern uncertainty. Kept in the set '
             'as the warning case, not as a target.',
        source='WestSure AFS 2025',
    ),
    Insurer(
        name='Sunshine Insurance Company of Botswana', short='Sunshine',
        year_end='30 Jun 2025', fiscal_year='FY25',
        insurance_revenue=26.191,
        reinsurance_ceded=7.201, net_reinsurance_cost=5.274,
        claims_incurred=2.945, acquisition_costs=2.826,
        operating_expenses=3.147,
        insurance_service_result=1.602,
        investment_and_other_income=2.127,
        profit_before_tax=0.582, taxation=0.103, profit_after_tax=0.479,
        total_assets=73.749, total_equity=26.232, cash=46.011,
        note='Shrinking hard — insurance revenue fell from 42.2 to 26.2, down '
             '38% in one year. Scanned source. Discloses no staff-cost note.',
        source='Sunshine AFS 2025',
    ),
]


def all_insurers() -> list[Insurer]:
    """Alpha Direct first, then peers largest to smallest."""
    return [_adic_comparable_basis()] + sorted(
        PEERS, key=lambda i: i.insurance_revenue, reverse=True
    )


def market_stats() -> dict:
    """Peer median for each ratio, so the dashboard can say where we sit.

    Median rather than mean: BIC is four times the size of the next insurer
    and WestSure is in distress, and either one drags an average somewhere
    unhelpful. WestSure is excluded from the ratio medians entirely — a
    company failing its capital test is not a benchmark — but it stays in the
    table on screen because the CFO should see it.
    """
    healthy = [p for p in PEERS if p.short != 'WestSure']
    ratios = [
        'cession_ratio', 'gross_loss_ratio', 'staff_to_revenue',
        'staff_to_net_revenue', 'expense_to_revenue', 'expense_to_net_revenue',
        'acquisition_ratio', 'insurance_service_margin', 'combined_ratio',
        'pat_margin', 'return_on_equity', 'return_on_assets',
        'effective_tax_rate', 'underwriting_share_of_profit',
        'solvency_ratio', 'operating_leverage',
    ]
    out = {}
    for r in ratios:
        vals = sorted(v for v in (getattr(p, r) for p in healthy) if v is not None)
        if not vals:
            out[r] = None
            continue
        mid = len(vals) // 2
        out[r] = round(
            vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 1
        )
    return out


def build_benchmark() -> dict:
    """Everything the dashboard needs, in one payload."""
    insurers = all_insurers()
    us = insurers[0]
    median = market_stats()
    return {
        'units': UNITS,
        'as_at': '2026-09-10',
        'us': us.to_dict(),
        'insurers': [i.to_dict() for i in insurers],
        'peer_median': median,
        'history': ADIC_HISTORY,
        'peer_count': len(PEERS),
        'running_cost_base': RUNNING_COST_BASE,
        'underwriting_cost_base': UNDERWRITING_COST_BASE,
        'cost_base_note': (
            'Two expense bases are in use and they are not interchangeable. The '
            'running cost base of P33.226m (operating expenses plus depreciation) '
            'is what section 8 compares against peers, because peers rarely '
            'disclose a credit-loss charge. The underwriting cost base of '
            'P38.710m adds the P5.484m of provisions and is what the combined '
            'ratio uses, because provisions land on the underwriting result.'),
        'sources': 'Signed FY2025 Annual Financial Statements (8 insurers), '
                   'purchased from the Registrar September 2026. Alpha Direct '
                   'from the CFO Management Accounts workbook, June 2026.',
    }
