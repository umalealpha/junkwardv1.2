'use client'

/** /m/staff/approvals — ONE dashboard for approvers (CFO 2026-07-22:
 * "make all approvals in one dashboard for approvers"). Mirrors the desktop
 * Omni "My Approvals": every sign-only stream waiting on THIS user is itemised
 * with a checkbox; tick a few (or "select all") and sign them in ONE tap via
 * /api/v1/my-approvals/bulk-approve/ — the SAME server rule each item's own
 * page enforces (authority, segregation-of-duties, payment quorum, audit). No
 * 30-character note to approve — approving is meant to be fast.
 *
 * Streams that need a reason to REJECT (spend & event, refunds) also live here
 * with inline approve / send-back, reusing the same endpoints as the old
 * /m/staff/money screen so nothing is lost. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, CheckCircle2, XCircle } from 'lucide-react'
import {
  getStaffApprovalItems, bulkApprove, decideApproval, DECISION_PRESETS, getStaffApprovalHistory,
  getApprovalBrief, getApprovalPack, enableApprovalPush, sfetch, hfetch,
  getStaffApprovals, fetchPoAttachmentBlob,
  type MyApprovalItems, type ApprovalHistoryRow, type ApprovalStream, type ApprovalPack,
} from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { openHrisFile } from './StaffFormKit'

// The PO pack behind a 'po' stream item (CFO 2026-09-03: "approve with the
// pack"). Same detail endpoint the desktop PO page reads —
// GET /api/v1/purchase-orders/<id>/ (PurchaseOrderDetailSerializer) plus
// GET /api/v1/purchase-orders/<id>/attachments/ (POAttachmentViewSet.list).
// Every figure is the server's, echoed as sent.
interface PoLine { id: string; description: string; quantity: string; unit_price: string; line_total: string; account_code?: string }
// `file_url` = the authenticated /api/v1/…/attachments/<id>/file/ path (the
// only one prod serves); `url` = the raw /media/ path, kept for old backends.
interface PoAttachment { id: string; label: string; filename: string; url: string; file_url?: string; size_bytes: number }
interface PoPack {
  po_number: string; supplier_name: string; company_name?: string; department_display: string; status_display: string
  currency_code: string; subtotal: string; tax_total: string; total_amount: string; total_bwp?: string
  justification: string; submitted_by_username?: string; expected_delivery_date?: string | null
  lines: PoLine[]; attachments?: PoAttachment[]
}
const NAVY_TEXT = '#0D1B2A'   // AA on white and on orange (axe audit 2026-09-03)

interface SR { id: string; request_type_display: string; title: string; amount: string; requester: string; status: string; within_budget: boolean; ai_summary: string }
interface Claim { id: string; requester: string; category: string; amount: string; currency: string; status: string; has_payment_proof: boolean }
interface LeaveRow { id: string; employee: string; leave_type: string; start_date: string; end_date: string; days: number; day_breakdown?: string; reason: string; has_certificate: boolean }
type Pending = { kind: 'spend'; id: string } | { kind: 'refund'; id: string } | { kind: 'leave'; id: string }

const rowKey = (streamKey: string, id: string) => `${streamKey}::${id}`

export default function StaffApprovals() {
  const base = useStaffBase()
  const [data, setData] = useState<MyApprovalItems | null>(null)
  const [spend, setSpend] = useState<SR[]>([])
  const [canSpend, setCanSpend] = useState(false)
  const [refunds, setRefunds] = useState<Claim[]>([])
  const [toProcess, setToProcess] = useState<Claim[]>([])   // senior-accountant queue (pay in FNB, attach in Omni)
  const [leaveQueue, setLeaveQueue] = useState<LeaveRow[]>([])
  const [otherStreams, setOtherStreams] = useState<ApprovalStream[]>([])  // catch-all so every counted stream is visible
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [rejectFor, setRejectFor] = useState<Pending | null>(null)
  const [reason, setReason] = useState('')
  // The item whose five-button decision sheet is open (bulk streams only).
  const [decideItem, setDecideItem] = useState<{ stream: string; id: string; title: string } | null>(null)
  const [history, setHistory] = useState<ApprovalHistoryRow[] | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [brief, setBrief] = useState<string>('')
  const [poPack, setPoPack] = useState<PoPack | null>(null)
  const [poErr, setPoErr] = useState<string | null>(null)
  // The detail pack for every OTHER stream (CFO 2026-09-20) — PO keeps its own
  // bespoke pack above, which also shows attachments.
  const [pack, setPack] = useState<ApprovalPack | null>(null)
  const [packLoading, setPackLoading] = useState(false)
  const [pushable, setPushable] = useState(false)   // VAPID configured on the server
  const [pushOn, setPushOn] = useState(false)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  // Is push configured server-side? (hides the button otherwise.)
  useEffect(() => {
    sfetch<{ key: string }>('/my-approvals/push/vapid-key/')
      .then(d => setPushable(!!d.key)).catch(() => setPushable(false))
  }, [])

  // Fetch the decision-sheet one-liner when a sheet opens (wow-feature 3).
  useEffect(() => {
    setBrief('')
    if (!decideItem) return
    let live = true
    getApprovalBrief(decideItem.stream, decideItem.id)
      .then(d => { if (live) setBrief(d.brief || '') }).catch(() => {})
    return () => { live = false }
  }, [decideItem])

  // The detail pack — what am I actually signing? Same pack as the email.
  useEffect(() => {
    setPack(null)
    if (!decideItem || decideItem.stream === 'po') { setPackLoading(false); return }
    let live = true
    setPackLoading(true)
    getApprovalPack(decideItem.stream, decideItem.id)
      .then(d => { if (live) setPack(d.pack) })
      .catch(() => { if (live) setPack(null) })
      .finally(() => { if (live) setPackLoading(false) })
    return () => { live = false }
  }, [decideItem])

  // PO pack — lines + attachments — so the approver sees WHAT they are signing.
  useEffect(() => {
    setPoPack(null); setPoErr(null)
    if (!decideItem || decideItem.stream !== 'po') return
    let live = true
    Promise.all([
      sfetch<PoPack>(`/purchase-orders/${decideItem.id}/`),
      sfetch<{ attachments: PoAttachment[] }>(`/purchase-orders/${decideItem.id}/attachments/`).catch(() => ({ attachments: [] as PoAttachment[] })),
    ]).then(([po, att]) => { if (live) setPoPack({ ...po, attachments: att.attachments || [] }) })
      .catch(e => { if (live) setPoErr(e instanceof Error ? e.message : 'Could not load the PO.') })
    return () => { live = false }
  }, [decideItem])

  // Token-bearing fetch → blob → new tab (the openHrisFile pattern): a plain
  // <a href> would carry no Authorization header. Prefer the authenticated
  // file_url; the raw /media/ url only when an older backend omits file_url.
  async function openAttachment(a: PoAttachment) {
    try {
      if (!a.file_url) { await openHrisFile(a.url); return }
      const u = URL.createObjectURL(await fetchPoAttachmentBlob(a.file_url))
      window.open(u, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(u), 60_000)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not open the file.') }
  }

  async function turnOnPush() {
    setBusy(true)
    try {
      const r = await enableApprovalPush()
      const msg: Record<string, string> = {
        on: 'Alerts on — I\'ll nudge you when approvals arrive. 🔔',
        denied: 'You blocked notifications — turn them on in your phone settings.',
        unsupported: 'This phone/browser can\'t do push alerts.',
        unconfigured: 'Alerts aren\'t switched on by IT yet.',
        error: 'Could not turn on alerts — try again.',
      }
      if (r === 'on') setPushOn(true)
      show(msg[r] || 'Done.')
    } finally { setBusy(false) }
  }

  const openHistory = () => {
    setShowHistory(true)
    if (history === null) getStaffApprovalHistory().then(d => setHistory(d.history || [])).catch(() => setHistory([]))
  }

  const load = useCallback(() => {
    getStaffApprovalItems().then(setData).catch(() => setData({ streams: [], total: 0 }))
    getStaffApprovals().then(a => setOtherStreams(a.streams)).catch(() => setOtherStreams([]))
    sfetch<{ can_approve: boolean; requests: SR[] }>('/spend-requests/')
      .then(d => { setCanSpend(!!d.can_approve); setSpend((d.requests || []).filter(r => r.status === 'submitted')) })
      .catch(() => {})
    sfetch<Claim[]>('/expense-claims/cfo-queue/').then(setRefunds).catch(() => setRefunds([]))
    sfetch<Claim[]>('/expense-claims/queue/').then(setToProcess).catch(() => setToProcess([]))
    // Leave approvals folded into this one dashboard (CFO 2026-07-22). Same
    // HRIS queue + decide endpoints as the leave page's Approve tab.
    hfetch<{ pending: LeaveRow[] }>('/leave-requests/queue/')
      .then(d => setLeaveQueue(d.pending || [])).catch(() => setLeaveQueue([]))
  }, [])
  useEffect(() => { load() }, [load])

  // Payments live on their own screen now (CFO 2026-08-29): keep them off this
  // 'other things' dashboard so the two are never mixed again.
  const streams = (data?.streams ?? []).filter(s => s.key !== 'payments')
  const toggle = (k: string) => setSelected(prev => {
    const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n
  })
  const toggleStream = (s: MyApprovalItems['streams'][number]) => setSelected(prev => {
    const n = new Set(prev)
    const keys = s.items.map(i => rowKey(s.key, i.id))
    const allOn = keys.every(k => n.has(k))
    keys.forEach(k => allOn ? n.delete(k) : n.add(k))
    return n
  })

  const selectedCount = selected.size
  // Running cash total of ticked items (wow-feature 1). Sums numeric amounts;
  // BWP is the vast majority, so we show one BWP figure (a mixed-currency tick
  // is rare and still directionally right for a "how much am I signing" glance).
  const selectedTotal = useMemo(() => {
    let sum = 0
    for (const s of streams) for (const it of s.items)
      if (selected.has(rowKey(s.key, it.id)) && it.amount) sum += it.amount
    return sum
  }, [streams, selected])
  const fmtBWP = (n: number) => `BWP ${n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
  // Streams already shown inline elsewhere on this screen — everything else in
  // /my-approvals/ (e.g. payroll sign-off) is surfaced by the catch-all card so
  // the badge count always has a home (the empty-Approvals bug, 2026-08-28).
  // 'payments' and 'payment_requests' are handled on the dedicated Payments
  // screen — mark them SHOWN so the catch-all never resurfaces them here.
  const SHOWN_KEYS = useMemo(() => new Set<string>([
    ...streams.map(s => s.key),
    'spend', 'refunds', 'refunds_process', 'customer_refunds_fnb', 'leave',
    'payments', 'payment_requests',
  ]), [streams])
  const uncovered = useMemo(
    () => otherStreams.filter(s => s.count > 0 && !SHOWN_KEYS.has(s.key)),
    [otherStreams, SHOWN_KEYS],
  )
  const payStreamCount = useMemo(
    () => (data?.streams ?? []).filter(s => s.key === 'payments').reduce((n, s) => n + s.items.length, 0),
    [data])
  const totalWaiting = useMemo(
    () => (data?.total ?? 0) - payStreamCount + spend.length + refunds.length + toProcess.length + leaveQueue.length
          + uncovered.reduce((n, s) => n + s.count, 0),
    [data, payStreamCount, spend, refunds, toProcess, leaveQueue, uncovered],
  )

  async function approveSelected() {
    const payload: { stream: string; id: string }[] = []
    for (const s of streams) for (const it of s.items)
      if (selected.has(rowKey(s.key, it.id))) payload.push({ stream: s.key, id: it.id })
    if (!payload.length) return
    setBusy(true)
    try {
      const res = await bulkApprove(payload)
      if (res.failed.length === 0) show(`Signed ${res.approved} — done. ✅`)
      else show(`Signed ${res.approved}. ${res.failed.length} couldn't: ${res.failed[0].error}`)
      setSelected(new Set()); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not sign.') }
    finally { setBusy(false) }
  }

  async function runDecision(preset: typeof DECISION_PRESETS[number]) {
    if (!decideItem) return
    const it = decideItem
    setBusy(true)
    try {
      await decideApproval(it.stream, it.id, preset.action, preset.note)
      show(preset.action === 'approve' ? 'Approved. ✅' : 'Sent back to the submitter.')
      setDecideItem(null)
      setSelected(prev => { const n = new Set(prev); n.delete(rowKey(it.stream, it.id)); return n })
      load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not action this.') }
    finally { setBusy(false) }
  }

  async function approveSpend(id: string) {
    setBusy(true)
    try { await sfetch(`/spend-requests/${id}/decide/`, { method: 'POST', body: JSON.stringify({ decision: 'approve', notes: '' }) }); show('Approved. ✅'); load() }
    catch (e) { show(e instanceof Error ? e.message : 'Failed') } finally { setBusy(false) }
  }
  async function approveRefund(id: string) {
    setBusy(true)
    try { await sfetch(`/expense-claims/${id}/approve/`, { method: 'POST', body: JSON.stringify({}) }); show('Refund approved. ✅'); load() }
    catch (e) { show(e instanceof Error ? e.message : 'Failed') } finally { setBusy(false) }
  }
  async function approveLeave(id: string) {
    setBusy(true)
    try { await hfetch(`/leave-requests/${id}/decide/`, { method: 'POST', body: JSON.stringify({ decision: 'approve', notes: '' }) }); show('Leave approved. ✅'); load() }
    catch (e) { show(e instanceof Error ? e.message : 'Failed') } finally { setBusy(false) }
  }
  async function confirmReject() {
    if (!rejectFor || !reason.trim()) return
    const p = rejectFor; setBusy(true)
    try {
      if (p.kind === 'spend')
        await sfetch(`/spend-requests/${p.id}/decide/`, { method: 'POST', body: JSON.stringify({ decision: 'reject', notes: reason.trim() }) })
      else if (p.kind === 'leave')
        await hfetch(`/leave-requests/${p.id}/decide/`, { method: 'POST', body: JSON.stringify({ decision: 'reject', notes: reason.trim() }) })
      else
        await sfetch(`/expense-claims/${p.id}/reject/`, { method: 'POST', body: JSON.stringify({ reason: reason.trim() }) })
      show('Sent back with your reason.'); setRejectFor(null); setReason(''); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Failed') } finally { setBusy(false) }
  }

  const ApproveReject = ({ onYes, onNo }: { onYes: () => void; onNo: () => void }) => (
    <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
      <button onClick={onYes} disabled={busy} style={{ flex: 1, minHeight: 44, padding: 12, borderRadius: 999, border: 'none', background: NAVY_TEXT, color: '#fff', fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}><CheckCircle2 size={16} aria-hidden="true" /> Approve</button>
      <button onClick={onNo} disabled={busy} style={{ flex: 1, minHeight: 44, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B91C1C', fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}><XCircle size={16} aria-hidden="true" /> Reject</button>
    </div>
  )

  const nothing = totalWaiting === 0

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans, paddingBottom: selectedCount ? 96 : 24 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'flex', minWidth: 44, minHeight: 44, alignItems: 'center' }}><ArrowLeft size={20} aria-hidden="true" /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0, color: '#fff' }}>Approvals</h1>
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Everything waiting for your sign-off. Tap an item for the buttons — approve, reject, or send it back — or tick several and approve all at once.</p>
        {pushable && !pushOn && (
          <button onClick={turnOnPush} disabled={busy}
            style={{ alignSelf: 'flex-start', minHeight: 44, padding: '9px 16px', borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>
            🔔 Turn on alerts — get nudged when approvals arrive
          </button>
        )}

        {nothing && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 28 }}>Nothing waiting on you. 🎉</p>}

        {/* One-tap bulk streams */}
        {streams.map(s => {
          const keys = s.items.map(i => rowKey(s.key, i.id))
          const allOn = keys.length > 0 && keys.every(k => selected.has(k))
          return (
            <div key={s.key} style={{ ...card, padding: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <b style={{ color: C.ink, fontSize: 15 }}>{s.label} <span style={{ color: C.inkSoft, fontWeight: 600 }}>({s.items.length})</span></b>
                <button onClick={() => toggleStream(s)} style={{ minHeight: 44, padding: '0 10px', background: 'none', border: 'none', color: NAVY_TEXT, fontWeight: 800, fontSize: 12.5, cursor: 'pointer', fontFamily: sans }}>{allOn ? 'Clear' : 'Select all'}</button>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                {s.items.map(it => {
                  const k = rowKey(s.key, it.id); const on = selected.has(k)
                  return (
                    <div key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 4, padding: '2px 0', borderTop: `1px solid ${C.line}` }}>
                      {/* 24px control inside a 44px hit area (axe audit 2026-09-03) */}
                      <label style={{ minWidth: 44, minHeight: 44, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, cursor: 'pointer' }}>
                        <input type="checkbox" checked={on} onChange={() => toggle(k)} aria-label={`Select ${it.title} for bulk approve`} style={{ width: 24, height: 24, margin: 0, accentColor: C.orange }} />
                      </label>
                      <button onClick={() => setDecideItem({ stream: s.key, id: it.id, title: it.title })}
                        style={{ flex: 1, minHeight: 44, textAlign: 'left', background: 'none', border: 'none', padding: '9px 4px', fontSize: 14, color: C.ink, lineHeight: 1.3, cursor: 'pointer', fontFamily: sans }}>
                        <div><b>{it.title}</b>{it.sub && <span style={{ color: C.inkSoft }}> · {it.sub}</span>}
                          <span aria-hidden="true" style={{ color: NAVY_TEXT, fontWeight: 700, fontSize: 12.5 }}>  ›</span></div>
                        {it.flags.length > 0 && (
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 5 }}>
                            {it.flags.map((f, i) => (
                              <span key={i} style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 7px', borderRadius: 6,
                                background: f.level === 'warn' ? '#FEF2F2' : '#F6F7F9',
                                color: f.level === 'warn' ? '#B91C1C' : C.inkSoft }}>
                                {f.level === 'warn' ? '⚠ ' : ''}{f.text}
                              </span>
                            ))}
                          </div>
                        )}
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}

        {/* Spend & event — approve, or send back with a reason */}
        {canSpend && spend.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>Spend &amp; event requests ({spend.length})</b>
            {spend.map(r => (
              <div key={r.id} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: '0 0 2px', fontWeight: 700, color: C.ink, fontSize: 14.5 }}>{r.title} — BWP {r.amount}</p>
                <p style={{ color: C.inkSoft, fontSize: 12.5, margin: 0 }}>{r.requester} · {r.within_budget ? 'within budget' : '⚠ out of budget'}</p>
                <ApproveReject onYes={() => approveSpend(r.id)} onNo={() => setRejectFor({ kind: 'spend', id: r.id })} />
              </div>))}
          </div>
        )}

        {/* Refunds — approve, or send back with a reason */}
        {refunds.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>Refunds to approve ({refunds.length})</b>
            {refunds.map(c => (
              <div key={c.id} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: 0, fontWeight: 700, color: C.ink, fontSize: 14.5 }}>{c.category || 'Refund'} — {c.currency} {c.amount}</p>
                <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>{c.requester}{c.has_payment_proof ? ' · proof attached' : ''}</p>
                <ApproveReject onYes={() => approveRefund(c.id)} onNo={() => setRejectFor({ kind: 'refund', id: c.id })} />
              </div>))}
          </div>
        )}

        {/* Refunds routed to YOU to pay (senior accountant) — pay in FNB, then
            attach the proof in Omni; or send back if it's wrong */}
        {toProcess.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>Refunds for you to process ({toProcess.length})</b>
            {toProcess.map(c => (
              <div key={c.id} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: 0, fontWeight: 700, color: C.ink, fontSize: 14.5 }}>{c.category || 'Refund'} — {c.currency} {c.amount}</p>
                <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>{c.requester}</p>
                <p style={{ color: C.inkSoft, fontSize: 12, margin: '6px 0 0', background: '#F6F7F9', borderRadius: 10, padding: '8px 10px' }}>Pay this in FNB, then open Omni on your computer to attach the payment proof. Send it back here if it&apos;s wrong.</p>
                <div style={{ marginTop: 10 }}>
                  <button onClick={() => setRejectFor({ kind: 'refund', id: c.id })} disabled={busy} style={{ width: '100%', minHeight: 44, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B91C1C', fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Send back</button>
                </div>
              </div>))}
          </div>
        )}

        {/* Leave — approve your team's requests, or decline with a reason */}
        {leaveQueue.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>Leave to approve ({leaveQueue.length})</b>
            {leaveQueue.map(l => (
              <div key={l.id} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: 0, fontWeight: 700, color: C.ink, fontSize: 14.5 }}>{l.employee} — {l.leave_type}</p>
                <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>
                  {l.day_breakdown || `${l.start_date} → ${l.end_date} · ${l.days}d`}{l.has_certificate ? ' · cert attached' : ''}
                </p>
                {l.reason && <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0', fontStyle: 'italic' }}>“{l.reason}”</p>}
                <ApproveReject onYes={() => approveLeave(l.id)} onNo={() => setRejectFor({ kind: 'leave', id: l.id })} />
              </div>))}
          </div>
        )}

        {/* Catch-all: anything else waiting (e.g. payroll sign-off) that is actioned
            on desktop Omni — so the badge count always has somewhere to land. */}
        {uncovered.length > 0 && (
          <div style={{ ...card, padding: 14 }}>
            <b style={{ color: C.ink, fontSize: 15 }}>Also waiting for you</b>
            {uncovered.map(s => (
              <div key={s.key} style={{ marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
                <p style={{ margin: 0, fontWeight: 700, color: C.ink, fontSize: 14.5 }}>{s.label} ({s.count})</p>
                <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>Open Omni on your computer to action{s.count === 1 ? ' this' : ' these'}.</p>
              </div>))}
          </div>
        )}

        {/* What I signed — recent decisions ledger (wow-feature 5) */}
        <button onClick={openHistory}
          style={{ marginTop: 4, alignSelf: 'flex-start', minHeight: 44, padding: '0 4px', background: 'none', border: 'none', color: NAVY_TEXT, fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans }}>
          What I signed →
        </button>
      </main>

      {/* Sticky sign bar */}
      {/* In the Omni app the tab bar owns the bottom 76px — sit above it, or the
          Approve button is hidden behind Home/Approve/Do (CFO 4-Sep-2026). */}
      {selectedCount > 0 && (
        <div style={{ position: 'fixed', bottom: base === '/app' ? 'calc(76px + env(safe-area-inset-bottom, 0px))' : 0, left: 0, right: 0, maxWidth: 480, margin: '0 auto', background: C.card, borderTop: `1px solid ${C.line}`, padding: base === '/app' ? '12px 16px' : '12px 16px calc(12px + env(safe-area-inset-bottom, 0px))', display: 'flex', alignItems: 'center', gap: 12, zIndex: 41 }}>
          <div>
            <span style={{ fontSize: 14, fontWeight: 700, color: C.ink }}>{selectedCount} selected</span>
            {selectedTotal > 0 && <span style={{ fontSize: 12.5, color: C.inkSoft, marginLeft: 8 }}>{fmtBWP(selectedTotal)}</span>}
          </div>
          <button onClick={() => setSelected(new Set())} disabled={busy} style={{ minHeight: 44, padding: '0 8px', background: 'none', border: 'none', color: C.inkSoft, fontSize: 13, fontWeight: 700, cursor: 'pointer', fontFamily: sans }}>Clear</button>
          <div style={{ flex: 1 }} />
          <button onClick={approveSelected} disabled={busy} style={{ minHeight: 48, padding: '13px 22px', borderRadius: 999, border: 'none', background: C.orange, color: NAVY_TEXT, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', gap: 7, opacity: busy ? 0.6 : 1 }}>
            <CheckCircle2 size={17} aria-hidden="true" /> {busy ? 'Signing…' : `Approve ${selectedCount}${selectedTotal > 0 ? ` · ${fmtBWP(selectedTotal)}` : ''}`}
          </button>
        </div>
      )}

      {/* "What I signed" ledger */}
      {showHistory && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 66 }} onClick={() => setShowHistory(false)}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', maxHeight: '80vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px calc(24px + env(safe-area-inset-bottom, 0px))' }}>
            <b style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>What I signed</b>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 12px' }}>Your recent approvals across Omni.</p>
            {history === null && <p style={{ color: C.inkSoft, fontSize: 13 }}>Loading…</p>}
            {history && history.length === 0 && <p style={{ color: C.inkSoft, fontSize: 13 }}>Nothing signed yet.</p>}
            {history && history.map((r, i) => (
              <div key={i} style={{ padding: '10px 0', borderTop: i ? `1px solid ${C.line}` : 'none' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 12.5, color: NAVY_TEXT }}>{r.kind}</span>
                  {r.at && <span style={{ fontSize: 11.5, color: C.inkSoft }}>{new Date(r.at).toLocaleDateString()}</span>}
                </div>
                <p style={{ margin: '2px 0 0', fontSize: 13, color: C.ink }}>{r.detail}</p>
              </div>
            ))}
            <button onClick={() => setShowHistory(false)} style={{ width: '100%', marginTop: 14, minHeight: 44, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Close</button>
          </div>
        </div>
      )}

      {/* Five-button decision sheet (CFO 2026-07-22) — one tap, no typing */}
      {decideItem && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 65 }} onClick={() => !busy && setDecideItem(null)}>
          <div role="dialog" aria-modal="true" aria-labelledby="decide-title" onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', maxHeight: '90vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px calc(24px + env(safe-area-inset-bottom, 0px))' }}>
            <h2 id="decide-title" style={{ fontFamily: serif, fontSize: 18, color: C.ink, margin: 0 }}>What is your decision?</h2>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 8px' }}>{decideItem.title}</p>
            {brief && (
              <p style={{ fontSize: 12.5, color: C.ink, margin: '0 0 14px', background: '#F6F7F9', borderRadius: 10, padding: '9px 11px' }}>🤖 {brief}</p>
            )}

            {/* The PO pack — lines + attachments — above the buttons (CFO 2026-09-03) */}
            {decideItem.stream === 'po' && (
              <section aria-label="Purchase order pack" style={{ margin: '0 0 14px', background: '#F6F7F9', borderRadius: 12, padding: '10px 12px', fontSize: 12.5, color: C.ink }}>
                {poPack === null && !poErr && <p style={{ margin: 0, color: C.inkSoft }}>Loading the PO…</p>}
                {poErr && <p role="alert" style={{ margin: 0, color: '#991B1B' }}>{poErr}</p>}
                {poPack && (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                      <b>{poPack.po_number}</b><span style={{ color: C.inkSoft }}>{poPack.status_display}</span>
                    </div>
                    <p style={{ margin: '2px 0 0' }}>{poPack.supplier_name}<span style={{ color: C.inkSoft }}> · {poPack.department_display}{poPack.company_name ? ` · ${poPack.company_name}` : ''}</span></p>
                    {poPack.submitted_by_username && <p style={{ margin: '2px 0 0', color: C.inkSoft }}>Raised by {poPack.submitted_by_username}{poPack.expected_delivery_date ? ` · delivery ${poPack.expected_delivery_date}` : ''}</p>}
                    <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8, fontSize: 12 }}>
                      <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>Lines</caption>
                      <thead><tr style={{ color: C.inkSoft, textAlign: 'left' }}><th scope="col" style={{ padding: '4px 0', fontWeight: 700 }}>Item</th><th scope="col" style={{ padding: '4px 0', fontWeight: 700, textAlign: 'right' }}>Qty × price</th><th scope="col" style={{ padding: '4px 0', fontWeight: 700, textAlign: 'right' }}>Total</th></tr></thead>
                      <tbody>
                        {poPack.lines.map(l => (
                          <tr key={l.id} style={{ borderTop: `1px solid ${C.line}` }}>
                            <td style={{ padding: '5px 6px 5px 0' }}>{l.description}{l.account_code ? <span style={{ color: C.inkSoft }}> · {l.account_code}</span> : null}</td>
                            <td style={{ padding: '5px 6px', textAlign: 'right', whiteSpace: 'nowrap', color: C.inkSoft }}>{l.quantity} × {l.unit_price}</td>
                            <td style={{ padding: '5px 0', textAlign: 'right', whiteSpace: 'nowrap', fontWeight: 700 }}>{l.line_total}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div style={{ borderTop: `1px solid ${C.line}`, marginTop: 4, paddingTop: 6, display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: C.inkSoft }}><span>Subtotal</span><span>{poPack.currency_code} {poPack.subtotal}</span></div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', color: C.inkSoft }}><span>VAT</span><span>{poPack.currency_code} {poPack.tax_total}</span></div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 800, fontSize: 14 }}><span>Total</span><span>{poPack.currency_code} {poPack.total_amount}</span></div>
                      {poPack.total_bwp && poPack.currency_code !== 'BWP' && <div style={{ display: 'flex', justifyContent: 'space-between', color: C.inkSoft }}><span>In BWP</span><span>BWP {poPack.total_bwp}</span></div>}
                    </div>
                    {poPack.justification && <p style={{ margin: '8px 0 0', fontStyle: 'italic', color: C.inkSoft }}>“{poPack.justification}”</p>}
                    <div style={{ marginTop: 8 }}>
                      <b style={{ fontSize: 12, color: C.inkSoft }}>Attachments ({poPack.attachments?.length ?? 0})</b>
                      {(poPack.attachments?.length ?? 0) === 0 && <p style={{ margin: '2px 0 0', color: '#92400E' }}>No quote or supporting document attached.</p>}
                      {poPack.attachments?.map(a => (
                        <button key={a.id} onClick={() => openAttachment(a)}
                          style={{ display: 'flex', width: '100%', minHeight: 44, alignItems: 'center', gap: 8, marginTop: 4, padding: '6px 10px', borderRadius: 10, border: `1px solid ${C.line}`, background: C.card, color: NAVY_TEXT, fontWeight: 700, fontSize: 12.5, cursor: 'pointer', fontFamily: sans, textAlign: 'left' }}>
                          📎 <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.label || a.filename}</span>
                          <span style={{ color: C.inkSoft, fontWeight: 600 }}>{a.size_bytes ? `${Math.max(1, Math.round(a.size_bytes / 1024))} KB` : ''}</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </section>
            )}

            {/* Everything else: the shared detail pack (CFO 2026-09-20) —
                the same lines, rates, deductions and sanity checks the
                approval email now carries, so a phone approval never needs
                the desktop site. */}
            {decideItem.stream !== 'po' && (packLoading || pack) && (
              <section aria-label="What you are approving" style={{ margin: '0 0 14px', background: '#F6F7F9', borderRadius: 12, padding: '10px 12px', fontSize: 12.5, color: C.ink }}>
                {packLoading && !pack && <p style={{ margin: 0, color: C.inkSoft }}>Loading the detail…</p>}
                {pack && (
                  <>
                    {pack.subtitle && <p style={{ margin: '0 0 6px', color: C.inkSoft, fontWeight: 700 }}>{pack.subtitle}</p>}
                    {pack.checks?.level === 'check' && (pack.checks.items?.length ?? 0) > 0 ? (
                      <div role="alert" style={{ margin: '0 0 8px', padding: '7px 9px', borderRadius: 9, background: '#FFFBEB', color: '#92400E' }}>
                        <b>Worth a look</b>
                        <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
                          {pack.checks.items.map((c, i) => <li key={i}>{c}</li>)}
                        </ul>
                      </div>
                    ) : pack.checks?.level === 'clean' ? (
                      <p style={{ margin: '0 0 8px', color: '#166534' }}>Checked — nothing looks out of place.</p>
                    ) : (
                      // 'unknown' = the checks crashed. Never render that green.
                      <p style={{ margin: '0 0 8px', color: '#334155' }}>The sanity checks could not be run on this one.</p>
                    )}
                    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 10px' }}>
                      {pack.summary.map((s, i) => (
                        <div key={i} style={{ display: 'contents' }}>
                          <span style={{ color: C.inkSoft, whiteSpace: 'nowrap' }}>{s.label}</span>
                          <span style={{ fontWeight: 700, textAlign: 'right' }}>{s.value}</span>
                        </div>
                      ))}
                    </div>
                    {pack.rows.length > 0 && (
                      <div style={{ marginTop: 10, overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                          <thead>
                            <tr>{pack.columns.map((c, i) => (
                              <th key={i} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '3px 6px 3px 0', color: C.inkSoft, borderBottom: `1px solid ${C.line}` }}>{c}</th>
                            ))}</tr>
                          </thead>
                          <tbody>
                            {pack.rows.map((r, ri) => (
                              <tr key={ri}>{r.map((cell, ci) => (
                                <td key={ci} style={{ textAlign: ci === 0 ? 'left' : 'right', padding: '3px 6px 3px 0', borderBottom: `1px solid ${C.line}` }}>{cell}</td>
                              ))}</tr>
                            ))}
                          </tbody>
                        </table>
                        <p style={{ margin: '6px 0 0', fontWeight: 800 }}>{pack.row_count} lines{pack.row_total ? ` · ${pack.row_total}` : ''}</p>
                        {pack.row_count > pack.shown_count && (
                          <p style={{ margin: '2px 0 0', color: C.inkSoft }}>Showing the first {pack.shown_count} of {pack.row_count}.</p>
                        )}
                      </div>
                    )}
                    {pack.note && <p style={{ margin: '8px 0 0', color: C.inkSoft }}>{pack.note}</p>}
                  </>
                )}
              </section>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {DECISION_PRESETS.map(p => {
                const bg = p.tone === 'yes' ? NAVY_TEXT : p.tone === 'no' ? '#B91C1C' : '#fff'
                const fg = p.tone === 'info' ? C.ink : '#fff'
                const bd = p.tone === 'info' ? `1px solid ${C.line}` : 'none'
                return (
                  <button key={p.key} onClick={() => runDecision(p)} disabled={busy}
                    style={{ width: '100%', padding: 15, borderRadius: 14, border: bd, background: bg, color: fg, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, textAlign: 'left', opacity: busy ? 0.6 : 1 }}>
                    {p.label}
                  </button>
                )
              })}
            </div>
            <button onClick={() => setDecideItem(null)} disabled={busy} style={{ width: '100%', marginTop: 12, minHeight: 44, padding: 12, borderRadius: 999, border: 'none', background: 'none', color: C.inkSoft, fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Cancel</button>
          </div>
        </div>
      )}

      {rejectFor && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }} onClick={() => !busy && setRejectFor(null)}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 18, fontFamily: sans }}>
            <label htmlFor="reject-reason" style={{ display: 'block', margin: '0 0 8px', fontWeight: 800, color: C.ink, fontSize: 16 }}>Reason (sent to the requester)</label>
            <textarea id="reject-reason" value={reason} onChange={e => setReason(e.target.value)} rows={3} autoFocus
              placeholder="Say why, in a line or two…"
              style={{ width: '100%', boxSizing: 'border-box', border: `1px solid ${C.line}`, borderRadius: 12, padding: 12, fontSize: 14, fontFamily: sans }} />
            <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
              <button onClick={() => setRejectFor(null)} style={{ flex: 1, minHeight: 44, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Cancel</button>
              <button onClick={confirmReject} disabled={busy || !reason.trim()} style={{ flex: 1, minHeight: 44, padding: 12, borderRadius: 999, border: 'none', background: reason.trim() ? '#B91C1C' : '#E5E7EB', color: reason.trim() ? '#fff' : NAVY_TEXT, fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Send back</button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div style={{ position: 'fixed', bottom: `calc(${selectedCount ? 88 : 32}px + ${base === '/app' ? '76px + env(safe-area-inset-bottom, 0px)' : '0px'})`, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 70, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
