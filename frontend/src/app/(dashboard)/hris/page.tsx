'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useHrisAccess, useHrisCan, useHrisSelfService } from '@/hooks/useHrisAccess'
import {
  Users,
  Users as UsersIcon,
  Calendar,
  Bell,
  BarChart3,
  UserCircle,
  Award,
  Grid3x3,
  GitBranch,
  Compass,
  Sparkle,
  Target,
  TrendingUp,
  Wallet,
  FileText,
  Gift,
  Sparkles,
  Building2,
  ExternalLink,
  ArrowUpRight,
  Activity,
  Cake,
  PartyPopper,
  MapPin,
  Briefcase,
  type LucideIcon,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { AIInsightRibbon } from '@/components/AIInsightRibbon'
import { useTheme } from '@/contexts/ThemeContext'
import { getMe, getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import { FunModeGalaxy, useFunModeKonami } from '@/components/hris/FunModeGalaxy'
import FeatureAdoptionCard from '@/components/hris/FeatureAdoptionCard'
import { authedHrisFetch } from './_shared'

async function fetchHrisEmployees(): Promise<{ employees: Employee[] }> {
  const headers: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const bearer = await acquireApiToken()
      if (bearer) headers['Authorization'] = `Bearer ${bearer}`
    }
  } catch {}
  if (!headers['Authorization']) {
    const t = getToken()
    if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
  }
  // CFO directive 2026-05-18 (multi-entity audit): forward the topbar
  // company selection so the HRIS list scopes per entity.
  const company = typeof window !== 'undefined' ? localStorage.getItem('alpha_company_id') : null
  const qs = company ? `?company=${encodeURIComponent(company)}` : ''
  const r = await fetch(`/hris/api/employees/${qs}`, { credentials: 'include', headers })
  if (!r.ok) return { employees: [] }
  return r.json()
}

interface Employee {
  id: number
  nm: string
  ps: string
  dp: string
  gn: string
  age: number
  loc: string
  hired: string
  img?: string
  salary: number
}

interface EmployeesResponse {
  count: number
  employees: Employee[]
}

function greetingFor(hour: number): string {
  if (hour < 5)  return 'Burning the midnight oil'
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  if (hour < 21) return 'Good evening'
  return 'Working late'
}

function yearsBetween(iso: string, now = new Date()): number {
  if (!iso) return 0
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 0
  return (now.getTime() - d.getTime()) / (365.25 * 86400000)
}

function useCountUp(target: number, duration = 1400): number {
  const [val, setVal] = useState(0)
  useEffect(() => {
    if (!target) { setVal(0); return }
    const start = performance.now()
    let raf = 0
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(Math.round(eased * target))
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return val
}

function useLiveClock(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

export default function HRISLandingPage() {
  const { theme } = useTheme()
  const router = useRouter()
  const hrisAllowed = useHrisAccess()   // undefined while loading, true/false after
  const selfService = useHrisSelfService()  // every employee holds ESS
  const [employees, setEmployees] = useState<Employee[]>([])
  const [firstName, setFirstName] = useState<string>('')
  // Daily leave summary (CFO 2026-07-15) — manager/HR tier only.
  const canViewLeaveSummary = useHrisCan('view_team')
  const [leaveSummary, setLeaveSummary] = useState<{ requested_today_count: number; approved_today_count: number } | null>(null)
  const now = useLiveClock()
  const [funMode, setFunMode] = useState(false)
  useFunModeKonami(() => setFunMode(true))

  // Non-whitelisted users don't see the full HR hub — but every employee has
  // Employee Self-Service (their own profile / payslips / leave). Send them to
  // their own page instead of bouncing them out (CFO 2026-07-09: staff reported
  // a "restriction after logging in" — the hub kicked them to /dashboard even
  // though My Profile/Payslips/Leave are open to them). Wait for BOTH probes
  // so we don't redirect before self-service resolves.
  useEffect(() => {
    if (hrisAllowed !== false) return
    if (selfService === undefined) return          // still loading
    router.replace(selfService ? '/hris/profile' : '/dashboard')
  }, [hrisAllowed, selfService, router])

  useEffect(() => {
    if (hrisAllowed !== true) return
    const load = () => {
      fetchHrisEmployees()
        .then(d => setEmployees(Array.isArray(d.employees) ? d.employees : []))
        .catch(() => setEmployees([]))
    }
    load()
    getMe().then(me => setFirstName(me.first_name || '')).catch(() => {})
    // Bug 07c1c74a: refetch the headcount stats when the entity is switched.
    window.addEventListener('alpha-company-changed', load)
    return () => window.removeEventListener('alpha-company-changed', load)
  }, [hrisAllowed])

  useEffect(() => {
    if (hrisAllowed !== true || canViewLeaveSummary !== true) return
    authedHrisFetch('/hris/api/leave-daily-summary/')
      .then(async r => { if (r.ok) setLeaveSummary(await r.json()) })
      .catch(() => { /* summary card is a bonus — never break the dashboard */ })
  }, [hrisAllowed, canViewLeaveSummary])

  // Hooks must fire on every render in the same order; the access-gate
  // short-circuit moved below this useMemo block (CFO directive 2026-05-18
  // — React error #310 fired when the early return came before useMemo +
  // useCountUp). The stats memo is safe to compute with employees=[]
  // because every branch returns valid numbers.
  const stats = useMemo(() => {
    const total = employees.length
    const female = employees.filter(e => /^f/i.test(e.gn)).length
    const male = employees.filter(e => /^m/i.test(e.gn)).length
    const depts = new Set(employees.map(e => e.dp).filter(Boolean))
    const locs = new Set(employees.map(e => e.loc).filter(Boolean))
    const ages = employees.map(e => e.age).filter(a => a > 0)
    const avgAge = ages.length ? Math.round(ages.reduce((a, b) => a + b, 0) / ages.length) : 0
    const tenures = employees.map(e => yearsBetween(e.hired)).filter(t => t > 0)
    const avgTenure = tenures.length ? +(tenures.reduce((a, b) => a + b, 0) / tenures.length).toFixed(1) : 0
    const salaries = employees.map(e => e.salary).filter(s => s > 0)
    const totalPayroll = salaries.reduce((a, b) => a + b, 0)

    const ageBuckets = [0, 0, 0, 0]
    for (const a of ages) {
      if (a < 30) ageBuckets[0]++
      else if (a < 40) ageBuckets[1]++
      else if (a < 50) ageBuckets[2]++
      else ageBuckets[3]++
    }

    const deptCounts = new Map<string, number>()
    for (const e of employees) {
      const d = e.dp || 'Unassigned'
      deptCounts.set(d, (deptCounts.get(d) || 0) + 1)
    }
    const depList = Array.from(deptCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 6)

    const locCounts = new Map<string, number>()
    for (const e of employees) {
      if (e.loc) locCounts.set(e.loc, (locCounts.get(e.loc) || 0) + 1)
    }
    const locList = Array.from(locCounts.entries()).sort((a, b) => b[1] - a[1])

    const recentHires = [...employees]
      .filter(e => e.hired)
      .sort((a, b) => b.hired.localeCompare(a.hired))
      .slice(0, 5)

    return {
      total, female, male, fPct: total ? Math.round(female * 100 / total) : 0,
      deptCount: depts.size, locCount: locs.size,
      avgAge, avgTenure, totalPayroll,
      ageBuckets, depList, locList, recentHires,
    }
  }, [employees])

  const totalAnim = useCountUp(stats.total)
  const deptAnim  = useCountUp(stats.deptCount)
  const locAnim   = useCountUp(stats.locCount)

  const dateStr = now.toLocaleDateString('en-BW', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
  const timeStr = now.toLocaleTimeString('en-BW', { hour12: false })
  const greeting = greetingFor(now.getHours())
  const fname = firstName || 'Team'

  // Block render until the probe resolves AND the user is allowed.
  // Placed AFTER every hook call so the hook order stays stable across
  // renders (see comment above the stats useMemo).
  if (hrisAllowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen relative" style={{ background: theme.bg }}>
      {funMode && (
        <FunModeGalaxy
          open={funMode}
          onClose={() => setFunMode(false)}
          initials={employees.map(e => (e.nm || '').split(' ').map((p: string) => p[0]).slice(0, 2).join('').toUpperCase()).filter(Boolean)}
        />
      )}
      <TopBar title="HRIS" />

      <div className="p-4 lg:p-6 space-y-5 max-w-[1500px] mx-auto">

        {/* CFO-directed PIP alert — shows for the subject + named viewers only. */}
        <PipBanner />

        {/* New-feature sign-off tasks (CFO 2026-07-21) — HR team must open each
            new feature and press "I am happy with this feature". */}
        <FeatureAdoptionCard />

        {/* ─── Hero — light aurora command centre ──────────────────────── */}
        <div className="relative overflow-hidden rounded-2xl"
             style={{ background: '#FFFFFF', border: '1px solid rgba(13,27,42,0.06)', boxShadow: '0 10px 40px -12px rgba(13,27,42,0.12)' }}>
          {/* Aurora blob — right side, pastel */}
          <div className="absolute -top-40 -right-32 w-[44rem] h-[44rem] rounded-full pointer-events-none"
               style={{
                 background: `radial-gradient(circle at 30% 30%, rgba(240,127,0,0.18) 0%, transparent 55%),
                              radial-gradient(circle at 70% 60%, rgba(168,85,247,0.18) 0%, transparent 55%),
                              radial-gradient(circle at 50% 80%, rgba(0,240,255,0.18) 0%, transparent 60%)`,
                 filter: 'blur(40px)',
                 animation: 'auroraDriftA 16s ease-in-out infinite',
               }} />
          <div className="absolute -bottom-32 -left-20 w-[28rem] h-[28rem] rounded-full pointer-events-none"
               style={{
                 background: 'radial-gradient(circle, rgba(240,127,0,0.10), transparent 70%)',
                 filter: 'blur(30px)',
                 animation: 'auroraDriftB 22s ease-in-out infinite',
               }} />

          {/* Content */}
          <div className="relative px-6 py-10 lg:px-12 lg:py-14" style={{ color: '#0D1B2A' }}>
            <div className="flex items-center justify-between mb-8 flex-wrap gap-3">
              <p className="text-[11px] uppercase tracking-[0.25em] font-semibold" style={{ color: '#0D1B2A', opacity: 0.55 }}>{dateStr}</p>
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-full"
                   style={{ background: 'rgba(13,27,42,0.04)', border: '1px solid rgba(13,27,42,0.08)' }}>
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: '#00C9B7' }} />
                <span className="font-mono text-xs tabular-nums" style={{ color: '#0D1B2A', opacity: 0.7 }}>{timeStr} CAT</span>
              </div>
            </div>

            <h1 className="font-display-tight text-5xl sm:text-6xl lg:text-[5.5rem] font-bold leading-[0.92]" style={{ color: '#0D1B2A' }}>
              <span style={{ opacity: 0.92 }}>{greeting},</span>
              <br />
              <span
                className="italic bg-clip-text text-transparent"
                style={{
                  backgroundImage: 'linear-gradient(120deg, #0D1B2A 0%, #F07F00 60%, #FF9A2E 100%)',
                }}
              >
                {fname}.
              </span>
            </h1>

            <p className="font-display mt-6 text-lg lg:text-xl max-w-2xl leading-relaxed italic" style={{ color: '#374151' }}>
              Welcome to your command centre. {stats.total} of us, building Botswana&apos;s smartest insurer.
            </p>

            <div className="mt-6">
              <AIInsightRibbon ctx="hris" />
            </div>

            <div className="mt-10 grid grid-cols-3 gap-3 sm:gap-4 max-w-2xl">
              <StatOrb label="Humans" value={totalAnim} icon={Users} />
              <StatOrb label="Teams"  value={deptAnim}  icon={Briefcase} />
              <StatOrb label="Cities" value={locAnim}   icon={MapPin} />
            </div>
          </div>
        </div>

        {/* ─── Pulse strip ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <PulseCard
            theme={theme}
            icon={TrendingUp}
            label="Avg tenure"
            value={`${stats.avgTenure}y`}
            sub="Sticky team"
            tone="emerald"
          />
          <PulseCard
            theme={theme}
            icon={Activity}
            label="Gender balance"
            value={`${stats.fPct}% F`}
            sub={`${stats.female}F · ${stats.male}M`}
            tone="violet"
            progress={stats.fPct}
          />
          <PulseCard
            theme={theme}
            icon={Wallet}
            label="Monthly payroll"
            value={`P${Math.round(stats.totalPayroll / 1000)}K`}
            sub="across the group"
            tone="amber"
          />
          <PulseCard
            theme={theme}
            icon={Cake}
            label="Average age"
            value={stats.avgAge.toString()}
            sub="years young"
            tone="cyan"
          />
        </div>

        {/* ─── Leave today (CFO 2026-07-15) — manager/HR tier only ───────── */}
        {canViewLeaveSummary && leaveSummary && (
          <div className="grid grid-cols-2 gap-3 sm:gap-4">
            <PulseCard
              theme={theme}
              icon={Calendar}
              label="Leave requested today"
              value={leaveSummary.requested_today_count.toString()}
              sub="new applications"
              tone="amber"
            />
            <PulseCard
              theme={theme}
              icon={Calendar}
              label="Leave approved today"
              value={leaveSummary.approved_today_count.toString()}
              sub="decisions made"
              tone="emerald"
            />
          </div>
        )}

        {/* ─── Mission Control: charts + my day ────────────────────────── */}
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-2 gap-4">
            <GenderCard theme={theme} female={stats.female} male={stats.male} fPct={stats.fPct} />
            <AgeHistogramCard theme={theme} buckets={stats.ageBuckets} total={stats.total} />
            <DepartmentBars theme={theme} list={stats.depList} total={stats.total} />
            <LocationCard theme={theme} list={stats.locList} total={stats.total} />
          </div>
          <div className="space-y-4">
            <PersonalCard theme={theme} firstName={fname} />
            <ActivityFeed theme={theme} hires={stats.recentHires} />
          </div>
        </div>

        {/* ─── Migration banner ─────────────────────────────────────────── */}
        <div
          className="rounded-xl px-5 py-4 border flex items-start gap-3"
          style={{ background: '#FFF7E6', borderColor: '#F4A62333', color: '#0D1B2A' }}
        >
          <Sparkles className="w-5 h-5 mt-0.5 flex-shrink-0" style={{ color: '#F4A623' }} />
          <div className="text-sm">
            <p className="font-semibold mb-0.5">HRIS v2 — migration in progress.</p>
            <p style={{ color: '#374151' }}>
              <strong>HR Analytics</strong> is live in the new Omni interface
              below. Leave, People Directory, Payslip, Rewards and other
              modules still live in the previous HRIS — open them via
              <em> Full HRIS</em>. Each module will move to the Omni interface
              over the coming weeks.
            </p>
          </div>
        </div>

        {/* ─── Jump in — eight native Omni surfaces (Graphiter iframe
              retired 2026-05-18 per CFO directive — every link below is
              a Next.js route that inherits the Omni MSAL session). ──── */}
        <section className="pt-2">
          <SectionLabel theme={theme}>Jump in</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-3">
            <ActionTile
              theme={theme} icon={Calendar}
              title="Apply Leave"
              subtitle="Balance, request, manager approval"
              href="/hris/leave" isInternal
            />
            <ActionTile
              theme={theme} icon={UserCircle}
              title="My Profile"
              subtitle="Identity, grade, compensation"
              href="/hris/profile" isInternal
            />
            <ActionTile
              theme={theme} icon={TrendingUp}
              title="Career Growth"
              subtitle="Ladder, OKRs, learning"
              href="/hris/career" isInternal
            />
            <ActionTile
              theme={theme} icon={Bell}
              title="Alerts & Reminders"
              subtitle="Birthdays, anniversaries, deadlines"
              href="/hris/alerts" isInternal
            />
            <ActionTile
              theme={theme} icon={BarChart3}
              title="Analytics Dashboard"
              subtitle="Headcount, gender, payroll, ISO 30414"
              href="/hr-analytics" isInternal accent
            />
            <ActionTile
              theme={theme} icon={UsersIcon}
              title="People Directory"
              subtitle="79 employees · search by name, role, dept"
              href="/hris/directory" isInternal
            />
            <ActionTile
              theme={theme} icon={Wallet}
              title="Payslips"
              subtitle="Latest payslip + 12-month history"
              href="/hris/payslips" isInternal
            />
            <ActionTile
              theme={theme} icon={FileText}
              title="Letters"
              subtitle="Employment confirmation · manager-signed · letterhead"
              href="/hris/letters" isInternal
            />
            <ActionTile
              theme={theme} icon={Award}
              title="Rewards & Merit"
              subtitle="Performance reviews + recognition"
              href="/hris/rewards" isInternal
            />
            <ActionTile
              theme={theme} icon={UserCircle}
              title="Amendments"
              subtitle="Edit employee data — dual-approved before it applies"
              href="/hris/amendments" isInternal accent
            />
            <ActionTile
              theme={theme} icon={Wallet}
              title="Bank details upload"
              subtitle="Load staff account + branch from the salary file — Dorothy approves"
              href="/hris/bank-upload" isInternal accent
            />
          </div>
        </section>

        {/* ─── Talent Management — Unami TMS Orbit parity (2026-05-26).
              Four surfaces close the gap Unami flagged after comparing the
              live HRIS landing with AlphaDirect_TMS_Orbit.html:
                9-Box Grid · Succession · IDP · AI Readiness            ── */}
        <section className="pt-4">
          <SectionLabel theme={theme}>Talent Management</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-3">
            <ActionTile
              theme={theme} icon={Target}
              title="Development Dialogue (All Employees)"
              subtitle="Every employee's dialogue and the 9-grid — saved in omni (system of record)"
              href="/hris/talent-cockpit" isInternal accent
            />
            <ActionTile
              theme={theme} icon={Grid3x3}
              title="9-Box Talent Grid"
              subtitle="Performance × Potential — HiPOs, gaps, action map"
              href="/hris/ninebox" isInternal accent
            />
            <ActionTile
              theme={theme} icon={GitBranch}
              title="Succession Planning"
              subtitle="Critical-role coverage + ranked successors"
              href="/hris/succession" isInternal
            />
            <ActionTile
              theme={theme} icon={Compass}
              title="Individual Dev Plan"
              subtitle="Competency gaps + 12-month roadmap"
              href="/hris/idp" isInternal
            />
            <ActionTile
              theme={theme} icon={Sparkle}
              title="AI Readiness"
              subtitle="Workforce AI tier + dept rollup"
              href="/hris/ai-readiness" isInternal
            />
          </div>
        </section>

        {/* ─── Manager & Compliance — Unami audit closeout (2026-05-18).
              These three surfaces close the gap the audit flagged:
              full PMS engine, BURS ITW8, and the 3-layer bonus pool. ── */}
        <section className="pt-4">
          <SectionLabel theme={theme}>Manager &amp; Compliance</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-3">
            <ActionTile
              theme={theme} icon={Award}
              title="Performance Assessment"
              subtitle="1-4 scale · Competencies 80% · Values 20% · OKRs"
              href="/hris/assess" isInternal accent
            />
            <ActionTile
              theme={theme} icon={FileText}
              title="ITW8 Tax Certificate"
              subtitle="BURS-format earnings certificate"
              href="/hris/itw8" isInternal
            />
            <ActionTile
              theme={theme} icon={Wallet}
              title="Bonus Simulation"
              subtitle="3-layer pool: Org × Dept × Individual"
              href="/hris/bonus-simulation" isInternal
            />
          </div>
        </section>

      </div>

      {/* Keyframes for hero animations */}
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes auroraDriftA {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50%      { transform: translate(-30px, 20px) scale(1.06); }
        }
        @keyframes auroraDriftB {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50%      { transform: translate(40px, -25px) scale(1.1); }
        }
        @keyframes hrisShine {
          0%   { transform: translateX(-120%) skewX(-20deg); }
          100% { transform: translateX(220%)  skewX(-20deg); }
        }
      ` }} />
    </div>
  )
}

// ─── Building blocks ─────────────────────────────────────────────────────────

// CFO-directed PIP alert. Fetches the caller's visible directed PIP(s); renders
// nothing unless there is one (so it only appears for the subject + named
// viewers). Links through to /hris/pip. CFO directive 2026-07-15.
function PipBanner() {
  const [pips, setPips] = useState<{ id: string; title: string; employee_name: string; is_subject: boolean }[]>([])
  useEffect(() => {
    let live = true
    authedHrisFetch('/hris/api/pips/directed/')
      .then(async r => (r.ok ? r.json() : { pips: [] }))
      .then(d => { if (live) setPips(Array.isArray(d.pips) ? d.pips : []) })
      .catch(() => { if (live) setPips([]) })
    return () => { live = false }
  }, [])
  if (pips.length === 0) return null
  const p = pips[0]
  const mine = p.is_subject
  return (
    <Link href="/hris/pip"
          className="flex items-center justify-between gap-4 rounded-2xl px-5 py-4 group"
          style={{ background: 'linear-gradient(135deg,#7f1d1d,#b91c1c)', color: '#fff' }}>
      <div className="flex items-center gap-3">
        <Bell className="w-5 h-5 flex-shrink-0" />
        <div>
          <div className="font-semibold">
            {mine ? 'Performance Improvement Plan — action required'
                  : `Performance Improvement Plan — ${p.employee_name}`}
          </div>
          <div className="text-xs opacity-85">
            {p.title}{mine ? ' · open it to read the plan and add your explanation' : ' · open to review'}
          </div>
        </div>
      </div>
      <ArrowUpRight className="w-5 h-5 opacity-80 group-hover:opacity-100 flex-shrink-0" />
    </Link>
  )
}

function SectionLabel({ theme, children }: { theme: any; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <h2
        className="text-[11px] font-bold uppercase tracking-[0.18em]"
        style={{ color: theme.t2 }}
      >
        {children}
      </h2>
      <div className="flex-1 h-px" style={{ background: theme.cardBdr }} />
    </div>
  )
}

function StatOrb({ label, value, icon: Icon }: { label: string; value: number; icon: LucideIcon }) {
  return (
    <div className="relative rounded-xl px-4 py-4 backdrop-blur-md overflow-hidden"
         style={{
           background: 'rgba(255,255,255,0.65)',
           border: '1px solid rgba(13,27,42,0.08)',
           boxShadow: '0 8px 24px -8px rgba(13,27,42,0.12), 0 2px 4px -1px rgba(13,27,42,0.06)',
         }}>
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-3.5 h-3.5" strokeWidth={2} style={{ color: '#F07F00' }} />
        <span className="text-[10px] uppercase tracking-widest font-semibold" style={{ color: '#0D1B2A', opacity: 0.6 }}>{label}</span>
      </div>
      <div className="font-display-tight text-5xl sm:text-6xl font-bold tabular-nums" style={{ color: '#0D1B2A' }}>{value}</div>
    </div>
  )
}

type PulseTone = 'emerald' | 'violet' | 'amber' | 'cyan'

const PULSE_TONES: Record<PulseTone, { bg: string; fg: string; bar: string }> = {
  emerald: { bg: '#ECFDF5', fg: '#059669', bar: '#10B981' },
  violet:  { bg: '#F5F3FF', fg: '#7C3AED', bar: '#8B5CF6' },
  amber:   { bg: '#FFFBEB', fg: '#D97706', bar: '#F59E0B' },
  cyan:    { bg: '#ECFEFF', fg: '#0891B2', bar: '#06B6D4' },
}

function PulseCard({
  theme, icon: Icon, label, value, sub, tone, progress,
}: {
  theme: any
  icon: LucideIcon
  label: string
  value: string
  sub: string
  tone: PulseTone
  progress?: number
}) {
  const t = PULSE_TONES[tone]
  return (
    <div
      className="relative rounded-xl p-4 overflow-hidden group"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: t.bg }}>
          <Icon className="w-4 h-4" style={{ color: t.fg }} strokeWidth={2} />
        </div>
        <span className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: theme.t3 }}>
          {label}
        </span>
      </div>
      <div className="font-display-tight text-3xl font-bold tabular-nums" style={{ color: theme.navy }}>
        {value}
      </div>
      <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>{sub}</div>
      {typeof progress === 'number' && (
        <div className="mt-3 h-1 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
          <div
            className="h-full rounded-full transition-all duration-1000"
            style={{ width: `${progress}%`, background: t.bar }}
          />
        </div>
      )}
    </div>
  )
}

function ChartCard({
  theme, title, subtitle, children,
}: {
  theme: any
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div
      className="rounded-xl p-5 overflow-hidden"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}
    >
      <div className="mb-4">
        <h3 className="font-display text-lg font-bold" style={{ color: theme.navy }}>{title}</h3>
        {subtitle && <p className="text-[11px] mt-0.5" style={{ color: theme.t3 }}>{subtitle}</p>}
      </div>
      {children}
    </div>
  )
}

function GenderCard({ theme, female, male, fPct }: { theme: any; female: number; male: number; fPct: number }) {
  const r = 38
  const c = 2 * Math.PI * r
  const fArc = (fPct / 100) * c
  return (
    <ChartCard theme={theme} title="Gender balance" subtitle="Female / Male split">
      <div className="flex items-center gap-5">
        <div className="relative w-28 h-28">
          <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
            <defs>
              <linearGradient id="genGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%"  stopColor="#F97316" />
                <stop offset="100%" stopColor="#FB923C" />
              </linearGradient>
            </defs>
            <circle cx="50" cy="50" r={r} fill="none" stroke="#EEF2FF" strokeWidth="11" />
            <circle
              cx="50" cy="50" r={r} fill="none"
              stroke="url(#genGrad)"
              strokeWidth="11"
              strokeLinecap="round"
              strokeDasharray={`${fArc} ${c}`}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <div className="font-display-tight text-3xl font-bold tabular-nums" style={{ color: theme.navy }}>{fPct}%</div>
            <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>female</div>
          </div>
        </div>
        <div className="flex-1 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: '#F97316' }} />
              <span className="text-sm" style={{ color: theme.text }}>Female</span>
            </div>
            <span className="text-sm font-bold tabular-nums" style={{ color: theme.navy }}>{female}</span>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: '#C7D2FE' }} />
              <span className="text-sm" style={{ color: theme.text }}>Male</span>
            </div>
            <span className="text-sm font-bold tabular-nums" style={{ color: theme.navy }}>{male}</span>
          </div>
        </div>
      </div>
    </ChartCard>
  )
}

function AgeHistogramCard({ theme, buckets, total }: { theme: any; buckets: number[]; total: number }) {
  const labels = ['<30', '30-39', '40-49', '50+']
  const max = Math.max(...buckets, 1)
  return (
    <ChartCard theme={theme} title="Age distribution" subtitle="Years of life on this team">
      <div className="flex items-end justify-between gap-2 h-28">
        {buckets.map((v, i) => {
          const h = (v / max) * 100
          return (
            <div key={i} className="flex-1 flex flex-col items-center justify-end gap-2">
              <div className="text-xs font-bold tabular-nums" style={{ color: theme.navy }}>{v}</div>
              <div className="w-full rounded-md relative overflow-hidden" style={{ height: `${h}%`, minHeight: '8px' }}>
                <div
                  className="absolute inset-0 rounded-md"
                  style={{ background: `linear-gradient(180deg, #818CF8 0%, #6366F1 100%)` }}
                />
              </div>
              <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>{labels[i]}</div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 text-[11px]" style={{ color: theme.t3 }}>
        {total} people across {buckets.filter(b => b > 0).length} age bands
      </div>
    </ChartCard>
  )
}

function DepartmentBars({ theme, list, total }: { theme: any; list: [string, number][]; total: number }) {
  return (
    <ChartCard theme={theme} title="Top departments" subtitle="Where the headcount sits">
      <div className="space-y-2.5">
        {list.map(([name, n]) => {
          const pct = Math.round((n / total) * 100)
          return (
            <div key={name}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs truncate" style={{ color: theme.text }}>{name}</span>
                <span className="text-[11px] font-bold tabular-nums" style={{ color: theme.navy }}>
                  {n} <span style={{ color: theme.t3 }}>· {pct}%</span>
                </span>
              </div>
              <div className="h-1.5 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                <div
                  className="h-full rounded-full"
                  style={{ width: `${pct}%`, background: 'linear-gradient(90deg, #F97316, #FB923C)' }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </ChartCard>
  )
}

function LocationCard({ theme, list, total }: { theme: any; list: [string, number][]; total: number }) {
  return (
    <ChartCard theme={theme} title="Cities we work from" subtitle={`${list.length} locations across Botswana`}>
      <div className="space-y-2">
        {list.map(([city, n]) => {
          const pct = Math.round((n / total) * 100)
          return (
            <div key={city} className="flex items-center gap-3">
              <div className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                   style={{ background: '#ECFEFF' }}>
                <MapPin className="w-3.5 h-3.5" style={{ color: '#0891B2' }} strokeWidth={2} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium truncate" style={{ color: theme.text }}>{city}</span>
                  <span className="text-[11px] font-bold tabular-nums" style={{ color: theme.navy }}>{n}</span>
                </div>
                <div className="h-1 mt-1 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: '#06B6D4' }} />
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </ChartCard>
  )
}

function PersonalCard({ theme, firstName }: { theme: any; firstName: string }) {
  const initials = firstName ? firstName.charAt(0).toUpperCase() : 'U'
  return (
    <div
      className="relative rounded-xl p-5 overflow-hidden"
      style={{
        background: `linear-gradient(135deg, ${theme.navy} 0%, #1F1547 100%)`,
        color: '#fff',
      }}
    >
      <div className="absolute -top-12 -right-12 w-40 h-40 rounded-full blur-2xl pointer-events-none"
           style={{ background: 'radial-gradient(circle, rgba(240,127,0,0.5), transparent 70%)' }} />
      <div className="relative">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-12 h-12 rounded-full flex items-center justify-center font-bold text-lg"
               style={{ background: 'linear-gradient(135deg, #F97316, #FB923C)' }}>
            {initials}
          </div>
          <div>
            <div className="text-xs uppercase tracking-widest opacity-60 font-semibold">Signed in as</div>
            <div className="font-display text-xl font-bold italic">{firstName || 'Welcome'}</div>
          </div>
        </div>
        <div className="space-y-2.5 text-sm">
          <PersonalRow label="Leave balance"  value="21d" />
          <PersonalRow label="Next review"    value="30 Jun" />
          <PersonalRow label="Pending tasks"  value="0" highlight />
        </div>
        <Link
          href="/hris/directory"
          className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold opacity-80 hover:opacity-100 transition-opacity"
        >
          Open the directory <ArrowUpRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </div>
  )
}

function PersonalRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs opacity-60">{label}</span>
      <span className={`text-sm font-bold tabular-nums ${highlight ? 'text-emerald-300' : ''}`}>{value}</span>
    </div>
  )
}

function ActivityFeed({ theme, hires }: { theme: any; hires: Employee[] }) {
  return (
    <ChartCard theme={theme} title="Recent activity" subtitle="Latest movements">
      <div className="space-y-3">
        {hires.length === 0 && (
          <div className="text-xs" style={{ color: theme.t3 }}>No activity yet</div>
        )}
        {hires.map(e => (
          <div key={e.id} className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                 style={{ background: 'linear-gradient(135deg, #6366F1, #8B5CF6)', color: '#fff' }}>
              {e.img || e.nm.split(' ').map(p => p[0]).join('').slice(0, 2)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold truncate" style={{ color: theme.text }}>{e.nm}</div>
              <div className="text-[11px] truncate" style={{ color: theme.t3 }}>{e.ps} · {e.dp}</div>
            </div>
            <YearChip theme={theme}>{e.hired.slice(0, 4)}</YearChip>
          </div>
        ))}
      </div>
    </ChartCard>
  )
}

function YearChip({ theme, children }: { theme: any; children: React.ReactNode }) {
  return (
    <span
      className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider tabular-nums"
      style={{ background: theme.g100, color: theme.t2 }}
    >
      {children}
    </span>
  )
}

function ActionTile({
  theme, icon: Icon, title, subtitle, href, accent = false, isInternal = false,
}: {
  theme: any
  icon: LucideIcon
  title: string
  subtitle: string
  href: string
  accent?: boolean
  isInternal?: boolean
}) {
  return (
    <Link
      href={href}
      target={isInternal ? undefined : '_blank'}
      className="group relative rounded-xl overflow-hidden block transition-transform duration-300 hover:-translate-y-0.5"
      style={{
        background: accent ? `linear-gradient(135deg, ${theme.navy} 0%, #1F1547 100%)` : theme.card,
        border: `1px solid ${accent ? theme.navy : theme.cardBdr}`,
        color: accent ? '#fff' : theme.text,
        boxShadow: theme.cardSh,
      }}
    >
      {/* Hover shine */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div
          className="absolute -inset-y-4 -left-1/2 w-1/3 opacity-0 group-hover:opacity-100"
          style={{
            background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent)',
            animation: 'hrisShine 1.2s ease-in-out forwards',
            animationPlayState: 'paused',
          }}
        />
      </div>

      <div className="relative p-5">
        <div className="flex items-start justify-between mb-6">
          <div
            className="w-11 h-11 rounded-lg flex items-center justify-center"
            style={{
              background: accent ? 'rgba(249,115,22,0.25)' : 'linear-gradient(135deg, #FFF7ED 0%, #FFEDD5 100%)',
            }}
          >
            <Icon
              className="w-5 h-5"
              style={{ color: accent ? '#FB923C' : '#F97316' }}
              strokeWidth={2}
            />
          </div>
          {isInternal ? (
            <span className="text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full"
                  style={{ background: '#ECFDF5', color: '#059669' }}>
              Live
            </span>
          ) : (
            <ExternalLink className="w-4 h-4 opacity-50 group-hover:opacity-100 transition-opacity" />
          )}
        </div>
        <div className="font-display text-xl font-bold">{title}</div>
        <div
          className="text-xs mt-1"
          style={{ color: accent ? 'rgba(255,255,255,0.65)' : theme.t2 }}
        >
          {subtitle}
        </div>
      </div>
    </Link>
  )
}

// ModTile removed 2026-05-17 — it rendered the dozens of duplicate
// /hris/?legacy=1 tiles on the HRIS landing. Replaced with a single
// "Full HRIS" ActionTile + a migration banner. Per-feature React pages
// (Leave first) replace it incrementally in follow-up PRs.
