'use client'

/** /m/staff/petty-cash — ask for petty cash BEFORE spending. Rides the same
 * pre-spend approval workflow as /spend-requests (CFO/EXCO approve), so
 * Finance sees every request in one queue. */
import { useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Wallet } from 'lucide-react'
import { sfetch } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans }

export default function StaffPettyCash() {
  const base = useStaffBase()
  const [what, setWhat] = useState('')
  const [amount, setAmount] = useState('')
  const [why, setWhy] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3500) }

  async function submit() {
    const amt = Number(amount)
    if (!what.trim()) { show('Say what it is for.'); return }
    if (!isFinite(amt) || amt <= 0) { show('Enter an amount greater than zero.'); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('request_type', 'other')
      fd.append('title', `Petty cash — ${what.trim()}`.slice(0, 200))
      fd.append('amount', amt.toFixed(2))
      // NOTE: do NOT forge within_budget — staff are not asked and cannot
      // attest to it (no-forge rule for /m/*). Let the approver assess it.
      fd.append('description', why.trim() || what.trim())
      await sfetch('/spend-requests/', { method: 'POST', body: fd }, false)
      setDone(true)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not submit.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Petty cash</h1>
      </header>

      <main style={{ padding: 16 }}>
        {done ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <p style={{ fontSize: 52, margin: '16px 0 8px' }}>✅</p>
            <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>Request sent</h2>
            <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 24px' }}>
              It&apos;s with the approvers. You&apos;ll be told once it&apos;s approved — then collect from Finance.
            </p>
            <Link href={base} style={{ display: 'inline-block', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, textDecoration: 'none', padding: '13px 28px', borderRadius: 999 }}>Done</Link>
          </div>
        ) : (
          <div style={{ ...card, padding: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Wallet size={20} style={{ color: C.orange }} />
              </div>
              <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>Ask <b style={{ color: C.ink }}>before</b> you spend — approval comes back fast.</p>
            </div>
            <input value={what} onChange={e => setWhat(e.target.value)} placeholder="What is it for? (stationery, taxi…)" aria-label="What is it for" style={{ ...inputStyle, marginBottom: 10 }} />
            <input value={amount} onChange={e => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} inputMode="decimal" placeholder="Amount (BWP)" aria-label="Amount (BWP)" style={{ ...inputStyle, marginBottom: 10 }} />
            <textarea value={why} onChange={e => setWhy(e.target.value)} rows={3} placeholder="A line or two of detail (optional)" aria-label="Detail (optional)" style={{ ...inputStyle, marginBottom: 14, resize: 'vertical' }} />
            <button onClick={submit} disabled={busy}
              style={{ width: '100%', padding: 14, borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Sending…' : 'Request petty cash'}
            </button>
          </div>
        )}
      </main>

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
