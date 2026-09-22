'use client'

/**
 * DropBox — drop invoice(s) or a ZIP, Omni reads each and creates a filled DRAFT
 * payment request (CFO handover 2026-09-02). The raiser CHECKS and corrects each
 * draft, then submits it — every existing control fires on submit, server-side.
 * Omni moves no money.
 */
import { useCallback, useEffect, useState } from 'react'
import { UploadCloud, Loader2, X, Send, Trash2, AlertTriangle, CheckCircle2 } from 'lucide-react'
import {
  dropBoxInvoices, listPaymentDrafts, updatePaymentDraft, deletePaymentDraft,
  submitPaymentDraft, type PaymentDraft, type DraftSubmitAnswer,
} from '@/lib/api'

const NAVY = '#0D1B2A', ORANGE = '#F4A623'
// Premium Refund is absent on purpose: it is importer-owned and the server
// refuses a hand-keyed one (PAY-REFUND-01). The other two refunds are here so a
// copied refund draft (B7) still shows its own kind rather than falling blank.
const CATEGORIES = [
  ['', 'Choose…'], ['supplier', 'Supplier'], ['vendor', 'Vendor'],
  ['petty_cash', 'Petty cash'], ['other', 'Other (operations)'], ['claim', 'Claim'],
  ['erroneous_refund', 'Refund — erroneous payment'], ['excess_refund', 'Excess refund'],
] as const
const HAND_RAISED_REFUNDS = new Set(['erroneous_refund', 'excess_refund'])

function firstAmount(d: PaymentDraft): string {
  const ln = (d.line_items || [])[0] as { amount?: string } | undefined
  return (ln?.amount as string) || ''
}

type Line = Record<string, unknown>

/** Read a field off the draft's FIRST line. */
export function line0(d: PaymentDraft, key: string): string {
  const ln = ((d.line_items || [])[0] || {}) as Line
  return (ln[key] as string) || ''
}

/** Patch the FIRST line and KEEP every other line.
 *
 * The old inline spread rebuilt line_items as a one-element array, so editing
 * an amount on a copy of a multi-line request silently dropped lines 2..n —
 * and a copy carries every line forward.
 */
export function mergeLine0(lines: unknown[] | undefined, patch: Line): Line[] {
  const all = (lines || []) as Line[]
  const head = { ...(all[0] || {}), ...patch }
  return [head, ...all.slice(1)]
}

/** The supplier-terms fields the raiser must key on a COPY.
 *
 * duplicate_as_draft blanks amount / invoice_number / invoice_date / due_date
 * on purpose (they are the SOURCE's own invoice facts). The Drop Box card only
 * ever offered Amount, so a copied draft could not be completed: the raiser had
 * no field for the invoice number or the dates, submitted it incomplete, and
 * PAY-SUP-01 threw "terms not established" every time. Reported 2026-09-16 by
 * Koketso Kgetse and Leano Makwapa.
 */
export function InvoiceTermsFields({ draft, onPatch }: {
  draft: PaymentDraft
  onPatch: (fields: Partial<PaymentDraft>) => void
}) {
  const set = (key: string) => (e: { target: { value: string } }) =>
    onPatch({ line_items: mergeLine0(draft.line_items, { [key]: e.target.value }) as PaymentDraft['line_items'] })
  return (
    <>
      <label className="text-xs text-[#6B7280]">Invoice number
        <input defaultValue={line0(draft, 'invoice_number')} onBlur={set('invoice_number')}
               placeholder="As printed on the new invoice"
               className="w-full mt-0.5 px-2 py-1 rounded border" /></label>
      <label className="text-xs text-[#6B7280]">Invoice date
        <input type="date" defaultValue={line0(draft, 'invoice_date')} onBlur={set('invoice_date')}
               className="w-full mt-0.5 px-2 py-1 rounded border" /></label>
      <label className="text-xs text-[#6B7280]">Due date
        <input type="date" defaultValue={line0(draft, 'due_date')} onBlur={set('due_date')}
               className="w-full mt-0.5 px-2 py-1 rounded border" /></label>
    </>
  )
}

export function DropBox({ onClose, onSubmitted, notice }: {
  onClose: () => void; onSubmitted: () => void
  // B7: what just happened before this panel opened — e.g. "Copied PAY/... into
  // a new draft". Shown at the top so the copy is never a silent action.
  notice?: string | null
}) {
  const [drafts, setDrafts] = useState<PaymentDraft[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [note, setNote] = useState<string>('')
  // A submitted draft that went to the exception committee — said plainly,
  // never a silent success (Fable 5.1 audit 2026-09-02, H6).
  const [committeeNote, setCommitteeNote] = useState<string>('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  // The control that blocked a submit, and the human's answer to clear it.
  const [blocked, setBlocked] = useState<Record<string, string>>({})
  const [answers, setAnswers] = useState<Record<string, DraftSubmitAnswer>>({})
  const setAnswer = (id: string, patch: DraftSubmitAnswer) =>
    setAnswers((a) => ({ ...a, [id]: { ...a[id], ...patch } }))

  const refresh = useCallback(async () => {
    setLoading(true)
    try { setDrafts((await listPaymentDrafts()).drafts || []) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleFiles = useCallback(async (files: File[]) => {
    if (!files.length) return
    setBusy(true); setNote('')
    try {
      const res = await dropBoxInvoices(files)
      const bits = [`${res.created} draft${res.created === 1 ? '' : 's'} created`]
      if (res.unreadable?.length) bits.push(`${res.unreadable.length} could not be read — type those by hand`)
      setNote(bits.join(' · '))
      await refresh()
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'Could not read those invoices.')
    } finally { setBusy(false) }
  }, [refresh])

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false)
    handleFiles(Array.from(e.dataTransfer.files || []))
  }

  const patch = async (id: string, fields: Partial<PaymentDraft>) => {
    const updated = await updatePaymentDraft(id, fields)
    setDrafts((ds) => ds.map((d) => (d.id === id ? updated : d)))
  }
  const remove = async (id: string) => {
    await deletePaymentDraft(id)
    setDrafts((ds) => ds.filter((d) => d.id !== id))
  }
  const submit = async (id: string) => {
    setBusy(true); setErrors((e) => ({ ...e, [id]: '' }))
    try {
      const r = await submitPaymentDraft(id, answers[id] || {})
      if (r.ok) {
        setDrafts((ds) => ds.filter((d) => d.id !== id))
        setBlocked((b) => ({ ...b, [id]: '' }))
        if (r.exception?.message) setCommitteeNote(r.exception.message)
        onSubmitted()
      } else {
        // A control fired — remember which, so the raiser gets the box to clear it.
        setBlocked((b) => ({ ...b, [id]: r.control || 'BLOCKED' }))
        setErrors((e) => ({ ...e, [id]: r.detail || `Could not submit (${r.status}).` }))
      }
    } catch (e) {
      setErrors((er) => ({ ...er, [id]: e instanceof Error ? e.message : 'Could not submit.' }))
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[10000] bg-black/50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4" style={{ background: NAVY }}>
          <div className="flex items-center gap-2 text-white">
            <UploadCloud className="w-5 h-5" style={{ color: ORANGE }} />
            <h2 className="text-base font-semibold">Drop Box — drop an invoice, check the draft</h2>
          </div>
          <button onClick={onClose} className="text-white/70 hover:text-white p-1"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex-1 overflow-auto min-w-0 p-5 space-y-4">
          <label
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className="block border-2 border-dashed rounded-xl px-6 py-8 text-center cursor-pointer transition-colors"
            style={{ borderColor: dragOver ? ORANGE : '#D1D5DB', background: dragOver ? '#FFF7ED' : '#F9FAFB' }}>
            <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.heic,.tif,.tiff,.zip"
                   className="hidden"
                   onChange={(e) => handleFiles(Array.from(e.target.files || []))} />
            {busy ? (
              <span className="inline-flex items-center gap-2 text-[#6B7280]"><Loader2 className="w-5 h-5 animate-spin" /> Reading…</span>
            ) : (
              <span className="text-[#374151]">
                <UploadCloud className="w-7 h-7 mx-auto mb-1" style={{ color: NAVY }} />
                Drag invoices here, or click to choose. One, many, or a ZIP.<br />
                <span className="text-xs text-[#9CA3AF]">PDF or photo. Omni reads each and makes a draft to check.</span>
              </span>
            )}
          </label>
          {notice && (
            <div className="rounded-md p-3 text-sm"
                 style={{ background: '#F0FDF4', border: '1px solid #86EFAC', color: '#065F46' }}>
              {notice}
            </div>
          )}
          {note && <p className="text-sm text-[#0D1B2A]">{note}</p>}
          {committeeNote && (
            <div className="rounded-md p-3 text-sm flex items-start gap-2"
                 style={{ background: '#FFFBEB', border: `1px solid ${ORANGE}`, color: '#92400E' }}>
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <div className="flex-1">
                <p className="font-semibold">This went to the committee</p>
                <p className="whitespace-pre-line">{committeeNote}</p>
              </div>
              <button onClick={() => setCommitteeNote('')} className="text-xs font-medium underline">OK</button>
            </div>
          )}

          <div className="space-y-3">
            <h3 className="text-sm font-semibold" style={{ color: NAVY }}>
              Your drafts {loading ? '' : `(${drafts.length})`}
            </h3>
            {loading ? (
              <p className="text-sm text-[#9CA3AF]">Loading…</p>
            ) : drafts.length === 0 ? (
              <p className="text-sm text-[#9CA3AF]">No drafts. Drop an invoice above to make one.</p>
            ) : drafts.map((d) => {
              const need = (f: string) => (d.needs_check || []).includes(f)
              const ring = (f: string) => need(f) ? { borderColor: ORANGE, background: '#FFFBEB' } : {}
              return (
                <div key={d.id} className="border rounded-lg p-3" style={{ borderColor: '#E5E7EB' }}>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <label className="col-span-2 text-xs text-[#6B7280]">Payee
                      <input defaultValue={d.payee} onBlur={(e) => patch(d.id, { payee: e.target.value })}
                             className="w-full mt-0.5 px-2 py-1 rounded border" style={ring('payee')} /></label>
                    <label className="text-xs text-[#6B7280]">Category
                      <select defaultValue={d.category} onChange={(e) => patch(d.id, { category: e.target.value })}
                              className="w-full mt-0.5 px-2 py-1 rounded border bg-white" style={ring('category')}>
                        {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select></label>
                    <label className="text-xs text-[#6B7280]">Amount
                      <input defaultValue={firstAmount(d)}
                             onBlur={(e) => patch(d.id, { line_items: mergeLine0(d.line_items, { amount: e.target.value }) as PaymentDraft['line_items'] })}
                             className="w-full mt-0.5 px-2 py-1 rounded border text-right tabular-nums" style={ring('amount')} /></label>
                    <label className="text-xs text-[#6B7280]">Account number
                      <input defaultValue={d.account_number} onBlur={(e) => patch(d.id, { account_number: e.target.value })}
                             className="w-full mt-0.5 px-2 py-1 rounded border" style={ring('account_number')} /></label>
                    <label className="text-xs text-[#6B7280]">Bank
                      <input defaultValue={d.bank_name} onBlur={(e) => patch(d.id, { bank_name: e.target.value })}
                             className="w-full mt-0.5 px-2 py-1 rounded border" /></label>
                    <InvoiceTermsFields draft={d} onPatch={(fields) => patch(d.id, fields)} />
                    {/* B8 — a hand-raised refund must name the payment it
                        reverses. The server refuses without it (PAY-REFUND-02);
                        this is where the raiser answers it. */}
                    {HAND_RAISED_REFUNDS.has(d.category) && (
                      <label className="col-span-2 text-xs text-[#6B7280]">Original payment reference *
                        <input defaultValue={d.original_payment_ref || ''}
                               placeholder="The payment being refunded — request ref, bank reference or receipt number"
                               onBlur={(e) => patch(d.id, { original_payment_ref: e.target.value })}
                               className="w-full mt-0.5 px-2 py-1 rounded border"
                               style={ring('original_payment_ref')} /></label>
                    )}
                  </div>
                  {d.duplicated_from_ref && (
                    <p className="mt-1 text-[11px] text-[#6B7280]">
                      Duplicated from {d.duplicated_from_ref}. Key the new invoice
                      number and amount, and attach the new invoice.
                    </p>
                  )}
                  {d.source_file && <p className="mt-1 text-[11px] text-[#9CA3AF]">from {d.source_file}</p>}

                  {/* Safety catch — amount does not match the invoice (PAY-AMT-01) */}
                  {(d.amount_mismatch || blocked[d.id] === 'PAY-AMT-01') && (
                    <label className="mt-2 flex items-start gap-2 rounded-md p-2 text-xs"
                           style={{ background: '#FFFBEB', border: `1px solid ${ORANGE}` }}>
                      <input type="checkbox" className="mt-0.5"
                             checked={!!answers[d.id]?.confirm_amount}
                             onChange={(e) => setAnswer(d.id, { confirm_amount: e.target.checked })} />
                      <span className="text-[#92400E]">
                        The invoice we read said <b>{d.read_amount}</b>, but the amount here is <b>{firstAmount(d)}</b>.
                        Tick to confirm the amount really changed, then submit.
                      </span>
                    </label>
                  )}

                  {/* Supplier terms (PAY-SUP-01): an invoice not yet due needs the date it
                      should be paid (on/after the due date) or the CFO-approved reason.
                      These were never forwarded, so such a draft could never be
                      submitted (Fable 5.1 audit 2026-09-02, H6). */}
                  {blocked[d.id] === 'PAY-SUP-01' && (
                    <div className="mt-2 rounded-md p-2 text-xs space-y-1" style={{ background: '#FFFBEB', border: `1px solid ${ORANGE}` }}>
                      <p className="text-[#92400E] font-medium">
                        If the invoice is not yet due, pick the date it should be paid (on or after the due date), or give the CFO-approved reason for paying early.
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        <input type="date" value={answers[d.id]?.payment_date || ''}
                               onChange={(e) => setAnswer(d.id, { payment_date: e.target.value })}
                               className="px-2 py-1 rounded border" style={{ borderColor: ORANGE }} />
                        <input value={answers[d.id]?.early_payment_reason || ''}
                               onChange={(e) => setAnswer(d.id, { early_payment_reason: e.target.value })}
                               placeholder="CFO-approved reason (only if paying early)"
                               className="px-2 py-1 rounded border" style={{ borderColor: ORANGE }} />
                      </div>
                      <label className="flex items-start gap-2">
                        <input type="checkbox" className="mt-0.5"
                               checked={!!answers[d.id]?.discount_checked}
                               onChange={(e) => setAnswer(d.id, { discount_checked: e.target.checked })} />
                        <span className="text-[#92400E]">I checked whether an early-settlement or offshore discount applies to this invoice.</span>
                      </label>
                    </div>
                  )}

                  {/* Safety catch — first-ever payment to this payee (PAY-BANK-03) */}
                  {blocked[d.id] === 'PAY-BANK-03' && (
                    <label className="mt-2 flex items-start gap-2 rounded-md p-2 text-xs"
                           style={{ background: '#FEF2F2', border: '1px solid #FCA5A5' }}>
                      <input type="checkbox" className="mt-0.5"
                             checked={!!answers[d.id]?.new_payee_confirmed}
                             onChange={(e) => setAnswer(d.id, { new_payee_confirmed: e.target.checked })} />
                      <span className="text-[#991B1B]">
                        First time paying this payee. I checked the account digits against the invoice and confirmed the supplier through a contact I already had.
                      </span>
                    </label>
                  )}

                  {errors[d.id] && (
                    <p className="mt-2 text-xs text-[#991B1B] flex items-start gap-1">
                      <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {errors[d.id]}
                    </p>
                  )}
                  <div className="mt-2 flex items-center justify-end gap-2">
                    <button onClick={() => remove(d.id)} disabled={busy}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-[#6B7280] hover:text-[#991B1B]">
                      <Trash2 className="w-3.5 h-3.5" /> Discard
                    </button>
                    <button onClick={() => submit(d.id)} disabled={busy}
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-lg text-white"
                            style={{ background: NAVY }}>
                      <Send className="w-3.5 h-3.5" /> Check &amp; submit
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        <div className="px-5 py-3 border-t flex items-center gap-2 text-xs text-[#6B7280]" style={{ borderColor: '#E5E7EB' }}>
          <CheckCircle2 className="w-4 h-4" style={{ color: ORANGE }} />
          Every draft is checked by you before it is submitted. Omni moves no money.
        </div>
      </div>
    </div>
  )
}
