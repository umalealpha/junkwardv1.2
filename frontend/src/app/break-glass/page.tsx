'use client'

/**
 * /break-glass — emergency admin login that does NOT use Microsoft SSO.
 *
 * CFO directive 2026-06-16 (permanent SSO fix, part 1 of 4): when Microsoft
 * sign-in is down — a stuck redirect, a tenant/conditional-access issue, or a
 * Microsoft Entra outage — the CFO + key admins still need a way in. This page
 * posts username + password to /api-token-auth/ (RateLimitedLoginView), which
 * is rate-limited, admin-only (BREAK_GLASS_EMAILS allowlist or staff/superuser)
 * and audited. Deliberately NOT linked from the main UI; reachable by URL only.
 *
 * Only accounts with a real Django password set can use it — SSO users have
 * unusable passwords, so this is not a general back-door.
 */
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { setToken } from '@/lib/api'

const TOKEN_URL = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api-token-auth/`

export default function BreakGlassPage() {
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password) { setErr('Enter your username and password.'); return }
    setBusy(true); setErr(null)
    try {
      const r = await fetch(TOKEN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok || !d.token) {
        setErr(
          r.status === 429
            ? 'Too many attempts — wait a minute and try again.'
            : (d.detail || 'Sign-in failed. Check your username and password.'),
        )
        return
      }
      setToken(d.token)
      router.replace('/dashboard')
    } catch {
      setErr('Network error — please try again.')
    } finally {
      setBusy(false)
    }
  }

  const inp: React.CSSProperties = {
    width: '100%', padding: '11px 13px', marginTop: 6, borderRadius: 9,
    border: '1px solid #2b3a4d', background: '#0b1622', color: '#fff',
    fontSize: 14, outline: 'none',
  }
  const lbl: React.CSSProperties = { fontSize: 12, color: '#9fb0c3', fontWeight: 600 }

  return (
    <main style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'radial-gradient(circle at 50% 25%, #14283d 0%, #0D1B2A 60%)',
      fontFamily: "'Book Antiqua', Georgia, serif", padding: 20,
    }}>
      <div style={{
        width: 380, maxWidth: '100%', background: '#0f2030',
        border: '1px solid #24364a', borderRadius: 16, overflow: 'hidden',
        boxShadow: '0 24px 60px -12px rgba(0,0,0,0.6)',
      }}>
        <div style={{ background: '#0D1B2A', padding: '18px 22px', borderBottom: '1px solid #24364a' }}>
          <div style={{ color: '#F4A623', fontWeight: 700, fontSize: 18 }}>Omni — Emergency Login</div>
          <div style={{ color: '#9fb0c3', fontSize: 12, marginTop: 4 }}>
            Use this only when “Sign in with Microsoft” is unavailable.
          </div>
        </div>
        <form onSubmit={submit} style={{ padding: 22 }}>
          <label style={lbl}>Username
            <input style={inp} value={username} autoFocus autoComplete="username"
                   onChange={e => setUsername(e.target.value)} placeholder="e.g. pganesharajah" />
          </label>
          <div style={{ height: 14 }} />
          <label style={lbl}>Password
            <input style={inp} type="password" value={password} autoComplete="current-password"
                   onChange={e => setPassword(e.target.value)} />
          </label>
          {err && (
            <div style={{
              marginTop: 14, background: 'rgba(193,18,31,0.15)', border: '1px solid #C1121F',
              color: '#ffb3ba', borderRadius: 9, padding: '9px 12px', fontSize: 13,
            }}>{err}</div>
          )}
          <button type="submit" disabled={busy} style={{
            width: '100%', marginTop: 18, padding: '12px', borderRadius: 9, border: 'none',
            background: busy ? '#7a5410' : '#F4A623', color: '#0D1B2A', fontWeight: 700,
            fontSize: 15, cursor: busy ? 'not-allowed' : 'pointer',
          }}>{busy ? 'Signing in…' : 'Sign in'}</button>
          <a href="/" style={{
            display: 'block', textAlign: 'center', marginTop: 14,
            color: '#9fb0c3', fontSize: 12.5, textDecoration: 'none',
          }}>← Back to normal Microsoft sign-in</a>
        </form>
      </div>
    </main>
  )
}
