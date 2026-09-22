'use client'

/** Raise a payment from the phone — the Drop Box flow (CFO handover 2026-09-02),
 * mirroring frontend/src/components/payments/DropBox.tsx and the create form in
 * frontend/src/app/(dashboard)/payment-requests/page.tsx. Backend: taskboard/dropbox_views.py.
 *   POST   /payment-requests/drop-box/              photo/PDF as `files` → Omni reads it → one DRAFT per invoice
 *   GET    /payment-requests/drafts/                my drafts (a draft enters NO approval path until submitted)
 *   PATCH  /payment-requests/drafts/<id>/           the raiser correcting what was read (same field names as desktop)
 *   DELETE /payment-requests/drafts/<id>/           discard
 *   POST   /payment-requests/drafts/<id>/submit/    submit — the server re-runs EVERY control (PAY-AMT-01, PAY-SUP-01,
 *                                                   PAY-BANK-02/03, PAY-DUP-01, claims match); its message is shown verbatim
 *   POST   /payment-requests/                       CLAIMS drafts only — see submit() for why
 * Omni moves no money. Nothing here computes an amount — figures are the server's. */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Camera, Send, Trash2 } from 'lucide-react'
import { compressImage, reauthOn401, sfetch } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch, type JsonBody,
} from './StaffFormKit'
import { handoffFile, takeSnapHandoff } from './snapHandoff'

interface DraftLine {
  description?: string; amount?: string; invoice_number?: string; invoice_date?: string
  terms_basis?: string; terms_days?: string; due_date?: string; discount_checked?: boolean; claim_number?: string
}
interface Draft {
  id: string; ref: string; status: string; subject: string; payee: string; category: string
  entity: string; currency: string; account_name: string; account_number: string; bank_name: string; branch_code: string
  line_items: DraftLine[]; needs_check: string[]; source_file: string
  original_payment_ref?: string; duplicated_from_ref?: string
  read_amount: string | null; amount_mismatch: boolean; bank_change_reason: string; created_at: string
}
interface Answer {
  confirm_amount?: boolean; new_payee_confirmed?: boolean; bank_change_reason?: string
  payment_date?: string; early_payment_reason?: string; discount_checked?: boolean
}
interface MeCompany { id: string; code: string; name: string }
interface Done { ref: string; exception?: { control?: string; message?: string } }

// Same lists as the desktop create form / DropBox.
const CURRENCIES = ['BWP', 'ZAR', 'USD', 'INR']
const OPS_CATEGORIES = [['supplier', 'Supplier'], ['vendor', 'Vendor'], ['petty_cash', 'Petty cash'], ['other', 'Other (operations)']] as const
// B8 — Refund is a THIRD top-level payment type here too. Missing this screen
// would leave staff on phones with two options and no way to reach a refund at
// all. Premium Refund is deliberately NOT offered: the overnight importer owns
// it and the server refuses a hand-keyed one (PAY-REFUND-01).
const REFUND_CATEGORIES = [['erroneous_refund', 'Refund — erroneous payment'], ['excess_refund', 'Excess refund']] as const
const REFUND_KEYS: readonly string[] = ['premium_refund', 'erroneous_refund', 'excess_refund']

/** Same derivation as the desktop page (a Next page file cannot export it):
 * terms run from the STATEMENT the invoice lands on (month-end) unless the
 * basis is 'invoice'. The server re-derives and refuses a shortened term. */
function dueFromTerms(invoiceDate: string, termsDays: string, basis: string): string {
  if (!invoiceDate) return ''
  const d = new Date(invoiceDate)
  if (Number.isNaN(d.getTime())) return ''
  const anchor = basis === 'invoice' ? d : new Date(d.getFullYear(), d.getMonth() + 1, 0)
  anchor.setDate(anchor.getDate() + (Number(termsDays) || 30))
  return `${anchor.getFullYear()}-${String(anchor.getMonth() + 1).padStart(2, '0')}-${String(anchor.getDate()).padStart(2, '0')}`
}
const line0 = (d: Draft): DraftLine => (d.line_items || [])[0] || {}

export default function RaisePaymentScreen() {
  const base = useStaffBase()
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [companies, setCompanies] = useState<MeCompany[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reading, setReading] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)                 // the draft being checked
  const [payeeType, setPayeeType] = useState<Record<string, '' | 'client' | 'provider'>>({})
  const [answers, setAnswers] = useState<Record<string, Answer>>({})
  const [blocked, setBlocked] = useState<Record<string, { control: string; text: string }>>({})
  const [done, setDone] = useState<Done | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }
  const setAnswer = (id: string, patch: Answer) => setAnswers(a => ({ ...a, [id]: { ...a[id], ...patch } }))

  const load = useCallback(() => {
    setLoadErr(null)
    sfetch<{ drafts: Draft[] }>('/payment-requests/drafts/')
      .then(r => { setDrafts(r.drafts || []); setOpen(o => o || (r.drafts?.[0]?.id ?? null)) })
      .catch(e => { if (!reauthOn401(e)) { setDrafts(d => d ?? []); setLoadErr(errText(e, 'Could not load your drafts.')) } })
    sfetch<{ companies: MeCompany[] }>('/me/companies/').then(r => setCompanies(r.companies || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  // Arrived from Snap (/app/snap) with an invoice photo already taken? Send it
  // to the Drop Box exactly as the camera button below would — once.
  useEffect(() => {
    const h = takeSnapHandoff('invoice')
    if (!h) return
    handoffFile(h).then(readInvoice).catch(() => setNote('Could not reuse your snap — take it again below.'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSnap = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]
    e.target.value = ''
    if (!raw) return
    await readInvoice(await compressImage(raw))          // a 3–12 MB camera shot → a few hundred KB
  }

  async function readInvoice(f: File) {
    setReading(true); setNote(null)
    try {
      const fd = new FormData(); fd.append('files', f)
      const r = await sfetch<{ created: number; drafts: Draft[]; unreadable: string[] }>('/payment-requests/drop-box/', { method: 'POST', body: fd }, false)
      const bits = [`${r.created} draft${r.created === 1 ? '' : 's'} created from what Omni read`]
      if (r.unreadable?.length) bits.push(`${r.unreadable.length} could not be read — type it by hand`)
      setNote(bits.join(' · '))
      setDrafts(ds => [...(r.drafts || []), ...(ds || [])])
      if (r.drafts?.[0]) setOpen(r.drafts[0].id)
    } catch (err) {
      if (!reauthOn401(err)) setNote(errText(err, 'Could not read that invoice.'))
    } finally { setReading(false) }
  }

  // PATCH one field (or the first line) and keep the server's copy — it is the
  // server that clears `needs_check` and recomputes `amount_mismatch`.
  const patch = async (d: Draft, fields: Partial<Draft>) => {
    try {
      const r = await rawStaffFetch(`/payment-requests/drafts/${d.id}/`, { method: 'PATCH', body: JSON.stringify(fields) })
      if (!r.ok) { show(formatServerErrors(r.body, r.status)); return }
      const upd = r.body as unknown as Draft
      setDrafts(ds => (ds || []).map(x => x.id === d.id ? upd : x))
    } catch (e) { if (!reauthOn401(e)) show(errText(e, 'Could not save that change.')) }
  }
  const patchLine = (d: Draft, fields: DraftLine) => patch(d, { line_items: [{ ...line0(d), ...fields }] })

  const remove = async (d: Draft) => {
    if (!window.confirm('Discard this draft?')) return
    setBusy(true)
    try {
      const r = await rawStaffFetch(`/payment-requests/drafts/${d.id}/`, { method: 'DELETE' })
      if (!r.ok && r.status !== 204) { show(formatServerErrors(r.body, r.status)); return }
      setDrafts(ds => (ds || []).filter(x => x.id !== d.id)); if (open === d.id) setOpen(null)
    } catch (e) { if (!reauthOn401(e)) show(errText(e, 'Could not discard.')) }
    finally { setBusy(false) }
  }

  async function submit(d: Draft) {
    setBusy(true); setBlocked(b => ({ ...b, [d.id]: { control: '', text: '' } }))
    const a = answers[d.id] || {}
    try {
      let r
      if (d.category === 'claim') {
        // A claims pack must say who is paid (claim_payee_type) — the draft PATCH
        // does not carry that field, so the draft route could never pass the
        // server's claims check. Use the create endpoint the draft submit wraps
        // internally, with the SAME fields the draft holds, then drop the draft.
        // The Drop Box safety catch (PAY-AMT-01) still applies: the server's own
        // amount_mismatch flag must be confirmed first.
        if (d.amount_mismatch && !a.confirm_amount) {
          setBlocked(b => ({ ...b, [d.id]: { control: 'PAY-AMT-01', text: `The amount entered does not match the amount Omni read from the invoice (${d.currency || 'BWP'} ${d.read_amount}). Correct it, or tick to confirm the change.` } }))
          return
        }
        if (!payeeType[d.id]) { setBlocked(b => ({ ...b, [d.id]: { control: 'PAY-SUP-01', text: 'Say who is being paid on this claim: the client direct, or a supplier / repairer / service provider.' } })); return }
        const body: JsonBody = {
          subject: d.subject, category: 'claim', claim_payee_type: payeeType[d.id], payee: d.payee,
          entity: d.entity, currency: d.currency || 'BWP',
          account_name: d.account_name, account_number: d.account_number, bank_name: d.bank_name, branch_code: d.branch_code,
          line_items: (d.line_items || []).map(ln => ({ ...ln, ...(a.discount_checked ? { discount_checked: true } : {}) })),
          ...(a.bank_change_reason ? { bank_change_reason: a.bank_change_reason } : {}),
          ...(a.new_payee_confirmed ? { new_payee_confirmed: true } : {}),
          ...(a.payment_date ? { payment_date: a.payment_date } : {}),
          ...(a.early_payment_reason ? { early_payment_reason: a.early_payment_reason } : {}),
        }
        r = await rawStaffFetch('/payment-requests/', { method: 'POST', body: JSON.stringify(body) })
        if (r.ok) await rawStaffFetch(`/payment-requests/drafts/${d.id}/`, { method: 'DELETE' }).catch(() => null)
      } else {
        r = await rawStaffFetch(`/payment-requests/drafts/${d.id}/submit/`, { method: 'POST', body: JSON.stringify(a) })
      }
      if (r.ok) {
        const b = r.body as { ref?: string; exception?: { control?: string; message?: string } }
        setDrafts(ds => (ds || []).filter(x => x.id !== d.id))
        setDone({ ref: b.ref || d.ref, exception: b.exception })
        return
      }
      // A control fired. Keep the draft; show the server's words and open the
      // matching tick-box. A closed raising window (PAY-WIN-*) is just told plainly.
      const control = String(r.body.control || '')
      setBlocked(b => ({ ...b, [d.id]: { control, text: formatServerErrors(r.body, r.status) } }))
    } catch (e) {
      if (!reauthOn401(e)) setBlocked(b => ({ ...b, [d.id]: { control: '', text: errText(e, 'Could not submit.') } }))
    } finally { setBusy(false) }
  }

  if (done) return (
    <ScreenFrame title="Raise a payment" base={base}>
      <div style={{ textAlign: 'center', padding: '24px 8px 8px' }}>
        <p style={{ fontSize: 52, margin: '0 0 8px' }}>✅</p>
        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>{done.ref}</h2>
        <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 0' }}>Submitted for finance sign-off, then the CFO. Omni moves no money — the payment is still authorised in the bank.</p>
      </div>
      {done.exception?.message && <ServerMessage control={done.exception.control} text={`This went to the committee. ${done.exception.message}`} />}
      <Link href={`${base}/approvals`} style={{ ...primaryBtn(false), textDecoration: 'none' }}>Go to Approvals</Link>
      <button onClick={() => setDone(null)} style={ghostBtn}>Back to my drafts</button>
    </ScreenFrame>
  )

  return (
    <ScreenFrame title="Raise a payment" base={base}>
      <input ref={camRef} type="file" accept="image/*,.pdf" capture="environment" style={{ display: 'none' }} onChange={onSnap} />
      <button onClick={() => camRef.current?.click()} disabled={reading}
        style={{ ...primaryBtn(reading), background: '#FFF7ED', color: '#B45309', border: `2px dashed ${C.orange}`, minHeight: 72 }}>
        <Camera size={22} /> {reading ? 'Reading the invoice…' : 'Snap the invoice — Omni fills a draft'}
      </button>
      {note && <ServerMessage text={note} tone="ok" />}
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}

      <b style={{ color: C.ink, fontSize: 15 }}>Your drafts {drafts ? `(${drafts.length})` : ''}</b>
      {drafts === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}
      {drafts && drafts.length === 0 && !loadErr && (
        <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', margin: '12px 0' }}>No drafts yet. Snap an invoice above — you check what Omni read, then submit.</p>
      )}

      {drafts?.map(d => {
        const isOpen = open === d.id
        const ln = line0(d)
        const need = (f: string) => (d.needs_check || []).includes(f)
        const ring = (f: string): React.CSSProperties => need(f) ? { boxShadow: `inset 0 0 0 2px ${C.orange}`, background: '#FFFBEB' } : {}
        const nature: '' | 'claims' | 'operations' | 'refund' =
          d.category === 'claim' ? 'claims'
            : REFUND_KEYS.includes(d.category) ? 'refund'
              : d.category ? 'operations' : ''
        const termsGated = d.category === 'supplier' || d.category === 'vendor' || (d.category === 'claim' && payeeType[d.id] === 'provider')
        const a = answers[d.id] || {}
        const blk = blocked[d.id]
        return (
          <Card key={d.id} style={{ padding: 14 }}>
            <button onClick={() => setOpen(isOpen ? null : d.id)} style={{ width: '100%', minHeight: 44, textAlign: 'left', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                <b style={{ color: C.ink, fontSize: 15 }}>{d.payee || 'Payee not read'}</b>
                <span style={{ fontSize: 14, fontWeight: 800, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{ln.amount ? `${d.currency || 'BWP'} ${ln.amount}` : '—'}</span>
              </div>
              <p style={{ margin: '3px 0 0', fontSize: 12.5, color: C.inkSoft }}>
                {d.ref}{ln.invoice_number ? ` · invoice ${ln.invoice_number}` : ''}{d.source_file ? ` · from ${d.source_file}` : ''}
                {d.needs_check?.length ? ` · check: ${d.needs_check.join(', ')}` : ''}
              </p>
            </button>

            {isOpen && (
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: 0, fontSize: 12.5, color: C.inkSoft, lineHeight: 1.5 }}>
                  What Omni read — correct anything wrong. {d.read_amount ? <>Amount on the invoice: <b style={{ color: C.ink }}>{d.currency || 'BWP'} {d.read_amount}</b>.</> : 'No amount was read.'}
                </p>

                <label style={labelStyle}>Payee</label>
                <input aria-label="Payee" key={`${d.id}-payee-${d.payee}`} defaultValue={d.payee} onBlur={e => e.target.value !== d.payee && patch(d, { payee: e.target.value })} style={{ ...inputStyle, ...ring('payee') }} />
                <label style={labelStyle}>Subject</label>
                <input aria-label="Subject" key={`${d.id}-subj-${d.subject}`} defaultValue={d.subject} onBlur={e => e.target.value !== d.subject && patch(d, { subject: e.target.value })} style={inputStyle} />

                <div style={{ display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle}>Amount</label>
                    <input aria-label="Amount" key={`${d.id}-amt-${ln.amount}`} defaultValue={ln.amount || ''} inputMode="decimal" onBlur={e => e.target.value !== (ln.amount || '') && patchLine(d, { amount: e.target.value })} style={{ ...inputStyle, ...ring('amount'), textAlign: 'right' }} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle}>Currency</label>
                    <select aria-label="Currency" value={d.currency || 'BWP'} onChange={e => patch(d, { currency: e.target.value })} style={inputStyle}>
                      {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                      {d.currency && !CURRENCIES.includes(d.currency) && <option value={d.currency}>{d.currency}</option>}
                    </select>
                  </div>
                </div>
                <label style={labelStyle}>Invoice number</label>
                <input aria-label="Invoice number" key={`${d.id}-inv-${ln.invoice_number}`} defaultValue={ln.invoice_number || ''} onBlur={e => e.target.value !== (ln.invoice_number || '') && patchLine(d, { invoice_number: e.target.value })} style={inputStyle} />

                <label style={labelStyle}>Entity</label>
                {companies.length ? (
                  <select aria-label="Entity" value={d.entity} onChange={e => patch(d, { entity: e.target.value })} style={inputStyle}>
                    {!companies.some(c => c.name === d.entity) && <option value={d.entity}>{d.entity}</option>}
                    {companies.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}
                  </select>
                ) : <p style={{ margin: 0, fontSize: 14, color: C.ink }}>{d.entity}</p>}

                {/* The question the server requires — never guessed from the read (CFO 2026-07-29). */}
                <label style={labelStyle}>What kind of payment is this? *</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => patch(d, { category: 'claim' })} style={{ ...ghostBtn, flex: 1, ...(nature === 'claims' ? { background: '#FFF7ED', borderColor: C.orange, color: '#92400E' } : {}), ...ring('category') }}>Claims payment<br /><span style={{ fontWeight: 500, fontSize: 11, color: C.inkSoft }}>Settling a claim — ADIC only</span></button>
                  <button onClick={() => patch(d, { category: 'supplier' })} style={{ ...ghostBtn, flex: 1, ...(nature === 'operations' ? { background: '#FFF7ED', borderColor: C.orange, color: '#92400E' } : {}), ...ring('category') }}>Operations<br /><span style={{ fontWeight: 500, fontSize: 11, color: C.inkSoft }}>Suppliers, vendors, petty cash</span></button>
                  <button onClick={() => patch(d, { category: 'erroneous_refund' })} style={{ ...ghostBtn, flex: 1, ...(nature === 'refund' ? { background: '#FFF7ED', borderColor: C.orange, color: '#92400E' } : {}), ...ring('category') }}>Refund<br /><span style={{ fontWeight: 500, fontSize: 11, color: C.inkSoft }}>Premium, erroneous and excess refunds</span></button>
                </div>
                {nature === 'claims' && (
                  <>
                    <label style={labelStyle}>Who is being paid? *</label>
                    <select aria-label="Who is being paid" value={payeeType[d.id] || ''} onChange={e => setPayeeType(p => ({ ...p, [d.id]: e.target.value as '' | 'client' | 'provider' }))} style={inputStyle}>
                      <option value="">Choose…</option>
                      <option value="client">The client / policyholder direct</option>
                      <option value="provider">A supplier, repairer or service provider</option>
                    </select>
                    <label style={labelStyle}>Claim number (required on every claim line)</label>
                    <input aria-label="Claim number" key={`${d.id}-clm-${ln.claim_number}`} defaultValue={ln.claim_number || ''} placeholder="e.g. G2026004287" onBlur={e => e.target.value !== (ln.claim_number || '') && patchLine(d, { claim_number: e.target.value })} style={inputStyle} />
                  </>
                )}
                {nature === 'refund' && (
                  <>
                    <label style={labelStyle}>Which kind? *</label>
                    <select aria-label="Which kind of refund" value={d.category} onChange={e => patch(d, { category: e.target.value })} style={inputStyle}>
                      {REFUND_CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                    <label style={labelStyle}>Original payment reference *</label>
                    <input aria-label="Original payment reference" key={`${d.id}-opr-${d.original_payment_ref}`} defaultValue={d.original_payment_ref || ''} placeholder="The payment being refunded" onBlur={e => e.target.value !== (d.original_payment_ref || '') && patch(d, { original_payment_ref: e.target.value })} style={inputStyle} />
                    <p style={{ margin: '4px 0 0', fontSize: 12, color: C.inkSoft }}>
                      A refund must say which payment it reverses. Premium refunds
                      are not keyed here — they arrive overnight from Graphite.
                    </p>
                  </>
                )}
                {nature === 'operations' && (
                  <>
                    <label style={labelStyle}>Which kind? *</label>
                    <select aria-label="Which kind of operations payment" value={d.category} onChange={e => patch(d, { category: e.target.value })} style={inputStyle}>
                      {OPS_CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </>
                )}

                {/* Supplier terms (PAY-SUP-01) — same line fields as the desktop form. */}
                {termsGated && (
                  <div style={{ marginTop: 10, padding: 12, borderRadius: 12, background: '#FFFBEB', border: `1px solid #FCD34D` }}>
                    <b style={{ fontSize: 12, color: '#92400E' }}>Supplier terms — required</b>
                    <label style={labelStyle}>Invoice date</label>
                    <input aria-label="Invoice date" type="date" value={ln.invoice_date || ''} onChange={e => patchLine(d, { invoice_date: e.target.value, due_date: dueFromTerms(e.target.value, ln.terms_days || '30', ln.terms_basis || 'statement') })} style={inputStyle} />
                    <div style={{ display: 'flex', gap: 8 }}>
                      <div style={{ flex: 1 }}>
                        <label style={labelStyle}>Terms (days)</label>
                        <input aria-label="Terms (days)" key={`${d.id}-td-${ln.terms_days}`} defaultValue={ln.terms_days ?? '30'} inputMode="numeric" onBlur={e => patchLine(d, { terms_days: e.target.value, due_date: dueFromTerms(ln.invoice_date || '', e.target.value, ln.terms_basis || 'statement') })} style={inputStyle} />
                      </div>
                      <div style={{ flex: 1.3 }}>
                        <label style={labelStyle}>Terms run from</label>
                        <select aria-label="Terms run from" value={ln.terms_basis || 'statement'} onChange={e => patchLine(d, { terms_basis: e.target.value, due_date: dueFromTerms(ln.invoice_date || '', ln.terms_days || '30', e.target.value) })} style={inputStyle}>
                          <option value="statement">Statement</option><option value="invoice">Invoice</option>
                        </select>
                      </div>
                    </div>
                    <label style={labelStyle}>Due date (derived)</label>
                    <input aria-label="Due date (derived)" type="date" value={ln.due_date || ''} readOnly style={{ ...inputStyle, color: C.inkSoft }} />
                    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 10, fontSize: 13, color: '#92400E', minHeight: 44 }}>
                      <input type="checkbox" checked={!!ln.discount_checked} onChange={e => patchLine(d, { discount_checked: e.target.checked })} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
                      I checked whether an early-settlement or offshore discount applies to this invoice.
                    </label>
                  </div>
                )}

                {/* Where the money goes (PAY-BANK-02 — mandatory, except small petty cash). */}
                <label style={labelStyle}>Account holder name</label>
                <input aria-label="Account holder name" key={`${d.id}-an-${d.account_name}`} defaultValue={d.account_name} onBlur={e => e.target.value !== d.account_name && patch(d, { account_name: e.target.value })} style={inputStyle} />
                <div style={{ display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1.3 }}>
                    <label style={labelStyle}>Bank</label>
                    <input aria-label="Bank" key={`${d.id}-bn-${d.bank_name}`} defaultValue={d.bank_name} placeholder="e.g. FNB Botswana" onBlur={e => e.target.value !== d.bank_name && patch(d, { bank_name: e.target.value })} style={inputStyle} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <label style={labelStyle}>Branch code</label>
                    <input aria-label="Branch code" key={`${d.id}-bc-${d.branch_code}`} defaultValue={d.branch_code} placeholder="FNB: blank" onBlur={e => e.target.value !== d.branch_code && patch(d, { branch_code: e.target.value })} style={inputStyle} />
                  </div>
                </div>
                <label style={labelStyle}>Account number</label>
                <input aria-label="Account number" key={`${d.id}-ac-${d.account_number}`} defaultValue={d.account_number} inputMode="numeric" onBlur={e => e.target.value !== d.account_number && patch(d, { account_number: e.target.value })} style={{ ...inputStyle, ...ring('account_number') }} />

                {/* The server's answer to the last submit, verbatim, plus the tick that clears it. */}
                {blk?.text && <div style={{ marginTop: 12 }}><ServerMessage control={blk.control || undefined} text={blk.text} tone={blk.control.startsWith('PAY-WIN') ? 'warn' : 'error'} /></div>}
                {blk?.control.startsWith('PAY-WIN') && <p style={{ margin: '6px 0 0', fontSize: 12.5, color: C.inkSoft }}>Your draft is kept — come back in the raising window and submit it then.</p>}
                {(d.amount_mismatch || blk?.control === 'PAY-AMT-01') && (
                  <Tick checked={!!a.confirm_amount} onChange={v => setAnswer(d.id, { confirm_amount: v })}
                    text={`The invoice Omni read said ${d.read_amount ?? '—'}, but the amount here is ${ln.amount || '—'}. Tick to confirm the amount really changed, then submit again.`} />
                )}
                {blk?.control === 'PAY-BANK-03' && (
                  <Tick checked={!!a.new_payee_confirmed} onChange={v => setAnswer(d.id, { new_payee_confirmed: v })}
                    text="First time paying this payee. I checked the account digits against the invoice and confirmed the supplier through a contact I already had." />
                )}
                {blk?.control === 'PAY-SUP-01' && d.category !== 'claim' && (
                  <div style={{ marginTop: 8 }}>
                    <label style={labelStyle}>Payment date (on/after the due date)</label>
                    <input aria-label="Payment date (on/after the due date)" type="date" value={a.payment_date || ''} onChange={e => setAnswer(d.id, { payment_date: e.target.value })} style={inputStyle} />
                    <label style={labelStyle}>CFO-approved reason (only if paying early)</label>
                    <input aria-label="CFO-approved reason (only if paying early)" value={a.early_payment_reason || ''} onChange={e => setAnswer(d.id, { early_payment_reason: e.target.value })} style={inputStyle} />
                    <Tick checked={!!a.discount_checked} onChange={v => setAnswer(d.id, { discount_checked: v })} text="I checked whether an early-settlement or offshore discount applies to this invoice." />
                  </div>
                )}
                {(blk?.control === 'PAY-BANK-01' || d.bank_change_reason) && (
                  <>
                    <label style={labelStyle}>This is a different bank account — what happened, and who confirmed it?</label>
                    <textarea aria-label="Different bank account — what happened and who confirmed it" value={a.bank_change_reason ?? d.bank_change_reason ?? ''} onChange={e => setAnswer(d.id, { bank_change_reason: e.target.value })} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
                  </>
                )}

                <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
                  <button onClick={() => remove(d)} disabled={busy} style={{ ...ghostBtn, color: '#B91C1C', display: 'flex', alignItems: 'center', gap: 6 }}><Trash2 size={14} /> Discard</button>
                  <button onClick={() => submit(d)} disabled={busy} style={{ ...primaryBtn(busy), flex: 1 }}><Send size={16} /> {busy ? 'Submitting…' : 'Check & submit'}</button>
                </div>
              </div>
            )}
          </Card>
        )
      })}
      <p style={{ margin: 0, fontSize: 11.5, color: C.inkSoft, textAlign: 'center' }}>Every draft is checked by you before it is submitted. Omni moves no money.</p>
      <Toast text={toast} />
    </ScreenFrame>
  )
}

function Tick({ checked, onChange, text }: { checked: boolean; onChange: (v: boolean) => void; text: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginTop: 10, padding: 10, borderRadius: 12, background: '#FFFBEB', border: '1px solid #FCD34D', fontSize: 12.5, color: '#92400E', lineHeight: 1.5, minHeight: 44 }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
      <span>{text}</span>
    </label>
  )
}
