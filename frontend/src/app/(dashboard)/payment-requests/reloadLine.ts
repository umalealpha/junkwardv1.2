import { PaymentLine, PaymentHistoryRow } from '@/lib/api'

/**
 * Convert a payment history row into a new PaymentLine for reloading.
 *
 * When a user reloads a previous payment:
 * - Copy metadata (description, gl_code, ref, terms data, claim_number, pop recipients)
 * - Keep amount BLANK (reloading last month's amount is the paid-twice risk)
 * - Keep invoice_number and invoice_date blank (user must re-enter)
 * - Carry copied_from_ref for duplicate-payment checking
 *
 * Returns null if the row is not copyable.
 *
 * @param row - The history row from the server (with copy=1 param)
 * @param base - Optional base line to merge with (defaults to minimal line)
 * @returns A new PaymentLine ready to add, or null if not copyable
 */
export function lineFromHistoryRow(
  row: PaymentHistoryRow,
  base?: Partial<PaymentLine>,
): PaymentLine | null {
  if (!row.copyable) return null

  const copy = row.copy || {}
  return {
    description: copy.description || '',
    gl_code: copy.gl_code || '',
    ref: copy.ref || '',
    amount: '',  // BLANK — the user must re-enter
    invoice_number: '',  // BLANK — must be re-entered
    invoice_date: '',  // BLANK — must be re-entered
    terms_basis: copy.terms_basis || 'statement',
    terms_days: copy.terms_days || '30',
    claim_number: copy.claim_number || '',
    pop_recipient_name: copy.pop_recipient_name || '',
    pop_recipient_email: copy.pop_recipient_email || '',
    due_date: '',  // Derived from terms, not copied
    discount_checked: false,  // User must decide again
    copied_from_ref: copy.copied_from_ref || '',
    ...base,  // Allow overrides
  }
}
