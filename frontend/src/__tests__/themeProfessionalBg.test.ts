import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { join } from 'path'

/**
 * The professional theme repaints hardcoded brand backgrounds by SUBSTRING match on
 * the class attribute. A substring also matches the variant forms, so
 * `hover:bg-[#F4A623]/[0.06]` (the Purchase Orders row hover tint) got repainted at
 * REST — painting every row of the list solid blue with dark text on top.
 * Found live 11-Sep-2026. The guard is the `:not([class*=":bg-[#"])` on every
 * at-rest background rule.
 */
const css = readFileSync(join(__dirname, '../app/globals.css'), 'utf8')

const atRestBgRules = css
  .split('\n')
  .filter(l => /^html\.theme-professional \[class\*="bg-\[#/.test(l.trim()))

describe('theme-professional background overrides', () => {
  it('has at-rest background rules to check', () => {
    expect(atRestBgRules.length).toBeGreaterThan(0)
  })

  it('never repaints a variant background at rest', () => {
    const unguarded = atRestBgRules.filter(l => !l.includes(':not([class*=":bg-[#"])'))
    expect(unguarded).toEqual([])
  })

  it('still recolours the variant forms in their own state', () => {
    expect(css).toMatch(/\[class\*="hover:bg-\[#0D1B2A"\]:hover/)
    expect(css).toMatch(/\[class\*="hover:bg-\[#F4A623\]\/"\]:hover/)
    expect(css).toMatch(/::file-selector-button/)
  })
})
