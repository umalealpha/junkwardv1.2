/**
 * The sign-in design freeze.
 *
 * Supersedes the 2026-09-08 freeze. The CFO supplied a new finished sign-in
 * design on 2026-09-12 (`design_handoff_omni_signin`) whose README opens with
 * the reason this file exists at all:
 *
 *   "The last handoff of this design lost the animations. That is the single
 *    most common failure mode here, because the motion is not decoration — it
 *    is the concept."
 *
 * So this guards three things:
 *   1. the still design — the Kaushan wordmark, the type metrics, the design's
 *      navy, the PURE BLACK brain panel;
 *   2. THE MOTION — every keyframe block, every duration/easing/delay in the
 *      handoff's ANIMATION SPEC, the per-index stagger formulas, and the
 *      two-phase 10s scene rotation. Strip any one of them and this goes red,
 *      which is the only thing that stops the same loss happening a third time;
 *   3. the two LOCKOUT-RECOVERY links, which the design does not show but which
 *      two earlier CFO directives put on this page on purpose (2026-07-09 and
 *      the catch-22 fix of 2026-08-07). Applying a picture must never quietly
 *      delete the only way a locked-out person can ask for help.
 *
 * Read as source rather than rendered: the sign-in page pulls in MSAL, which
 * cannot be constructed in a test process.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const root = join(__dirname, '..')
const shell = readFileSync(join(root, '_components/AccessPortalShell.tsx'), 'utf8')
/** The same file with comments stripped. The negative colour assertions below
 *  have to read CODE only — a comment that names the forbidden brand navy in
 *  order to forbid it would otherwise fail the test that enforces it. */
const shellCode = shell.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const login = readFileSync(join(root, 'login/page.tsx'), 'utf8')
const layout = readFileSync(join(root, 'layout.tsx'), 'utf8')

describe('Omni sign-in — the CFO’s design, frozen', () => {
  it('carries the script wordmark from the design', () => {
    expect(shell).toContain('oap-signin-wordmark')
    // These are the shipped, frozen metrics after the height-cap added by
    // 63387f41 (the 10.5vh cap lets the wordmark shrink on a short screen).
    // This pins the CSS text only — it does not itself prove the sign-in sits
    // above the fold.
    expect(shell).toContain('font-size: min(clamp(48px, 9vw, 140px), 10.5vh)')
    expect(shell).toContain('line-height: 1.02')
  })

  it('self-hosts Kaushan Script + Inter through next/font, so there is no runtime call to Google', () => {
    expect(layout).toContain("from 'next/font/google'")
    expect(layout).toContain('Kaushan_Script')
    expect(layout).toContain("variable: '--font-kaushan'")
    expect(layout).toContain("variable: '--font-inter'")
    expect(shell).toContain('var(--font-kaushan)')
    expect(shell).toContain('var(--font-inter)')
  })

  it('keeps the four-class selector that beats the blanket theme font override', () => {
    // globals.css flattens every element's font-family with !important at
    // specificity (0,3,4). Three classes cannot win. Do not shorten this.
    expect(shellCode).toContain('.oap-root .oap-left .oap-brand .oap-signin-wordmark')
  })

  it('serves both design assets from our own origin, never a CDN', () => {
    expect(shell).toContain('/brand/omni-brain.png')
    expect(shell).toContain('/brand/alpha-direct-mark-clean.png')
    expect(shellCode).not.toContain('https://')
  })

  it('keeps the brain panel PURE BLACK', () => {
    // Not a taste question: omni-brain.png has a black ground, so anything but
    // #000000 shows the asset as a visible rectangle.
    expect(shellCode).toContain("const PANEL = '#000000'")
    expect(shellCode).toContain('background: ${PANEL}')
  })

  it('keeps the design’s navy and does NOT harmonise to the house brand', () => {
    expect(shellCode).toContain("const NAVY = '#0B0B3B'")
    expect(shellCode).not.toContain('#1D3270')
    expect(shellCode).not.toContain('#F47C20')
  })

  it('uses the design’s own words under the heading', () => {
    expect(login).toContain('One source of truth for Alpha Direct')
    expect(login).toContain('Microsoft is here too')
  })

  it('carries all four scenes, in order, with their verbatim copy', () => {
    for (const mode of ['reading', 'watching', 'remembering', 'deciding']) {
      expect(shell).toContain(`mode: '${mode}'`)
    }
    expect(shell).toContain('One system that reads, checks and remembers.')
    expect(shell).toContain('It notices what nobody reported.')
    expect(shell).toContain('It forgets nothing, and it can prove it.')
    expect(shell).toContain('It reads the same records forward.')
  })

  /* ── THE MOTION. This is the block that must never be allowed to go quiet. ── */

  it('declares every keyframe block from the ANIMATION SPEC', () => {
    for (const name of [
      'om-spin', 'om-spinrev', 'om-breathe', 'om-pulsering',
      'om-fadeup', 'om-node', 'om-synapse', 'om-core',
    ]) {
      expect(shell).toContain(`@keyframes ${name}`)
    }
  })

  it('keeps the orbit durations and directions exactly as specified', () => {
    expect(shell).toContain('animation: om-spin 14s linear infinite')     // blue arc
    expect(shell).toContain('animation: om-spinrev 9s linear infinite')   // orange arc
    expect(shell).toContain('animation: om-spin 11s linear infinite')     // orange particle
    expect(shell).toContain('animation: om-spinrev 17s linear infinite')  // blue particle
    expect(shell).toContain('animation: om-pulsering 5.5s ease-out infinite')
    expect(shell).toContain('animation: om-breathe 7s ease-in-out infinite')
    expect(shell).toContain('animation: om-fadeup 1.1s ease-out both')    // one shot
    expect(shell).toContain('animation: om-core 3.4s ease-in-out infinite')
  })

  it('computes the per-index stagger rather than hardcoding one value', () => {
    // These formulas are the only thing keeping the eight nodes and eight
    // synapses from firing in unison.
    expect(shell).toContain('(2.8 + (k % 4) * 0.6)')
    expect(shell).toContain('(k * 0.4)')
    expect(shell).toContain('(2.6 + (k % 5) * 0.6)')
    expect(shell).toContain('(k * 0.35)')
  })

  it('draws the synapses as animated SVG strokes, not a picture', () => {
    expect(shell).toContain('stroke-dasharray: 14 206')
    expect(shell).toContain('animation-name: om-synapse')
    expect(shell).toContain('stroke-dashoffset:220')
    expect(shell).toContain('vectorEffect="non-scaling-stroke"')
  })

  it('rotates the scenes on 10s with the 2s content swap held inside it', () => {
    expect(shell).toContain('}, 10000)')
    expect(shell).toContain('}, 2000)')
    expect(shell).toContain('% SCENES.length')
    // Both timers cleared on unmount.
    expect(shell).toContain('clearInterval(timer)')
    expect(shell).toContain('clearTimeout(swap)')
  })

  it('dissolves with blur and drift, staggered across the three blocks', () => {
    expect(shell).toContain("fade ? '0px' : '9px'")
    expect(shell).toContain('fade ? 1 : 1.045')
    expect(shell).toContain("fade ? '0px' : '14px'")
    expect(shell).toContain('transition: opacity 1.6s cubic-bezier(.4,0,.2,1)')
    expect(shell).toContain('transform 2.4s cubic-bezier(.4,0,.2,1)')
    expect(shell).toContain('transform 1.8s cubic-bezier(.4,0,.2,1) .15s')  // copy lags .15s
    expect(shell).toContain('transform 1.8s cubic-bezier(.4,0,.2,1) .3s')   // features lag .3s
  })

  it('reduces motion ONLY under prefers-reduced-motion, and keeps the fade there', () => {
    expect(shell).toContain('@media (prefers-reduced-motion: reduce)')
    // The scene rotation survives as a plain opacity fade — the handoff asks for
    // a reduction, not a static page.
    expect(shell).toContain('transition: opacity .6s ease !important')
  })

  it('STILL offers both ways out for someone who is locked out', () => {
    // Neither of these is in the design. Both were put here by earlier CFO
    // directives and must survive any restyle.
    expect(login).toContain('Microsoft sign-in stuck?')
    expect(login).toContain('Still cannot get in?')
    expect(login).toContain('NO_LOGIN_REPORT_URL')
  })
})
