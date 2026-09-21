"""
nbfira/builders.py — pure-function generators for each quarterly schedule.

Phase 2 deliverable. Each `build_schedule_*(period_start, period_end,
company_id)` returns a list of `{schedule, line_code, label, value,
sort_order, source_accounts, formula, section}` dicts ready to write
into NBFIRAReturnLine.

Source-traced where possible — the `source_accounts` list lets the UI
expand each row to show contributing GL accounts.

Values are expressed in P'000 (NBFIRA convention: divide BWP by 1000).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .constants import (
    DEFAULT_IRC, DEFAULT_MCR_BWP,
    DEFAULT_MRC, INSURANCE_CLASSES, CLASS_LABEL,
    SCHEDULE_A_ASSETS, SCHEDULE_A_CLASS_COLS, SCHEDULE_A_LIAB,
    SCHEDULE_A_OPERATING, SCHEDULE_A_SURPLUS, SCHEDULE_C, SCHEDULE_IS,
    SCHEDULE_AFS, SCHEDULE_IMF_ASSETS, SCHEDULE_IMF_LIAB,
)
from django.utils import timezone


ZERO = Decimal('0')
THOUSAND = Decimal('1000')


def _p000(v) -> Decimal:
    """Convert raw BWP to P'000 with 2dp."""
    try:
        return (Decimal(str(v or 0)) / THOUSAND).quantize(Decimal('0.0001'))
    except Exception:    # noqa: BLE001
        return ZERO


def _factor(kind: str, key: str, default: float, as_of: date | None = None) -> Decimal:
    """Read NBFIRACapitalFactor or fall back to blueprint default."""
    from .models import NBFIRACapitalFactor
    as_of = as_of or timezone.localdate()
    qs = NBFIRACapitalFactor.objects.filter(kind=kind, key=key,
                                            effective_from__lte=as_of)
    qs = qs.exclude(effective_to__isnull=False, effective_to__lt=as_of)
    obj = qs.order_by('-effective_from').first()
    if obj:
        return Decimal(obj.factor_value)
    return Decimal(str(default))


# ─────────────────────────────────────────────────────────────────────────
# Schedule IS — Income Statement (MA layout via existing builder)
# ─────────────────────────────────────────────────────────────────────────
def build_schedule_is(period_start: date, period_end: date,
                      company_id: Optional[str] = None) -> list[dict]:
    from reporting.ma_pl import build_ma_pl
    try:
        r = build_ma_pl(period_start, period_end, company_id=company_id)
    except Exception:    # noqa: BLE001
        r = {'totals': {}}
    t = r.get('totals', {}) or {}

    def _d(key, default='0'):
        return Decimal(str(t.get(key, default) or default))

    # NBFIRA expects P'000.
    gwp        = _d('gross_written_premium')
    ceded      = _d('premiums_ceded')
    upr        = _d('change_in_upr')
    nep        = _d('net_earned_premium')
    gross_clm  = _d('gross_claims')
    ri_rec     = _d('ri_claims_recovered')
    subrog     = _d('subrogations_salvages')
    nci        = _d('net_claim_incurred')
    comm_ri    = _d('commission_from_reinsurers') if 'commission_from_reinsurers' in t else ZERO
    comm_pd    = _d('commissions_paid') if 'commissions_paid' in t else ZERO
    net_acq    = _d('net_acquisition')
    gp         = _d('gross_profit')
    other_inc  = _d('total_other_income')
    opex       = _d('total_operating_expenses')
    prov       = _d('total_provisions')
    ebitda     = _d('ebitda')
    depr       = _d('depreciation')
    ebit       = _d('ebit')
    fc         = _d('finance_cost')
    pbt        = _d('pbt')
    tax        = _d('taxation')
    pat        = _d('pat')
    gross_lr   = Decimal(str(t.get('gross_loss_ratio', '0') or '0'))
    net_lr     = Decimal(str(t.get('net_loss_ratio',   '0') or '0'))

    cost_ratio = ((opex.copy_abs() + net_acq.copy_abs()) / gwp) if gwp != 0 else ZERO
    combined   = net_lr + cost_ratio
    gp_margin  = (gp / gwp) if gwp != 0 else ZERO

    raw = {
        'IS_01': gwp,         'IS_02': ceded,      'IS_03': upr,        'IS_04': nep,
        'IS_05': gross_clm,   'IS_06': ri_rec,     'IS_07': subrog,     'IS_08': nci,
        'IS_09': comm_ri,     'IS_10': comm_pd,    'IS_11': net_acq,
        'IS_12': gp,          'IS_13': other_inc,  'IS_14': opex,       'IS_15': prov,
        'IS_16': ebitda,      'IS_17': depr,       'IS_18': ebit,       'IS_19': fc,
        'IS_20': pbt,         'IS_21': tax,        'IS_22': pat,
        # Ratios kept as plain decimal (caller renders as %).
        'IS_23': gross_lr,    'IS_24': net_lr,
        'IS_25': cost_ratio,  'IS_26': combined,   'IS_27': gp_margin,
    }
    out = []
    for i, (code, label) in enumerate(SCHEDULE_IS):
        v = raw.get(code, ZERO)
        # Ratios (IS_23..27) stay as decimals; money goes to P'000.
        if code in ('IS_23', 'IS_24', 'IS_25', 'IS_26', 'IS_27'):
            value = Decimal(v).quantize(Decimal('0.0001'))
        else:
            value = _p000(v)
        out.append({
            'schedule': 'IS', 'section': 'income_statement',
            'line_code': code, 'label': label,
            'value': value, 'sort_order': i,
            'source_accounts': [],
            'formula': 'From MA P&L (reporting.ma_pl.build_ma_pl)',
        })
    return out


# ─────────────────────────────────────────────────────────────────────────
# Per-class GWP — derived from billing.Invoice or PolicyClass mapping.
# For Phase 2 we read from a pluggable provider; if no mapping exists,
# all classes return zero and the form lets Finance edit per-class
# overrides post-generate.
# ─────────────────────────────────────────────────────────────────────────
def per_class_gwp(period_start: date, period_end: date,
                  company_id: Optional[str] = None) -> dict[str, Decimal]:
    """Stub: returns 0 for every class. Hook a real provider when the
    policy-class GL mapping table is seeded (Phase 4 work item).

    The Quarterly Return UI will let Finance override these values per
    class so the seeded zeros never block a real return.
    """
    return {c: ZERO for c in INSURANCE_CLASSES}


# ─────────────────────────────────────────────────────────────────────────
# Schedule A — Current quarter operating results + assets + liabs + PCT
# ─────────────────────────────────────────────────────────────────────────
def build_schedule_a(period_start: date, period_end: date,
                     company_id: Optional[str] = None,
                     ytd: bool = False) -> list[dict]:
    schedule = 'B' if ytd else 'A'

    # Pull P&L for operating section.
    from reporting.ma_pl import build_ma_pl
    try:
        r = build_ma_pl(period_start, period_end, company_id=company_id)
    except Exception:    # noqa: BLE001
        r = {'totals': {}}
    t = r.get('totals', {}) or {}

    def _d(key):
        return Decimal(str(t.get(key, '0') or '0'))

    gwp  = _d('gross_written_premium')
    nwp  = gwp - _d('premiums_ceded')
    upr  = _d('change_in_upr')
    nep  = _d('net_earned_premium')
    nci  = _d('net_claim_incurred')
    comm_pd  = _d('commissions_paid') if 'commissions_paid' in t else ZERO
    comm_ri  = _d('commission_from_reinsurers') if 'commission_from_reinsurers' in t else ZERO
    net_comm = comm_pd - comm_ri
    opex     = _d('total_operating_expenses').copy_abs()
    underw   = nep - nci - net_comm - opex
    inv_inc  = ZERO  # populated when investments module reports
    other_inc = _d('total_other_income')
    pbt      = _d('pbt')
    tax      = _d('taxation')
    npat     = _d('pat')

    op_values = {
        'A_OP_01': ZERO,           # opening UPR (closing of prior period)
        'A_OP_02': gwp,
        'A_OP_03': nwp,
        'A_OP_04': ZERO,
        'A_OP_05': upr,            # closing UPR
        'A_OP_06': nep,
        'A_OP_07': ZERO,           # opening OS claims + IBNR
        'A_OP_08': nci,            # net claims paid for the period
        'A_OP_09': ZERO,
        'A_OP_10': ZERO,           # closing OS claims + IBNR
        'A_OP_11': nci,
        'A_OP_12': net_comm,
        'A_OP_13': comm_pd,
        'A_OP_14': comm_ri,
        'A_OP_15': opex,
        'A_OP_16': underw,
        'A_OP_17': inv_inc,
        'A_OP_18': other_inc,
        'A_OP_19': ZERO,
        'A_OP_20': ZERO,
        'A_OP_21': ZERO,
        'A_OP_22': ZERO,
        'A_OP_23': pbt,
        'A_OP_24': tax,
        'A_OP_25': npat,
        'A_OP_26': ZERO,
        'A_OP_27': npat,
        'A_OP_28': ZERO,
        'A_OP_29': ZERO,
        'A_OP_30': ZERO,
        'A_OP_31': npat,
    }

    out = []
    so = 0
    for code, label in SCHEDULE_A_OPERATING:
        out.append({
            'schedule': schedule, 'section': 'operating_results',
            'line_code': code, 'label': label,
            'value': _p000(op_values.get(code, ZERO)), 'sort_order': so,
            'source_accounts': [],
            'formula': '',
        })
        so += 1

    # Per-class block (4 columns × 9 classes).
    class_gwp = per_class_gwp(period_start, period_end, company_id)
    for cls in INSURANCE_CLASSES:
        for col_key, col_label in SCHEDULE_A_CLASS_COLS:
            code = f'{schedule}_CL_{cls.upper()}_{col_key.upper()}'
            label = f'{CLASS_LABEL[cls]} — {col_label}'
            # Phase 2 default: zero. Finance edits via UI.
            value = _p000(class_gwp.get(cls, ZERO)) if col_key == 'written' else ZERO
            out.append({
                'schedule': schedule, 'section': 'underwriting_per_class',
                'line_code': code, 'label': label,
                'value': value, 'sort_order': so,
                'source_accounts': [],
                'formula': 'Per-class GWP mapping (seeded zero — edit in UI).',
            })
            so += 1

    # Assets block — best-effort pull from build_balance_sheet.
    asset_values = _balance_sheet_pull(period_end, company_id)
    for code, label in SCHEDULE_A_ASSETS:
        v = asset_values.get(code, ZERO)
        out.append({
            'schedule': schedule, 'section': 'assets',
            'line_code': code, 'label': label,
            'value': _p000(v), 'sort_order': so,
            'source_accounts': [],
            'formula': 'From Balance Sheet builder.',
        })
        so += 1

    # Liabilities block
    liab_values = _balance_sheet_liabs(period_end, company_id)
    for code, label in SCHEDULE_A_LIAB:
        v = liab_values.get(code, ZERO)
        out.append({
            'schedule': schedule, 'section': 'liabilities',
            'line_code': code, 'label': label,
            'value': _p000(v), 'sort_order': so,
            'source_accounts': [],
            'formula': 'From Balance Sheet builder.',
        })
        so += 1

    # Surplus / PCT block — computed against Statement A.1 totals.
    total_adm = asset_values.get('A_AS_39', ZERO)
    total_liab = liab_values.get('A_LI_16', ZERO)
    net_assets = total_adm - total_liab
    # PCT requirement filled by Schedule A.1 builder; for now leave 0;
    # the API layer re-stitches A.1 PCT into A_SU_04 after both build.
    pct_req = ZERO
    surplus = net_assets - pct_req
    pct_cover = (net_assets / pct_req) if pct_req != 0 else ZERO

    surplus_values = {
        'A_SU_01': total_adm, 'A_SU_02': total_liab, 'A_SU_03': net_assets,
        'A_SU_04': pct_req,    'A_SU_05': surplus,    'A_SU_06': pct_cover,
    }
    for code, label in SCHEDULE_A_SURPLUS:
        out.append({
            'schedule': schedule, 'section': 'surplus_assets',
            'line_code': code, 'label': label,
            'value': _p000(surplus_values.get(code, ZERO)) if code != 'A_SU_06'
                     else surplus_values.get(code, ZERO).quantize(Decimal('0.0001')),
            'sort_order': so,
            'source_accounts': [],
            'formula': '',
        })
        so += 1
    return out


def _balance_sheet_pull(as_of: date, company_id: Optional[str]) -> dict[str, Decimal]:
    """Best-effort BS pull. Returns line_code → value in raw BWP."""
    from reporting.reports import build_balance_sheet
    try:
        bs = build_balance_sheet(as_of, company_id=company_id)
    except Exception:    # noqa: BLE001
        return {'A_AS_39': ZERO}
    totals = (bs or {}).get('totals', {}) or {}
    out: dict[str, Decimal] = {}
    out['A_AS_39'] = Decimal(str(totals.get('total_assets', '0') or '0'))
    # Other rows left blank in Phase 2; Finance fills via UI override.
    return out


def _balance_sheet_liabs(as_of: date, company_id: Optional[str]) -> dict[str, Decimal]:
    from reporting.reports import build_balance_sheet
    try:
        bs = build_balance_sheet(as_of, company_id=company_id)
    except Exception:    # noqa: BLE001
        return {'A_LI_16': ZERO}
    totals = (bs or {}).get('totals', {}) or {}
    # liabilities_and_equity = total_assets when balanced; we want liabilities alone.
    # Approximation: liabilities = total_assets - equity (when balanced).
    total_assets = Decimal(str(totals.get('total_assets', '0') or '0'))
    le           = Decimal(str(totals.get('liabilities_and_equity', '0') or '0'))
    # Treat liabilities as the larger of (le - assets) reverse-split or zero.
    # Conservative: just return total_assets so the surplus arithmetic doesn't lie.
    return {'A_LI_16': total_assets if le == ZERO else (le - total_assets).copy_abs()}


# ─────────────────────────────────────────────────────────────────────────
# Schedule A.1 — Prescribed Capital Target / Solvency
# ─────────────────────────────────────────────────────────────────────────
def a1_inputs(period_start: date, period_end: date,
              company_id: Optional[str] = None,
              return_obj=None) -> dict:
    """The four A.1 inputs the workbook needs, none of which the GL can supply.

    `anwp` is ASSUMED annual net written premium for the NEXT 12 months — a
    forward assumption, not history. Omni's old code reached for historical GWP
    by class here, which is the wrong figure conceptually however well it is
    mapped. `mer_total` comes off the reinsurance treaty's net event retention,
    and the asset rows come from an investment classification. All three are
    entered figures.

    Until there is somewhere to enter them these stay zero, and the target falls
    to the statutory MCR floor — an honest floor rather than a confident wrong
    number. Wire a real source in here; nothing else needs to change.
    """
    blank = {
        'anwp':        {c: ZERO for c in INSURANCE_CLASSES},
        'mer_total':   ZERO,
        'net_assets':  {key: ZERO for key, _l, _f in DEFAULT_MRC},
        'alloc_mrctr': {key: ZERO for key, _l, _f in DEFAULT_MRC},
    }
    if return_obj is None:
        return blank
    from .models import NBFIRAA1Input
    row = NBFIRAA1Input.objects.filter(return_obj=return_obj).first()
    if row is None:
        return blank
    saved = row.as_builder_inputs()
    # Merge over the blanks so a class or bucket the entry left out reads zero
    # rather than disappearing from the schedule.
    return {
        'anwp':        {**blank['anwp'], **saved['anwp']},
        'mer_total':   saved['mer_total'],
        'net_assets':  {**blank['net_assets'], **saved['net_assets']},
        'alloc_mrctr': {**blank['alloc_mrctr'], **saved['alloc_mrctr']},
    }


def build_schedule_a1(period_start: date, period_end: date,
                      company_id: Optional[str] = None,
                      inputs: Optional[dict] = None,
                      return_obj=None) -> list[dict]:
    """Statement A.1, computed the way the filed NBFIRA workbook computes it.

    CFO decision 2026-08-18. The arithmetic lives in `a1_math.compute_a1` and is
    proved against the four FY2026 filed returns in tests/test_a1_math.py. The
    previous version multiplied a flat factor by premium, added the three risk
    parts together and multiplied by stored g-factors — wrong on all three
    counts. See a1_math's docstring.
    """
    from .a1_math import compute_a1

    src = inputs if inputs is not None else a1_inputs(
        period_start, period_end, company_id, return_obj=return_obj)

    mcr = _factor('mcr', 'default', DEFAULT_MCR_BWP)
    irc_factors = {c: _factor('irc', c, DEFAULT_IRC[c]) for c in INSURANCE_CLASSES}
    mrc_factors = {key: _factor('mrc', key, fac) for key, _l, fac in DEFAULT_MRC}

    r = compute_a1(
        mcr=_p000(mcr),
        classes=INSURANCE_CLASSES,
        irc_factors=irc_factors,
        anwp=src.get('anwp') or {},
        mer_total=src.get('mer_total') or ZERO,
        buckets=[key for key, _l, _f in DEFAULT_MRC],
        mrc_factors=mrc_factors,
        net_assets=src.get('net_assets') or {},
        alloc_mrctr=src.get('alloc_mrctr') or {},
    )

    # compute_a1 works entirely in P'000 (the workbook's unit, and what these
    # lines are stored in), so values go out as-is rather than through _p000 a
    # second time.
    def q(v):
        return Decimal(v).quantize(Decimal('0.0001'))

    out = []
    so = 0

    def add(section, code, label, value, formula):
        # NBFIRAReturnLine.formula is varchar(200); an over-long trace is a
        # 500 on save, so cap it here rather than discover it on Regenerate.
        nonlocal so
        out.append({'schedule': 'A.1', 'section': section, 'line_code': code,
                    'label': label, 'value': q(value), 'sort_order': so,
                    'source_accounts': [], 'formula': formula[:200]})
        so += 1

    add('mcr', 'A1_MCR_01', 'Minimum Capital Requirement (BWP)', _p000(mcr),
        'NBFIRACapitalFactor(kind=mcr,key=default).')

    for cls in INSURANCE_CLASSES:
        add('irc', f'A1_IRC_{cls.upper()}', f'IRC — {CLASS_LABEL[cls]}',
            r['irc'][cls],
            f'25% × {CLASS_LABEL[cls]} assumed annual NWP × '
            f'(1 + {irc_factors[cls]})')

    add('irc', 'A1_IRC_TOTAL', 'Total IRC', r['irc_total'], 'Sum(IRC × class)')
    add('irc', 'A1_IRC_ADJ', 'IRC adjusted (share of combined risk)',
        r['irc_adj'],
        'Combined risk ((IRC+MER)² + MRC²)^0.5, apportioned on IRC')

    add('mer', 'A1_MER_TOTAL', 'Total MER', r['mer_total'],
        'Reinsurance treaty net event retention — entered, not derived.')
    add('mer', 'A1_MER_ADJ', 'MER adjusted (share of combined risk)',
        r['mer_adj'],
        'Combined risk apportioned on MER')

    for key, label, _fac in DEFAULT_MRC:
        add('mrc', f'A1_MRC_{key.upper()}', f'MRC — {label}', r['mrc'][key],
            f'Allocation to MRCTR × {mrc_factors[key]}')

    add('mrc', 'A1_MRC_TOTAL', 'Total MRC TC', r['mrc_total'],
        'Sum(MRC × bucket)')
    add('mrc', 'A1_MRC_ADJ', 'MRC TR adjusted (share of combined risk)',
        r['mrc_adj'], 'Combined risk apportioned on MRC')

    # g-factors are DERIVED from the asset mix, so they belong in the trace
    # rather than sitting in a settings table pretending to be constants.
    floored = r['pct'] == _p000(mcr) and r['risk_based'] < _p000(mcr)
    add('pct', 'A1_PCT', 'Prescribed Capital Target', r['pct'],
        'max(MCR, ((IRC_adj+MER_adj)/g_ins)² + (MRC_adj/g_mkt)²)^0.5; '
        f'derived g_ins {r["g_insurance"]:.4f}, g_mkt {r["g_market"]:.4f}'
        + ('; MCR floor applies — assumptions not entered' if floored else ''))

    return out


# ─────────────────────────────────────────────────────────────────────────
# Schedule B — YTD operating results (same shape as A, longer window)
# ─────────────────────────────────────────────────────────────────────────
def build_schedule_b(period_start: date, period_end: date,
                     company_id: Optional[str] = None) -> list[dict]:
    fy_start = _fy_start_for(period_end)
    return build_schedule_a(fy_start, period_end, company_id=company_id, ytd=True)


def _fy_start_for(d: date) -> date:
    """ADIC fiscal year starts 1 July."""
    y = d.year if d.month >= 7 else d.year - 1
    return date(y, 7, 1)


# ─────────────────────────────────────────────────────────────────────────
# Schedule C — Cell captive insurers (zeros for ADIC)
# ─────────────────────────────────────────────────────────────────────────
def build_schedule_c(period_start: date, period_end: date,
                     company_id: Optional[str] = None) -> list[dict]:
    out = []
    for i, (code, label) in enumerate(SCHEDULE_C):
        out.append({
            'schedule': 'C', 'section': 'cell_captive',
            'line_code': code, 'label': label,
            'value': ZERO, 'sort_order': i,
            'source_accounts': [],
            'formula': 'No cell captive arrangements — structural zeros.',
        })
    return out


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────
def generate_quarterly(period_start: date, period_end: date,
                       company_id: Optional[str] = None,
                       return_obj=None) -> list[dict]:
    """Produce ALL quarterly-return schedules as a single ordered list."""
    out: list[dict] = []
    out += build_schedule_is(period_start, period_end, company_id)
    out += build_schedule_a (period_start, period_end, company_id)
    a1   = build_schedule_a1(period_start, period_end, company_id,
                             return_obj=return_obj)
    out += a1
    # Stitch A.1's PCT back into A.A_SU_04 so the surplus line resolves.
    pct_line = next((l for l in a1 if l['line_code'] == 'A1_PCT'), None)
    if pct_line is not None:
        for line in out:
            if line['schedule'] == 'A' and line['line_code'] == 'A_SU_04':
                line['value'] = pct_line['value']
            if line['schedule'] == 'A' and line['line_code'] == 'A_SU_05':
                # surplus = net_assets - PCT
                pass    # recomputed in UI from neighbours
    out += build_schedule_b(period_start, period_end, company_id)
    out += build_schedule_c(period_start, period_end, company_id)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 — Annual Return builders
# ─────────────────────────────────────────────────────────────────────────
def build_schedule_afs(period_start: date, period_end: date,
                       company_id: Optional[str] = None) -> list[dict]:
    """AFS — Statement of Financial Position.

    Pulls BS totals from reporting.build_balance_sheet; individual lines
    seeded with 0 until Finance maps GL → AFS line (Phase 4 task in the
    Settings → AFS Mapping page). Total Assets / Total Liabilities pulled
    in directly so the AFS opens with the right two control totals.
    """
    from reporting.reports import build_balance_sheet
    try:
        bs = build_balance_sheet(period_end, company_id=company_id)
    except Exception:    # noqa: BLE001
        bs = {'totals': {}}
    t = (bs or {}).get('totals', {}) or {}
    total_assets = Decimal(str(t.get('total_assets', '0') or '0'))
    le           = Decimal(str(t.get('liabilities_and_equity', '0') or '0'))

    seeded = {
        'AFS_TA_01': total_assets,
        'AFS_TL_01': le,
    }
    out = []
    for i, (section, code, label) in enumerate(SCHEDULE_AFS):
        v = seeded.get(code, ZERO)
        out.append({
            'schedule': 'AFS', 'section': section,
            'line_code': code, 'label': label,
            'value': _p000(v) if section != 'ratio' else v,
            'sort_order': i,
            'source_accounts': [],
            'formula': 'Total Assets / Total L+E from build_balance_sheet; '
                       'individual rows seeded zero — Finance maps GL via '
                       'Settings → AFS Mapping.',
        })
    return out


def build_schedule_imf_assets(period_start: date, period_end: date,
                              company_id: Optional[str] = None) -> list[dict]:
    out = []
    for i, (code, label) in enumerate(SCHEDULE_IMF_ASSETS):
        out.append({
            'schedule': 'IMF_A', 'section': 'imf_assets',
            'line_code': code, 'label': label,
            'value': ZERO, 'sort_order': i,
            'source_accounts': [],
            'formula': 'Finance to map per A-line via Settings → IMF Assets Mapping.',
        })
    return out


def build_schedule_imf_liab(period_start: date, period_end: date,
                            company_id: Optional[str] = None) -> list[dict]:
    out = []
    for i, (code, label) in enumerate(SCHEDULE_IMF_LIAB):
        out.append({
            'schedule': 'IMF_L', 'section': 'imf_liab',
            'line_code': code, 'label': label,
            'value': ZERO, 'sort_order': i,
            'source_accounts': [],
            'formula': 'Finance to map per L-line via Settings → IMF Liabilities Mapping.',
        })
    return out


def generate_annual(period_start: date, period_end: date,
                    company_id: Optional[str] = None) -> list[dict]:
    """Produce ALL annual-return schedules as a single ordered list."""
    out: list[dict] = []
    out += build_schedule_afs       (period_start, period_end, company_id)
    out += build_schedule_imf_assets(period_start, period_end, company_id)
    out += build_schedule_imf_liab  (period_start, period_end, company_id)
    return out
