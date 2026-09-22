'use client'

/**
 * ScreenBeacon — screen-usage telemetry (CFO 2026-09-03: "who opens which
 * screen, per minute, staff only, no content").
 *
 * Omni is a Next.js SPA, so page changes never reach Django. This posts the
 * current path to /api/v1/adoption/screen-view/ on every route change; the
 * server collapses record ids to `:id` and keeps one row per user/screen/minute.
 * Pure telemetry — a failed beacon must never touch the page.
 */
import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { API_BASE } from '@/lib/api'

export type BeaconSurface = 'desktop' | 'm' | 'app'

const THROTTLE_MS = 60_000
// Per screen, last time we beaconed it — survives route changes within the tab.
const lastSent = new Map<string, number>()

/**
 * The credential for THIS surface, then the others as fallbacks.
 *
 * The first cut used one fixed order — app token, then Nexus, then desktop —
 * for all three shells. Any member of staff who had ever opened the rewards
 * area in the same browser kept an `alpha_rewards_token` in localStorage
 * forever after, so every desktop beacon went out carrying the Nexus
 * credential and came back 401. Measured on prod on 2026-09-09: 31 of 32
 * beacons in 24 hours were rejected, and `.catch()` swallowed every one of
 * them. 108 of 156 staff looked like they had never opened Omni, and rule 9
 * of the review engine was about to mark them down for it.
 *
 * So: the surface picks its own credential first, and the others are only
 * tried if that one is missing or is refused.
 */
const CREDENTIALS: Record<BeaconSurface, Array<[string, string]>> = {
  //          localStorage key        Authorization scheme
  desktop: [['alpha_token',         'Token'],
            ['omni_app_token',      'Bearer'],
            ['alpha_rewards_token', 'Bearer']],
  app:     [['omni_app_token',      'Bearer'],
            ['alpha_token',         'Token'],
            ['alpha_rewards_token', 'Bearer']],
  m:       [['alpha_rewards_token', 'Bearer'],
            ['alpha_token',         'Token'],
            ['omni_app_token',      'Bearer']],
}

export function authHeaders(surface: BeaconSurface): string[] {
  const out: string[] = []
  try {
    for (const [key, scheme] of CREDENTIALS[surface] ?? []) {
      const v = localStorage.getItem(key)
      if (v) out.push(`${scheme} ${v}`)
    }
  } catch {
    // storage blocked (private mode) — no session to attribute the view to
  }
  return out
}

export function ScreenBeacon({ surface }: { surface: BeaconSurface }) {
  const pathname = usePathname()

  useEffect(() => {
    if (!pathname) return
    const creds = authHeaders(surface)
    if (creds.length === 0) return
    const now = Date.now()
    const last = lastSent.get(pathname) ?? 0
    if (now - last < THROTTLE_MS) return
    lastSent.set(pathname, now)

    // Try this surface's own credential; on a 401 fall through to the others,
    // so a stale key left behind by another shell cannot silence the beacon.
    const post = async () => {
      for (const auth of creds) {
        const r = await fetch(`${API_BASE}/adoption/screen-view/`, {
          method: 'POST',
          keepalive: true,
          headers: { 'Content-Type': 'application/json', Authorization: auth },
          body: JSON.stringify({ screen: pathname, surface }),
        })
        if (r.status !== 401) return          // recorded, or a fault worth leaving alone
      }
      // Every credential refused: the person is signed out. Forget the throttle
      // so the next navigation after they sign back in is recorded.
      lastSent.delete(pathname)
    }
    post().catch(() => undefined) // telemetry only — never surface a network error
  }, [pathname, surface])

  return null
}
