"""
ifrs17/constants.py — the FY2026 valuation, as Empirica signed it.

SOURCE: "Alpha Direct Insurance Company — IFRS 17 Valuation Report", Empirica
Actuaries, valuation date 30 June 2026, report date 19 August 2026.
Local copy: ~/Desktop/IFRS 17 workings/FY26/Alpha Direct IFRS17 Report 2026.pdf

Implemented AS-IS. No figure in this file deviates from the report. Where the
report itself carries an unresolved difference, the difference is recorded here
rather than absorbed — see KNOWN_VARIANCES. Do NOT silently "fix" any of them:
each one is disclosed in the report and the auditors have been told about it.

Same discipline as frontend/src/lib/fiveYearModel.ts, for the same reason: a
figure nobody can trace back to a source is a figure nobody can defend.
"""
from __future__ import annotations

from decimal import Decimal as D

# ---------------------------------------------------------------------------
# Measurement elections (report §3.1, Table 5)
# ---------------------------------------------------------------------------
MEASUREMENT_MODEL = 'paa'          # Premium Allocation Approach, all groups
CONFIDENCE_LEVEL = 75              # percentile the risk adjustment is calibrated to
DISCOUNTING_APPLIED = False        # paras 56 / 59(b) expedients — short-tailed book
ACQUISITION_CASH_FLOWS_EXPENSED = False   # amortised over the coverage period

# ---------------------------------------------------------------------------
# The levers. Every one of these is a real, sourced assumption — and every one
# is a slider on the cockpit, because these are the numbers that move the result.
# ---------------------------------------------------------------------------
BASE_LEVERS: dict[str, object] = {
    # §3.8 — 6% of fulfilment cash flows, 75th percentile, gross AND ceded.
    'ra_pct': D('0.06'),
    'confidence_level': CONFIDENCE_LEVEL,
    # §3.7 — applied to (best-estimate gross IBNR + half of gross case reserves).
    # Carried forward UNCHANGED from FY25 because no approved FY26 claims-handling
    # allocation was provided, so the whole FY26 increase is the higher reserve
    # base, not a change in the assumption. That makes it the most likely to move.
    'che_factor_pct': D('0.024779704082'),
    # §3.11 — management-approved expected-recovery estimate, set equal to FY2025
    # actual recovery income (salvage 1,273,667 + subrogation 751,677), expressed
    # against gross case reserves plus gross IBNR.
    'salvage_subro_pct': D('0.062015'),
    # §3.10 — attributable share of total operating expenses. FY25 was 80.5%.
    # The report is explicit that the APPORTIONMENT change, not cost escalation,
    # drives the 22.7% rise: 3,891,418 of the 5,398,775 increase.
    'attributable_share_pct': D('0.929'),
    # §3.6, Table 7 — the year6→7 age-to-age factor. FY25 applied a NIL tail
    # (exactly 1.000000). This single change is worth 896,862 of gross IBNR and
    # the report calls it "the clearest example of a method change".
    'ibnr_tail_factor': D('1.003100'),
    'discounting': DISCOUNTING_APPLIED,
    # §6.3 — JBB Motor quota share commission. Booked at the 25% PROVISIONAL rate;
    # the contractual sliding scale is 24%–41% on final underwriting-year loss
    # ratio. At 41% on ceded premium of 54,450,560 that is +8,712,000 of
    # commission. The report states plainly: "That is a sensitivity, not a
    # bookable adjustment, and it has not been recognised."
    'jbb_commission_pct': D('0.25'),
    # §3.9 — no loss component carried at either date, justified on materiality
    # (Engineering + Guarantee premium is 0.8% of the book). Empirica recommends
    # a formal onerousness test on both before the FY2027 valuation.
    'onerous_test': False,
    # §5.4 — Health is NOT one of the eight reporting segments and is not modelled
    # as a distinct group. Loss-making at (108,337) on 133,609 of premium, and it
    # carries 70,353 of ceded premium that is not separately modelled because the
    # treaty documentation contains no identifiable Health treaty.
    'health_modelled': False,
}

# JBB sliding scale, for the sensitivity callout (§6.3).
JBB_PROVISIONAL_RATE = D('0.25')
JBB_SCALE_MIN = D('0.24')
JBB_SCALE_MAX = D('0.41')
JBB_CEDED_PREMIUM_FY26 = D('54450560')

# ---------------------------------------------------------------------------
# Reported results — the parity target. If the engine cannot reproduce these to
# the cent, the engine is wrong, not the report.
# ---------------------------------------------------------------------------
REPORTED = {
    'FY2026': {
        # §1.2 Table 1 + §4.1 Table 14
        'insurance_revenue':                 D('133416295'),
        'insurance_service_expenses':        D('-113980546'),
        'service_result_before_reinsurance': D('19435749'),
        'allocation_of_reinsurance_premiums': D('-58581854'),
        'amounts_recoverable':               D('52010349'),
        'net_reinsurance_result':             D('-6571505'),
        'insurance_service_result':           D('12864243'),
        'profit_before_tax':                  D('12864243'),
        # §4.4 Table 17 / Table 18
        'insurance_contract_liabilities':     D('49927070'),
        'reinsurance_contract_assets':        D('35443271'),
        'net_ifrs17_liability':               D('14483799'),
        'lrc':                                D('16748968'),
        'lic_best_estimate':                  D('31300096'),
        'lic_risk_adjustment':                D('1878006'),
        'arc':                                D('10782688'),
        'aric_best_estimate':                 D('23264702'),
        'aric_risk_adjustment':               D('1395882'),
        # §5
        'gross_case_reserves':                D('11503407'),
        'gross_ibnr':                         D('21155286'),
        'net_ibnr':                           D('6672465'),
        'che_reserve':                        D('666747'),
        'salvage_subrogation_deduction':      D('-2025344'),
        # §2 / §7
        'gross_written_premium':              D('133416295'),
        'claims_incurred':                    D('68679937'),
        'acquisition_commission_paid':        D('12075395'),
        'reinsurance_commission_received':    D('21682691'),
        'attributable_expenses':              D('29168270'),
        'total_operating_expenses':           D('31381207'),
        'non_attributable_expenses':          D('2212937'),
        'ceded_premium':                      D('80264545'),
        'direct_recoveries_received':         D('48236373'),
        'gross_unearned_premium':             D('16748968'),
        'ceded_unearned_premium':             D('9355130'),
        'net_unearned_premium':               D('7393838'),
        'profit_commission_receivable':       D('1427557.30'),
        'loss_ratio':                         D('0.51'),
        'combined_ratio':                     D('0.82'),
    },
    'FY2025': {
        # Comparatives, drawn from the FY2025 PAA model so both dates sit on a
        # consistent basis (§4 preamble).
        'insurance_revenue':                 D('125202058'),
        'insurance_service_expenses':        D('-130047716'),
        'service_result_before_reinsurance': D('-4845658'),
        'allocation_of_reinsurance_premiums': D('-30565948'),
        'amounts_recoverable':               D('62250312'),
        'net_reinsurance_result':             D('31684364'),
        'insurance_service_result':           D('26838707'),
        'profit_before_tax':                  D('26838707'),
        'insurance_contract_liabilities':     D('47850064.78'),
        'reinsurance_contract_assets':        D('32136927.70'),
        'net_ifrs17_liability':               D('15713137.08'),
        'lrc':                                D('18728899.18'),
        'lic_best_estimate':                  D('27472797.73'),
        'lic_risk_adjustment':                D('1648367.86'),
        'gross_case_reserves':                D('10439514.29'),
        'gross_ibnr':                         D('16495193.39'),
        'net_ibnr':                           D('3195219'),
        'che_reserve':                        D('538090.05'),
        'salvage_subrogation_deduction':      D('0'),
        # 🔴 Ties EXACTLY to the frozen ADIC FY25 GWP of 125.15 Mn.
        'gross_written_premium':              D('125148692'),
        'claims_incurred':                    D('62969792'),
        'acquisition_commission_paid':        D('14550782'),
        'reinsurance_commission_received':    D('26555139'),
        'attributable_expenses':              D('23769495'),
        'total_operating_expenses':           D('29509824'),
        'non_attributable_expenses':          D('5740329'),
        'ceded_premium':                      D('57121087'),
        'direct_recoveries_received':         D('41754864'),
        'gross_unearned_premium':             D('18728899'),
        'ceded_unearned_premium':             D('11399424'),
        'net_unearned_premium':               D('7329476'),
        'profit_commission_receivable':       D('0'),
        'loss_ratio':                         D('0.50'),
    },
}

# ---------------------------------------------------------------------------
# Segments (§2.5 Table 4, §4.3 Table 16, §7.4 Table 42)
# ---------------------------------------------------------------------------
SEGMENTS = ('accident', 'engineering', 'guarantee', 'liability',
            'miscellaneous', 'motor', 'property', 'transportation')

# Health is deliberately NOT in SEGMENTS — it is the ninth, off by default.
HEALTH_SEGMENT = 'health'

SEGMENT_FY26 = {
    # segment:        (premium,   commission, claims,    unearned_premium)
    'accident':       (D('14691203'), D('970320'),   D('3376824'),  D('1857867')),
    'engineering':    (D('552531'),   D('105974'),   D('1628280'),  D('116531')),
    'guarantee':      (D('524045'),   D('76841'),    D('755803'),   D('204386')),
    'liability':      (D('17008699'), D('362755'),   D('453666'),   D('731864')),
    'miscellaneous':  (D('6614097'),  D('946738'),   D('1590783'),  D('1200629')),
    'motor':          (D('68414860'), D('5219331'),  D('49579541'), D('8536414')),
    'property':       (D('21373350'), D('3674077'),  D('10121611'), D('3388405')),
    'transportation': (D('4237511'),  D('719358'),   D('1173430'),  D('712873')),
}

# §2.5 Table 3 — Six-year premium and claims performance. GWP, claims incurred
# and the resulting loss ratio, FY2021 → FY2026. The spine of every trend chart.
SIX_YEAR = {
    #  fy:   (gross_written_premium, claims_incurred,  loss_ratio)
    2021: (D('54940782'),  D('36890971'), D('0.67')),
    2022: (D('66774528'),  D('32078400'), D('0.48')),
    2023: (D('74486393'),  D('43167884'), D('0.58')),
    2024: (D('100878280'), D('49285015'), D('0.49')),
    2025: (D('125148692'), D('62969792'), D('0.50')),
    2026: (D('133416295'), D('68679937'), D('0.51')),
}

# §2.5 Table 4 — Gross written premium by segment, FY2022 → FY2026. Lets the
# cockpit show which segments grew, which contracted, and each one's CAGR.
SEGMENT_GWP_HISTORY = {
    #  segment:        (fy22,        fy23,        fy24,        fy25,        fy26)
    'accident':       (D('6107697'),  D('7034848'),  D('10350653'), D('13070668'), D('14691202')),
    'engineering':    (D('1392014'),  D('1964957'),  D('2816557'),  D('1620766'),  D('552530')),
    'guarantee':      (D('713090'),   D('818969'),   D('808792'),   D('1071832'),  D('524044')),
    'liability':      (D('2024807'),  D('2687070'),  D('4393384'),  D('8990923'),  D('17008698')),
    'miscellaneous':  (D('4283007'),  D('4820162'),  D('5729483'),  D('4407957'),  D('6614097')),
    'motor':          (D('36170508'), D('38635150'), D('55248143'), D('69678697'), D('68414859')),
    'property':       (D('13262197'), D('15379014'), D('17493241'), D('21799301'), D('21373350')),
    'transportation': (D('2821205'),  D('3146221'),  D('4038023'),  D('4508542'),  D('4237510')),
}
SEGMENT_HISTORY_YEARS = (2022, 2023, 2024, 2025, 2026)

# §5.4 Table 24 — shown for transparency, excluded from the eight segments.
HEALTH_FY26 = {
    'gross_written_premium': D('133609'),
    'claims_incurred':       D('63763'),
    'attributable_expenses': D('178183'),
    'underwriting_result':   D('-108337'),
    'ceded_premium':         D('70353'),
    'ceded_recoveries':      D('56603'),
}

# ---------------------------------------------------------------------------
# IBNR by underwriting year (§5.2 Table 21) and the development factors (Table 7)
# ---------------------------------------------------------------------------
IBNR_BY_UWY_FY26 = {
    # uwy: (observed_cumulative, selected_ultimate, outstanding, ibnr)
    2020: (D('39112317'), D('39112317'), D('0'),        D('0')),
    2021: (D('30609415'), D('30704319'), D('94903'),    D('94903')),
    2022: (D('32728441'), D('32942770'), D('214329'),   D('90414')),
    2023: (D('39229598'), D('39671739'), D('442142'),   D('440631')),
    2024: (D('47536194'), D('48632515'), D('1096320'),  D('965071')),
    2025: (D('58987145'), D('61817299'), D('2830154'),  D('2052836')),
    2026: (D('49150954'), D('76395012'), D('27244057'), D('17511400')),
}

DEV_FACTORS = {
    # basis: (yr1_2, yr2_3, yr3_4, yr4_5, yr5_6, yr6_7)
    'gross_fy26_selected': (D('1.483134'), D('1.024355'), D('1.011661'),
                            D('1.004691'), D('1.003438'), D('1.003100')),
    'gross_fy25_signed':   (D('1.511578'), D('1.029700'), D('1.010501'),
                            D('1.006977'), D('1.004532'), D('1.000000')),
    'net_fy26_selected':   (D('1.400442'), D('1.026084'), D('1.006900'),
                            D('1.003597'), D('1.003040'), D('1.003015')),
    'net_fy25_signed':     (D('1.414465'), D('1.031123'), D('1.008760'),
                            D('1.005247'), D('1.005545'), D('1.000000')),
}

# The FY25→FY26 tail change, quantified by the report itself (§3.6).
TAIL_CHANGE_IBNR_EFFECT = D('896862')

# ---------------------------------------------------------------------------
# Reinsurance programme (§6.1 Table 25, §6.2 Table 26, §6.4 Table 29)
# ---------------------------------------------------------------------------
TREATIES_FY26 = {
    # treaty:            (ceded_premium, commission,   direct_recovery)
    'facultative':       (D('9198540'),  D('2485725'), D('2216404')),
    'fire_eng_surplus':  (D('5959762'),  D('1787929'), D('1190408')),
    'fmre_motor_qs':     (D('0'),        D('0'),       D('0')),
    'general_qs':        (D('6768113'),  D('2368840'), D('3528461')),
    'hcv':               (D('0'),        D('0'),       D('272854')),
    'jbb_motor_qs':      (D('54450560'), D('15040197'), D('39736998')),
    'xl_and_cat':        (D('3887570'),  D('0'),       D('1291248')),
}

CEDED_UNEARNED_BY_TREATY_FY26 = {
    'jbb_motor_qs': D('6829000'),
    'general_qs':   D('1323000'),
    'surplus':      D('705000'),
    'unallocated':  D('499000'),
}

# §6.5 Table 30 — WHICH reinsurance balances are treaty-level FACTS and which are
# a modelling allocation. The screen must show this distinction, never hide it.
CEDED_EVIDENCE_BASIS = {
    'ceded_premium':          'treaty_level',
    'commission':             'treaty_level',
    'direct_recoveries':      'treaty_level',
    'ceded_unearned_premium': 'treaty_level_partial',   # 499,000 unallocated
    'ceded_case_reserves':    'aggregate_only',
    'ceded_ibnr':             'aggregate_only',
    'ceded_risk_adjustment':  'aggregate_only',
    'profit_commission':      'aggregate_only',
}

# §6.9 Table 36 — reinsurer panel. `confirmed=False` means the signed share is
# still pending evidence.
REINSURER_PANEL_FY26 = [
    # (name,                        rating,  share,        pd,             confirmed)
    ('Munich Re of Africa',         'A+/AA-', D('0.400'), D('0.0005'), True),
    ('GIC Reinsurance (SA)',        'zaAAA',  D('0.225'), D('0.0001'), True),
    ('Grand Reinsurance Company',   'AA+',    None,       D('0.0003'), False),
    ('Continental Reinsurance',     'B+',     None,       D('0.0244'), False),
    ('P&C Reinsurance Company',     'BBB+',   D('0.075'), D('0.0016'), True),
    ('Kuwait Reinsurance',          'A+',     None,       D('0.0006'), False),
]
CONFIRMED_PANEL_SHARE_FY26 = D('0.70')     # was 0.90 in FY2025

# ---------------------------------------------------------------------------
# 🔴 KNOWN VARIANCES — disclosed in the report. Do NOT silently absorb any of
# these into a result. Each is a row in the data-quality register.
# ---------------------------------------------------------------------------
KNOWN_VARIANCES = [
    {
        'ref': 'DQ-01',
        'title': 'JBB Motor QS commission booked at the provisional rate',
        'amount': D('8712000'),
        'severity': 'high',
        'detail': 'Commission is booked at the 25% provisional rate. The contractual '
                  'sliding scale is 24%-41% on final underwriting-year loss ratio. At '
                  '41% on ceded premium of 54,450,560 the commission would be 8,712,000 '
                  'higher. The report states this is a sensitivity, not a bookable '
                  'adjustment, and it has NOT been recognised.',
        'source': 'report §6.3',
    },
    {
        'ref': 'DQ-02',
        'title': 'Only 70% of the reinsurer panel shares are confirmed',
        'amount': D('60000'),
        'severity': 'high',
        'detail': 'FY2026 identified share is 70%, down from 90% in FY2025. Grand Re, '
                  'Continental Re and Kuwait Re shares are pending signed evidence. '
                  'Continental Re is rated B+ with a 2.44% one-year default probability. '
                  'No expected credit loss has been computed; on FY2025 shares it would '
                  'have been of the order of 60,000 against a 24.66m recoverable.',
        'source': 'report §6.9',
    },
    {
        'ref': 'DQ-03',
        'title': 'Health is not modelled and has no treaty slip',
        'amount': D('70353'),
        'severity': 'medium',
        'detail': 'Health is not one of the eight reporting segments and is not modelled '
                  'as a distinct group. It loses 108,337 and carries 70,353 of ceded '
                  'premium that is not separately modelled, because the treaty '
                  'documentation supplied contains no identifiable Health treaty. '
                  'Empirica recommends separate modelling and a Health treaty slip '
                  'before the FY2027 valuation.',
        'source': 'report §5.4',
    },
    {
        'ref': 'DQ-04',
        'title': 'Engineering 295% and Guarantee 144% loss ratios with no loss component',
        'amount': D('1076575'),
        'severity': 'medium',
        'detail': 'No loss component is carried at either date. Both segments are the '
                  'most likely to give rise to one; their combined premium of 1,076,575 '
                  'is 0.8% of the book, so no loss component would be material. Empirica '
                  'recommends a formal onerousness test on both before FY2027.',
        'source': 'report §3.9',
    },
    {
        'ref': 'DQ-05',
        'title': 'Ceded case reserves, IBNR and risk adjustment are aggregate only',
        'amount': D('23264702'),
        'severity': 'medium',
        'detail': 'The aggregate ceded incurred-claims balance is controlled, but its '
                  'allocation across treaties is a modelling proxy, not a claim-level '
                  'attribution. There is therefore no treaty-level ceded-reserve fact.',
        'source': 'report §6.5 Table 30',
    },
    {
        'ref': 'DQ-06',
        'title': 'FY2025 opening unearned-premium difference has not been posted',
        'amount': D('505382'),
        'severity': 'medium',
        'detail': 'The FY2025 opening difference of 505,382 between the signed and the '
                  'ledger unearned-premium balances has not been posted.',
        'source': 'report §7.4',
    },
    {
        'ref': 'DQ-07',
        'title': 'Unsupported opening overlay in the reinsurance asset',
        'amount': D('149103.88'),
        'severity': 'low',
        'detail': 'The model releases an opening balance of 20,886,607.94 while the '
                  'balance carried forward from FY2025 is 20,737,504.06. The 149,103.88 '
                  'difference is an unsupported prior-year excess-of-loss and '
                  'catastrophe row. Conservative in direction; retained unadjusted.',
        'source': 'report §6.6 Table 33',
    },
    {
        'ref': 'DQ-08',
        'title': 'Profit commission accrual is not contractually evidenced',
        'amount': D('1427557.30'),
        'severity': 'medium',
        'detail': 'Accrued from the approved trial balance. Its attribution to JBB Motor '
                  'QS is a technical proxy, not a contractual allocation, and the '
                  '31 July 2026 settlement date is described as plausible but not '
                  'contractually evidenced.',
        'source': 'report §6.7 Table 34',
    },
    {
        'ref': 'DQ-09',
        'title': 'Trial balance premium differs from IFRS 17 revenue',
        'amount': D('21490'),
        'severity': 'low',
        'detail': 'The trial balance records 133,437,785 of gross written premium against '
                  'IFRS 17 revenue of 133,416,295 — a 21,490 class-allocation rounding '
                  'difference, disclosed.',
        'source': 'report §2.1',
    },
    {
        'ref': 'DQ-14',
        'title': 'Segment premium totals differ from the reported GWP',
        'amount': D('5'),
        'severity': 'low',
        'detail': 'The eight segment premiums sum to 133,416,290 (or 133,416,296 with '
                  "the model's per-cent rounding) against a reported gross written "
                  'premium of 133,416,295. The report discloses this at Table 4: '
                  '"Segment totals differ from the reported gross written premium by '
                  'BWP 5 in FY2026 and BWP 6 in FY2025 because of rounding within the '
                  'class allocation." The reported GWP is the valuation figure.',
        'source': 'report §2.5 Table 4',
    },
    {
        'ref': 'DQ-13',
        'title': "The report's own components do not foot to its stated result",
        'amount': D('1'),
        'severity': 'low',
        'detail': 'Insurance revenue 133,416,295 plus insurance service expenses '
                  '(113,980,546) plus the net reinsurance result (6,571,505) sums to '
                  '12,864,244, while Table 1 and Table 14 state a profit before tax of '
                  '12,864,243. The report acknowledges the same class of difference at '
                  '§7.2, where its own bridge "equals BWP 12,864,245. The BWP 2 '
                  'difference is rounding." The engine reports the arithmetic sum and '
                  'discloses the difference rather than forcing either side.',
        'source': 'report §1.2 Table 1 / §4.1 Table 14 / §7.2 Table 41',
    },
    {
        'ref': 'DQ-11',
        'title': 'The IBNR-by-underwriting-year table does not foot to the selection',
        'amount': D('31'),
        'severity': 'low',
        'detail': 'Table 21 lists IBNR by underwriting year; the seven rows sum to '
                  '21,155,255 against a stated gross IBNR selection of 21,155,286 — '
                  'a 31 rounding difference inside the report. The SIGNED SELECTION '
                  'is used as the valuation figure; the rows are carried for display. '
                  'Re-adding the rows instead would silently replace a signed figure '
                  'with one 31 lower and drag the claims-handling reserve, the salvage '
                  'deduction, the LIC, the balance sheet and the net position with it.',
        'source': 'report §5.2 Table 21 vs §1.2 Table 1',
    },
    {
        'ref': 'DQ-12',
        'title': 'The published attributable-expense share is a rounded display',
        'amount': D('15129'),
        'severity': 'low',
        'detail': 'The report states the attributable share as 92.9%. The signed '
                  'attributable expense of 29,168,270 against total operating '
                  'expenses of 31,381,207 is 92.9435%. Multiplying by the rounded '
                  '92.9% understates the expense by 15,129, which then flows to the '
                  'insurance service result. The signed figure is used at the signed '
                  'share; the lever scales proportionally away from it.',
        'source': 'report §3.10 Table 12',
    },
    {
        'ref': 'DQ-10',
        'title': 'Case reserves that cannot be mapped to an origin year',
        'amount': D('401561'),
        'severity': 'low',
        'detail': 'Of the gross case reserves, 401,561 could not be mapped to an origin '
                  'year. It is retained in full in the liability but excluded from the '
                  'deduction against the chain-ladder outstanding, which is prudent.',
        'source': 'report §2.2',
    },
]
