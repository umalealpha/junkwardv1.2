'use client'

/** /m/staff/bug — report a system bug from the phone. Same channel + rules as
 * desktop /report-bug: at least 50 words + 3 screenshots, straight to EXCO. */
import { useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Bug, Camera, Upload, X } from 'lucide-react'
import { sfetch, compressImage } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

const MIN_WORDS = 50
const MIN_SHOTS = 2   // matches the backend minimum (bug_report_views)

export default function StaffBug() {
  const base = useStaffBase()
  const [description, setDescription] = useState('')
  const [shots, setShots] = useState<File[]>([])
  const [previews, setPreviews] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const words = description.trim() ? description.trim().split(/\s+/).length : 0

  const onShot = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = Array.from(e.target.files || [])
    e.target.value = ''
    if (!raw.length) return
    const fs = await Promise.all(raw.map(f => compressImage(f)))   // shrink before upload
    setShots(p => [...p, ...fs])
    setPreviews(p => [...p, ...fs.map(f => URL.createObjectURL(f))])
  }
  const removeShot = (i: number) => {
    URL.revokeObjectURL(previews[i])
    setShots(p => p.filter((_, j) => j !== i))
    setPreviews(p => p.filter((_, j) => j !== i))
  }

  async function submit() {
    if (words < MIN_WORDS) { show(`Describe it in at least ${MIN_WORDS} words (${words}/${MIN_WORDS}).`); return }
    if (shots.length < MIN_SHOTS) { show(`Attach at least ${MIN_SHOTS} photos/screenshots (${shots.length}/${MIN_SHOTS}).`); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('description', description.trim())
      fd.append('page_url', 'Alpha Nexus app (mobile)')
      shots.forEach(f => fd.append('screenshots', f))
      await sfetch('/bug-reports/', { method: 'POST', body: fd }, false)
      setDone(true)
    } catch (e) { show(e instanceof Error ? e.message : 'Could not send.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Report a bug</h1>
      </header>

      <main style={{ padding: 16 }}>
        {done ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <p style={{ fontSize: 52, margin: '16px 0 8px' }}>🐛✅</p>
            <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 24, color: C.ink, margin: 0 }}>Bug reported</h2>
            <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.6, margin: '10px 0 24px' }}>
              It went straight to the board&apos;s inbox. You&apos;ll get an email when it&apos;s fixed.
            </p>
            <Link href={base} style={{ display: 'inline-block', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, textDecoration: 'none', padding: '13px 28px', borderRadius: 999 }}>Done</Link>
          </div>
        ) : (
          <div style={{ ...card, padding: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, color: C.ink, fontWeight: 700, fontSize: 14 }}>
              <Bug size={17} style={{ color: C.orange }} /> What went wrong?
            </div>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={5} aria-label="What went wrong?"
              placeholder={`Tell the story in at least ${MIN_WORDS} words — what you did, what you expected, what happened instead.`}
              style={{ width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans, resize: 'vertical' }} />
            <p style={{ fontSize: 13, fontWeight: 700, color: words >= MIN_WORDS ? '#047857' : C.inkSoft, margin: '6px 0 12px' }}>{words}/{MIN_WORDS} words</p>

            <input ref={camRef} type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={onShot} />
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              {previews.map((u, i) => (
                <div key={u} style={{ position: 'relative' }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={u} alt={`shot ${i + 1}`} style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 12, border: `1px solid ${C.line}` }} />
                  <button onClick={() => removeShot(i)} aria-label="Remove"
                    style={{ position: 'absolute', top: -6, right: -6, width: 20, height: 20, borderRadius: 999, border: 'none', background: C.navy, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer' }}>
                    <X size={11} />
                  </button>
                </div>))}
              <button onClick={() => camRef.current?.click()}
                style={{ width: 72, height: 72, borderRadius: 12, border: `2px dashed ${C.orange}`, background: '#FFF7ED', color: '#92400E', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 3, cursor: 'pointer', fontFamily: sans, fontSize: 10, fontWeight: 700 }}>
                <Camera size={18} /> {shots.length}/{MIN_SHOTS}+
              </button>
            </div>

            <button onClick={submit} disabled={busy}
              style={{ width: '100%', padding: 14, borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: busy ? 0.6 : 1 }}>
              <Upload size={16} /> {busy ? 'Sending…' : 'Send bug report'}
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
