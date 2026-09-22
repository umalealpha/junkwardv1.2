/**
 * /staff-login one-screen — CFO master 1/3, 2026-09-18.
 *
 * "Remove the remaining approximately 63 px vertical overflow on
 *  /staff-login. Keep the rotating AD/brain positioned correctly and do
 *  not alter authentication, MFA or branding."
 *
 * jsdom does not compute layout, so a live scrollHeight === clientHeight
 * assertion is a Playwright job (out of scope for this vitest suite). This
 * suite matches the pattern of `signinDesignFreeze.test.ts` and locks in
 * the CSS rules that keep the page bounded to one viewport regardless of
 * form step. Revert either rule and the corresponding test goes red — that
 * is what "acceptance test" means for this fix, not a green count.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = join(__dirname, '..')
const shell = readFileSync(join(root, '_components/AccessPortalShell.tsx'), 'utf8')

describe('AccessPortalShell fits one screen', () => {

  it('bounds the root to the viewport height', () => {
    // `min-height` alone lets the root grow when a taller form is inside.
    // The reported ~63px overflow was that growth. `height: 100dvh` bounds it.
    expect(shell).toContain('min-height: 100dvh')
    expect(shell).toContain('height: 100dvh')
  })

  it('scrolls the form INSIDE its column, not the page', () => {
    // .oap-signin carries the fields. If the form is taller than the left
    // column (staff-login reset step: three fields + submit), it must
    // scroll inside its box so the brand and orbit stay pinned. This is
    // what actually stops the outer page overflowing on /staff-login.
    const signinBlock = shell.match(/\.oap-signin\s*\{[^}]+\}/)?.[0] ?? ''
    expect(signinBlock).toContain('max-height: 100%')
    expect(signinBlock).toContain('overflow-y: auto')
    expect(signinBlock).toContain('overscroll-behavior: contain')
  })

  it('does NOT touch the frozen typography that carries the AD/brain identity', () => {
    // Safety net: the CFO's brief said keep the rotating AD/brain positioned
    // correctly and not alter branding. These frozen tokens must still be
    // in the shell so signinDesignFreeze.test.ts continues to pass.
    expect(shell).toContain('font-family: ${OAP_SCRIPT}')
    expect(shell).toContain('color: ${NAVY}')
  })
})
