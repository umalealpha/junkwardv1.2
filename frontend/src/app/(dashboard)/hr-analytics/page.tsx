'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import {
  Users,
  UserCheck,
  Calendar,
  Briefcase,
  Wallet,
  DollarSign,
  TrendingUp,
  MapPin,
  AlertCircle,
  ChevronLeft,
  RefreshCw,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetchRaw } from '@/lib/api'

// Delegates to the shared apiFetchRaw so SSO users get the same Bearer→Token
// acquisition + cold-MSAL retry as the rest of the app. The previous bespoke
// one-shot fetch 401'd when MSAL wasn't ready on mount (HR-analytics bug,
// 2026-06-08 — see lib/api.ts apiFetchRaw + common-mistakes.md §11).
async function authedHrisFetch(path: string): Promise<Response> {
  return apiFetchRaw(path)
}

interface Employee {
  id: number
  nm: string
  ps: string
  dp: string
  company: string
  grade: string
  salary: number
  hired: string
  mg: string
  img: string
  gn: string
  age: number
  loc: string
  grossActual?: number
  netActual?: number
  payeActual?: number
}

interface EmployeesResponse {
  count: number
  employees: Employee[]
}

function fmtNum(n: number): string {
  return new Intl.NumberFormat('en-BW', { maximumFractionDigits: 0 }).format(n)
}

function fmtPula(n: number): string {
  return `P${fmtNum(n)}`
}

function pct(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0
}

function yearsBetween(iso: string, now = new Date()): number {
  if (!iso) return 0
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 0
  return (now.getTime() - d.getTime()) / (365.25 * 86400000)
}

export default function HRISAnalyticsPage() {
  const { theme } = useTheme()
  const router = useRouter()
  const hrisAllowed = useHrisAccess()   // undefined | true | false (CFO directive 2026-05-18)
  const [employees, setEmployees] = useState<Employee[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    setError(null)
    try {
      const res = await authedHrisFetch('/hris/api/employees/')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data: EmployeesResponse = await res.json()
      setEmployees(Array.isArray(data.employees) ? data.employees : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load HR data')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  // Whitelist gate: non-allowed users get bounced to /dashboard
  // before any HR data hits their browser.
  useEffect(() => {
    if (hrisAllowed === false) {
      router.replace('/dashboard')
    }
  }, [hrisAllowed, router])

  useEffect(() => {
    if (hrisAllowed !== true) return
    load()
  }, [hrisAllowed])

  // Whitelist gate moved BELOW the useMemo so hook order is stable
  // across renders (React error #310 fix — CFO directive 2026-05-18).
  const analytics = useMemo(() => {
    const total = employees.length
    const female = employees.filter(e => /^f/i.test(e.gn)).length
    const male = employees.filter(e => /^m/i.test(e.gn)).length
    const fPct = pct(female, total)

    const ages = employees.map(e => e.age).filter(a => a && a > 0)
    const avgAge = ages.length ? Math.round(ages.reduce((a, b) => a + b, 0) / ages.length) : 0

    const tenures = employees.map(e => yearsBetween(e.hired)).filter(t => t > 0)
    const avgTenure = tenures.length
      ? (tenures.reduce((a, b) => a + b, 0) / tenures.length).toFixed(1)
      : '0.0'

    const salaries = employees.map(e => e.salary).filter(s => s && s > 0)
    const avgSalary = salaries.length
      ? Math.round(salaries.reduce((a, b) => a + b, 0) / salaries.length)
      : 0
    const totalPayroll = salaries.reduce((a, b) => a + b, 0)

    const locCounts: Record<string, number> = {}
    for (const e of employees) {
      if (e.loc) locCounts[e.loc] = (locCounts[e.loc] || 0) + 1
    }
    const locs = Object.entries(locCounts).sort((a, b) => b[1] - a[1])

    const ageBuckets = { 'Under 30': 0, '30-39': 0, '40-49': 0, '50+': 0 }
    for (const a of ages) {
      if (a < 30) ageBuckets['Under 30']++
      else if (a < 40) ageBuckets['30-39']++
      else if (a < 50) ageBuckets['40-49']++
      else ageBuckets['50+']++
    }

    const deptCounts: Record<string, number> = {}
    for (const e of employees) {
      const d = e.dp || 'Unassigned'
      deptCounts[d] = (deptCounts[d] || 0) + 1
    }
    const depts = Object.entries(deptCounts).sort((a, b) => b[1] - a[1])

    return {
      total, female, male, fPct,
      avgAge, avgTenure,
      avgSalary, totalPayroll,
      locs, ageBuckets, depts,
    }
  }, [employees])

  const kpis = [
    { label: 'Headcount',  value: fmtNum(analytics.total),            badge: 'Active',                   badgeVariant: 'success' as const, icon: Users,       isOrange: false },
    { label: 'Gender',     value: `${analytics.fPct}% F`,             badge: `${analytics.female}F / ${analytics.male}M`, badgeVariant: 'navy' as const, icon: UserCheck, isOrange: false },
    { label: 'Avg Age',    value: fmtNum(analytics.avgAge),           badge: 'Years',                    badgeVariant: 'info' as const,    icon: Calendar,    isOrange: false },
    { label: 'Avg Tenure', value: `${analytics.avgTenure}y`,          badge: 'Service',                  badgeVariant: 'success' as const, icon: Briefcase,   isOrange: false },
    { label: 'Avg Salary', value: fmtPula(analytics.avgSalary),       badge: 'Monthly',                  badgeVariant: 'navy' as const,    icon: Wallet,      isOrange: false },
    { label: 'Payroll',    value: `${fmtPula(analytics.totalPayroll)}/mo`, badge: 'Total',               badgeVariant: 'orange' as const,  icon: DollarSign,  isOrange: true  },
    { label: 'Succession', value: '100%',                             badge: '9/9 covered',              badgeVariant: 'success' as const, icon: TrendingUp,  isOrange: false },
    { label: 'Locations',  value: fmtNum(analytics.locs.length),      badge: 'Cities',                   badgeVariant: 'info' as const,    icon: MapPin,      isOrange: false },
  ]

  // Whitelist gate (was above useMemo and triggered React error #310).
  if (hrisAllowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="HR Analytics" />

      <div className="p-4 lg:p-6 space-y-4">
        {/* ─── Page header ──────────────────────────────────────────────── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-bold" style={{ color: theme.navy }}>
              HR Analytics &amp; ISO 30414
            </h1>
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              People metrics, gender balance, tenure, payroll snapshot
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/hris">
              <Button variant="secondary" size="sm">
                <ChevronLeft className="w-4 h-4 mr-1" strokeWidth={1.7} />
                HRIS Home
              </Button>
            </Link>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => load(true)}
              disabled={refreshing}
            >
              <RefreshCw className={`w-4 h-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} strokeWidth={1.7} />
              Refresh
            </Button>
            {/* Legacy iframe link retired 2026-05-18 — all HRIS surfaces
                are now native Next.js routes under /hris/*. */}
          </div>
        </div>

        {/* ─── Error banner ─────────────────────────────────────────────── */}
        {error && (
          <div
            className="rounded-lg p-4 flex items-center gap-3"
            style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.er }} />
            <div className="flex-1">
              <p className="font-medium text-sm" style={{ color: theme.er }}>
                Couldn&apos;t load HR data
              </p>
              <p className="text-xs mt-0.5" style={{ color: theme.er, opacity: 0.7 }}>
                {error}
              </p>
            </div>
            <Button variant="danger" size="sm" onClick={() => load()}>
              Retry
            </Button>
          </div>
        )}

        {/* ─── KPI grid (4x2 on xl, 2x4 on md, 1x8 on mobile) ─────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {loading ? (
            Array.from({ length: 8 }).map((_, i) => (
              <div
                key={i}
                className="rounded-lg p-4 h-[112px] animate-pulse"
                style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
              />
            ))
          ) : (
            kpis.map((card, idx) => {
              const Icon = card.icon
              return (
                <div
                  key={idx}
                  className="rounded-lg p-4 relative"
                  style={{
                    background: theme.card,
                    border: `1px solid ${theme.cardBdr}`,
                    boxShadow: theme.cardSh,
                  }}
                >
                  <div className="flex items-start justify-between">
                    <div
                      className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: card.isOrange ? theme.oL : theme.inB }}
                    >
                      <Icon
                        className="w-5 h-5"
                        style={{ color: card.isOrange ? theme.orange : theme.inf }}
                        strokeWidth={1.5}
                      />
                    </div>
                    <Badge variant={card.badgeVariant}>{card.badge}</Badge>
                  </div>
                  <div
                    className="text-[22px] font-bold mt-3 font-mono-nums"
                    style={{ color: theme.navy }}
                  >
                    {card.value}
                  </div>
                  <div className="text-xs mt-1" style={{ color: theme.t2 }}>
                    {card.label}
                  </div>
                </div>
              )
            })
          )}
        </div>

        {/* ─── Distribution panels (3-up) ──────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <DistributionCard
            theme={theme}
            title="Gender Distribution"
            rows={[
              { label: 'Female', count: analytics.female, total: analytics.total, color: theme.orange },
              { label: 'Male',   count: analytics.male,   total: analytics.total, color: theme.navy },
            ]}
            loading={loading}
          />
          <DistributionCard
            theme={theme}
            title="Age Distribution"
            rows={Object.entries(analytics.ageBuckets).map(([label, count]) => ({
              label,
              count,
              total: analytics.total,
              color: theme.inf,
            }))}
            loading={loading}
          />
          <DistributionCard
            theme={theme}
            title="Location"
            rows={analytics.locs.map(([label, count]) => ({
              label,
              count,
              total: analytics.total,
              color: theme.ok,
            }))}
            loading={loading}
          />
        </div>

        {/* ─── Department breakdown ────────────────────────────────────── */}
        <div
          className="rounded-lg overflow-hidden"
          style={{
            background: theme.card,
            border: `1px solid ${theme.cardBdr}`,
            boxShadow: theme.cardSh,
          }}
        >
          <div className="px-5 py-4 border-b" style={{ borderColor: theme.cardBdr }}>
            <h3 className="text-base font-semibold" style={{ color: theme.navy }}>
              Department Breakdown
            </h3>
          </div>
          <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {loading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <div
                  key={i}
                  className="h-12 rounded-md animate-pulse"
                  style={{ background: theme.g100 }}
                />
              ))
            ) : (
              analytics.depts.map(([name, count]) => (
                <div
                  key={name}
                  className="rounded-md px-3 py-2.5 flex items-center justify-between"
                  style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}
                >
                  <span className="text-sm truncate" style={{ color: theme.text }}>{name}</span>
                  <Badge variant="navy">{count}</Badge>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── DistributionCard ────────────────────────────────────────────────────────

interface DistRow {
  label: string
  count: number
  total: number
  color: string
}

function DistributionCard({
  theme,
  title,
  rows,
  loading,
}: {
  theme: ReturnType<typeof useTheme>['theme']
  title: string
  rows: DistRow[]
  loading: boolean
}) {
  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
      }}
    >
      <div className="px-5 py-4 border-b" style={{ borderColor: theme.cardBdr }}>
        <h3 className="text-base font-semibold" style={{ color: theme.navy }}>{title}</h3>
      </div>
      <div className="p-5 space-y-3">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-6 rounded animate-pulse" style={{ background: theme.g100 }} />
          ))
        ) : rows.length === 0 ? (
          <p className="text-xs" style={{ color: theme.t3 }}>No data</p>
        ) : (
          rows.map(r => {
            const p = pct(r.count, r.total)
            return (
              <div key={r.label}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-sm" style={{ color: theme.text }}>{r.label}</span>
                  <span className="text-sm font-medium font-mono-nums" style={{ color: theme.t2 }}>
                    {r.count} <span style={{ color: theme.t3 }}>({p}%)</span>
                  </span>
                </div>
                <div className="h-2 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                  <div
                    className="h-full rounded-full transition-all"
                    style={{ background: r.color, width: `${p}%` }}
                  />
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
