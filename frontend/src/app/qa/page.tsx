'use client'

/**
 * /qa — open Omni in READ-ONLY quality-check mode, with no sign-in.
 *
 * CFO directive 2026-07-29: checking Omni through a normal staff account was a
 * headache — a password, an emailed code, a session that dies overnight, a
 * privacy pop-up, and role denials that look like faults. This page trades one
 * access key for a session on the locked `omni-qa-view` identity: every page
 * visible, every write refused by the server itself (core/token_auth.py).
 *
 * The key is remembered on this browser, so after the first time this page is a
 * single button — bookmark /qa and go. It is sent in the request body, never in
 * the address bar, so it stays out of server logs and browser history.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { setToken } from '@/lib/api'
import { setQaReadOnly, getSavedQaKey, saveQaKey, forgetQaKey } from '@/lib/qaView'

const API = `${process.env.NEXT_PUBLIC_API_BASE ?? ''}/api/v1/auth/qa-view/`

export default function QaViewPage() {
  const router = useRouter()
  const [key, setKey] = useState('')
  const [remembered, setRemembered] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    const saved = getSavedQaKey()
    if (saved) { setKey(saved); setRemembered(true) }
  }, [])

  async function open(e: React.FormEvent) {
    e.preventDefault()
    const k = key.trim()
    if (!k) { setErr('Paste the quality-check key.'); return }
    setBusy(true); setErr(null)
    try {
      const r = await fetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: k }),
      })
      const d = await r.json().catch(() => ({} as { detail?: string }))
      if (!r.ok) {
        throw new Error(r.status === 429
          ? 'Too many tries — wait a minute.'
          : (d.detail || 'Could not open the read-only view.'))
      }
      saveQaKey(k)
      setQaReadOnly()
      setToken(d.token as string)
      router.replace('/dashboard')
    } catch (e) {
      setErr((e as Error).message)
      setBusy(false)
    }
  }

  const inp: React.CSSProperties = {
    width: '100%', padding: '11px 13px', marginTop: 6, borderRadius: 9,
    border: '1px solid #2b3a4d', background: '#0b1622', color: '#fff',
    fontSize: 14, outline: 'none', boxSizing: 'border-box',
  }
  const lbl: React.CSSProperties = { fontSize: 12, color: '#9fb0c3', fontWeight: 600 }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: '#0D1B2A', padding: 24,
    }}>
      <form onSubmit={open} style={{
        width: '100%', maxWidth: 420, background: '#101f31',
        border: '1px solid #22344a', borderRadius: 14, padding: 28,
        fontFamily: 'system-ui, sans-serif',
      }}>
        <div style={{ fontSize: 11, letterSpacing: 1.4, color: '#F4A623', fontWeight: 700 }}>
          ALPHA DIRECT · OMNI
        </div>
        <h1 style={{ color: '#fff', fontSize: 22, fontWeight: 700, margin: '8px 0 6px' }}>
          Quality check — read only
        </h1>
        <p style={{ color: '#9fb0c3', fontSize: 13, lineHeight: 1.55, margin: '0 0 18px' }}>
          Opens Omni with everything visible and nothing changeable. No email, no
          password, no code. Every save, approve and delete is refused by the
          server, so you can click freely without touching real data.
        </p>

        <label style={lbl} htmlFor="qa-key">Quality-check key</label>
        <input
          id="qa-key" type="password" value={key} autoComplete="off"
          onChange={ev => { setKey(ev.target.value); setRemembered(false) }}
          placeholder={remembered ? 'Remembered on this browser' : 'Paste the key'}
          style={inp}
        />

        {err && (
          <div style={{
            marginTop: 14, padding: '10px 12px', borderRadius: 9,
            background: '#3a1620', border: '1px solid #6b2331',
            color: '#ffb4b4', fontSize: 13,
          }}>{err}</div>
        )}

        <button type="submit" disabled={busy} style={{
          width: '100%', marginTop: 18, padding: '12px', borderRadius: 9,
          border: 'none', background: busy ? '#7a5410' : '#F4A623',
          color: '#0D1B2A', fontWeight: 700, fontSize: 15,
          cursor: busy ? 'not-allowed' : 'pointer',
        }}>
          {busy ? 'Opening…' : 'Open read-only view'}
        </button>

        {remembered && (
          <button type="button" onClick={() => { forgetQaKey(); setKey(''); setRemembered(false) }}
            style={{
              width: '100%', marginTop: 10, padding: '9px', borderRadius: 9,
              border: '1px solid #2b3a4d', background: 'transparent',
              color: '#9fb0c3', fontSize: 12, cursor: 'pointer',
            }}>
            Forget the key on this browser
          </button>
        )}

        <p style={{ color: '#6f8298', fontSize: 11, lineHeight: 1.5, marginTop: 18 }}>
          This view can read everything in Omni, including staff and customer
          records. Keep the key to yourself. Every time it is used, or a wrong key
          is tried, it is written to the audit log.
        </p>
      </form>
    </div>
  )
}
