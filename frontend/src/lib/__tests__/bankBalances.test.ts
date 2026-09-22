/**
 * The rules of BALANCES_CONTRACT.md that a screen must not get wrong.
 *
 * The bug this whole feature exists to stop: `fnb/statements.py` defaults
 * `closing_balance` to 0.00 and only overwrites it when the bank sends a CLBD
 * block, so "the bank returned no balance" and "the account is genuinely empty"
 * are indistinguishable. The contract fixes that on the wire (null vs "0.00")
 * and `hasFigure` is the only test the screens are allowed to make.
 */
import { describe, expect, it } from 'vitest'
import {
  BALANCE_LABEL, FLOOR_LABEL, formatBwp, gaboroneTime, hasFigure, isMoreSerious,
  needsAttention, readingLabel, statusMeta, type BalancesHeadline,
} from '../bankBalances'

const headline = (over: Partial<BalancesHeadline> = {}): BalancesHeadline => ({
  total_balance: '1.00', accounts_read: 6, accounts_fresh: 6, accounts_total: 6,
  worst_status: 'ok', taken_at: '2026-09-20T04:04:12Z', sentence: 's', ...over,
})

describe('hasFigure — unknown vs a confirmed zero', () => {
  it('THE BUG: "0.00" is a REAL balance and must not read as unknown', () => {
    expect(hasFigure('0.00')).toBe(true)
    expect(hasFigure('0')).toBe(true)
  })

  it('null is the only way to say "we do not know"', () => {
    expect(hasFigure(null)).toBe(false)
    expect(hasFigure(undefined)).toBe(false)
    expect(hasFigure('')).toBe(false)
    expect(hasFigure('   ')).toBe(false)
  })
})

describe('the floor is never called a closing balance', () => {
  it('is worded as a floor', () => {
    expect(FLOOR_LABEL).toBe('Lowest you could be left with')
  })

  it('never contains the words "closing balance" in any casing', () => {
    expect(FLOOR_LABEL.toLowerCase()).not.toContain('closing balance')
    expect(BALANCE_LABEL.toLowerCase()).not.toContain('closing balance')
  })
})

describe('needsAttention', () => {
  it('is calm only when every account reported and nothing is off', () => {
    expect(needsAttention(headline())).toBe(false)
  })

  it('fires when an account did not report', () => {
    expect(needsAttention(headline({ accounts_read: 3, accounts_fresh: 3 }))).toBe(true)
  })

  it('fires when every account has a figure but two are not current', () => {
    // accounts_read is six — the old check was calm here. An account that
    // fell back to an older good reading still has a number, which is exactly
    // why counting figures rather than fresh figures hid the problem.
    expect(needsAttention(headline({ accounts_read: 6, accounts_fresh: 4 }))).toBe(true)
  })

  it('fires when all six reported but one reading is stale — the half that is easy to miss', () => {
    expect(needsAttention(headline({ accounts_read: 6, accounts_fresh: 6, worst_status: 'stale' }))).toBe(true)
  })
})

describe('status table', () => {
  it('strikes the figure only for a failed read, where the number shown is old', () => {
    expect(statusMeta('failed').strike).toBe(true)
    for (const s of ['ok', 'stale', 'no_balance', 'never_read'] as const) {
      expect(statusMeta(s).strike).toBe(false)
    }
  })

  it('tones the timestamp per the contract: muted / amber / red', () => {
    expect(statusMeta('ok').tone).toBe('muted')
    expect(statusMeta('stale').tone).toBe('warn')
    expect(statusMeta('no_balance').tone).toBe('warn')
    expect(statusMeta('never_read').tone).toBe('warn')
    expect(statusMeta('failed').tone).toBe('danger')
  })

  it('carries a plain sentence for every status the server may leave blank', () => {
    for (const s of ['stale', 'failed', 'no_balance', 'never_read'] as const) {
      expect(statusMeta(s).fallbackNote.length).toBeGreaterThan(0)
    }
  })

  it('ranks a failed read as the most serious', () => {
    expect(isMoreSerious('failed', 'never_read')).toBe(true)
    expect(isMoreSerious('stale', 'ok')).toBe(true)
    expect(isMoreSerious('ok', 'stale')).toBe(false)
  })
})

describe('formatBwp — full thebe, never the dashboard shorthand', () => {
  it('shows every digit to two places', () => {
    expect(formatBwp('2184302.61')).toBe('BWP 2,184,302.61')
    expect(formatBwp('0.00')).toBe('BWP 0.00')
    expect(formatBwp(1189199.5)).toBe('BWP 1,189,199.50')
  })

  it('brackets a negative floor rather than hiding the minus sign', () => {
    expect(formatBwp('-779921.25')).toBe('(BWP 779,921.25)')
  })

  it('never abbreviates to millions — "BWP 2.18M" cannot answer "what can I pay today"', () => {
    expect(formatBwp('2184302.61')).not.toContain('M')
  })

  it('does not invent a number from rubbish', () => {
    expect(formatBwp('not-a-number')).toBe('BWP —')
  })
})

describe('gaboroneTime', () => {
  it('reads the Botswana morning whatever the viewer’s timezone is', () => {
    // 04:04 UTC is 06:04 in Gaborone (UTC+2, no DST).
    expect(gaboroneTime('2026-09-20T04:04:12Z')).toBe('06:04')
  })

  it('returns empty rather than "Invalid Date" for junk', () => {
    expect(gaboroneTime('nonsense')).toBe('')
  })
})

describe('readingLabel — built from the server’s age_hours, not the browser clock', () => {
  it('says so plainly when the account has never been read', () => {
    expect(readingLabel({ taken_at: null, age_hours: null, balance_status: 'never_read' }))
      .toBe('Never read from the bank')
  })

  it('reads as hours inside a day', () => {
    expect(readingLabel({ taken_at: '2026-09-20T04:04:12Z', age_hours: 3.2, balance_status: 'ok' }))
      .toBe('Read 3 hours ago, at 06:04')
  })

  it('rolls over to days once it is older than two', () => {
    expect(readingLabel({ taken_at: '2026-09-18T04:04:12Z', age_hours: 51.2, balance_status: 'failed' }))
      .toBe('Read 2 days ago, at 06:04')
  })

  it('never claims a time when there is no reading on record', () => {
    expect(readingLabel({ taken_at: null, age_hours: null, balance_status: 'failed' }))
      .toBe('No reading on record')
  })
})
