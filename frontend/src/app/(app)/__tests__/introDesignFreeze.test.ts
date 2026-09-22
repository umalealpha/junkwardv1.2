/**
 * The splash design freeze.
 *
 * The CFO supplied the finished splash on 2026-09-08 ("Omni Amendments — FINAL
 * pack", artboard "2a — Orbit, white ground") with one instruction repeated in
 * two amendments: apply it exactly, then LOCK it — no restyling, no recolouring,
 * no "harmonising" to the house brand. A design freeze that lives only in a
 * comment is not a freeze, so it is asserted here: if someone repaints the
 * splash, this test goes red and names what changed.
 *
 * Rendered with react-dom/server (this repo has jsdom but no testing-library),
 * which is enough to see the real markup the component produces. createElement
 * rather than JSX so the file stays a .ts and the existing vitest include
 * pattern picks it up unchanged.
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Intro } from '../Intro'

const html = renderToStaticMarkup(createElement(Intro, { onDone: () => {} }))

describe('Alpha Omni splash \u2014 the CFO\u2019s design, frozen', () => {
  it('stands on a white ground, not the old navy gradient', () => {
    expect(html).toContain('background:#FFFFFF')
    expect(html).not.toContain('#050522')     // the retired navy-gradient ground
  })

  it('is the designed 1080 \u00d7 2340 stage, so every measurement stays the design\u2019s own', () => {
    expect(html).toContain('width:1080px')
    expect(html).toContain('height:2340px')
  })

  it('keeps the three designed colours \u2014 deep navy, orange, light blue', () => {
    expect(html).toContain('#0B0B3B')
    expect(html).toContain('#F07F00')
    expect(html).toContain('#40A8E0')
  })

  it('does NOT use the house brand navy/orange, which the CFO ruled out for this screen', () => {
    expect(html).not.toContain('#1D3270')
    expect(html).not.toContain('#F47C20')
  })

  it('sets the wordmark in the designed script face', () => {
    expect(html).toContain('var(--font-kaushan)')
    expect(html).toContain('font-size:285px')
    expect(html).toContain('>Omni<')
  })

  it('carries the designed words exactly', () => {
    expect(html).toContain('One system. Every decision.')
    expect(html).toContain('Tap to skip')
  })

  it('shows the real Alpha Direct mark, never a redrawn or recoloured one', () => {
    expect(html).toContain('/brand/ad-mark.png')
  })

  it('draws the orbit: three hairline rings, two turning arcs and their dots', () => {
    for (const cls of ['om-spin-14', 'om-spinrev-9', 'om-spin-11', 'om-spinrev-17', 'om-pulsering', 'om-breathe']) {
      expect(html).toContain(cls)
    }
  })
})
