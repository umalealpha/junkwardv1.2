/**
 * The panel, RENDERED — not grepped.
 *
 * The failure mode this guards is the one that bit this codebase before: a
 * component that type-checks, builds and ships a BLANK card. Every assertion
 * below reads real rendered markup (react-dom/server; the repo has jsdom but no
 * testing-library, matching (app)/__tests__/introDesignFreeze.test.ts).
 *
 * And the CFO's own instruction, in his words: "we can create the dashboard
 * still and put those bank balances as zero. At least then I will know what is
 * not rendering." An unreadable account must APPEAR, marked — never be dropped.
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { BankBalancesPanel } from '../BankBalancesPanel'
import { fromTheme } from '../balancesPalette'
import { themes } from '@/lib/themes'
import { FLOOR_LABEL, type BankBalances } from '@/lib/bankBalances'
import { healthyBalances, mixedBalances, nothingRead, troubledBalances } from '@/lib/bankBalancesMock'

const palette = fromTheme(themes.light)

/** reduceMotion:true everywhere — the count-up starts at 0 and only lands on
 *  the real figure after a frame, which a static render never gets. Asserting
 *  a figure with the animation on would be asserting "0.00". */
function render(data: BankBalances | null, extra: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(
    createElement(BankBalancesPanel, { data, palette, reduceMotion: true, ...extra } as never),
  )
}

/** Strip tags so a text assertion cannot be fooled by markup in between. */
const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

/** Just the BALANCE figure on each card — the slot between "In the bank now"
 *  and "Going out". Scoped on purpose: the outgoing rows legitimately print
 *  "BWP 0.00" when nothing is queued, so a panel-wide search for that string
 *  proves nothing about the balance. (This assertion was wrong first time and
 *  the test caught it.) */
function balanceSlots(html: string): string[] {
  return text(html)
    .split('In the bank now').slice(1)
    .map((part) => part.split('Going out')[0])
}

describe('every account appears — the CFO’s instruction', () => {
  const html = render(mixedBalances)

  it('renders all six accounts, including the three never read', () => {
    for (const label of [
      'Alpha Direct — Current', 'Alpha Direct — Claims', 'Alpha Direct — Call',
      'Veritas — Current', 'Risk Software — Current', 'Unicoin — Current',
    ]) {
      expect(text(html)).toContain(label)
    }
  })

  it('groups them under their company', () => {
    for (const company of ['Alpha Direct', 'Veritas', 'Risk Software', 'Unicoin']) {
      expect(text(html)).toContain(company)
    }
  })

  it('shows the masked account number and never a full one', () => {
    expect(text(html)).toContain('…2335')
    expect(html).not.toContain('62403392335')
  })

  it('THE BLANK-CARD GUARD: every card carries its three figures, not an empty shell', () => {
    const t = text(html)
    expect(t).toContain('In the bank now')
    expect(t).toContain('Going out')
    expect(t).toContain('Waiting in Omni')
    expect(t).toContain('At the bank, unconfirmed')
    expect(t).toContain(FLOOR_LABEL)
    // Six cards → six copies of the balance label.
    expect(t.split('In the bank now').length - 1).toBe(6)
  })
})

describe('a missing balance and a confirmed zero are different things', () => {
  it('an unknown balance shows NO currency figure — it says so in words', () => {
    // The Veritas card is never_read. Render that group alone so the assertion
    // cannot be satisfied by a neighbouring card's number.
    const only: BankBalances = { ...mixedBalances, groups: [mixedBalances.groups[1]] }
    const html = render(only)
    const [slot] = balanceSlots(html)
    expect(slot).toContain('No figure')
    expect(slot).not.toContain('BWP')          // no figure of ANY kind, least of all a zero
    expect(text(html)).toContain('Never read from the bank')
    expect(text(html)).toContain('Not yet being read from the bank')
    // No balance, and therefore no floor either.
    expect(text(html)).toContain('Needs the bank balance before this can be worked out')
  })

  it('a REAL zero renders as BWP 0.00 and is not marked as missing', () => {
    // healthyBalances gives the second account of each group a confirmed "0.00".
    const only: BankBalances = {
      ...healthyBalances,
      groups: [{ ...healthyBalances.groups[0], accounts: [healthyBalances.groups[0].accounts[1]] }],
    }
    const [slot] = balanceSlots(render(only))
    expect(slot).toContain('BWP 0.00')
    expect(slot).not.toContain('No figure')
  })

  it('so the two are told apart at a glance in the same panel', () => {
    const html = render(mixedBalances)
    // The Claims account is no_balance. Its sentence is on screen, and NOT ONE
    // of the six balance slots shows an invented zero.
    expect(text(html)).toContain('The bank answered but sent no balance for this account')
    const slots = balanceSlots(html)
    expect(slots).toHaveLength(6)
    expect(slots.filter((s) => s.includes('No figure'))).toHaveLength(4)
    expect(slots.filter((s) => s.includes('BWP 0.00'))).toHaveLength(0)
  })
})

describe('the headline', () => {
  it('shows the server’s sentence verbatim — the screen never recomputes the total', () => {
    expect(text(render(mixedBalances)))
      .toContain('Cash in the six accounts: BWP 2,184,302.61 — 3 of 6 accounts read')
  })

  it('says how many accounts were read', () => {
    expect(text(render(mixedBalances))).toContain('3 of 6 accounts read')
  })

  it('carries the attention ring when not every account reported', () => {
    const html = render(mixedBalances)
    expect(html).toContain(`inset 0 0 0 1px ${palette.accent}`)
    expect(text(html)).toContain('Some accounts are not being read yet')
  })

  it('drops the ring when all six read cleanly', () => {
    const html = render(healthyBalances)
    expect(html).not.toContain(`inset 0 0 0 1px ${palette.accent}`)
    expect(text(html)).toContain('6 of 6 accounts up to date')
  })

  it('never puts accent-coloured text on the dark block (the theme-professional trap)', () => {
    // Every colour declared inside the headline block must be white; the accent
    // appears only as a ring/wash. Guarding the rendered style, not the source.
    const html = render(mixedBalances)
    const block = html.slice(html.indexOf('bank-balances-heading') - 900, html.indexOf('</h2>'))
    expect(block).not.toContain(`color:${palette.accent}`)
  })
})

describe('the failure states render, rather than emptying the screen', () => {
  it('a failed read strikes the old figure through and says why', () => {
    const html = render(troubledBalances)
    expect(html).toContain('line-through')
    expect(text(html)).toContain('This morning’s read failed')
    // The old figure is still shown — a struck-through number is information.
    expect(text(html)).toContain('BWP 48,210.00')
  })

  it('a stale reading keeps its figure and is marked amber', () => {
    const html = render(troubledBalances)
    expect(text(html)).toContain('This reading is from yesterday evening')
    expect(html).toContain(palette.warn)
  })

  it('when nothing at all could be read, all six accounts still appear', () => {
    const t = text(render(nothingRead))
    expect(t).toContain('No balance could be read from any of the six accounts this morning')
    expect(t.split('In the bank now').length - 1).toBe(6)
    expect(t).toContain('The bank connection failed this morning')
  })

  it('an error with no data says so and offers a retry, and invents no number', () => {
    const t = text(render(null, { error: 'The bank balances could not be read just now.', onRetry: () => {} }))
    expect(t).toContain('The balances could not be read just now')
    expect(t).toContain('Try again')
    expect(t).not.toContain('BWP')
  })

  it('shows a skeleton while loading, not a zero', () => {
    const t = text(render(null, { loading: true }))
    expect(t).toContain('Reading the bank…')
    expect(t).not.toContain('BWP 0.00')
  })
})

describe('the wording the CFO would be misled by', () => {
  it('labels the third figure as a floor', () => {
    expect(text(render(mixedBalances))).toContain(FLOOR_LABEL)
  })

  it('never says "closing balance" anywhere on the panel, in any state', () => {
    for (const d of [mixedBalances, healthyBalances, troubledBalances, nothingRead]) {
      expect(text(render(d)).toLowerCase()).not.toContain('closing balance')
    }
  })
})

describe('motion', () => {
  it('staggers the cards 40ms apart on mount', () => {
    const html = render(mixedBalances, { reduceMotion: false })
    expect(html).toContain('animation-delay:0ms')
    expect(html).toContain('animation-delay:40ms')
    expect(html).toContain('animation-delay:80ms')
  })

  it('the stagger runs once down the whole panel, not restarting per company', () => {
    const html = render(mixedBalances, { reduceMotion: false })
    // Six cards → the last one is the sixth step, not the first of its group.
    expect(html).toContain('animation-delay:200ms')
  })

  it('reduce-motion removes the animation entirely', () => {
    const html = render(mixedBalances, { reduceMotion: true })
    expect(html).not.toContain('animation-delay')
    expect(html).not.toContain('class="bb-rise"')
  })

  it('and the OS setting is honoured too, for anyone who never set the in-app one', () => {
    expect(render(mixedBalances)).toContain('@media (prefers-reduced-motion: reduce)')
  })
})

describe('the phone layout', () => {
  it('is a single full-width column', () => {
    expect(render(mixedBalances, { layout: 'single' })).toContain('grid-template-columns:1fr')
  })

  it('pins the headline when asked', () => {
    expect(render(mixedBalances, { layout: 'single', stickyHeadline: true })).toContain('position:sticky')
  })

  it('never pins it on the desktop', () => {
    expect(render(mixedBalances)).not.toContain('position:sticky')
  })
})
