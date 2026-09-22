import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { financialFigures } from '../adh-dashboard/figures'
import type { HealthDashboard } from '@/lib/api'

/**
 * The ADH dashboard shipped on 9-Sep-2026 summing the stored gross, which is
 * VAT-INCLUSIVE — so it showed YTD GWP 117,309 and a loss ratio of 7.6% where the
 * Health Cover screen showed 102,903 and 8.72% for the same month. These figures
 * are the real prod payload for Aug-2026.
 */
const LIVE = {
  fy: { label: 'FY27', start: '2026-07-01', end: '2027-06-30' },
  kpi: {
    gwpYtdIncl: 117309.42, gwpYtdExcl: 102903.0,
    gwpMonthIncl: 57753.54, gwpMonthExcl: 50661.0,
    gwpMonthLabel: 'Aug 26', lossRatioYtdPct: 8.72,
  },
  fyTotals: [
    { fy: 2026, label: 'FY26', gwpExcl: 133015.0, gwpIncl: 151637.1, claims: 62892.67, isCurrent: false },
    { fy: 2027, label: 'FY27', gwpExcl: 102903.0, gwpIncl: 117309.42, claims: 8973.46, isCurrent: true },
  ],
} as unknown as HealthDashboard

describe('ADH dashboard money tiles', () => {
  it('shows premium EXCLUDING VAT, never the stored inclusive figure', () => {
    const f = financialFigures(LIVE)
    expect(f.ytdGwp).toBe(102903.0)
    expect(f.ytdGwp).not.toBe(117309.42)
    expect(f.latestGwp).toBe(50661.0)
    expect(f.latestGwp).not.toBe(57753.54)
  })

  it('agrees with the Health Cover screen on the loss ratio', () => {
    expect(financialFigures(LIVE).lossRatio).toBe(8.72)
  })

  it('reads claims from the CURRENT financial year, not every year on file', () => {
    expect(financialFigures(LIVE).ytdClaims).toBe(8973.46)
  })

  it('is the same ratio the feed publishes — a local sum would read 7.6%', () => {
    const f = financialFigures(LIVE)
    const wrong = (f.ytdClaims! / LIVE.kpi.gwpYtdIncl!) * 100
    expect(Number(wrong.toFixed(1))).toBe(7.6)          // what the bug showed
    expect(Number(f.lossRatio!.toFixed(1))).toBe(8.7)   // what it shows now
  })

  it('says "no data" rather than zero when the feed is empty', () => {
    const f = financialFigures(null)
    expect(f).toEqual({ ytdGwp: null, ytdClaims: null, lossRatio: null, latestGwp: null })
  })
})

describe('ADH dashboard source guard', () => {
  const read = (f: string) => readFileSync(join(__dirname, '..', 'adh-dashboard', f), 'utf8')
  const src = read('page.tsx')
  // The comment in figures.ts names the inclusive fields to explain why they are
  // banned, so the guard looks at code only.
  const figuresCode = read('figures.ts').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')

  it('never reads a VAT-inclusive premium field', () => {
    for (const code of [src, figuresCode]) {
      expect(code).not.toMatch(/gwpYtdIncl|gwpMonthIncl|annualisedIncl/)
      expect(code).not.toMatch(/gwpIncl\s*\??\./)
    }
  })

  it('plots the excl-VAT monthly premium in the bar chart', () => {
    expect(src).toMatch(/gwp:\s*m\.gwpExcl/)
  })

  it('tells the reader the money excludes VAT', () => {
    expect(src).toMatch(/excl\. VAT/)
  })
})
