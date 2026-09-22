'use client'
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { loginStart, loginVerify, loginChange, forgotStart, forgotReset, setAppToken, AppApiError } from '../../api'
import { C, h, serif, headerPad, card } from '../../ui'

/** Sign-in mirrors the desktop email login exactly (core/staff_login_views.py):
 *  email + password → 6-digit code by email → in (first time: choose a password).
 *  "Forgot password" = emailed code → set a new password → in. Both end in a
 *  30-day device session. The first build sent only the email and the server
 *  rightly refused it — Kago Tshutlhedi reported the missing password box, 3-Sep-2026. */
type Step = 'email' | 'code' | 'change' | 'forgot' | 'forgot-code'
const MIN_PW = 8   // server MIN_PW_LEN

export default function AppLogin() {
  const router = useRouter()
  const [step, setStep] = useState<Step>('email')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [ticket, setTicket] = useState('')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null)
    try { await fn() } catch (e) { setErr(e instanceof AppApiError ? e.message : 'Something went wrong. Try again.') }
    finally { setBusy(false) }
  }
  const finish = (token: string) => { setAppToken(token); router.replace('/app') }
  const mail = () => email.trim().toLowerCase()
  const go = (s: Step) => { setErr(null); setCode(''); setStep(s) }

  return (
    <main style={{ minHeight: '100dvh', background: C.navy, color: '#fff' }}>
      <header style={{ padding: headerPad, paddingTop: `calc(40px + env(safe-area-inset-top, 0px))` }}>
        <div style={{ fontFamily: serif, fontSize: 30, fontWeight: 700, letterSpacing: '-0.01em', lineHeight: 1 }}>Alpha Omni</div>
        <div style={{ opacity: 0.75, fontSize: 14, marginTop: 6 }}>Alpha Direct staff</div>
      </header>
      <section className="oa-rise" style={{ ...card, margin: '8px 16px', padding: 20, color: C.ink }}>
        {step === 'email' && (
          <form onSubmit={e => { e.preventDefault(); run(async () => { await loginStart(mail(), password); setStep('code') }) }}>
            <h1 style={h(22)}>Sign in</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>Work email and your Omni password. We then send a 6-digit code to your email.</p>
            <input type="email" required autoFocus inputMode="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="name@alphadirect.co.bw" style={input} aria-label="Work email" />
            <input type="password" required autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)}
              placeholder="Omni password" style={input} aria-label="Omni password" />
            <button disabled={busy} className="oa-press" style={primary}>{busy ? 'Checking…' : 'Send code'}</button>
            <button type="button" onClick={() => go('forgot')} className="oa-press" style={ghost}>Forgot password, or never set one?</button>
          </form>
        )}
        {step === 'code' && (
          <form onSubmit={e => { e.preventDefault(); run(async () => {
              const r = await loginVerify(mail(), code.trim())
              if (r.change_required && r.ticket) { setTicket(r.ticket); setStep('change'); return }
              if (r.token) finish(r.token)
            }) }}>
            <h1 style={h(22)}>Enter the code</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>Sent to {email}. It lasts 10 minutes.</p>
            <input required autoFocus inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"
              value={code} onChange={e => setCode(e.target.value)} placeholder="123456" style={{ ...input, letterSpacing: '0.3em', fontSize: 22 }} aria-label="6-digit code" />
            <button disabled={busy} className="oa-press" style={primary}>{busy ? 'Checking…' : 'Sign in for 30 days'}</button>
            <button type="button" onClick={() => go('email')} className="oa-press" style={ghost}>Start again</button>
          </form>
        )}
        {step === 'change' && (
          <form onSubmit={e => { e.preventDefault(); run(async () => { const r = await loginChange(mail(), ticket, pw); finish(r.token) }) }}>
            <h1 style={h(22)}>Choose your own password</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>First sign-in: replace the default password (at least {MIN_PW} characters). You will not be asked again on this phone.</p>
            <input type="password" required autoFocus minLength={MIN_PW} autoComplete="new-password" value={pw} onChange={e => setPw(e.target.value)} style={input} aria-label="New password" />
            <button disabled={busy} className="oa-press" style={primary}>{busy ? 'Saving…' : 'Save and sign in'}</button>
          </form>
        )}
        {step === 'forgot' && (
          <form onSubmit={e => { e.preventDefault(); run(async () => { await forgotStart(mail()); setStep('forgot-code') }) }}>
            <h1 style={h(22)}>Reset your password</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>Use this if you sign in to Omni with Microsoft and have never set an Omni password. We email you a code, then you choose a password.</p>
            <input type="email" required autoFocus inputMode="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="name@alphadirect.co.bw" style={input} aria-label="Work email" />
            <button disabled={busy} className="oa-press" style={primary}>{busy ? 'Sending…' : 'Email me a code'}</button>
            <button type="button" onClick={() => go('email')} className="oa-press" style={ghost}>Back to sign in</button>
          </form>
        )}
        {step === 'forgot-code' && (
          <form onSubmit={e => { e.preventDefault(); run(async () => { const r = await forgotReset(mail(), code.trim(), pw); finish(r.token) }) }}>
            <h1 style={h(22)}>Code and new password</h1>
            <p style={{ color: C.inkSoft, fontSize: 14 }}>Code sent to {email} (10 minutes). Choose a password of at least {MIN_PW} characters.</p>
            <input required autoFocus inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"
              value={code} onChange={e => setCode(e.target.value)} placeholder="123456" style={{ ...input, letterSpacing: '0.3em', fontSize: 22 }} aria-label="6-digit code" />
            <input type="password" required minLength={MIN_PW} autoComplete="new-password" value={pw} onChange={e => setPw(e.target.value)}
              placeholder="New password" style={input} aria-label="New password" />
            <button disabled={busy} className="oa-press" style={primary}>{busy ? 'Saving…' : 'Set password and sign in'}</button>
            <button type="button" onClick={() => go('forgot')} className="oa-press" style={ghost}>Send a new code</button>
          </form>
        )}
        {err && <p role="alert" style={{ color: C.red, fontSize: 14, marginTop: 12 }}>{err}</p>}
      </section>
      <p style={{ margin: '16px 20px', fontSize: 12, opacity: 0.7 }}>Signing in on this phone keeps you in for 30 days. You can switch a phone off any time under Me → Signed-in devices.</p>
    </main>
  )
}

const input: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '14px 14px', fontSize: 17, borderRadius: 12,
  border: `1px solid ${C.line}`, margin: '12px 0', background: C.surface }
const primary: React.CSSProperties = { width: '100%', minHeight: 48, borderRadius: 12, border: 0, background: C.orange, color: C.navy,
  fontWeight: 700, fontSize: 16 }
const ghost: React.CSSProperties = { width: '100%', minHeight: 44, marginTop: 8, borderRadius: 12, border: 0, background: 'transparent',
  color: C.inkSoft, fontSize: 14 }
