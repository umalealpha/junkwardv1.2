/** A payslip's breakdown lines, safe against a payslip that arrives without one.
 *
 * The screen used to read `slip.detail.earnings.length` directly, so a payslip
 * with no `detail` block took down the whole payslips screen with
 * "Cannot read properties of undefined (reading 'earnings')" — the staff member
 * saw an error page instead of their pay. Every payslip on production carries
 * the block today, which is exactly why this was easy to miss. */
export interface SlipLine { label: string; amount: string }
export interface SlipDetail { earnings?: SlipLine[]; deductions?: SlipLine[] }

export function slipLines(detail: SlipDetail | null | undefined): { earnings: SlipLine[]; deductions: SlipLine[] } {
  return { earnings: detail?.earnings ?? [], deductions: detail?.deductions ?? [] }
}

/** True when there is a breakdown worth showing behind the "Show detail" toggle. */
export function hasSlipDetail(detail: SlipDetail | null | undefined): boolean {
  const { earnings, deductions } = slipLines(detail)
  return earnings.length > 0 || deductions.length > 0
}
