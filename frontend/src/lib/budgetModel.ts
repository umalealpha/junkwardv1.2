/**
 * budgetModel.ts — FY2026/27 budget model (deterministic, single source of truth).
 *
 * Reverse-engineered from Kago's "FY27 Budget PROFIT (updated).xlsx" (FY27 Budget P&L,
 * Revenue Bridge, Reinsurance, Acquisition Cost sheets) and anchored on omni's
 * May-2026 management-accounts balance sheet (ADIC actual). Verified to reproduce:
 *   - P&L Base (EBITDA -1.47 / PBT -5.98) and Cost-cut (EBITDA 4.74 / PBT 0.23)
 *     — updated 2026-07-13 for Kago's revised workbook (new Group management fee at 7.5% of
 *       GWP (P11.37m), EXCO salaries down P3.04m) PLUS a ring-fenced P0.50m AI & software
 *       provision (CFO 2026-07-13); base is a loss.
 *   - Signed 2026/27 treaty schedule (Kago 2026-07-03): Accident & Liability RETAINED;
 *     Property on the Fire & Eng Surplus (~24% cession, was flat 70%); Motor LR 65%; new scales
 *   - GWP by class summing to 151.56, overall cession ~63%
 *   - the May-2026 balance sheet (total assets 55.1, balanced)
 *
 * Four levers drive everything (P&L, products, balance sheet, cash flow):
 *   revenue → gwp ; loss → lossRatio ; reinsurance → cession ; expenses → opex
 *
 * All arithmetic lives here — NO LLM does the maths. The Aria advisor only
 * reasons over the numbers this produces.
 */

// ── Levers + P&L ──────────────────────────────────────────────────────────
export interface Levers { gwp: number; lossRatio: number; cession: number; opex: number }

export interface PnL {
  gwp: number; ceded: number; dUPR: number; nep: number
  grossClaims: number; recovered: number; netClaims: number
  propRecovered?: number; slRecovered?: number
  commission: number; acq: number; netAcq: number; grossProfit: number
  otherIncome: number; opex: number; ebitda: number
  provisions: number; deprec: number; ebit: number; financeCost: number
  pbt: number; tax: number; pat: number
  nepMargin: number; expenseRatio: number; commRate: number
}

export const TAX_RATE = 0.15   // CFO 2026-06-29: 15% flat effective rate on PBT

const K = {
  dUPR: 3.68, otherIncome: 2.646, provisions: 2.65, deprec: 1.88, financeCost: 0.02,
  baseRiskFee: 0.025 * 151.55,
  acqVarRate: 0.47 * 0.15, acqFixed: 7.5 + 0.9,
  commRate: 27.827 / 94.963,              // 0.293 base RI commission / ceded
  commSlope: 0.60,                        // sliding scale: lower loss ratio → higher commission
  recovK: (55.02 / 81.724) / (94.963 / 151.55),
  // Non-proportional XL/CAT stop-loss on the retained net account (recovery side only — the
  // XL/CAT premium is already inside the cession lever). ESTIMATE — XL/CAT slip pending; the
  // attach/limit are a portfolio proxy (real XL is per-risk), confirm with Kago/Arun.
  slAttachNepLr: 0.55,   // stop-loss attaches at a 55% net loss ratio (on NEP)
  slLimit: 12,           // P12m layer width (limit)
}

// Ring-fenced FY27 AI & software subscriptions provision (CFO 2026-07-13). Annualised FY25/26
// pure-AI credit-card run-rate (~P0.24m) plus headroom for FY27 AI scale-up. Added ON TOP of
// Kago's workbook opex so the AI cost is explicit and cannot be overlooked — previously it sat
// only inside the IT-expenses line at a generic 5% uplift.
export const AI_PROVISION = 0.5

// opex = Kago's revised FY27 base operating expenses (Operating Expenses MoM tab: new Group
// management fee 7.5% of GWP = P11.37m, EXCO salaries down to P9.16m) PLUS the P0.50m AI provision:
//   base    45.9214 + 0.50 = 46.4214
//   cost-cut 39.7114 + 0.50 = 40.2114   (base − P6.21m discretionary cuts, then + AI)
// The AI provision stays in the cost-cut case — AI tooling is operational, not discretionary.
export const BASE: Levers = { gwp: 151.55, lossRatio: 81.724 / 151.55, cession: 94.963 / 151.55, opex: 46.4214 }
export const COST_CUT: Levers = { ...BASE, opex: 40.2114 }
export const FY26 = { gwp: 133.11, ebitda: 9.08, pat: 1.66 }  // PAT post P3m bad-debts provision (was 4.66)
export const TARGET = { ebitda: 8.0, gwpFloor: 160.0, pat: 1.0 }

// Munich Re non-proportional (XL) LEAD terms — JB Boda renewal 1.7.2026/27, P Mn.
// EGNPI = est. gross net premium income (the XL premium base, net of the QS); MDP = minimum &
// deposit premium (what the XL costs). The cockpit models the recovery side; the MDP premium is
// already inside the cession lever.
export interface XlTreaty { cls: string; structure: string; limit: number; ded: number; egnpi: number; mdp: number }
export const MUNICH_XL: XlTreaty[] = [
  { cls: 'Motor XL',     structure: '4.0m xs 1.0m',  limit: 4.0,  ded: 1.0, egnpi: 19.89, mdp: 0.737 },
  { cls: 'Non-Motor XL', structure: '11.5m xs 0.5m', limit: 11.5, ded: 0.5, egnpi: 10.55, mdp: 1.018 },
]
export const XL_MDP = MUNICH_XL.reduce((s, x) => s + x.mdp, 0)   // 1.755 total XL premium

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** RI commission rate as a function of loss ratio (sliding-scale, treaty-blended). */
export function commRateFor(lossRatio: number): number {
  return clamp(K.commRate + K.commSlope * (BASE.lossRatio - lossRatio), 0.15, 0.45)
}

// Exact RI commission sliding scales — SIGNED 2026/27 treaty schedule (per JB Boda, Kago 2026-07-03).
// Commission % is read off each treaty's scale at the treaty's loss ratio. Points = (loss ratio, commission %).
const SCALES: Record<string, [number, number][]> = {
  'Motor QS':           [[0.50, 0.40], [0.75, 0.225]],                                 // signed: 40% @50% LR → 22.5% @75% (prov 25%)
  'Fire & Eng Surplus': [[0.0, 0.30], [2.0, 0.30]],                                    // Property surplus: 30% flat (+28.5% profit comm), ~24% cession
  'General QS':         [[0.0, 0.375], [0.60, 0.375], [0.6001, 0.325], [2.0, 0.325]],  // 37.5% to LR 60%, 32.5% above (+30% profit comm, ME 7.5%)
  'Legal Expenses QS':  [[0.0, 0.275], [2.0, 0.275]],                                  // 80% cession / 27.5% flat (Southern Summit Re)
  'Retained':           [[0.0, 0.0], [2.0, 0.0]],
}
/** Commission % off a treaty's sliding scale at the given loss ratio (piecewise-linear). */
export function commScale(treaty: string, lr: number): number {
  const pts = SCALES[treaty]
  if (!pts) return 0
  if (lr <= pts[0][0]) return pts[0][1]
  if (lr >= pts[pts.length - 1][0]) return pts[pts.length - 1][1]
  for (let i = 1; i < pts.length; i++) {
    if (lr <= pts[i][0]) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]; return y0 + (y1 - y0) * (lr - x0) / (x1 - x0) }
  }
  return pts[pts.length - 1][1]
}
// RECONCILE_COMM (FAC + General-QS profit commission, fixed add-on) is defined after
// computeProducts so it self-calibrates to tie the base aggregate commission to P39.05m.

export function compute(l: Levers): PnL {
  const gwp = l.gwp
  const ceded = gwp * l.cession
  const nep = gwp - ceded + K.dUPR
  const grossClaims = gwp * l.lossRatio
  const propRecovered = grossClaims * (l.cession * K.recovK)   // proportional (QS / surplus) treaty
  // Non-proportional XL/CAT stop-loss — the recovery that "kicks in" when the loss ratio spikes
  // (the proportional treaty alone lets the net retained loss run away). Recovers retained net
  // claims above the attachment, capped at the layer limit; zero at the base loss ratio, so the
  // base scenario is unchanged.
  const slRecovered = clamp((grossClaims - propRecovered) - K.slAttachNepLr * nep, 0, K.slLimit)
  const recovered = propRecovered + slRecovered
  const netClaims = grossClaims - recovered
  // Commission = sum of the per-treaty sliding-scale commissions (exact scales) + the fixed
  // FAC/profit-commission add-on, so it tracks ceded + loss ratio per the real treaties.
  const commission = computeProducts(l).totals.commission + RECONCILE_COMM
  const commRate = ceded ? commission / ceded : 0
  const acq = K.acqVarRate * gwp + K.acqFixed
  const netAcq = commission - acq
  const grossProfit = nep - netClaims + netAcq
  const opex = l.opex - K.baseRiskFee + 0.025 * gwp
  const ebitda = grossProfit + K.otherIncome - opex
  const ebit = ebitda - K.provisions - K.deprec
  const pbt = ebit + K.financeCost
  const tax = Math.max(0, pbt) * TAX_RATE
  const pat = pbt - tax
  return {
    gwp, ceded, dUPR: K.dUPR, nep, grossClaims, propRecovered, slRecovered, recovered, netClaims, commission, acq, netAcq,
    grossProfit, otherIncome: K.otherIncome, opex, ebitda, provisions: K.provisions, deprec: K.deprec,
    ebit, financeCost: K.financeCost, pbt, tax, pat,
    nepMargin: nep ? ebitda / nep : 0,
    expenseRatio: gwp ? (opex + K.deprec) / gwp : 0,
    commRate,
  }
}

// ── 3D waterfall steps (GWP → PAT) ─────────────────────────────────────────
export interface WaterfallStep { key: string; label: string; value: number; kind: 'start' | 'add' | 'sub' | 'total' }

export function waterfall(p: PnL): WaterfallStep[] {
  return [
    { key: 'gwp', label: 'GWP', value: p.gwp, kind: 'start' },
    { key: 'ceded', label: 'Ceded to RI', value: -p.ceded, kind: 'sub' },
    { key: 'dupr', label: 'Δ UPR', value: p.dUPR, kind: 'add' },
    { key: 'nep', label: 'Net Earned Prem', value: p.nep, kind: 'total' },
    { key: 'netclaims', label: 'Net Claims', value: -p.netClaims, kind: 'sub' },
    { key: 'netacq', label: 'Net Acq Income', value: p.netAcq, kind: p.netAcq >= 0 ? 'add' : 'sub' },
    { key: 'gp', label: 'Gross Profit', value: p.grossProfit, kind: 'total' },
    { key: 'oi', label: 'Other Income', value: p.otherIncome, kind: 'add' },
    { key: 'opex', label: 'Operating Exp', value: -p.opex, kind: 'sub' },
    { key: 'ebitda', label: 'EBITDA', value: p.ebitda, kind: 'total' },
    { key: 'provdep', label: 'Prov + Deprec', value: -(p.provisions + p.deprec), kind: 'sub' },
    { key: 'fin', label: 'Finance', value: p.financeCost, kind: 'add' },
    { key: 'pat', label: 'PAT', value: p.pat, kind: 'total' },
  ]
}

// ── Product / reinsurance book (proportional treaty, from the Reinsurance sheet) ──
export interface ProductBase {
  key: string; name: string; gwp: number; cession: number; lossRatio: number
  recoveryPct: number; commPct: number; treaty: string
}

export const PRODUCTS: ProductBase[] = [
  { key: 'motor',      name: 'Motor',                     gwp: 81.05, cession: 0.70, lossRatio: 0.650, recoveryPct: 0.70, commPct: 0.295, treaty: 'Motor QS' },
  { key: 'property',   name: 'Property',                  gwp: 21.67, cession: 0.24, lossRatio: 0.650, recoveryPct: 0.24, commPct: 0.300, treaty: 'Fire & Eng Surplus' },   // signed: on the Surplus at ~24% (was flat 70% QS)
  { key: 'accident',   name: 'Accident',                  gwp: 14.54, cession: 0.00, lossRatio: 0.071, recoveryPct: 0.00, commPct: 0.000, treaty: 'Retained' },   // FULLY RETAINED (CFO 2026-06-29) — low-loss 7.1% book kept in-house, +~P3.7m
  { key: 'hospital',   name: 'Hospital Cashback & Legal', gwp: 7.33,  cession: 0.80, lossRatio: 0.072, recoveryPct: 0.80, commPct: 0.275, treaty: 'Legal Expenses QS' },
  { key: 'misc',       name: 'Miscellaneous',             gwp: 7.29,  cession: 0.70, lossRatio: 0.454, recoveryPct: 0.70, commPct: 0.375, treaty: 'General QS' },
  { key: 'unionlegal', name: 'Union Legal scheme',        gwp: 6.69,  cession: 0.80, lossRatio: 0.757, recoveryPct: 0.80, commPct: 0.275, treaty: 'Legal Expenses QS' },
  { key: 'transport',  name: 'Transportation',            gwp: 5.07,  cession: 0.70, lossRatio: 0.197, recoveryPct: 0.70, commPct: 0.375, treaty: 'General QS' },
  { key: 'health',     name: 'Health (new BU)',           gwp: 3.24,  cession: 0.56, lossRatio: 0.548, recoveryPct: 0.90, commPct: 0.000, treaty: 'Retained' },
  { key: 'liability',  name: 'Liability',                 gwp: 4.03,  cession: 0.00, lossRatio: 0.428, recoveryPct: 0.00, commPct: 0.000, treaty: 'Retained' },
  { key: 'guarantee',  name: 'Guarantee',                 gwp: 0.65,  cession: 0.70, lossRatio: 0.804, recoveryPct: 0.70, commPct: 0.375, treaty: 'General QS' },
]

export interface ProductResult extends ProductBase {
  ceded: number; netWritten: number; grossClaims: number; recovered: number
  netClaims: number; commission: number; netContribution: number; share: number
}

// Commission sliding-scale steepness per treaty (Δcommission% per 1.0 Δloss-ratio).
// ESTIMATE from the Reinsurance tab: Motor QS steep (55%→42%..75%→24% ≈ 0.9), Fire&Eng
// Surplus moderate (35%→32.5%..65%→20% ≈ 0.42), General QS / Legal QS ~flat, Retained 0.
// Exact contractual scales to be confirmed by Kago/Pako.
export const TREATY_SLOPE: Record<string, number> = {
  'Motor QS': 0.90, 'Fire & Eng Surplus': 0.42, 'General QS': 0.15, 'Legal Expenses QS': 0.0, 'Retained': 0.0,
}

/** Per-class book scaled by the global levers (revenue preserves mix; loss & cession scale each class). */
export function computeProducts(l: Levers): { rows: ProductResult[]; totals: ProductResult } {
  const gMul = l.gwp / BASE.gwp
  const lrMul = l.lossRatio / BASE.lossRatio
  const cesMul = l.cession / BASE.cession
  const rows: ProductResult[] = PRODUCTS.map(p => {
    const gwp = p.gwp * gMul
    const cession = p.cession ? clamp(p.cession * cesMul, 0, 0.95) : 0
    const lossRatio = clamp(p.lossRatio * lrMul, 0, 2)
    const ceded = gwp * cession
    const netWritten = gwp - ceded
    const grossClaims = gwp * lossRatio
    const recovered = grossClaims * p.recoveryPct * (cession ? cession / (p.cession || 1) : 0)
    const netClaims = grossClaims - recovered
    // Commission read off the treaty's EXACT sliding scale (Arun's proposed structure,
    // Reinsurance tab), anchored to the workbook's base commission % so it ties at base.
    const commPct = p.commPct ? clamp(commScale(p.treaty, lossRatio) + (p.commPct - commScale(p.treaty, p.lossRatio)), 0, 0.5) : 0
    const commission = ceded * commPct
    const netContribution = netWritten - netClaims + commission
    return { ...p, gwp, cession, lossRatio, ceded, netWritten, grossClaims, recovered, netClaims, commission, netContribution, share: 0 }
  })
  const sum = (f: (r: ProductResult) => number) => rows.reduce((a, r) => a + f(r), 0)
  const totGwp = sum(r => r.gwp)
  rows.forEach(r => { r.share = totGwp ? r.gwp / totGwp : 0 })
  const totals: ProductResult = {
    key: 'total', name: 'Total (proportional)', treaty: '', recoveryPct: 0, commPct: 0,
    gwp: totGwp, cession: totGwp ? sum(r => r.ceded) / totGwp : 0,
    lossRatio: sum(r => r.grossClaims) / totGwp,
    ceded: sum(r => r.ceded), netWritten: sum(r => r.netWritten), grossClaims: sum(r => r.grossClaims),
    recovered: sum(r => r.recovered), netClaims: sum(r => r.netClaims), commission: sum(r => r.commission),
    netContribution: sum(r => r.netContribution), share: 1,
  }
  return { rows, totals }
}

// FAC + General-QS profit commission (settled in arrears) — fixed add-on, calibrated so the
// aggregate P&L commission ties to the workbook's P27.83m at base. compute() uses it; defined
// here so it can self-calibrate off the base proportional-treaty commission.
export const RECONCILE_COMM = 27.827 - computeProducts(BASE).totals.commission

// ── Monthly P&L — phased on the team's REAL revenue ramp (FY27 Revenue MoM tab),
// not ÷12. Incremental monthly GWP (cumulative-YTD differenced) sums to the GWP target.
export const MONTHS = ['Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun']
const MONTHLY_GWP_INC = [16.328, 10.217, 12.583, 15.258, 13.284, 11.180, 15.969, 9.849, 9.649, 13.327, 10.962, 12.949]

export interface MonthRow {
  label: string; gwp: number; ceded: number; nep: number
  grossClaims: number; recovered: number; netClaims: number
  commission: number; netAcq: number; opex: number; ebitda: number; pat: number
}
/**
 * Monthly P&L: revenue-linked lines (GWP, ceded, gross claims, RI recoveries, commission,
 * acquisition) phased by each month's share of the ramp; fixed lines (UPR, opex, other income,
 * provisions, deprec, finance, tax) spread ÷12. Sums exactly to the annual budget P&L — derived
 * from compute(l). Gross claims + recoveries shown separately so the RI cover is visible.
 */
export function computeMonthly(l: Levers): { months: MonthRow[]; fy: MonthRow } {
  const p = compute(l)
  const tot = MONTHLY_GWP_INC.reduce((a, b) => a + b, 0)
  const mk = (s: number, label: string): MonthRow => {
    const gwp = p.gwp * s, ceded = p.ceded * s
    const grossClaims = p.grossClaims * s, recovered = p.recovered * s, netClaims = p.netClaims * s
    const commission = p.commission * s, acq = p.acq * s
    const nep = gwp - ceded + p.dUPR / 12
    const netAcq = commission - acq
    const opex = p.opex / 12
    const ebitda = (nep - netClaims + netAcq) + p.otherIncome / 12 - opex
    const pat = ebitda - (p.provisions + p.deprec) / 12 + p.financeCost / 12 - p.tax / 12
    return { label, gwp, ceded, nep, grossClaims, recovered, netClaims, commission, netAcq, opex, ebitda, pat }
  }
  const months = MONTHS.map((label, i) => mk(MONTHLY_GWP_INC[i] / tot, label))
  const fy: MonthRow = {
    label: 'Full Year', gwp: p.gwp, ceded: p.ceded, nep: p.nep,
    grossClaims: p.grossClaims, recovered: p.recovered, netClaims: p.netClaims,
    commission: p.commission, netAcq: p.netAcq, opex: p.opex, ebitda: p.ebitda, pat: p.pat,
  }
  return { months, fy }
}

// Real per-class monthly GWP ramp (incremental, from the workbook's "FY27 Revenue MoM" tab) —
// each class has its OWN seasonality (Motor front-loaded on July renewals; Health BU ramps all
// year). Phases the by-product monthly view so each class matches Kago's actual file. Each row
// sums to the class FY GWP; the column sums equal MONTHLY_GWP_INC.
const MONTHLY_CLASS_INC: Record<string, number[]> = {
  motor:      [8.721, 6.125, 6.561, 8.431, 6.164, 6.351, 7.538, 5.701, 5.318, 6.690, 6.699, 6.755],
  property:   [3.092, 1.057, 1.855, 2.880, 1.527, 1.086, 3.007, 1.025, 0.903, 2.527, 0.906, 1.806],
  accident:   [1.481, 0.973, 1.225, 1.265, 1.496, 1.097, 1.952, 0.913, 0.980, 1.012, 0.935, 1.212],
  hospital:   [0.631, 0.568, 0.627, 0.738, 0.606, 0.760, 0.611, 0.536, 0.636, 0.529, 0.478, 0.611],
  misc:       [0.864, 0.326, 0.703, 0.583, 0.697, 0.448, 1.088, 0.374, 0.382, 0.854, 0.360, 0.607],
  unionlegal: [0.578, 0.586, 0.591, 0.589, 0.589, 0.586, 0.591, 0.585, 0.589, 0.585, 0.262, 0.558],
  transport:  [0.538, 0.319, 0.452, 0.371, 0.644, 0.370, 0.510, 0.220, 0.257, 0.390, 0.572, 0.422],
  health:     [0.030, 0.062, 0.098, 0.135, 0.177, 0.221, 0.271, 0.323, 0.381, 0.444, 0.513, 0.588],
  liability:  [0.338, 0.172, 0.421, 0.208, 1.363, 0.182, 0.333, 0.139, 0.164, 0.252, 0.121, 0.336],
  guarantee:  [0.055, 0.029, 0.050, 0.058, 0.021, 0.079, 0.068, 0.033, 0.039, 0.044, 0.116, 0.054],
}

// ── Monthly × PRODUCT breakdown (CFO 2026-06-29: "monthly Productwise GWP, RI and Claims") ──
// Each class's annual GWP / ceded (RI) / gross claims / recoveries / net claims / commission
// (from computeProducts) phased on the class's OWN monthly ramp (above). Ties both ways: every
// class row sums to its full-year book (= the Reinsurance-by-class table); the GWP column totals
// equal the consolidated monthly. Ceded/claims/commission are the proportional-treaty book; the
// P&L layers XL/CAT premium + facultative + profit commission on top (see the Reinsurance note).
export type MonthlyMetric = 'gwp' | 'ceded' | 'grossClaims' | 'recovered' | 'netClaims' | 'commission'
export interface MonthlyProductRow {
  key: string; name: string; treaty: string
  months: Record<MonthlyMetric, number[]>
  fy: Record<MonthlyMetric, number>
}
const M_METRIC_KEYS: MonthlyMetric[] = ['gwp', 'ceded', 'grossClaims', 'recovered', 'netClaims', 'commission']
export function computeMonthlyProducts(l: Levers): { rows: MonthlyProductRow[]; totals: MonthlyProductRow } {
  const { rows } = computeProducts(l)
  const fallback = (() => { const t = MONTHLY_GWP_INC.reduce((a, b) => a + b, 0); return MONTHLY_GWP_INC.map(x => x / t) })()
  const prows: MonthlyProductRow[] = rows.map(p => {
    const inc = MONTHLY_CLASS_INC[p.key]
    const tot = inc ? inc.reduce((a, b) => a + b, 0) : 0
    const shares = inc && tot ? inc.map(x => x / tot) : fallback   // each class phased on its own ramp
    const phase = (annual: number) => shares.map(s => annual * s)
    return {
      key: p.key, name: p.name, treaty: p.treaty,
      months: { gwp: phase(p.gwp), ceded: phase(p.ceded), grossClaims: phase(p.grossClaims), recovered: phase(p.recovered), netClaims: phase(p.netClaims), commission: phase(p.commission) },
      fy: { gwp: p.gwp, ceded: p.ceded, grossClaims: p.grossClaims, recovered: p.recovered, netClaims: p.netClaims, commission: p.commission },
    }
  })
  const sumM = (m: MonthlyMetric) => MONTHS.map((_, i) => prows.reduce((a, r) => a + r.months[m][i], 0))
  const sumFy = (m: MonthlyMetric) => prows.reduce((a, r) => a + r.fy[m], 0)
  const blank = <T,>(fill: (m: MonthlyMetric) => T) => Object.fromEntries(M_METRIC_KEYS.map(m => [m, fill(m)])) as Record<MonthlyMetric, T>
  const totals: MonthlyProductRow = {
    key: 'total', name: 'Total (proportional)', treaty: '',
    months: blank(sumM), fy: blank(sumFy),
  }
  return { rows: prows, totals }
}

// ── Balance sheet (FY25 actual anchor) + roll-forward + indirect cash flow ──
// FY25 actuals from omni build_ma_balance_sheet(2025-06-30, ADIC), in P Mn.
export const MA_GWP = 133.1   // FY26 annualised actual basis (May-2026 YTD 122.0 ×12/11)

export interface BS {
  // assets
  cash: number; tradeRec: number; otherRec: number; riProvisions: number
  relatedParty: number; staffLoans: number; subrogation: number; salvage: number; deferredTax: number
  netPPE: number; rou: number
  // liabilities
  upr: number; claimsPayable: number; ibnr: number; dueToRI: number; tradePayables: number
  wht: number; vat: number; severance: number; taxPayable: number
  shortLoan: number; longLoan: number; leaseShort: number; leaseLong: number
  // equity
  statedCapital: number; retainedEarnings: number; ibnrReserve: number; patYear: number
}

// Balance-sheet anchor = May-2026 management accounts (ADIC actual), P Mn.
// Canonical per the MA workbook (total assets 55.1, balanced). Liabilities held positive.
export const MA_BS: BS = {
  cash: 7.997, tradeRec: 17.50, otherRec: 1.02, riProvisions: 9.63,
  relatedParty: 6.36, staffLoans: 0.36, subrogation: 2.17, salvage: 2.06, deferredTax: 2.12,
  netPPE: 5.72, rou: 0.20,
  upr: 3.83, claimsPayable: 11.70, ibnr: 4.77, dueToRI: 4.31, tradePayables: 4.39,
  wht: 0.02, vat: 1.21, severance: 0.47, taxPayable: 1.00,
  shortLoan: 0.39, longLoan: 1.47, leaseShort: 0.89, leaseLong: 1.71,
  // retainedEarnings carries a tiny rounding plug (-16.933 vs the MA's -16.96) so the
  // anchor balances to the cent (assets 55.137 = L+E); otherwise the ~P27k rounding
  // residual leaks into the cash-flow reconciliation. Invisible at 1-dp display.
  statedCapital: 26.50, retainedEarnings: -16.933, ibnrReserve: 5.12, patYear: 4.29,
}

// FY26 actual (May-2026 MA, 11-month YTD) — P&L + ratio reference for comparison.
export const FY26_ACTUAL = {
  gwp: 122.02, ceded: 69.527, dUPR: 4.001, nep: 56.494, grossClaims: 71.674, recovered: 42.941,
  netClaims: 26.172, commission: 19.178, acq: 13.130, netAcq: 6.048, grossProfit: 36.37,
  otherIncome: 2.246, opex: 30.276, provisions: 2.425, deprec: 1.642, ebitda: 2.916, pat: 1.291,   // net of a P3m bad-debts provision (11-mo, CFO 2026-07-03) — within EBITDA per MA basis
  // ratios (MA Ratios sheet, FY26 actual)
  grossLossRatio: 0.587, netLossRatio: 0.463, expenseRatio: 0.262, cession: 0.570,
  ebitdaMarginGwp: 2.916 / 122.02, patMargin: 1.291 / 122.02, dToE: 1.909,
  combinedRatio: 0.832, roe: 0.226, solvency: 0.344,
}

// FY26 budget (MA Ratios sheet, budget column) — secondary comparator.
export const FY26_BUDGET_RATIOS = { grossLossRatio: 0.493, expenseRatio: 0.221, cession: 0.635, ebitdaMarginNep: 0.064, combinedRatio: 0.788 }

export const bsTotals = (b: BS) => {
  const assets = b.cash + b.tradeRec + b.otherRec + b.riProvisions + b.relatedParty + b.staffLoans +
    b.subrogation + b.salvage + b.deferredTax + b.netPPE + b.rou
  const liabilities = b.upr + b.claimsPayable + b.ibnr + b.dueToRI + b.tradePayables + b.wht + b.vat +
    b.severance + b.taxPayable + b.shortLoan + b.longLoan + b.leaseShort + b.leaseLong
  const equity = b.statedCapital + b.retainedEarnings + b.ibnrReserve + b.patYear
  return { assets, liabilities, equity, le: liabilities + equity }
}

export interface CFLine { label: string; amount: number }
export interface CashFlow {
  operating: CFLine[]; investing: CFLine[]; financing: CFLine[]
  netOperating: number; netInvesting: number; netFinancing: number
  openingCash: number; netChange: number; closingCash: number
}

export interface CashAssumptions { capex: number; debtRepay: number; oneOffCapex: number; collections: number }
// collections = fractional reduction in trade/other receivables from better collection
// (0 = as-is; 0.5 = receivables halved → one-off cash release). CFO 2026-06-29.
export const CASH_DEFAULTS: CashAssumptions = { capex: 1.88, debtRepay: 1.30, oneOffCapex: 0, collections: 0 }

/**
 * Roll one year forward. Non-cash items scale with GWP; equity grows by PAT;
 * cash is the balancing plug (so the closing BS always balances). The cash-flow
 * statement is the indirect-method decomposition of that cash movement.
 */
export function rollForward(open: BS, gwp: number, pat: number, deprec: number, a: CashAssumptions): { close: BS; cf: CashFlow } {
  const g = gwp / MA_GWP                       // scale vs FY26 actual basis
  const ratio = (base: number) => base * g     // scale an anchor line by current GWP

  const close: BS = { ...open }
  // assets that scale with GWP — receivables also reduced by the collections-improvement lever
  const coll = 1 - (a.collections || 0)
  close.tradeRec = ratio(MA_BS.tradeRec) * coll
  close.otherRec = ratio(MA_BS.otherRec) * coll
  close.riProvisions = ratio(MA_BS.riProvisions)
  close.subrogation = ratio(MA_BS.subrogation)
  close.salvage = ratio(MA_BS.salvage)
  // assets held flat
  close.relatedParty = open.relatedParty; close.staffLoans = open.staffLoans; close.deferredTax = open.deferredTax
  close.netPPE = open.netPPE + a.capex + a.oneOffCapex - deprec
  close.rou = open.rou
  // liabilities that scale with GWP
  close.upr = ratio(MA_BS.upr)
  close.claimsPayable = ratio(MA_BS.claimsPayable)
  close.ibnr = ratio(MA_BS.ibnr)
  close.dueToRI = ratio(MA_BS.dueToRI)
  close.tradePayables = ratio(MA_BS.tradePayables)
  // liabilities held / amortised
  close.wht = open.wht; close.vat = open.vat; close.severance = open.severance; close.taxPayable = open.taxPayable
  const repay = a.debtRepay
  close.shortLoan = Math.max(0, open.shortLoan - repay * 0.2)
  close.longLoan = Math.max(0, open.longLoan - repay * 0.4)
  close.leaseShort = Math.max(0, open.leaseShort - repay * 0.15)
  close.leaseLong = Math.max(0, open.leaseLong - repay * 0.25)
  // equity
  close.statedCapital = open.statedCapital
  close.ibnrReserve = open.ibnrReserve
  close.retainedEarnings = open.retainedEarnings + open.patYear   // last year's PAT rolls into RE
  close.patYear = pat
  // cash = plug so the sheet balances
  const t = bsTotals(close)
  const nonCashAssets = t.assets - close.cash
  close.cash = t.le - nonCashAssets

  // indirect cash-flow decomposition (sums exactly to Δcash)
  const dAsset = (k: keyof BS) => (close[k] as number) - (open[k] as number)
  const operating: CFLine[] = [
    { label: 'Profit after tax', amount: pat },
    { label: 'Depreciation', amount: deprec },
    { label: 'Δ Trade & other receivables', amount: -(dAsset('tradeRec') + dAsset('otherRec')) },
    { label: 'Δ Reinsurance & recovery assets', amount: -(dAsset('riProvisions') + dAsset('subrogation') + dAsset('salvage')) },
    { label: 'Δ Unearned premium reserve', amount: dAsset('upr') },
    { label: 'Δ Claims payable + IBNR', amount: dAsset('claimsPayable') + dAsset('ibnr') },
    { label: 'Δ Reinsurer & trade payables', amount: dAsset('dueToRI') + dAsset('tradePayables') },
  ]
  const investing: CFLine[] = [
    { label: 'Purchase of property & equipment', amount: -(a.capex + a.oneOffCapex) },
  ]
  const financing: CFLine[] = [
    { label: 'Loan repayments', amount: dAsset('shortLoan') + dAsset('longLoan') },
    { label: 'Lease repayments', amount: dAsset('leaseShort') + dAsset('leaseLong') },
  ]
  const sum = (a: CFLine[]) => a.reduce((s, x) => s + x.amount, 0)
  const netOperating = sum(operating), netInvesting = sum(investing), netFinancing = sum(financing)
  return {
    close,
    cf: {
      operating, investing, financing, netOperating, netInvesting, netFinancing,
      openingCash: open.cash, netChange: netOperating + netInvesting + netFinancing, closingCash: close.cash,
    },
  }
}

// ── Ratios (for the KPI gauges) ─────────────────────────────────────────────
// GWP-based where possible — robust across the budget's high (~69%) cession,
// which shrinks NEP and distorts NEP-based margins.
export interface Ratios {
  grossLossRatio: number; netLossRatio: number; expenseRatio: number; cession: number
  ebitdaMarginGwp: number; ebitdaMarginNep: number; patMargin: number; combinedRatio: number; dToE: number
}
export function ratios(p: PnL, bs: BS): Ratios {
  const t = bsTotals(bs)
  return {
    grossLossRatio: p.gwp ? p.grossClaims / p.gwp : 0,
    netLossRatio: p.nep ? p.netClaims / p.nep : 0,
    expenseRatio: p.gwp ? (p.opex + p.deprec) / p.gwp : 0,
    cession: p.gwp ? p.ceded / p.gwp : 0,
    ebitdaMarginGwp: p.gwp ? p.ebitda / p.gwp : 0,
    ebitdaMarginNep: p.nep ? p.ebitda / p.nep : 0,
    patMargin: p.gwp ? p.pat / p.gwp : 0,
    combinedRatio: p.nep ? (p.netClaims - p.netAcq + p.opex + p.deprec) / p.nep : 0,
    dToE: t.equity ? t.liabilities / t.equity : 0,
  }
}

/** FY26 actual P&L (May-2026 MA, 11-month YTD) as a PnL shape. */
export const FY26_PNL: PnL = {
  gwp: FY26_ACTUAL.gwp, ceded: FY26_ACTUAL.ceded, dUPR: FY26_ACTUAL.dUPR, nep: FY26_ACTUAL.nep,
  grossClaims: FY26_ACTUAL.grossClaims, recovered: FY26_ACTUAL.recovered, netClaims: FY26_ACTUAL.netClaims,
  commission: FY26_ACTUAL.commission, acq: FY26_ACTUAL.acq, netAcq: FY26_ACTUAL.netAcq,
  grossProfit: FY26_ACTUAL.grossProfit, otherIncome: FY26_ACTUAL.otherIncome, opex: FY26_ACTUAL.opex,
  ebitda: FY26_ACTUAL.ebitda, provisions: FY26_ACTUAL.provisions, deprec: FY26_ACTUAL.deprec,
  // FY26 (MA basis) reports provisions WITHIN EBITDA, so EBIT = EBITDA − depreciation
  // (≠ FY27/Kago basis, which shows provisions below EBITDA). Both bridge to their own PAT.
  ebit: FY26_ACTUAL.ebitda - FY26_ACTUAL.deprec, financeCost: 0.017,
  pbt: FY26_ACTUAL.pat, tax: 0, pat: FY26_ACTUAL.pat,   // MA: Taxation nil
  nepMargin: FY26_ACTUAL.ebitda / FY26_ACTUAL.nep,
  expenseRatio: (FY26_ACTUAL.opex + FY26_ACTUAL.deprec) / FY26_ACTUAL.gwp,
  commRate: FY26_ACTUAL.commission / FY26_ACTUAL.ceded,
}

/** FY26 full-year FORECAST (Kago's FY27-workbook "FY26 Forecast" column; provisions below EBITDA). */
export const FY26_FORECAST: PnL = {
  gwp: 133.11, ceded: 75.85, dUPR: 4.36, nep: 61.62,
  grossClaims: 78.19, recovered: 49.64, netClaims: 28.55,
  commission: 20.92, acq: 14.33, netAcq: 6.59, grossProfit: 39.66,
  otherIncome: 2.45, opex: 33.03, ebitda: 9.08, provisions: 5.65, deprec: 1.79,   // provisions incl. P3m bad-debts (CFO 2026-07-03) — below EBITDA per Kago basis
  ebit: 1.64, financeCost: 0.02, pbt: 1.66, tax: 0, pat: 1.66,
  nepMargin: 9.08 / 61.62, expenseRatio: (33.03 + 1.79) / 133.11, commRate: 20.92 / 75.85,
}

export interface YearSet {
  fy26: { pnl: PnL; bs: BS; ratios: Ratios }                                  // actual anchor (May-2026 MA)
  fy27: { pnl: PnL; bs: BS; cf: CashFlow; products: ReturnType<typeof computeProducts>; ratios: Ratios }
}

/** FY26 = actual (May-2026 MA); FY27 = lever-driven, balance sheet rolled off the FY26 actual. */
export function buildYears(l: Levers, a: CashAssumptions = CASH_DEFAULTS): YearSet {
  const fy27pnl = compute(l)
  const r27 = rollForward(MA_BS, l.gwp, fy27pnl.pat, fy27pnl.deprec, { ...a, oneOffCapex: 0.06 })
  return {
    fy26: { pnl: FY26_PNL, bs: MA_BS, ratios: ratios(FY26_PNL, MA_BS) },
    fy27: { pnl: fy27pnl, bs: r27.close, cf: r27.cf, products: computeProducts(l), ratios: ratios(fy27pnl, r27.close) },
  }
}

// ── formatters ──────────────────────────────────────────────────────────────
export const fmt = (v: number, dp = 1) => `P${v.toFixed(dp)}m`
export const pct = (v: number, dp = 1) => `${(v * 100).toFixed(dp)}%`
export const signed = (v: number, dp = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(dp)}`
