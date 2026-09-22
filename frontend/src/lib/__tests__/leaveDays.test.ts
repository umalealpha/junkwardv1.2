import { describe, expect, it } from 'vitest'
import { estimateWorkingDays } from '../leaveDays'

describe('estimateWorkingDays — the apply-form heads-up estimate', () => {
  it("matches Snehal's request (Thu 17 -> Fri 18 Sep 2026 = 2 working days)", () => {
    // The exact case that triggered this feature: the server said "requested: 2d".
    expect(estimateWorkingDays('2026-09-17', '2026-09-18', false, false)).toBe(2)
  })

  it('counts a single weekday as one day', () => {
    expect(estimateWorkingDays('2026-09-17', '2026-09-17', false, false)).toBe(1)
  })

  it('a single-day half request is half a day', () => {
    expect(estimateWorkingDays('2026-09-17', '2026-09-17', true, false)).toBe(0.5)
  })

  it('excludes weekends across a range (Fri -> Mon = 2)', () => {
    expect(estimateWorkingDays('2026-09-18', '2026-09-21', false, false)).toBe(2)
  })

  it('counts a full Mon–Fri week as five', () => {
    expect(estimateWorkingDays('2026-09-14', '2026-09-18', false, false)).toBe(5)
  })

  it('trims a half day off each chosen boundary of a range', () => {
    expect(estimateWorkingDays('2026-09-14', '2026-09-18', true, true)).toBe(4)
  })

  it('a Saturday-only request is zero working days', () => {
    expect(estimateWorkingDays('2026-09-19', '2026-09-19', false, false)).toBe(0)
  })

  it('returns null when the end is before the start', () => {
    expect(estimateWorkingDays('2026-09-18', '2026-09-17', false, false)).toBeNull()
  })

  it('returns null for an incomplete date range', () => {
    expect(estimateWorkingDays('', '2026-09-18', false, false)).toBeNull()
    expect(estimateWorkingDays('2026-09-17', '', false, false)).toBeNull()
  })

  it('DELIBERATELY ignores public holidays — it is a rough advisory, and the '
     + 'server (which knows holidays and the accrual cut-off) is the exact gate', () => {
    // 30 Sep 2026 is Botswana Day (a Wednesday). The on-screen estimate counts
    // it as a normal working day ON PURPOSE — this warning never blocks a
    // submit, so a small over-count is fine and the server has the last word.
    // This test pins the scope boundary so it is not "fixed" into the frontend.
    expect(estimateWorkingDays('2026-09-30', '2026-09-30', false, false)).toBe(1)
  })
})
