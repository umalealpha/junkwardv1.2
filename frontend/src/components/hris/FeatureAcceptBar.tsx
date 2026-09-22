'use client'

/**
 * FeatureAcceptBar — the one-click "I am happy with this feature" declaration.
 *
 * CFO directive 2026-07-21: force HR-team adoption. Every new HRIS feature page
 * mounts this bar; the person must be ON the page to accept it. Their dashboard
 * (HRIS home) shows what's still outstanding as their tasks.
 *
 * Uses apiFetch (@/lib/api) which prefixes /api/v1 and injects the SSO Bearer /
 * DRF token automatically — same auth as every other authed call.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

interface Feature { key: string; accepted: boolean; accepted_at: string | null }

export default function FeatureAcceptBar({ featureKey }: { featureKey: string }) {
  const [accepted, setAccepted] = useState<boolean | null>(null)
  const [at, setAt] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    apiFetch<{ features: Feature[] }>('/hris/feature-adoption/')
      .then((d) => {
        if (!live) return
        const f = (d.features || []).find((x) => x.key === featureKey)
        if (f) { setAccepted(!!f.accepted); setAt(f.accepted_at) }
        else setAccepted(false)
      })
      .catch(() => { if (live) setAccepted(false) })
    return () => { live = false }
  }, [featureKey])

  async function accept() {
    setBusy(true); setErr(null)
    try {
      const d = await apiFetch<{ accepted_at: string }>(
        '/hris/feature-adoption/accept/',
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: featureKey }) },
      )
      setAccepted(true); setAt(d.accepted_at)
    } catch {
      setErr('Could not save — please try again.')
    } finally { setBusy(false) }
  }

  const fmt = (iso: string | null) => {
    if (!iso) return ''
    try { return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) }
    catch { return '' }
  }

  // Loading: reserve space, no flash.
  if (accepted === null) {
    return <div style={{ height: 58, margin: '0 0 20px' }} aria-hidden />
  }

  const wrap: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    padding: '14px 18px', margin: '0 0 20px', borderRadius: 12,
    border: `1px solid ${accepted ? '#1f7a4d' : '#F4A623'}`,
    background: accepted ? 'rgba(31,122,77,0.10)' : 'rgba(244,166,35,0.10)',
  }

  return (
    <div style={wrap}>
      {accepted ? (
        <>
          <span style={{ fontSize: 20 }}>✅</span>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 700, color: '#1f7a4d' }}>You&rsquo;re happy with this feature</div>
            <div style={{ fontSize: 12.5, opacity: 0.75 }}>Signed off{at ? ` on ${fmt(at)}` : ''}. Thank you.</div>
          </div>
        </>
      ) : (
        <>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontWeight: 700 }}>Had a look? Let us know this works for you.</div>
            <div style={{ fontSize: 12.5, opacity: 0.75 }}>One click confirms you&rsquo;re happy with this feature.</div>
          </div>
          <button
            onClick={accept}
            disabled={busy}
            style={{
              padding: '10px 18px', borderRadius: 9, border: 'none',
              background: busy ? '#7a5410' : '#F4A623', color: '#0D1B2A',
              fontWeight: 800, fontSize: 14, cursor: busy ? 'not-allowed' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {busy ? 'Saving…' : '✓ I am happy with this feature'}
          </button>
        </>
      )}
      {err && <div style={{ width: '100%', color: '#C1121F', fontSize: 12.5 }}>{err}</div>}
    </div>
  )
}
