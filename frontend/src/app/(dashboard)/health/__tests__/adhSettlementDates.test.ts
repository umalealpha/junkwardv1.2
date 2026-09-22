// Pinned BEFORE the import, and set on the process rather than as a shell
// prefix, because this build machine sits at UTC+02:00 — the one zone where
// both traps below are invisible — and Windows Node ignores a `TZ=… ` prefix
// but does honour process.env.TZ. Without this line these cases pass whether
// or not the helpers are correct, which is a comment, not a test.
process.env.TZ = 'America/New_York'

import { describe, it, expect } from 'vitest'
import { formatDay, formatTime } from '../adh-settlements/dates'

/**
 * The ADH settlement files land Saturday around 01:20 Gaborone. That is
 * 23:20 UTC on the FRIDAY — so both of these renderings sit exactly on the
 * boundary where the day slides, and both are written to not slide.
 *
 * These cases DISCRIMINATE: each asserts the value the naive rendering would
 * NOT produce, so reverting either helper turns the test red.
 */
describe('the settlement Saturday, as a plain calendar date', () => {
  it('shows the date the server wrote, not a UTC midnight re-read locally', () => {
    // new Date('2026-09-12').toLocaleDateString() is 11 Sep for any reader
    // behind UTC. The 12th is the whole point — that is the file's name.
    // Matched loosely on purpose: the day and the weekday are what must be
    // right, and "Sep" vs "Sept" is only which ICU data the reader happens to
    // have. Pinning the spelling would fail on a browser, not on a bug.
    expect(formatDay('2026-09-12')).toMatch(/^Sat, 12 Sept? 2026$/)
  })

  it('ignores anything after the date, so a timestamp cannot move the day', () => {
    expect(formatDay('2026-09-12T23:59:59+02:00')).toMatch(/^Sat, 12 Sept? 2026$/)
    expect(formatDay('2026-09-12T00:00:00Z')).toMatch(/^Sat, 12 Sept? 2026$/)
  })

  it('returns a value it cannot parse untouched rather than rendering nonsense', () => {
    expect(formatDay('')).toBe('')
    expect(formatDay('not a date')).toBe('not a date')
  })
})

describe('the time the run actually happened, in Gaborone', () => {
  it('reads 23:20 UTC on the Friday as 01:20 on the Saturday', () => {
    // The naive rendering gives 23:20 on a UTC machine — the run would look
    // like it happened on Friday night, which is the bug this guards.
    expect(formatTime('2026-09-11T23:20:00Z')).toBe('01:20')
    expect(formatTime('2026-09-11T23:20:00Z')).not.toBe('23:20')
  })

  it('leaves the column empty rather than printing Invalid Date', () => {
    expect(formatTime(null)).toBe('')
    expect(formatTime(undefined)).toBe('')
    expect(formatTime('nonsense')).toBe('')
  })
})
