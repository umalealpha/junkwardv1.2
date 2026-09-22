'use client'

/** /m/staff/rewards — YOUR Staff Rewards (separate from customer Nexus
 * points): pillar scores, plus quick steps + meal-photo submissions. */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Apple, Footprints, Trophy } from 'lucide-react'
import { sfetch, compressImage } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Pillar { key: string; label: string; total: number; tier: string; progress: { pct: number } | null }
interface Dash { pillars: Pillar[]; overallPoints: number; overallTier: string }
const inputStyle: React.CSSProperties = { flex: 1, boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans }

export default function StaffRewards() {
  const base = useStaffBase()
  const [dash, setDash] = useState<Dash | null>(null)
  const [err, setErr] = useState('')
  const [steps, setSteps] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    sfetch<Dash>('/staff-rewards/dashboard/').then(setDash)
      .catch(e => setErr(e instanceof Error ? e.message : 'Could not load.'))
  }, [])
  useEffect(() => { load() }, [load])

  async function submitSteps() {
    const n = parseInt(steps.replace(/\D/g, ''), 10) || 0
    if (n <= 0) { show('Enter your step count first.'); return }
    setBusy('steps')
    try {
      await sfetch('/staff-rewards/submit/', { method: 'POST', body: JSON.stringify({ feature_code: 'steps', payload: { steps: n, consent: true } }) })
      show('Steps in — points follow approval. 👟'); setSteps(''); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not submit.') }
    finally { setBusy(null) }
  }

  const onMeal = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]; e.target.value = ''
    if (!raw) return
    setBusy('meal')
    const f = await compressImage(raw)   // shrink camera output before base64 upload
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        await sfetch('/staff-rewards/score-meal/', { method: 'POST', body: JSON.stringify({ imageData: String(reader.result), consent: true }) })
        show('Meal scored — the photo is discarded, only the score is kept. 🍎'); load()
      } catch (err2) { show(err2 instanceof Error ? err2.message : 'Could not score that meal.') }
      finally { setBusy(null) }
    }
    reader.onerror = () => { setBusy(null); show('Could not read that photo.') }
    reader.readAsDataURL(f)
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Staff Rewards</h1>
      </header>

      <main style={{ padding: 16 }}>
        {err && <p style={{ color: C.inkSoft, fontSize: 13, textAlign: 'center' }}>{err}</p>}
        {dash && (
          <>
            <div style={{ background: `linear-gradient(160deg, ${C.navy2}, ${C.navy})`, borderRadius: 20, padding: 20, color: '#fff', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 16 }}>
              <Trophy size={34} style={{ color: C.orange }} />
              <div>
                <p style={{ margin: 0, fontSize: 11, letterSpacing: '0.12em', color: 'rgba(255,255,255,0.6)' }}>MY STAFF POINTS</p>
                <p style={{ fontFamily: serif, fontWeight: 800, fontSize: 32, margin: '2px 0 0' }}>{dash.overallPoints.toLocaleString()} <span style={{ fontSize: 14, color: C.orange }}>· {dash.overallTier}</span></p>
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 18 }}>
              {dash.pillars.map(p => (
                <div key={p.key} style={{ ...card, padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13.5 }}>
                    <b style={{ color: C.ink }}>{p.label}</b>
                    <span style={{ color: C.inkSoft, fontVariantNumeric: 'tabular-nums' }}>{p.total} pts · {p.tier}</span>
                  </div>
                  <div style={{ height: 6, borderRadius: 999, background: '#EEF0F3', marginTop: 8, overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${Math.min(100, Math.max(0, Math.round(p.progress?.pct || 0)))}%`, background: `linear-gradient(90deg, ${C.orange}, ${C.orangeDeep})` }} />
                  </div>
                </div>))}
            </div>
          </>
        )}

        <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 18, color: C.ink, margin: '0 0 10px' }}>Earn now</h2>
        <div style={{ ...card, padding: 16, marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, color: C.ink, fontWeight: 700, fontSize: 14 }}>
            <Footprints size={17} style={{ color: C.orange }} /> Today&apos;s steps
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <input value={steps} onChange={e => setSteps(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" placeholder="e.g. 8000" aria-label="Today's steps" style={inputStyle} />
            <button onClick={submitSteps} disabled={busy === 'steps'}
              style={{ padding: '0 22px', borderRadius: 12, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Log</button>
          </div>
        </div>
        <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onMeal} />
        <button onClick={() => camRef.current?.click()} disabled={busy === 'meal'}
          style={{ ...card, width: '100%', padding: 16, display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', color: C.ink, fontWeight: 700, fontSize: 14, fontFamily: sans, border: 'none', textAlign: 'left' }}>
          <Apple size={18} style={{ color: C.orange }} /> {busy === 'meal' ? 'Scoring…' : 'Snap a healthy meal (photo is scored, then discarded)'}
        </button>
      </main>

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
