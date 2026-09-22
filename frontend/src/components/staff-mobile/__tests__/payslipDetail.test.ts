import { describe, it, expect } from 'vitest'
import { slipLines, hasSlipDetail } from '../payslipDetail'

describe('payslip breakdown', () => {
  // The real defect: a payslip with no detail block crashed the whole screen.
  it('survives a payslip with no detail block at all', () => {
    expect(() => slipLines(undefined)).not.toThrow()
    expect(slipLines(undefined)).toEqual({ earnings: [], deductions: [] })
    expect(hasSlipDetail(undefined)).toBe(false)
  })

  it('survives a detail block missing its arrays', () => {
    expect(slipLines({})).toEqual({ earnings: [], deductions: [] })
    expect(hasSlipDetail({})).toBe(false)
  })

  it('survives a null detail block', () => {
    expect(hasSlipDetail(null)).toBe(false)
  })

  it('still reports a real breakdown', () => {
    const d = { earnings: [{ label: 'Basic salary', amount: '24000.00' }], deductions: [] }
    expect(hasSlipDetail(d)).toBe(true)
    expect(slipLines(d).earnings).toHaveLength(1)
    expect(slipLines(d).deductions).toEqual([])
  })

  it('counts deductions on their own as a breakdown', () => {
    expect(hasSlipDetail({ deductions: [{ label: 'PAYE', amount: '3837.50' }] })).toBe(true)
  })
})
