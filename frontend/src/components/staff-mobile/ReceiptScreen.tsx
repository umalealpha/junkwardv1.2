'use client'

/** /m/staff/receipt — photograph a receipt and claim the money back.
 * Submits into the SAME refund workflow as desktop /refunds (accountant loads
 * the payment in FNB, CFO approves). The camera opens straight away. */
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Camera, Upload, X } from 'lucide-react'
import { sfetch, compressImage, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Approver { id: number; name: string }
const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans }

export default function StaffReceipt() {
  const base = useStaffBase()
  const [approvers, setApprovers] = useState<Approver[]>([])
  const [photos, setPhotos] = useState<File[]>([])
  const [previews, setPreviews] = useState<string[]>([])
  const [amount, setAmount] = useState('')
  const [category, setCategory] = useState('')
  const [approverId, setApproverId] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  // If the phone's sign-in has expired, this 401s: bounce to sign-in instead of
  // leaving the accountant list silently empty (which made Submit feel dead).
  useEffect(() => {
    sfetch<Approver[]>('/expense-claims/approvers/').then(setApprovers)
      .catch(e => { if (!reauthOn401(e)) show('Could not load the accountants — check your connection and reopen this screen.') })
  }, [])
  useEffect(() => () => { previews.forEach(u => URL.revokeObjectURL(u)) }, [previews])

  const words = description.trim() ? description.trim().split(/\s+/).length : 0

  const onSnap = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]
    e.target.value = ''
    if (!raw) return
    const f = await compressImage(raw)   // shrink camera output before upload
    setPhotos(p => [...p, f])
    setPreviews(p => [...p, URL.createObjectURL(f)])
  }
  const removePhoto = (i: number) => {
    URL.revokeObjectURL(previews[i])
    setPhotos(p => p.filter((_, j) => j !== i))
    setPreviews(p => p.filter((_, j) => j !== i))
  }

  async function submit() {
    if (photos.length === 0) { show('Snap the receipt first.'); return }
    if (!amount || !approverId) { show('Fill in the amount and pick an accountant.'); return }
    if (words < 50) { show(`Describe it in at least 50 words (${words}/50) — the approver needs the story.`); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('amount', amount); fd.append('category', category || 'Receipt claim')
      fd.append('description', description); fd.append('approver_id', approverId)
      photos.forEach(f => fd.append('invoices', f))
      await sfetch('/expense-claims/', { method: 'POST', body: fd }, false)
      setDone(true)
    } catch (e) { if (!reauthOn401(e)) show(e instanceof Error ? e.message : 'Could not submit.') }
    finally { setBusy(false) }
  }

  if (done) return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans, display: 'flex', flexDirection: 'column' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Snap a receipt</h1>
      </header>
      <main style={{ padding: 32, textAlign: 'center' }}>
        <p style={{ fontSize: 52, margin: '24px 0 8px' }}>✅</p>
        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>Claim submitted</h2>
        <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 24px' }}>
          It&apos;s with the accountant now. You&apos;ll see the status here and get the money once it&apos;s approved.
        </p>
        <Link href={base} style={{ display: 'inline-block', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, textDecoration: 'none', padding: '13px 28px', borderRadius: 999 }}>Done</Link>
      </main>
    </div>
  )

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Snap a receipt</h1>
      </header>

      <main style={{ padding: 16 }}>
        <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onSnap} />

        {/* photos */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          {previews.map((u, i) => (
            <div key={u} style={{ position: 'relative' }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={u} alt={`receipt ${i + 1}`} style={{ width: 92, height: 92, objectFit: 'cover', borderRadius: 14, border: `1px solid ${C.line}` }} />
              <button onClick={() => removePhoto(i)} aria-label="Remove photo"
                style={{ position: 'absolute', top: -6, right: -6, width: 22, height: 22, borderRadius: 999, border: 'none', background: C.navy, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                <X size={12} />
              </button>
            </div>))}
          <button onClick={() => camRef.current?.click()}
            style={{ width: 92, height: 92, borderRadius: 14, border: `2px dashed ${C.orange}`, background: '#FFF7ED', color: '#92400E', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4, cursor: 'pointer', fontFamily: sans, fontSize: 11, fontWeight: 700 }}>
            <Camera size={22} /> {photos.length ? 'Add another' : 'Open camera'}
          </button>
        </div>

        <div style={{ ...card, padding: 18 }}>
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={amount} onChange={e => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} inputMode="decimal" placeholder="Amount (BWP)" aria-label="Amount (BWP)" style={{ ...inputStyle, flex: 1 }} />
            <input value={category} onChange={e => setCategory(e.target.value)} placeholder="What for (fuel…)" aria-label="What for" style={{ ...inputStyle, flex: 1 }} />
          </div>
          <select value={approverId} onChange={e => setApproverId(e.target.value)} aria-label="Send to (senior accountant)" style={{ ...inputStyle, margin: '10px 0' }}>
            <option value="">Send to… (senior accountant)</option>
            {approvers.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={4} aria-label="Describe the expense"
            placeholder="Describe the expense in your own words (at least 50 words) — what it was for, when, why."
            style={{ ...inputStyle, resize: 'vertical' }} />
          <p style={{ fontSize: 13, fontWeight: 700, color: words >= 50 ? '#047857' : C.inkSoft, margin: '6px 0 12px' }}>{words}/50 words</p>
          <button onClick={submit} disabled={busy}
            style={{ width: '100%', padding: 14, borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: busy ? 0.6 : 1 }}>
            <Upload size={16} /> {busy ? 'Sending…' : 'Submit claim'}
          </button>
        </div>
      </main>

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
