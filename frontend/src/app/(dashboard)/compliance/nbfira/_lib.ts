import { localYmd } from '@/lib/utils'
/**
 * NBFIRA compliance helpers — Botswana Insurance Industry Regulations 2019.
 *
 * Built 2026-05-22 from public NBFIRA guidance (Insurance Industry Act 2015 +
 * Insurance Industry Regulations 2019 + Prudential Returns Guidance Note).
 * Numbers and limits are seeded so the internal auditor can review and
 * mark up corrections — none of this is legal advice.
 */

export interface QuarterDef {
  label: string;    // 'Q1 FY26'
  start: string;    // ISO date
  end: string;      // ISO date
  due: string;      // ISO date (NBFIRA quarterly return due = end + 30 days)
}

/**
 * Botswana FY July-June. Quarterly returns due 30 calendar days after
 * the quarter-end per Insurance Industry Regulations 2019 reg 28.
 */
export function fyQuarters(fyEndingYear: number): QuarterDef[] {
  const startYear = fyEndingYear - 1;
  const mk = (label: string, s: string, e: string): QuarterDef => {
    const end = new Date(e);
    const due = new Date(end);
    due.setDate(due.getDate() + 30);
    return { label, start: s, end: e, due: localYmd(due) };
  };
  return [
    mk(`Q1 FY${String(fyEndingYear).slice(-2)}`, `${startYear}-07-01`, `${startYear}-09-30`),
    mk(`Q2 FY${String(fyEndingYear).slice(-2)}`, `${startYear}-10-01`, `${startYear}-12-31`),
    mk(`Q3 FY${String(fyEndingYear).slice(-2)}`, `${fyEndingYear}-01-01`, `${fyEndingYear}-03-31`),
    mk(`Q4 FY${String(fyEndingYear).slice(-2)}`, `${fyEndingYear}-04-01`, `${fyEndingYear}-06-30`),
  ];
}

/**
 * Annual return due 90 days after FY end (Insurance Industry Regulations 2019
 * reg 31). For Alpha Direct (Jun 30 FY end) → 28 Sep.
 */
export function annualReturnDue(fyEndingYear: number): string {
  const end = new Date(`${fyEndingYear}-06-30`);
  end.setDate(end.getDate() + 90);
  return localYmd(end);
}

/** Days remaining until ISO date (negative = overdue). */
export function daysUntil(isoDate: string): number {
  const d = new Date(isoDate + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - today.getTime()) / 86_400_000);
}

/** Status badge colour from days remaining. */
export function statusForDays(d: number): { label: string; cls: string } {
  if (d < 0)      return { label: `Overdue ${Math.abs(d)}d`,  cls: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]' };
  if (d <= 7)     return { label: `Due in ${d}d`,             cls: 'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]' };
  if (d <= 30)    return { label: `Due in ${d}d`,             cls: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]' };
  return            { label: `Due in ${d}d`,                  cls: 'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]' };
}

/* ── Prudential investment limits ──────────────────────────────────────────
 * Basis: Insurance Industry Regulations 2019 (S.I. 57 of 2019),
 * Regulation 7 → SCHEDULE 1 (general insurers). Alpha Direct is a GENERAL
 * insurer, so Schedule 1 applies — NOT Schedule 2's long-term-insurer figures.
 * Percentages are of total investments unless stated.
 *
 * Verified against the gazetted regulation (botswanalaws.com consolidated
 * subsidiary legislation), 2026-08-17. This replaces the earlier citations to
 * a non-existent "Insurance Industry Investment Regulations 2019" and to
 * "Reg 5(x)", which were wrong.
 *
 * NOTE — sub-limits marked "not yet monitored" need per-instrument asset
 * classification on Investment rows (issuer type, listed/unlisted, market cap).
 * That is a Tier-2 follow-up; today's engine measures the aggregate only. */

export interface PrudentialLimit {
  id: string;
  label: string;
  rule: string;          // human-readable ceiling (e.g. "≤ 25% of investment portfolio")
  threshold: number;     // 0..1
  measured?: number;     // 0..1 — current usage (filled when data available)
  source: string;        // Schedule 1 item reference
  note?: string;         // scope caveat / sub-limits not yet monitored
}

export const PRUDENTIAL_LIMITS: PrudentialLimit[] = [
  {
    id: 'single_counterparty',
    label: 'Single non-Government issuer (concentration proxy)',
    rule: '≤ 25 % (bank deposits, per institution)',
    threshold: 0.25,
    note:
      'Coarse proxy on the largest single non-Government issuer. Schedule 1 sets ' +
      'DIFFERENT single-issuer sub-limits per asset class — bank deposits 25 % (8.2), ' +
      'company & foreign bonds 5 % (8.4 / 8.5), listed shares 2.5–5 % (8.7). The ' +
      'per-asset-class breakdown is not yet monitored, so a small bond/share ' +
      'concentration can read green here.',
    source: 'Schedule 1, item 8.2',
  },
  {
    id: 'equities_total',
    label: 'Shares — aggregate holding',
    rule: '≤ 30 % of total investments',
    threshold: 0.30,
    note:
      'Single-company sub-caps (2.5 % if BSE market cap > P700m, else 5 %) and the ' +
      '20 % unlisted-shares aggregate are not yet separately monitored.',
    source: 'Schedule 1, item 8.7',
  },
  {
    id: 'foreign_assets',
    label: 'Foreign-currency investments (proxy for the foreign-bond caps)',
    rule: '≤ 20 % of total investments',
    threshold: 0.20,
    note:
      'Schedule 1 caps FOREIGN BONDS — foreign government bonds 20 % and foreign ' +
      'non-government bonds 20 % aggregate, each with a 5 % single-institution sub-limit ' +
      '(item 8.5). The engine currently measures TOTAL foreign-currency investments (all ' +
      'non-BWP holdings), used as a CONSERVATIVE proxy for those caps: total FX ≥ any bond ' +
      'subset, so it can over-flag but never hide a breach. Per-instrument foreign-bond ' +
      'monitoring is a Tier-2 follow-up. Foreign investment also needs exchange-control ' +
      'approval.',
    source: 'Schedule 1, item 8.5',
  },
  {
    id: 'property',
    label: 'Immovable property — aggregate',
    rule: '≤ 10 % of total investments',
    threshold: 0.10,
    note:
      'Single property/project sub-limit 5 %. (The 25 % figure previously shown is the ' +
      'LONG-TERM-insurer limit, Schedule 2 item 9.6 — it does not apply to Alpha Direct.)',
    source: 'Schedule 1, item 8.6',
  },
  {
    id: 'liquidity',
    label: 'Liquidity cover (cash + near-cash vs 12-month claims)',
    rule: '≥ 100 % of projected 12-month gross claims',
    threshold: 1.00,
    note:
      'Management / prudential liquidity measure — NOT a Schedule 1 statutory investment ' +
      'limit. The exact NBFIRA source (a prudential rule or guidance note) is still to be ' +
      'confirmed; treat the ceiling as an internal target until verified.',
    source: 'Internal prudential measure (source to confirm)',
  },
  {
    id: 'related_party',
    label: 'Related-party loans / debentures',
    rule: '≤ 5 % of total investments',
    threshold: 0.05,
    note:
      'Non-convertible related-party loans/debentures. Needs a related-party flag on ' +
      'Investment rows before it can be measured (currently returns 0, not confirmed nil).',
    source: 'Schedule 1, item 8.8',
  },
];

/* ── Capital adequacy thresholds (Botswana general insurance) ── */

export const MIN_CAPITAL_BWP   = 5_000_000;       // MCR for general insurance
export const TARGET_SCR_RATIO  = 1.50;            // Internal management target
export const FLOOR_SCR_RATIO   = 1.00;            // NBFIRA hard floor
