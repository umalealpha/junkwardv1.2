import { describe, expect, test } from 'vitest'
import { classifyDecide, decidePath, decidePayload, isPaymentTask } from '../paymentDecide'

describe('finance first-approver payment leg (phone)', () => {
  test('a task is a payment leg only when the serializer attached payment_request_id', () => {
    expect(isPaymentTask({ payment_request_id: '11111111-2222-4333-8444-555555555555' })).toBe(true)
    expect(isPaymentTask({ payment_request_id: null })).toBe(false)
  })

  test('the decision goes to /payment-requests/<id>/decide/, never /taskboard/tasks/<id>/complete/', () => {
    const p = decidePath('11111111-2222-4333-8444-555555555555')
    expect(p).toBe('/payment-requests/11111111-2222-4333-8444-555555555555/decide/')
    expect(p).not.toContain('/complete/')
  })

  test('payload carries decision + trimmed notes; the bank acknowledgement only once ticked', () => {
    expect(decidePayload('reject', '  wrong invoice  ', false)).toEqual({ decision: 'reject', notes: 'wrong invoice' })
    expect(decidePayload('approve', '', false)).not.toHaveProperty('bank_ack')
    const acked = decidePayload('approve', '', true)
    expect(acked.bank_ack).toBe(true)          // what taskboard/payment_views.py reads
    expect(acked.bank_change_ack).toBe(true)   // the control's own name
  })

  test('409 PAY-BANK-ACK surfaces the bank change verbatim instead of an error', () => {
    const change = { known_account_tail: '4410', new_account_tail: '7788', detail: 'You paid X into 4410 before; this says 7788.' }
    const out = classifyDecide({ ok: false, status: 409, body: { detail: 'Confirm the new account.', control: 'PAY-BANK-ACK', bank: { change } } })
    expect(out).toEqual({ kind: 'bank_ack', change })
  })

  test('409 already-signed and 403 SoD are refused AND trigger a list refresh; 200 is ok', () => {
    expect(classifyDecide({ ok: true, status: 200, body: { status: 'pending_cfo' } })).toEqual({ kind: 'ok' })
    const dup = classifyDecide({ ok: false, status: 409, body: { detail: 'This request has already been signed off.' } })
    expect(dup.kind).toBe('refused'); if (dup.kind === 'refused') expect(dup.refresh).toBe(true)
    const sod = classifyDecide({ ok: false, status: 403, body: { detail: 'You cannot sign off a payment request you raised (segregation of duties).' } })
    expect(sod.kind).toBe('refused'); if (sod.kind === 'refused') expect(sod.refresh).toBe(true)
    const bad = classifyDecide({ ok: false, status: 400, body: { detail: 'Please give a reason when rejecting a payment request.' } })
    expect(bad.kind).toBe('refused'); if (bad.kind === 'refused') expect(bad.refresh).toBe(false)
  })
})
