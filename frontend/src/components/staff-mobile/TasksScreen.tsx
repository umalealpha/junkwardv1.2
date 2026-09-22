'use client'

/** /m/staff/tasks — your task inbox on the phone. Marking Done is one tap; the
 * completion note is OPTIONAL (CFO 2026-07-24). Report Blocked / Partly done
 * with a reason + a photo as evidence (camera) — the server requires those.
 *
 * Tapping a card opens the detail (CFO 2026-08-03). A payment authorisation
 * shows the whole pack — every supplier line, invoice number, due date and
 * amount, plus the attachments — because the card alone only ever showed a
 * grand total and the approver was signing blind.
 *
 * A payment-request task is a DECISION, not a to-do (bug 2026-09-03): the
 * finance first-approver's leg goes through /payment-requests/<id>/decide/
 * (SoD, duplicate re-check, PAY-BANK-ACK, hands to the CFO). Marking it "Done"
 * via /tasks/<id>/complete/ only ever advanced a PENDING_CFO request, so a
 * finance sign-off closed the task and left the payment stuck with no CFO task.
 * Those cards now show "Review payment" and never the Done / Partly / Blocked
 * controls. The CFO's own leg stays on the Payments screen. */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Camera, CheckCircle2, ChevronRight, Paperclip, X, XCircle } from 'lucide-react'
import {
  sfetch, getPaymentPack, paymentAttachmentBlob, getApprovalBrief,
  type PaymentPack,
} from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { rawStaffFetch, formatServerErrors, ServerMessage, errText } from './StaffFormKit'
import { isPaymentTask, decidePath, decidePayload, classifyDecide, type BankChange, type Decision } from './paymentDecide'

interface Task {
  id: string; title: string; body: string; status: string
  due_at: string | null; due_time: string | null; assigner_name: string; is_overdue: boolean
  payment_request_id: string | null; payment_ref: string
}

const STATUS_LABEL: Record<string, string> = { pending: 'Not started', in_progress: 'In progress', partial: 'Partly done', blocked: 'Blocked' }
const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans }

// Render a task body so a phone user is never shown a raw link (CFO 2026-09-07:
// a manager saw a wall of raw feedback URLs and got confused). A "- Name: url"
// line becomes a big tappable name; any other bare URL becomes a tap target and
// a long one (a token link) hides its characters behind "open ↗".
const URL_RE = /(https?:\/\/[^\s]+)/g
const PERSON_LINE = /^\s*[-•]\s+(.+?):\s+(https?:\/\/\S+)\s*$/
function MobileTaskBody({ body }: { body: string }) {
  const lines = body.replace(/\r\n/g, '\n').split('\n')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontFamily: sans, fontSize: 13, color: C.ink, lineHeight: 1.55 }}>
      {lines.map((line, i) => {
        const p = line.match(PERSON_LINE)
        if (p) return (
          <a key={i} href={p[2]} target="_blank" rel="noopener noreferrer"
             style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minHeight: 44, padding: '10px 14px', borderRadius: 12, background: '#FFF7EC', border: `1px solid ${C.orange}`, color: C.navy, fontWeight: 700, textDecoration: 'none' }}>
            <span aria-hidden style={{ color: C.orange }}>✎</span>{p[1].trim()}
          </a>
        )
        if (!line.trim()) return <div key={i} style={{ height: 4 }} />
        const parts = line.split(URL_RE)
        return (
          <div key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {parts.map((part, j) => j % 2 === 1
              ? <a key={j} href={part} target="_blank" rel="noopener noreferrer" style={{ color: C.navy, fontWeight: 600 }}>{part.length > 40 ? 'open ↗' : part}</a>
              : <span key={j}>{part}</span>)}
          </div>
        )
      })}
    </div>
  )
}

/** Alpha Direct's own paying account, shown on a phone that gets read over
 * shoulders. The approver needs to recognise WHICH account, not to be able to
 * transcribe it, so only the last 4 digits are rendered. */
const maskAccount = (n: string) => {
  const s = (n || '').trim()
  return s.length > 4 ? `••••${s.slice(-4)}` : s
}

const money = (ccy: string, v: string | number | undefined) => {
  const n = Number(v ?? 0)
  return `${ccy} ${(Number.isFinite(n) ? n : 0).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function StaffTasks() {
  const base = useStaffBase()
  const [tasks, setTasks] = useState<Task[]>([])
  const [openFor, setOpenFor] = useState<Task | null>(null)
  const [mode, setMode] = useState<'done' | 'blocked' | 'partial'>('done')
  const [note, setNote] = useState('')
  const [evidence, setEvidence] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  // Detail sheet (CFO 2026-08-03): the task being read, plus the payment pack
  // once it loads. `packBusy` covers the fetch; `pack === null` after it means
  // there is nothing extra beyond the task body.
  const [detailFor, setDetailFor] = useState<Task | null>(null)
  const [pack, setPack] = useState<PaymentPack | null>(null)
  const [packBusy, setPackBusy] = useState(false)
  const [packErr, setPackErr] = useState('')
  // Finance sign-off leg (bug 2026-09-03): the one-liner brief, the reason
  // (mandatory to reject), the PAY-BANK-ACK change the server sent back, the
  // approver's tick on it, and the server's refusal text shown verbatim.
  const [brief, setBrief] = useState('')
  const [reason, setReason] = useState('')
  const [bankChange, setBankChange] = useState<BankChange | null>(null)
  const [bankAcked, setBankAcked] = useState(false)
  const [decideMsg, setDecideMsg] = useState<{ text: string; control?: string } | null>(null)
  const openedAt = useRef<number>(0)
  const camRef = useRef<HTMLInputElement | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    // Payment authorisations are hidden for the CFO server-side (they live on
    // the dedicated Payments screen now, CFO 2026-08-29); finance first-approvers
    // still get their payment sign-off tasks here.
    sfetch<Task[]>('/taskboard/my-tasks/').then(setTasks).catch(() => setTasks([]))
  }, [])
  useEffect(() => {
    load()
    // Keep the list fresh across devices (CFO 2026-07-24): refetch when the app
    // regains focus and on a light background poll, so a task completed on the
    // website (or another phone) drops off here without a manual reload.
    const onVis = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', load)
    const id = window.setInterval(load, 120000)   // 2-min poll (well within "~15 min")
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', load)
      window.clearInterval(id)
    }
  }, [load])

  const open = (t: Task, m: 'done' | 'blocked' | 'partial') => {
    setOpenFor(t); setMode(m); setNote(''); setEvidence(null)
    openedAt.current = Date.now()
  }

  const openDetail = (t: Task) => {
    setDetailFor(t); setPack(null); setPackErr('')
    setBrief(''); setReason(''); setBankChange(null); setBankAcked(false); setDecideMsg(null)
    if (!t.payment_request_id) return
    setPackBusy(true)
    getPaymentPack(t.payment_request_id)
      .then(setPack)
      .catch(e => setPackErr(e instanceof Error ? e.message : 'Could not load the payment details.'))
      .finally(() => setPackBusy(false))
    // The same one-liner the Approvals decision sheet shows — best effort.
    getApprovalBrief('payment_requests', t.payment_request_id).then(d => setBrief(d.brief || '')).catch(() => {})
  }

  /** Finance first-approver decision on a payment request (never the generic
   * task-complete). Server text is shown as sent; a 409 PAY-BANK-ACK turns into
   * the bank-change block + tick, and the approve is re-posted acknowledged. */
  async function decide(decision: Decision) {
    const id = detailFor?.payment_request_id
    if (!id) return
    if (decision === 'reject' && !reason.trim()) { show('Say why you are rejecting it — the raiser gets your reason.'); return }
    if (decision === 'approve' && bankChange && !bankAcked) { show('Tick that you have checked the new bank details first.'); return }
    setBusy(true); setDecideMsg(null)
    try {
      const r = await rawStaffFetch(decidePath(id), { method: 'POST', body: JSON.stringify(decidePayload(decision, reason, bankAcked)) })
      const out = classifyDecide(r)
      if (out.kind === 'ok') {
        show(decision === 'approve' ? 'Signed off — it is with the CFO now. ✅' : 'Rejected — the raiser has your reason.')
        setDetailFor(null); load(); return
      }
      if (out.kind === 'bank_ack') { setBankChange(out.change); setBankAcked(false); return }
      const control = typeof out.body.control === 'string' ? out.body.control : undefined
      setDecideMsg({ text: formatServerErrors(out.body, out.status), control })
      if (out.refresh) load()
    } catch (e) { show(errText(e, 'Could not send your decision.')) }
    finally { setBusy(false) }
  }

  async function openAttachment(attId: string, name: string) {
    try {
      const blob = await paymentAttachmentBlob(attId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = name; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not open that file.') }
  }

  async function submit() {
    if (!openFor) return
    const secs = Math.floor((Date.now() - openedAt.current) / 1000)
    // CFO 2026-07-24: Done is one tap — the note is optional. Partly/Blocked
    // still need a reason + photo (that IS the "explain why not done" path).
    if (mode !== 'done') {
      if (!note.trim()) { show('Say why.'); return }
      if (!evidence) { show('A photo is required as evidence — tap the camera.'); return }
    }
    setBusy(true)
    try {
      if (mode === 'done') {
        await sfetch(`/taskboard/tasks/${openFor.id}/complete/`, {
          method: 'POST', body: JSON.stringify({ body: note.trim(), interaction_seconds: secs }) })
        show('Done — nice work. ✅')
      } else {
        const fd = new FormData()
        fd.append('status', mode); fd.append('body', note.trim())
        if (evidence) fd.append('evidence', evidence)
        await sfetch(`/taskboard/tasks/${openFor.id}/status/`, { method: 'POST', body: fd }, false)
        show(mode === 'blocked' ? 'Marked blocked — your manager can see the evidence.' : 'Marked partly done.')
      }
      setOpenFor(null); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not save.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My tasks</h1>
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {tasks.length === 0 && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>No open tasks. 🎉</p>}
        {tasks.map(t => (
          <div key={t.id} style={{ ...card, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <b style={{ color: C.ink, fontSize: 15, lineHeight: 1.35 }}>{t.title}</b>
              {t.is_overdue && <span style={{ color: '#B91C1C', fontSize: 11, fontWeight: 800, whiteSpace: 'nowrap' }}>OVERDUE</span>}
            </div>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>
              from {t.assigner_name} · {STATUS_LABEL[t.status] || t.status}{t.due_at ? ` · due ${t.due_time ? t.due_time.slice(0, 5) : t.due_at}` : ''}{t.payment_ref ? ` · ${t.payment_ref}` : ''}
            </p>
            {isPaymentTask(t) ? (
              // A payment leg is decided (approve / reject) inside the pack sheet —
              // never marked Done / Partly / Blocked like a to-do (bug 2026-09-03).
              <button onClick={() => openDetail(t)} data-testid="review-payment"
                style={{ marginTop: 12, width: '100%', padding: 12, borderRadius: 999, border: 'none', background: C.navy, color: C.orange, fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                Review payment <ChevronRight size={16} />
              </button>
            ) : (
              <>
                {/* Read what is actually inside before you sign it (CFO 2026-08-03). */}
                <button onClick={() => openDetail(t)}
                  style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8, padding: 0, background: 'none', border: 'none', color: C.head, fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans, textAlign: 'left' }}>
                  <span>See details</span>
                  <ChevronRight size={15} style={{ flexShrink: 0 }} />
                </button>
                <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                  <button onClick={() => open(t, 'done')} style={{ flex: 1.2, padding: 11, borderRadius: 999, border: 'none', background: '#047857', color: '#fff', fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5 }}><CheckCircle2 size={15} /> Done</button>
                  <button onClick={() => open(t, 'partial')} style={{ flex: 1, padding: 11, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B45309', fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>Partly</button>
                  <button onClick={() => open(t, 'blocked')} style={{ flex: 1, padding: 11, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B91C1C', fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>Blocked</button>
                </div>
              </>
            )}
          </div>))}
      </main>

      {detailFor && (
        <div role="dialog" aria-modal="true" aria-label="Task details" onClick={() => setDetailFor(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: C.card, width: '100%', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
              <b style={{ fontFamily: serif, fontSize: 18, color: C.ink, lineHeight: 1.3 }}>{detailFor.title}</b>
              <button onClick={() => setDetailFor(null)} aria-label="Close" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer', flexShrink: 0 }}><X size={18} /></button>
            </div>

            {packBusy && <p style={{ color: C.inkSoft, fontSize: 13 }}>Loading the payment details…</p>}
            {packErr && <p style={{ color: '#B91C1C', fontSize: 13 }}>{packErr}</p>}

            {pack && (
              <>
                <div style={{ background: '#F0F2F5', borderRadius: 12, padding: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 12.5, color: C.inkSoft }}>{pack.ref} · {pack.entity}</div>
                  <div style={{ fontFamily: serif, fontSize: 24, fontWeight: 800, color: C.ink, margin: '2px 0 6px' }}>
                    {money(pack.currency, pack.total)}
                  </div>
                  <div style={{ fontSize: 13, color: C.ink }}><b>Paid to:</b> {pack.payee || '—'}</div>
                  {pack.subject && <div style={{ fontSize: 13, color: C.ink, marginTop: 2 }}><b>For:</b> {pack.subject}</div>}
                  <div style={{ fontSize: 12.5, color: C.inkSoft, marginTop: 4 }}>
                    {pack.category_label}{pack.claim_payee_label ? ` · ${pack.claim_payee_label}` : ''}
                    {pack.due_date ? ` · due ${pack.due_date}` : ''}
                  </div>
                </div>

                {pack.summary && (
                  <p style={{ fontSize: 13, color: C.ink, lineHeight: 1.5, margin: '0 0 12px' }}>{pack.summary}</p>)}

                <b style={{ fontSize: 13, color: C.ink }}>What makes up this amount ({pack.line_items.length})</b>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0 12px' }}>
                  {pack.line_items.map((ln, i) => {
                    // Raisers routinely paste the same string into description
                    // AND ref (and sometimes the claim number is already in the
                    // description). Repeating it under every line is noise —
                    // only show a detail that adds something.
                    const desc = (ln.description || '').trim()
                    const adds = (v?: string) => {
                      const s = (v || '').trim()
                      return s && !desc.toLowerCase().includes(s.toLowerCase()) ? s : ''
                    }
                    const inv = adds(ln.invoice_number), clm = adds(ln.claim_number), rf = adds(ln.ref)
                    const meta = [
                      inv && `Invoice ${inv}`,
                      ln.invoice_date && `dated ${ln.invoice_date}`,
                      ln.due_date && `due ${ln.due_date}`,
                      clm && `claim ${clm}`,
                      rf && `ref ${rf}`,
                    ].filter(Boolean).join(' · ')
                    return (
                      <div key={i} style={{ border: `1px solid ${C.line}`, borderRadius: 12, padding: 11 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                          <span style={{ fontSize: 13.5, color: C.ink, fontWeight: 600 }}>{desc || '—'}</span>
                          <b style={{ fontSize: 13.5, color: C.ink, whiteSpace: 'nowrap' }}>{money(pack.currency, ln.amount)}</b>
                        </div>
                        {meta && <div style={{ fontSize: 12, color: C.inkSoft, marginTop: 4, lineHeight: 1.5 }}>{meta}</div>}
                      </div>)
                  })}
                </div>

                {(pack.account_name || pack.bank_name) && (
                  <div style={{ fontSize: 12.5, color: C.inkSoft, marginBottom: 10, lineHeight: 1.6 }}>
                    {/* The SOURCE account — it carries the opening balance the
                        pack draws down, so it is what the money leaves, not
                        where it lands. Labelling it "Into" was wrong. */}
                    <b style={{ color: C.ink }}>Paid from:</b> {pack.account_name}{pack.bank_name ? ` · ${pack.bank_name}` : ''}
                    {pack.account_number ? ` · ${maskAccount(pack.account_number)}` : ''}
                  </div>)}

                {pack.attachments.length > 0 && (
                  <div style={{ marginBottom: 10 }}>
                    <b style={{ fontSize: 13, color: C.ink }}>Attached ({pack.attachments.length})</b>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
                      {pack.attachments.map(a => (
                        <button key={a.id} onClick={() => openAttachment(a.id, a.name)}
                          style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '9px 12px', borderRadius: 10, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 12.5, cursor: 'pointer', fontFamily: sans, textAlign: 'left' }}>
                          <Paperclip size={14} /> {a.name}
                        </button>))}
                    </div>
                  </div>)}

                <div style={{ fontSize: 12, color: C.inkSoft, lineHeight: 1.6 }}>
                  Raised by {pack.created_by || '—'}
                  {pack.first_approver ? ` · finance sign-off by ${pack.first_approver}` : ''}
                  {pack.verifier ? ` · verified by ${pack.verifier}` : ''}
                  {pack.decision_notes ? ` · note: ${pack.decision_notes}` : ''}
                </div>

                {/* The decision (bug 2026-09-03). Only offered once the full pack has
                    loaded — the approver never signs blind — and only at the finance
                    stage. The CFO's leg is authorised on the Payments screen. */}
                <div style={{ marginTop: 18, borderTop: `1px solid ${C.line}`, paddingTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="payment-decision">
                  {brief && (
                    <p style={{ fontSize: 12.5, color: C.ink, margin: 0, background: '#F6F7F9', borderRadius: 10, padding: '9px 11px' }}>🤖 {brief}</p>)}
                  {pack.status === 'pending_finance' ? (
                    <>
                      {decideMsg && <ServerMessage text={decideMsg.text} control={decideMsg.control} tone="error" />}
                      {bankChange && (
                        <div data-testid="bank-change" style={{ background: '#FFFBEB', border: '1px solid #FCD34D', borderRadius: 12, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                          <b style={{ fontSize: 11, letterSpacing: '0.04em', color: '#92400E' }}>PAY-BANK-ACK · bank account changed</b>
                          {bankChange.detail && <p style={{ margin: 0, fontSize: 13, color: '#92400E', lineHeight: 1.5, whiteSpace: 'pre-line' }}>{bankChange.detail}</p>}
                          <div style={{ fontSize: 12.5, color: '#92400E' }}>
                            Known account ends <b>{bankChange.known_account_tail || '—'}</b> · this request ends <b>{bankChange.new_account_tail || '—'}</b>
                            {bankChange.known_source ? ` · from ${bankChange.known_source}` : ''}
                          </div>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 44, fontSize: 13, fontWeight: 700, color: '#92400E', cursor: 'pointer' }}>
                            <input type="checkbox" checked={bankAcked} onChange={e => setBankAcked(e.target.checked)} data-testid="bank-ack"
                              style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
                            I have checked the new bank details
                          </label>
                        </div>)}
                      <label htmlFor="pay-decide-reason" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Reason — required to reject, optional to approve</label>
                      <textarea id="pay-decide-reason" value={reason} onChange={e => setReason(e.target.value)} rows={2}
                        placeholder="Say why (sent to the raiser)" style={{ ...inputStyle, resize: 'vertical' }} />
                      <div style={{ display: 'flex', gap: 10 }}>
                        <button onClick={() => decide('approve')} disabled={busy || (!!bankChange && !bankAcked)} data-testid="approve-payment"
                          style={{ flex: 1.2, padding: 13, borderRadius: 999, border: 'none', background: '#047857', color: '#fff', fontWeight: 800, fontSize: 14.5, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, opacity: busy || (!!bankChange && !bankAcked) ? 0.6 : 1 }}>
                          <CheckCircle2 size={16} /> {busy ? 'Sending…' : bankChange ? 'Confirm and sign off' : 'Approve'}
                        </button>
                        <button onClick={() => decide('reject')} disabled={busy} data-testid="reject-payment"
                          style={{ flex: 1, padding: 13, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B91C1C', fontWeight: 800, fontSize: 14.5, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                          <XCircle size={16} /> Reject
                        </button>
                      </div>
                      <p style={{ fontSize: 11.5, color: C.inkSoft, margin: 0, lineHeight: 1.5 }}>Approving hands the pack to the CFO. No money moves here — it is released at the bank.</p>
                    </>
                  ) : pack.status === 'pending_cfo' ? (
                    <>
                      <p style={{ fontSize: 13, color: C.ink, margin: 0, lineHeight: 1.5 }}>Finance has signed this off — it is at the CFO stage now. Authorise it on the Payments screen.</p>
                      <Link href={`${base}/payments`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, minHeight: 44, padding: '12px 16px', borderRadius: 999, background: C.navy, color: C.orange, fontWeight: 800, fontSize: 14, textDecoration: 'none' }}>
                        Open Payments <ChevronRight size={16} />
                      </Link>
                    </>
                  ) : (
                    <p style={{ fontSize: 13, color: C.inkSoft, margin: 0 }}>This request is {pack.status_label || pack.status} — nothing to decide here.</p>
                  )}
                </div>
              </>)}

            {!pack && !packBusy && (
              detailFor.body
                ? <MobileTaskBody body={detailFor.body} />
                : <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>No extra detail was written on this task.</p>)}
          </div>
        </div>)}

      {openFor && (
        <div role="dialog" aria-modal="true" onClick={() => !busy && setOpenFor(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <b style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>
                {mode === 'done' ? 'Mark done' : mode === 'partial' ? 'Partly done' : 'Blocked'}
              </b>
              <button onClick={() => setOpenFor(null)} aria-label="Close" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer' }}><X size={18} /></button>
            </div>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '0 0 10px' }}>{openFor.title}</p>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={3} autoFocus aria-label={mode === 'done' ? 'What did you do (optional)' : 'Why — what happened'}
              placeholder={mode === 'done' ? 'What did you do? (optional)' : 'Why? What happened?'}
              style={{ ...inputStyle, resize: 'vertical' }} />
            {mode === 'done' && (
              <p style={{ fontSize: 12, color: C.inkSoft, margin: '6px 0 0' }}>Optional — a quick note helps, but you can just complete it.</p>)}
            {mode !== 'done' && (
              <div style={{ marginTop: 10 }}>
                <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                  onChange={e => { setEvidence(e.target.files?.[0] || null); e.target.value = '' }} />
                <button onClick={() => camRef.current?.click()}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 44, padding: '11px 16px', borderRadius: 999, border: `2px dashed ${C.orange}`, background: '#FFF7ED', color: '#92400E', fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>
                  <Camera size={16} /> {evidence ? `Photo attached ✓ (${Math.round(evidence.size / 1024)} KB)` : 'Photo evidence (required)'}
                </button>
              </div>)}
            <button onClick={submit} disabled={busy}
              style={{ marginTop: 14, width: '100%', padding: 14, borderRadius: 999, border: 'none', background: mode === 'done' ? '#047857' : `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: mode === 'done' ? '#fff' : C.navy, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Saving…' : mode === 'done' ? 'Complete task' : 'Save'}
            </button>
          </div>
        </div>)}

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 70, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
