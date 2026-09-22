'use client'
// Omni Mobile — Purchase orders list / lookup.
// Mirrors the desktop PO list, reusing GET /purchase-orders/. Raising already
// lives at /app/raise-po and approve/reject in Approvals; this adds browse +
// a detail screen (which carries Cancel — the one gap vs desktop).
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Search, Loader2, FileText } from 'lucide-react'
import { afetch } from '../../api'
import { C, card, serif, headerPad } from '../../ui'

interface PORow {
  id: string; po_number: string; department_display: string; supplier_name: string
  status: string; status_display: string; currency_code: string
  total_amount: string; total_bwp: string
}
interface POList { count: number; results: PORow[] }

const FILTERS: { k: string; label: string }[] = [
  { k: '', label: 'All' },
  { k: 'pending_fm_approval', label: 'Awaiting approval' },
  { k: 'pending_cfo_approval', label: 'Awaiting CFO' },
  { k: 'approved', label: 'Approved' },
  { k: 'rejected', label: 'Rejected' },
  { k: 'cancelled', label: 'Cancelled' },
]

const STATUS_TONE: Record<string, string> = {
  approved: C.green, fully_received: C.green, partially_received: C.green,
  rejected: C.red, cancelled: C.red, expired: C.red,
  pending_fm_approval: C.amber, pending_cfo_approval: C.amber, draft: C.inkSoft,
}

function pula(n: string) {
  const v = parseFloat(n)
  return isNaN(v) ? '—' : `P ${v.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function PurchaseOrders() {
  const [rows, setRows] = useState<PORow[] | null>(null)
  const [count, setCount] = useState(0)
  const [err, setErr] = useState(false)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    let alive = true
    setRows(null); setErr(false)
    const t = setTimeout(() => {
      const params = new URLSearchParams()
      if (q.trim()) params.set('search', q.trim())
      if (status) params.set('status', status)
      afetch<POList>(`/purchase-orders/?${params.toString()}`)
        .then(r => { if (alive) { setRows(r.results || []); setCount(r.count || 0) } })
        .catch(() => { if (alive) { setRows([]); setErr(true) } })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [q, status])

  return (
    <main>
      <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
        <Link href="/app/work" aria-label="Back" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>
        <div>
          <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, lineHeight: 1, margin: 0 }}>Purchase orders</h1>
          <div style={{ opacity: 0.8, fontSize: 13, marginTop: 4 }}>Find, view, approve or cancel a PO</div>
        </div>
      </header>

      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
        <div className="oa-rise" style={{ ...card, padding: 12, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Search size={18} color={C.inkSoft} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="PO number or supplier" aria-label="Search purchase orders" style={{ flex: 1, border: 0, outline: 'none', fontSize: 16, color: C.ink, background: 'transparent' }} />
        </div>

        <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 2 }}>
          {FILTERS.map(f => (
            <button key={f.label} onClick={() => setStatus(f.k)} className="oa-press" aria-pressed={status === f.k}
              style={{ whiteSpace: 'nowrap', minHeight: 36, padding: '0 12px', borderRadius: 999, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${status === f.k ? C.navy : C.line}`, background: status === f.k ? C.navy : C.card, color: status === f.k ? '#fff' : C.ink }}>{f.label}</button>
          ))}
        </div>

        <div className="oa-rise oa-rise-2" style={{ ...card, padding: 0, overflow: 'hidden' }}>
          {rows === null && <div style={{ padding: 24, textAlign: 'center', color: C.inkSoft }}><Loader2 size={20} className="oa-spin" /></div>}
          {err && <div role="alert" style={{ padding: 20, textAlign: 'center', color: C.red, fontSize: 14 }}>Couldn&apos;t load purchase orders. Check your connection and try again.</div>}
          {rows !== null && !err && rows.length === 0 && <div style={{ padding: 24, textAlign: 'center', color: C.inkSoft, fontSize: 14 }}>No purchase orders match.</div>}
          {(rows || []).map((po, i, arr) => (
            <Link key={po.id} href={`/app/purchase-orders/${po.id}`} className="oa-press"
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', textDecoration: 'none', color: C.ink, borderBottom: i === arr.length - 1 ? undefined : `1px solid ${C.line}` }}>
              <span style={{ display: 'grid', placeItems: 'center', width: 40, height: 40, borderRadius: 12, background: 'var(--ao-navy-wash, rgba(11,11,59,0.06))', flexShrink: 0 }}><FileText size={19} color={C.head} /></span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: 'block', fontWeight: 700, fontSize: 15 }}>{po.po_number}</span>
                <span style={{ display: 'block', fontSize: 12.5, color: C.inkSoft, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{po.supplier_name} · {po.department_display}</span>
              </span>
              <span style={{ textAlign: 'right', flexShrink: 0 }}>
                <span style={{ display: 'block', fontWeight: 700, fontSize: 14 }}>{pula(po.total_bwp)}</span>
                <span style={{ display: 'block', marginTop: 3, fontSize: 11, fontWeight: 700, color: STATUS_TONE[po.status] || C.inkSoft }}>{po.status_display}</span>
              </span>
            </Link>
          ))}
        </div>
        {rows && !err && count > rows.length && (
          <p style={{ color: C.inkSoft, fontSize: 12.5, textAlign: 'center', margin: '2px 0 4px' }}>
            Showing the latest {rows.length} of {count} — search by PO number or supplier to narrow.
          </p>
        )}
      </section>
    </main>
  )
}
