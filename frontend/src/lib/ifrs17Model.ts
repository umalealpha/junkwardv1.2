/**
 * ifrs17Model.ts — Alpha Direct Insurance Company, IFRS 17 valuation cockpit.
 *
 * Deterministic engine. Same shape as fiveYearModel.ts and budgetModel.ts:
 * THE PAGE OWNS NO ARITHMETIC. Move a lever, everything recomputes here.
 *
 * SOURCE: "Alpha Direct Insurance Company — IFRS 17 Valuation Report",
 * Empirica Actuaries, valuation date 30 June 2026, report date 19 August 2026.
 * Implemented AS-IS — no figure in the base layer deviates from the report.
 * The Python side (ifrs17/engine.py) implements the same maths and both are held
 * to the report by ifrs17/tests/test_parity.py.
 *
 * WHY THE SIGNED FIGURES ARE THE BASE, NOT A RE-DERIVATION: Empirica's selections
 * are a reperformed, signed actuarial opinion. At base lever positions this model
 * returns those figures verbatim; levers move AWAY from them. Re-deriving instead
 * would quietly replace signed numbers with slightly different ones — the report's
 * own IBNR table is 31 short of its stated selection, and its published 92.9%
 * expense share is a rounded display of 92.9435%. Either would drag the LIC, the
 * balance sheet and the net position with it.
 *
 * KNOWN VARIANCES IN THE REPORT — do NOT silently "fix" any of these; each is
 * disclosed and each is a row in the data-quality register:
 *   · IBNR by underwriting year sums to 21,155,255 vs a signed 21,155,286 (31).
 *   · The attributable share prints as 92.9% but is 92.9435% (15,129).
 *   · Revenue + service expenses + net reinsurance = 12,864,244, while Table 1
 *     states a profit before tax of 12,864,243 (1). §7.2's own bridge lands on
 *     12,864,245 and calls the 2 rounding.
 *   · Trial balance premium 133,437,785 vs IFRS 17 revenue 133,416,295 (21,490).
 *   · An unsupported 149,104 opening overlay in the reinsurance asset.
 *   · 505,382 of FY2025 opening unearned premium never posted.
 */

export type SegmentId =
  | 'accident' | 'engineering' | 'guarantee' | 'liability'
  | 'miscellaneous' | 'motor' | 'property' | 'transportation'

export const SEGMENT_IDS: SegmentId[] = [
  'accident', 'engineering', 'guarantee', 'liability',
  'miscellaneous', 'motor', 'property', 'transportation',
]

export interface Segment {
  id: SegmentId
  name: string
  premium: number
  commission: number
  claims: number
  unearnedPremium: number
}

/** §4.3 Table 16 + §7.4 Table 42, verbatim. */
export const SEGMENTS: Segment[] = [
  { id: 'accident',       name: 'Accident',       premium: 14_691_203, commission:   970_320, claims:  3_376_824, unearnedPremium: 1_857_867 },
  { id: 'engineering',    name: 'Engineering',    premium:    552_531, commission:   105_974, claims:  1_628_280, unearnedPremium:   116_531 },
  { id: 'guarantee',      name: 'Guarantee',      premium:    524_045, commission:    76_841, claims:    755_803, unearnedPremium:   204_386 },
  { id: 'liability',      name: 'Liability',      premium: 17_008_699, commission:   362_755, claims:    453_666, unearnedPremium:   731_864 },
  { id: 'miscellaneous',  name: 'Miscellaneous',  premium:  6_614_097, commission:   946_738, claims:  1_590_783, unearnedPremium: 1_200_629 },
  { id: 'motor',          name: 'Motor',          premium: 68_414_860, commission: 5_219_331, claims: 49_579_541, unearnedPremium: 8_536_414 },
  { id: 'property',       name: 'Property',       premium: 21_373_350, commission: 3_674_077, claims: 10_121_611, unearnedPremium: 3_388_405 },
  { id: 'transportation', name: 'Transportation', premium:  4_237_511, commission:   719_358, claims:  1_173_430, unearnedPremium:   712_873 },
]

/** §5.4 Table 24 — shown for transparency, NOT one of the eight segments. */
export const HEALTH = {
  premium: 133_609, claims: 63_763, attributableExpenses: 178_183,
  underwritingResult: -108_337, cededPremium: 70_353, cededRecoveries: 56_603,
}

export interface Treaty {
  id: string; name: string
  cededPremium: number; commission: number; directRecovery: number
}

/** §6.2 Table 26. */
export const TREATIES: Treaty[] = [
  { id: 'facultative',      name: 'Facultative / AutoFac',   cededPremium:  9_198_540, commission:  2_485_725, directRecovery:  2_216_404 },
  { id: 'fire_eng_surplus', name: 'Fire & Engineering Surplus', cededPremium: 5_959_762, commission: 1_787_929, directRecovery: 1_190_408 },
  { id: 'general_qs',       name: 'General QS',              cededPremium:  6_768_113, commission:  2_368_840, directRecovery:  3_528_461 },
  { id: 'jbb_motor_qs',     name: 'JBB Motor QS',            cededPremium: 54_450_560, commission: 15_040_197, directRecovery: 39_736_998 },
  { id: 'xl_and_cat',       name: 'XL and CAT',              cededPremium:  3_887_570, commission:          0, directRecovery:  1_291_248 },
  { id: 'hcv',              name: 'HCV (run-off)',           cededPremium:          0, commission:          0, directRecovery:    272_854 },
  { id: 'fmre_motor_qs',    name: 'FMRE Motor QS (run-off)', cededPremium:          0, commission:          0, directRecovery:          0 },
]

/** §5.2 Table 21. The rows do NOT foot to the signed selection — see the header. */
export const IBNR_BY_UWY = [
  { uwy: 2020, observed: 39_112_317, ultimate: 39_112_317, outstanding:          0, ibnr:          0 },
  { uwy: 2021, observed: 30_609_415, ultimate: 30_704_319, outstanding:     94_903, ibnr:     94_903 },
  { uwy: 2022, observed: 32_728_441, ultimate: 32_942_770, outstanding:    214_329, ibnr:     90_414 },
  { uwy: 2023, observed: 39_229_598, ultimate: 39_671_739, outstanding:    442_142, ibnr:    440_631 },
  { uwy: 2024, observed: 47_536_194, ultimate: 48_632_515, outstanding:  1_096_320, ibnr:    965_071 },
  { uwy: 2025, observed: 58_987_145, ultimate: 61_817_299, outstanding:  2_830_154, ibnr:  2_052_836 },
  { uwy: 2026, observed: 49_150_954, ultimate: 76_395_012, outstanding: 27_244_057, ibnr: 17_511_400 },
]

// ---------------------------------------------------------------------------
// Levers
// ---------------------------------------------------------------------------

export interface Levers {
  raPct: number
  cheFactorPct: number
  salvageSubroPct: number
  attributableSharePct: number
  ibnrTailFactor: number
  discounting: boolean
  jbbCommissionPct: number
  onerousTest: boolean
  healthModelled: boolean
}

/** Empirica's FY2026 selections, verbatim. */
export const BASE: Levers = {
  raPct: 0.06,
  cheFactorPct: 0.024779704082,
  salvageSubroPct: 0.062015,
  attributableSharePct: 0.929,
  ibnrTailFactor: 1.0031,
  discounting: false,
  jbbCommissionPct: 0.25,
  onerousTest: false,
  healthModelled: false,
}

export interface SliderSpec {
  key: keyof Levers
  label: string
  min: number; max: number; step: number
  format: 'pct' | 'pct4' | 'factor'
  why: string
}

/** Every slider, with WHY it earns one. The `why` text renders under the control. */
export const SLIDERS: SliderSpec[] = [
  { key: 'raPct', label: 'Risk adjustment', min: 0.03, max: 0.12, step: 0.005, format: 'pct',
    why: '6% of fulfilment cash flows at the 75th percentile, gross and ceded.' },
  { key: 'cheFactorPct', label: 'Claims-handling expense factor', min: 0, max: 0.06, step: 0.0005, format: 'pct4',
    why: 'Applied to gross IBNR plus half of case reserves. Carried forward unchanged from FY2025 because no approved FY2026 allocation was provided — so this is the assumption most likely to move.' },
  { key: 'salvageSubroPct', label: 'Salvage & subrogation recovery', min: 0, max: 0.15, step: 0.0025, format: 'pct',
    why: 'A management estimate set equal to FY2025 actual recovery income.' },
  { key: 'attributableSharePct', label: 'Attributable share of operating expenses', min: 0.6, max: 1, step: 0.005, format: 'pct',
    why: 'FY2026 92.9%, FY2025 80.5%. The report is explicit that the apportionment change — not cost escalation — drives the 22.7% rise.' },
  { key: 'ibnrTailFactor', label: 'IBNR year 6→7 tail factor', min: 1, max: 1.01, step: 0.0001, format: 'factor',
    why: 'FY2025 used a nil tail of exactly 1.000000. Moving to the observed 1.003100 is worth 896,862 of gross IBNR on its own.' },
  { key: 'jbbCommissionPct', label: 'JBB Motor QS commission rate', min: 0.24, max: 0.41, step: 0.005, format: 'pct',
    why: 'Booked at the 25% provisional rate against a 24–41% sliding scale settled on final underwriting-year loss ratio. SENSITIVITY ONLY — the report states this is "not a bookable adjustment, and it has not been recognised".' },
]

export const JBB_CEDED_PREMIUM = 54_450_560
export const JBB_PROVISIONAL = 0.25

/** §6.3 — the unrecognised commission at any rate on the scale. */
export const jbbSensitivity = (rate: number) =>
  (rate - JBB_PROVISIONAL) * JBB_CEDED_PREMIUM

// ---------------------------------------------------------------------------
// The signed FY2026 valuation — the base layer
// ---------------------------------------------------------------------------

export const SIGNED = {
  insuranceRevenue: 133_416_295,
  insuranceServiceExpenses: -113_980_546,
  serviceResultBeforeRI: 19_435_749,
  allocationOfReinsurancePremiums: -58_581_854,
  amountsRecoverable: 52_010_349,
  netReinsuranceResult: -6_571_505,
  profitBeforeTax: 12_864_243,
  lrc: 16_748_968,
  licBestEstimate: 31_300_096,
  licRiskAdjustment: 1_878_006,
  insuranceContractLiabilities: 49_927_070,
  arc: 10_782_688,
  aricBestEstimate: 23_264_702,
  aricRiskAdjustment: 1_395_882,
  reinsuranceContractAssets: 35_443_271,
  netIfrs17Liability: 14_483_799,
  grossCaseReserves: 11_503_407,
  grossIbnr: 21_155_286,
  cheReserve: 666_747,
  salvageSubrogation: 2_025_344,
  claimsIncurred: 68_679_937,
  acquisitionCommission: 12_075_395,
  attributableExpenses: 29_168_270,
  totalOperatingExpenses: 31_381_207,
  nonAttributableExpenses: 2_212_937,
  grossWrittenPremium: 133_416_295,
  directRecoveriesReceived: 48_236_373,
}

/** FY2025 comparatives (§4, drawn from the FY2025 PAA model). */
export const SIGNED_FY25 = {
  insuranceRevenue: 125_202_058,
  profitBeforeTax: 26_838_707,
  grossWrittenPremium: 125_148_692,   // ties EXACTLY to the frozen 125.15 Mn
  insuranceContractLiabilities: 47_850_064.78,
  reinsuranceContractAssets: 32_136_927.70,
  netIfrs17Liability: 15_713_137.08,
  lrc: 18_728_899.18,
  licBestEstimate: 27_472_797.73,
  licRiskAdjustment: 1_648_367.86,
  grossCaseReserves: 10_439_514.29,
  grossIbnr: 16_495_193.39,
  cheReserve: 538_090.05,
}

// ---------------------------------------------------------------------------

export interface SegmentResult {
  id: SegmentId; name: string
  premium: number; commission: number; claims: number; unearnedPremium: number
  lossRatio: number; combinedRatio: number
}

export interface Computed {
  insuranceRevenue: number
  insuranceServiceExpenses: number
  serviceResultBeforeRI: number
  netReinsuranceResult: number
  profitBeforeTax: number
  lrc: number
  licBestEstimate: number
  licRiskAdjustment: number
  insuranceContractLiabilities: number
  reinsuranceContractAssets: number
  netIfrs17Liability: number
  grossCaseReserves: number
  grossIbnr: number
  cheReserve: number
  salvageSubrogation: number
  claimsIncurred: number
  attributableExpenses: number
  acquisitionAmortisation: number
  lossRatio: number
  combinedRatio: number
  expenseRatio: number
  acquisitionRatio: number
  bySegment: SegmentResult[]
  byUwy: { uwy: number; observed: number; ultimate: number; outstanding: number; ibnr: number }[]
  byTreaty: { id: string; name: string; cededPremium: number; commission: number; directRecovery: number; netPremiumCost: number; directEconomics: number }[]
  jbbSensitivity: number
  notes: string[]
  isSignedBasis: boolean
}

/** True while every reserve-feeding lever still sits on the signed selection. */
const atSignedDefaults = (l: Levers) =>
  l.raPct === BASE.raPct &&
  l.cheFactorPct === BASE.cheFactorPct &&
  l.salvageSubroPct === BASE.salvageSubroPct &&
  l.ibnrTailFactor === BASE.ibnrTailFactor

export function compute(levers: Levers, on: Set<SegmentId>): Computed {
  const notes: string[] = []
  const fullBook = on.size === SEGMENTS.length && !levers.healthModelled
  const signedBasis = atSignedDefaults(levers) &&
    levers.attributableSharePct === BASE.attributableSharePct &&
    levers.jbbCommissionPct === BASE.jbbCommissionPct && fullBook

  // ---- segments -------------------------------------------------------
  const live = SEGMENTS.filter((s) => on.has(s.id))
  let premium = live.reduce((a, s) => a + s.premium, 0)
  let commission = live.reduce((a, s) => a + s.commission, 0)
  let claims = live.reduce((a, s) => a + s.claims, 0)
  const unearned = live.reduce((a, s) => a + s.unearnedPremium, 0)

  if (levers.healthModelled) {
    premium += HEALTH.premium
    claims += HEALTH.claims
    notes.push(
      'Health is INCLUDED. It is not one of the eight reporting segments and Empirica does not model it; it loses 108,337 and carries 70,353 of ceded premium with no identifiable Health treaty. This is a sensitivity, not the signed valuation.',
    )
  }

  // ---- reserves: signed base, levers move away from it -----------------
  const scale = levers.ibnrTailFactor / BASE.ibnrTailFactor
  // DELTA-FORM RESERVES (Fable 2026-08-25) — each component is the SIGNED figure
  // plus f(current) - f(base), so it equals signed exactly at base and moves
  // continuously off it. No basis-switch jump; must match ifrs17/engine.py.
  const grossIbnr = SIGNED.grossIbnr * scale        // scale is 1 at base
  const grossCaseReserves = SIGNED.grossCaseReserves
  const ibnrBase = SIGNED.grossIbnr

  const che = (ibnr: number, f: number) => f * (ibnr + grossCaseReserves / 2)
  const salv = (ibnr: number, p: number) => p * (grossCaseReserves + ibnr)

  const cheReserve = SIGNED.cheReserve
    + (che(grossIbnr, levers.cheFactorPct) - che(ibnrBase, BASE.cheFactorPct))
  const salvage = SIGNED.salvageSubrogation
    + (salv(grossIbnr, levers.salvageSubroPct) - salv(ibnrBase, BASE.salvageSubroPct))

  const licNow = grossCaseReserves + grossIbnr
    + che(grossIbnr, levers.cheFactorPct) - salv(grossIbnr, levers.salvageSubroPct)
  const licBaseCalc = grossCaseReserves + ibnrBase
    + che(ibnrBase, BASE.cheFactorPct) - salv(ibnrBase, BASE.salvageSubroPct)
  const licBestEstimate = SIGNED.licBestEstimate + (licNow - licBaseCalc)

  const licRiskAdjustment = SIGNED.licRiskAdjustment
    + (levers.raPct * licBestEstimate - BASE.raPct * SIGNED.licBestEstimate)

  const lrc = fullBook ? SIGNED.lrc : unearned
  const insuranceContractLiabilities = lrc + licBestEstimate + licRiskAdjustment

  const aricRiskAdjustment = SIGNED.aricRiskAdjustment
    + (levers.raPct - BASE.raPct) * SIGNED.aricBestEstimate
  const reinsuranceContractAssets = SIGNED.reinsuranceContractAssets
    + (aricRiskAdjustment - SIGNED.aricRiskAdjustment)

  // ---- result ----------------------------------------------------------
  const insuranceRevenue = fullBook ? SIGNED.insuranceRevenue : premium
  const claimsIncurred = fullBook ? SIGNED.claimsIncurred : claims
  const acquisitionAmortisation = fullBook ? SIGNED.acquisitionCommission : commission
  const attributableExpenses =
    levers.attributableSharePct === BASE.attributableSharePct
      ? SIGNED.attributableExpenses
      : SIGNED.attributableExpenses * (levers.attributableSharePct / BASE.attributableSharePct)

  // Insurance service expenses in DELTA FORM (Fable 2026-08-25). At the signed
  // basis this is the signed figure; when a lever moves the reserves or the
  // expense apportionment, ISE moves by the SAME delta — more prudence is more
  // expense, so ISE goes more negative and profit falls. The earlier version
  // re-derived ISE from scratch off-base, which snapped ISE by ~2.18m and made
  // raising the risk adjustment RAISE profit on the cockpit. Must match
  // ifrs17/engine.py to the cent.
  const insuranceServiceExpenses = fullBook
    ? SIGNED.insuranceServiceExpenses
      - (attributableExpenses - SIGNED.attributableExpenses)
      - (licRiskAdjustment - SIGNED.licRiskAdjustment)
      - (licBestEstimate - SIGNED.licBestEstimate)
    : -(claimsIncurred + attributableExpenses + acquisitionAmortisation + licRiskAdjustment)
  const serviceResultBeforeRI = insuranceRevenue + insuranceServiceExpenses

  const jbbDelta = jbbSensitivity(levers.jbbCommissionPct)
  if (jbbDelta !== 0) {
    notes.push(
      `JBB Motor QS commission modelled at ${(levers.jbbCommissionPct * 100).toFixed(1)}% against the 25.0% provisional rate booked: ${Math.round(jbbDelta).toLocaleString('en-GB')} of additional commission. SENSITIVITY ONLY — the report states this is "not a bookable adjustment, and it has not been recognised".`,
    )
  }
  const netReinsuranceResult = SIGNED.netReinsuranceResult + jbbDelta
  const profitBeforeTax = serviceResultBeforeRI + netReinsuranceResult

  if (levers.onerousTest) {
    notes.push(
      'Onerousness test ON. Engineering (295% gross loss ratio) and Guarantee (144%) are the candidates; their combined premium of 1,076,575 is 0.8% of the book, so no loss component would be material. Empirica recommends a formal test before FY2027.',
    )
  }
  if (levers.discounting) {
    notes.push(
      'Discounting ON. The signed valuation takes the paragraph 56 and 59(b) expedients and applies NO discounting. Turning this on departs from the signed basis and needs a yield curve the valuation does not carry.',
    )
  }

  const lossRatio = insuranceRevenue ? claimsIncurred / insuranceRevenue : 0
  const acquisitionRatio = insuranceRevenue ? acquisitionAmortisation / insuranceRevenue : 0
  const expenseRatio = insuranceRevenue ? attributableExpenses / insuranceRevenue : 0

  return {
    insuranceRevenue,
    insuranceServiceExpenses,
    serviceResultBeforeRI,
    netReinsuranceResult,
    profitBeforeTax,
    lrc,
    licBestEstimate,
    licRiskAdjustment,
    insuranceContractLiabilities,
    reinsuranceContractAssets,
    netIfrs17Liability: insuranceContractLiabilities - reinsuranceContractAssets,
    grossCaseReserves,
    grossIbnr,
    cheReserve,
    salvageSubrogation: salvage,
    claimsIncurred,
    attributableExpenses,
    acquisitionAmortisation,
    lossRatio,
    combinedRatio: lossRatio + acquisitionRatio + expenseRatio,
    expenseRatio,
    acquisitionRatio,
    bySegment: live.map((s) => {
      const lr = s.premium ? s.claims / s.premium : 0
      const ar = s.premium ? s.commission / s.premium : 0
      return {
        id: s.id, name: s.name, premium: s.premium, commission: s.commission,
        claims: s.claims, unearnedPremium: s.unearnedPremium,
        lossRatio: lr, combinedRatio: lr + ar + expenseRatio,
      }
    }),
    byUwy: IBNR_BY_UWY.map((r) => ({ ...r, ibnr: r.ibnr * (atSignedDefaults(levers) ? 1 : scale) })),
    byTreaty: TREATIES.map((t) => {
      const comm = t.commission + (t.id === 'jbb_motor_qs' ? jbbDelta : 0)
      return {
        id: t.id, name: t.name, cededPremium: t.cededPremium, commission: comm,
        directRecovery: t.directRecovery,
        netPremiumCost: t.cededPremium - comm,
        directEconomics: t.directRecovery + comm - t.cededPremium,
      }
    }),
    jbbSensitivity: jbbDelta,
    notes,
    isSignedBasis: signedBasis,
  }
}

// ---- formatting (same helpers as fiveYearModel.ts) -----------------------
export const mn = (v: number) =>
  (v / 1_000_000).toLocaleString('en-GB', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
export const num = (v: number) => Math.round(v).toLocaleString('en-GB')
export const pct = (v: number, dp = 1) => `${(v * 100).toFixed(dp)}%`
export const signed = (v: number) => (v < 0 ? `(${num(Math.abs(v))})` : num(v))
