import { describe, expect, it } from 'vitest'

import { cellText } from '../_shared'

/**
 * The BONU schedule is read side by side with the Excel it was loaded from. Omni
 * stores each cell verbatim, so it holds Excel's full float tail — the VAT cell the
 * spreadsheet displays as -81,393.43 is stored as '-81393.42842105262'. On screen
 * that reads as a mismatch when the two figures are the same number (CFO, 13 Aug
 * 2026: "yes please fix the long decimals").
 *
 * These pin BOTH halves: the tail is rounded away for the eye, and everything that
 * is not a decimal number is left completely alone.
 */
describe('cellText — how a stored schedule cell reads on screen', () => {
  it('rounds Excel float tails to two places, with thousands separators', () => {
    expect(cellText('-81393.42842105262')).toBe('-81,393.43')
    expect(cellText('8513570.759999996')).toBe('8,513,570.76')
    expect(cellText('662024.999')).toBe('662,025.00')
    expect(cellText('-30.520674918334468')).toBe('-30.52')
  })

  it('shows a two-decimal figure unchanged in value, separated for reading', () => {
    expect(cellText('12228693.57')).toBe('12,228,693.57')
  })

  it('leaves an integer completely alone — it may be a reference, not an amount', () => {
    // A leading-zero invoice reference must never become '861.00'.
    expect(cellText('000861')).toBe('000861')
    expect(cellText('316115')).toBe('316115')
    expect(cellText('2025')).toBe('2025')
  })

  it('leaves dates, text and blanks alone', () => {
    expect(cellText('2025-01-01')).toBe('2025-01-01')
    expect(cellText('2026 Aug')).toBe('2026 Aug')
    expect(cellText('Jeremiah & Co')).toBe('Jeremiah & Co')
    expect(cellText('Paid ')).toBe('Paid ')
    expect(cellText('')).toBe('')
    expect(cellText(null)).toBe('')
    expect(cellText(undefined)).toBe('')
  })

  it('does not touch something that merely contains digits and a dot', () => {
    expect(cellText('ITA-D267-2026')).toBe('ITA-D267-2026')
    expect(cellText('1.2.3')).toBe('1.2.3')
  })
})
