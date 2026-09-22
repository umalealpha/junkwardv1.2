'use client'

/**
 * /hris/career — native Career Growth surface.
 *
 * Replaces the Graphiter "Career" iframe. Surfaces the user's current
 * grade, midpoint, OKRs from the latest performance review, and a 3-
 * step suggested ladder (current → next grade → grade after). All read
 * from the existing /hris/api/employees/ payload — no new endpoint yet.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, TrendingUp, Target, Award, ArrowUpRight } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { getMe } from '@/lib/api'
import type { UserProfile } from '@/lib/api'
import { fetchHrisEmployees, fmtPula, type HrisEmployee } from '../_shared'

interface Okr { nm: string; w: number; s1: number; s2: number }

function localPart(email: string | null | undefined): string {
  if (!email) return ''
  return (email.split('@', 2)[0] || '').toLowerCase()
}

function looksLikeMe(e: HrisEmployee, me: UserProfile | null): boolean {
  if (!me) return false
  const myLocal = localPart(me.email)
  if (!myLocal) return false
  return e.nm.toLowerCase().split(/\s+/).some(t => myLocal.startsWith(t) || t.startsWith(myLocal))
}

export default function HrisCareerPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const [me, setMe] = useState<UserProfile | null>(null)
  const [employees, setEmployees] = useState<HrisEmployee[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    Promise.all([
      getMe().catch(() => null),
      fetchHrisEmployees().then(r => r.employees || []).catch(() => []),
    ]).then(([m, emps]) => {
      setMe(m)
      setEmployees(emps)
    }).finally(() => setLoading(false))
  }, [allowed])

  const myRecord = useMemo(() => employees.find(e => looksLikeMe(e, me)) ?? null, [employees, me])

  // Build an ordered grade list from peers so we can suggest the next two.
  const ladder = useMemo(() => {
    const seen = new Map<string, number>()
    employees.forEach(e => {
      if (!e.grade) return
      const sal = e.salary || 0
      if (!seen.has(e.grade) || sal > (seen.get(e.grade) || 0)) seen.set(e.grade, sal)
    })
    const sorted = Array.from(seen.entries()).sort((a, b) => a[1] - b[1])
    if (!myRecord?.grade) return []
    const idx = sorted.findIndex(([g]) => g === myRecord.grade)
    if (idx < 0) return []
    return sorted.slice(idx, idx + 3).map(([grade, salary]) => ({ grade, salary }))
  }, [employees, myRecord])

  const okrs: Okr[] = myRecord?.okrs || []

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Career Growth" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Career' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {/* Ladder */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold" style={{ color: theme.text }}>Your ladder</h3>
          </div>
          {ladder.length === 0 ? (
            <p className="text-sm" style={{ color: theme.t2 }}>
              {loading ? 'Loading…' : 'Grade information not on file yet — email HR to pair your profile.'}
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {ladder.map((step, i) => (
                <div key={step.grade} className="rounded-xl p-4"
                     style={{
                       background: i === 0 ? theme.oL : theme.g100,
                       border: `1px solid ${i === 0 ? theme.orange + '55' : theme.cardBdr}`,
                     }}>
                  <div className="text-[11px] uppercase tracking-widest font-semibold"
                       style={{ color: i === 0 ? theme.orange : theme.t2 }}>
                    {i === 0 ? 'You are here' : i === 1 ? 'Next step' : 'After that'}
                  </div>
                  <div className="mt-1 text-2xl font-bold" style={{ color: theme.text }}>{step.grade}</div>
                  <div className="text-xs mt-1" style={{ color: theme.t2 }}>Midpoint {fmtPula(step.salary)}</div>
                  {i > 0 && (
                    <div className="mt-2 text-[11px] flex items-center gap-1" style={{ color: theme.t2 }}>
                      <ArrowUpRight className="w-3 h-3" /> +{fmtPula(step.salary - ladder[i - 1].salary)} delta
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* OKRs */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-2 mb-3">
            <Target className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold" style={{ color: theme.text }}>Your OKRs</h3>
          </div>
          {okrs.length === 0 ? (
            <p className="text-sm" style={{ color: theme.t2 }}>
              {loading ? 'Loading…' : 'No OKRs on file for the current period.'}
            </p>
          ) : (
            <div className="space-y-3">
              {okrs.map((o, i) => {
                const weighted = ((o.s1 + o.s2) / 2) * (o.w / 100)
                return (
                  <div key={i} className="space-y-1.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm font-medium truncate" style={{ color: theme.text }}>{o.nm}</span>
                      <span className="text-xs flex-shrink-0" style={{ color: theme.t2 }}>
                        weight {o.w}% · H1 {o.s1.toFixed(1)} · H2 {o.s2.toFixed(1)}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                      <div className="h-full rounded-full"
                           style={{ width: `${Math.min(100, weighted * 20)}%`, background: theme.orange }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Suggested learning */}
        <div className="rounded-2xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-2 mb-2">
            <Award className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold" style={{ color: theme.text }}>Suggested learning</h3>
          </div>
          <p className="text-sm" style={{ color: theme.t2 }}>
            Curated tracks land here once HR's learning catalogue API ships. For now, see Confluence ·
            People · Learning for the active list.
          </p>
        </div>
      </main>
    </div>
  )
}
