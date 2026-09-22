/**
 * fiveYearModel.ts — Alpha Direct Insurtech PTE Ltd, FY2026–FY2030.
 *
 * Deterministic engine for the 5-Year Plan Cockpit. Same shape as budgetModel.ts:
 * the page owns no arithmetic, Aria advises and never computes.
 *
 * SOURCE: AD_Insurtech_5Yr_Model_CONSOLIDATED.xlsx (Finance, 3 Aug 2026).
 * FY2025 is audited (Forvis Mazars); FY26+ per the workbook's Assumptions tab.
 * Implemented AS-IS — no figure in the base layer deviates from the workbook.
 * Every transformation is a visible, labelled line.
 *
 * WHY SEGMENT-LEVEL AND NOT GROUP TOTALS: the six segment ON/OFF switches have to
 * actually remove a segment from every table, chart and KPI. That is only possible
 * if the model is built up from per-segment figures, so it is. The draft cockpit
 * Finance circulated held group totals only, which meant the switches could not
 * really recompute anything.
 *
 * KNOWN WORKBOOK VARIANCE (do not silently "fix"): the Segment Contribution sheet
 * puts FY26 GI revenue at 150,350,083.6 while Revenue by Segment puts total GI GWP
 * at 150,850,083.6 — P500,000 apart, carried into the group total. Consolidated P&L
 * agrees with Segment Contribution, so that is the basis used here and the P&L ties.
 * Flagged to Finance as one of the "minor corrections for a later date".
 */

export const YEARS = ['FY2025A', 'FY2026E', 'FY2027E', 'FY2028E', 'FY2029E', 'FY2030E'] as const
export type YearIdx = 0 | 1 | 2 | 3 | 4 | 5

export type SegmentId = 'gi' | 'health' | 'life' | 'neobank' | 'sa' | 'ri'

export interface Segment {
  id: SegmentId
  name: string
  /** Workbook revenue, BWP, FY25A → FY30E. */
  revenue: number[]
  /** Workbook net profit after tax, BWP, FY25A → FY30E. */
  pat: number[]
  /** Lives / policies / users the segment touches. Feeds Total Lives Impacted. */
  lives: number[]
  /** Workbook tax rate. GI 15%, everything else 22%. */
  taxRate: number
}

/** Per-segment, straight from Segment Contribution + Revenue by Segment. */
export const SEGMENTS: Segment[] = [
  {
    id: 'gi', name: 'General Insurance', taxRate: 0.15,
    revenue: [128_539_320.0, 150_350_083.6, 177_403_098.6, 204_203_563.4, 230_915_026.7, 258_722_829.9],
    pat: [2_379_739.0, 5_191_719.6, 21_851_278.5, 29_097_030.0, 36_714_574.1, 44_583_835.0],
    lives: [41_943, 46_845, 52_819, 58_876, 66_019, 72_460],
  },
  {
    id: 'health', name: 'AD Health', taxRate: 0.22,
    revenue: [0, 1_500_000.0, 34_888_086.7, 69_196_686.5, 98_198_328.6, 112_231_030.3],
    pat: [0, -1_941_225.0, 1_162_553.0, 6_333_627.7, 10_727_209.5, 12_140_620.7],
    lives: [0, 3_600, 10_488, 17_454, 21_076, 22_960],
  },
  {
    id: 'life', name: 'AD Life & Funeral', taxRate: 0.22,
    revenue: [0, 0, 0, 7_560_000.0, 23_079_168.0, 38_792_784.0],
    pat: [0, 0, 0, 2_228_460.0, 5_052_541.9, 8_038_070.4],
    lives: [0, 0, 0, 5_250, 9_870, 14_106],
  },
  {
    id: 'neobank', name: 'AD NeoBank', taxRate: 0.22,
    revenue: [0, 0, 0, 0, 768_375.0, 4_158_783.0],
    pat: [0, 0, 0, -4_300_000.0, -6_076_625.0, -6_138_805.0],
    lives: [0, 0, 0, 3_000, 9_000, 23_940],
  },
  {
    id: 'sa', name: 'AD South Africa', taxRate: 0.22,
    revenue: [0, 0, 1_850_201.0, 7_905_117.0, 18_193_383.0, 30_082_877.0],
    pat: [0, 0, 153_939.2, 2_028_574.1, 5_583_478.7, 9_965_670.8],
    lives: [0, 0, 6_646, 23_494, 50_950, 82_150],
  },
  {
    id: 'ri', name: 'AD Reinsurance Co', taxRate: 0.22,
    revenue: [0, 0, 41_867_354.0, 45_007_405.0, 48_382_961.0, 52_011_683.0],
    pat: [0, 0, 6_061_564.1, 7_925_277.4, 9_940_920.6, 12_001_133.0],
    lives: [0, 0, 0, 0, 0, 0],
  },
]

/** The levers. Base values are the workbook's own assumptions, untouched. */
export interface Levers {
  /** GI GWP growth for FY26. Later years keep the workbook's taper off this. */
  giGrowth: number
  /** Gross loss ratio, FY26. */
  lossRatio: number
  /** Reinsurance cession rate, FY26. */
  cession: number
  /** AD Health new members per month. */
  healthMembers: number
  /** Fixed OpEx, P Mn, FY26. */
  fixedOpex: number
  /** Revenue multiple for the valuation tab. */
  revMultiple: number
}

export const BASE: Levers = {
  giGrowth: 0.20, lossRatio: 0.54, cession: 0.59,
  healthMembers: 300, fixedOpex: 45.7, revMultiple: 3.5,
}

export const CONSERVATIVE: Levers = {
  ...BASE, giGrowth: 0.12, lossRatio: 0.60, cession: 0.62,
  healthMembers: 180, fixedOpex: 49.0, revMultiple: 2.25,
}

export const AGGRESSIVE: Levers = {
  ...BASE, giGrowth: 0.28, lossRatio: 0.49, cession: 0.55,
  healthMembers: 600, fixedOpex: 43.0, revMultiple: 5.0,
}

/** The workbook's own GI growth taper, FY26 → FY30. */
export const GI_TAPER = [0.20, 0.18, 0.15, 0.13, 0.12]
/** The workbook's own loss-ratio glide, FY26 → FY30. */
export const LR_GLIDE = [0.54, 0.52, 0.51, 0.50, 0.49]
/** The workbook's own cession glide, FY26 → FY30. */
export const CESSION_GLIDE = [0.59, 0.59, 0.585, 0.58, 0.575]

// ── Consolidated statements, exactly as the workbook states them ─────────

export interface StatementLine {
  label: string
  values: number[]
  /** bold subtotal */ bold?: boolean
  /** rendered as its own visible line, per the build spec */ adjustment?: boolean
  note?: string
  indent?: boolean
}

/** Consolidated P&L. Group-level lines that do not vary by segment toggle. */
export const PL_STATIC: Record<string, number[]> = {
  insuranceExpenses: [-109_472_713.0, -117_526_506.2, -193_701_804.1, -260_519_756.0, -329_258_713.9, -382_307_730.0],
  commissionFeeIncome: [14_458_623.0, 16_380_961.0, 37_944_134.4, 63_832_014.3, 91_682_576.7, 112_617_960.9],
  grossProfit: [33_525_230.0, 50_704_538.4, 100_251_070.7, 137_185_030.2, 181_961_105.1, 226_310_218.1],
  operatingExpenses: [-31_145_491.0, -45_687_858.0, -62_410_496.2, -77_715_274.2, -90_531_264.5, -101_747_986.2],
  ebitda: [2_379_739.0, 5_016_680.4, 37_840_574.4, 59_469_756.0, 91_429_840.6, 124_562_231.9],
}

export const BALANCE_SHEET: StatementLine[] = [
  { label: 'Non-Current Assets', values: [], bold: true },
  { label: 'Property, plant & equipment', values: [7_303_332, 7_500_000, 7_700_000, 8_000_000, 8_300_000, 8_600_000], indent: true },
  { label: 'Right-of-use assets', values: [435_146, 400_000, 360_000, 320_000, 280_000, 240_000], indent: true },
  { label: 'Intangible assets', values: [5_032_969, 5_000_000, 5_200_000, 5_400_000, 5_600_000, 5_800_000], indent: true },
  { label: 'Deferred tax', values: [2_738_490, 2_500_000, 2_300_000, 2_100_000, 1_900_000, 1_700_000], indent: true },
  { label: 'Total Non-Current Assets', values: [15_509_937, 15_400_000, 15_560_000, 15_820_000, 16_080_000, 16_340_000], bold: true },
  { label: 'Current Assets', values: [], bold: true },
  { label: 'Trade & other receivables', values: [3_301_872, 3_910_659.7, 6_593_101.8, 8_598_367.3, 10_804_520.8, 12_773_698.3], indent: true },
  { label: 'Insurance contract assets', values: [14_155_400, 17_155_036.8, 23_972_130.7, 30_841_918.6, 37_104_106.6, 41_809_831.9], indent: true },
  { label: 'Reinsurance contract assets', values: [30_497_769, 37_013_460.3, 51_181_439.7, 65_026_269.1, 77_537_090.8, 86_817_449.7], indent: true },
  { label: 'Other current assets', values: [886_598, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000], indent: true },
  { label: 'Cash & cash equivalents', values: [15_146_433, 22_413_146.2, 76_131_754.6, 148_582_639.7, 259_239_503.1, 387_480_429.1], indent: true },
  { label: 'Total Current Assets', values: [63_988_072, 81_492_303, 158_878_426.8, 254_049_194.7, 385_685_221.3, 529_881_409], bold: true },
  { label: 'TOTAL ASSETS', values: [79_498_009, 96_892_303, 174_438_426.8, 269_869_194.7, 401_765_221.3, 546_221_409], bold: true },
  { label: 'Equity', values: [], bold: true },
  { label: 'Stated capital', values: [40_292_843, 40_292_843, 55_292_843, 75_292_843, 115_292_843, 155_292_843], indent: true },
  { label: 'Reserves', values: [4_582_475, 5_294_265, 6_006_055, 6_717_845, 7_429_635, 8_141_425], indent: true },
  { label: 'Retained income', values: [-30_152_328, -26_901_833.4, 2_327_501.4, 45_640_470.6, 107_582_570.3, 188_173_095.3], indent: true },
  { label: 'Non-controlling interest', values: [739_708, 739_708, 739_708, 739_708, 739_708, 739_708], indent: true },
  { label: 'Total Equity', values: [15_462_698, 19_424_982.6, 64_366_107.4, 128_390_866.6, 231_044_756.3, 352_347_071.3], bold: true },
  { label: 'Non-Current Liabilities', values: [3_708_214, 3_200_000, 2_700_000, 2_200_000, 1_700_000, 1_200_000], bold: true },
  { label: 'Current Liabilities', values: [], bold: true },
  { label: 'Trade & other payables', values: [7_931_038, 11_628_498.7, 15_884_753.7, 19_780_134.2, 23_042_067.1, 25_896_953.2], indent: true },
  { label: 'Borrowings & leases', values: [1_300_604, 1_200_000, 1_100_000, 1_000_000, 900_000, 800_000], indent: true },
  { label: 'Current tax payable', values: [1_136_658, 916_185.8, 5_814_355.1, 9_688_505.6, 14_175_832.1, 18_473_111.3], indent: true },
  { label: 'Insurance contract liabilities', values: [49_925_418, 60_522_636, 84_573_210.6, 108_809_688.3, 130_902_565.8, 147_504_273.2], indent: true },
  { label: 'Total Current Liabilities', values: [60_327_097, 74_267_320.4, 107_372_319.4, 139_278_328.1, 169_020_464.9, 192_674_337.7], bold: true },
  { label: 'TOTAL EQUITY & LIABILITIES', values: [79_498_009, 96_892_303, 174_438_426.8, 269_869_194.7, 401_765_221.3, 546_221_409], bold: true },
  { label: 'Balance check (Assets − E&L)', values: [0, 0, 0, 0, 0, 0], note: 'Always balances — workbook check row' },
]

/** Investor Funding Summary — the cash waterfall, FY26 → FY30 (no FY25 column). */
export const CASH_WATERFALL: StatementLine[] = [
  { label: 'Opening cash', values: [15_146_433.0, -1_215_206.0, 2_010_002.6, 35_165_308.4, 100_999_497.5] },
  { label: 'GI operating cash flow (PAT)', values: [5_191_719.6, 21_851_278.5, 29_097_030.0, 36_714_574.1, 44_583_835.0] },
  { label: 'New ventures net profit', values: [-1_941_225.0, 7_378_056.3, 14_215_939.2, 25_227_525.6, 36_006_689.9] },
  { label: 'Regulatory capital required', values: [-17_537_133.6, -21_989_721.9, -26_697_829.0, -31_197_994.2, -35_138_039.7] },
  { label: 'Capital investments (non-recurring)', values: [-2_075_000.0, -19_014_404.3, -3_459_834.3, -4_909_916.4, -5_611_551.5] },
  { label: 'CASH BEFORE FUNDING', values: [-1_215_206.0, -12_989_997.4, 15_165_308.4, 60_999_497.5, 140_840_431.2], bold: true },
  { label: 'Investor capital injection', values: [0, 15_000_000.0, 20_000_000.0, 40_000_000.0, 40_000_000.0] },
  { label: 'CLOSING CASH', values: [-1_215_206.0, 2_010_002.6, 35_165_308.4, 100_999_497.5, 180_840_431.2], bold: true },
  { label: 'Minimum cash buffer (P5M)', values: [5_000_000, 5_000_000, 5_000_000, 5_000_000, 5_000_000] },
  { label: 'FUNDING GAP / (SURPLUS)', values: [6_215_206.0, 2_989_997.4, -30_165_308.4, -95_999_497.5, -175_840_431.2], bold: true },
]

export const RUNWAY: StatementLine[] = [
  { label: 'Monthly burn (total OpEx ÷ 12)', values: [3_807_321.5, 5_200_874.7, 6_476_272.8, 7_544_272.0, 8_478_998.9] },
  { label: 'Runway (months)', values: [-0.3, 0.4, 5.4, 13.4, 21.3] },
  { label: 'Runway WITHOUT investor capital', values: [-0.3, -2.5, 2.3, 8.1, 16.6] },
]

export const CAPITAL: StatementLine[] = [
  { label: 'Required capital — General Insurance (25% of NEP)', values: [12_919_681.1, 15_462_133.6, 18_245_317.6, 21_237_994.7, 24_288_077.8, 27_526_488.2] },
  { label: 'Required capital — Healthcare (5% rule)', values: [0, 75_000.0, 1_744_404.3, 3_459_834.3, 4_909_916.4, 5_611_551.5] },
  { label: 'Salvage operations working capital', values: [0, 2_000_000, 2_000_000, 2_000_000, 2_000_000, 2_000_000] },
  { label: 'TOTAL REQUIRED CAPITAL', values: [12_919_681.1, 17_537_133.6, 21_989_721.9, 26_697_829.0, 31_197_994.2, 35_138_039.7], bold: true },
  { label: 'Total available capital', values: [14_722_990.0, 18_685_274.6, 63_626_399.4, 127_651_158.6, 230_305_048.3, 351_607_363.3], bold: true },
  { label: 'CAPITAL SURPLUS / (DEFICIT)', values: [1_803_308.9, 1_148_141.0, 41_636_677.5, 100_953_329.6, 199_107_054.1, 316_469_323.6], bold: true },
]

export const USE_OF_FUNDS = {
  rate: 14.0,
  tranche1: {
    when: 'Tranche 1 · Jul 2026',
    items: [
      { label: 'AD Life & Funeral — licence + setup', bwp: 20_000_000 },
      { label: 'AD NeoBank — BoB sandbox + tech', bwp: 40_000_000 },
      { label: 'AD Reinsurance Co — NBFIRA licence', bwp: 15_000_000 },
    ],
    total: 75_000_000,
  },
  tranche2: {
    when: 'Tranche 2 · Jul 2028',
    items: [
      { label: 'AD NeoBank — year 2 scale', bwp: 10_000_000 },
      { label: 'Working capital buffer', bwp: 5_000_000 },
    ],
    total: 15_000_000,
  },
}

export const COMPARABLES = [
  { name: 'Lemonade (US)', multiple: '4–8x', note: 'AI-native P&C, NYSE listed' },
  { name: 'Root Inc (US)', multiple: '1.5–3x', note: 'Telematics auto, NASDAQ' },
  { name: 'Discovery Ltd (SA)', multiple: '2–3x', note: 'Integrated insurance + banking' },
  { name: 'BIHL (Botswana)', multiple: '1.5–2.5x', note: 'Largest listed insurer in BW' },
  { name: 'Sanlam (SA)', multiple: '2–3x', note: 'Pan-African financial services' },
  { name: 'MicroEnsure / BIMA', multiple: '3–5x', note: 'Private African insurtech' },
]

export const INVESTOR = {
  capitalUsd: 5_000_000,
  capitalBwp: 70_000_000,
  preMoneyUsd: 40_000_000,
  postMoneyUsd: 45_000_000,
  stake: 5_000_000 / 45_000_000,   // 11.1%
}

// ── the engine ──────────────────────────────────────────────────────────

export interface Computed {
  /** Revenue by year, only the segments switched on. */
  revenue: number[]
  pat: number[]
  ebitda: number[]
  grossProfit: number[]
  lives: number[]
  cash: number[]
  assets: number[]
  solvency: number[]
  lossRatio: number[]
  cession: number[]
  ebitdaMargin: number[]
  expenseRatio: number[]
  combinedRatio: number[]
  cumulativePat: number
  /** Per-segment, after the levers, for the By Segment tab. */
  bySegment: { id: SegmentId; name: string; revenue: number[]; pat: number[]; cumPat: number }[]
  fundingNeed: number
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/**
 * Recompute the plan under a set of levers, with only `on` segments included.
 *
 * The levers scale the workbook rather than replacing it: a lever at its base
 * value returns the workbook figure exactly, which is what makes "Reset" a true
 * return to source and what makes every "vs base" delta meaningful.
 */
export function compute(levers: Levers, on: Set<SegmentId>): Computed {
  const n = YEARS.length
  const zeros = () => Array(n).fill(0) as number[]

  // GI responds to the growth, loss-ratio and cession levers. FY25 is audited and
  // never moves — a lever must not restate an audited year.
  const giGrowthScale = levers.giGrowth / BASE.giGrowth
  const lrScale = levers.lossRatio / BASE.lossRatio
  const cessionScale = levers.cession / BASE.cession
  const healthScale = levers.healthMembers / BASE.healthMembers
  const opexScale = levers.fixedOpex / BASE.fixedOpex

  const bySegment = SEGMENTS.map((s) => {
    const revenue = s.revenue.map((v, i) => {
      if (i === 0 || !on.has(s.id)) return on.has(s.id) ? v : 0
      if (s.id === 'gi') {
        // Compound the growth delta across the years, tapering as the workbook does.
        const yearsIn = i
        return v * Math.pow(1 + (giGrowthScale - 1) * (GI_TAPER[i - 1] / GI_TAPER[0]), yearsIn === 0 ? 0 : 1)
      }
      if (s.id === 'health') return v * healthScale
      return v
    })
    const pat = s.pat.map((v, i) => {
      if (i === 0 || !on.has(s.id)) return on.has(s.id) ? v : 0
      let out = v
      if (s.id === 'gi') {
        // A higher loss ratio and a higher cession both eat GI profit.
        out = out * (1 - (lrScale - 1) * 2.2) * (1 - (cessionScale - 1) * 0.45)
        out = out * (revenue[i] / (s.revenue[i] || 1))
      } else if (s.id === 'health') {
        out = out * healthScale
      }
      // Fixed OpEx is a group cost; charge its delta pro-rata on revenue share.
      return out
    })
    const cumPat = pat.slice(1).reduce((a, b) => a + b, 0)
    return { id: s.id, name: s.name, revenue, pat, cumPat }
  })

  const revenue = zeros().map((_, i) => bySegment.reduce((a, s) => a + s.revenue[i], 0))
  const patBeforeOpex = zeros().map((_, i) => bySegment.reduce((a, s) => a + s.pat[i], 0))

  // The Fixed OpEx lever moves the whole group, not one segment.
  const opexDelta = (levers.fixedOpex - BASE.fixedOpex) * 1_000_000
  const pat = patBeforeOpex.map((v, i) => (i === 0 ? v : v - opexDelta))

  const baseRevenue = SEGMENTS.reduce((a, s) => a.map((x, i) => x + s.revenue[i]), zeros())
  const revShare = revenue.map((v, i) => (baseRevenue[i] ? v / baseRevenue[i] : 0))

  const grossProfit = PL_STATIC.grossProfit.map((v, i) => v * (i === 0 ? 1 : revShare[i]))
  const ebitda = PL_STATIC.ebitda.map((v, i) => (i === 0 ? v : v * revShare[i] - opexDelta))
  const lives = zeros().map((_, i) =>
    SEGMENTS.reduce((a, s) => a + (on.has(s.id) ? s.lives[i] : 0), 0))

  const cashBase = BALANCE_SHEET.find((l) => l.label === 'Cash & cash equivalents')!.values
  const assetsBase = BALANCE_SHEET.find((l) => l.label === 'TOTAL ASSETS')!.values
  const cash = cashBase.map((v, i) => (i === 0 ? v : v * revShare[i]))
  const assets = assetsBase.map((v, i) => (i === 0 ? v : v * revShare[i]))

  const required = CAPITAL.find((l) => l.label === 'TOTAL REQUIRED CAPITAL')!.values
  const available = CAPITAL.find((l) => l.label === 'Total available capital')!.values
  const solvency = required.map((r, i) => (r ? (available[i] * (i === 0 ? 1 : revShare[i])) / r : 0))

  const lossRatio = [0.564, ...LR_GLIDE].map((v, i) => (i === 0 ? v : clamp(v * lrScale, 0.2, 1.2)))
  const cession = [0.589, ...CESSION_GLIDE].map((v, i) => (i === 0 ? v : clamp(v * cessionScale, 0.2, 0.95)))
  const ebitdaMargin = ebitda.map((v, i) => (revenue[i] ? v / revenue[i] : 0))
  const expenseRatio = PL_STATIC.operatingExpenses.map((v, i) =>
    revenue[i] ? Math.abs(i === 0 ? v : v * revShare[i] - opexDelta) / revenue[i] : 0)
  const combinedRatio = lossRatio.map((lr, i) => lr + expenseRatio[i])

  const cumulativePat = pat.slice(1).reduce((a, b) => a + b, 0)
  const fundingNeed = USE_OF_FUNDS.tranche1.total + USE_OF_FUNDS.tranche2.total

  return {
    revenue, pat, ebitda, grossProfit, lives, cash, assets, solvency,
    lossRatio, cession, ebitdaMargin, expenseRatio, combinedRatio,
    cumulativePat, bySegment, fundingNeed,
  }
}

/** Enterprise value at a multiple, recomputed live on the Valuation tab. */
export function valuation(fy30Revenue: number, multiple: number) {
  const evBwp = fy30Revenue * multiple
  const evUsd = evBwp / USE_OF_FUNDS.rate
  const investorShareUsd = evUsd * INVESTOR.stake
  const moic = investorShareUsd / INVESTOR.capitalUsd
  // 3-year hold, FY27 → FY30.
  const irr = moic > 0 ? Math.pow(moic, 1 / 3) - 1 : 0
  return { evBwp, evUsd, investorShareUsd, moic, irr }
}

// ── formatting ──────────────────────────────────────────────────────────

/** P Mn with one decimal, the house convention. */
export const mn = (v: number) => (v / 1_000_000).toLocaleString('en-GB', {
  minimumFractionDigits: 1, maximumFractionDigits: 1,
})
export const pct = (v: number, dp = 1) => `${(v * 100).toFixed(dp)}%`
export const times = (v: number) => `${v.toFixed(2)}x`
export const usd = (v: number) => `USD ${(v / 1_000_000).toFixed(1)}m`
export const num = (v: number) => Math.round(v).toLocaleString('en-GB')

/** CAGR FY25A → FY30E. */
export const cagr = (series: number[]) => {
  const a = series[0], b = series[series.length - 1]
  if (!a || a <= 0 || b <= 0) return null
  return Math.pow(b / a, 1 / (series.length - 1)) - 1
}
