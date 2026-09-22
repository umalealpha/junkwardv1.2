'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { Home, CheckCircle2, Briefcase, ListChecks, User, MoreHorizontal, Plus, X, CalendarClock } from 'lucide-react'
import { getAppToken } from './api'
import { C, safeBottom, dur, ease, serif } from './ui'
import { useManagerProbe } from './managerProbe'
import { applyTheme, readTheme } from './appThemes'
import { ScreenBeacon } from '@/components/ScreenBeacon'
import { isPublicAppRoute } from './publicRoutes'
import { Intro } from './Intro'
import { shouldPlayIntro, introAlreadyShown, markIntroShown } from './introGate'
import { isQaReadOnly } from '@/lib/qaView'

// Fixed tabs for everyone. Work (Omni Mobile C) is the permission-driven workspace
// hub and is where a manager reaches their team ("My team", gated inside Work); the
// Home TeamCard still surfaces the team at a glance. The manager probe below stays
// only to keep the bar stable across screens; all tabs are shown to everyone.
const TABS = [
  { href: '/app',         label: 'Home',    icon: Home,           managersOnly: false },
  { href: '/app/approve', label: 'Approve', icon: CheckCircle2,   managersOnly: false },
  { href: '/app/work',    label: 'Work',    icon: Briefcase,      managersOnly: false },
  { href: '/app/do',      label: 'Do',      icon: ListChecks,     managersOnly: false },
  { href: '/app/me',      label: 'Me',      icon: User,           managersOnly: false },
  { href: '/app/more',    label: 'More',    icon: MoreHorizontal, managersOnly: false },
]

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || '/app'
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const [quick, setQuick] = useState(false)   // Quick + sheet (Omni Mobile G)
  const isPublic = isPublicAppRoute(pathname)
  const onQuickTask = (pathname || '').startsWith('/app/quick-task')  // FAB is redundant on the composer itself
  const { isManager } = useManagerProbe(!isPublic)
  // The 5-second opening (CFO 2026-09-07). Decide AFTER mount, never in the
  // initialiser: the server can't read sessionStorage, so an initialiser that
  // does would disagree with the client's first paint and break hydration.
  // 'boot' renders the same navy frame on server and first client paint (no
  // white flash, on-brand), then the effect flips to 'intro' or 'done'.
  // Gate rules + the test live in introGate.ts.
  const [phase, setPhase] = useState<'boot' | 'intro' | 'done'>('boot')
  useEffect(() => {
    setPhase(shouldPlayIntro(pathname, { alreadyShown: introAlreadyShown(), qaReadOnly: isQaReadOnly() })
      ? 'intro' : 'done')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])   // once, on first mount — pathname at open is what matters
  const tabs = TABS.filter(t => !t.managersOnly || isManager)

  useEffect(() => {
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/app-sw.js', { scope: '/app' }).catch(() => {})
  }, [])
  // Safety net for the boot script (blocked inline scripts, a restored bfcache page).
  useEffect(() => { applyTheme(readTheme()) }, [])
  useEffect(() => {
    if (!isPublic && !getAppToken()) { router.replace('/app/login'); return }
    setReady(true)
  }, [isPublic, pathname, router])

  if (phase === 'boot') return <div style={{ minHeight: '100dvh', background: C.navy }} />
  if (phase === 'intro') return <Intro onDone={() => { markIntroShown(); setPhase('done') }} />
  if (!ready && !isPublic) return <div style={{ minHeight: '100dvh', background: C.navy }} />
  const active = (href: string) => href === '/app' ? pathname === '/app' : pathname.startsWith(href)
  return (
    <div style={{ minHeight: '100dvh', background: C.surface, maxWidth: 480, margin: '0 auto',
                  paddingBottom: isPublic ? 0 : `calc(76px + ${safeBottom})` }}>
      {children}
      {!isPublic && <ScreenBeacon surface="app" />}
      {!isPublic && (
        <nav aria-label="Alpha Omni" style={{ position: 'fixed', left: 0, right: 0, bottom: 0, margin: '0 auto', maxWidth: 480,
              background: C.card, borderTop: `1px solid ${C.line}`, display: 'flex',
              padding: `8px 0 calc(10px + ${safeBottom})`, zIndex: 40 }}>
          {tabs.map(({ href, label, icon: Icon }) => {
            const on = active(href)
            return (
              <Link key={href} href={href} aria-current={on ? 'page' : undefined} className="oa-press"
                style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
                         minHeight: 44, textDecoration: 'none', color: on ? C.head : C.inkSoft,
                         transition: `color ${dur.fast} ${ease}, transform ${dur.fast} ${ease}` }}>
                <span style={{ display: 'grid', placeItems: 'center', width: 44, height: 28, borderRadius: 14,
                               background: on ? 'var(--ao-orange-wash, rgba(240,127,0,0.14))' : 'transparent',
                               transition: `background ${dur.base} ${ease}` }}>
                  <Icon size={20} strokeWidth={on ? 2.4 : 2} />
                </span>
                <span style={{ fontSize: 11, fontWeight: on ? 700 : 500, whiteSpace: 'nowrap' }}>{label}</span>
              </Link>
            )
          })}
        </nav>
      )}

      {/* Quick + — one persistent action to hand out a task now (Omni Mobile G).
          Meeting is the next action here once Quick Meeting (F) lands. */}
      {!isPublic && !onQuickTask && (
        <>
          <div style={{ position: 'fixed', left: 0, right: 0, bottom: `calc(86px + ${safeBottom})`, zIndex: 45, maxWidth: 480, margin: '0 auto', pointerEvents: 'none' }}>
            <button aria-label="Quick add" onClick={() => setQuick(true)} className="oa-press"
              style={{ position: 'absolute', right: 16, bottom: 0, width: 56, height: 56, borderRadius: 18, border: 0, background: C.orange, color: C.navy,
                       boxShadow: '0 8px 24px rgba(11,11,59,0.28)', display: 'grid', placeItems: 'center', pointerEvents: 'auto',
                       transition: `transform ${dur.fast} ${ease}` }}>
              <Plus size={26} strokeWidth={2.6} />
            </button>
          </div>
          {quick && (
            <div role="dialog" aria-modal="true" aria-label="Quick add" onClick={() => setQuick(false)}
                 style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'var(--ao-navy-wash, rgba(11,11,59,0.06))', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
              <div onClick={e => e.stopPropagation()} className="oa-rise"
                   style={{ width: '100%', maxWidth: 480, background: C.card, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: `16px 16px calc(20px + ${safeBottom})` }}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
                  <span style={{ flex: 1, fontFamily: serif, fontWeight: 700, fontSize: 18, color: C.head }}>Quick add</span>
                  <button onClick={() => setQuick(false)} aria-label="Close" className="oa-press" style={{ border: 0, background: 'transparent', color: C.inkSoft, display: 'grid', placeItems: 'center', width: 36, height: 36 }}><X size={22} /></button>
                </div>
                <button onClick={() => { setQuick(false); router.push('/app/quick-task') }} className="oa-press"
                  style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 14, padding: 16, borderRadius: 14, border: `1px solid ${C.line}`, background: C.card, textAlign: 'left', cursor: 'pointer', marginBottom: 10 }}>
                  <span style={{ display: 'grid', placeItems: 'center', width: 44, height: 44, borderRadius: 14, background: C.navy }}><ListChecks size={22} color={C.orange} /></span>
                  <span style={{ flex: 1 }}>
                    <span style={{ display: 'block', fontWeight: 700, fontSize: 16, color: C.ink }}>New task</span>
                    <span style={{ display: 'block', fontSize: 13, color: C.inkSoft }}>Hand a job to a colleague</span>
                  </span>
                </button>
                <div aria-disabled="true"
                  style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 14, padding: 16, borderRadius: 14, border: `1px solid ${C.line}`, background: C.surface, opacity: 0.7 }}>
                  <span style={{ display: 'grid', placeItems: 'center', width: 44, height: 44, borderRadius: 14, background: C.line }}><CalendarClock size={22} color={C.inkSoft} /></span>
                  <span style={{ flex: 1 }}>
                    <span style={{ display: 'block', fontWeight: 700, fontSize: 16, color: C.ink }}>New meeting</span>
                    <span style={{ display: 'block', fontSize: 13, color: C.inkSoft }}>Coming soon</span>
                  </span>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
