/**
 * The bug this pins (reported 2026-09-16 by Koketso Kgetse and Leano Makwapa):
 *
 * Copy makes a new DRAFT and deliberately blanks the source's own invoice facts
 * (amount / invoice_number / invoice_date / due_date). The Drop Box card only
 * offered Amount, so the raiser had nowhere to key the invoice number or the
 * dates — the copy could only be submitted incomplete, and PAY-SUP-01 threw
 * "terms not established" every time ("the option is denied").
 *
 * Second bug pinned here: the old amount edit rebuilt line_items as a
 * one-element array, dropping lines 2..n of a copied multi-line request.
 */
import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { InvoiceTermsFields, mergeLine0, line0 } from '../DropBox'
import type { PaymentDraft } from '@/lib/api'

const draft = {
  id: 'd1', ref: 'PAY/ADIC/2026/09/16/0013',
  line_items: [{ description: 'Line one' }, { description: 'Line two', amount: '50' }],
} as unknown as PaymentDraft

const render = (d: PaymentDraft) =>
  renderToStaticMarkup(createElement(InvoiceTermsFields, { draft: d, onPatch: () => {} }))

describe('a copied draft can be completed', () => {
  it('THE BUG: the card offers invoice number, invoice date and due date', () => {
    const html = render(draft)
    expect(html).toContain('Invoice number')
    expect(html).toContain('Invoice date')
    expect(html).toContain('Due date')
    // and the two dates are real date pickers, not free text
    expect(html.match(/type="date"/g)?.length).toBe(2)
  })

  it('shows what is already on the line rather than an empty box', () => {
    const filled = { line_items: [{ invoice_number: 'INV-77' }] } as unknown as PaymentDraft
    expect(render(filled)).toContain('INV-77')
    expect(line0(filled, 'invoice_number')).toBe('INV-77')
  })
})

describe('mergeLine0', () => {
  it('THE BUG: editing line one must not delete the other lines', () => {
    const out = mergeLine0(draft.line_items, { amount: '100' })
    expect(out).toHaveLength(2)
    expect(out[1]).toEqual({ description: 'Line two', amount: '50' })
  })

  it('writes the field onto line one and keeps its other fields', () => {
    const out = mergeLine0(draft.line_items, { invoice_number: 'INV-1' })
    expect(out[0]).toEqual({ description: 'Line one', invoice_number: 'INV-1' })
  })

  it('copes with a draft that has no lines at all', () => {
    expect(mergeLine0(undefined, { amount: '5' })).toEqual([{ amount: '5' }])
  })
})
