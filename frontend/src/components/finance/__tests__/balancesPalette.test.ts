/**
 * The two colour traps this panel can fall into.
 *
 * 1. `html.theme-professional` in globals.css repaints hardcoded brand hexes by
 *    CLASS substring — `[class*="bg-[#0D1B2A"]` → #4F6BED — and orange or grey
 *    text on that becomes unreadable. The panel therefore carries NO brand-hex
 *    Tailwind class at all; it takes colours as a prop.
 * 2. `theme.navy` is NOT a navy in every theme. In Fun Mode it is #E0E7FF, the
 *    heading TEXT colour, because the cards there are already dark. Using it as
 *    the headline background would have painted white on near-white.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { darkSurface, fromTheme } from '../balancesPalette'
import { themes, type ThemeKey } from '@/lib/themes'

const ALL = Object.keys(themes) as ThemeKey[]

function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const [r, g, b] = [0, 2, 4]
    .map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(fg: string, bg: string): number {
  const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a)
  return (hi + 0.05) / (lo + 0.05)
}

describe('the headline block is readable in every theme', () => {
  it.each(ALL)('%s: white on the headline background clears WCAG AA', (key) => {
    const p = fromTheme(themes[key])
    expect(contrast(p.onNavy, p.navy)).toBeGreaterThanOrEqual(4.5)
  })

  it('THE TRAP: Fun Mode’s theme.navy is a light TEXT colour, so it is not used as the background', () => {
    expect(luminance(themes.fun.navy)).toBeGreaterThan(0.5)      // it really is light
    expect(darkSurface(themes.fun)).not.toBe(themes.fun.navy)    // and we don't use it
    expect(contrast('#FFFFFF', darkSurface(themes.fun))).toBeGreaterThanOrEqual(4.5)
  })

  it('falls back to the brand navy rather than returning something unreadable', () => {
    const allLight = { ...themes.light, navy: '#FFFFFF', sidebar: '#FFFFFF' }
    expect(darkSurface(allLight)).toBe('#0D1B2A')
  })
})

describe('card text is readable in every theme', () => {
  it.each(ALL)('%s: the muted label clears AA on the card', (key) => {
    const p = fromTheme(themes[key])
    expect(contrast(p.inkSoft, p.card)).toBeGreaterThanOrEqual(4.5)
  })

  it.each(ALL)('%s: the warning and danger notes clear AA on the card', (key) => {
    const p = fromTheme(themes[key])
    expect(contrast(p.warn, p.card)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(p.danger, p.card)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('the theme-professional repaint cannot reach this panel', () => {
  const sources = [
    'BankBalancesPanel.tsx', 'BankBalancesSection.tsx', 'balancesPalette.ts',
  ].map((f) => readFileSync(join(__dirname, '..', f), 'utf8'))
  // Comments explain the forbidden classes in order to forbid them, so the
  // assertions below read CODE only.
  const code = sources
    .map((s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, ''))
    .join('\n')

  it('uses no arbitrary brand-hex Tailwind background, which is what gets repainted', () => {
    expect(code).not.toMatch(/bg-\[#0D1B2A/)
    expect(code).not.toMatch(/bg-\[#F4A623/)
    expect(code).not.toMatch(/bg-\[#0B0B3B/)
  })

  it('uses no orange text class either', () => {
    expect(code).not.toMatch(/text-\[#F4A623/)
    expect(code).not.toMatch(/text-\[#F07F00/)
  })
})
