'use client'

/** /m/staff/payments — the CFO's ONE place to AUTHORISE payments (CFO
 * 2026-08-29: "I need two places: approve payments, and approve other things").
 * Payments used to appear on BOTH the Approvals dashboard AND the Tasks inbox —
 * the same money in two lists. This screen is now the single home for payment
 * authorisation; it is an APPROVAL, never a task. Everything else (staff loans,
 * leave, incentives …) lives on /m/staff/approvals.
 *
 * Reuses the proven payment machinery that was on the Tasks screen: the
 * duplicate-checked bulk authorise (one press for every clean pack) and the
 * full pack detail (every supplier line, invoice, due date, attachment) so the
 * approver never signs blind. Nothing new server-side — same endpoints. */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ChevronRight, Paperclip, X, CheckCircle2, UserPlus, Ban } from 'lucide-react'
import {
  getPaymentBulkPreview, approvePaymentBulk, getPaymentPack, paymentAttachmentBlob,
  getStaffApprovalItems, bulkApprove,
  authoriseOnePayment, clearPaymentRequest, getTaskAssignees, reassignPaymentTask,
  type BulkPreview, type BulkPack, type PaymentPack, type MyApprovalItems, type TaskAssignee,
} from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

/** Alpha Direct's own paying account, read over shoulders on a phone — show
 * only the last 4 so the approver recognises WHICH account, not enough to copy. */
const maskAccount = (n: string) => {
  const s = (n || '').trim()
  return s.length > 4 ? `••••${s.slice(-4)}` : s
}
const money = (ccy: string, v: string | number | undefined) => {
  const n = Number(v ?? 0)
  return `${ccy} ${(Number.isFinite(n) ? n : 0).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function StaffPayments() {
  const base = useStaffBase()
  const [bulk, setBulk] = useState<BulkPreview | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [detailFor, setDetailFor] = useState<BulkPack | null>(null)
  const [pack, setPack] = useState<PaymentPack | null>(null)
  const [packBusy, setPackBusy] = useState(false)
  const [packErr, setPackErr] = useState('')
  const [signStream, setSignStream] = useState<MyApprovalItems['streams'][number] | null>(null)  // 'Payments to sign' (Payment model)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [panel, setPanel] = useState<'' | 'reassign' | 'decline'>('')   // which action panel is open in the sheet
  const [assignees, setAssignees] = useState<TaskAssignee[] | null>(null)
  const [reTo, setReTo] = useState('')
  const [reReason, setReReason] = useState('')
  const [declineReason, setDeclineReason] = useState('')
  const seen = useRef(false)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    // 403 for anyone who is not an authoriser — swallow it; the screen simply
    // shows "nothing waiting" for them.
    getPaymentBulkPreview().then(setBulk).catch(() => setBulk(null)).finally(() => setLoaded(true))
    // The 'Payments to sign' stream is a different model (payments.Payment) that
    // also belongs on this screen so a signer has a home for it.
    getStaffApprovalItems()
      .then(d => setSignStream(d.streams.find(x => x.key === 'payments') ?? null))
      .catch(() => setSignStream(null))
  }, [])
  useEffect(() => {
    load()
    const onVis = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', load)
    const id = window.setInterval(load, 120000)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', load)
      window.clearInterval(id)
    }
  }, [load])

  const openDetail = (p: BulkPack) => {
    setDetailFor(p); setPack(null); setPackErr(''); setPackBusy(true)
    setPanel(''); setReTo(''); setReReason(''); setDeclineReason('')
    getPaymentPack(p.id)
      .then(setPack)
      .catch(e => setPackErr(e instanceof Error ? e.message : 'Could not load the payment details.'))
      .finally(() => setPackBusy(false))
  }

  function openReassign() {
    setPanel(p => p === 'reassign' ? '' : 'reassign')
    if (assignees === null) getTaskAssignees().then(setAssignees).catch(() => setAssignees([]))
  }

  // Approve ONE payment = mark its task paid (same PAY-* controls as desktop).
  async function approveOne() {
    if (!detailFor) return
    setBusy(true)
    try {
      await authoriseOnePayment(detailFor.task_id)
      show('Payment authorised — marked paid. ✅'); setDetailFor(null); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not authorise this payment.') }
    finally { setBusy(false) }
  }

  async function declineOne() {
    if (!detailFor || !declineReason.trim()) return
    setBusy(true)
    try {
      await clearPaymentRequest(detailFor.id, declineReason.trim())
      show('Cleared from the queue — not paid.'); setDetailFor(null); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not clear this request.') }
    finally { setBusy(false) }
  }

  async function reassignOne() {
    if (!detailFor || !reTo) return
    setBusy(true)
    try {
      await reassignPaymentTask(detailFor.task_id, reTo, reReason.trim())
      show('Handed over. ✅'); setDetailFor(null); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not reassign this payment.') }
    finally { setBusy(false) }
  }

  async function runBulkApprove() {
    if (!bulk) return
    setBusy(true)
    try {
      const r = await approvePaymentBulk(bulk.ready_total)
      show(r.refused_count
        ? `${r.authorised_count} authorised. ${r.refused_count} refused — open Omni on your computer to action those.`
        : `${r.authorised_count} payment${r.authorised_count === 1 ? '' : 's'} authorised. ✅`)
      setBulkOpen(false); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not authorise.') }
    finally { setBusy(false) }
  }

  const toggleSel = (id: string) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  async function signSelected() {
    if (!signStream) return
    const payload = signStream.items.filter(i => selected.has(i.id)).map(i => ({ stream: 'payments', id: i.id }))
    if (!payload.length) return
    setBusy(true)
    try {
      const res = await bulkApprove(payload)
      show(res.failed.length === 0 ? `Signed ${res.approved}. ✅` : `Signed ${res.approved}. ${res.failed.length} couldn't: ${res.failed[0].error}`)
      setSelected(new Set()); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not sign.') }
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

  const ready = bulk?.ready ?? []
  const blocked = bulk?.blocked ?? []
  const signItems = signStream?.items ?? []
  const total = ready.length + blocked.length + signItems.length
  seen.current = seen.current || loaded

  const Row = ({ p, warn }: { p: BulkPack; warn?: boolean }) => (
    <button onClick={() => openDetail(p)}
      style={{ ...card, padding: 14, width: '100%', textAlign: 'left', cursor: 'pointer', fontFamily: sans, border: warn ? '1px solid #F3D6D6' : (card.border as string) }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
        <b style={{ color: C.ink, fontSize: 14.5 }}>{p.subject || p.ref}</b>
        <b style={{ color: C.ink, fontSize: 14.5, whiteSpace: 'nowrap' }}>{money(p.currency, p.total)}</b>
      </div>
      <div style={{ color: C.inkSoft, fontSize: 12.5, marginTop: 4 }}>
        {p.entity} · {p.lines} line{p.lines === 1 ? '' : 's'}{p.signed_off_by ? ` · signed off by ${p.signed_off_by}` : ''}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: warn || p.edited_after_signoff ? 6 : 0 }}>
        {p.edited_after_signoff && <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 6, background: '#FEF2F2', color: '#B91C1C' }}>⚠ changed after sign-off</span>}
        {warn && (p.clashes ?? 0) > 0 && <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 6, background: '#FEF2F2', color: '#B91C1C' }}>⚠ {p.clashes} possible duplicate</span>}
      </div>
      <div style={{ marginTop: 8, color: C.head, fontWeight: 800, fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 3 }}>See the payment details <ChevronRight size={14} /></div>
    </button>
  )

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans, paddingBottom: 24 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Payments</h1>
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Payments waiting for your authorisation. Tap one to read the full pack before you sign, or authorise every clean pack in one press. Money never leaves here — you release it at the bank.</p>

        {seen.current && total === 0 && (
          <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 28 }}>No payments waiting on you. 🎉</p>)}

        {/* One press for every duplicate-checked pack (CFO 2026-08-04). */}
        {bulk && bulk.ready_count > 0 && (
          <button onClick={() => setBulkOpen(true)}
            style={{ ...card, padding: 16, border: `1.5px solid ${C.navy}`, textAlign: 'left', cursor: 'pointer', fontFamily: sans }}>
            <div style={{ fontSize: 12, color: C.inkSoft, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase' }}>Ready to authorise</div>
            <div style={{ fontFamily: serif, fontSize: 23, fontWeight: 800, color: C.ink, margin: '3px 0 2px' }}>{money('BWP', bulk.ready_total)}</div>
            <div style={{ fontSize: 13, color: C.inkSoft }}>
              {bulk.ready_count} pack{bulk.ready_count === 1 ? '' : 's'} passed the duplicate check
              {bulk.blocked_count > 0 && ` · ${bulk.blocked_count} still blocked`}
            </div>
            <div style={{ marginTop: 10, color: C.head, fontWeight: 800, fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>Review and authorise <ChevronRight size={15} /></div>
          </button>)}

        {ready.length > 0 && (
          <>
            <div style={{ fontSize: 12, color: C.inkSoft, fontWeight: 800, letterSpacing: '.06em', textTransform: 'uppercase', marginTop: 2 }}>Ready ({ready.length})</div>
            {ready.map(p => <Row key={p.id} p={p} />)}
          </>)}

        {blocked.length > 0 && (
          <>
            <div style={{ fontSize: 12, color: '#B91C1C', fontWeight: 800, letterSpacing: '.06em', textTransform: 'uppercase', marginTop: 6 }}>Needs attention ({blocked.length})</div>
            {blocked.map(p => <Row key={p.id} p={p} warn />)}
            <p style={{ fontSize: 12, color: C.inkSoft, margin: '2px 0 0' }}>These can&apos;t be authorised here — open Omni on your computer to fix them.</p>
          </>)}

        {/* 'Payments to sign' — the outbound Payment stream (payments.Payment),
            different model to the payment requests above; sign selected in one tap. */}
        {signItems.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <b style={{ color: C.ink, fontSize: 15 }}>{signStream?.label} <span style={{ color: C.inkSoft, fontWeight: 600 }}>({signItems.length})</span></b>
              <button onClick={() => setSelected(prev => { const keys = signItems.map(i => i.id); const allOn = keys.every(k => prev.has(k)); const n = new Set(prev); keys.forEach(k => allOn ? n.delete(k) : n.add(k)); return n })}
                style={{ background: 'none', border: 'none', color: C.head, fontWeight: 800, fontSize: 12.5, cursor: 'pointer', fontFamily: sans }}>
                {signItems.every(i => selected.has(i.id)) ? 'Clear' : 'Select all'}
              </button>
            </div>
            {signItems.map(it => (
              <label key={it.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '11px 4px', minHeight: 44, borderTop: `1px solid ${C.line}`, cursor: 'pointer' }}>
                <input type="checkbox" checked={selected.has(it.id)} onChange={() => toggleSel(it.id)} aria-label={`Select to sign: ${it.title}`} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} />
                <span style={{ flex: 1, fontSize: 14, color: C.ink, lineHeight: 1.3 }}>
                  <b>{it.title}</b>{it.sub && <span style={{ color: C.inkSoft }}> · {it.sub}</span>}
                </span>
              </label>))}
            {[...selected].some(id => signItems.some(i => i.id === id)) && (
              <button onClick={signSelected} disabled={busy} style={{ width: '100%', marginTop: 12, padding: 13, borderRadius: 999, border: 'none', background: C.navy, color: C.orange, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1 }}>
                {busy ? 'Signing…' : `Sign ${[...selected].filter(id => signItems.some(i => i.id === id)).length}`}
              </button>)}
          </div>)}
      </main>

      {/* Bulk authorise sheet — ported verbatim from the Tasks screen. */}
      {bulkOpen && bulk && (
        <div role="dialog" aria-modal="true" aria-label="Authorise payments" onClick={() => !busy && setBulkOpen(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: C.card, width: '100%', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <b style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>Authorise {bulk.ready_count} payment{bulk.ready_count === 1 ? '' : 's'}</b>
              <button onClick={() => setBulkOpen(false)} aria-label="Close" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer' }}><X size={18} /></button>
            </div>
            <div style={{ fontFamily: serif, fontSize: 27, fontWeight: 800, color: C.ink, marginBottom: 12 }}>{money('BWP', bulk.ready_total)}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
              {bulk.ready.map(p => (
                <div key={p.id} style={{ border: `1px solid ${C.line}`, borderRadius: 12, padding: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                    <span style={{ fontSize: 13, color: C.inkSoft }}>{p.ref}</span>
                    <b style={{ fontSize: 13.5, color: C.ink, whiteSpace: 'nowrap' }}>{money(p.currency, p.total)}</b>
                  </div>
                  <div style={{ fontSize: 12, color: C.inkSoft, marginTop: 3 }}>{p.lines} line{p.lines === 1 ? '' : 's'}{p.signed_off_by ? ` · signed off by ${p.signed_off_by}` : ''}</div>
                  {p.edited_after_signoff && <div style={{ fontSize: 12, color: '#B45309', fontWeight: 700, marginTop: 4 }}>Changed after sign-off — read this one before you press</div>}
                </div>))}
            </div>
            {bulk.blocked_count > 0 && (
              <div style={{ background: '#FEF7F7', border: '1px solid #F3D6D6', borderRadius: 12, padding: 12, marginBottom: 14 }}>
                <b style={{ fontSize: 13, color: '#B91C1C' }}>{bulk.blocked_count} pack{bulk.blocked_count === 1 ? ' is' : 's are'} left out</b>
                <div style={{ fontSize: 12.5, color: C.inkSoft, marginTop: 4, lineHeight: 1.5 }}>{bulk.blocked.map(b => `${b.ref} — ${b.clashes} repeated line${b.clashes === 1 ? '' : 's'}`).join(' · ')}</div>
              </div>)}
            <p style={{ fontSize: 12.5, color: C.inkSoft, margin: '0 0 12px', lineHeight: 1.55 }}>This is your authorisation. Every pack is checked against the duplicate control again at the moment you press, and anything that has changed since this screen loaded will stop the whole run.</p>
            <button onClick={runBulkApprove} disabled={busy}
              style={{ width: '100%', padding: 15, borderRadius: 999, border: 'none', background: '#047857', color: '#fff', fontWeight: 800, fontSize: 15.5, cursor: 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Authorising…' : `Authorise ${money('BWP', bulk.ready_total)}`}
            </button>
          </div>
        </div>)}

      {/* Pack detail sheet — ported verbatim from the Tasks screen. */}
      {detailFor && (
        <div role="dialog" aria-modal="true" aria-label="Payment details" onClick={() => setDetailFor(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: C.card, width: '100%', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
              <b style={{ fontFamily: serif, fontSize: 18, color: C.ink, lineHeight: 1.3 }}>{detailFor.subject || detailFor.ref}</b>
              <button onClick={() => setDetailFor(null)} aria-label="Close" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer', flexShrink: 0 }}><X size={18} /></button>
            </div>
            {packBusy && <p style={{ color: C.inkSoft, fontSize: 13 }}>Loading the payment details…</p>}
            {packErr && <p style={{ color: '#B91C1C', fontSize: 13 }}>{packErr}</p>}
            {pack && (
              <>
                <div style={{ background: '#F0F2F5', borderRadius: 12, padding: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 12.5, color: C.inkSoft }}>{pack.ref} · {pack.entity}</div>
                  <div style={{ fontFamily: serif, fontSize: 24, fontWeight: 800, color: C.ink, margin: '2px 0 6px' }}>{money(pack.currency, pack.total)}</div>
                  <div style={{ fontSize: 13, color: C.ink }}><b>Paid to:</b> {pack.payee || '—'}</div>
                  {pack.subject && <div style={{ fontSize: 13, color: C.ink, marginTop: 2 }}><b>For:</b> {pack.subject}</div>}
                  <div style={{ fontSize: 12.5, color: C.inkSoft, marginTop: 4 }}>{pack.category_label}{pack.claim_payee_label ? ` · ${pack.claim_payee_label}` : ''}{pack.due_date ? ` · due ${pack.due_date}` : ''}</div>
                </div>
                {pack.summary && <p style={{ fontSize: 13, color: C.ink, lineHeight: 1.5, margin: '0 0 12px' }}>{pack.summary}</p>}
                <b style={{ fontSize: 13, color: C.ink }}>What makes up this amount ({pack.line_items.length})</b>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0 12px' }}>
                  {pack.line_items.map((ln, i) => {
                    const desc = (ln.description || '').trim()
                    const adds = (v?: string) => { const s = (v || '').trim(); return s && !desc.toLowerCase().includes(s.toLowerCase()) ? s : '' }
                    const inv = adds(ln.invoice_number), clm = adds(ln.claim_number), rf = adds(ln.ref)
                    const meta = [inv && `Invoice ${inv}`, ln.invoice_date && `dated ${ln.invoice_date}`, ln.due_date && `due ${ln.due_date}`, clm && `claim ${clm}`, rf && `ref ${rf}`].filter(Boolean).join(' · ')
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
                    <b style={{ color: C.ink }}>Paid from:</b> {pack.account_name}{pack.bank_name ? ` · ${pack.bank_name}` : ''}{pack.account_number ? ` · ${maskAccount(pack.account_number)}` : ''}
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
                  Raised by {pack.created_by || '—'}{pack.first_approver ? ` · finance sign-off by ${pack.first_approver}` : ''}{pack.verifier ? ` · verified by ${pack.verifier}` : ''}{pack.decision_notes ? ` · note: ${pack.decision_notes}` : ''}
                </div>
              </>)}

            {/* Per-payment CFO actions (CFO 2026-08-29) — the mobile screen had
                lost these. Blocked (duplicate) packs can't be approved here, only
                cleared or handed over. Gated on `pack` too: never offer an action
                until the full pack has loaded, so the CFO never signs blind. */}
            {detailFor && pack && (
              <div style={{ marginTop: 18, borderTop: `1px solid ${C.line}`, paddingTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                {(detailFor.clashes ?? 0) > 0 ? (
                  <p style={{ fontSize: 12.5, color: '#B45309', margin: 0, background: '#FEF7F7', border: '1px solid #F3D6D6', borderRadius: 10, padding: '8px 10px' }}>
                    ⚠ Possible duplicate — this can&apos;t be authorised here. Clear it or hand it over, then fix it in Omni.
                  </p>
                ) : (
                  <button onClick={approveOne} disabled={busy}
                    style={{ width: '100%', padding: 14, borderRadius: 999, border: 'none', background: '#047857', color: '#fff', fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, opacity: busy ? 0.6 : 1 }}>
                    <CheckCircle2 size={17} /> {busy ? 'Authorising…' : 'Approve — mark as paid'}
                  </button>
                )}
                <div style={{ display: 'flex', gap: 10 }}>
                  <button onClick={openReassign} disabled={busy}
                    style={{ flex: 1, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: panel === 'reassign' ? '#F6F7F9' : '#fff', color: C.head, fontWeight: 800, fontSize: 13.5, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                    <UserPlus size={15} /> Assign to…
                  </button>
                  <button onClick={() => setPanel(p => p === 'decline' ? '' : 'decline')} disabled={busy}
                    style={{ flex: 1, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: panel === 'decline' ? '#FEF2F2' : '#fff', color: '#B91C1C', fontWeight: 800, fontSize: 13.5, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                    <Ban size={15} /> Decline
                  </button>
                </div>

                {panel === 'reassign' && (
                  <div style={{ border: `1px solid ${C.line}`, borderRadius: 12, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <p style={{ fontSize: 12, color: C.inkSoft, margin: 0 }}>Hand this to the right person (e.g. above your limit) — it lands in their inbox.</p>
                    <select value={reTo} onChange={e => setReTo(e.target.value)} aria-label="Hand this payment to"
                      style={{ width: '100%', boxSizing: 'border-box', border: `1px solid ${C.line}`, borderRadius: 10, padding: '11px 12px', fontSize: 14, fontFamily: sans, background: C.card, color: C.ink }}>
                      <option value="">{assignees === null ? 'Loading people…' : 'Choose a person…'}</option>
                      {(assignees ?? []).map(a => (
                        <option key={a.username} value={a.username}>{a.full_name}{a.title ? ` — ${a.title}` : ''}</option>
                      ))}
                    </select>
                    <input value={reReason} onChange={e => setReReason(e.target.value)} placeholder="Reason (optional)" aria-label="Reason for handing over (optional)"
                      style={{ width: '100%', boxSizing: 'border-box', border: `1px solid ${C.line}`, borderRadius: 10, padding: '11px 12px', fontSize: 14, fontFamily: sans }} />
                    <button onClick={reassignOne} disabled={busy || !reTo}
                      style={{ width: '100%', padding: 12, borderRadius: 999, border: 'none', background: reTo ? C.navy : '#E5E7EB', color: reTo ? C.orange : '#9CA3AF', fontWeight: 800, fontSize: 14, cursor: reTo ? 'pointer' : 'default', fontFamily: sans }}>
                      {busy ? 'Handing over…' : 'Hand it over'}
                    </button>
                  </div>
                )}

                {panel === 'decline' && (
                  <div style={{ border: `1px solid ${C.line}`, borderRadius: 12, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <p style={{ fontSize: 12, color: C.inkSoft, margin: 0 }}>Not paying this through Omni? Clear it out of the queue (already paid elsewhere, duplicate, or expired). This does <b>not</b> pay anything.</p>
                    <textarea value={declineReason} onChange={e => setDeclineReason(e.target.value)} rows={2} placeholder="Reason (required)" aria-label="Reason for clearing (required)"
                      style={{ width: '100%', boxSizing: 'border-box', border: `1px solid ${C.line}`, borderRadius: 10, padding: '11px 12px', fontSize: 14, fontFamily: sans, resize: 'vertical' }} />
                    <button onClick={declineOne} disabled={busy || !declineReason.trim()}
                      style={{ width: '100%', padding: 12, borderRadius: 999, border: 'none', background: declineReason.trim() ? '#B91C1C' : '#E5E7EB', color: declineReason.trim() ? '#fff' : '#9CA3AF', fontWeight: 800, fontSize: 14, cursor: declineReason.trim() ? 'pointer' : 'default', fontFamily: sans }}>
                      {busy ? 'Clearing…' : 'Clear from queue'}
                    </button>
                  </div>
                )}
              </div>)}
          </div>
        </div>)}

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 70, maxWidth: '88%', textAlign: 'center' }}>{toast}</div>)}
    </div>
  )
}
