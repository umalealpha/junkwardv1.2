import { describe, expect, it } from 'vitest'
import { themes, type ThemeKey } from '../themes'

// WCAG 2.1 AA for normal-size text. Fable 5.1 measured these two shared pairs
// failing on 2026-09-08 during a /qctest run on the health dashboard: the
// violations came from the shared tokens here, not from that one page.
const AA_NORMAL = 4.5

function relativeLuminance(hex: string): number {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(fg: string, bg: string): number {
  const a = relativeLuminance(fg)
  const b = relativeLuminance(bg)
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

const ALL_THEMES = Object.keys(themes) as ThemeKey[]

describe('shared theme tokens meet WCAG 2.1 AA', () => {
  // Sanity: the ratio maths itself. Black on white is exactly 21:1.
  it('computes known contrast ratios correctly', () => {
    expect(contrast('#000000', '#FFFFFF')).toBeCloseTo(21, 5)
    expect(contrast('#FFFFFF', '#FFFFFF')).toBeCloseTo(1, 5)
  })

  it.each(ALL_THEMES)('%s: warning text (wr) on warning background (wrB) clears 4.5:1', (key) => {
    const t = themes[key]
    expect(contrast(t.wr, t.wrB)).toBeGreaterThanOrEqual(AA_NORMAL)
  })

  it.each(ALL_THEMES)('%s: muted label (t2) on grey (g100) clears 4.5:1', (key) => {
    const t = themes[key]
    expect(contrast(t.t2, t.g100)).toBeGreaterThanOrEqual(AA_NORMAL)
  })

  // t2 is a body/label colour, so it must also hold on the surfaces it most
  // often sits on, not only on g100.
  it.each(ALL_THEMES)('%s: muted label (t2) also clears 4.5:1 on card and page background', (key) => {
    const t = themes[key]
    expect(contrast(t.t2, t.card)).toBeGreaterThanOrEqual(AA_NORMAL)
    expect(contrast(t.t2, t.bg)).toBeGreaterThanOrEqual(AA_NORMAL)
  })

  // t3 (muted text) sits on the same surfaces; QC 13-Sep-2026 counted 229
  // low-contrast elements on /hris/directory from it.
  it.each(ALL_THEMES)('%s: muted text (t3) clears 4.5:1 on card, page and grey', (key) => {
    const t = themes[key]
    expect(contrast(t.t3, t.card)).toBeGreaterThanOrEqual(AA_NORMAL)
    expect(contrast(t.t3, t.bg)).toBeGreaterThanOrEqual(AA_NORMAL)
    expect(contrast(t.t3, t.g100)).toBeGreaterThanOrEqual(AA_NORMAL)
  })

  // The fun theme is the dark one: its wr/t2 are light-on-dark and already pass
  // by a wide margin. Darkening them would REDUCE contrast, so they are left
  // alone — this pins that decision.
  it('fun (dark theme) was left alone because it already passes comfortably', () => {
    expect(contrast(themes.fun.wr, themes.fun.wrB)).toBeGreaterThan(10)
    expect(contrast(themes.fun.t2, themes.fun.g100)).toBeGreaterThan(10)
  })
})
