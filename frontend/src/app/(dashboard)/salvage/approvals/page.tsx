'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, Loader2, AlertCircle, ShieldCheck } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'

interface Approval {
  id: string
  kind: 'sale' | 'quote_accept' | 'disposal'
  item: string
  item_code: string
  buyer_quote: string | null
  sale: string | null
  requested_by: string
  requester_name: string
  approved_by: string | null
  approver_name: string | null
  status: 'pending' | 'approved' | 'rejected'
  requested_amount: string
  threshold_amount: string
  notes: string
  resolved_at: string | null
  created_at: string
}

interface ListResp { count: number; results: Approval[] }

const fmt = (n: string | number) => `P ${Number(n).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const KIND_LABEL: Record<string, string> = {
  sale: 'Sale below reserve / over threshold',
  quote_accept: 'Quote acceptance',
  disposal: 'Disposal / write-off',
}

export default function SalvageApprovalsPage() {
  const { theme } = useTheme()
  const [rows, setRows] = useState<Approval[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState('pending')
  const [acting, setActing] = useState<string | null>(null)
  const [activeRow, setActiveRow] = useState<string | null>(null)
  const [decisionNotes, setDecisionNotes] = useState('')

  const load = () => {
    setLoading(true); setError(null)
    apiFetch<ListResp>(`/salvage/approvals/?status=${status}&page_size=100`)
      .then(r => setRows(r.results || []))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [status]) // eslint-disable-line

  const resolve = async (id: string, decision: 'approved' | 'rejected') => {
    setActing(id)
    try {
      await apiFetch(`/salvage/approvals/${id}/resolve/`, {
        method: 'POST',
        body: JSON.stringify({ decision, notes: decisionNotes }),
      })
      setActiveRow(null); setDecisionNotes('')
      load()
    } catch (e) {
      alert(`Resolve failed: ${e instanceof Error ? e.message : 'unknown'}`)
    } finally { setActing(null) }
  }

  return (
    <div style={{ background: theme.bg, minHeight: '100vh' }}>
      <TopBar />
      <div style={{ padding: '1.5rem 2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <div>
            <h1 style={{ color: theme.text, fontSize: 22, fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
              <ShieldCheck size={22} style={{ color: theme.orange }} /> Salvage Approvals
            </h1>
            <p style={{ color: theme.t3, fontSize: 13, margin: '4px 0 0' }}>EXCO sign-off queue for sales over threshold or below reserve.</p>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {['pending', 'approved', 'rejected'].map(s => (
              <button key={s} onClick={() => setStatus(s)}
                style={{ padding: '6px 12px', background: status === s ? (theme.orange || '#F07F00') : 'transparent', color: status === s ? '#fff' : theme.text, border: `1px solid ${theme.cardBdr}`, borderRadius: 6, fontSize: 12, cursor: 'pointer', textTransform: 'capitalize' }}>
                {s}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div style={{ background: '#fee', color: '#900', padding: 12, borderRadius: 6, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertCircle size={16} /> {error}
          </div>
        )}

        {loading ? (
          <div style={{ padding: 32, textAlign: 'center', color: theme.t3 }}><Loader2 className="animate-spin" /></div>
        ) : (
          <div style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, borderRadius: 8, overflow: 'hidden' }}>
            {rows.length === 0 ? (
              <div style={{ padding: 32, textAlign: 'center', color: theme.t3 }}>
                No {status} approvals.
              </div>
            ) : rows.map(r => {
              const open = activeRow === r.id
              return (
                <div key={r.id} style={{ padding: 16, borderBottom: `1px solid ${theme.cardBdr}` }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 12, background: '#F07F0022', color: theme.orange, fontWeight: 600, textTransform: 'uppercase' }}>{KIND_LABEL[r.kind] || r.kind}</span>
                        <span style={{ fontWeight: 600, color: theme.text }}>{r.item_code}</span>
                      </div>
                      <div style={{ color: theme.t3, fontSize: 13 }}>{r.notes}</div>
                      <div style={{ marginTop: 6, fontSize: 12, color: theme.t3 }}>
                        Requested by <strong>{r.requester_name}</strong> on {new Date(r.created_at).toLocaleString()}
                        {r.approver_name && <> · Resolved by <strong>{r.approver_name}</strong></>}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 20, fontWeight: 700, color: theme.text }}>{fmt(r.requested_amount)}</div>
                      <div style={{ fontSize: 11, color: theme.t3 }}>threshold {fmt(r.threshold_amount)}</div>
                    </div>
                  </div>
                  {r.status === 'pending' && (
                    <div style={{ marginTop: 12 }}>
                      {!open ? (
                        <button onClick={() => { setActiveRow(r.id); setDecisionNotes('') }}
                          style={{ padding: '6px 12px', background: theme.orange || '#F07F00', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                          Review
                        </button>
                      ) : (
                        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                          <div style={{ flex: 1 }}>
                            <label style={{ display: 'block', fontSize: 11, color: theme.t3, marginBottom: 4 }}>Decision notes</label>
                            <input value={decisionNotes} onChange={e => setDecisionNotes(e.target.value)}
                              placeholder="Required for auditable rationale"
                              style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text, fontSize: 13 }} />
                          </div>
                          <button onClick={() => resolve(r.id, 'approved')} disabled={acting === r.id}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '6px 10px', background: '#16a34a', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                            <CheckCircle2 size={14} /> Approve
                          </button>
                          <button onClick={() => resolve(r.id, 'rejected')} disabled={acting === r.id}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '6px 10px', background: '#dc2626', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                            <XCircle size={14} /> Reject
                          </button>
                          <button onClick={() => setActiveRow(null)}
                            style={{ padding: '6px 10px', background: 'transparent', color: theme.t3, border: `1px solid ${theme.cardBdr}`, borderRadius: 6, fontSize: 12, cursor: 'pointer' }}>
                            Cancel
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
