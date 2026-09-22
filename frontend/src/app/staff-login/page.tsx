'use client'

/**
 * /staff-login — Alpha Direct staff sign-in.
 *
 * CFO directive 2026-07-07: staff (and the CFO) kept getting stuck on the
 * Microsoft "we couldn't sign you in" loop. This is a self-service way in for
 * every active Alpha Direct M365 user:
 *   email + password  ->  a 6-digit code is emailed  ->  enter code  ->  in.
 * Everyone starts on the default password (Omni123) and is forced to set a
 * new one on first sign-in. The emailed code is the second factor, so a known
 * default password alone cannot get anyone in.
 *
 * 2026-08-17: re-skinned to the "Omni Access Portal" two-panel design. The
 * creds -> code -> success flow maps to the design's step1 -> step2 -> step3.
 * Every fetch / token / redirect below is unchanged; the Microsoft button
 * reuses the exact MSAL handler from the landing page. This is the primary,
 * full sign-in surface.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { setToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import { clearSignedOut } from '@/auth/signedOut'
import {
  getMsalInstance,
  isSSOConfigured,
  loginScopes,
  clearLocalMsalState,
} from '@/auth/msal'
import AccessPortalShell, { OAP, MicrosoftMark } from '@/app/_components/AccessPortalShell'

const API = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1/auth/staff`

type Step = 'creds' | 'code' | 'change' | 'forgot' | 'reset' | 'done'

export default function StaffLoginPage() {
  const router = useRouter()
  const [step, setStep] = useState<Step>('creds')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [code, setCode] = useState('')
  const [ticket, setTicket] = useState('')
  const [pw1, setPw1] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  async function post(path: string, body: Record<string, string>) {
    const r = await fetch(`${API}/${path}/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await r.json().catch(() => ({}))
    if (!r.ok) {
      throw new Error(r.status === 429
        ? 'Too many attempts — wait a minute and try again.'
        : (d.detail || 'Something went wrong. Please try again.'))
    }
    return d
  }

  function finish(token: string) {
    // Signing in with a password is just as explicit as clicking the Microsoft
    // button, so it must lift the sticky signed-out gate too. Without this, a
    // user bounced out to /login?signedout=1 who then takes the recommended
    // password route stays flagged as signed-out, and the next visit to the bare
    // address parks them on the landing page while actually authenticated.
    clearSignedOut()
    setToken(token)
    // Show the "Identity confirmed" step, then hand off to the dashboard.
    setStep('done')
  }

  // Redirect after the success step is shown (design step 3). The token is
  // already set by finish(); this only sequences the hand-off.
  useEffect(() => {
    if (step !== 'done') return
    const t = setTimeout(() => router.replace('/my-omni'), 1100)
    return () => clearTimeout(t)
  }, [step, router])

  async function submitCreds(e: React.FormEvent) {
    e.preventDefault()
    if (!email.trim() || !password) { setErr('Enter your work email and password.'); return }
    setBusy(true); setErr(null); setMsg(null)
    try {
      await post('start', { email: email.trim(), password })
      setStep('code'); setMsg(`We emailed a 6-digit code to ${email.trim()}. It expires in 10 minutes.`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault()
    if (!code.trim()) { setErr('Enter the code from your email.'); return }
    setBusy(true); setErr(null)
    try {
      const d = await post('verify', { email: email.trim(), code: code.trim() })
      if (d.change_required) { setTicket(d.ticket); setStep('change'); setMsg('First sign-in — please set your own password.') }
      else finish(d.token)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function submitChange(e: React.FormEvent) {
    e.preventDefault()
    if (pw1.length < 8) { setErr('New password must be at least 8 characters.'); return }
    if (pw1 !== pw2) { setErr('The two passwords do not match.'); return }
    setBusy(true); setErr(null)
    try {
      const d = await post('change', { email: email.trim(), ticket, new_password: pw1 })
      finish(d.token)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function resend() {
    setBusy(true); setErr(null)
    try {
      // Resend the right code for whichever flow we're in.
      if (step === 'reset') await post('forgot-start', { email: email.trim() })
      else await post('start', { email: email.trim(), password })
      setMsg('A new code is on its way.')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function submitForgot(e: React.FormEvent) {
    e.preventDefault()
    if (!email.trim()) { setErr('Enter your work email.'); return }
    setBusy(true); setErr(null); setMsg(null)
    try {
      await post('forgot-start', { email: email.trim() })
      setCode(''); setPw1(''); setPw2(''); setStep('reset')
      setMsg(`If ${email.trim()} is a staff account, we've emailed a 6-digit reset code. It expires in 10 minutes.`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function submitReset(e: React.FormEvent) {
    e.preventDefault()
    if (!code.trim()) { setErr('Enter the code from your email.'); return }
    if (pw1.length < 8) { setErr('New password must be at least 8 characters.'); return }
    if (pw1 !== pw2) { setErr('The two passwords do not match.'); return }
    setBusy(true); setErr(null)
    try {
      const d = await post('forgot-reset', { email: email.trim(), code: code.trim(), new_password: pw1 })
      finish(d.token)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  // Microsoft SSO — copied verbatim from the landing page (frontend/src/app/page.tsx).
  // Every MSAL call and the token/redirect behaviour is identical; do not edit.
  const handleSignIn = async () => {
    // The user asking to sign in is the only thing that lifts the gate.
    clearSignedOut()
    if (!isSSOConfigured()) {
      // eslint-disable-next-line no-console
      console.error('MSAL: Azure SSO is not configured in this build.')
      return
    }
    try {
      const msal = getMsalInstance()
      // MSAL v4 REQUIRES initialize() before any other call; idempotent.
      try { await msal.initialize() } catch { /* already initialized */ }
      // Drain any pending redirect interaction. Without this, a stuck
      // interaction_in_progress flag (left by an interrupted earlier redirect
      // — e.g. the user closed the MS tab, or a prior loop) makes the next
      // loginRedirect throw and the sign-in bounce straight back to this page.
      try { await msal.handleRedirectPromise() } catch { /* no pending */ }

      const account = msal.getActiveAccount() || msal.getAllAccounts()[0]
      if (account) {
        msal.setActiveAccount(account)
        setToken(SSO_SENTINEL_TOKEN)
        router.replace('/my-omni')
        return
      }
      await msal.loginRedirect({ scopes: loginScopes() })
    } catch (err) {
      const e = err as { errorCode?: string; message?: string }
      const stuck =
        e?.errorCode === 'interaction_in_progress' ||
        /interaction_in_progress/i.test(e?.message || '')
      if (stuck) {
        // Wipe the local MSAL cache (clears the stuck interaction.status key)
        // and retry once — self-heals instead of looping.
        try {
          await clearLocalMsalState()
          const msal2 = getMsalInstance()
          try { await msal2.initialize() } catch { /* already initialized */ }
          await msal2.loginRedirect({ scopes: loginScopes() })
          return
        } catch (retryErr) {
          // eslint-disable-next-line no-console
          console.error('MSAL loginRedirect retry failed:', retryErr)
        }
      }
      // eslint-disable-next-line no-console
      console.error('MSAL loginRedirect failed:', err)
    }
  }

  const codeInputStyle: React.CSSProperties = {
    letterSpacing: 8, fontSize: 22, textAlign: 'center', fontWeight: 600,
  }

  const HEAD: Record<Step, { h1: string; sub: string }> = {
    creds:  { h1: 'Sign in to Omni', sub: 'Use your Alpha Direct work email. First time in? Your password is Omni123.' },
    code:   { h1: 'Check your email', sub: 'Enter the 6-digit code we just sent you.' },
    change: { h1: 'Set your password', sub: 'Choose your own password to finish signing in.' },
    forgot: { h1: 'Reset your password', sub: "Enter your work email and we'll send a reset code." },
    reset:  { h1: 'Choose a new password', sub: 'Enter the reset code from your email, then set a new password.' },
    done:   { h1: 'Identity confirmed', sub: 'Taking you into Omni…' },
  }

  return (
    <AccessPortalShell>
      {step !== 'done' && (
        <>
          <h1 className="oap-h1">{HEAD[step].h1}</h1>
          <p className="oap-sub">{HEAD[step].sub}</p>
        </>
      )}

      {step === 'creds' && (
        <form onSubmit={submitCreds} noValidate>
          <label htmlFor="oap-email" className={OAP.label}>Work email</label>
          <input
            id="oap-email" className={OAP.field} value={email} autoFocus
            autoComplete="username" type="email" inputMode="email"
            onChange={e => setEmail(e.target.value)} placeholder="you@alphadirect.co.bw"
          />
          <div style={{ height: 16 }} />
          <label htmlFor="oap-pw" className={OAP.label}>Password</label>
          <div className={OAP.pwWrap}>
            <input
              id="oap-pw" className={OAP.field} type={showPw ? 'text' : 'password'}
              value={password} autoComplete="current-password"
              onChange={e => setPassword(e.target.value)} placeholder="First time? Use Omni123"
            />
            <button
              type="button" className={OAP.pwToggle}
              onClick={() => setShowPw(s => !s)}
              aria-label={showPw ? 'Hide password' : 'Show password'} aria-pressed={showPw}
            >{showPw ? 'Hide' : 'Show'}</button>
          </div>

          <div style={{ height: 20 }} />
          <button type="submit" disabled={busy} className={OAP.primaryBtn}>
            {busy ? 'Checking…' : 'Continue'}
          </button>

          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <button
              type="button" className={OAP.linkBtn}
              onClick={() => { setErr(null); setMsg(null); setStep('forgot') }}
            >Forgot your password?</button>
          </div>

          <div className={OAP.orModule}>or</div>
          <button type="button" onClick={handleSignIn} className={OAP.msBtn}>
            <MicrosoftMark />
            Sign in with Microsoft
          </button>
        </form>
      )}

      {step === 'forgot' && (
        <form onSubmit={submitForgot} noValidate>
          <label htmlFor="oap-femail" className={OAP.label}>Work email</label>
          <input
            id="oap-femail" className={OAP.field} value={email} autoFocus
            autoComplete="username" type="email" inputMode="email"
            onChange={e => setEmail(e.target.value)} placeholder="you@alphadirect.co.bw"
          />
          <div style={{ height: 20 }} />
          <button type="submit" disabled={busy} className={OAP.primaryBtn}>
            {busy ? 'Sending…' : 'Send reset code'}
          </button>
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <button
              type="button" className={OAP.linkBtn}
              onClick={() => { setErr(null); setMsg(null); setStep('creds') }}
            >← Back to sign in</button>
          </div>
        </form>
      )}

      {step === 'reset' && (
        <form onSubmit={submitReset} noValidate>
          <label htmlFor="oap-rcode" className={OAP.label}>6-digit code</label>
          <input
            id="oap-rcode" className={OAP.field} style={codeInputStyle}
            value={code} autoFocus inputMode="numeric" maxLength={6}
            onChange={e => setCode(e.target.value.replace(/\D/g, ''))} placeholder="••••••"
          />
          <div style={{ height: 16 }} />
          <label htmlFor="oap-rpw1" className={OAP.label}>New password</label>
          <input
            id="oap-rpw1" className={OAP.field} type="password" value={pw1}
            autoComplete="new-password" onChange={e => setPw1(e.target.value)}
            placeholder="At least 8 characters"
          />
          <div style={{ height: 16 }} />
          <label htmlFor="oap-rpw2" className={OAP.label}>Confirm new password</label>
          <input
            id="oap-rpw2" className={OAP.field} type="password" value={pw2}
            autoComplete="new-password" onChange={e => setPw2(e.target.value)}
          />
          <div style={{ height: 20 }} />
          <button type="submit" disabled={busy} className={OAP.primaryBtn}>
            {busy ? 'Saving…' : 'Reset password & sign in'}
          </button>
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <button type="button" className={OAP.linkBtn} onClick={resend} disabled={busy}>
              Didn&rsquo;t get it? Resend code
            </button>
          </div>
        </form>
      )}

      {step === 'code' && (
        <form onSubmit={submitCode} noValidate>
          <label htmlFor="oap-code" className={OAP.label}>6-digit code</label>
          <input
            id="oap-code" className={OAP.field} style={codeInputStyle}
            value={code} autoFocus inputMode="numeric" maxLength={6}
            onChange={e => setCode(e.target.value.replace(/\D/g, ''))} placeholder="••••••"
          />
          <div style={{ height: 20 }} />
          <button type="submit" disabled={busy} className={OAP.primaryBtn}>
            {busy ? 'Verifying…' : 'Verify & sign in'}
          </button>
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <button type="button" className={OAP.linkBtn} onClick={resend} disabled={busy}>
              Didn&rsquo;t get it? Resend code
            </button>
          </div>
        </form>
      )}

      {step === 'change' && (
        <form onSubmit={submitChange} noValidate>
          <label htmlFor="oap-cpw1" className={OAP.label}>New password</label>
          <input
            id="oap-cpw1" className={OAP.field} type="password" value={pw1} autoFocus
            autoComplete="new-password" onChange={e => setPw1(e.target.value)}
            placeholder="At least 8 characters"
          />
          <div style={{ height: 16 }} />
          <label htmlFor="oap-cpw2" className={OAP.label}>Confirm new password</label>
          <input
            id="oap-cpw2" className={OAP.field} type="password" value={pw2}
            autoComplete="new-password" onChange={e => setPw2(e.target.value)}
          />
          <div style={{ height: 20 }} />
          <button type="submit" disabled={busy} className={OAP.primaryBtn}>
            {busy ? 'Saving…' : 'Set password & sign in'}
          </button>
        </form>
      )}

      {step === 'done' && (
        <div role="status" aria-live="polite" style={{ paddingTop: 6 }}>
          <div style={{
            width: 56, height: 56, borderRadius: '50%',
            background: 'rgba(240,127,0,0.12)', border: '1px solid rgba(240,127,0,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 20,
          }}>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 12.5l5 5L20 6.5" stroke="#F07F00" strokeWidth="2.4"
                    strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="oap-h1">{HEAD.done.h1}</h1>
          <p className="oap-sub">{HEAD.done.sub}</p>
        </div>
      )}

      {msg && !err && step !== 'done' && (
        <div className={OAP.notice}>{msg}</div>
      )}
      {err && (
        <div className={OAP.error}>{err}</div>
      )}

      {step !== 'done' && (
        <div style={{ marginTop: 20, textAlign: 'center' }}>
          <a href="/login" style={{ color: '#55657a', fontSize: 12.5, textDecoration: 'none' }}>
            Trouble signing in? More options
          </a>
        </div>
      )}
    </AccessPortalShell>
  )
}
