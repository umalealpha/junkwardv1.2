'use client'

import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, XCircle, RotateCcw, Loader2, AlertCircle, Search } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'

interface BuyerQuote {
  id: string
  item: string
  item_code: string
  part_name: string
  buyer_name: string
  buyer_email: string
  buyer_phone: string
  buyer_company: string
  offered_price: string
  message: string
  status: 'pending' | 'under_review' | 'accepted' | 'rejected' | 'countered'
  counter_price: string | null
  reviewer: string | null
  review_notes: string
  reviewed_at: string | null
  created_at: string
}

interface ListResp { count: number; results: BuyerQuote[] }

const STATUSES: { key: string; label: string }[] = [
  { key: '',              label: 'All' },
  { key: 'pending',       label: 'Pending' },
  { key: 'under_review',  label: 'Under review' },
  { key: 'accepted',      label: 'Accepted' },
  { key: 'rejected',      label: 'Rejected' },
  { key: 'countered',     label: 'Countered' },
]

const fmt = (n: string | number) => `P ${Number(n).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function SalvageQuotesPage() {
  const { theme } = useTheme()
  const [rows, setRows]   = useState<BuyerQuote[]>([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState('pending')
  const [search, setSearch] = useState('')
  const [acting, setActing] = useState<string | null>(null)
  const [counterPrice, setCounterPrice] = useState('')
  const [activeRow, setActiveRow] = useState<string | null>(null)
  const [reviewNote, setReviewNote] = useState('')

  const url = useMemo(() => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    p.set('page_size', '100')
    return `/salvage/buyer-quotes/?${p.toString()}`
  }, [status])

  const load = () => {
    setLoading(true); setError(null)
    apiFetch<ListResp>(url)
      .then(r => { setRows(r.results || []); setCount(r.count || 0) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [url]) // eslint-disable-line react-hooks/exhaustive-deps

  const review = async (id: string, decision: 'accepted' | 'rejected' | 'countered') => {
    setActing(id)
    try {
      const body: Record<string, unknown> = { decision, notes: reviewNote }
      if (decision === 'countered') {
        if (!counterPrice || isNaN(Number(counterPrice))) {
          alert('Enter a counter price first.'); setActing(null); return
        }
        body.counter_price = counterPrice
      }
      const res = await apiFetch<{ approval_required?: boolean; approval?: { id: string } }>(
        `/salvage/buyer-quotes/${id}/review/`,
        { method: 'POST', body: JSON.stringify(body) },
      )
      if (res.approval_required) {
        alert('Offer accepted. Amount exceeds CFO threshold — an approval has been queued for EXCO sign-off.')
      }
      setActiveRow(null); setCounterPrice(''); setReviewNote('')
      load()
    } catch (e) {
      alert(`Review failed: ${e instanceof Error ? e.message : 'unknown'}`)
    } finally { setActing(null) }
  }

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(r =>
      r.buyer_name.toLowerCase().includes(q) ||
      r.item_code.toLowerCase().includes(q) ||
      r.part_name.toLowerCase().includes(q),
    )
  }, [rows, search])

  return (
    <div style={{ background: theme.bg, minHeight: '100vh' }}>
      <TopBar />
      <div style={{ padding: '1.5rem 2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <div>
            <h1 style={{ color: theme.text, fontSize: 22, fontWeight: 700, margin: 0 }}>Salvage Buyer Quotes</h1>
            <p style={{ color: theme.t3, fontSize: 13, margin: '4px 0 0' }}>{count} offer{count === 1 ? '' : 's'} in the queue</p>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 8, top: 9, color: theme.t3 }} />
              <input value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Search buyer / item…"
                style={{ padding: '6px 8px 6px 28px', borderRadius: 6, border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text, fontSize: 13 }} />
            </div>
            <select value={status} onChange={e => setStatus(e.target.value)}
              style={{ padding: '6px 8px', borderRadius: 6, border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text, fontSize: 13 }}>
              {STATUSES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
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
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: theme.g100, color: theme.t2 }}>
                  <th style={th}>Item</th>
                  <th style={th}>Buyer</th>
                  <th style={th}>Contact</th>
                  <th style={{ ...th, textAlign: 'right' }}>Offer</th>
                  <th style={{ ...th, textAlign: 'right' }}>Counter</th>
                  <th style={th}>Status</th>
                  <th style={th}>Submitted</th>
                  <th style={th}>Action</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={8} style={{ padding: 32, textAlign: 'center', color: theme.t3 }}>No quotes match the filter.</td></tr>
                ) : filtered.map(r => {
                  const open = activeRow === r.id
                  return (
                    <>
                      <tr key={r.id} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                        <td style={td}>
                          <div style={{ fontWeight: 600, color: theme.text }}>{r.item_code}</div>
                          <div style={{ color: theme.t3, fontSize: 12 }}>{r.part_name}</div>
                        </td>
                        <td style={td}>
                          <div>{r.buyer_name}</div>
                          {r.buyer_company && <div style={{ color: theme.t3, fontSize: 12 }}>{r.buyer_company}</div>}
                        </td>
                        <td style={td}>
                          <div>{r.buyer_phone}</div>
                          {r.buyer_email && <div style={{ color: theme.t3, fontSize: 12 }}>{r.buyer_email}</div>}
                        </td>
                        <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{fmt(r.offered_price)}</td>
                        <td style={{ ...td, textAlign: 'right', color: theme.t3 }}>{r.counter_price ? fmt(r.counter_price) : '—'}</td>
                        <td style={td}><StatusBadge status={r.status} theme={theme} /></td>
                        <td style={{ ...td, color: theme.t3 }}>{new Date(r.created_at).toLocaleDateString()}</td>
                        <td style={td}>
                          {(r.status === 'pending' || r.status === 'under_review') ? (
                            <button onClick={() => { setActiveRow(open ? null : r.id); setCounterPrice(''); setReviewNote('') }}
                              style={btn(theme, 'primary')}>{open ? 'Cancel' : 'Review'}</button>
                          ) : <span style={{ color: theme.t3 }}>—</span>}
                        </td>
                      </tr>
                      {open && (
                        <tr style={{ background: theme.g100 }}>
                          <td colSpan={8} style={{ padding: 16 }}>
                            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                              <div style={{ flex: 1, minWidth: 220 }}>
                                <label style={lbl(theme)}>Review notes (optional)</label>
                                <input value={reviewNote} onChange={e => setReviewNote(e.target.value)}
                                  placeholder="Visible to the buyer if accepted/countered"
                                  style={input(theme)} />
                              </div>
                              <div>
                                <label style={lbl(theme)}>Counter price (BWP)</label>
                                <input value={counterPrice} onChange={e => setCounterPrice(e.target.value)}
                                  placeholder="Used only for 'Counter'"
                                  style={{ ...input(theme), width: 160 }} type="number" min="0" step="0.01" />
                              </div>
                              <div style={{ display: 'flex', gap: 6 }}>
                                <button onClick={() => review(r.id, 'accepted')} disabled={acting === r.id}
                                  style={btn(theme, 'success')}><CheckCircle2 size={14} /> Accept</button>
                                <button onClick={() => review(r.id, 'countered')} disabled={acting === r.id}
                                  style={btn(theme, 'warn')}><RotateCcw size={14} /> Counter</button>
                                <button onClick={() => review(r.id, 'rejected')} disabled={acting === r.id}
                                  style={btn(theme, 'danger')}><XCircle size={14} /> Reject</button>
                              </div>
                            </div>
                            {r.message && (
                              <div style={{ marginTop: 8, fontSize: 12, color: theme.t2 }}>
                                <strong>Buyer message:</strong> {r.message}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

const th: React.CSSProperties = { padding: '10px 12px', textAlign: 'left', fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4 }
const td: React.CSSProperties = { padding: '10px 12px', verticalAlign: 'top' }
const lbl = (theme: { t3: string }): React.CSSProperties => ({ display: 'block', fontSize: 11, color: theme.t3, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 })
const input = (theme: { cardBdr: string; card: string; text: string }): React.CSSProperties => ({ padding: '6px 8px', borderRadius: 6, border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text, fontSize: 13, width: '100%' })
const btn = (theme: { orange?: string }, kind: 'primary' | 'success' | 'danger' | 'warn'): React.CSSProperties => {
  const bg = kind === 'primary' ? (theme.orange || '#F07F00')
           : kind === 'success' ? '#16a34a'
           : kind === 'danger'  ? '#dc2626'
           : '#ca8a04'
  return { display: 'inline-flex', alignItems: 'center', gap: 4, padding: '6px 10px', background: bg, color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer' }
}

function StatusBadge({ status, theme }: { status: string; theme: { t3: string } }) {
  const colour: Record<string, string> = {
    pending: '#ca8a04', under_review: '#0ea5e9', accepted: '#16a34a',
    rejected: '#dc2626', countered: '#9333ea',
  }
  return (
    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 12, background: (colour[status] || theme.t3) + '22', color: colour[status] || theme.t3, fontWeight: 600, textTransform: 'uppercase' }}>
      {status.replace('_', ' ')}
    </span>
  )
}
