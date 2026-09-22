'use client'

/** /m/login — Alpha Nexus email sign-in (Stitch design). Enter email → code.
 * Unknown emails self-register (test app), so any Gmail joins easily. */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowRight } from 'lucide-react'
import { requestOtp, verifyOtp, setCustToken } from '../../api'
import { C, serif, sans, h, headerPad, isInAppShell } from '../../ui'

export default function CustomerLogin() {
  const router = useRouter()
  const [step, setStep] = useState<'request' | 'verify'>('request')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [referral, setReferral] = useState('')
  const [showReferral, setShowReferral] = useState(false)
  // "Get the app" install links must NEVER show inside the installed app —
  // Apple rejected 1.0.0 (Guideline 2.3.10) for the Android button appearing
  // in the iOS app. Detected post-mount to stay hydration-safe.
  const [showGetApp, setShowGetApp] = useState(false)
  useEffect(() => { setShowGetApp(!isInAppShell()) }, [])

  const send = async () => {
    if (!/.+@.+\..+/.test(email.trim())) { setError('Enter a valid email address.'); return }
    setBusy(true); setError(null)
    try {
      const r = await requestOtp(email.trim(), referral.trim() || undefined)
      setNote(r.message || 'Check your email for a 6-digit code.'); setStep('verify')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not send the code.') }
    finally { setBusy(false) }
  }
  const verify = async () => {
    if (code.trim().length < 4) { setError('Enter the code from your email.'); return }
    setBusy(true); setError(null)
    try {
      const r = await verifyOtp(email.trim(), code.trim()); setCustToken(r.token); router.replace('/m')
    } catch (e) { setError(e instanceof Error ? e.message : 'That code did not work.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ minHeight: '100vh', background: `radial-gradient(120% 80% at 80% 0%, #EAF6F4 0%, ${C.surface} 45%)`, fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: headerPad }}>
        <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 22, color: C.ink }}>Alpha Nexus</span>
      </header>

      <main style={{ padding: '24px 20px' }}>
        <div style={{ background: C.card, borderRadius: 26, padding: 28, boxShadow: '0 20px 50px rgba(16,27,42,0.10)', border: `1px solid ${C.line}` }}>
          {step === 'request' ? (
            <>
              <h1 style={h(32)}>Sign in or register</h1>
              <p style={{ color: C.inkSoft, fontSize: 15, lineHeight: 1.5, margin: '10px 0 22px' }}>
                Enter your email to join the nexus of rewards and wellness.
              </p>
              <label style={lbl}>Email address</label>
              <input value={email} onChange={e => setEmail(e.target.value)} type="email" inputMode="email"
                placeholder="name@gmail.com" autoComplete="email" style={inp} />
              {/* Optional, and kept behind a link so it never clutters the one
                  thing a new member has to do. A wrong code is ignored. */}
              {showReferral ? (
                <>
                  <label style={lbl}>Referral code (optional)</label>
                  <input value={referral} onChange={e => setReferral(e.target.value.toUpperCase().trim())}
                    maxLength={12} placeholder="From the friend who invited you"
                    aria-label="Referral code" style={{ ...inp, letterSpacing: '0.14em' }} />
                </>
              ) : (
                <button onClick={() => setShowReferral(true)} type="button"
                  style={{ background: 'none', border: 'none', padding: 0, marginBottom: 14, color: C.teal, fontWeight: 600, fontSize: 13, cursor: 'pointer', fontFamily: sans, textAlign: 'left' }}>
                  Someone invited me — I have a code
                </button>
              )}
              <button onClick={send} disabled={busy} style={btn}>
                {busy ? 'Sending…' : <>Email me a code <ArrowRight size={18} /></>}
              </button>
            </>
          ) : (
            <>
              <h1 style={h(30)}>Enter your code</h1>
              {note && <p style={{ color: C.inkSoft, fontSize: 14, margin: '10px 0 18px' }}>{note}</p>}
              <label style={lbl}>6-digit code</label>
              <input value={code} onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                inputMode="numeric" placeholder="••••••" style={{ ...inp, letterSpacing: 10, fontSize: 24, textAlign: 'center' }} />
              <button onClick={verify} disabled={busy} style={btn}>{busy ? 'Checking…' : 'Sign in'}</button>
              <button onClick={() => { setStep('request'); setCode(''); setError(null) }}
                style={{ ...btn, background: 'transparent', color: C.ink, boxShadow: 'none', marginTop: 6 }}>Use a different email</button>
            </>
          )}
          {error && <p style={{ color: '#C0392B', fontSize: 14, marginTop: 14 }}>{error}</p>}
        </div>
        {showGetApp && (
          <div style={{ textAlign: 'center', marginTop: 24 }}>
            <p style={{ color: C.inkSoft, fontSize: 13, margin: '0 0 10px' }}>📲 Get the app for the best experience</p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
              <a href="/m/get/android" style={appLink}>Android</a>
              <a href="/m/get/ios" style={appLink}>iPhone</a>
            </div>
          </div>
        )}
        <p style={{ textAlign: 'center', color: C.inkSoft, fontSize: 12, marginTop: 22, opacity: 0.8 }}>
          By continuing you agree to the Alpha Nexus <a href="/m/privacy" style={{ color: C.inkSoft, textDecoration: 'underline' }}>Privacy Policy</a>.
        </p>
      </main>
    </div>
  )
}

const lbl: React.CSSProperties = { display: 'block', fontSize: 13, fontWeight: 700, color: C.ink, marginBottom: 8 }
const inp: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '15px 16px', border: 'none', background: '#F0F2F5', borderRadius: 14, fontSize: 16, color: C.ink, outline: 'none' }
const appLink: React.CSSProperties = {
  display: 'inline-block', padding: '10px 22px', borderRadius: 999, textDecoration: 'none',
  border: `1px solid ${C.line}`, background: C.card, color: C.ink, fontWeight: 600, fontSize: 14,
}
const btn: React.CSSProperties = {
  width: '100%', marginTop: 18, padding: '16px', borderRadius: 999, border: 'none',
  background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: '#fff',
  fontWeight: 700, fontSize: 16, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8,
  boxShadow: '0 12px 26px rgba(226,112,11,0.35)', fontFamily: sans,
}
