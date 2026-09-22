'use client'

import Image from 'next/image'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState, useEffect, useMemo, useRef } from 'react'
import { activeRowKey, deepestHref, routeModule } from '@/lib/navActive'
// Sidebar chrome only — every nav-data icon moved to lib/navModules.ts with
// the rest of the nav definition (M-NAV-DOMAINS, 19-Sep-2026).
import {
  BookOpen,
  Search,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Menu,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTheme } from '@/contexts/ThemeContext'
import { removeToken, getMe, apiFetch } from '@/lib/api'
import type { UserProfile } from '@/lib/api'
import { useSalvageAccess } from '@/hooks/useSalvageAccess'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { useCompany } from '@/contexts/CompanyContext'
import { ADIPL_ONLY_HREFS, isAdiplPlanVisible, isCompanyResolving } from '@/lib/entityScope'

// ─── Navigation structure ────────────────────────────────────────────────────
// One canonical nav definition (M-NAV-DOMAINS, 19-Sep-2026): every item, group,
// icon and gate lives in lib/navModules.ts — buildNavModules() below is called
// with the live permission probes. See that file for the full placement
// reasoning block (CFO be335dfe + 3f2b2411) and the domain -> item mapping.
import {
  buildNavModules,
  homeItems, myWorkItems, financeSelfServiceItems, dataAiItems, platformOpsItems,
  nexusItems, hrisSelfServiceItems, helpItems,
  type NavItem, type NavModule, type NavSection, type NavGates,
} from '@/lib/navModules'

/** With-alpha helper so the active-item wash always follows the theme accent. */
function withAlpha(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

// ─── Props ───────────────────────────────────────────────────────────────────

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
}

/** Rail width + expanded width — (dashboard)/layout.tsx margins must match. */
// Wide enough for a two-line name under the icon (CFO 2026-08-06).
export const SIDEBAR_RAIL_W = 96
export const SIDEBAR_FULL_W = 356

// ─── Component ───────────────────────────────────────────────────────────────

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname()
  const router = useRouter()
  const { theme, themeKey } = useTheme()
  const { selected: selectedCompany, selectedId: selectedCompanyId } = useCompany()
  // On unknown, hide. While the company list is still resolving `selected` is
  // null, which would otherwise read as the group view and show the links.
  const planVisible = !isCompanyResolving(selectedCompanyId, selectedCompany)
    && isAdiplPlanVisible(selectedCompany?.code)

  const [mobileOpen, setMobileOpen] = useState(false)
  const [username, setUsername] = useState('')
  const [me, setMe] = useState<UserProfile | null>(null)
  // Module the user explicitly clicked on the rail. Null = follow the route.
  const [pinnedModule, setPinnedModule] = useState<string | null>(null)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)
  const canAccessSalvage = useSalvageAccess()
  // HRIS module is restricted to a CFO whitelist (Prathap, Arun, Kago,
  // Pako, Unami) plus superuser / admins — CFO directive 2026-05-18.
  const canAccessHris = useHrisAccess()
  // Self-service tier — every employee (incl. subsidiaries like ADRisk) gets
  // their own leave / payslips / profile even off the whitelist (CFO directive
  // 2026-06-16, bug aec2f3ce).
  const canSelfServeHris = useHrisSelfService()
  // Data Protection module — DPO + C-suite + HR + Finance Mgr, server-gated
  // (CFO directive 2026-07-20: its own folder, like HRIS).
  const [canAccessDpa, setCanAccessDpa] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/dpa-dashboard/access/')
      .then(r => setCanAccessDpa(!!r.allowed)).catch(() => setCanAccessDpa(false))
  }, [])
  // AML / Compliance overview — AML officer + Compliance & Risk + C-suite / HR /
  // Finance, server-gated (CFO directive 2026-07-24). Shown atop the Compliance
  // module only to those allowed; the page itself is server-gated too.
  const [canAccessCompliance, setCanAccessCompliance] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/compliance-dashboard/access/')
      .then(r => setCanAccessCompliance(!!r.allowed)).catch(() => setCanAccessCompliance(false))
  }, [])
  // Security Posture — group C-suite only (CEO / COO / CFO), server-gated
  // (CFO directive 2026-08-06). Sits in the Compliance module beside AML.
  const [canAccessSecurity, setCanAccessSecurity] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/security-dashboard/access/')
      .then(r => setCanAccessSecurity(!!r.allowed)).catch(() => setCanAccessSecurity(false))
  }, [])
  // UniCoin Instant Insurance portal — its OWN top-level module for the portal
  // managers (CFO 2026-07-27: "under unicoin, out of ADIC"), not buried in the
  // Accounting list. Same manager probe as the portal itself; non-managers never
  // see it. Probe loading → hidden (no flash).
  const [canManageUnicoin, setCanManageUnicoin] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ is_manager: boolean }>('/agent-portal/cycles/access/')
      .then(r => setCanManageUnicoin(!!r.is_manager)).catch(() => setCanManageUnicoin(false))
  }, [])
  // Audience Feedback — confidential to the speaker alone (CFO 2026-08-03).
  // Server-gated with no admin bypass; hidden here for everyone else so the
  // module's existence isn't advertised. Probe loading → hidden (no flash).
  const [canReadSpeakerFeedback, setCanReadSpeakerFeedback] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/speaker-feedback/access/')
      .then(r => setCanReadSpeakerFeedback(!!r.allowed))
      .catch(() => setCanReadSpeakerFeedback(false))
  }, [])

  // The CFO's build log is his alone (CFO 2026-09-09). Same treatment as the
  // speaker feedback above: probe, and while the answer is unknown keep it
  // hidden, so the item never flashes on somebody else's menu. Hiding is not
  // the control — the API and the page are both gated — it just stops the
  // screen's existence being advertised to everyone.
  const [isTheCfo, setIsTheCfo] = useState<boolean | undefined>(undefined)
  const [canSeeForgiveness, setCanSeeForgiveness] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ is_the_cfo: boolean; can_see_forgiveness: boolean }>('/cfo/whoami/')
      .then(r => { setIsTheCfo(!!r.is_the_cfo); setCanSeeForgiveness(!!r.can_see_forgiveness) })
      .catch(() => { setIsTheCfo(false); setCanSeeForgiveness(false) })
  }, [])

  // ADH Settlement Runs — Health or Finance only (CFO 2026-09-13). The screen
  // lists claim numbers and settlement amounts for identifiable medical
  // claims. Probe the SAME gate the endpoint enforces, so nobody clicks the
  // menu into a 403. Probe loading -> hidden (no flash). Hiding is not the
  // control; healthcare/claims_settlement_views.py is.
  const [canAccessAdhSettlements, setCanAccessAdhSettlements] = useState<boolean | undefined>(undefined)
  useEffect(() => {
    apiFetch<{ allowed: boolean }>('/health/adh-settlements/access/')
      .then(r => setCanAccessAdhSettlements(!!r.allowed))
      .catch(() => setCanAccessAdhSettlements(false))
  }, [])

  useEffect(() => {
    const stored = localStorage.getItem('alpha_user')
    if (stored) setUsername(stored)
    getMe().then(setMe).catch(() => setMe(null))
  }, [])

  // CFO directive 2026-06-25 ("stop the menu jumping"): navigating closes the
  // mobile drawer + user menu but KEEPS the module the user opened — no
  // auto-jump. The panel still auto-follows the route until the user pins one
  // (pinnedModule stays null), then stays where they put it.
  useEffect(() => {
    setMobileOpen(false)
    setUserMenuOpen(false)
  }, [pathname])

  // Close the user-chip menu on outside click.
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    if (userMenuOpen) {
      document.addEventListener('mousedown', onDocClick)
      return () => document.removeEventListener('mousedown', onDocClick)
    }
  }, [userMenuOpen])

  const isExp = !collapsed || mobileOpen

  function isActive(href: string): boolean {
    // Sub-header items use href='#' as a placeholder — never an active route.
    if (!href || href === '#') return false
    // Strip query params for comparison
    const base = href.split('?')[0]
    if (base === '/') return pathname === '/'
    return pathname === base || pathname.startsWith(base + '/')
  }

  // ─── Modules (derived, RBAC-gated exactly like the old flat sidebar) ──────

  // The full placement logic (which domain/section each item renders under,
  // every icon, every gate) lives in buildNavModules() — see lib/navModules.ts
  // for the reasoning block (CFO be335dfe + 3f2b2411, M-NAV-DOMAINS 19-Sep-2026).
  // This component only supplies the live permission probes.
  const modules: NavModule[] = useMemo(() => buildNavModules({
    isTheCfo, canSeeForgiveness, me, canAccessHris, canSelfServeHris, canAccessSalvage,
    canAccessDpa, canAccessCompliance, canAccessSecurity, canReadSpeakerFeedback,
    planVisible, canAccessAdhSettlements, canManageUnicoin,
  } as NavGates),
    // planVisible gates the AD Insurtech plan links inside buildNavModules, so it
    // MUST be a dependency. Without it the nav was built once on the first paint —
    // before the company context resolves, when selectedId and selected are both
    // null, which reads as the "All companies" group view and shows the links.
    // The page guard then correctly refused to render plan content under ADIC
    // while the sidebar kept offering the links for the rest of the session, and
    // switching TO AD Insurtech never revealed them either.
    // Bug 4c339e6b, Ontlametse Mogomotsi 2026-08-06.
    [isTheCfo, canSeeForgiveness, me, canAccessHris, canSelfServeHris, canAccessSalvage, canAccessDpa, canAccessCompliance,
      canAccessSecurity, canReadSpeakerFeedback, planVisible, canAccessAdhSettlements])

  // BUG 30f69c69 (Oprah 2026-06-26): two sidebar items highlighted at once on
  // nested routes. isActive() prefix-matches, so on /banking/realpay/... BOTH
  // "Bank Reconciliation" (/banking) and "RealPay" (/banking/realpay) lit up.
  // Fix: only the SINGLE most-specific (longest) matching href is active.
  const deepestActiveHref = useMemo(() => {
    const hrefs: string[] = []
    const walk = (items: NavItem[]) => items.forEach(it => {
      if (it.href && it.href !== '#' && !it.header) hrefs.push(it.href.split('?')[0])
    })
    modules.forEach(m => m.sections.forEach(s => walk(s.items)))
    walk(hrisSelfServiceItems); walk(helpItems); walk(homeItems); walk(myWorkItems)
    walk(financeSelfServiceItems); walk(dataAiItems); walk(platformOpsItems); walk(nexusItems)
    return deepestHref(hrefs, pathname)
  }, [modules, pathname])

  // The CURRENT ROUTE is authoritative (M-PEOPLE, 19-Sep-2026): the module that
  // opens is the one holding the single most-specific matching link. Before,
  // the FIRST module with any prefix match won — so on /hris/profile the panel
  // could open the HR admin menu because '/hris' (HRIS Home) prefix-matched.
  const routeModuleKey = useMemo(() => {
    const exact = routeModule(modules, deepestActiveHref)
    if (exact) return exact
    // The page is in no visible module (e.g. an admin login with no staff
    // record on /hris/profile): fall back to the old prefix rule rather than
    // dropping to Overview (Opus judge, 19-Sep).
    for (const m of modules) {
      for (const sec of m.sections) {
        if (sec.items.some(it => isActive(it.href))) return m.key
      }
    }
    return null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modules, deepestActiveHref, pathname])

  const activeKey = pinnedModule ?? routeModuleKey ?? 'overview'
  const activeModule = modules.find(m => m.key === activeKey)
    || modules.find(m => m.key === routeModuleKey) || modules[0]

  // A page listed twice in one module (e.g. /hris/leave as "My Leave" and
  // "Leave Admin") must light ONE row: the first occurrence, in panel order.
  const activeItemKey = useMemo(() => activeRowKey(activeModule.sections, deepestActiveHref),
    [activeModule, deepestActiveHref])

  // BUG 13c0b4b0 (Oprah 2026-06-26): the sub-sections inside a module (Banks,
  // Collections, Petty Cash, …) all rendered expanded at once — a very long,
  // hard-to-scan panel. Accordion them: one section open at a time, the section
  // holding the current page auto-opens, the rest collapse behind a ▸.
  const [openSection, setOpenSection] = useState<string | null>(null)
  const activeSectionTitle = useMemo(() => {
    for (const s of activeModule.sections) {
      if (s.items.some(it => it.href && it.href !== '#' && it.href.split('?')[0] === deepestActiveHref)) {
        return s.title
      }
    }
    return activeModule.sections[0]?.title ?? null
  }, [activeModule, deepestActiveHref])
  // Reset the manual toggle when the module changes so the new module's active
  // section opens (a title from the old module would otherwise leave all closed).
  useEffect(() => { setOpenSection(null) }, [activeKey])

  function onRailClick(key: string) {
    // CFO directive 2026-06-25 — click the already-open module again to COLLAPSE
    // the panel (click-to-toggle), instead of it only ever opening.
    if (key === activeKey && !collapsed && !mobileOpen) {
      onToggle()
      return
    }
    setPinnedModule(key)
    // Bug bc371a49 (CFO 2026-09-01): a single-item module (Commissions, Alpha
    // Rooms, …) must navigate straight to its page on a rail click. Before this
    // the rail only pinned the module and opened a one-row panel, so clicking
    // "Commissions" looked dead — the highlight moved but the route never
    // changed and the content stayed on whatever page you were on.
    const mod = modules.find(m => m.key === key)
    const navItems = mod
      ? mod.sections.flatMap(s => s.items).filter(it => it.href && it.href !== '#' && !it.external)
      : []
    if (navItems.length === 1 && pathname.split('?')[0] !== navItems[0].href.split('?')[0]) {
      router.push(navItems[0].href)
    }
    // Collapsed rail: choosing a module re-opens the panel so the links are
    // actually reachable (the rail alone only carries module-level icons).
    if (collapsed && !mobileOpen) onToggle()
  }

  async function handleLogout() {
    // Local cleanup first so /login can't auto-route us back to /dashboard
    // before the MSAL redirect fires. removeToken() drops the alpha_token
    // sentinel + the me/companies caches; alpha_user is the display name.
    removeToken()
    localStorage.removeItem('alpha_user')
    localStorage.removeItem('alpha_company_id')

    // SSO logout: dynamically import so the MSAL bundle isn't pulled into
    // every page that renders the sidebar. signOut() calls logoutRedirect
    // when SSO is configured (ends the Microsoft session too) and falls
    // back to a hard nav to /login?signedout=1 otherwise. Without this
    // step the cached MSAL account survives, /login auto-routes to /,
    // and the user appears stuck on the dashboard after clicking Logout.
    try {
      const { signOut } = await import('@/auth/msal')
      await signOut()
    } catch {
      router.push('/login?signedout=1')
    }
  }

  function handleNav() {
    setMobileOpen(false)
  }

  // BUG-013/020 (Oprah 2026-06-05): the footer showed empty username -> "Admin"
  // while Tasks/ARIA showed the real signed-in user. Resolve the real name from
  // the /me profile (full_name -> username) so it's consistent everywhere.
  // 2026-07-25: the fallback below used to be the literal word "Admin", so an
  // ordinary finance-manager account was labelled Admin whenever /me hadn't
  // loaded. Never fall back to something that reads like a role or a rank.
  const realName = (me as any)?.full_name || me?.username || username || ''
  const displayName = realName || 'Signed in'
  const initials = realName ? realName.slice(0, 2).toUpperCase() : '··'

  const accentSoft = withAlpha(theme.orange, 0.14)

  // ─── Sub-renderers ─────────────────────────────────────────────────────────

  function renderItem(item: NavItem, sectionTitle?: string) {
    // Active = the single most-specific matching route (BUG 30f69c69), not every
    // prefix — and only its first listing when the same page appears twice.
    const active = item.href !== '#' && item.href.split('?')[0] === deepestActiveHref
      && (sectionTitle === undefined || activeItemKey === `${sectionTitle}|${item.href}|${item.label}`)

    const Icon = item.icon
    const className = cn(
      'flex items-center gap-2.5 h-9 rounded-lg px-3 transition-colors duration-150 group',
      active ? 'text-white' : 'text-white/70 hover:bg-white/[0.04] hover:text-white',
    )
    const style = {
      borderLeft: `3px solid ${active ? theme.orange : 'transparent'}`,
      background: active ? accentSoft : undefined,
    }
    const inner = (
      <>
        <Icon
          className="flex-shrink-0 w-[18px] h-[18px]"
          style={active ? { color: theme.orange } : undefined}
          strokeWidth={1.5}
        />
        <span
          className="text-sm font-medium truncate"
          style={active ? { color: theme.orange } : undefined}
        >
          {item.label}
        </span>
      </>
    )
    // External items (e.g. /claims-po — the Claims PO app served same-origin
    // behind omni's Caddy, not a Next route) must be a full-page anchor; a
    // Next <Link> would client-route and 404.
    if (item.external) {
      return (
        <a key={item.href + item.label} href={item.href} onClick={handleNav}
           title={'Opens ' + item.label}
           className={className} style={style}>
          {inner}
        </a>
      )
    }
    return (
      <Link key={item.href + item.label} href={item.href} onClick={handleNav}
            className={className} style={style}>
        {inner}
      </Link>
    )
  }

  function renderSection(section: NavSection) {
    // Accordion only when the module has more than one section. A lone section
    // (e.g. Overview) stays always-open — collapsing the only nav helps no one.
    const accordion = activeModule.sections.length > 1
    if (!accordion) {
      return (
        <div key={section.title}>
          <div className="px-3 pt-4 pb-1.5 text-[10.5px] uppercase tracking-[0.18em] font-semibold text-white/35 select-none">
            {section.title}
          </div>
          <div className="space-y-0.5">
            {section.items.map(item => renderItem(item, section.title))}
          </div>
        </div>
      )
    }
    const isOpen = (openSection ?? activeSectionTitle) === section.title
    return (
      <div key={section.title}>
        <button
          type="button"
          onClick={() => setOpenSection(isOpen ? null : section.title)}
          className="w-full flex items-center justify-between px-3 pt-4 pb-1.5 text-[10.5px] uppercase tracking-[0.18em] font-semibold text-white/35 hover:text-white/60 transition-colors select-none"
        >
          <span>{section.title}</span>
          {isOpen
            ? <ChevronDown className="w-3 h-3 flex-shrink-0" />
            : <ChevronRight className="w-3 h-3 flex-shrink-0" />}
        </button>
        {isOpen && (
          <div className="space-y-0.5">
            {section.items.map(item => renderItem(item, section.title))}
          </div>
        )}
      </div>
    )
  }

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <>
      {/* Mobile hamburger */}
      <button
        className="fixed left-4 z-50 md:hidden p-2 rounded-lg text-white"
        // `top` clears the iOS status-bar / notch safe area. In an installed
        // (Add to Home Screen) PWA with viewport-fit=cover, env(safe-area-inset-top)
        // is ~47px, and the old fixed top-4 (16px) put this button UNDER the status
        // bar, where iOS eats the tap — so the menu was unopenable and the app stuck
        // on the dashboard (reported from a home-screen install, 2026-08-12). In
        // Safari / on desktop the inset is 0, so this stays at 1rem — no change there.
        style={{ background: theme.sidebar, top: 'calc(env(safe-area-inset-top, 0px) + 1rem)' }}
        onClick={() => setMobileOpen(!mobileOpen)}
        aria-label="Toggle menu"
      >
        {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar — icon rail + contextual panel (redesign pack 02) */}
      <aside
        data-omni-sidebar
        className={cn(
          'fixed left-0 top-0 h-full z-40 flex-row',
          'hidden md:flex',
          mobileOpen && '!flex',
        )}
        style={{
          background: theme.sidebar,
          width: mobileOpen ? SIDEBAR_FULL_W : collapsed ? SIDEBAR_RAIL_W : SIDEBAR_FULL_W,
          transition: 'width 0.2s ease',
        }}
      >
        {/* ── Icon rail ───────────────────────────────────────────────── */}
        <div
          className="flex flex-col items-center flex-shrink-0 h-full"
          style={{
            width: SIDEBAR_RAIL_W,
            background: 'rgba(0,0,0,0.28)',
            borderRight: '1px solid rgba(255,255,255,0.06)',
          }}
        >
          {/* Brand mark — the REAL Alpha Direct logo, as-is. Full-colour on the
              light (professional) rail; the official white version on the dark
              rails so it reads on navy. Replaces the lone orange swoosh that
              looked like a broken fragment. Shown on the rail only when the
              sidebar is COLLAPSED — when expanded, the panel header carries the
              logo, so it never appears twice (CFO 2026-08-17). */}
          {!isExp && (
            <Link href="/dashboard" onClick={handleNav} className="py-4 flex-shrink-0 flex justify-center w-full" title="omni — Dashboard">
              <Image
                src={themeKey === 'professional' ? '/brand/logo-full-color.png' : '/brand/logo-monotone-white.png'}
                alt="Alpha Direct"
                width={200}
                height={99}
                className="w-14 h-auto object-contain"
                priority
              />
            </Link>
          )}

          {/* Module tiles */}
          <div className="flex-1 w-full flex flex-col items-center gap-1.5 py-2 overflow-y-auto overflow-x-hidden">
            {modules.map(m => {
              const Icon = m.icon
              const moduleActive = m.key === activeKey
              return (
                <button
                  key={m.key}
                  onClick={() => onRailClick(m.key)}
                  title={m.label}
                  aria-label={m.label}
                  aria-pressed={moduleActive}
                  data-omni-active={moduleActive ? '' : undefined}
                  className={cn(
                    'w-[84px] rounded-xl flex flex-col items-center justify-start gap-1 flex-shrink-0',
                    'px-1 pt-2 pb-1.5 transition-all duration-150',
                    moduleActive ? 'text-white' : 'text-white/60 hover:text-white hover:bg-white/[0.06]',
                  )}
                  style={moduleActive ? {
                    background: theme.orange,
                    boxShadow: `0 0 0 1px ${withAlpha(theme.orange, 0.35)}, 0 8px 22px -8px ${withAlpha(theme.orange, 0.65)}`,
                  } : undefined}
                >
                  <Icon className="w-5 h-5 flex-shrink-0" strokeWidth={1.6} />
                  <span
                    className="w-full text-center leading-[1.15] font-semibold"
                    style={{ fontSize: 9.5, letterSpacing: '0.01em', hyphens: 'auto' }}
                  >
                    {m.short || m.label}
                  </span>
                </button>
              )
            })}
          </div>

          {/* Collapsed-rail user affordances — when the contextual panel (which
              holds the user chip + Logout) is hidden, keep the avatar and a
              Logout control reachable on the rail itself. Without this, a
              collapsed sidebar has no path to log out (parity with the old
              always-rendered bottom section). */}
          {!isExp && (
            <div className="flex flex-col items-center gap-1.5 mb-1.5 flex-shrink-0">
              <div className="relative" title={`${displayName} — ${me?.title || 'Alpha Direct'}`}>
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ background: accentSoft, border: `1px solid ${withAlpha(theme.orange, 0.35)}` }}
                >
                  <span className="text-xs font-bold" style={{ color: theme.orange }}>{initials}</span>
                </div>
                <span
                  className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full"
                  style={{ background: '#34D399', border: `2px solid ${theme.sidebar}` }}
                  aria-hidden="true"
                />
              </div>
              <button
                onClick={handleLogout}
                className="w-11 h-9 rounded-lg flex items-center justify-center text-white/40 hover:text-red-400 hover:bg-white/[0.06] transition-colors"
                aria-label="Logout"
                title="Logout"
              >
                <LogOut className="w-4 h-4" strokeWidth={1.5} />
              </button>
            </div>
          )}

          {/* Collapse chevron — desktop only */}
          <button
            onClick={onToggle}
            className="hidden md:flex w-11 h-10 mb-3 rounded-lg items-center justify-center text-white/35 hover:text-white/70 hover:bg-white/[0.06] transition-colors flex-shrink-0"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
          </button>
        </div>

        {/* ── Contextual panel ────────────────────────────────────────── */}
        {isExp && (
          <div
            className="flex flex-col flex-1 min-w-0 h-full"
            style={{ background: 'linear-gradient(180deg, rgba(255,255,255,0.035) 0%, rgba(255,255,255,0.01) 100%)' }}
          >
            {/* Lockup + active module eyebrow */}
            <div
              className="px-5 pt-4 pb-3 flex-shrink-0"
              style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}
            >
              {/* The real Alpha Direct logo, as-is, on every theme: full-colour
                  on the light (professional) sidebar, the official white version
                  on the dark sidebars (default / fun / heavenly) so it reads on
                  navy. Replaces the old "OMNI" text (CFO 2026-08-17). */}
              <Link href="/dashboard" onClick={handleNav} title="omni — Dashboard" className="inline-block">
                <Image
                  src={themeKey === 'professional' ? '/brand/logo-full-color.png' : '/brand/logo-monotone-white.png'}
                  alt="Alpha Direct"
                  width={210}
                  height={104}
                  priority
                  className="h-[108px] w-auto object-contain object-left"
                />
              </Link>
              <p className="mt-1.5 text-[10px] uppercase tracking-[0.22em] font-semibold" style={{ color: theme.orange }}>
                {activeModule.label}
              </p>
            </div>

            {/* Sections */}
            <nav aria-label="Main navigation" className="flex-1 px-2.5 pb-3 overflow-y-auto overflow-x-hidden">
              {/* CFO directive 2026-06-25 — a visible search that fires the
                  existing global command palette (CommandPalette.tsx listens
                  for 'omni:cmdk'); type to jump to any of the ~194 pages. */}
              <button
                onClick={() => window.dispatchEvent(new Event('omni:cmdk'))}
                className="mt-3 mb-1.5 flex items-center gap-2 h-9 rounded-lg px-3 w-full text-left text-white/60 hover:text-white hover:bg-white/[0.06] transition-colors border border-white/10"
                title="Search — jump to any page"
              >
                <Search className="w-4 h-4 flex-shrink-0" strokeWidth={1.5} />
                <span className="text-sm">Search… jump to any page</span>
                <span className="ml-auto text-[10px] text-white/40 border border-white/15 rounded px-1.5 py-0.5">⌘K</span>
              </button>
              {activeModule.sections.map(section => renderSection(section))}
            </nav>

            {/* User chip — pinned bottom (replaces the old mid-sidebar block) */}
            <div
              ref={userMenuRef}
              className="relative flex-shrink-0 p-2.5"
              style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}
            >
              {userMenuOpen && (
                <div
                  className="absolute left-2.5 right-2.5 bottom-[64px] rounded-xl overflow-hidden py-1"
                  style={{
                    background: theme.sidebar,
                    border: '1px solid rgba(255,255,255,0.12)',
                    boxShadow: '0 18px 40px -12px rgba(0,0,0,0.6)',
                    zIndex: 50,
                  }}
                >
                  <Link
                    href="/help"
                    onClick={() => { setUserMenuOpen(false); handleNav() }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-white/80 hover:bg-white/[0.06] hover:text-white transition-colors"
                  >
                    <BookOpen className="w-4 h-4" strokeWidth={1.5} />
                    <span>User Manual</span>
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-white/80 hover:bg-white/[0.06] hover:text-red-400 transition-colors"
                  >
                    <LogOut className="w-4 h-4" strokeWidth={1.5} />
                    <span>Logout</span>
                  </button>
                </div>
              )}
              <button
                onClick={() => setUserMenuOpen(o => !o)}
                className="w-full flex items-center gap-2.5 rounded-xl px-2.5 py-2 hover:bg-white/[0.05] transition-colors text-left"
                aria-haspopup="menu"
                aria-expanded={userMenuOpen}
              >
                <div className="relative flex-shrink-0">
                  <div
                    className="w-9 h-9 rounded-full flex items-center justify-center"
                    style={{ background: accentSoft, border: `1px solid ${withAlpha(theme.orange, 0.35)}` }}
                  >
                    <span className="text-xs font-bold" style={{ color: theme.orange }}>{initials}</span>
                  </div>
                  <span
                    className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full"
                    style={{ background: '#34D399', border: `2px solid ${theme.sidebar}` }}
                    aria-hidden="true"
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-white truncate leading-tight">{displayName}</p>
                  <p className="text-[11px] text-white/40 truncate">{me?.title || 'Alpha Direct'}</p>
                </div>
                <ChevronDown
                  className={cn('w-4 h-4 text-white/35 flex-shrink-0 transition-transform', userMenuOpen && 'rotate-180')}
                />
              </button>
            </div>
          </div>
        )}
      </aside>
    </>
  )
}
