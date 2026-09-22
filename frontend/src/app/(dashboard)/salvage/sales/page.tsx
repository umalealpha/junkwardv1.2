'use client'

import { useEffect, useMemo, useState } from 'react'
import { Plus, Loader2, AlertCircle, X, CheckCircle2 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'
import { localYmd } from '@/lib/utils'

interface Sale {
  id: string; item: string; item_code: string; part_name: string;
  buyer_name: string; buyer_phone: string; buyer_email: string;
  sale_price: string; payment_method: string; payment_ref: string;
  sale_date: string; seller: string | null; approver: string | null;
  journal_entry: string | null; notes: string; created_at: string;
}
interface SalvageItemLite { id: string; item_code: string; part_name: string; asking_price: string; reserve_price: string }
interface ListResp<T> { count: number; results: T[] }

const fmt = (n: string | number) => `P ${Number(n).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function SalvageSalesPage() {
  const { theme } = useTheme()
  const [rows, setRows] = useState<Sale[]>([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)

  // form state
  const [items, setItems] = useState<SalvageItemLite[]>([])
  const [itemId, setItemId] = useState('')
  const [buyerName, setBuyerName] = useState('')
  const [buyerPhone, setBuyerPhone] = useState('')
  const [buyerEmail, setBuyerEmail] = useState('')
  const [salePrice, setSalePrice] = useState('')
  const [paymentMethod, setPaymentMethod] = useState('eft')
  const [paymentRef, setPaymentRef] = useState('')
  const [saleDate, setSaleDate] = useState(() => localYmd(new Date()))
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = () => {
    setLoading(true); setError(null)
    apiFetch<ListResp<Sale>>('/salvage/sales/?page_size=100')
      .then(r => { setRows(r.results || []); setCount(r.count || 0) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }
  useEffect(load, [])

  useEffect(() => {
    if (showForm && items.length === 0) {
      apiFetch<ListResp<SalvageItemLite>>('/salvage-items/?status=quoted&page_size=200')
        .then(r => setItems(r.results || [])).catch(() => {})
    }
  }, [showForm, items.length])

  const submit = async () => {
    if (!itemId || !buyerName.trim() || !salePrice) { alert('Item, buyer name and sale price are required.'); return }
    setSubmitting(true)
    try {
      await apiFetch('/salvage/sales/', {
        method: 'POST',
        body: JSON.stringify({
          item: itemId, buyer_name: buyerName, buyer_phone: buyerPhone, buyer_email: buyerEmail,
          sale_price: salePrice, payment_method: paymentMethod, payment_ref: paymentRef,
          sale_date: saleDate, notes,
        }),
      })
      setShowForm(false); setItemId(''); setBuyerName(''); setBuyerPhone(''); setBuyerEmail('')
      setSalePrice(''); setPaymentRef(''); setNotes('')
      load()
    } catch (e) {
      alert(`Failed to record sale: ${e instanceof Error ? e.message : 'unknown'}`)
    } finally { setSubmitting(false) }
  }

  const totalSold = useMemo(() => rows.reduce((a, b) => a + Number(b.sale_price || 0), 0), [rows])

  return (
    <div style={{ background: theme.bg, minHeight: '100vh' }}>
      <TopBar />
      <div style={{ padding: '1.5rem 2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <div>
            <h1 style={{ color: theme.text, fontSize: 22, fontWeight: 700, margin: 0 }}>Salvage Sales Register</h1>
            <p style={{ color: theme.t3, fontSize: 13, margin: '4px 0 0' }}>{count} sale{count === 1 ? '' : 's'} — total proceeds {fmt(totalSold)}</p>
          </div>
          <button onClick={() => setShowForm(true)}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px', background: theme.orange || '#F07F00', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
            <Plus size={14} /> Record sale
          </button>
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
                  <th style={th}>Date</th>
                  <th style={th}>Item</th>
                  <th style={th}>Buyer</th>
                  <th style={th}>Method</th>
                  <th style={{ ...th, textAlign: 'right' }}>Price</th>
                  <th style={th}>Seller</th>
                  <th style={th}>JE</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr><td colSpan={7} style={{ padding: 32, textAlign: 'center', color: theme.t3 }}>No sales recorded yet.</td></tr>
                ) : rows.map(r => (
                  <tr key={r.id} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                    <td style={td}>{r.sale_date}</td>
                    <td style={td}>
                      <div style={{ fontWeight: 600, color: theme.text }}>{r.item_code}</div>
                      <div style={{ color: theme.t3, fontSize: 12 }}>{r.part_name}</div>
                    </td>
                    <td style={td}>
                      <div>{r.buyer_name}</div>
                      <div style={{ color: theme.t3, fontSize: 12 }}>{r.buyer_phone}</div>
                    </td>
                    <td style={td}>{r.payment_method.toUpperCase()}{r.payment_ref && <div style={{ color: theme.t3, fontSize: 12 }}>{r.payment_ref}</div>}</td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{fmt(r.sale_price)}</td>
                    <td style={td}>{r.seller || '—'}</td>
                    <td style={td}>{r.journal_entry ? <span title={r.journal_entry} style={{ color: '#16a34a' }}><CheckCircle2 size={14} /></span> : <span style={{ color: theme.t3 }}>—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {showForm && (
          <div style={overlay} onClick={() => setShowForm(false)}>
            <div style={{ ...modal, background: theme.card }} onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h2 style={{ color: theme.text, fontSize: 18, fontWeight: 700, margin: 0 }}>Record salvage sale</h2>
                <button onClick={() => setShowForm(false)} style={{ background: 'none', border: 'none', color: theme.t3, cursor: 'pointer' }}><X size={18} /></button>
              </div>
              <Field theme={theme} label="Item">
                <select value={itemId} onChange={e => setItemId(e.target.value)} style={input(theme)}>
                  <option value="">Select an item (quoted)…</option>
                  {items.map(i => <option key={i.id} value={i.id}>{i.item_code} — {i.part_name} (asking {fmt(i.asking_price)})</option>)}
                </select>
              </Field>
              <Field theme={theme} label="Buyer name *"><input value={buyerName} onChange={e => setBuyerName(e.target.value)} style={input(theme)} /></Field>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <Field theme={theme} label="Buyer phone"><input value={buyerPhone} onChange={e => setBuyerPhone(e.target.value)} style={input(theme)} /></Field>
                <Field theme={theme} label="Buyer email"><input value={buyerEmail} onChange={e => setBuyerEmail(e.target.value)} type="email" style={input(theme)} /></Field>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                <Field theme={theme} label="Sale price (BWP) *"><input value={salePrice} onChange={e => setSalePrice(e.target.value)} type="number" min="0" step="0.01" style={input(theme)} /></Field>
                <Field theme={theme} label="Payment method">
                  <select value={paymentMethod} onChange={e => setPaymentMethod(e.target.value)} style={input(theme)}>
                    <option value="cash">Cash</option>
                    <option value="eft">EFT</option>
                    <option value="cheque">Cheque</option>
                    <option value="mobile_money">Mobile money</option>
                    <option value="card">Card</option>
                  </select>
                </Field>
                <Field theme={theme} label="Sale date"><input value={saleDate} onChange={e => setSaleDate(e.target.value)} type="date" style={input(theme)} /></Field>
              </div>
              <Field theme={theme} label="Payment ref"><input value={paymentRef} onChange={e => setPaymentRef(e.target.value)} style={input(theme)} placeholder="EFT ref, cheque #, etc." /></Field>
              <Field theme={theme} label="Notes"><textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} style={{ ...input(theme), resize: 'vertical' }} /></Field>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                <button onClick={() => setShowForm(false)} style={{ padding: '8px 14px', background: 'transparent', color: theme.text, border: `1px solid ${theme.cardBdr}`, borderRadius: 6, fontSize: 13, cursor: 'pointer' }}>Cancel</button>
                <button onClick={submit} disabled={submitting}
                  style={{ padding: '8px 14px', background: theme.orange || '#F07F00', color: '#fff', border: 'none', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', opacity: submitting ? 0.6 : 1 }}>
                  {submitting ? 'Recording…' : 'Record sale'}
                </button>
              </div>
              <p style={{ color: theme.t3, fontSize: 12, marginTop: 12 }}>
                Posts a GL journal entry automatically (DR cash, CR salvage income). If the
                sale price is below reserve or above the CFO threshold, an approval row
                will be queued for EXCO sign-off.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ theme, label, children }: { theme: { t3: string }; label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{ display: 'block', fontSize: 11, color: theme.t3, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</label>
      {children}
    </div>
  )
}

const th: React.CSSProperties = { padding: '10px 12px', textAlign: 'left', fontWeight: 600, fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4 }
const td: React.CSSProperties = { padding: '10px 12px', verticalAlign: 'top' }
const input = (theme: { cardBdr: string; card: string; text: string }): React.CSSProperties => ({
  padding: '8px 10px', borderRadius: 6, border: `1px solid ${theme.cardBdr}`, background: theme.card, color: theme.text, fontSize: 13, width: '100%', boxSizing: 'border-box',
})
const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16,
}
const modal: React.CSSProperties = { width: '100%', maxWidth: 560, borderRadius: 10, padding: 24, maxHeight: '90vh', overflowY: 'auto' }
