import { describe, it, expect } from 'vitest'
import { lineFromHistoryRow } from '@/app/(dashboard)/payment-requests/reloadLine'
import { PaymentHistoryRow, PaymentLine } from '@/lib/api'

describe('lineFromHistoryRow', () => {
  const createRow = (overrides?: Partial<PaymentHistoryRow>): PaymentHistoryRow => ({
    request_id: 'req-123',
    ref: 'ref-456',
    date: '2026-08-15',
    entity: 'Entity A',
    line: 1,
    claim_number: 'CLM-789',
    payee: 'Supplier Corp',
    invoice_number: 'INV-001',
    description: 'Consulting services',
    currency: 'BWP',
    amount: '10000',
    status: 'paid',
    status_label: 'Paid',
    cancelled: false,
    copyable: true,
    copy: {
      description: 'Consulting services',
      gl_code: '4100',
      ref: 'ref-456',
      terms_basis: 'invoice',
      terms_days: '14',
      claim_number: 'CLM-789',
      pop_recipient_name: 'John Doe',
      pop_recipient_email: 'john@example.com',
      copied_from_ref: 'ref-456',
    },
    ...overrides,
  })

  it('returns null when row is not copyable', () => {
    const row = createRow({ copyable: false })
    const result = lineFromHistoryRow(row)
    expect(result).toBeNull()
  })

  it('creates a line with amount BLANK even though history row has amount', () => {
    const row = createRow()
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.amount).toBe('')
  })

  it('creates a line with invoice_number BLANK', () => {
    const row = createRow()
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.invoice_number).toBe('')
  })

  it('creates a line with invoice_date BLANK', () => {
    const row = createRow()
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.invoice_date).toBe('')
  })

  it('carries copied_from_ref from copy data', () => {
    const row = createRow({
      copy: {
        copied_from_ref: 'ref-456',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.copied_from_ref).toBe('ref-456')
  })

  it('carries description from copy data', () => {
    const row = createRow({
      copy: {
        description: 'Custom consulting work',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.description).toBe('Custom consulting work')
  })

  it('carries gl_code from copy data', () => {
    const row = createRow({
      copy: {
        gl_code: '4200',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.gl_code).toBe('4200')
  })

  it('carries ref from copy data', () => {
    const row = createRow({
      copy: {
        ref: 'ref-999',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.ref).toBe('ref-999')
  })

  it('carries terms_basis from copy data', () => {
    const row = createRow({
      copy: {
        terms_basis: 'statement',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.terms_basis).toBe('statement')
  })

  it('carries terms_days from copy data', () => {
    const row = createRow({
      copy: {
        terms_days: '45',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.terms_days).toBe('45')
  })

  it('carries claim_number from copy data', () => {
    const row = createRow({
      copy: {
        claim_number: 'CLM-999',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.claim_number).toBe('CLM-999')
  })

  it('carries pop_recipient_name from copy data', () => {
    const row = createRow({
      copy: {
        pop_recipient_name: 'Jane Smith',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.pop_recipient_name).toBe('Jane Smith')
  })

  it('carries pop_recipient_email from copy data', () => {
    const row = createRow({
      copy: {
        pop_recipient_email: 'jane@example.com',
      },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.pop_recipient_email).toBe('jane@example.com')
  })

  it('sets due_date to empty (derived later, not copied)', () => {
    const row = createRow()
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.due_date).toBe('')
  })

  it('sets discount_checked to false (user decides again)', () => {
    const row = createRow()
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.discount_checked).toBe(false)
  })

  it('defaults terms_basis to statement when not in copy data', () => {
    const row = createRow({
      copy: { terms_basis: undefined },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.terms_basis).toBe('statement')
  })

  it('defaults terms_days to 30 when not in copy data', () => {
    const row = createRow({
      copy: { terms_days: undefined },
    })
    const result = lineFromHistoryRow(row)
    expect(result).not.toBeNull()
    expect(result!.terms_days).toBe('30')
  })

  it('allows overrides via base parameter', () => {
    const row = createRow({
      copy: {
        description: 'Original description',
      },
    })
    const result = lineFromHistoryRow(row, { description: 'Override description' })
    expect(result).not.toBeNull()
    expect(result!.description).toBe('Override description')
  })
})
