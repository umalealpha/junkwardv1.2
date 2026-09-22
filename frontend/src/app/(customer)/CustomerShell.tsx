'use client'

/**
 * CustomerShell — client gate + bottom navigation for the customer app.
 * Redirects to /m/login when there is no session (except on the login page),
 * and shows a 4-tab bottom bar once signed in. Mobile-first, Alpha Direct
 * navy/orange, no dependency on the staff dashboard providers.
 */
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import Link from 'next/link'
import { Home, Compass, Gift, HeartPulse } from 'lucide-react'
import { getCustToken } from './api'
import { Splash } from './Splash'
import { safeBottom } from './ui'
import { ScreenBeacon } from '@/components/ScreenBeacon'

/* Register the /m/-scoped offline service worker (separate from the root push SW) */
function registerNexusSW() {
  if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
    navigator.serviceWorker.register('/m/sw.js', { scope: '/m/' }).catch(() => {})
  }
}

const SPLASH_KEY = 'nexus_splash_shown'

const NAVY = '#0D1B2A'

const TABS = [
  { href: '/m',         label: 'Home',    icon: Home },
  { href: '/m/drive',   label: 'Drive',   icon: Compass },
  { href: '/m/rewards', label: 'Rewards', icon: Gift },
  { href: '/m/thrive',  label: 'Wellness', icon: HeartPulse },
]

export function CustomerShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [ready, setReady] = useState(false)

  // Register offline service worker on mount
  useEffect(() => { registerNexusSW() }, [])

  // The splash is a FIRST-OPEN intro only. Read the session flag synchronously
  // (lazy initializer) — reading it in an effect let the splash mount for a
  // frame even when already shown, and worse: the login page skipped the
  // splash without marking it shown, so it played AFTER you signed in.
  const [splashDone, setSplashDone] = useState(() => {
    try { return typeof window !== 'undefined' && sessionStorage.getItem(SPLASH_KEY) === '1' }
    catch { return true }  // storage blocked (private mode) → never loop the splash
  })
  const isPublic = pathname === '/m/login' || pathname.startsWith('/m/get') || pathname === '/m/privacy'
    || pathname === '/m/privacy-notice'  // public Policyholder Privacy Notice (DPA S-3)
    || pathname === '/m/delete-account'  // public deletion page (Google Play Data-safety URL)

  // Entering via a public page (login etc.) consumes the splash for this
  // session too — otherwise it plays after login, which reads as a glitch.
  useEffect(() => {
    if (isPublic && !splashDone) {
      try { sessionStorage.setItem(SPLASH_KEY, '1') } catch { /* private mode */ }
      setSplashDone(true)
    }
  }, [isPublic, splashDone])

  useEffect(() => {
    if (!splashDone) return
    if (!isPublic && !getCustToken()) { router.replace('/m/login'); return }
    setReady(true)
  }, [isPublic, router, pathname, splashDone])

  if (!splashDone && !isPublic) {
    return <Splash onDone={() => {
      try { sessionStorage.setItem(SPLASH_KEY, '1') } catch { /* private mode */ }
      setSplashDone(true)
    }} />
  }

  if (!ready && !isPublic) {
    return <div style={{ minHeight: '100vh', background: NAVY }} />
  }

  return (
    <div style={{ minHeight: '100vh', background: '#F6F7F9', maxWidth: 480, margin: '0 auto', position: 'relative' }}>
      <div style={{ paddingBottom: isPublic ? 0 : `calc(76px + ${safeBottom})` }}>{children}</div>
      {/* Screen-usage telemetry (CFO 2026-09-03) — staff sessions only; no token, no beacon. */}
      <ScreenBeacon surface="m" />

      {!isPublic && (
        <nav style={{
          position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
          width: '100%', maxWidth: 480, background: '#FFFFFF', borderTop: '1px solid #E5E7EB',
          display: 'flex', justifyContent: 'space-around',
          padding: `8px 0 calc(10px + ${safeBottom})`, zIndex: 20,
        }}>
          {TABS.map(t => {
            const active = pathname === t.href
            const Icon = t.icon
            return (
              <Link key={t.href} href={t.href}
                style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
                  textDecoration: 'none', color: active ? '#0E9488' : '#9CA3AF', fontSize: 11, fontWeight: 600 }}>
                <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 52, height: 30, borderRadius: 999, background: active ? 'rgba(45,212,191,0.16)' : 'transparent' }}>
                  <Icon size={21} />
                </span>
                {t.label}
              </Link>
            )
          })}
        </nav>
      )}
    </div>
  )
}
