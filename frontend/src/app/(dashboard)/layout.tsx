'use client'

import { useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Sidebar } from '@/components/layout/Sidebar'
import { FunBackground } from '@/components/FunBackground'
import { LoadingScreen } from '@/components/LoadingScreen'
import { getToken, getRBACMe, removeToken } from '@/lib/api'
import { isSSOConfigured, getMsalInstance } from '@/auth/msal'
import { useTheme } from '@/contexts/ThemeContext'
import { useQuote } from '@/contexts/QuoteContext'
import { CompanyProvider } from '@/contexts/CompanyContext'
import { getQuotePageKey } from '@/lib/quotes'
import { cn } from '@/lib/utils'
import ErrorBoundary from '@/components/ErrorBoundary'
import AriaFloatingA from '@/components/aria/AriaFloatingA'
import { CommandPalette } from '@/components/CommandPalette'
import ChatWidget from '@/components/chat/ChatWidget'
import { BirthdayBanner } from '@/components/layout/BirthdayBanner'
import { QaReadOnlyBanner } from '@/components/layout/QaReadOnlyBanner'
import { isQaReadOnly } from '@/lib/qaView'
import { TaskReminderGuard } from '@/components/TaskReminderGuard'
import { PrivacyNoticeModal } from '@/components/PrivacyNoticeModal'
import { ScreenBeacon } from '@/components/ScreenBeacon'
import AriaPopupOverlay from '@/components/aria/AriaPopupOverlay'

// ─── Layout ──────────────────────────────────────────────────────────────────

interface DashboardLayoutProps {
  children: React.ReactNode
}

export default function DashboardLayout({ children }: DashboardLayoutProps) {
  const router = useRouter()
  const pathname = usePathname()
  const { theme } = useTheme()
  const { refreshQuote } = useQuote()

  const [checking, setChecking] = useState(true)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  // Lightweight auth gate — only checks the user is signed in (MSAL account
  // OR legacy DRF token). The /pending-approval role gate runs once on the
  // home page (app/page.tsx); we don't re-check here on every navigation,
  // otherwise any transient backend hiccup bounces the user around. Real
  // API calls underneath enforce permissions per-request.
  useEffect(() => {
    if (isSSOConfigured()) {
      try {
        if (getMsalInstance().getAllAccounts().length > 0) {
          setChecking(false)
          return
        }
      } catch {}
    }
    if (getToken()) {
      setChecking(false)
    } else {
      router.replace(isQaReadOnly() ? '/qa' : '/login')
    }
  }, [router])

  // Auto sign-out after 60 minutes of inactivity (CFO directive 2026-07-18).
  // Protects a walked-away/unlocked screen. Any mouse/keyboard/touch/scroll
  // resets the timer; on expiry we clear the local session and bounce to the
  // sign-in page. The server also enforces a hard 15-hour session cap.
  useEffect(() => {
    const IDLE_MS = 60 * 60 * 1000
    let timer: number
    const signOutIdle = () => {
      // Read-only quality-check sessions go back to /qa, which reopens in one
      // click from the remembered key — /login is a dead end for them.
      const backTo = isQaReadOnly() ? '/qa' : '/login?signedout=1'
      try { removeToken() } catch {}
      try { localStorage.removeItem('alpha_user') } catch {}
      try { localStorage.removeItem('alpha_company_id') } catch {}
      router.replace(backTo)
    }
    const reset = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(signOutIdle, IDLE_MS)
    }
    const events = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'click']
    events.forEach(e => window.addEventListener(e, reset, { passive: true }))
    reset()
    return () => {
      window.clearTimeout(timer)
      events.forEach(e => window.removeEventListener(e, reset))
    }
  }, [router])

  // When the company switcher changes, pages that declare companyId in their
  // useEffect deps automatically refetch via React state propagation from
  // CompanyContext. A full window.location.reload() is NOT needed and caused
  // a jarring full-page-reload "disco lights" effect on every company switch.
  // Pages that need to react to company changes should include selectedId from
  // useCompany() in their own useEffect dependency arrays.

  // Refresh the funny quote on navigation. No fade overlay — that added a
  // 150ms perceptual stall on every sidebar click for no real UX benefit.
  useEffect(() => {
    refreshQuote(getQuotePageKey(pathname))
  }, [pathname, refreshQuote])

  if (checking) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ backgroundColor: theme.bg }}
      >
        <LoadingScreen page="default" />
      </div>
    )
  }

  return (
    <CompanyProvider>
      <div className="min-h-screen flex" style={{ backgroundColor: theme.bg }}>
        {/* Fun mode background */}
        <FunBackground active={theme.funBg} />

        {/* Sidebar */}
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(prev => !prev)}
        />

        {/* Main content area */}
        {/* BUG-1/BUG-2 ROOT CAUSE (Oprah QA 2026-06-10): `main` is a flex
            child whose default min-width:auto let wide page content push it
            PAST the viewport (right edge x≈1212 in a 1151px viewport), so
            right-aligned dates/amounts rendered off-screen on every report.
            min-w-0 makes main shrink to the available space; overflow-x-clip
            stops any residual bleed. Fixes /reports/balance-sheet,
            /reports/expense-analysis, /reports/cash-position, /payments in
            one place instead of per-row CSS patches. */}
        <main
          className={cn(
            'aurora-surface flex-1 min-w-0 overflow-x-clip min-h-screen flex flex-col transition-all duration-200 relative z-[1]',
            // Margins track the two-tier sidebar (icon rail 72px + contextual
            // panel 260px) from Sidebar.tsx — keep in sync with SIDEBAR_*_W.
            // Must track SIDEBAR_RAIL_W / SIDEBAR_FULL_W in Sidebar.tsx —
            // these are hardcoded, so widening the rail (72->96) without
            // changing them leaves the content overlapping the labels.
            sidebarCollapsed ? 'md:ml-[96px]' : 'md:ml-[356px]'
          )}
        >
          <div className="flex-1 flex flex-col">
            {/* Birthday ribbon — shows when staff have a birthday today.
                Per-day per-browser dismissal via localStorage. CFO 2026-06-09. */}
            {/* Read-only quality-check strip — only in the /qa view. */}
            <QaReadOnlyBanner />
            <BirthdayBanner />
            <ErrorBoundary surface={pathname}>
              {children}
            </ErrorBoundary>
          </div>
        </main>
        {/* ARIA — floating orange-A assistant. Per-user kill in its own
            component via localStorage 'aria_killed'. Sits above the
            sidebar (z=9998), below modals (which use 9999+). */}
        <AriaFloatingA />
        {/* Team chat — pops up once per session; /task @user → OmniTask. 2026-06-09 */}
        <ChatWidget />
        {/* Global ⌘K / Ctrl-K quick navigator (CFO "Ferrari" UX 2026-06-03) */}
        <CommandPalette />
        {/* Taskboard reminder engine — assign toast + due/overdue force-action
            modal + the completion gate (meeting discipline, CFO 2026-07-09).
            Polls every 90s; no websockets (cost discipline). */}
        <TaskReminderGuard />
        {/* Staff privacy notice — non-dismissible login pop-up until signed;
            who has NOT signed shows in the daily HRIS email (CFO 2026-07-09). */}
        <PrivacyNoticeModal />
        {/* CFO popup messages via Aria — polls every 30s, full-screen overlay. */}
        <AriaPopupOverlay />
        {/* Screen-usage telemetry — which screens staff open, per minute, no
            content (CFO 2026-09-03). Renders nothing. */}
        <ScreenBeacon surface="desktop" />
      </div>
    </CompanyProvider>
  )
}
