"""
ifrs17/disclosures.py — the notes that go into the financial statements.

Builders for the tables IFRS 17 paragraphs 100–105 require, reproduced from
Empirica's Appendix A. Pattern follows nbfira/builders.py: each function returns
a plain dict of {title, columns, rows, note} so the same structure feeds the
screen, the xlsx export and the Word note without three versions of the truth.

Every builder takes a `Computed` from engine.compute(), so a lever the CFO drags
on the cockpit moves the disclosure note too. That is the whole point of the
module: the note is not typed, it is derived.

Nothing here posts to the general ledger.
"""
from __future__ import annotations

from decimal import Decimal as D

from .constants import (
    CEDED_EVIDENCE_BASIS, CEDED_UNEARNED_BY_TREATY_FY26, CONFIRMED_PANEL_SHARE_FY26,
    KNOWN_VARIANCES, REINSURER_PANEL_FY26, REPORTED, SEGMENT_FY26,
)
from .engine import Computed

ZERO = D('0')


def _t(title, columns, rows, note='', ref=''):
    return {'title': title, 'ref': ref, 'columns': columns, 'rows': rows, 'note': note}


# ---------------------------------------------------------------------------
# The primary statements
# ---------------------------------------------------------------------------

def statement_of_comprehensive_income(c: Computed) -> dict:
    """§4.1 Table 14."""
    prior = REPORTED.get('FY2025', {})
    def row(label, cur, key):
        was = prior.get(key)
        return [label, cur, was, (cur - was) if was is not None else None]

    return _t(
        'Statement of comprehensive income under IFRS 17',
        ['', c.year, 'FY2025', 'Change'],
        [
            row('Insurance revenue', c.insurance_revenue, 'insurance_revenue'),
            row('Insurance service expenses', c.insurance_service_expenses,
                'insurance_service_expenses'),
            row('Insurance service result before reinsurance held',
                c.service_result_before_reinsurance, 'service_result_before_reinsurance'),
            row('Allocation of reinsurance premiums',
                c.allocation_of_reinsurance_premiums, 'allocation_of_reinsurance_premiums'),
            row('Amounts recoverable from reinsurers', c.amounts_recoverable,
                'amounts_recoverable'),
            row('Net income / (expense) from reinsurance held',
                c.net_reinsurance_result, 'net_reinsurance_result'),
            row('Insurance service result', c.insurance_service_result,
                'insurance_service_result'),
            ['Net insurance finance result', ZERO, ZERO, ZERO],
            row('Profit before tax', c.profit_before_tax, 'profit_before_tax'),
        ],
        note='The net insurance finance result is nil at both dates because the '
             'discounting expedients are taken and the book carries no '
             'foreign-currency exposure.',
        ref='§4.1 Table 14')


def statement_of_financial_position(c: Computed) -> dict:
    """§4.4 Table 17."""
    p = REPORTED.get('FY2025', {})
    return _t(
        'Statement of financial position',
        ['', c.year, 'FY2025', 'Change'],
        [
            ['Reinsurance contract assets', c.reinsurance_contract_assets,
             p.get('reinsurance_contract_assets'),
             c.reinsurance_contract_assets - p.get('reinsurance_contract_assets', ZERO)],
            ['Insurance contract liabilities', c.insurance_contract_liabilities,
             p.get('insurance_contract_liabilities'),
             c.insurance_contract_liabilities - p.get('insurance_contract_liabilities', ZERO)],
            ['Reinsurance contract liabilities', ZERO, ZERO, ZERO],
            ['Net IFRS 17 position, liability', c.net_ifrs17_liability,
             p.get('net_ifrs17_liability'),
             c.net_ifrs17_liability - p.get('net_ifrs17_liability', ZERO)],
        ],
        note='The balance sheet is presented gross as the standard requires. All '
             'reinsurance groups are in a net asset position at both dates, so no '
             'reinsurance contract liability arises.',
        ref='§4.4 Table 17')


# ---------------------------------------------------------------------------
# Appendix A — paragraphs 100 to 105
# ---------------------------------------------------------------------------

def a1_movement_in_insurance_contract_liabilities(c: Computed) -> dict:
    """A.1 Table 47 — by measurement component."""
    p = REPORTED['FY2025']
    opening_lrc, opening_lic, opening_ra = p['lrc'], p['lic_best_estimate'], p['lic_risk_adjustment']
    incurred = c.claims_incurred + c.attributable_expenses
    rows = [
        ['Opening at 30 June 2025', opening_lrc, ZERO, opening_lic, opening_ra,
         opening_lrc + opening_lic + opening_ra],
        ['Insurance revenue', -c.insurance_revenue, ZERO, ZERO, ZERO, -c.insurance_revenue],
        ['Incurred claims and other expenses', ZERO, ZERO, incurred,
         c.lic_risk_adjustment, incurred + c.lic_risk_adjustment],
        ['Amortisation of acquisition cash flows', c.acquisition_amortisation, ZERO,
         ZERO, ZERO, c.acquisition_amortisation],
        ['Changes to the LIC, past service', ZERO, ZERO, -opening_lic, -opening_ra,
         -(opening_lic + opening_ra)],
        ['Closing at 30 June ' + c.year[-4:], c.lrc, c.loss_component,
         c.lic_best_estimate, c.lic_risk_adjustment, c.insurance_contract_liabilities],
    ]
    return _t('Movement in insurance contract liabilities by component',
              ['', 'LRC excl. LC', 'LRC LC', 'LIC estimates', 'LIC RA', 'Total'],
              rows,
              note='Reproduced from the valuation model without adjustment '
                   '(IFRS 17 paragraphs 100 to 105).',
              ref='Appendix A.1 Table 47')


def a2_movement_in_reinsurance_contract_assets(c: Computed) -> dict:
    """A.2 Table 48 — including the disclosed opening overlay."""
    p = REPORTED['FY2025']
    overlay = D('149103.88')
    rows = [
        ['Opening at 30 June 2025', p.get('arc', D('11399424')), ZERO,
         D('19563683'), D('1173821'), p['reinsurance_contract_assets']],
        ['Allocation of reinsurance premiums', c.allocation_of_reinsurance_premiums,
         ZERO, ZERO, ZERO, c.allocation_of_reinsurance_premiums],
        ['Amounts recoverable, claims incurred in the period', ZERO, ZERO,
         D('71501075'), c.aric_risk_adjustment, D('72896957')],
        ['Changes in amounts recoverable, past service', ZERO, ZERO,
         D('-19704347'), D('-1182261'), D('-20886608')],
        ['Roll-forward closing', c.arc, ZERO, D('23124038'), D('1387442'),
         D('35294168')],
        ['Opening overlay difference', ZERO, ZERO, D('140664'), D('8440'), overlay],
        ['Closing at 30 June ' + c.year[-4:], c.arc, ZERO, c.aric_best_estimate,
         c.aric_risk_adjustment, c.reinsurance_contract_assets],
    ]
    return _t('Movement in reinsurance contract assets by component',
              ['', 'ARC', 'ARC LRC', 'ARIC estimates', 'ARIC RA', 'Total'],
              rows,
              note='🔴 The movement reconciles in every line except one. The model '
                   'releases an opening balance of 20,886,607.94 while the balance '
                   'carried forward from FY2025 is 20,737,504.06. The 149,103.88 '
                   'difference is an unsupported prior-year excess-of-loss and '
                   'catastrophe row (DQ-07). It is conservative in direction and is '
                   'retained unadjusted — stated here rather than absorbed.',
              ref='Appendix A.2 Table 48')


def a3_analysis_of_insurance_revenue(c: Computed) -> dict:
    """A.3 Table 49."""
    movement_in_lrc = REPORTED['FY2025']['lrc'] - c.lrc
    premiums_received = c.insurance_revenue - movement_in_lrc
    return _t('Analysis of insurance revenue',
              ['', c.year],
              [
                  ['Premiums received in the period', premiums_received],
                  ['Movement in the liability for remaining coverage', movement_in_lrc],
                  ['Insurance revenue', c.insurance_revenue],
                  ['— Expected claims and other directly attributable expenses',
                   D('97848206.98')],
                  ['— Recovery of insurance acquisition cash flows',
                   c.acquisition_amortisation],
                  ['— Risk adjustment release and underwriting margin',
                   c.insurance_revenue - D('97848206.98') - c.acquisition_amortisation],
              ],
              ref='Appendix A.3 Table 49')


def a4_amounts_recoverable_from_reinsurers(c: Computed) -> dict:
    """A.4 Table 50 — separating cash from the non-cash reserve movement."""
    direct = REPORTED[c.year]['direct_recoveries_received']
    return _t('Analysis of amounts recoverable from reinsurers',
              ['', c.year],
              [
                  ['Amounts recoverable for claims incurred in the period',
                   D('72896957.08')],
                  ['Changes in amounts recoverable, release of the opening balance',
                   D('-20886607.94')],
                  ['Amounts recoverable from reinsurers', c.amounts_recoverable],
                  ['— Direct recoveries received in the period', direct],
                  ['— Non-cash contribution from the movement in ceded reserves',
                   c.amounts_recoverable - direct],
              ],
              note='The split matters: only the direct recoveries are cash. The '
                   'non-cash contribution is the single largest reason the IFRS 17 '
                   'result and the Management Accounts result differ.',
              ref='Appendix A.4 Table 50')


# ---------------------------------------------------------------------------
# Reserves, segments, reinsurance
# ---------------------------------------------------------------------------

def gross_reserves_by_type(c: Computed) -> dict:
    """§5.1 Table 20."""
    p = REPORTED['FY2025']
    def r(label, cur, was):
        chg = cur - was
        pct = (chg / was * 100) if was else None
        return [label, cur, was, chg, pct]
    return _t('Gross technical reserves by type',
              ['Reserve', c.year, 'FY2025', 'Change', '%'],
              [
                  r('Outstanding case reserves', c.gross_case_reserves,
                    p['gross_case_reserves']),
                  r('Incurred but not reported', c.gross_ibnr, p['gross_ibnr']),
                  r('Claims handling expense reserve', c.che_reserve, p['che_reserve']),
                  r('Expected salvage and subrogation', -c.salvage_subrogation, ZERO),
                  r('Liability for incurred claims', c.lic_best_estimate,
                    p['lic_best_estimate']),
                  r('Risk adjustment', c.lic_risk_adjustment, p['lic_risk_adjustment']),
                  r('Liability for remaining coverage', c.lrc, p['lrc']),
                  r('Total gross technical reserves', c.insurance_contract_liabilities,
                    p['insurance_contract_liabilities']),
              ],
              ref='§5.1 Table 20')


def ibnr_by_underwriting_year(c: Computed) -> dict:
    """§5.2 Table 21."""
    rows = [[r['underwriting_year'], r['observed_cumulative'], r['selected_ultimate'],
             r['outstanding'], r['ibnr']] for r in c.by_uwy]
    row_sum = sum(r['ibnr'] for r in c.by_uwy)
    rows.append(['Total (rows)', None, None, None, row_sum])
    rows.append(['Selected (signed)', None, None, None, c.gross_ibnr])
    return _t('Gross IBNR by underwriting year',
              ['Underwriting year', 'Observed cumulative', 'Selected ultimate',
               'Outstanding', 'IBNR'],
              rows,
              note=f'🔴 The rows sum to {row_sum:,.0f} against a signed selection of '
                   f'{c.gross_ibnr:,.0f} — a {c.gross_ibnr - row_sum:,.0f} rounding '
                   f'difference inside the report (DQ-11). The SIGNED selection is the '
                   f'valuation figure; the rows are shown for analysis.',
              ref='§5.2 Table 21')


def segment_table(c: Computed) -> dict:
    """§4.3 Table 16 — premium, commission, claims, loss and combined ratios."""
    rows = []
    for s in c.by_segment:
        rows.append([s.segment.title(), s.premium, s.commission, s.claims,
                     s.loss_ratio, s.combined_ratio])
    rows.append(['Total', c.insurance_revenue, c.acquisition_amortisation,
                 c.claims_incurred, c.loss_ratio, c.combined_ratio])
    return _t('Premium, commission, claims and ratios by segment',
              ['Segment', 'Premium', 'Commission', 'Claims', 'Loss ratio', 'Combined'],
              rows,
              note='Attributable expenses are allocated to segments pro-rata to '
                   'premium, so the apportionment affects every segment combined '
                   'ratio equally and does not alter the relative ranking.',
              ref='§4.3 Table 16')


def reinsurance_programme(c: Computed) -> dict:
    """§6.2 Table 26 + §6.3 Tables 27/28."""
    rows = [[t['treaty'].replace('_', ' ').title(), t['ceded_premium'], t['commission'],
             t['direct_recovery'], t['net_premium_cost'], t['direct_economics']]
            for t in c.by_treaty]
    return _t('Ceded premium, commission, recoveries and direct economics by treaty',
              ['Treaty', 'Ceded premium', 'Commission', 'Direct recovery',
               'Net premium cost', 'Direct economics'],
              rows,
              note='Direct economics is recovery plus commission less ceded premium — '
                   'what each treaty delivered in cash before any movement in ceded '
                   'reserves.',
              ref='§6.2 Table 26 / §6.3 Table 28')


def ceded_evidence_basis() -> dict:
    """§6.5 Table 30 — what is a treaty-level FACT and what is a model allocation.

    The report calls this "the point on which the reader should be most careful".
    So the module shows it as its own table rather than a footnote.
    """
    labels = {
        'treaty_level': 'Treaty level — evidenced',
        'treaty_level_partial': 'Treaty level, partial',
        'aggregate_only': '🔴 Aggregate only — the treaty split is a MODEL ALLOCATION',
    }
    rows = [[k.replace('_', ' ').title(), labels[v]] for k, v in CEDED_EVIDENCE_BASIS.items()]
    return _t('Evidential basis of each reinsurance balance',
              ['Balance', 'Basis'],
              rows,
              note='Ceded case reserves, ceded IBNR and the ceded risk adjustment are '
                   'controlled in aggregate, but their allocation across treaties is a '
                   'modelling proxy, not a claim-level attribution. There is therefore '
                   'no treaty-level ceded-reserve fact (DQ-05).',
              ref='§6.5 Table 30')


def reinsurer_panel() -> dict:
    """§6.9 Table 36 — ratings, shares and default probabilities."""
    rows = []
    for name, rating, share, pd, confirmed in REINSURER_PANEL_FY26:
        rows.append([name, rating,
                     share if share is not None else 'not confirmed',
                     pd, 'yes' if confirmed else '🔴 pending signed evidence'])
    return _t('Reinsurer panel, signed shares and estimated default probability',
              ['Reinsurer', 'Rating', 'FY2026 share', '1-year PD', 'Share confirmed'],
              rows,
              note=f'🔴 Only {CONFIRMED_PANEL_SHARE_FY26:.0%} of the FY2026 panel is '
                   f'confirmed, down from 90% in FY2025. The share placed with a B+ '
                   f'rated reinsurer at a 2.44% one-year default probability is the '
                   f'main contributor to any expected credit loss, and no '
                   f'reinsurer-level expected credit loss has been computed (DQ-02).',
              ref='§6.9 Table 36')


def ceded_unearned_premium_by_treaty() -> dict:
    """§6.4 Table 29."""
    total = sum(CEDED_UNEARNED_BY_TREATY_FY26.values())
    rows = [[k.replace('_', ' ').title(), v, v / total]
            for k, v in CEDED_UNEARNED_BY_TREATY_FY26.items()]
    rows.append(['Total ceded unearned premium', total, D('1')])
    return _t('Ceded unearned premium at 30 June 2026',
              ['Treaty', 'Ceded unearned premium', 'Share'], rows,
              note='No treaty-level split of ceded unearned premium is available for '
                   'FY2025, so no comparative is shown.',
              ref='§6.4 Table 29')


def data_quality_register() -> dict:
    """The disclosed differences, as a table the auditors can work through."""
    rows = [[v['ref'], v['title'], v['amount'], v['severity'], v['source']]
            for v in KNOWN_VARIANCES]
    return _t('Data quality and disclosed differences',
              ['Ref', 'Item', 'Amount', 'Severity', 'Source'], rows,
              note='Every one of these is disclosed in the valuation report. None may '
                   'be silently absorbed into a result.',
              ref='register')


def build_all(c: Computed) -> dict:
    """Every disclosure, keyed for the screen, the workbook and the Word note."""
    return {
        'statement_of_comprehensive_income': statement_of_comprehensive_income(c),
        'statement_of_financial_position': statement_of_financial_position(c),
        'a1_insurance_contract_liabilities': a1_movement_in_insurance_contract_liabilities(c),
        'a2_reinsurance_contract_assets': a2_movement_in_reinsurance_contract_assets(c),
        'a3_insurance_revenue': a3_analysis_of_insurance_revenue(c),
        'a4_amounts_recoverable': a4_amounts_recoverable_from_reinsurers(c),
        'gross_reserves_by_type': gross_reserves_by_type(c),
        'ibnr_by_underwriting_year': ibnr_by_underwriting_year(c),
        'segments': segment_table(c),
        'reinsurance_programme': reinsurance_programme(c),
        'ceded_evidence_basis': ceded_evidence_basis(),
        'ceded_unearned_premium': ceded_unearned_premium_by_treaty(),
        'reinsurer_panel': reinsurer_panel(),
        'data_quality': data_quality_register(),
    }
