'use client'

/** /m/feedback — in-app feedback / short survey. Lets a member rate the app,
 * pick the area, and say what they'd want next. Signal to shape Nexus around
 * what members value (Medu 2026-07). No points — pure product feedback. */
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Star } from 'lucide-react'
import { postFeedback } from '../../api'
import { C, serif, sans, h, card, headerPad } from '../../ui'

const AREAS = [
  { v: 'overall', label: 'Overall app' },
  { v: 'drive', label: 'Nexus Drive' },
  { v: 'wellness', label: 'Wellness' },
  { v: 'rewards', label: 'Rewards' },
  { v: 'other', label: 'Something else' },
]

export default function CustomerFeedback() {
  const router = useRouter()
  const [rating, setRating] = useState(0)
  const [area, setArea] = useState('overall')
  const [comment, setComment] = useState('')
  const [wants, setWants] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const redirectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (redirectTimer.current) clearTimeout(redirectTimer.current) }, [])

  const submit = async () => {
    if (rating < 1) { setError('Tap a star to rate first.'); return }
    setBusy(true); setError(null)
    try {
      await postFeedback({ rating, area, comment: comment.trim(), wants: wants.trim() })
      setDone(true)
      redirectTimer.current = setTimeout(() => router.replace('/m'), 1800)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not send — please try again.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ textAlign: 'center', padding: headerPad, background: C.card, borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 21, color: C.ink }}>Alpha Nexus</span>
      </header>
      <main style={{ padding: 16, maxWidth: 480, margin: '0 auto' }}>
        {done ? (
          <div style={{ ...card, padding: 28, textAlign: 'center' }}>
            <div style={{ fontSize: 40, marginBottom: 8 }}>🙏</div>
            <h1 style={{ ...h(24), marginBottom: 8 }}>Thank you</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>Your feedback helps us build what you actually want.</p>
          </div>
        ) : (
          <div style={{ ...card, padding: 22 }}>
            <h1 style={{ ...h(26), marginBottom: 6 }}>Tell us what you think</h1>
            <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.5, margin: '0 0 18px' }}>
              We&apos;re building Nexus around what you value. 30 seconds — no wrong answers.
            </p>

            <span id="rate-lbl" style={lbl}>How is the app so far?</span>
            <div role="radiogroup" aria-labelledby="rate-lbl" style={{ display: 'flex', gap: 8, margin: '4px 0 18px' }}>
              {[1, 2, 3, 4, 5].map(n => (
                <button key={n} role="radio" aria-checked={n === rating} onClick={() => { setRating(n); if (error) setError(null) }}
                  aria-label={`${n} star${n > 1 ? 's' : ''}`}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2 }}>
                  <Star size={34} fill={n <= rating ? C.orange : 'none'} color={n <= rating ? C.orange : '#C4C8CE'} />
                </button>
              ))}
            </div>

            <span id="area-lbl" style={lbl}>Which part?</span>
            <div role="radiogroup" aria-labelledby="area-lbl" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '4px 0 18px' }}>
              {AREAS.map(a => (
                <button key={a.v} role="radio" aria-checked={area === a.v} onClick={() => setArea(a.v)}
                  style={{ padding: '8px 14px', borderRadius: 999, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                    border: `1px solid ${area === a.v ? C.teal : C.line}`,
                    background: area === a.v ? 'rgba(14,148,136,0.10)' : '#fff',
                    color: area === a.v ? C.teal : C.ink }}>{a.label}</button>
              ))}
            </div>

            <span style={lbl}>Anything you&apos;d like to tell us? (optional)</span>
            <textarea value={comment} onChange={e => setComment(e.target.value.slice(0, 2000))} rows={3}
              aria-label="Anything you'd like to tell us" placeholder="What you like, what's annoying…" style={ta} />

            <span style={{ ...lbl, marginTop: 14 }}>What would you love Nexus to do next? (optional)</span>
            <textarea value={wants} onChange={e => setWants(e.target.value.slice(0, 2000))} rows={3}
              aria-label="What would you love Nexus to do next" placeholder="A feature, a reward, anything…" style={ta} />

            {error && <p style={{ color: '#B91C1C', fontSize: 13, margin: '12px 0 0' }}>{error}</p>}
            <button onClick={submit} disabled={busy} style={btn}>{busy ? 'Sending…' : 'Send feedback'}</button>
          </div>
        )}
      </main>
    </div>
  )
}

const lbl: React.CSSProperties = { display: 'block', fontSize: 13, fontWeight: 700, color: C.ink }
const ta: React.CSSProperties = { width: '100%', boxSizing: 'border-box', marginTop: 6, padding: '12px 14px', border: `1px solid ${C.line}`, background: '#F8F9FB', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', resize: 'vertical', fontFamily: sans }
const btn: React.CSSProperties = { width: '100%', marginTop: 18, padding: '15px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff', fontWeight: 700, fontSize: 15, cursor: 'pointer', fontFamily: sans }
