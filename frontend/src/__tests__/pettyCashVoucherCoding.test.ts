import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { join } from 'path'

/**
 * Bakang Mhusiwa, 12-Sep-2026: "I don't seem to have access to the GL line for
 * correct allocation of the expense."
 *
 * The server has ALWAYS accepted a coded petty-cash voucher from a finance
 * maker — petty_cash/api_views.create gates the expense_account on
 * UserProfile.CREATION_TITLES (Accountant / Senior Accountant / Financial
 * Controller / CFO). The new-voucher FORM simply had no box for it, so an
 * accountant could not reach a capability the backend already granted. A
 * permission that exists on the server and nowhere on the screen is not a
 * permission anybody has.
 *
 * These assertions fail against the form as it stood before 12-Sep-2026.
 */
const page = readFileSync(
  join(__dirname, '../app/(dashboard)/petty-cash/new/page.tsx'), 'utf8')

describe('new petty cash voucher — expense account', () => {
  it('offers an expense account field', () => {
    expect(page).toContain('Expense account (optional)')
  })

  it('sends the chosen account on create', () => {
    expect(page).toMatch(/expense_account:\s*expenseAccountId/)
  })

  // The one rule that must not drift: the screen decides who may code from the
  // SAME flag the server decides by (can_create_journal_entries === title in
  // CREATION_TITLES). A hand-written list of titles here is how three copies of
  // one threshold ended up disagreeing on UniCoin.
  it('gates on the server\'s own capability, not a copied title list', () => {
    expect(page).toContain('can_create_journal_entries')
    expect(page).not.toMatch(/'Senior Accountant'|"Senior Accountant"/)
  })

  // Coding stays OPTIONAL: CFO directive 2026-08-10 — an ordinary requester
  // raises an uncoded voucher and the petty-cash team codes it.
  it('keeps the field optional and omits it when blank', () => {
    expect(page).toContain('Leave for the petty-cash team to code')
    expect(page).toMatch(/canCode && expenseAccountId \? \{ expense_account/)
  })
})
