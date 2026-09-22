/** The finance first-approver's payment leg on the phone (bug 2026-09-03).
 *
 * A payment-request task on /app/tasks used to be closed with the generic
 * POST /taskboard/tasks/<id>/complete/. That only advances a PENDING_CFO request
 * (taskboard/services.py complete_task); for PENDING_FINANCE it marked the task
 * DONE and left the payment stuck — no CFO task was ever created. The real
 * endpoint is POST /payment-requests/<id>/decide/ (taskboard/payment_views.py
 * payment_request_decide), which enforces SoD, re-checks duplicates, gates a
 * changed payee account behind PAY-BANK-ACK and hands the pack to the CFO.
 *
 * Pure helpers, no React, so the contract is unit-tested. */
import type { RawResult, JsonBody } from './StaffFormKit'

export interface BankChange {
  control?: string; payee?: string; known_account_tail?: string; new_account_tail?: string
  known_source?: string; detail?: string
}
export type Decision = 'approve' | 'reject'

/** A task row is a payment leg when the serializer attached the linked request
 * (OmniTaskBriefSerializer.payment_request_id, set only for source == 'payment_request'). */
export const isPaymentTask = (t: { payment_request_id: string | null }) => !!t.payment_request_id

export const decidePath = (requestId: string) => `/payment-requests/${requestId}/decide/`

/** Body for the decide endpoint. The server reads `bank_ack`; `bank_change_ack`
 * is sent alongside because that is the name of the control's acknowledgement
 * in the phone spec — both are only present once the approver has ticked
 * "I have checked the new bank details". */
export function decidePayload(decision: Decision, notes: string, bankAcked: boolean): JsonBody {
  return { decision, notes: notes.trim(), ...(bankAcked ? { bank_ack: true, bank_change_ack: true } : {}) }
}

export type DecideOutcome =
  | { kind: 'ok' }
  | { kind: 'bank_ack'; change: BankChange }
  | { kind: 'refused'; status: number; body: JsonBody; refresh: boolean }

/** 200 → done. 409 + control PAY-BANK-ACK → show the bank change and ask for the
 * tick. Any other 409 (already signed off) or 403 (own raise / own sign-off) →
 * show the server's words and refresh the list, because the task may be gone. */
export function classifyDecide(r: RawResult): DecideOutcome {
  if (r.ok) return { kind: 'ok' }
  if (r.status === 409 && r.body.control === 'PAY-BANK-ACK') {
    const bank = r.body.bank as { change?: BankChange } | undefined
    return { kind: 'bank_ack', change: bank?.change ?? {} }
  }
  return { kind: 'refused', status: r.status, body: r.body, refresh: r.status === 409 || r.status === 403 }
}
