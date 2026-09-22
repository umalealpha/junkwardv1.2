'use client'
/**
 * /vendor-sign/[token] — PUBLIC remote e-sign page (NO login).
 * The doctor opens the tokenised link, reviews the agreement the Alpha Direct
 * Healthcare team prepared, gives DPA consent, signs (draw OR upload), submits.
 * Calls the token-gated public endpoints:
 *   GET  /api/v1/health/vendor-sign/<token>/         — read-only review data
 *   POST /api/v1/health/vendor-sign/<token>/submit/  — consent + signature
 */
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const API = (process.env.NEXT_PUBLIC_API_BASE ?? '') + '/api/v1'

type Vendor = Record<string, any>

export default function VendorSignPage() {
  const params = useParams<{ token: string }>()
  const token = params?.token as string
  const [phase, setPhase] = useState<'loading' | 'form' | 'invalid' | 'expired' | 'done' | 'error'>('loading')
  const [msg, setMsg] = useState('')
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const [consent, setConsent] = useState(false)
  const [name, setName] = useState('')
  const [sig, setSig] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!token) return
    ;(async () => {
      try {
        const r = await fetch(`${API}/health/vendor-sign/${token}/`)
        if (r.status === 410) { const j = await r.json().catch(() => ({})); setMsg(j.detail || 'This link has expired.'); setPhase('expired'); return }
        if (r.status === 404) { setPhase('invalid'); return }
        if (!r.ok) { setPhase('error'); setMsg('Could not load the agreement.'); return }
        const j = await r.json()
        setVendor(j.vendor || {})
        setName(j.vendor?.practitioner_name || '')
        setPhase('form')
      } catch { setPhase('error'); setMsg('Network error loading the agreement.') }
    })()
  }, [token])

  // Bring the error + submit button into view whenever a validation message
  // appears — on mobile the button sits below the fold (CX review 2026-06-05).
  useEffect(() => { if (msg) document.getElementById('sign-submit')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }, [msg])

  async function submit() {
    if (!consent) { setMsg('Please tick the consent box to continue.'); return }
    if (!sig) { setMsg('Please sign — draw your signature or upload an image.'); return }
    setSubmitting(true); setMsg('')
    try {
      const r = await fetch(`${API}/health/vendor-sign/${token}/submit/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ consent: true, signatory_full_name: name, signature_data_url: sig }),
      })
      if (r.status === 410) { setPhase('expired'); setMsg('This link has expired or was already used.'); return }
      if (!r.ok) { const j = await r.json().catch(() => ({})); setMsg(j.detail || 'Could not submit. Please try again.'); setSubmitting(false); return }
      setPhase('done')
    } catch { setMsg('Network error. Please try again.'); setSubmitting(false) }
  }

  const Shell = ({ children }: { children: React.ReactNode }) => (
    <div style={{ minHeight: '100vh', background: '#F4F6F8', fontFamily: 'Georgia, "Book Antiqua", serif', color: NAVY }}>
      <div style={{ background: NAVY, color: '#fff', padding: '16px 20px', fontSize: 20, fontWeight: 700 }}>
        Alpha Direct Healthcare — Vendor Agreement
      </div>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '20px 16px' }}>{children}</div>
    </div>
  )

  if (phase === 'loading') return <Shell><p>Loading your agreement…</p></Shell>
  if (phase === 'invalid') return <Shell><Card title="Invalid link">This signing link is not valid. Please ask Alpha Direct Healthcare to resend it.</Card></Shell>
  if (phase === 'expired') return <Shell><Card title="Link expired">{msg || 'This signing link has expired or was already used.'} Please ask for a fresh link.</Card></Shell>
  if (phase === 'error') return <Shell><Card title="Something went wrong">{msg}</Card></Shell>
  if (phase === 'done') return (
    <Shell><Card title="Thank you — signed ✓">
      Your agreement has been signed and submitted. A copy of the fully executed
      agreement will be emailed to you. You can close this page.
    </Card></Shell>
  )

  const v = vendor || {}
  const row = (k: string, val: any) => val ? (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #eee', fontSize: 14 }}>
      <span style={{ color: '#667' }}>{k}</span><span style={{ fontWeight: 600, textAlign: 'right' }}>{val}</span>
    </div>
  ) : null

  return (
    <Shell>
      <p style={{ fontSize: 14, color: '#445' }}>Your details have been prepared for you by the Alpha Direct Healthcare team — please review, then sign below. You do not need to fill in any forms.</p>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, margin: '14px 0', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
        <h3 style={{ margin: '0 0 8px', color: NAVY }}>Your details</h3>
        {row('Practice / trading name', v.trading_name || v.company_name)}
        {row('Practitioner', v.practitioner_name)}
        {row('Discipline', v.discipline)}
        {row('Council', [v.council_type, v.council_registration_number].filter(Boolean).join(' · '))}
        {row('Service category', v.service_category)}
        {row('Principal place of business', v.principal_place_of_business)}
        {row('Bank account', v.masked_account)}
        {row('Reference', v.reference_number)}
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, margin: '14px 0', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
        <h3 style={{ margin: '0 0 8px', color: NAVY }}>AFA Service Provider Network Agreement</h3>
        <p style={{ fontSize: 13, color: '#445', lineHeight: 1.5 }}>
          This is the AFA Service Provider Network Agreement between Associated Fund
          Administrators Botswana and you (the Practitioner). Funder: Alpha Direct
          Insurance Company. By signing you agree to be bound by its terms. <b>The
          complete, fully executed agreement (all clauses) will be emailed to you on
          signing.</b>
        </p>
        <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: 10, fontSize: 13 }}>
          <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} style={{ marginTop: 3 }} />
          <span>I consent, under the Botswana Data Protection Act, to Alpha Direct processing my information for vendor onboarding, payment setup and regulatory record-keeping; and I confirm the details above are correct and I am authorised to sign.</span>
        </label>
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, margin: '14px 0', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
        <h3 style={{ margin: '0 0 8px', color: NAVY }}>Sign</h3>
        <label htmlFor="sig-name" style={{ fontSize: 13, color: '#556' }}>Full name of signatory <span style={{ color: '#889' }}>— pre-filled, edit only if incorrect</span></label>
        <input id="sig-name" value={name} onChange={e => setName(e.target.value)}
               style={{ width: '100%', padding: 12, border: '1px solid #ccd', borderRadius: 8, margin: '4px 0 12px', fontSize: 16 }} />
        <SignaturePad value={sig} onChange={setSig} />
      </div>

      {msg && <p id="sign-msg" style={{ color: '#b3261e', fontSize: 15, fontWeight: 600 }}>{msg}</p>}
      <button id="sign-submit" onClick={submit} disabled={submitting}
              style={{ width: '100%', background: submitting ? '#ccc' : ORANGE, color: NAVY, border: 'none', borderRadius: 10, padding: '16px', fontSize: 17, fontWeight: 700, cursor: submitting ? 'default' : 'pointer' }}>
        {submitting ? 'Submitting…' : 'Sign & submit'}
      </button>
    </Shell>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#fff', borderRadius: 12, padding: 24, marginTop: 24, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
      <h2 style={{ margin: '0 0 10px', color: '#0D1B2A' }}>{title}</h2>
      <p style={{ fontSize: 15, color: '#445', lineHeight: 1.6 }}>{children}</p>
      <p style={{ fontSize: 13, color: '#778', marginTop: 14 }}>Questions? Contact Alpha Direct Healthcare — health@alphadirect.co.bw · +267 370 2744.</p>
    </div>
  )
}

/** Draw-OR-upload signature (Alana #4: upload a saved signature as an alternative). */
function SignaturePad({ value, onChange }: { value: string; onChange: (d: string) => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const drawing = useRef(false)
  function pos(e: React.PointerEvent) {
    const c = ref.current!; const r = c.getBoundingClientRect()
    return { x: (e.clientX - r.left) * (c.width / r.width), y: (e.clientY - r.top) * (c.height / r.height) }
  }
  function down(e: React.PointerEvent) { drawing.current = true; const ctx = ref.current!.getContext('2d')!; const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y) }
  function move(e: React.PointerEvent) {
    if (!drawing.current) return
    const ctx = ref.current!.getContext('2d')!; const p = pos(e)
    ctx.lineWidth = 2; ctx.lineCap = 'round'; ctx.strokeStyle = '#0D1B2A'; ctx.lineTo(p.x, p.y); ctx.stroke()
  }
  function up() { if (drawing.current) { drawing.current = false; onChange(ref.current!.toDataURL('image/png')) } }
  function clear() { const c = ref.current!; c.getContext('2d')!.clearRect(0, 0, c.width, c.height); onChange('') }
  function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]; if (!f) return
    const fr = new FileReader(); fr.onload = () => onChange(String(fr.result)); fr.readAsDataURL(f)
  }
  return (
    <div>
      <canvas ref={ref} width={640} height={180} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
              style={{ width: '100%', height: 160, touchAction: 'none', borderRadius: 8, border: '1px solid #ccd', background: '#fff' }} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, fontSize: 14, flexWrap: 'wrap' }}>
        <button onClick={clear} type="button" style={{ background: 'transparent', border: '1px solid #ccd', borderRadius: 8, padding: '8px 16px', cursor: 'pointer', fontSize: 14 }}>Clear</button>
        <span style={{ color: '#889' }}>or</span>
        <label style={{ cursor: 'pointer', color: '#fff', background: NAVY, borderRadius: 8, padding: '8px 16px', fontSize: 14 }}>
          Upload a saved signature
          <input type="file" accept="image/png,image/jpeg" onChange={upload} style={{ display: 'none' }} />
        </label>
      </div>
      {value && (
        <div style={{ marginTop: 10 }}>
          <span style={{ color: '#1a7f37', fontSize: 13 }}>✓ This is what you will sign with:</span>
          <img src={value} alt="your signature" style={{ display: 'block', maxHeight: 80, maxWidth: '100%', marginTop: 4, border: '1px solid #eee', borderRadius: 6, background: '#fff' }} />
        </div>
      )}
    </div>
  )
}
