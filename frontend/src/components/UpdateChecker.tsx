'use client'

/**
 * UpdateChecker — tells an already-open Omni (browser tab OR the Windows
 * OmniDesktop WebView2 app) that a newer build has been deployed, and offers a
 * one-click refresh.
 *
 * Why: Omni is a single-page app. Once loaded it never re-fetches itself, so a
 * new deploy is invisible to anyone who leaves the window open — most visibly
 * the Windows desktop app, which people keep running for days. This polls a
 * tiny, never-cached build id (`/build-id.txt`, stamped per docker build) and
 * shows a banner when it changes. CFO directive 2026-07-07.
 */
import { useEffect, useRef, useState } from 'react'

const POLL_MS = 5 * 60 * 1000

// The build id baked into THIS bundle at compile time (see next.config.ts).
// It is the truth of "what version this tab is actually running" — unlike the
// first fetched value, which is stale if the bundle itself was served from
// cache after a deploy. Falls back to null on an older build that predates it.
const MY_BUILD_ID = process.env.NEXT_PUBLIC_BUILD_ID || null

export default function UpdateChecker() {
  const loaded = useRef<string | null>(null)
  const [stale, setStale] = useState(false)

  useEffect(() => {
    async function check() {
      try {
        const r = await fetch('/build-id.txt', { cache: 'no-store' })
        if (!r.ok) return
        const id = (await r.text()).trim()
        if (!id) return
        // Prefer the compile-time id: if the server serves a different build
        // than this bundle was built as, this tab is stale — catch it even on
        // the first read (a cache-served bundle opened after a deploy). Older
        // bundles without a baked id fall back to the first-fetch baseline.
        const mine = MY_BUILD_ID ?? loaded.current
        if (loaded.current === null) loaded.current = id
        if (mine && id !== mine) setStale(true)
      } catch { /* offline / transient — ignore */ }
    }
    check()
    const timer = setInterval(check, POLL_MS)
    const onFocus = () => check()
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [])

  if (!stale) return null
  return (
    <div role="status" style={{
      position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 99999,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      gap: 14, padding: '12px 18px', background: '#1D3270', color: '#fff',
      fontSize: 14, boxShadow: '0 -6px 24px rgba(0,0,0,.28)',
    }}>
      <span>A new version of Omni is ready.</span>
      <button onClick={() => window.location.reload()} style={{
        background: '#F47C20', color: '#0D1B2A', border: 0, borderRadius: 8,
        padding: '7px 16px', fontWeight: 700, cursor: 'pointer',
      }}>Refresh now</button>
      <button onClick={() => setStale(false)} style={{
        background: 'transparent', color: '#cdd6e6', border: '1px solid rgba(255,255,255,.35)',
        borderRadius: 8, padding: '7px 14px', cursor: 'pointer',
      }}>Later</button>
    </div>
  )
}
