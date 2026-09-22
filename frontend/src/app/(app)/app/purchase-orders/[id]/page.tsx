'use client'
// Omni Mobile — Purchase order detail + actions. Reuses the desktop endpoints:
// GET /purchase-orders/<id>/ and the submit / fm-approve / cfo-approve / reject /
// cancel actions. Buttons are gated by the server's own can_approve / can_cancel /
// status, so the phone shows exactly what the desktop would allow.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ChevronLeft, Loader2, Check, X } from 'lucide-react'
import { afetch } from '../../../api'
import { C, card, serif, headerPad, h } from '../../../ui'

interface POLine { id: string; description: string; quantity: string; unit_price: string; line_total: string }
interface PODetail {
  id: string; po_number: string; department: string; department_display: string
  supplier_name: string; status: string; status_display: string; currency_code: string
  subtotal: string; tax_total: string; total_amount: string; total_bwp: string
  justification: string
  submitted_by_username?: string; fm_approved_by_username?: string; cfo_approved_by_username?: string
  rejection_reason?: string; cancellation_reason?: string
  lines: POLine[]; can_approve?: boolean; can_reject?: boolean; can_cancel?: boolean
}

function pula(n?: string, ccy = 'P') {
  const v = parseFloat(n || '')
  return isNaN(v) ? '—' : `${ccy} ${v.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function PODetailScreen() {
  const params = useParams<{ id: string }>()
  const id = params?.id
  const [po, setPo] = useState<PODetail | null>(null)
  const [loadErr, setLoadErr] = useState(false)
  const [busy, setBusy] = useState('')      // which action is running
  const [err, setErr] = useState('')
  const [reasonFor, setReasonFor] = useState<'reject' | 'cancel' | ''>('')
  const [reason, setReason] = useState('')

  const load = useCallback(() => {
    if (!id) return
    setLoadErr(false)
    afetch<PODetail>(`/purchase-orders/${id}/`).then(setPo).catch(() => setLoadErr(true))
  }, [id])
  useEffect(() => { load() }, [load])

  const act = async (path: string, key: string, body?: object) => {
    if (busy) return
    setBusy(key); setErr('')
    try {
      // The action returns the fresh PO (with updated can_approve/can_cancel), so
      // use it directly — atomic, no stale buttons between success and a reload.
      const updated = await afetch<PODetail>(`/purchase-orders/${id}/${path}/`, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
      setPo(updated)
      setReasonFor(''); setReason('')
    } catch (e) {
      setErr((e as Error)?.message || 'That action could not be completed. Try again.')
    } finally { setBusy('') }
  }

  const submitReason = () => {
    if (!reason.trim() || !reasonFor) return
    act(reasonFor, reasonFor, { reason: reason.trim() })
  }

  if (loadErr) return <Shell><Msg tone="err">Couldn&apos;t load this purchase order. <button onClick={load} style={linkBtn}>Try again</button></Msg></Shell>
  if (!po) return <Shell><div style={{ padding: 28, textAlign: 'center', color: C.inkSoft }}><Loader2 size={22} className="oa-spin" /></div></Shell>

  const isClaims = po.department === 'claims'
  const cc = po.currency_code === 'BWP' ? 'P' : po.currency_code   // lines/net/VAT are in the PO's own currency
  const showApprove = po.status === 'pending_fm_approval' && po.can_approve
  const showCfo = po.status === 'pending_cfo_approval' && po.can_approve
  const showReject = (po.status === 'pending_fm_approval' || po.status === 'pending_cfo_approval') && po.can_reject
  const showSubmit = po.status === 'draft'
  const showCancel = !!po.can_cancel

  return (
    <Shell>
      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
        <div className="oa-rise" style={{ ...card, padding: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: serif, fontSize: 20, fontWeight: 700, color: C.head }}>{po.po_number}</span>
            <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 700, color: toneFor(po.status) }}>{po.status_display}</span>
          </div>
          <div style={{ fontSize: 13.5, color: C.inkSoft, marginTop: 4 }}>{po.supplier_name} · {po.department_display}</div>
          <div style={{ fontFamily: serif, fontSize: 30, fontWeight: 700, color: C.head, marginTop: 10 }}>{pula(po.total_bwp)}</div>
          <div style={{ fontSize: 12.5, color: C.inkSoft }}>Total in BWP · Net {pula(po.subtotal, cc)} + VAT {pula(po.tax_total, cc)}{cc !== 'P' ? ` (${po.currency_code})` : ''}</div>
        </div>

        {po.justification && (
          <div className="oa-rise oa-rise-2" style={{ ...card, padding: 16 }}>
            <div style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.inkSoft, fontWeight: 600 }}>Why it&apos;s being bought</div>
            <div style={{ fontSize: 14, marginTop: 4 }}>{po.justification}</div>
          </div>
        )}

        <div className="oa-rise oa-rise-2" style={{ ...card, padding: 0, overflow: 'hidden' }}>
          <div style={{ ...h(14), padding: '12px 16px', borderBottom: `1px solid ${C.line}` }}>Items</div>
          {po.lines.map((ln, i) => (
            <div key={ln.id} style={{ display: 'flex', gap: 10, padding: '10px 16px', borderBottom: i === po.lines.length - 1 ? undefined : `1px solid ${C.line}` }}>
              <span style={{ flex: 1, fontSize: 14 }}>{ln.description}<span style={{ color: C.inkSoft, fontSize: 12.5 }}> · {ln.quantity} × {pula(ln.unit_price, cc)}</span></span>
              <span style={{ fontWeight: 700, fontSize: 14, whiteSpace: 'nowrap' }}>{pula(ln.line_total, cc)}</span>
            </div>
          ))}
        </div>

        {(po.rejection_reason || po.cancellation_reason) && (
          <div className="oa-rise" style={{ ...card, padding: 14, borderLeft: `4px solid ${C.red}`, fontSize: 14 }}>
            {po.rejection_reason ? `Rejected: ${po.rejection_reason}` : `Cancelled: ${po.cancellation_reason}`}
          </div>
        )}

        {err && <div role="alert" style={{ ...card, padding: 14, borderLeft: `4px solid ${C.red}`, color: C.red, fontSize: 14 }}>{err}</div>}

        {/* Actions — only what the server says this person may do */}
        {(showSubmit || showApprove || showCfo || showReject || showCancel) && (
          <div style={{ display: 'grid', gap: 10 }}>
            {showSubmit && <Btn onClick={() => act('submit', 'submit')} busy={busy === 'submit'} tone="navy">Submit for approval</Btn>}
            {showApprove && <Btn onClick={() => act('fm-approve', 'fm-approve')} busy={busy === 'fm-approve'} tone="go">{isClaims ? 'Claims approve' : 'Approve'}</Btn>}
            {showCfo && <Btn onClick={() => act('cfo-approve', 'cfo-approve')} busy={busy === 'cfo-approve'} tone="go">CFO approve</Btn>}
            {showReject && <Btn onClick={() => { setReasonFor('reject'); setReason('') }} tone="danger-outline">Reject</Btn>}
            {showCancel && <Btn onClick={() => { setReasonFor('cancel'); setReason('') }} tone="danger-outline">Cancel PO</Btn>}
          </div>
        )}
      </section>

      {reasonFor && (
        <div role="dialog" aria-modal="true" aria-label={reasonFor === 'reject' ? 'Reject this PO' : 'Cancel this PO'} onClick={() => !busy && setReasonFor('')}
             style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'var(--ao-navy-wash, rgba(11,11,59,0.06))', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
          <div onClick={e => e.stopPropagation()} className="oa-rise" style={{ width: '100%', maxWidth: 480, background: C.card, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: '16px 16px calc(20px + env(safe-area-inset-bottom, 0px))' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ flex: 1, fontFamily: serif, fontWeight: 700, fontSize: 18, color: C.head }}>{reasonFor === 'reject' ? 'Reject this PO' : 'Cancel this PO'}</span>
              <button onClick={() => !busy && setReasonFor('')} aria-label="Close" className="oa-press" style={{ border: 0, background: 'transparent', color: C.inkSoft }}><X size={22} /></button>
            </div>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} autoFocus placeholder="Reason (required)" aria-label="Reason"
              style={{ width: '100%', boxSizing: 'border-box', resize: 'none', padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 16, fontFamily: 'inherit', color: C.ink }} />
            {err && <div role="alert" style={{ color: C.red, fontSize: 13.5, marginTop: 8 }}>{err}</div>}
            <button onClick={submitReason} disabled={!reason.trim() || !!busy} className="oa-press"
              style={{ width: '100%', marginTop: 10, minHeight: 50, borderRadius: 14, border: 0, background: reason.trim() && !busy ? C.red : C.line, color: reason.trim() && !busy ? '#fff' : C.inkSoft, fontWeight: 800, fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              {busy ? <Loader2 size={20} className="oa-spin" /> : null}{reasonFor === 'reject' ? 'Reject PO' : 'Cancel PO'}
            </button>
          </div>
        </div>
      )}
    </Shell>
  )
}

const linkBtn: React.CSSProperties = { color: C.head, fontWeight: 700, background: 'none', border: 0, textDecoration: 'underline', cursor: 'pointer', font: 'inherit' }
function toneFor(s: string) {
  if (['approved', 'fully_received', 'partially_received'].includes(s)) return C.green
  if (['rejected', 'cancelled', 'expired'].includes(s)) return C.red
  if (['pending_fm_approval', 'pending_cfo_approval'].includes(s)) return C.amber
  return C.inkSoft
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main>
      <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Link href="/app/purchase-orders" aria-label="Back" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>
        <h1 style={{ fontFamily: serif, fontSize: 22, fontWeight: 700, lineHeight: 1, margin: 0 }}>Purchase order</h1>
      </header>
      {children}
    </main>
  )
}
function Msg({ tone, children }: { tone: 'err'; children: React.ReactNode }) {
  return <section style={{ padding: '0 16px', marginTop: -28 }}><div className="oa-rise" style={{ ...card, padding: 18, color: tone === 'err' ? C.red : C.ink, fontSize: 14, textAlign: 'center' }}>{children}</div></section>
}
function Btn({ onClick, busy, tone, children }: { onClick: () => void; busy?: boolean; tone: 'navy' | 'go' | 'danger-outline'; children: React.ReactNode }) {
  const styles: Record<string, React.CSSProperties> = {
    navy: { background: C.navy, color: '#fff', border: 0 },
    go: { background: C.orange, color: C.navy, border: 0 },
    'danger-outline': { background: C.card, color: C.red, border: `1px solid ${C.red}` },
  }
  return (
    <button onClick={onClick} disabled={busy} className="oa-press"
      style={{ minHeight: 52, borderRadius: 14, fontWeight: 800, fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, cursor: 'pointer', ...styles[tone] }}>
      {busy ? <Loader2 size={20} className="oa-spin" /> : (tone === 'go' ? <Check size={20} /> : null)}{children}
    </button>
  )
}
